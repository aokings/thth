"""Operator-only, resumable local stop, provider revocation and owned cleanup."""
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import stat
from . import accounts, admin_log, approval_relay, authclients, authflow, jst, leave_gate as gate, managed_repo, server_files

PHASES={'stopped','worker_revoked','remote_pending','remote_confirmed','manual_unconfirmed','deleting','deleted_pending_log','completed'}


def _save(row):
    raw=server_files.encode(row)
    if len(raw)>262144:raise ValueError('leave_inventory_too_large')
    with server_files.directory(gate.location(),create=True,private=True) as fd:
        server_files.replace_at(fd,row['account']+'.json',raw,private=True)


def read(account):
    if not accounts.name_is_safe(account):raise ValueError('invalid_account')
    try:
        with server_files.directory(gate.location(),private=True) as fd:
            value=json.loads(server_files.read_at(fd,account+'.json',private=True,maximum=262144))
    except FileNotFoundError:return None
    try:valid=_valid(value,account)
    except (ValueError,TypeError,KeyError,RecursionError):valid=False
    if not valid:raise ValueError('leave_journal_unreadable')
    return value


def _hash(value):return hashlib.sha256(server_files.encode(value)).hexdigest()


def _valid(row,account):
    if type(row) is not dict:return False
    required={'schema_version','account','phase','operation_id','stopped_at','stopped_by','remote','deleted'}
    optional={'completed_at','cfg','cfg_sha256','targets','inventory_sha256','preserved','token_shared','revoked'}
    if not required<=set(row) or set(row)-required-optional:return False
    if (type(row['schema_version']) is not int or row['schema_version']!=1 or row['account']!=account
        or not isinstance(row['phase'],str) or row['phase'] not in PHASES
        or not isinstance(row['operation_id'],str) or not approval_relay.OPAQUE.fullmatch(row['operation_id'])
        or not jst.parse(row['stopped_at']) or not isinstance(row['stopped_by'],str) or not row['stopped_by'] or row['remote'] not in ('pending','confirmed','unconfirmed_manual','unconfirmed_shared')):return False
    categories={'token','env','state','ledger','managed_repo'}
    if type(row['deleted']) is not dict or set(row['deleted'])-categories or any(type(n) is not int or n<0 for n in row['deleted'].values()):return False
    if 'preserved' in row and (type(row['preserved']) is not list or any(x not in ('token_shared','env_shared','external_repo') for x in row['preserved'])):return False
    if row['phase']=='completed':
        return set(row)==required|{'completed_at','preserved'} and bool(jst.parse(row['completed_at'])) and row['remote']!='pending'
    cfg=row.get('cfg')
    if type(cfg) is not dict or cfg.get('account')!=account or row.get('cfg_sha256')!=_hash(cfg):return False
    if type(row.get('revoked')) is not list or any(x not in ('access','refresh') for x in row['revoked']):return False
    if row['phase']=='stopped' and 'targets' not in row:return True
    if (type(row.get('targets')) is not list or len(row['targets'])>5 or type(row.get('token_shared')) is not bool
        or row.get('inventory_sha256')!=_hash(row['targets'])):return False
    root=Path(accounts.thth_root()).resolve();expected={'state':root/'state'/account,'ledger':root/'accounts'/(account+'.json'),'managed_repo':root/'repos/_server'/account}
    seen=set()
    for target in row['targets']:
        if type(target) is not dict or target.get('category') not in categories or target['category'] in seen:return False
        category=target['category'];seen.add(category)
        try:
            wanted=_path(cfg.get(category)) if category in ('token','env') else expected[category]
            if target.get('path')!=str(wanted):return False
            if category in ('token','env') and not _credential_owned(wanted,account):return False
        except (ValueError,TypeError):return False
        if 'tree' in target:
            if category not in ('state','managed_repo') or set(target)!={'category','path','tree'} or type(target['tree']) is not dict:return False
            for path,identity in target['tree'].items():
                if not isinstance(path,str) or path and (Path(path).is_absolute() or any(x in ('.','..') for x in Path(path).parts)):return False
                if not _valid_identity(identity):return False
        elif set(target)!={'category','path','identity'} or category in ('state','managed_repo') or target['identity'] is not None and not _valid_identity(target['identity'],file=True):return False
    return {'state','ledger'}<=seen


