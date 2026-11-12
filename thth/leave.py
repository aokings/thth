"""Operator-only, resumable local stop, provider revocation and owned cleanup."""
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import stat
from . import accounts, admin_log, article_media, relay, authclients, authflow, jst, leave_gate as gate, managed_repo, server_files, timer_cleanup

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
    optional={'completed_at','cfg','cfg_sha256','targets','inventory_sha256','preserved','token_shared','revoked','timers'}
    if not required<=set(row) or set(row)-required-optional:return False
    if (type(row['schema_version']) is not int or row['schema_version']!=1 or row['account']!=account
        or not isinstance(row['phase'],str) or row['phase'] not in PHASES
        or not isinstance(row['operation_id'],str) or not relay.OPAQUE.fullmatch(row['operation_id'])
        or not jst.parse(row['stopped_at']) or not isinstance(row['stopped_by'],str) or not row['stopped_by'] or row['remote'] not in ('pending','confirmed','unconfirmed_manual','unconfirmed_shared')):return False
    categories={'token','env','state','ledger','managed_repo'}
    if type(row['deleted']) is not dict or set(row['deleted'])-categories or any(type(n) is not int or n<0 for n in row['deleted'].values()):return False
    if 'preserved' in row and (type(row['preserved']) is not list or any(x not in ('token_shared','env_shared','external_repo','principal_token') for x in row['preserved'])):return False
    if row['phase']=='completed':
        # `timers`（3.5.1 件 1）は完了のあとに 1 回だけ足す記録。無い row（3.5.0 までの
        # 完了）もそのまま読む。
        keys=required|{'completed_at','preserved'}
        if set(row)-{'timers'}!=keys or 'timers' in row and not timer_cleanup.valid_record(row['timers'],account):return False
        return bool(jst.parse(row['completed_at'])) and row['remote']!='pending'
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
    return {key:row[key] for key in ('account','phase','stopped_at','completed_at','remote','deleted','preserved','reason','timers') if key in row}


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


def _file(path, *, private=False):
    try:
        with _parent(path) as fd:
            leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            with os.fdopen(leaf,'rb') as stream:
                info=os.fstat(stream.fileno());server_files.regular(info,private=private)
                if info.st_size>16777216:raise ValueError('owned_file_too_large')
                raw=stream.read(16777217)
        if len(raw)>16777216:raise ValueError('owned_file_too_large')
        return raw,{'dev':info.st_dev,'ino':info.st_ino,'sha256':hashlib.sha256(raw).hexdigest(),'mode':stat.S_IMODE(info.st_mode)}
    except FileNotFoundError:return None,None


def _identity(path, *, private=False):
    return _file(path,private=private)[1]


def _token(cfg):
    if not cfg.get('token'):return {},None
    raw,identity=_file(_path(cfg['token']),private=True)
    if raw is None:return {},None
    from .handoff_cursor import _pairs
    token=json.loads(raw,object_pairs_hook=_pairs)
    if type(token) is not dict:raise ValueError('credential_unreadable')
    return token,identity


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
                    if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino) or opened.st_uid!=os.getuid() or opened.st_mode&0o022:
                        # どのディレクトリかを添える（運用者が chmod go-w で直せるように・3.14.3 穴 7）。
                        unsafe=ValueError('unsafe_owned_directory');unsafe.path=str(path/rel);raise unsafe
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
    try:signer=_path(str(relay.key_path()))
    except relay.RelayError:raise ValueError('global_signer_location_unsafe') from None
    return [_path(appenv.default_path()),apps,signer]


def _principal_owns_token(cfg):
    """口座の `token` の持ち主が主体か（記事の口座・5.18.1）。

    記事の口座（WordPress・はてなブログ）の `token` は、主体（`thth principal`）の鍵のパスを写したもの
    （`account_cli` の add・`principals.check_account` の 4）。鍵を書くのも消すのも主体の口
    （`thth principal auth <主体> --forget`）で、口座の leave は数えない・読まない・消さない。
    """
    return article_media.is_article_account(cfg)


