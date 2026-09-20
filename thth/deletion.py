"""Verify opaque provider deletion receipts locally; never expose their blobs."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
from . import accounts,admin_log,appenv,approval_relay as relay,jst,leave,leave_gate,server_files


def _decode(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',value):raise ValueError('invalid_signed_request')
    return base64.urlsafe_b64decode(value+'='*((4-len(value)%4)%4))


class SignatureMismatch(ValueError):
    """HMAC comparison failed after the configured app secret was read."""


def verify(blob):
    if not isinstance(blob,str) or len(blob)>8192 or blob.count('.')!=1:raise ValueError('invalid_signed_request')
    signature,encoded=blob.split('.');signature=_decode(signature)
    path=leave._path(appenv.default_path())
    with leave._parent(path) as fd:
        raw=server_files.read_at(fd,path.name,private=True,maximum=65536)
    client=appenv.parse_app_env(raw.decode())
    secret=client.get('THREADS_APP_SECRET')
    if not secret or len(signature)!=32:raise ValueError('signature_unverified')
    if not hmac.compare_digest(signature,hmac.digest(secret.encode(),encoded.encode(),'sha256')):
        raise SignatureMismatch('signature_mismatch')
    payload=json.loads(_decode(encoded))
    if (type(payload) is not dict or payload.get('algorithm')!='HMAC-SHA256'
            or not isinstance(payload.get('user_id'),str) or not payload['user_id'] or len(payload['user_id'])>256):
        raise ValueError('invalid_signed_request')
    matched=[]
    for name in accounts.list_account_names():
        with leave_gate.recovery(name):
            cfg=accounts.load_account(name)
            if cfg.get('media')!='threads':continue
            token=accounts.load_token(cfg)
            if type(token) is dict and token.get('user_id')==payload['user_id']:matched.append(name)
    if len(matched)!=1:raise ValueError('identity_unmatched')
    return matched[0]


def _folder():return leave_gate.location()/'receipts'
def _key(receipt):
    if not isinstance(receipt,str) or not relay.OPAQUE.fullmatch(receipt):raise ValueError('invalid_receipt')
    return hashlib.sha256(receipt.encode()).hexdigest()


def _save(row):
    with server_files.directory(_folder(),create=True,private=True) as fd:
        name=_key(row['receipt'])+'.json'
        try:
            old=json.loads(server_files.read_at(fd,name,private=True))
            if old!=row:raise ValueError('receipt_binding_changed')
        except FileNotFoundError:pass
        server_files.replace_at(fd,name,server_files.encode(row),private=True)


def _records():
    try:
        with server_files.directory(_folder(),private=True) as fd:
            rows=[]
            for name in os.listdir(fd):
                if not re.fullmatch(r'[a-f0-9]{64}\.json',name):continue
                value=json.loads(server_files.read_at(fd,name,private=True))
                if (type(value) is not dict or set(value)!={'receipt','account','expires_at'}
                    or _key(value.get('receipt'))+'.json'!=name or not accounts.name_is_safe(value.get('account'))
                    or type(value['expires_at']) is not int):raise ValueError('receipt_unreadable')
                if value['expires_at']<=int(jst.now_jst().timestamp()*1000):
                    os.unlink(name,dir_fd=fd);os.fsync(fd);continue
                rows.append(value)
            return rows
    except FileNotFoundError:return []


def _event_exists(receipt,account):
    rows,_=admin_log.read(account=account,event='deletion_requested')
    return any(row.get('run_id')==_key(receipt) for row in rows)


def sync(*,by):
    admin_log.actor(by)
    with server_files.directory(leave_gate.location(),create=True,private=True) as fd:
        with server_files.lock_at(fd,'deletion.operation.lock'):
            return _sync(by=by)


def _sync(*,by):
    with admin_log.transaction():pass
    after=None;seen=set();verified=0;unmatched=0;discarded=0
    while True:
        page=relay.signed_request('deletion','inbox','list',{'after':after})
        if type(page) is not dict or set(page)!={'receipts','next'} or type(page['receipts']) is not list or len(page['receipts'])>50:raise ValueError('deletion_list_unreadable')
        for summary in page['receipts']:
            if type(summary) is not dict or not isinstance(summary.get('receipt'),str):raise ValueError('deletion_list_unreadable')
            receipt=summary['receipt'];_key(receipt)
            if receipt in seen:raise ValueError('deletion_list_unreadable')
            seen.add(receipt)
            row=relay.signed_request('deletion',receipt,'read',{})
            if type(row) is not dict or row.get('status') not in ('unverified','verified','completed') or type(row.get('expires_at')) is not int:raise ValueError('receipt_unreadable')
            if row['status']=='completed':continue
            if row['status']=='unverified':
                try:account=verify(row.get('signed_request'))
                except SignatureMismatch:
                    result=relay.signed_request('deletion',receipt,'discard',{'blob_sha256':hashlib.sha256(row['signed_request'].encode()).hexdigest()})
                    if result!={'status':'discarded'}:raise ValueError('receipt_discard_unconfirmed')
                    discarded+=1;continue
                except (OSError,ValueError,TypeError,accounts.AccountError):unmatched+=1;continue
                record={'receipt':receipt,'account':account,'expires_at':row['expires_at']}
                with admin_log.transaction():
                    _save(record)
                    if _event_exists(receipt,account):os.fsync(admin_log._active_fd.get())
                    else:
                        admin_log.repair_append_boundary()
                        admin_log.append('deletion_requested',account,{'media':'threads'},by=by,
                            diff={'request_received':[False,True]},run_id=_key(receipt))
                result=relay.signed_request('deletion',receipt,'verify',{'account':account,'blob_sha256':hashlib.sha256(row['signed_request'].encode()).hexdigest()})
                if result.get('status') not in ('verified','completed'):raise ValueError('receipt_verification_unconfirmed')
                verified+=1
            else:
                account=row.get('account')
                if not accounts.name_is_safe(account) or not _event_exists(receipt,account):raise ValueError('receipt_binding_unconfirmed')
                _save({'receipt':receipt,'account':account,'expires_at':row['expires_at']})
        after=page['next']
        if after is None:break
        if after not in seen or len(seen)>1000:raise ValueError('deletion_list_unreadable')
    return {'verified':verified,'unmatched':unmatched,'discarded_invalid_signature':discarded,'observed':len(seen)}


def complete_for(account,row):
    for record in _records():
        if record['account']!=account:continue
        if record['expires_at']<=int(jst.now_jst().timestamp()*1000):continue
        if not _event_exists(record['receipt'],account):raise ValueError('receipt_binding_unconfirmed')
        result=relay.signed_request('deletion',record['receipt'],'complete',{'account':account,'operation_id':row['operation_id'],'completed_at':int(jst.parse(row['completed_at']).timestamp()*1000)})
        if result!={'status':'completed'}:raise ValueError('receipt_completion_unconfirmed')


def command(args):
    try:
        result=sync(by=args.by)
        print(json.dumps(result,ensure_ascii=False) if args.json else 'deletion_receipts_checked: verified='+str(result['verified'])+' unmatched='+str(result['unmatched'])+' discarded_invalid_signature='+str(result['discarded_invalid_signature']))
        return 0
    except (OSError,ValueError,TypeError,accounts.AccountError,relay.RelayError,admin_log.AdminLogError):
        print('deletion_sync_incomplete: 未照合の受付を完了扱いにはしていません',file=sys.stderr)
        if args.json:print(json.dumps({'completed':False,'reason':'deletion_sync_incomplete'}))
        return 2


def register(commands):
    parser=commands.add_parser('deletion',help='媒体削除要求の署名を VM 内で照合（退出は別途 account leave）')
    operations=parser.add_subparsers(required=True);sync=operations.add_parser('sync')
    sync.add_argument('--by',required=True);sync.add_argument('--json',action='store_true');sync.set_defaults(func=command)