def _valid_identity(value,*,file=False):
    if type(value) is not dict or any(type(value.get(k)) is not int or value[k]<0 for k in ('dev','ino')):return False
    if value.get('directory') is True:return not file and set(value)=={'dev','ino','directory'}
    return (set(value)=={'dev','ino','mode','sha256'} and type(value['mode']) is int and 0<=value['mode']<=0o777
            and isinstance(value['sha256'],str) and bool(re.fullmatch(r'[a-f0-9]{64}',value['sha256'])))


def names():
    try:
        with server_files.directory(gate.location(),private=True) as fd:
            return sorted(n[:-5] for n in os.listdir(fd) if n.endswith('.json') and accounts.name_is_safe(n[:-5]))
    except FileNotFoundError:return []


def public(row):
    return {key:row[key] for key in ('account','phase','stopped_at','completed_at','remote','deleted','preserved','reason') if key in row}


@contextlib.contextmanager
def _parent(path):
    path=Path(path)
    root=Path(accounts.thth_root()).resolve()
    if path.is_relative_to(root):
        with server_files.directory(path.parent) as fd:yield fd
    else:
        # Only explicitly selected external credential leaves, never project trees.
        fd=authclients._directory(path)
        try:
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('private_credential_parent_required')
            yield fd
        finally:os.close(fd)


def _path(value):
    if not isinstance(value,str) or not os.path.isabs(value):raise ValueError('owned_path_invalid')
    # Host parent aliases are pinned after canonicalization; leaf is never resolved.
    return Path(value).parent.resolve()/Path(value).name


def _identity(path, *, private=False):
    try:
        with _parent(path) as fd:
            leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            with os.fdopen(leaf,'rb') as stream:
                info=os.fstat(stream.fileno());server_files.regular(info,private=private)
                if info.st_size>16777216:raise ValueError('owned_file_too_large')
                raw=stream.read(16777217)
        return {'dev':info.st_dev,'ino':info.st_ino,'sha256':hashlib.sha256(raw).hexdigest(),'mode':stat.S_IMODE(info.st_mode)}
    except FileNotFoundError:return None


def _tree(path):
    rows={}
    def visit(fd,relative):
        if len(rows)>100000 or len(Path(relative).parts)>64:raise ValueError('owned_tree_too_large')
        for name in os.listdir(fd):
            rel=str(Path(relative)/name);info=os.stat(name,dir_fd=fd,follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                try:
                    opened=os.fstat(child)
                    if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino) or opened.st_uid!=os.getuid() or opened.st_mode&0o022:raise ValueError('unsafe_owned_directory')
                    rows[rel]={'dev':info.st_dev,'ino':info.st_ino,'directory':True};visit(child,rel)
                finally:os.close(child)
            else:
                server_files.regular(info)
                rows[rel]=_identity(path/rel)
    try:
        with server_files.directory(path) as fd:
            info=os.fstat(fd);rows['']={'dev':info.st_dev,'ino':info.st_ino,'directory':True};visit(fd,'')
    except FileNotFoundError:return {}
    return rows


def _registry():
    rows={}
    for name in accounts.list_account_names():
        with gate.recovery(name):rows[name]=accounts.load_account(name)
    return rows


def _credential_owned(path,account):
    root=Path(accounts.thth_root()).resolve()
    # Configured path alone is not ownership. Account-private state and a named
    # secret leaf are the two server namespaces; host legacy leaves are exact.
    if path.parent==root/'secrets':
        owners=set(accounts.list_account_names())|set(names())
        if any(other!=account and (path.name==other or path.name.startswith(other+'.')) for other in owners):return False
    return (path.is_relative_to(root/'state'/account)
        or path.parent==root/'secrets' and (path.name==account or path.name.startswith(account+'.'))
        or path.parent==(Path.home()/'.config/thth').resolve() and path.name in (account+'.token',account+'.env'))


def _shared_globals():
    from . import appenv
    apps=Path(os.environ.get('THTH_APPS_DIR') or Path.home()/'.config/thth/apps').resolve()
    try:signer=_path(str(approval_relay.key_path()))
    except approval_relay.RelayError:raise ValueError('global_signer_location_unsafe') from None
    return [_path(appenv.default_path()),apps,signer]