class PrincipalCredentialsInside(ValueError):
    """主体の鍵（はてなは consumer も）が、口座の leave で消す所の中にある（監査 5.18.1 直したほうがよい 1）。"""
    def __init__(self,principal,where):
        super().__init__('owned_tree_contains_principal_credentials')
        self.principal=principal;self.where=where


def _principal_credentials(cfg):
    """記事の口座が使う主体の鍵のパス（`[(欄, Path)]`）。口座の台帳の `token`（主体の鍵の写し）と、主体の台帳の
    `token`・`consumer`。値は読まない（パスとファイルの同一性だけ）。主体の台帳が無ければ口座の写しだけを見る。"""
    from . import principals
    rows=[]
    if cfg.get('token'):rows.append(('token',cfg['token']))
    name=cfg.get('principal')
    if isinstance(name,str) and accounts.name_is_safe(name):
        try:p=principals.load_principal(name)
        except principals.PrincipalError as exc:
            # 台帳が壊れていると、どこに鍵があるかを言えない——消す前に止める（無いだけなら口座の写しで見る）。
            if exc.code!='principal_missing':raise ValueError('principal_unreadable') from None
        else:
            for key in ('token','consumer'):
                if isinstance(p.get(key),str) and os.path.isabs(p[key]):rows.append((key,p[key]))
    out=[]
    for key,value in rows:
        try:path=_path(value)
        except (ValueError,OSError):continue
        if (key,path) not in out:out.append((key,path))
    return out


def _guard_principal_credentials(cfg,targets,trees):
    """主体の鍵が消す木（state・管理する repo）の中か、消す env と同じファイルなら、何も消さずに止める。

    `thth principal add` が書く既定の置き場（`~/.config/thth/<主体>.token`）では起きない。主体の台帳を手で
    書き換えて鍵を口座の置き場に置いた形を、消してから「残しました」と言わないように、消す前に見る。"""
    principal=cfg.get('principal') if isinstance(cfg.get('principal'),str) and accounts.name_is_safe(cfg.get('principal') or '') else None
    envs=[target for target in targets if target['category']=='env']
    for key,path in _principal_credentials(cfg):
        where=None
        for tree in trees:
            if path.is_relative_to(tree) or path.is_relative_to(tree.resolve()):where='state' if tree==trees[0] else 'managed_repo'
        if where is None:
            identity=None
            for target in envs:
                if Path(target['path'])==path:where='env';break
                if identity is None:
                    try:identity=_identity(path,private=True) or {}
                    except (ValueError,OSError):identity={}
                own=target.get('identity') or {}
                if identity and own and (identity['dev'],identity['ino'])==(own['dev'],own['ino']):where='env';break
        if where:raise PrincipalCredentialsInside(principal,where)


