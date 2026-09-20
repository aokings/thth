"""Mastodon 4.3+ metadata, confidential client and S256 authorization."""
from .. import leave_gate

import base64
import hashlib
import http.client
import json
import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
from .. import authclients, httpsafe, jst, handoff_cursor
from ..authflow import AuthProfile, FlowError, secret

SCOPES = ['read:accounts','read:statuses','read:search','read:notifications','write:statuses']
CALLBACK = 'https://thth.me/callback/'
ENDPOINTS = {'authorization_endpoint':'/oauth/authorize','token_endpoint':'/oauth/token',
             'app_registration_endpoint':'/api/v1/apps'}


def origin(value):
    if not isinstance(value,str) or any(ord(c)<32 or ord(c)==127 for c in value):
        raise FlowError('mastodon_instance_invalid')
    try:
        p=urllib.parse.urlsplit(value)
        if (not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/')
                or not (p.scheme=='https' or p.scheme=='http' and p.hostname in ('127.0.0.1','localhost','::1'))):
            raise ValueError
        host=p.hostname.encode('idna').decode().lower()
        if ':' in host:
            ipaddress.IPv6Address(host)
            host='['+host+']'
        elif not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in host.rstrip('.').split('.')):
            raise ValueError
        port=p.port
        suffix='' if port is None or port==(443 if p.scheme=='https' else 80) else ':'+str(port)
        return p.scheme+'://'+host+suffix
    except (ValueError,UnicodeError):
        raise FlowError('mastodon_instance_invalid') from None


def request(base, path, *, data=None, token=None):
    headers={'Accept':'application/json'}
    if token:headers['Authorization']='Bearer '+secret(token)
    raw=None
    if data is not None:
        headers['Content-Type']='application/x-www-form-urlencoded'
        raw=urllib.parse.urlencode(data).encode()
    req=urllib.request.Request(base+path,data=raw,headers=headers,method='POST' if data is not None else 'GET')
    try:
        with httpsafe.urlopen(req,timeout=10) as response:
            raw=response.read(262145)
        if len(raw)>262144:raise ValueError
        value=json.loads(raw,object_pairs_hook=handoff_cursor._pairs)
        if not isinstance(value,dict):raise ValueError
        return value
    except urllib.error.HTTPError as exc:
        status=exc.code;exc.close()
        raise FlowError('mastodon_auth_http_'+str(status)) from None
    except (OSError,ValueError,http.client.HTTPException):
        raise FlowError('mastodon_auth_response_unavailable') from None


def metadata(value, base):
    if not isinstance(value,dict) or origin(value.get('issuer'))!=base:
        raise FlowError('mastodon_metadata_origin_mismatch')
    for key,path in ENDPOINTS.items():
        if value.get(key)!=base+path:raise FlowError('mastodon_metadata_endpoint_mismatch')
    for key,required in [('code_challenge_methods_supported',['S256']),('grant_types_supported',['authorization_code']),
                         ('response_types_supported',['code']),('scopes_supported',SCOPES),
                         ('token_endpoint_auth_methods_supported',['client_secret_post'])]:
        values=value.get(key)
        if not isinstance(values,list) or not all(isinstance(x,str) for x in values) or not set(required)<=set(values):
            raise FlowError('mastodon_pkce_or_scopes_unsupported')
    return {key:value[key] for key in ['issuer',*ENDPOINTS,'code_challenge_methods_supported',
             'grant_types_supported','response_types_supported','scopes_supported','token_endpoint_auth_methods_supported']}


def valid_client(data, base):
    if (not isinstance(data,dict) or set(data)!={'client_id','client_secret','instance','redirect_uri','scopes','metadata','created_at'}
            or data['instance']!=base or data['redirect_uri']!=CALLBACK or data['scopes']!=SCOPES):
        raise FlowError('mastodon_client_binding_invalid')
    for key in ('client_id','client_secret'):
        value=data.get(key)
        if not isinstance(value,str) or not value or len(value)>8192 or any(ord(c)<32 or ord(c)==127 for c in value):
            raise FlowError('mastodon_client_unavailable')
        secret(value)
    metadata(data['metadata'],base)
    return data