def inventory(account,cfg):
    root=Path(accounts.thth_root()).resolve();registry=_registry();targets=[];preserved=[]
    token=accounts.load_token(cfg) or {}
    if type(token) is not dict:raise ValueError('credential_unreadable')
    token_shared=False
    for category in ('token','env'):
        value=cfg.get(category)
        if not value:continue
        path=_path(value);identity=_identity(path,private=True)
        if identity is None:continue
        shared=False
        for name,other in registry.items():
            if name==account:continue
            for key in ('token','env'):
                if not other.get(key):continue
                other_path=_path(other[key]);other_id=_identity(other_path,private=True)
                if path==other_path or other_id and (identity['dev'],identity['ino'])==(other_id['dev'],other_id['ino']):shared=True
            if category=='token' and other.get('media')==cfg.get('media'):
                with gate.recovery(name):other_token=accounts.load_token(other) or {}
                if type(other_token) is not dict:raise ValueError('ownership_unproved')
                credentials={'access_token','refresh_token','app_password'}
                same_value=any(isinstance(token.get(k),str) and token[k] and token[k]==other_token.get(k) for k in credentials)
                same_origin=(cfg.get('instance'),cfg.get('service'))==(other.get('instance'),other.get('service'))
                same_grant=same_origin and token.get('user_id') and token.get('user_id')==other_token.get('user_id')
                if same_value or same_grant:shared=True
        from . import appenv
        app_directory=Path(os.environ.get('THTH_APPS_DIR') or Path.home()/'.config/thth/apps').resolve()
        if path==_path(appenv.default_path()) or path.is_relative_to(app_directory):shared=True
        if shared:
            preserved.append(category+'_shared')
            if category=='token':token_shared=True
            continue
        if not _credential_owned(path,account):raise ValueError('credential_ownership_unproved')
        targets.append({'category':category,'path':str(path),'identity':identity})
    clone,origin=managed_repo.locations(account)
    if cfg.get('repo_dir')==str(clone):
        if clone.exists():
            managed_repo.validate(clone)
            for name,other in registry.items():
                if name!=account and other.get('repo_dir') and Path(other['repo_dir']).resolve()==clone:raise ValueError('managed_repo_shared')
            targets.append({'category':'managed_repo','path':str(clone),'tree':_tree(clone)})
    elif cfg.get('repo_dir'):preserved.append('external_repo')
    state=Path(accounts.state_dir_for(account)).absolute()
    for name,other in registry.items():
        if name==account:continue
        for key in ('token','env','repo_dir'):
            value=other.get(key)
            if value and (_path(value).is_relative_to(state) or cfg.get('repo_dir')==str(clone) and _path(value).is_relative_to(clone)):
                raise ValueError('owned_tree_shared')
    trees=[state]+([clone] if cfg.get('repo_dir')==str(clone) else [])
    if any(path.is_relative_to(tree) or tree.is_relative_to(path) for path in _shared_globals() for tree in trees):
        raise ValueError('owned_tree_contains_global_credentials')
    targets.append({'category':'state','path':str(state),'tree':_tree(state)})
    ledger=Path(accounts.accounts_dir()).absolute()/(account+'.json')
    if ledger.parent!=root/'accounts':raise ValueError('external_ledger_ownership_unproved')
    targets.append({'category':'ledger','path':str(ledger),'identity':_identity(ledger)})
    return targets,sorted(set(preserved)),token_shared


def revoke(cfg,token,progress,save):
    media=cfg.get('media')
    if media in ('threads','bluesky'):return 'unconfirmed_manual'
    if media=='mastodon':
        from .adapters.auth_mastodon import origin,valid_client,request
        base=origin(cfg.get('instance'));client=valid_client(authclients.read(authclients.path_for(media,base,cfg)),base)
        access=token.get('access_token')
        if not isinstance(access,str) or not access:raise ValueError('revoke_credential_unavailable')
        if 'access' not in progress:
            result=request(base,'/oauth/revoke',data={'client_id':client['client_id'],'client_secret':client['client_secret'],'token':access})
            if result!={}:raise ValueError('remote_revoke_unconfirmed')
            progress.append('access');save()
        return 'confirmed'
    if media=='x':
        from .adapters.auth_x import XAuthProfile,request,credential
        profile=XAuthProfile.prepare(cfg)
        for label,key in (('refresh','refresh_token'),('access','access_token')):
            value=token.get(key)
            if label=='refresh' and not value:continue
            credential(value)
            if label in progress:continue
            result=request('/2/oauth2/revoke',pair=(profile.client_id,profile.client_secret),data={'token':value})
            if result not in ({},{'revoked':True}):raise ValueError('remote_revoke_unconfirmed')
            progress.append(label);save()
        return 'confirmed'
    raise ValueError('remote_revoke_unsupported')