def inventory(account,cfg):
    root=Path(accounts.thth_root()).resolve();registry=_registry();targets=[];preserved=[]
    principal_token=_principal_owns_token(cfg)
    # 主体の鍵は読まない（口座の退出に要らない秘密を開けない）。
    token,token_identity=({},None) if principal_token else _token(cfg)
    if type(token) is not dict:raise ValueError('credential_unreadable')
    token_shared=False
    for category in ('token','env'):
        value=cfg.get(category)
        if not value:continue
        if category=='token' and principal_token:
            # 主体の鍵は主体の物——同じ主体のほかの口座の有無によらず残す（5.18.1）。
            preserved.append('principal_token');continue
        path=_path(value);identity=token_identity if category=='token' else _identity(path,private=True)
        if identity is None:continue
        # **ファイルの共有と値の共有を分ける**（3.1.2 件 6・masaru 裁定 09-23）。
        # shared: 同じ path・同じ dev/ino・app directory 配下・同じ利用者（Threads／
        # Bluesky）——自分のファイルを消すと相手も困るので残す（preserved）。
        # value_shared: 別ファイルだが token の値が同じ——遠隔の失効だけ投げず、
        # 自分のファイルは消す（消しても失効にはならず、相手は自分のファイルを持つ。
        # 退出した account の秘密を VM に残さない）。
        shared=False;value_shared=False
        for name,other in registry.items():
            if name==account:continue
            for key in ('token','env'):
                if not other.get(key):continue
                other_path=_path(other[key]);other_id=_identity(other_path,private=True)
                if path==other_path or other_id and (identity['dev'],identity['ino'])==(other_id['dev'],other_id['ino']):shared=True
            if category=='token' and other.get('media')==cfg.get('media'):
                with gate.recovery(name):other_token,_=_token(other)
                if type(other_token) is not dict:raise ValueError('ownership_unproved')
                credentials={'access_token','refresh_token','app_password'}
                same_value=any(isinstance(token.get(k),str) and token[k] and token[k]==other_token.get(k) for k in credentials)
                same_origin=(cfg.get('instance'),cfg.get('service'))==(other.get('instance'),other.get('service'))
                same_grant=same_origin and token.get('user_id') and token.get('user_id')==other_token.get('user_id')
                # Mastodon と X の失効は渡した token の値だけに効く（同じ利用者の別 token は
                # 生き残る）。共有と見るのは値・ファイルが同じときだけにし、同じ利用者の別
                # 台帳があるだけで退出が worker_revoked で止まらないようにする。Threads と
                # Bluesky は遠隔の解除が本人の手なので、従前どおり同じ利用者を共有と見る。
                if same_grant and cfg.get('media') in ('mastodon','x'):same_grant=False
                if same_grant:shared=True
                if same_value:value_shared=True
        from . import appenv
        app_directory=Path(os.environ.get('THTH_APPS_DIR') or Path.home()/'.config/thth/apps').resolve()
        if path==_path(appenv.default_path()) or path.is_relative_to(app_directory):shared=True
        if shared:
            preserved.append(category+'_shared')
            if category=='token':token_shared=True
            continue
        if value_shared:token_shared=True
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
    if principal_token:_guard_principal_credentials(cfg,targets,trees)
    targets.append({'category':'state','path':str(state),'tree':_tree(state)})
    ledger=Path(accounts.accounts_dir()).absolute()/(account+'.json')
    if ledger.parent!=root/'accounts':raise ValueError('external_ledger_ownership_unproved')
    targets.append({'category':'ledger','path':str(ledger),'identity':_identity(ledger)})
    return targets,sorted(set(preserved)),token_shared


def revoke(cfg,token,progress,save):
    media=cfg.get('media')
    # Facebook ページの鍵の取り消しは、ページの持ち主が Meta の画面（ビジネス連携）で外す。
    if media in ('threads','bluesky','facebook','instagram'):return 'unconfirmed_manual'
    # 記事の口座（5.18.1）: 鍵は主体の物で、遠隔の取り消しは本人の手（WordPress はプロフィールのアプリケーション
    # パスワード、はてなは連携アプリの解除）。THTH が WordPress に送る道（投稿・カテゴリー・タグ・users/me）にも、
    # はてなの AtomPub にも、取り消しは入れていない。
    if article_media.is_article_media(media):return 'unconfirmed_manual'
    if media=='mastodon':
        from .adapters.auth_mastodon import client_for_token,request
        base,client=client_for_token(cfg,token)
        access=token.get('access_token')
        if not isinstance(access,str) or not access:raise ValueError('revoke_credential_unavailable')
        if 'access' not in progress:
            result=request(base,'/oauth/revoke',data={'client_id':client['client_id'],'client_secret':client['client_secret'],'token':access})
            if result!={}:raise ValueError('remote_revoke_unconfirmed')
            progress.append('access');save()
        return 'confirmed'
    if media=='x':
        from .adapters.auth_x import client_for_token,request,credential
        # 取消は**その token を出した client の世代**で行う（Mastodon と同じ規律）。
        pair,_=client_for_token(cfg,token)
        for label,key in (('refresh','refresh_token'),('access','access_token')):
            value=token.get(key)
            if label=='refresh' and not value:continue
            credential(value)
            if label in progress:continue
            result=request('/2/oauth2/revoke',pair=pair,data={'token':value})
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


