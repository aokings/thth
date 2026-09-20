"""Bluesky app-password ingestion shared by stdin and legacy human auth."""
from pathlib import Path
import urllib.parse
from . import accounts, admin_log, authflow, jst, secrets_fs
from .adapters import bluesky
from .authflow import FlowError, secret


def service_origin(value):
    if not isinstance(value,str) or any(ord(c)<32 or ord(c)==127 for c in value):
        raise FlowError('bluesky_service_invalid')
    try:
        p=urllib.parse.urlsplit(value)
        if (not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/')
                or not (p.scheme=='https' or p.scheme=='http' and p.hostname in ('localhost','127.0.0.1','::1'))):
            raise ValueError
        p.port
    except ValueError:raise FlowError('bluesky_service_invalid') from None
    return value.rstrip('/')


def run(account,*,password_input,by,log=print,force=False,identifier_input=None,cfg=None):
    from . import oauth
    try:
        admin_log.actor(by)
    except ValueError as exc:
        log(str(exc));return 2
    changed=False;snapshot=None;path=None
    def rollback():
        if changed:
            try:
                if snapshot is None:path.unlink(missing_ok=True)
                else:secrets_fs.atomic_write_text(str(path),snapshot[0].decode(),mode=snapshot[1])
            except (OSError,ValueError):raise FlowError('bluesky_rollback_failed_outcome_uncertain') from None
    try:
        cfg=cfg or accounts.load_account(account)
        if cfg.get('media')!='bluesky':raise FlowError('bluesky_account_required')
        handle=cfg.get('handle')
        if not isinstance(handle,str) or not handle.strip():raise FlowError('bluesky_ledger_identifier_required')
        identifier=handle.strip().lstrip('@')
        if not identifier or any(ord(c)<32 or ord(c)==127 for c in identifier):
            raise FlowError('bluesky_ledger_identifier_required')
        service=service_origin(cfg.get('service') or bluesky.DEFAULT_SERVICE)
        path=Path(cfg['token'])
        if admin_log._active_fd.get() is not None:raise FlowError('auth_nested_transaction_refused')
        Path(accounts.thth_root()).mkdir(parents=True,exist_ok=True)
        with admin_log.transaction():
            if accounts.load_account(account)!=cfg:raise FlowError('auth_account_changed')
            snapshot=authflow._token_snapshot(path)
            session=authflow._read_session(account)
            if snapshot is not None and not force:
                log('既に token があります。入れ替えるなら --force を付けてください');return 1
        # Neither terminal/stdin waiting nor createSession holds the admin lock.
        if identifier_input is not None and oauth.handle_matches(identifier,identifier_input(),media='bluesky') is not True:
            log('保存しませんでした: 入力handleが台帳と一致しません');return 1
        raw=password_input()
        if not isinstance(raw,str):raise FlowError('bluesky_app_password_invalid')
        password=secret(raw.strip())
        if not password or not bluesky.APP_PASSWORD_RE.fullmatch(password):
            log('App Password の形（xxxx-xxxx-xxxx-xxxx）ではありません');return 1
        try:
            token=bluesky.create_session(service,identifier,password)
        except (OSError,ValueError,RuntimeError):
            # Provider/network exceptions may contain response bodies or headers.
            log('Bluesky createSession が失敗しました。App Password と接続先を確認してください');return 1
        if (not isinstance(token,dict) or not isinstance(token.get('accessJwt'),str) or not token['accessJwt']
                or not isinstance(token.get('did'),str) or not token['did']
                or not isinstance(token.get('handle'),str) or not token['handle']
                or any(ord(c)<32 or ord(c)==127 for c in token['did']+token['handle'])
                or oauth.handle_matches(identifier,token['handle'],media='bluesky') is not True
                or cfg.get('user_id') and token['did']!=cfg['user_id']):
            log('保存しませんでした: 本人の id/handle が欠けているか台帳と一致しません');return 1
        # Persist only the App Password; temporary JWTs from createSession stay RAM-only.
        result=dict(identifier=identifier,app_password=password,did=token['did'],handle=token['handle'],
                    user_id=token['did'],username=token['handle'],no_expiry=True,obtained_at=jst.iso(),
                    scopes=None,scopes_source='unknown',auth_via='token_set')
        with admin_log.transaction(rollback=rollback):
            if (accounts.load_account(account)!=cfg or authflow._read_session(account)!=session
                    or authflow._generation(authflow._token_snapshot(path))!=authflow._generation(snapshot)):
                raise FlowError('bluesky_credentials_changed: 別の更新のため保存しません')
            snapshot=authflow._token_snapshot(path);changed=True
            secrets_fs.atomic_write_json(str(path),result,mode=0o600)
            admin_log.append('token_set',account,cfg,by=by,diff={'token':['present' if snapshot else 'absent','present'],
                                                              'auth_via':[None,'token_set']})
        oauth._out(f"handle={result['handle']} did={result['did']}",log=log)
        oauth._out(f"保存しました: {path}（600）",log=log)
        return 0
    except admin_log.AdminLogError as exc:
        log('admin_change_recorded_durability_unconfirmed' if exc.complete else
            'admin_change_partially_recorded_outcome_uncertain' if exc.appended else 'admin_change_refused')
        return 2
    except oauth.OAuthError as exc:
        oauth._out(str(exc),log=log);return 2
    except (OSError,ValueError,accounts.AccountError,EOFError) as exc:
        log(str(exc) if isinstance(exc,FlowError) else 'bluesky_auth_failed: 保存を完了できませんでした')
        return 2