def _protect_targets(row):
    targets=row['targets'];globals=_shared_globals()
    for target in targets:
        path=Path(target['path'])
        if any(path==protected or 'tree' in target and (protected.is_relative_to(path) or path.is_relative_to(protected)) for protected in globals):
            raise ValueError('owned_target_contains_global_credentials')
    for name,cfg in _registry().items():
        if name==row['account']:continue
        for key in ('token','env','repo_dir'):
            value=cfg.get(key)
            if not value:continue
            selected=_path(value)
            if any(selected==Path(t['path']) or 'tree' in t and selected.is_relative_to(Path(t['path'])) for t in targets):
                raise ValueError('owned_target_now_shared')


def _delete(target):
    path=Path(target['path'])
    if 'tree' not in target:
        current=_identity(path,private=target['category'] in ('token','env'))
        if current is None:return
        if current!=target['identity']:raise ValueError('owned_file_changed')
        with _parent(path) as fd:os.unlink(path.name,dir_fd=fd);os.fsync(fd)
        return
    expected=target['tree'];current=_tree(path)
    if any(key not in expected or value!=expected[key] for key,value in current.items()):raise ValueError('owned_tree_changed')
    for rel in sorted(current,key=lambda p:len(Path(p).parts),reverse=True):
        node=path/rel if rel else path
        with _parent(node) as fd:
            info=os.stat(node.name,dir_fd=fd,follow_symlinks=False);known=current[rel]
            if (info.st_dev,info.st_ino)!=(known['dev'],known['ino']):raise ValueError('owned_file_changed')
            if known.get('directory'):os.rmdir(node.name,dir_fd=fd)
            else:
                server_files.regular(info);os.unlink(node.name,dir_fd=fd)
            os.fsync(fd)