PLAZA_OPEN=('delete','keep')


def run(account,*,by,plaza_open='delete'):
    admin_log.actor(by)
    if not accounts.name_is_safe(account):raise ValueError('invalid_account')
    if plaza_open not in PLAZA_OPEN:raise ValueError('invalid_plaza_open')
    # Serialize leave attempts outside every deletable directory. This lock is
    # not used by normal operations and never reverses their repo/account order.
    with server_files.directory(gate.location(),create=True,private=True) as fd:
        with server_files.lock_at(fd,account+'.operation.lock'):
            # 観測の地図（設計 3.5.0 §2・照合 §1）: 最後の account なら project の地図を丸ごと、
            # 他の account が残るならこの媒体の集計の行を消す。**止める前に消す**——地図の置き場が
            # 壊れていれば何も止めずに断る（下の変更ログの事前検査と同じ筋）。何度呼んでも同じ結果。
            _purge_map(account,by)
            # 店を読む口（設計 ショップ §7）: その project の最後の account なら、店の台帳・写し・鍵も消す。
            # 地図と同じく止める前に消す。何度呼んでも同じ結果。
            _purge_stores(account,by)
            result=_run_locked(account,by=by)
            # 削除の段のあとに unit を止める（3.5.1 件 1）。止められなくても退出は完了のまま
            # ——台帳も秘密も消えていて、残った timer は空回りするだけ。記録に残して命令を渡す。
            result=_stop_units(account) or result
            from . import deletion
            deletion.complete_for(account,read(account))
            # 施策の広場（設計 3.4.0 §6）: この account が置いた project 範囲の書き込みは
            # 消す。open の書き込みは退出時の選択で「退出した持ち主」の名義で残すか消す
            # （既定は消す）。何度呼んでも同じ結果なので、失敗したら leave を再実行すれば
            # 続きから消える（`leave_incomplete`）。
            from . import plaza
            try:plaza.purge_account(account,keep_open=plaza_open=='keep',by=by)
            except plaza.PlazaError:raise ValueError('plaza_cleanup_incomplete') from None
            # 招待リンク（3.10.0）で足したその口座だけの資格情報を消す。何度呼んでも同じ結果。
            if (read(account) or {}).get('phase')=='completed':
                from . import invites
                invites.forget_credential(account,by=by)
            return result


def _stop_units(account):
    # 完了した row に 1 回だけ試す（`sudo -n` を 1 回）。記録があれば再実行でも呼ばない
    # ——leave を打ち直すたびに sudo を叩かない。残った unit は記録の命令で運用者が止める。
    row=read(account)
    if row is None or row['phase']!='completed':return None
    if 'timers' not in row:
        row['timers']=timer_cleanup.stop_for_leave(account);_save(row)
    return public(row)


def _purge_map(account,by):
    from . import map_store,map_world
    row=read(account)
    if row and row['phase']=='completed':return None
    if row:cfg=row['cfg']
    else:
        try:cfg=accounts.load_account(account)
        except accounts.AccountError:return None
    project=cfg.get('project')
    if not project:return None
    try:return map_world.purge_for_leave(account,project,cfg.get('media'),by=by)
    except map_store.MapError:raise ValueError('map_cleanup_incomplete') from None


