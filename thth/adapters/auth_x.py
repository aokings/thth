"""X OAuth only. This profile does not register a posting/collection adapter."""
import base64
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from .. import accounts, admin_log, authclients, authflow, handoff_cursor, httpsafe, jst, secrets_fs
from ..authflow import AuthProfile, FlowError, secret

SCOPES = ['tweet.read', 'tweet.write', 'users.read', 'offline.access']
CALLBACK = 'https://thth.me/callback/'
API = 'https://api.x.com'
REFRESH_BEFORE_SECONDS = 300


def api_origin():
    value = os.environ.get('THTH_X_BASE_URL', API)
    try:
        p = urllib.parse.urlsplit(value)
        if (any(ord(c)<32 or ord(c)==127 for c in value) or p.username or p.password or p.query or p.fragment
                or p.path not in ('', '/') or not (value.rstrip('/') == API or
                  p.scheme == 'http' and p.hostname in ('127.0.0.1','localhost','::1'))):
            raise ValueError
        p.port
        return value.rstrip('/')
    except ValueError:
        raise FlowError('x_token_endpoint_invalid') from None


def credential(value):
    if not isinstance(value,str) or not value or len(value)>16384 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise FlowError('x_credential_missing_or_invalid')
    return secret(value)


def client(data):
    if (not isinstance(data,dict) or set(data)!={'client_id','client_secret','client_type','redirect_uri'}
            or data.get('client_type')!='confidential' or data.get('redirect_uri')!=CALLBACK):
        raise FlowError('x_confidential_client_required')
    pair=credential(data['client_id']),credential(data['client_secret'])
    if ':' in pair[0]:raise FlowError('x_client_id_invalid')
    return pair


def request(path, *, data=None, pair=None, token=None):
    headers={'Accept':'application/json'}
    if pair:
        basic=secret(base64.b64encode((pair[0]+':'+pair[1]).encode()).decode())
        headers['Authorization']='Basic '+basic
    if token:headers['Authorization']='Bearer '+credential(token)
    raw=None
    if data is not None:
        headers['Content-Type']='application/x-www-form-urlencoded'
        raw=urllib.parse.urlencode(data).encode()
    req=urllib.request.Request(api_origin()+path,data=raw,headers=headers,method='POST' if data is not None else 'GET')
    try:
        with httpsafe.urlopen(req,timeout=10) as response:raw=response.read(262145)
        if len(raw)>262144:raise ValueError
        value=json.loads(raw,object_pairs_hook=handoff_cursor._pairs)
        if not isinstance(value,dict):raise ValueError
        return value
    except urllib.error.HTTPError as exc:
        status=exc.code;exc.close()
        raise FlowError('x_auth_http_'+str(status)+': 認可をやり直してください') from None
    except (OSError,ValueError,http.client.HTTPException):
        raise FlowError('x_auth_response_unavailable: 認可をやり直してください') from None


def token_result(body,cfg,*,now=None,previous=None):
    now=now or jst.now_jst()
    access=credential(body.get('access_token'));refresh=credential(body.get('refresh_token'))
    scope=body.get('scope');expiry=body.get('expires_in')
    if (not isinstance(scope,str) or not set(SCOPES)<=set(scope.split())
            or type(expiry) is not int or not 0<expiry<=31536000
            or str(body.get('token_type','')).lower()!='bearer'):
        raise FlowError('x_scope_or_expiry_unobserved')
    identity=request('/2/users/me',token=access).get('data')
    from .. import oauth
    if (not isinstance(identity,dict) or not isinstance(identity.get('id'),str) or not identity['id']
            or not isinstance(identity.get('username'),str) or not identity['username']
            or oauth.handle_matches(cfg.get('handle'),identity['username']) is not True
            or previous is not None and identity['id']!=previous.get('user_id')):
        raise FlowError('x_identity_mismatch')
    return dict(access_token=access,refresh_token=refresh,user_id=identity['id'],username=identity['username'],
                obtained_at=jst.iso(now),expires_in=expiry,expires_at=jst.iso(now+datetime.timedelta(seconds=expiry)),
                scopes=scope.split(),scopes_source='response')