def _confirm_completion(row):
    # A previous append/rename may have succeeded before fsync failed. Re-read
    # and synchronize both records before a retry reports durable completion.
    with admin_log.transaction():
        entries,_=admin_log.read(account=row['account'],event='account_removed')
        if not any(e.get('run_id')==row['operation_id'] for e in entries):
            raise ValueError('leave_completion_log_missing')
        os.fsync(admin_log._active_fd.get())
    with server_files.directory(gate.location(),private=True) as fd:
        leaf=os.open(row['account']+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:server_files.regular(os.fstat(leaf),private=True);os.fsync(leaf)
        finally:os.close(leaf)
        os.fsync(fd)
    return public(row)


def run(account,*,by):
    admin_log.actor(by)
    if not accounts.name_is_safe(account):raise ValueError('invalid_account')
    # Serialize leave attempts outside every deletable directory. This lock is
    # not used by normal operations and never reverses their repo/account order.
    with server_files.directory(gate.location(),create=True,private=True) as fd:
        with server_files.lock_at(fd,account+'.operation.lock'):
            result=_run_locked(account,by=by)
            from . import deletion
            deletion.complete_for(account,read(account))
            return result


def _run_locked(account,*,by):
    row=read(account)
    if row and row['phase']=='completed':return _confirm_completion(row)
    with gate.recovery(account):cfg=accounts.load_account(account) if not row else row['cfg']
    if cfg.get('account')!=account:raise ValueError('account_binding_invalid')
    # Only the first stop drains legacy repo/account operations. Recovery uses
    # the stable lease alone, so it cannot recreate deleted state/<account>/lock.
    drain=server_files.account_locks(account,cfg) if row is None else contextlib.nullcontext()
    with drain,gate.lease(account,exclusive=True),gate.recovery(account),gate.scope(account):
        row=read(account)
        if row and row['phase']=='completed':return _confirm_completion(row)
        if row is None:
            row=dict(schema_version=1,account=account,operation_id=secrets.token_urlsafe(32),phase='stopped',stopped_at=jst.iso(),stopped_by=admin_log.clean(by),cfg=dict(cfg),cfg_sha256=_hash(dict(cfg)),remote='pending',deleted={},revoked=[])
            _save(row)
        try:
            if row['phase']=='stopped':
                targets,preserved,shared=inventory(account,cfg)
                row.update(targets=targets,inventory_sha256=_hash(targets),preserved=preserved,token_shared=shared);_save(row)
                with admin_log.transaction():pass  # fail before remote effects on known-bad audit destination
                result=approval_relay.signed_request('account',account,'revoke',{})
                if result!={'status':'revoked'}:raise ValueError('worker_revoke_unconfirmed')
                row['phase']='worker_revoked';_save(row)
            if row['phase'] in ('worker_revoked','remote_pending'):
                with gate.credentials(exclusive=True):
                    _,_,currently_shared=inventory(account,cfg)
                    if currently_shared and cfg.get('media') not in ('threads','bluesky'):raise ValueError('shared_credential_revoke_refused')
                    if row['token_shared'] and cfg.get('media') not in ('threads','bluesky'):raise ValueError('shared_credential_revoke_refused')
                    if currently_shared and not row['token_shared']:
                        row['token_shared']=True
                        row['targets']=[target for target in row['targets'] if target['category']!='token']
                        row['inventory_sha256']=_hash(row['targets'])
                        row['preserved']=sorted(set(row['preserved'])|{'token_shared'})
                    row['phase']='remote_pending';_save(row)
                    token=accounts.load_token(cfg) or {}
                    # Same saved credential bytes; never revoke a replacement made by a different operator.
                    own=next((t for t in row['targets'] if t['category']=='token'),None)
                    if own and _identity(Path(own['path']),private=True)!=own['identity']:raise ValueError('credential_changed')
                    row['remote']=revoke(cfg,token,row['revoked'],lambda:_save(row))
                    if row['token_shared'] and row['remote']=='unconfirmed_manual':row['remote']='unconfirmed_shared'
                    row['phase']='remote_confirmed' if row['remote']=='confirmed' else 'manual_unconfirmed';_save(row)
            if row['phase'] in ('remote_confirmed','manual_unconfirmed','deleting'):
                with gate.credentials(exclusive=True),admin_log.transaction():
                    _protect_targets(row)
                    row['phase']='deleting';_save(row)
                    for target in row['targets']:
                        if target['category'] in row['deleted']:continue
                        _delete(target)
                        row['deleted'][target['category']]=len(target.get('tree',{})) if 'tree' in target else int(target['identity'] is not None)
                        _save(row)
                    row['phase']='deleted_pending_log';_save(row)
            if row['phase']=='deleted_pending_log':
                with admin_log.transaction():
                    entries,_=admin_log.read(account=account,event='account_removed')
                    if any(e.get('run_id')==row['operation_id'] for e in entries):
                        os.fsync(admin_log._active_fd.get())
                    else:
                        admin_log.repair_append_boundary()
                        admin_log.append('account_removed',account,{'media':cfg.get('media')},by=by,
                            diff={'deleted_categories':[[],sorted(row['deleted'])],
                                  'deleted_counts':[[],[row['deleted'][k] for k in sorted(row['deleted'])]], 'remote':[None,row['remote']]},run_id=row['operation_id'])
                row={k:row[k] for k in ('schema_version','account','operation_id','stopped_at','stopped_by','remote','deleted','preserved')}
                row.update(phase='completed',completed_at=jst.iso());_save(row)
            return public(row)
        except BaseException:
            # Never roll back the stop or delete the only retry credential on an error.
            raise


def command(args):
    import sys
    from .lock import LockBusy
    try:
        result=run(args.name,by=args.by)
        if args.json:print(json.dumps(result,ensure_ascii=False))
        else:
            print('local_complete: '+args.name)
            if result['remote']=='unconfirmed_shared':print('remote_unconfirmed: 他 account と共有の接続です。解除すると他 account に影響するため、運用者が解除対象を切り分けてください')
            if result['remote']=='unconfirmed_manual':print('remote_unconfirmed: Threads の接続設定、または Bluesky の App Password を本人が解除してください')
        return 0
    except (OSError,ValueError,accounts.AccountError,approval_relay.RelayError,admin_log.AdminLogError,LockBusy):
        print('leave_incomplete: 停止状態と再試行用の認証情報を確認し、同じ leave を再実行してください',file=sys.stderr)
        if args.json:print(json.dumps({'account':args.name,'completed':False,'reason':'leave_incomplete'},ensure_ascii=False))
        return 2