def _purge_stores(account,by):
    from .shop import remove as store_remove
    from .shop.wire import StoreRefused
    row=read(account)
    if row and row['phase']=='completed':return []
    if row:cfg=row['cfg']
    else:
        try:cfg=accounts.load_account(account)
        except accounts.AccountError:return []
    try:return store_remove.purge_for_leave(account,cfg.get('project'),by=by)
    except (StoreRefused,OSError,admin_log.AdminLogError):raise ValueError('store_cleanup_incomplete') from None


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
                result=relay.signed_request('account',account,'revoke',{})
                if result!={'status':'revoked'}:raise ValueError('worker_revoke_unconfirmed')
                row['phase']='worker_revoked';_save(row)
            if row['phase'] in ('worker_revoked','remote_pending'):
                with gate.credentials(exclusive=True):
                    current_targets,current_preserved,currently_shared=inventory(account,cfg)
                    if currently_shared and not row['token_shared']:row['token_shared']=True
                    has_token=any(target['category']=='token' for target in row['targets'])
                    if _principal_owns_token(cfg):
                        # 記事の口座（5.18.1）: 主体の鍵は消す対象から外し、共有の接続とも数えない。5.18.0 で
                        # 止まった row（主体の鍵を消す対象に入れて remote_revoke_unsupported）もここで直す。
                        row['targets']=[target for target in row['targets'] if target['category']!='token']
                        row['preserved']=sorted((set(row['preserved'])-{'token_shared'})|{'principal_token'})
                        row['token_shared']=False
                    elif 'token_shared' in current_preserved and has_token:
                        # いまはファイルごとの共有——自分のファイルも消さない。
                        row['targets']=[target for target in row['targets'] if target['category']!='token']
                        row['preserved']=sorted(set(row['preserved'])|{'token_shared'})
                    elif ('token_shared' in row['preserved'] and 'token_shared' not in current_preserved
                          and not has_token):
                        # 3.1.1 の形で止まった row（値だけの共有でも token を残す対象にしていた）。
                        # いまのファイルの同一性で消す対象に戻す（裁定: 秘密を VM に残さない）。
                        own=next((target for target in current_targets if target['category']=='token'),None)
                        if own is not None:
                            row['targets']=[own]+list(row['targets'])
                            row['preserved']=sorted(set(row['preserved'])-{'token_shared'})
                    row['inventory_sha256']=_hash(row['targets'])
                    if row['token_shared']:
                        # **共有の接続は遠隔で失効させず、そのまま退出を終える**（3.1.2 件 6・
                        # 実測 09-23: 同じ app で同じ利用者が認可すると Mastodon は既存の
                        # access token を返すので、別台帳でも値が同じになる）。失効すると他の
                        # 生きている account も死ぬので投げない——が、止まる理由にもしない。
                        # 値だけの共有なら自分の token file は消す対象に残る（消しても失効に
                        # はならない）。ファイルごとの共有なら残す（preserved）。解除の切り分け
                        # は運用者に渡す（`remote_unconfirmed` の 1 行）。
                        if ('token_shared' in row['preserved']
                                and any(target['category']=='token' for target in row['targets'])):
                            # ファイルごと共有なのに消す対象に token が残っている——矛盾なので止める。
                            raise ValueError('shared_credential_revoke_refused')
                        row['remote']='unconfirmed_shared'
                        row['phase']='manual_unconfirmed';_save(row)
                    else:
                        row['phase']='remote_pending';_save(row)
                        token,token_identity=({},None) if _principal_owns_token(cfg) else _token(cfg)
                        # Same saved credential bytes; never revoke a replacement made by a different operator.
                        own=next((t for t in row['targets'] if t['category']=='token'),None)
                        if own and token_identity!=own['identity']:raise ValueError('credential_changed')
                        row['remote']=revoke(cfg,token,row['revoked'],lambda:_save(row))
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
                        diff={'deleted_categories':[[],sorted(row['deleted'])],
                              'deleted_counts':[[],[row['deleted'][k] for k in sorted(row['deleted'])]], 'remote':[None,row['remote']]}
                        # 記事の口座は主体の名を残す（完了の案内が、打ち直しでも主体の鍵の残りと消し方を言えるように）。
                        if _principal_owns_token(cfg) and accounts.name_is_safe(cfg.get('principal') or ''):
                            diff['principal']=[None,cfg['principal']]
                        admin_log.append('account_removed',account,{'media':cfg.get('media')},by=by,diff=diff,run_id=row['operation_id'])
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
        result=run(args.name,by=args.by,plaza_open=getattr(args,'plaza_open',None) or 'delete')
        if args.json:print(json.dumps(result,ensure_ascii=False))
        else:
            print('local_complete: '+args.name)
            if result['remote']=='unconfirmed_shared':
                for line in _shared_guide(args.name):print(line)
            if result['remote']=='unconfirmed_manual':
                for line in _manual_guide(args.name,result):print(line)
            timers=result.get('timers') or {}
            if timers.get('pending'):
                # 貼ってそのまま打てる形（unit 名は台帳の名前から組んだものだけ）。
                print('timer_cleanup_pending: '+timer_cleanup.stop_command(timers['pending']))
            elif timers.get('reason')=='unit_listing_failed':
                print('timer_cleanup_unknown: systemctl list-unit-files '+' '.join(timer_cleanup.account_units(args.name))+' で残りを確かめ、あれば sudo systemctl disable --now <unit> で止めてください')
        return 0
    except (OSError,ValueError,accounts.AccountError,relay.RelayError,admin_log.AdminLogError,LockBusy) as exc:
        detail=_incomplete_detail(exc)
        print('leave_incomplete: '+detail+'。停止状態と再試行用の認証情報を確認し、同じ leave を再実行してください',file=sys.stderr)
        if args.json:print(json.dumps({'account':args.name,'completed':False,'reason':'leave_incomplete','detail':detail},ensure_ascii=False))
        return 2


