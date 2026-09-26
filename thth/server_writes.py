"""Scoped server requests. Publishing, deletion and scheduling happen directly (design 3.13.0)."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timezone
from . import accounts, admin_log, approval, relay, core, jst, managed_repo, queuefile, server_files, writeback
from .report_service import ReportContext, ReportServiceError

WRITE_OPERATIONS = frozenset(('draft_put','send_request','retract_request','schedule_request',
                              'media_upload_url','media_complete'))
# 公開・削除・予約に至る依頼。承認ページは無い（設計 3.13.0）: どれもその場で行う（安全装置は効く）。
# 3.12.0 までの `approval_request` は受け付けない（`unsupported_operation`）。
PUBLISHING_OPERATIONS = frozenset(('send_request','retract_request','schedule_request'))
READ_OPERATIONS = frozenset(('draft_list','queue'))
DRAFT_ID = re.compile(r'[0-9a-f]{64}\Z')
SAFE_ERRORS = frozenset(('invalid_request','unsupported_operation','invalid_scope','invalid_options','scope_unavailable',
    'writes_not_allowed','invalid_draft','draft_changed','draft_not_editable','managed_repo_required','production_disabled',
    'credential_changed','draft_commit_unconfirmed','draft_not_verified','account_stopped','account_leaving',
    'credential_unavailable','write_unavailable'))
from .lint import REASONS as LINT_REASONS
# `invalid_draft` に添える理由。**静的な符丁だけ**——lint の日本語 1 行や
# path をそのままサーバの口から出さない（2.14.1）。
DRAFT_REASONS = frozenset(('body_not_representable','media_not_representable')) | LINT_REASONS
from .media_uploads import REASONS as MEDIA_REASONS
# managed repo をモードで断ったときだけは `write_unavailable` で終わらせない——
# 運用者が chmod で直せる唯一の理由なので、静的な名前のまま上げる。
REPO_REASONS = frozenset(('managed_git_store_unsafe_mode',))
# 承認なしの道と安全装置（設計 3.12.0 §3.2・§3.3）。静的な名前だけ。
from .guard import REFUSALS as GUARD_REFUSALS, STOP_REASONS as GUARD_STOP_REASONS
DIRECT_REASONS = frozenset(('account_busy','publication_failed','publication_unconfirmed','deletion_failed',
                            'permission_unavailable','schedule_commit_unconfirmed',
                            # `scheduled: false` の口座（timer に載っていない）で予約・猶予を求めた（段 3・裁定 (b)）。
                            'schedule_unavailable'))
from .account_settings import REASONS as SETTINGS_REASONS
# 利用者 scope の読む口（設計 3.14.0 §3.2・`thth/server_reads.py`）。
from .server_reads import REASONS as READ_REASONS
SAFE_ERRORS = SAFE_ERRORS | MEDIA_REASONS | REPO_REASONS | GUARD_REFUSALS | DIRECT_REASONS | SETTINGS_REASONS | READ_REASONS
# 断りに添えてよい詳細（止まった理由）。
GUARD_DETAILS = GUARD_STOP_REASONS | frozenset(('guard_state_unreadable',))


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
                # 断るなら理由まで返す。呼び手は何を直せばよいか判らないまま
                # 同じ本文を送り直す（初回の 3.0 通し運転・2026-09-23）。
                if any(not lint.is_warning(item) for item in problems): error('invalid_draft',lint.reason_code(problems))
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


# --------------------------------------------------------------------------
# 公開・削除・予約はその場で行う（設計 3.13.0・3.12.0 §3.2 の道が唯一の道）。
# どの道も安全装置（`thth/guard.py`）を**ロックの中で**もう一度通してから出す・消す・刻む。
# --------------------------------------------------------------------------

def _origin(context, via):
    from . import guard
    return {'via':via,'credential':guard.credential_id(context)}


def _require_scheduled(cfg):
    """timer に載っていない口座（`scheduled: false`）では予約も猶予も「刻んでも出ない」。黙らず断る。"""
    if cfg.get('scheduled',True) is False: error('schedule_unavailable')


def _guarded(context, account, kind, origin, **extra):
    from . import guard
    def check(cfg):
        guard.check(account,cfg,kind,actor=context.actor,via=origin['via'],credential=origin['credential'],**extra)
    return check


def _refuse_if_stopped(account):
    from .guard import stopped, GuardRefused
    halted=stopped(account)
    if halted is not None: raise GuardRefused('account_stopped',reason=halted.get('reason'))


def direct_send(context, request, via):
    """その場で公開する。点検は `core.send_once` と同じ（rehearsal で digest を出し、同じ digest で出す）。"""
    _schema(request,('body','topic','reply_to'),('body',))
    if len(request['body'].encode())>48000: error('invalid_draft')
    for key in ('topic','reply_to'):
        if request.get(key) is not None and not isinstance(request[key],str): error('invalid_request')
    account=request['account'];cfg=current(context,account,write=True)
    if cfg.get('production') is not True: error('production_disabled')
    from . import postid
    reply=postid.for_account(cfg,request.get('reply_to'));topic=request.get('topic')
    # 返信には最短間隔を掛けない（裁定 (a)）。上限と burst には数える。
    origin=_origin(context,via);check=_guarded(context,account,'publish',origin,reply=bool(reply))
    check(cfg)
    dry=core.send_once(account,text=request['body'],topic=topic,reply_to=reply,log=lambda _:None)
    if dry.exit_code or not dry.digest: error('invalid_draft')
    def before(locked_cfg):
        if current(context,account,write=True)!=locked_cfg: error('credential_changed')
        check(locked_cfg)
        return accounts.load_token(locked_cfg)
    result=core.send_once(account,text=request['body'],topic=topic,reply_to=reply,production_flag=True,
                          confirm=dry.digest,log=lambda _:None,before_execute=before,
                          lock_context=server_files.account_locks(account,cfg),origin=origin)
    if result.action=='locked': error('account_busy')
    if result.action=='inflight': error('publication_unconfirmed')
    if result.exit_code or result.mode!='production' or not result.post_id: error('publication_failed')
    try:
        with admin_log.transaction():
            admin_log.append('sent',account,cfg,by=context.actor,via=via,diff={'published':[False,True]})
    except Exception:
        # 出たことの正本は sent/ と runs。変更ログに残せなくても「出ていない」とは答えない。
        pass
    permalink=result.url
    if not permalink:
        try:
            from . import retract_cli
            permalink=retract_cli._lookup_url(cfg,accounts.load_token(cfg),result.post_id)
        except Exception:
            permalink=None
    return {'account':account,'status':'published','post_id':result.post_id,'permalink':permalink,'via':via}


def direct_retract(context, request, via):
    """その場で削除する（DELETE は `retract_cli._do_retract` の 1 回だけ）。"""
    _schema(request,('post_id','reason'),('post_id','reason'))
    account=request['account'];cfg=current(context,account,write=True)
    from types import SimpleNamespace
    from . import adapters, postid, retract_cli
    if not postid.is_usable(request['post_id']) or writeback.has_control_chars(request['reason']): error('invalid_request')
    cls=adapters.adapter_class(cfg['media'])
    if not getattr(cls,'DELETE_PERMISSION',None): error('unsupported_operation')
    record=retract_cli._find_record(cfg,account,request['post_id'])
    if not record or record.get('front_matter',{}).get('retracted_at'): error('scope_unavailable')
    if record['source']=='queue':
        owned=[item for item in _rows(cfg,account) if str(_queue(cfg)[1]/item[0])==record['path']]
        if len(owned)!=1 or not owned[0][3]: error('draft_not_verified')
    if cfg.get('production') is not True: error('production_disabled')
    token=accounts.load_token(cfg)
    if not cls.has_token(token) or cls.missing_permissions(token,[cls.DELETE_PERMISSION]): error('permission_unavailable')
    origin=_origin(context,via);check=_guarded(context,account,'retract',origin)
    check(cfg)
    def before(locked_cfg):
        if current(context,account,write=True)!=locked_cfg: error('credential_changed')
        check(locked_cfg)
        return accounts.load_token(locked_cfg)
    outcome={}
    retract_cli._do_retract(SimpleNamespace(account=account,wait=0,json=False,result_sink=outcome.update),
            cfg,cls,token,record,request['post_id'],reason=request['reason'],by=context.actor,url=record.get('url'),
            before_execute=before,lock_context=server_files.account_locks(account,cfg),origin=origin)
    if outcome.get('retracted') is not True: error('deletion_failed')
    return {'account':account,'status':'retracted','post_id':request['post_id'],'retracted_at':outcome.get('retracted_at'),
            'push_pending':bool(outcome.get('push_error')),'via':via}


def _publish_time(value):
    try: return queuefile.parse_publish_at(value)
    except (ValueError,TypeError): return None


def schedule(context, request, via, extra_fields=None):
    """下書きを queue に「出してよい」として刻む（timer が `publish_at` 以降に拾う）。

    刻む front matter は `thth approve` と同じ `approval.approved_fields`（timer が指紋を照合する）。
    `approved_by` は資格の actor、足すのは `approved_via`（mcp・cli・http）と `approved_credential`。
    """
    _schema(request,('draft_id',),('draft_id',))
    account=request['account'];cfg=current(context,account,write=True)
    if cfg.get('production') is not True: error('production_disabled')
    _require_scheduled(cfg)
    origin=_origin(context,via)
    _refuse_if_stopped(account)
    with server_files.account_locks(account,cfg):
        locked=current(context,account,write=True)
        _sync(locked)
        name,raw,q=_draft(locked,account,request['draft_id'])
        if q.front_matter.get('status')!='draft' or q.front_matter.get('post_id'): error('draft_not_editable')
        from .cli import _prepare_one
        path=_queue(locked)[1]/name
        value,problem=_prepare_one(str(path))
        if problem or not value or value.get('bundle'): error('invalid_draft')
        publish_at=_publish_time(value.get('publish_at')) or jst.now_jst()
        day=jst.to_jst(publish_at).date()
        same_day=0
        for _name,_raw,other,_verified in _rows(locked,account):
            fm=other.front_matter;when=_publish_time(fm.get('publish_at'))
            if fm.get('status')=='approved' and not fm.get('post_id') and when is not None and jst.to_jst(when).date()==day:
                same_day+=1
        _guarded(context,account,'schedule',origin,publish_at=publish_at,scheduled_same_day=same_day)(locked)
        fields=approval.approved_fields(value,context.actor,jst.iso())
        fields['approved_via']=via
        if origin['credential']: fields['approved_credential']=origin['credential']
        fields.update(extra_fields or {})
        updated=writeback.front_matter_text(raw.decode(),fields)
        with server_files.directory(_queue(locked)[1]) as fd:
            server_files.replace_at(fd,name,updated.encode(),expected=raw)
        def unchanged():
            with server_files.directory(_queue(locked)[1]) as fd:
                return server_files.read_at(fd,name)==updated.encode()
        ok,_=writeback.commit_and_push(locked['repo_dir'],rel_path=os.path.relpath(path,locked['repo_dir']),
                                      message=f'schedule by={context.actor} via={via}',validate=unchanged)
        if not ok: error('schedule_commit_unconfirmed')
    return {'account':account,'draft_id':request['draft_id'],'status':'approved','publish_at':value.get('publish_at'),'via':via}


def held_send(context, request, via, minutes):
    """`hold_minutes` が 1 以上の口座の「今すぐ」: その分だけ先の予約にする（その間は持ち主が取り消せる）。"""
    import datetime as _dt
    _schema(request,('body','topic','reply_to'),('body',))
    _require_scheduled(current(context,request['account'],write=True))
    publish_at=jst.iso(jst.now_jst()+_dt.timedelta(minutes=minutes))
    put={'operation':'draft_put','account':request['account'],'body':request['body'],'publish_at':publish_at}
    for key in ('topic','reply_to'):
        if request.get(key): put[key]=request[key]
    made=draft_put(context,put,via)
    # `held_minutes` は /activity が「猶予中」と「予約」を見分ける印（出す時刻には効かない）。
    done=schedule(context,{'operation':'schedule_request','account':request['account'],'draft_id':made['draft_id']},via,
                  extra_fields={'held_minutes':str(minutes)})
    return {**done,'status':'held','hold_minutes':minutes}


def direct(context, request, via, cfg):
    op=request['operation']
    if op=='schedule_request':
        return schedule(context,request,via)
    if op=='retract_request':
        return direct_retract(context,request,via)
    minutes=accounts.guard_limits(cfg)['hold_minutes']
    return held_send(context,request,via,minutes) if minutes else direct_send(context,request,via)


def cancel_schedule(account, draft_id, *, by, via='http'):
    """予約（猶予中を含む）を取り消して draft に戻す（持ち主の /activity から・段 3）。

    `thth revoke` と同じ書き戻し（status: draft・承認の 3 項目を空・revoked_* を残す）。本文には
    触らない。既に出たもの（post_id あり）・承認済みでないものは断る。何度呼んでも同じ結果。
    戻り値は `cancelled`（取り消した）か `already_draft`（既に draft）。
    """
    admin_log.actor(by)
    cfg=accounts.load_account(account)
    with server_files.account_locks(account,cfg):
        cfg=accounts.load_account(account)
        _sync(cfg)
        name,raw,q=_draft(cfg,account,draft_id)
        fm=q.front_matter
        if fm.get('post_id'): error('draft_not_editable')
        if fm.get('status')=='draft': return 'already_draft'
        if fm.get('status')!='approved': error('draft_not_editable')
        path=_queue(cfg)[1]/name
        updated=writeback.front_matter_text(raw.decode(),{'status':'draft','approved_sha':None,'approved_at':None,
            'approved_by':None,'revoked_at':jst.iso(),'revoked_by':by,'revoked_reason':'owner_cancel_via_'+via})
        with server_files.directory(_queue(cfg)[1]) as fd:
            server_files.replace_at(fd,name,updated.encode(),expected=raw)
        def unchanged():
            with server_files.directory(_queue(cfg)[1]) as fd:
                return server_files.read_at(fd,name)==updated.encode()
        ok,_=writeback.commit_and_push(cfg['repo_dir'],rel_path=os.path.relpath(path,cfg['repo_dir']),
                                      message=f'cancel by={by} via={via}',validate=unchanged)
        if not ok: error('schedule_commit_unconfirmed')
    return 'cancelled'


def execute(context, request, *, via='http'):
    if type(request) is not dict or request.get('operation') not in WRITE_OPERATIONS: error('unsupported_operation')
    if not isinstance(request.get('account'),str): error('invalid_scope')
    try:
        cfg=current(context,request['account'],write=True)
        if request['operation'] in ('media_upload_url','media_complete'):
            from . import media_uploads
            return media_uploads.execute(context,request,via)
        if request['operation']=='draft_put':
            return draft_put(context,request,via)
        # 止まった口座（安全装置）は公開・削除・予約を受けない。
        _refuse_if_stopped(request['account'])
        # timer に載っていない口座では予約を刻んでも出ない（裁定 (b)）。
        if request['operation']=='schedule_request': _require_scheduled(cfg)
        return direct(context,request,via,cfg)
    except ReportServiceError: raise
    except (OSError,ValueError,TypeError,KeyError,accounts.AccountError,relay.RelayError,admin_log.AdminLogError) as exc:
        if str(exc) in REPO_REASONS: error(str(exc))
        error('write_unavailable')


def serve(context, request, *, via):
    """遠くの道（設計 3.14.0 §3.1）の受け付け: operation で書く口か読む口へ渡す。名前の表はここ（呼び手は持たない）。

    書く口は `execute`、下書きの読む口は `READ_OPERATIONS`（`read`）、§3.2 の読む口は `server_reads`、
    状態と設定は `account_settings`（MCP と同じ関数）。知らない名前は `unsupported_operation`。
    """
    operation=request.get('operation') if type(request) is dict else None
    if operation in WRITE_OPERATIONS: return execute(context,request,via=via)
    if operation in READ_OPERATIONS: return read(context,request)
    # 段 1 の読む口（設計 §3.2・`thth/server_reads.py`）と、状態・設定（MCP と同じ関数）。
    from . import server_reads
    if operation in server_reads.OPERATIONS: return server_reads.execute(context,request)
    if operation in ('settings','account_status'):
        from . import account_settings
        return (account_settings.mcp_settings if operation=='settings' else account_settings.mcp_status)(context,request)
    error('unsupported_operation')


def read(context, request):
    _schema(request,(),())
    cfg=current(context,request['account'])
    from . import leave_gate
    try:
        with leave_gate.lease(request['account']):
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