class MastodonAuthProfile(AuthProfile):
    media='mastodon'
    pkce=True

    @classmethod
    @leave_gate.configured("cfg")
    def prepare(cls,cfg,*,redirect_uri=None,rehearse=False,resume=False,by=None):
        from .. import admin_log, appconfig
        if not rehearse and not resume:admin_log.actor(by)
        base=origin(cfg.get('instance'))
        if redirect_uri not in (None,CALLBACK) or cfg.get('redirect_uri') not in (None,'',CALLBACK):
            raise FlowError('mastodon_callback_must_match_registered_uri')
        path=authclients.path_for('mastodon',base,cfg)
        # Rehearsal performs no metadata HTTP or auto-registration.
        if rehearse or resume:
            stored=authclients.read(path)
            if stored is None:raise FlowError('mastodon_client_not_registered: 通常のauthで先に登録してください')
            client=valid_client(stored,base)
        else:
            observed=metadata(request(base,'/.well-known/oauth-authorization-server'),base)
            with authclients.registration_lock(path):
                client=authclients.read(path)
                if client is None:
                    from pathlib import Path
                    from .. import accounts
                    Path(accounts.thth_root()).mkdir(parents=True, exist_ok=True)
                    # Reject known-bad audit destinations before remote registration.
                    # Release the global flock before the potentially slow HTTP call.
                    with admin_log.transaction():
                        expected=appconfig.snapshot(path)
                    body=request(base,'/api/v1/apps',data={'client_name':'THTH','redirect_uris':CALLBACK,
                                                         'scopes':' '.join(SCOPES),'website':'https://thth.me'})
                    for key in ('client_id','client_secret'):secret(body.get(key))
                    if (body.get('redirect_uris')!=[CALLBACK] or type(body.get('client_secret_expires_at',0)) is not int
                            or body.get('client_secret_expires_at',0)!=0):
                        raise FlowError('mastodon_registration_binding_invalid')
                    client=valid_client(dict(client_id=body.get('client_id'),client_secret=body.get('client_secret'),
                                             instance=base,redirect_uri=CALLBACK,scopes=list(SCOPES),metadata=observed,created_at=jst.iso()),base)
                    appconfig.save('mastodon',path,client,by=by,expected=expected,origin=base)
                else:valid_client(client,base)
        profile=cls(client['client_id'],client['client_secret'],CALLBACK,list(SCOPES))
        profile.instance=base;profile.client_path=path
        return profile

    def validate(self):
        origin(self.instance)
        valid_client(authclients.read(self.client_path),self.instance)

    def current_client(self):
        value=valid_client(authclients.read(self.client_path),self.instance)
        return value['client_id'],value['client_secret']

    def authorize(self,session):
        challenge=base64.urlsafe_b64encode(hashlib.sha256(session['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        return self.instance+'/oauth/authorize?'+urllib.parse.urlencode(dict(response_type='code',client_id=self.client_id,
            redirect_uri=CALLBACK,scope=' '.join(SCOPES),state=session['state'],code_challenge=challenge,
            code_challenge_method='S256',force_login='true'))

    @leave_gate.configured("account_cfg")
    def exchange(self,code_value,session,account_cfg,*,log):
        from .. import oauth
        self.validate()
        body=request(self.instance,'/oauth/token',data=dict(grant_type='authorization_code',code=secret(code_value),
            client_id=self.client_id,client_secret=self.client_secret,redirect_uri=CALLBACK,code_verifier=session['code_verifier']))
        token=body.get('access_token');secret(token);secret(body.get('refresh_token'))
        if 'expires_in' in body or body.get('refresh_token'):
            raise FlowError('mastodon_nonstandard_token_lifetime_unsupported')
        scope=body.get('scope')
        if (not isinstance(token,str) or not token or len(token)>16384 or any(ord(c)<32 or ord(c)==127 for c in token) or not isinstance(scope,str)
                or not set(SCOPES)<=set(scope.split()) or str(body.get('token_type','')).lower()!='bearer'):
            raise FlowError('mastodon_granted_scopes_or_token_invalid')
        me=request(self.instance,'/api/v1/accounts/verify_credentials',token=token)
        uid,acct=me.get('id'),me.get('acct')
        if not isinstance(uid,str) or not uid or not isinstance(acct,str) or not acct or oauth.handle_matches(account_cfg.get('handle'),acct) is not True:
            raise FlowError('mastodon_identity_mismatch')
        return dict(access_token=token,user_id=uid,username=acct,obtained_at=jst.iso(),no_expiry=True,
                    scopes=scope.split(),scopes_source='response')