# 遠隔の取り消しが本人の手の媒体ごとの 1 行（5.18.1 で媒体ごとに分けた。前は Threads・Bluesky の決め打ち）。
# 言い方は privacy の「停止とデータの削除」の節にそろえる。
MANUAL_REVOKE={
    'threads':'Threads の接続の解除は本人の手です。Threads の設定で THTH との接続を解除してください',
    'bluesky':'Bluesky の App Password の削除は本人の手です。Bluesky の設定で THTH に入れた App Password を削除してください',
    'facebook':'Facebook ページの許可の解除は本人の手です。Facebook の設定（ビジネス連携）で THTH を外してください',
    'instagram':'Instagram の許可の解除は本人の手です。Facebook の設定（ビジネス連携）で THTH を外してください',
    'wordpress':('WordPress のアプリケーションパスワードの取り消しは本人の手です（THTH が WordPress に送る道は投稿・カテゴリー・'
                 'タグ・users/me で、取り消しは入れていません）。WordPress の管理画面の「ユーザー」→「プロフィール」の'
                 '「アプリケーションパスワード」で、THTH に入れたものを取り消してください'),
    'hatenablog':('はてなの連携の解除は本人の手です（THTH ははてなに連携を外す要求を送りません）。はてなのアカウントの設定の'
                  '連携アプリ（OAuth で許したアプリ）から THTH を外してください'),
}


def _removed_entry(account):
    """この口座の最後の `account_removed` の記録（媒体と、記事の口座なら主体の名）。読めなければ `{}`。"""
    try:entries,_=admin_log.read(account=account,event='account_removed')
    except (OSError,ValueError,admin_log.AdminLogError):return {}
    return entries[-1] if entries else {}


def _manual_guide(account,result):
    """`unconfirmed_manual` の完了の案内（媒体ごとの 1 行と、記事の口座なら主体の鍵の残りと消し方）。"""
    entry=_removed_entry(account);media=entry.get('medium')
    principal=((entry.get('diff') or {}).get('principal') or [None,None])[-1]
    line=MANUAL_REVOKE.get(media) or '遠隔の許可の解除は本人の手です。その媒体の設定で THTH との接続を外してください'
    lines=['remote_unconfirmed: '+line]
    if 'principal_token' in (result.get('preserved') or []):lines.append(_principal_token_line(principal,media))
    return lines


