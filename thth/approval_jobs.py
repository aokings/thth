"""Durable single-consumer approval jobs; uncertain effects are never replayed."""
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
from . import accounts, admin_log, approval, approval_relay as relay, core, doctor, jst, lock, server_files, writeback
from .report_service import ReportServiceError

TERMINAL = frozenset(('completed','failed','unknown','expired'))
STATES = TERMINAL | {'registering','pending','consuming','ready','executing'}
REASONS = {'approval_registration_unknown','interrupted_outcome_unknown','approval_timeout',
           'operation_outcome_unknown','approval_no_longer_valid'}


def _valid_job(value, job_id):
    """Private storage is fallible: validate both shape and redundant bindings."""
    def string(v, maximum=48000):
        return isinstance(v,str) and len(v.encode()) <= maximum
    def matches(pattern, v):
        return isinstance(v,str) and pattern.fullmatch(v) is not None
    sha = re.compile(r'[0-9a-f]{64}\Z')
    required = {'schema_version','job_id','token','read_key','account','kind','actor','credential_digest',
                'request','binding','digest','status','expires_at','via'}
    if (type(value) is not dict or not required <= value.keys()
            or value.keys()-required-{'receipt','reason','post_id'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['job_id'] != job_id or not matches(relay.OPAQUE,job_id)
            or not all(matches(relay.OPAQUE,value[k]) for k in ('token','read_key'))
            or not isinstance(value['account'],str) or not accounts.name_is_safe(value['account'])
            or not matches(relay.PERSON,value['actor'])
            or not all(matches(sha,value[k]) for k in ('credential_digest','digest'))
            or not isinstance(value['status'],str) or value['status'] not in STATES
            or not isinstance(value['kind'],str) or value['kind'] not in ('approve','send','retract')
            or not isinstance(value['via'],str) or value['via'] not in ('http','mcp')
            or type(value['expires_at']) is not int or value['expires_at'] <= 0):
        return False
    if 'reason' in value and (not isinstance(value['reason'],str) or value['reason'] not in REASONS): return False
    if 'post_id' in value and (not string(value['post_id'],4096) or not value['post_id'] or writeback.has_control_chars(value['post_id'])): return False
    binding=value['binding'];request=value['request']
    if (type(binding) is not dict or set(binding) != {'kind','account','actor','text','context','ledger','credential_generation','source'}
            or any(binding[k] != value[k] for k in ('kind','account','actor'))
            or not string(binding['text']) or not all(matches(sha,binding[k]) for k in ('ledger','credential_generation'))
            or binding['source'] is not None and not matches(sha,binding['source'])): return False
    display=binding['context']
    if (type(display) is not dict or set(display) != {'media','reply_to','publish_at','target','reason','topic','options'}
            or not isinstance(display['media'],str) or display['media'] not in ('threads','mastodon','bluesky','x')
            or any(v is not None and not string(v,4096) for v in display.values())): return False
    required_request={'approve':{'draft_id'},'send':{'body'},'retract':{'post_id','reason'}}[value['kind']]
    optional={'send':{'topic','reply_to'}}.get(value['kind'],set())
    if (type(request) is not dict or not required_request|{'operation','account'} <= request.keys()
            or request.keys()-required_request-optional-{'operation','account'}
            or request['account'] != value['account']
            or request['operation'] != {'approve':'approval_request','send':'send_request','retract':'retract_request'}[value['kind']]
            or any(not string(request[k]) or not request[k].strip() for k in required_request)
            or any(request.get(k) is not None and not string(request[k],4096) for k in optional)
            or value['kind']=='approve' and not matches(sha,request['draft_id'])): return False
    if hashlib.sha256(server_files.encode(binding)).hexdigest() != value['digest']: return False
    if value['status']=='ready' and 'receipt' not in value: return False
    if 'receipt' in value and not _receipt_matches(value,value['receipt']): return False
    return True


def directory(account):
    if not accounts.name_is_safe(account): raise ValueError('invalid_account')
    return Path(accounts.state_dir_for(account))/'approval-jobs'


def _load(fd, job_id):
    if not isinstance(job_id,str) or not relay.OPAQUE.fullmatch(job_id): raise ReportServiceError('invalid_request')
    try: value=json.loads(server_files.read_at(fd,job_id+'.json',private=True))
    except RecursionError: raise ValueError('invalid_job') from None
    if not _valid_job(value,job_id): raise ValueError('invalid_job')
    return value


def _save(fd, job):
    server_files.replace_at(fd,job['job_id']+'.json',server_files.encode(job),private=True)


def _public(job):
    return {key:job[key] for key in ('job_id','account','kind','status','expires_at','reason','post_id') if key in job}


def status(context, account, job_id):
    try:
        with server_files.directory(directory(account),private=True) as fd: job=_load(fd,job_id)
        if job['account']!=account or job['credential_digest']!=context.credential_digest:
            raise ReportServiceError('scope_unavailable')
        return _public(job)
    except (OSError,ValueError,KeyError): raise ReportServiceError('scope_unavailable') from None


def create(context, request, binding, via):
    job_id,token,read_key=(secrets.token_urlsafe(32) for _ in range(3))
    now=int(time.time()*1000)
    digest=hashlib.sha256(server_files.encode(binding)).hexdigest()
    job=dict(schema_version=1,job_id=job_id,token=token,read_key=read_key,account=binding['account'],
             kind=binding['kind'],actor=context.actor,credential_digest=context.credential_digest,
             request=request,binding=binding,digest=digest,status='registering',expires_at=now+600000,via=via)
    # Preflight and durable intent precede any remote registration. Never put
    # body, token, URL, credential hash or read key into the administration log.
    with server_files.directory(directory(job['account']),create=True,private=True) as fd:
        with server_files.lock_at(fd,job_id+'.lock'):
            with admin_log.transaction():
                server_files.replace_at(fd,job_id+'.json',server_files.encode(job),new=True)
                admin_log.append({'approve':'approval_requested','send':'send_requested','retract':'retract_requested'}[job['kind']],
                                 job['account'],{},by=context.actor,via=via,diff={'request_present':[False,True]},run_id=job_id)
            try:
                value=relay.signed_request('session',token,'create',dict(person=context.actor,job_id=job_id,digest=digest,
                     account=job['account'],kind=job['kind'],text=binding['text'],context=binding['context'],
                     read_key_hash=hashlib.sha256(read_key.encode()).hexdigest()))
                if (value.get('status')!='pending' or type(value.get('expires_at')) is not int
                        or not now < value['expires_at'] <= int(time.time()*1000)+600000):
                    raise ValueError('invalid_registration')
                job['expires_at']=min(job['expires_at'],value['expires_at']);job['status']='pending';_save(fd,job)
            except Exception:
                job.update(status='unknown',reason='approval_registration_unknown');_save(fd,job)
                raise ReportServiceError('approval_registration_unknown') from None
    return {**_public(job),'text':binding['text'],'digest':digest,'context':binding['context'],
            'approval_url':os.environ.get('THTH_APPROVAL_BASE_URL','https://thth.me').rstrip('/')+'/approve/'+token}


def _receipt_matches(job, receipt):
    expected=dict(job_id=job['job_id'],digest=job['digest'],account=job['account'],kind=job['kind'],approver=job['actor'])
    return not (type(receipt) is not dict or set(receipt) != set(expected)|{'generation','approved_at','expires_at'}
            or any(receipt.get(k)!=v for k,v in expected.items())
            or not isinstance(receipt.get('generation'),str) or not relay.OPAQUE.fullmatch(receipt['generation'])
            or type(receipt.get('approved_at')) is not int or type(receipt.get('expires_at')) is not int
            or not job['expires_at']-600000 <= receipt['approved_at'] < job['expires_at']
            or receipt['expires_at'] < job['expires_at'])


def _validate_receipt(job, receipt):
    now=int(time.time()*1000)
    if not _receipt_matches(job,receipt) or now>=job['expires_at'] or receipt['approved_at']>now:
        raise ReportServiceError('approval_receipt_invalid')


def _perform(fd, job, context):
    from . import adapters, retract_cli, server_writes as writes
    cfg=writes.current(context,job['account'],write=True)
    if int(time.time()*1000)>=job['expires_at']: raise ReportServiceError('approval_timeout')
    def before(locked_cfg):
        fresh=writes.current(context,job['account'],write=True)
        if fresh!=locked_cfg or writes.prepare(context,job['request'])!=job['binding']:
            raise ReportServiceError('approval_content_changed')
        person=relay.signed_request('person',job['actor'],'status',{})
        if person!={'active':True,'locked':False,'generation':job['receipt']['generation']}:
            raise ReportServiceError('approver_changed')
        if int(time.time()*1000)>=job['expires_at']: raise ReportServiceError('approval_timeout')
        # Durable before any approved write / publish / DELETE. Any later error,
        # including fsync or provider uncertainty, may have acted and is unknown.
        with admin_log.transaction():
            latest=accounts.load_account(job['account'])
            if latest!=locked_cfg or doctor._credential_generation(latest)!=job['binding']['credential_generation']:
                raise ReportServiceError('credential_changed')
            bound_token=accounts.load_token(latest)
        job['status']='executing';_save(fd,job)
        return bound_token
    if job['kind']=='approve':
        with server_files.account_locks(job['account'],cfg):
            writes._sync(cfg);before(cfg)
            name,raw,q=writes._draft(cfg,job['account'],job['request']['draft_id'])
            from .cli import _prepare_one
            value,problem=_prepare_one(str(writes._queue(cfg)[1]/name))
            if problem: raise ReportServiceError('invalid_draft')
            updated=writeback.front_matter_text(raw.decode(),approval.approved_fields(value,job['actor'],
                         jst.iso(__import__('datetime').datetime.fromtimestamp(job['receipt']['approved_at']/1000,__import__('datetime').timezone.utc))))
            with server_files.directory(writes._queue(cfg)[1]) as queuefd:
                server_files.replace_at(queuefd,name,updated.encode(),expected=raw)
            def unchanged():
                with server_files.directory(writes._queue(cfg)[1]) as queuefd:
                    return server_files.read_at(queuefd,name)==updated.encode()
            ok,_=writeback.commit_and_push(cfg['repo_dir'],rel_path=os.path.relpath(writes._queue(cfg)[1]/name,cfg['repo_dir']),
                        message=f'approve by={job["actor"]} via={job["via"]}',validate=unchanged)
            if not ok: raise ReportServiceError('approval_commit_unconfirmed')
    elif job['kind']=='send':
        body=job['binding']['text'];display=job['binding']['context']
        digest=approval.compute_send_digest(text=body,account=job['account'],reply_to=display['reply_to'],topic=display['topic'])
        result=core.send_once(job['account'],text=job['request']['body'],topic=display['topic'],reply_to=display['reply_to'],
                production_flag=True,confirm=digest,log=lambda _:None,before_execute=before,
                lock_context=server_files.account_locks(job['account'],cfg))
        if result.action=='locked': raise lock.LockBusy('server_resource_busy')
        if result.exit_code or result.mode!='production' or not result.post_id: raise ReportServiceError('publication_unconfirmed')
        job['post_id']=result.post_id
        with admin_log.transaction():
            admin_log.append('sent',job['account'],cfg,by=job['actor'],via=job['via'],diff={'published':[False,True]},run_id=job['job_id'])
    else:
        request=job['request'];record=retract_cli._find_record(cfg,job['account'],request['post_id'])
        cls=adapters.adapter_class(cfg['media']);token=accounts.load_token(cfg)
        if cls.missing_permissions(token,[cls.DELETE_PERMISSION]): raise ReportServiceError('permission_unavailable')
        code=retract_cli._do_retract(SimpleNamespace(account=job['account'],wait=0,json=False,result_sink=lambda _:None),
                cfg,cls,token,record,request['post_id'],reason=request['reason'],by=job['actor'],url=record.get('url'),
                before_execute=before,lock_context=server_files.account_locks(job['account'],cfg))
        if code: raise ReportServiceError('deletion_unconfirmed')
    job['status']='completed';_save(fd,job)


def process(account, job_id, contexts):
    try:
        with server_files.directory(directory(account),private=True) as fd, server_files.lock_at(fd,job_id+'.lock'):
            job=_load(fd,job_id)
            if job['account'] != account: raise ValueError('invalid_job')
            if job.get('status') in TERMINAL: return
            if job.get('status') in ('registering','consuming','executing'):
                job.update(status='unknown',reason='interrupted_outcome_unknown');_save(fd,job);return
            try:
                context=next((c for c in contexts if c.credential_digest==job['credential_digest']),None)
                from .server_writes import current
                current(context,account,write=True)
                if context.actor != job['actor']: raise ReportServiceError('credential_changed')
                if int(time.time()*1000)>=job['expires_at']:
                    job.update(status='expired',reason='approval_timeout');_save(fd,job);return
                if job['status']=='pending':
                    try:
                        result=relay.signed_request('session',job['token'],'status',{'read_key':job['read_key']})
                    except relay.RelayError as exc:
                        if exc.status in (None,429,500,502,503,504): return
                        if exc.status==410:
                            job.update(status='expired',reason='approval_timeout');_save(fd,job);return
                        raise
                    if result.get('status')=='pending': return
                    if result.get('status')!='approved': raise ReportServiceError('approval_unavailable')
                    job['status']='consuming';_save(fd,job)
                    receipt=relay.signed_request('session',job['token'],'consume',{'read_key':job['read_key']})
                    _validate_receipt(job,receipt);job.update(status='ready',receipt=receipt);_save(fd,job)
                if job['status']=='ready':
                    _validate_receipt(job,job['receipt'])
                    _perform(fd,job,context)
            except lock.LockBusy: return
            except Exception:
                # completed is set in memory before its durable save. If that
                # save fails, the provider/Git effect has already happened.
                uncertain=job['status'] in ('consuming','executing','completed')
                job.update(status='unknown' if uncertain else 'failed',
                           reason='operation_outcome_unknown' if uncertain else 'approval_no_longer_valid')
                _save(fd,job)
    except (OSError,ValueError):
        # Leave durable prior intent in place; never turn storage failure into retry.
        return


def run_once(credentials_path):
    from datetime import datetime, timezone
    from .report_http import load_credentials
    root, credentials=load_credentials(Path(credentials_path))
    from .report_isolation import validate_environment
    for item in credentials:
        validate_environment(root,item[3].allowed_accounts,allow_unreadable=item[3].scope=='admin')
    contexts=[item[3] for item in credentials if not item[2] and datetime.now(timezone.utc)<item[1]]
    for account in accounts.list_account_names():
        try:
            with server_files.directory(directory(account),private=True) as fd:
                names=os.listdir(fd)
            for name in names:
                if name.endswith('.json') and relay.OPAQUE.fullmatch(name[:-5]): process(account,name[:-5],contexts)
        except (OSError,ValueError): continue


def command(args):
    import sys
    try:
        while True:
            run_once(args.credentials)
            if args.once: return 0
            time.sleep(2)
    except KeyboardInterrupt: return 0
    except (OSError,ValueError):
        print('approval_worker_unavailable',file=sys.stderr);return 2


def register(sub):
    parser=sub.add_parser('approval-worker',help='永続承認 job の再開・実行（管理者の常駐 process）')
    parser.add_argument('--credentials',required=True)
    parser.add_argument('--once',action='store_true',help='1 巡だけ検査する')
    parser.set_defaults(func=command)
