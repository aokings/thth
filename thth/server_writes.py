"""Scoped server requests. Human approval is consumed only by the durable worker."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timezone
from . import accounts, admin_log, approval, approval_relay, core, doctor, jst, managed_repo, queuefile, server_files, tags, writeback
from .report_service import ReportContext, ReportServiceError

WRITE_OPERATIONS = frozenset(('draft_put','approval_request','send_request','retract_request',
                              'media_upload_url','media_complete'))
READ_OPERATIONS = frozenset(('draft_list','queue','request_status'))
DRAFT_ID = re.compile(r'[0-9a-f]{64}\Z')
JOB_ID = approval_relay.OPAQUE
SAFE_ERRORS = frozenset(('invalid_request','unsupported_operation','invalid_scope','invalid_options','scope_unavailable',
    'writes_not_allowed','invalid_draft','draft_changed','draft_not_editable','managed_repo_required','production_disabled',
    'credential_changed','draft_commit_unconfirmed','draft_not_verified','account_stopped','account_leaving','approval_registration_unknown',
    'credential_unavailable','write_unavailable','media_preview_unavailable','approval_registration_rejected',
    'approval_request_too_large','approval_attachments_too_many'))
from .media_uploads import REASONS as MEDIA_REASONS
# managed repo をモードで断ったときだけは `write_unavailable` で終わらせない——
# 運用者が chmod で直せる唯一の理由なので、静的な名前のまま上げる。
REPO_REASONS = frozenset(('managed_git_store_unsafe_mode',))
SAFE_ERRORS = SAFE_ERRORS | MEDIA_REASONS | REPO_REASONS


def error(reason, detail=None):
    exc = ReportServiceError(reason)
    exc.reason = detail
    raise exc


def current(context, account, *, write=False):
    """Reauthenticate the trusted capability; no request field creates identity."""
    if type(context) is ReportContext:
        from .report_service import check_excluded
        check_excluded(context,account)
    if type(context) is not ReportContext or context.scope != 'user' or account not in context.allowed_accounts:
        error('scope_unavailable')
    if write and (not context.writes or not context.actor or not context.credential_digest or not context.credentials_path):
        error('writes_not_allowed')
    if context.credentials_path:
        from .report_http import load_credentials
        root, credentials = load_credentials(Path(context.credentials_path))
        found = next((item[3] for item in credentials if item[0] == context.credential_digest
                      and not item[2] and datetime.now(timezone.utc) < item[1]), None)
        from dataclasses import replace
        if found is not None:check_excluded(found,account)
        if (found is None or account not in found.allowed_accounts
                or found.allowed_accounts[account] != context.allowed_accounts[account]
                or found != replace(context,allowed_accounts=found.allowed_accounts)):
            error('credential_changed')
        from .report_isolation import validate_environment
        validate_environment(root, found.allowed_accounts)
    from . import leave_gate
    if leave_gate.stopped(account):
        try:leave_gate.check_busy(account)
        except accounts.AccountLeaving:error('account_leaving')
        error('account_stopped')
    cfg = accounts.load_account(account)
    if cfg.get('project') != context.allowed_accounts[account]: error('scope_unavailable')
    return cfg


def _schema(request, keys, required):
    if type(request) is not dict or set(request)-set(keys)-{'operation','account'} or not set(required)|{'account','operation'} <= set(request):
        error('invalid_request')
    for name in required:
        if not isinstance(request[name],str) or not request[name].strip(): error('invalid_request')
    if not isinstance(request['account'],str): error('invalid_scope')


def _queue(cfg):
    repo = Path(cfg['repo_dir']).absolute()
    relative=Path(cfg['queue_dir'])
    if relative.is_absolute() or not relative.parts or any(p.startswith('.') for p in relative.parts): error('invalid_draft')
    queue = repo / relative
    if os.path.commonpath([repo, queue.absolute()]) != str(repo) or '..' in queue.parts:
        error('invalid_draft')
    return repo, queue


def _id(name):
    return hashlib.sha256(name.encode()).hexdigest()


def _rows(cfg, account):
    repo, directory = _queue(cfg)
    tree = writeback.upstream_sha(str(repo))
    rows = []
    with server_files.directory(directory) as fd:
        for name in sorted(os.listdir(fd)):
            if not name.endswith('.md'): continue
            raw = server_files.read_at(fd, name)
            q = queuefile.parse_text(raw.decode(), str(directory/name))
            if q.malformed or q.front_matter.get('account') != account: continue
            verified = writeback.matches_synced_commit(str(repo), str(directory/name), tree_sha=tree, disk_bytes=raw)
            rows.append((name, raw, q, verified))
    return rows


def _draft(cfg, account, draft_id, *, verified=True):
    if not isinstance(draft_id,str) or not DRAFT_ID.fullmatch(draft_id): error('invalid_request')
    for name, raw, q, synced in _rows(cfg,account):
        if _id(name)==draft_id:
            if verified and not synced: error('draft_not_verified')
            return name, raw, q
    error('scope_unavailable')


def _sync(cfg):
    ok, _, sha = writeback.sync_repo(cfg['repo_dir'])
    if not ok or not sha: error('draft_not_verified')


def draft_put(context, request, via):
    _schema(request, ('body','topic','publish_at','reply_to','draft_id','expected_revision','media'), ('body','publish_at'))
    account=request['account'];cfg=current(context,account,write=True)
    clone, _ = managed_repo.locations(account)
    _, queue = _queue(cfg)
    if cfg['repo_dir'] != str(clone): error('managed_repo_required')
    if ('draft_id' in request) != ('expected_revision' in request): error('invalid_request')
    for key in ('topic','reply_to','publish_at'):
        value=request.get(key)
        if value is not None and (not isinstance(value,str) or writeback.has_control_chars(value)): error('invalid_draft')
    if len(request['body'].encode())>48000: error('invalid_draft')
    rows=None
    if 'media' in request:
        from . import media_uploads
        rows=media_uploads.draft_rows(account,context.actor,request['media'])
    with server_files.account_locks(account,cfg):
        current(context,account,write=True)
        managed_repo.initialize(account,cfg);_sync(cfg)
        name=secrets.token_hex(16)+'.md';old=None
        if 'draft_id' in request:
            name,old,q=_draft(cfg,account,request['draft_id'])
            if q.front_matter.get('status')!='draft' or q.front_matter.get('post_id'): error('draft_not_editable')
            if request['expected_revision']!=hashlib.sha256(old).hexdigest(): error('draft_changed')
        text='---\nthth: 1\naccount: '+account+'\nstatus: draft\npublish_at: '+request['publish_at']+'\n'
        for key in ('topic','reply_to'):
            if request.get(key): text+=key+': '+request[key]+'\n'
        if rows is not None:
            # Alt is quoted as JSON so any legal alt survives the round trip;
            # the file is the repo path a completed upload already committed.
            text+='media:\n'+''.join('  - file: '+row['file']+'\n    alt: '+json.dumps(row['alt'],ensure_ascii=False)+'\n' for row in rows)
        text+='---\n\n## '+cfg['media']+'\n\n'+request['body'].strip()+'\n'
        q=queuefile.parse_text(text,'draft')
        if queuefile.extract_section(q.body,cfg['media']).strip()!=request['body'].strip(): error('invalid_draft','body_not_representable')
        if rows is not None and q.front_matter.get('media')!=rows: error('invalid_draft','media_not_representable')
        if old == text.encode():
            return {'draft_id':_id(name),'account':account,'status':'draft','revision':hashlib.sha256(old).hexdigest()}
        # Existing lint is the authority. A private temporary leaf is never queued.
        from . import lint
        with server_files.directory(queue) as fd:
            temp='.lint-'+secrets.token_hex(16)+'.tmp'
            try:
                server_files.replace_at(fd,temp,text.encode(),new=True)
                problems=lint.lint_file(str(queue/temp))
                if any(not lint.is_warning(item) for item in problems): error('invalid_draft','lint_failed')
            finally:
                try: os.unlink(temp,dir_fd=fd)
                except FileNotFoundError: pass
            server_files.replace_at(fd,name,text.encode(),expected=old,new=old is None)
        def unchanged():
            with server_files.directory(queue) as fd:
                return server_files.read_at(fd,name)==text.encode()
        ok,_=writeback.commit_and_push(str(clone),rel_path=os.path.relpath(queue/name,clone),
                                      message=f'draft by={context.actor} via={via}',validate=unchanged)
        if not ok: error('draft_commit_unconfirmed')
        return {'draft_id':_id(name),'account':account,'status':'draft','revision':hashlib.sha256(text.encode()).hexdigest()}


def prepare(context, request, *, media_out=None):
    """Return exact public text plus a full private execution binding.

    `media_out` is filled only for display: the binding and its digest are
    unchanged by attachments, so a repeated prepare() still compares equal.
    """
    account=request['account'];cfg=current(context,account,write=True)
    kind={'approval_request':'approve','send_request':'send','retract_request':'retract'}[request['operation']]
    display=dict(media=cfg['media'],reply_to=None,publish_at=None,target=None,reason=None,topic=None,options=None)
    if kind=='approve':
        _schema(request,('draft_id',),('draft_id',))
        name,raw,q=_draft(cfg,account,request['draft_id'])
        if q.front_matter.get('status')!='draft': error('draft_not_editable')
        from .cli import _prepare_one
        value,problem=_prepare_one(str(_queue(cfg)[1]/name))
        if problem or not value or value.get('bundle'): error('invalid_draft')
        if media_out is not None and value.get('media_manifest'):
            media_out.update(manifest=value['media_manifest'],repo_dir=cfg['repo_dir'],
                             front_matter=q.front_matter,medium=cfg['media'])
        text=value['text'];source=hashlib.sha256(raw).hexdigest()
        for key in ('reply_to','publish_at','topic'): display[key]=value.get(key)
        display['options']=json.dumps({k:value[k] for k in ('location','location_id','share_to_instagram')},ensure_ascii=False,sort_keys=True)
    elif kind=='send':
        _schema(request,('body','topic','reply_to'),('body',))
        if len(request['body'].encode())>48000: error('invalid_draft')
        for key in ('topic','reply_to'):
            if request.get(key) is not None and not isinstance(request[key],str): error('invalid_request')
        from . import postid
        reply=postid.for_account(cfg,request.get('reply_to'))
        result=core.send_once(account,text=request['body'],topic=request.get('topic'),reply_to=reply,log=lambda _:None)
        if result.exit_code or not result.digest: error('invalid_draft')
        text=tags.prepared(cfg['media'],request['body'].strip(),queuefile.normalize_topic(request.get('topic')),hashtags=bool(cfg.get('hashtags',True)))
        display.update(reply_to=reply or None,topic=queuefile.normalize_topic(request.get('topic')))
        source=None
    else:
        _schema(request,('post_id','reason'),('post_id','reason'))
        from . import adapters, postid, retract_cli
        if not postid.is_usable(request['post_id']) or writeback.has_control_chars(request['reason']): error('invalid_request')
        cls=adapters.adapter_class(cfg['media'])
        if not getattr(cls,'DELETE_PERMISSION',None): error('unsupported_operation')
        record=retract_cli._find_record(cfg,account,request['post_id'])
        if not record or record.get('front_matter',{}).get('retracted_at'): error('scope_unavailable')
        if record['source']=='queue':
            owned=[item for item in _rows(cfg,account) if str(_queue(cfg)[1]/item[0])==record['path']]
            if len(owned)!=1 or not owned[0][3]: error('draft_not_verified')
        with server_files.directory(Path(record['path']).parent) as fd:
            record_bytes=server_files.read_at(fd,Path(record['path']).name)
        text=record.get('text') or '';source=hashlib.sha256(record_bytes).hexdigest()
        display.update(target=request['post_id'],reason=request['reason'])
    if cfg.get('production') is not True: error('production_disabled')
    generation=doctor._credential_generation(cfg)
    if not generation: error('credential_unavailable')
    binding=dict(kind=kind,account=account,actor=context.actor,text=text,context=display,
                 ledger=hashlib.sha256(server_files.encode(cfg)).hexdigest(),credential_generation=generation,source=source)
    return binding


def request_approval(context, request, via):
    media={}
    binding=prepare(context,request,media_out=media)
    from . import approval_jobs
    return approval_jobs.create(context,request,binding,via,media=media or None)


def execute(context, request, *, via='http'):
    if type(request) is not dict or request.get('operation') not in WRITE_OPERATIONS: error('unsupported_operation')
    if not isinstance(request.get('account'),str): error('invalid_scope')
    try:
        current(context,request['account'],write=True)
        if request['operation'] in ('media_upload_url','media_complete'):
            from . import media_uploads
            return media_uploads.execute(context,request,via)
        return draft_put(context,request,via) if request['operation']=='draft_put' else request_approval(context,request,via)
    except ReportServiceError: raise
    except (OSError,ValueError,TypeError,KeyError,accounts.AccountError,approval_relay.RelayError,admin_log.AdminLogError) as exc:
        if str(exc) in REPO_REASONS: error(str(exc))
        error('write_unavailable')


def read(context, request):
    op=request.get('operation')
    _schema(request,('job_id',) if op=='request_status' else (),('job_id',) if op=='request_status' else ())
    cfg=current(context,request['account'])
    from . import leave_gate
    try:
        with leave_gate.lease(request['account']):
            if op=='request_status':
                from .approval_jobs import status
                return status(context,request['account'],request['job_id'])
            rows=[]
            try:
                for name,raw,q,verified in _rows(cfg,request['account']):
                    fm=q.front_matter
                    rows.append(dict(draft_id=_id(name),revision=hashlib.sha256(raw).hexdigest(),status=fm.get('status'),
                                     body=queuefile.extract_section(q.body,cfg['media']),topic=fm.get('topic') or None,
                                     publish_at=fm.get('publish_at'),reply_to=fm.get('reply_to') or None,verified=verified))
            except FileNotFoundError: pass
            return {'account':request['account'],'drafts':rows}
    except accounts.AccountLeaving:
        error('account_leaving')