def _shared_guide(account):
    """`unconfirmed_shared` の完了の案内。記事の口座（5.18.0 までに、同じ主体の口座がほかにあって「共有の接続」で
    終わった退出）は、鍵の持ち主が主体なので、媒体ごとの 1 行と主体の鍵の行に言い直す（記録は変えない・監査 5.18.1 の 5）。"""
    entry=_removed_entry(account);media=entry.get('medium')
    if media in article_media.ARTICLE_MEDIA:
        principal=((entry.get('diff') or {}).get('principal') or [None,None])[-1]
        return ['remote_unconfirmed: '+MANUAL_REVOKE[media],_principal_token_line(principal,media)]
    return ['remote_unconfirmed: 他 account と共有の接続です。解除すると他 account に影響するため、運用者が解除対象を切り分けてください']


def _principal_token_line(principal,media):
    """記事の口座の完了の案内の 2 行め: 主体の鍵が残ること・消し方（無ければ、そう言う）。鍵の値は読まない。"""
    from . import principals
    head='principal_token_kept: '
    if not isinstance(principal,str) or not accounts.name_is_safe(principal):
        return (head+'主体の鍵は残しました（鍵の持ち主は主体で、口座の leave では消しません）。どの主体かは '
                'thth principal show <主体> で確かめ、もう使わないなら thth principal auth <主体> --by <名前> --forget で消せます')
    try:
        cfg=principals.load_principal(principal)
    except (principals.PrincipalError,accounts.AccountError,OSError,ValueError):
        return (head+f'主体 {principal} の台帳が読めず、鍵が残っているかを確かめられませんでした（口座の leave は主体の鍵を'
                f'消しません）。thth principal show {principal} で台帳と鍵の有無を確かめてください')
    if not os.path.exists(cfg['token']):
        return head+f'主体 {principal} の鍵のファイルはもうありません（消す物はありません）。遠隔の取り消しは上の 1 行のとおりです'
    others=[name for name,_ in principals.accounts_of(principal)]
    text=head+f'主体 {principal} の鍵は残しました（鍵の持ち主は主体で、口座の leave では消しません）。'
    if others:
        text+=f'この主体はまだ {"・".join(others)} が使っています。消すとそちらも出せなくなるので、使い終わってから '
    else:
        text+='この主体を使う口座はもうありません。消すなら '
    text+=f'thth principal auth {principal} --by <名前> --forget'
    # consumer を持つのは はてなの主体だけ（`--forget-consumer` もはてなの主体だけ）。
    if cfg.get('kind')=='hatena' and cfg.get('consumer'):text+='（consumer も消すなら --forget-consumer）'
    return text+' で消せます'


def _incomplete_detail(exc):
    """`leave_incomplete` に添える理由の 1 行（3.14.3 穴 7）。3.14.2 までは理由を丸めて出さなかった。"""
    text=' '.join(str(exc).split())[:200] or type(exc).__name__
    where=getattr(exc,'path',None)
    if isinstance(exc,PrincipalCredentialsInside):
        who=f'主体 {exc.principal}' if exc.principal else '主体'
        place={'state':'この口座の state の置き場の中','managed_repo':'この口座の管理する repo の中',
               'env':'この口座の env と同じファイル'}.get(exc.where,'この口座の leave で消す所')
        name=exc.principal or '<主体>'
        return (f'owned_tree_contains_principal_credentials {who} の鍵（はてなは consumer も）が{place}（{exc.where}）にあり、'
                'leave が口座の物と一緒に消してしまうので、何も消さずに止めました（口座は止めたままです）。'
                f'通すには、鍵のファイルを ~/.config/thth/{name}.token（consumer は ~/.config/thth/{name}.consumer）へ移し、'
                f'主体の台帳 principals/{name}.json の token（consumer）をそのパスに直して、thth principal show {name} で'
                '「ある」を確かめてから、同じ leave をもう一度打ってください（続きから進みます）。'
                'もっと良くするには、主体の鍵は thth principal add が書く既定の置き場に置くと、口座の出入りで鍵が巻き込まれません')
    if str(exc)=='unsafe_owned_directory' and isinstance(where,str):
        return f'unsafe_owned_directory {where}（group/other が書けます。chmod -R go-w {where} で直してから）'
    return text