class XAuthProfile(AuthProfile):
    media='x'
    pkce=True

    @classmethod
    def prepare(cls,cfg,*,redirect_uri=None,**_):
        api_origin()
        if redirect_uri not in (None,CALLBACK) or cfg.get('redirect_uri') not in (None,'',CALLBACK):
            raise FlowError('x_callback_must_match_registered_uri')
        path=authclients.path_for('x',None,cfg)
        pair=client(authclients.read(path))
        profile=cls(*pair,CALLBACK,list(SCOPES));profile.client_path=path
        return profile

    def validate(self):
        api_origin();self.current_client()

    def current_client(self):
        return client(authclients.read(self.client_path))

    def authorize(self,session):
        challenge=base64.urlsafe_b64encode(hashlib.sha256(session['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        return 'https://x.com/i/oauth2/authorize?'+urllib.parse.urlencode(dict(response_type='code',client_id=self.client_id,
            redirect_uri=CALLBACK,scope=' '.join(SCOPES),state=session['state'],code_challenge=challenge,code_challenge_method='S256'))

    def exchange(self,code_value,session,account_cfg,*,log):
        self.validate()
        received=getattr(code_value,'received_at',None)
        if received is not None and not 0<=(jst.now_jst()-received).total_seconds()<30:
            raise FlowError('x_code_expired: 認可をやり直してください')
        # A pasted code has no trustworthy issuance time. Exchange immediately;
        # provider invalid_grant is not retried or relabelled as a successful flow.
        body=request('/2/oauth2/token',pair=(self.client_id,self.client_secret),data=dict(
            grant_type='authorization_code',code=credential(code_value),redirect_uri=CALLBACK,code_verifier=credential(session.get('code_verifier'))))
        return token_result(body,account_cfg)


def remaining(token,now):
    obtained=jst.parse(token.get('obtained_at'))
    expiry=token.get('expires_in')
    if not obtained or type(expiry) is not int or not 0<expiry<=31536000 or obtained>now:
        raise FlowError('x_expiry_unobserved')
    return (obtained+datetime.timedelta(seconds=expiry)-now).total_seconds()


def run_refresh(account,*,force=False,check=False,log=print,now=None):
    now=now or jst.now_jst();changed=False;snapshot=None;path=None
    def rollback():
        if changed:
            try:secrets_fs.atomic_write_text(str(path),snapshot[0].decode(),mode=snapshot[1])
            except (OSError,ValueError):raise FlowError('x_refresh_rollback_failed_outcome_uncertain') from None
    try:
        cfg=accounts.load_account(account);profile=XAuthProfile.prepare(cfg)
        path=Path(cfg['token'])
        if check:
            snapshot=authflow._token_snapshot(path)
            token=json.loads(snapshot[0]) if snapshot else {}
        else:
            if admin_log._active_fd.get() is not None:raise FlowError('auth_nested_transaction_refused')
            with admin_log.transaction():
                if accounts.load_account(account)!=cfg:raise FlowError('auth_account_changed')
                binding=profile.binding(cfg,current=True)
                if binding!=profile.binding(cfg):raise FlowError('auth_client_changed')
                snapshot=authflow._token_snapshot(path)
                token=json.loads(snapshot[0]) if snapshot else {}
                session=authflow._read_session(account)
        if not isinstance(token,dict):raise FlowError('x_token_unreadable')
        credential(token.get('access_token'));credential(token.get('refresh_token'))
        seconds=remaining(token,now)
        if check:
            log(json.dumps({'account':account,'remaining_seconds':seconds,'needs_refresh':seconds<=REFRESH_BEFORE_SECONDS,'can_refresh':True,'no_expiry':False}))
            return 0
        if not force and seconds>REFRESH_BEFORE_SECONDS:
            log('まだ更新の必要がありません（X: 期限5分前から更新）');return 0
        body=request('/2/oauth2/token',pair=(profile.client_id,profile.client_secret),data=dict(
            grant_type='refresh_token',refresh_token=token['refresh_token']))
        updated=token_result(body,cfg,now=now,previous=token)
        if token.get('auth_via') in ('paste','relay'):updated['auth_via']=token['auth_via']
        with admin_log.transaction(rollback=rollback):
            if (accounts.load_account(account)!=cfg or profile.binding(cfg,current=True)!=binding
                    or authflow._read_session(account)!=session
                    or authflow._generation(authflow._token_snapshot(path))!=authflow._generation(snapshot)):
                raise FlowError('x_refresh_credentials_changed: 保存しません')
            # Snapshot and equality check are inside the same final lock.
            snapshot=authflow._token_snapshot(path);changed=True
            secrets_fs.atomic_write_json(str(path),updated,mode=0o600)
            admin_log.append('token_refreshed',account,cfg,by='thth-refresh',diff={'token':['present','present']})
        log('更新しました: '+account);return 0
    except admin_log.AdminLogError as exc:
        log('admin_change_recorded_durability_unconfirmed' if exc.complete else
            'admin_change_partially_recorded_outcome_uncertain' if exc.appended else 'admin_change_refused')
        return 2
    except (OSError,ValueError,accounts.AccountError) as exc:
        log((str(exc) if isinstance(exc,FlowError) else 'x_refresh_failed') + ': 必要なら認可をやり直してください。旧ファイル保持は旧 refresh token の再利用を保証しません')
        return 2
