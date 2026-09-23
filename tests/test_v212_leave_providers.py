"""Real loopback HTTP revoke forms; credentials exist only in temporary fixtures."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import socket
import threading
import urllib.parse
import pytest
from thth import accounts,authclients,leave,leave_gate,admin_log,approval_relay
from thth.adapters import auth_mastodon as masto,auth_x
from tests.test_v212_server_writes import env


@pytest.fixture
def provider(env,monkeypatch):
    data={'calls':[],'fail_access':False,'status':200,'client_id':secrets.token_urlsafe(24),'client_secret':secrets.token_urlsafe(32)}
    def connect(sock,address):
        assert address[0]=='127.0.0.1'
        return env['connect'](sock,address)
    monkeypatch.setattr(socket.socket,'connect',connect)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            form=urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
            data['calls'].append((self.path,form,self.headers.get('Authorization')))
            status=503 if data['fail_access'] and form.get('token')==[data.get('access')] else data['status']
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    data['base']='http://127.0.0.1:'+str(server.server_port)
    monkeypatch.setattr(approval_relay,'signed_request',lambda *a:{'status':'revoked'})
    try:yield data
    finally:server.shutdown();server.server_close();thread.join()


def configure(env,provider,monkeypatch,media):
    path=env['root']/'accounts/alpha.json';cfg=json.loads(path.read_text());cfg['media']=media
    if media=='mastodon':cfg['instance']=provider['base']
    path.write_text(json.dumps(cfg));cfg=accounts.load_account('alpha')
    pair={k:provider[k] for k in ('client_id','client_secret')}
    if media=='mastodon':
        metadata={'issuer':provider['base'],**{k:provider['base']+p for k,p in masto.ENDPOINTS.items()},'code_challenge_methods_supported':['S256'],'grant_types_supported':['authorization_code'],'response_types_supported':['code'],'scopes_supported':masto.LEGACY_SCOPES,'token_endpoint_auth_methods_supported':['client_secret_post']}
        client=dict(pair,instance=provider['base'],redirect_uri=masto.CALLBACK,scopes=masto.LEGACY_SCOPES,metadata=metadata,created_at='2026-09-20T00:00:00Z')
        p=authclients.path_for(media,provider['base'],cfg)
    else:
        monkeypatch.setenv('THTH_X_BASE_URL',provider['base'])
        client=dict(pair,client_type='confidential',redirect_uri=auth_x.CALLBACK);p=authclients.path_for(media,None,cfg)
    authclients.write(p,client);provider['client_path']=p;provider['client_bytes']=p.read_bytes()
    tokenpath=env['root']/'secrets/alpha.json';token=json.loads(tokenpath.read_text());token.update(refresh_token=secrets.token_urlsafe(32))
    tokenpath.write_text(json.dumps(token));provider.update(access=token['access_token'],refresh=token['refresh_token'])
    return cfg,tokenpath


@pytest.mark.parametrize('media',['mastodon','x'])
def test_actual_revoke_failure_preserves_then_retry_completes_without_touching_shared_client(env,provider,monkeypatch,media):
    cfg,tokenpath=configure(env,provider,monkeypatch,media);old=tokenpath.read_bytes();other=(env['root']/'secrets/beta.json').read_bytes()
    provider['status']=503
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert tokenpath.read_bytes()==old and leave_gate.stopped('alpha')
    assert leave.read('alpha')['phase']=='remote_pending' and admin_log.read(event='account_removed')[0]==[]
    provider['status']=200;result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='confirmed' and not tokenpath.exists()
    assert provider['client_path'].read_bytes()==provider['client_bytes'] and (env['root']/'secrets/beta.json').read_bytes()==other
    for path,form,auth in provider['calls']:
        if media=='mastodon':
            assert path=='/oauth/revoke' and form=={'client_id':[provider['client_id']],'client_secret':[provider['client_secret']],'token':[provider['access']]}
            assert auth is None
        else:
            assert path=='/2/oauth2/revoke' and set(form)=={'token'}
            assert auth=='Basic '+base64.b64encode((provider['client_id']+':'+provider['client_secret']).encode()).decode()
    output=json.dumps(result)+json.dumps(admin_log.read()[0])
    assert all(value not in output for value in [provider['access'],provider['refresh'],provider['client_secret'],provider['client_id']])


def test_x_partial_refresh_revocation_retries_only_unconfirmed_access(env,provider,monkeypatch):
    _,tokenpath=configure(env,provider,monkeypatch,'x');old=tokenpath.read_bytes();provider['fail_access']=True
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert leave.read('alpha')['revoked']==['refresh'] and tokenpath.read_bytes()==old
    provider['fail_access']=False;leave.run('alpha',by='operator')
    assert [form['token'][0] for _,form,_ in provider['calls']]==[provider['refresh'],provider['access'],provider['access']]


def test_shared_mastodon_grant_refuses_remote_revoke_but_shared_client_alone_does_not(env,provider,monkeypatch):
    cfg,tokenpath=configure(env,provider,monkeypatch,'mastodon');old=tokenpath.read_bytes()
    other=env['root']/'accounts/beta.json';b=json.loads(other.read_text());b.update(media='mastodon',instance=provider['base']);other.write_text(json.dumps(b))
    (env['root']/'secrets/beta.json').write_bytes(old)
    # 3.1.2 件 6: 共有の接続は遠隔で失効させず（calls==[]）、止まらずに退出を終える。
    # 裁定 09-23: 値だけの共有（別ファイル）なら自分の token file は消す。相手の file は残る。
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='unconfirmed_shared' and 'token_shared' not in result['preserved']
    assert provider['calls']==[] and not tokenpath.exists() and (env['root']/'secrets/beta.json').read_bytes()==old


def test_b_credential_commit_before_revoke_is_rechecked_after_inventory(env,provider,monkeypatch):
    from thth import authflow
    cfg,tokenpath=configure(env,provider,monkeypatch,'mastodon')
    path=env['root']/'accounts/beta.json';b=json.loads(path.read_text());b.update(media='mastodon',instance=provider['base']);path.write_text(json.dumps(b))
    cfg_b=accounts.load_account('beta');snapshot=authflow._token_snapshot(__import__('pathlib').Path(cfg_b['token']))
    token_a=json.loads(tokenpath.read_text())
    def worker(*args):
        # Worker revoke occurs after initial inventory but before provider revoke.
        authflow.commit_manual('beta',cfg_b,token_a,snapshot=snapshot,session=None,by='operator')
        return {'status':'revoked'}
    monkeypatch.setattr(approval_relay,'signed_request',worker)
    # 3.1.2 件 6: 共有の接続は遠隔で失効させず（calls==[]）、止まらずに退出を終える。
    # 裁定 09-23: 値だけの共有（別ファイル）なら自分の token file は消す。相手の file は残る。
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='unconfirmed_shared' and 'token_shared' not in result['preserved']
    assert provider['calls']==[] and not tokenpath.exists()
    assert json.loads((env['root']/'secrets/beta.json').read_text())==token_a


def test_b_commit_waits_for_provider_response_without_holding_global_admin_flock(env,provider,monkeypatch):
    from pathlib import Path
    from thth import authflow
    configure(env,provider,monkeypatch,'mastodon')
    cfg_b=accounts.load_account('beta');snapshot=authflow._token_snapshot(Path(cfg_b['token']))
    new=dict(json.loads(snapshot[0]),access_token=secrets.token_urlsafe(32))
    entered=threading.Event();release=threading.Event();saved=threading.Event();attempt=threading.Event();errors=[]
    real=masto.request
    def request(*a,**k):
        entered.set();assert release.wait(3);return real(*a,**k)
    monkeypatch.setattr(masto,'request',request)
    def leaving():
        try:leave.run('alpha',by='operator')
        except BaseException as exc:errors.append(type(exc).__name__)
    def saving():
        attempt.set()
        try:authflow.commit_manual('beta',cfg_b,new,snapshot=snapshot,session=None,by='operator');saved.set()
        except BaseException as exc:errors.append(type(exc).__name__)
    a=threading.Thread(target=leaving);a.start();assert entered.wait(2)
    b=threading.Thread(target=saving);b.start();assert attempt.wait(1)
    assert not saved.wait(.05)
    # Independent admin transaction remains available while the provider waits.
    with admin_log.transaction():pass
    release.set();a.join(4);b.join(4)
    assert not a.is_alive() and not b.is_alive() and errors==[] and saved.is_set()
    assert accounts.load_token(accounts.load_account('beta'))==new
    assert len(provider['calls'])==1


def test_b_exchange_to_commit_holds_registry_lease_and_late_shared_grant_vetoes_a(env,provider,monkeypatch):
    from thth import authflow
    cfg_a,tokenpath=configure(env,provider,monkeypatch,'mastodon')
    path=env['root']/'accounts/beta.json';cfg_b=json.loads(path.read_text());cfg_b.update(media='mastodon',instance=provider['base']);path.write_text(json.dumps(cfg_b));cfg_b=accounts.load_account('beta')
    value=dict(json.loads(tokenpath.read_text()),user_id='synthetic',username='demo')
    class Profile(authflow.AuthProfile):
        media='mastodon'
        def validate(self):pass
        def current_client(self):return self.client_id,self.client_secret
        def authorize(self,session):return 'https://example.invalid/authorize'
        def exchange(self,*args,**kwargs):return dict(value)
    profile=Profile(provider['client_id'],provider['client_secret'],masto.CALLBACK,[])
    at_commit=threading.Event();release=threading.Event();worker_done=threading.Event();results=[];leave_errors=[]
    original=authflow.commit
    def commit(account,*args,**kwargs):
        if account=='beta':at_commit.set();assert release.wait(3)
        return original(account,*args,**kwargs)
    monkeypatch.setattr(authflow,'commit',commit)
    def worker(*args):worker_done.set();return {'status':'revoked'}
    monkeypatch.setattr(approval_relay,'signed_request',worker)
    def input_url():
        # Browser/human wait does not hold the global credential lock.
        with leave_gate.credentials(exclusive=True):pass
        return masto.CALLBACK+'?'+urllib.parse.urlencode({'state':authflow._read_session('beta')['state'],'code':secrets.token_urlsafe(24)})
    b=threading.Thread(target=lambda:results.append(authflow.run('beta',cfg_b,profile,input_func=input_url,by='operator',log=lambda _:None,human_output=lambda _:None)))
    def leaving():
        try:leave.run('alpha',by='operator')
        except ValueError as exc:leave_errors.append(str(exc))
    b.start();assert at_commit.wait(2)
    a=threading.Thread(target=leaving);a.start();assert worker_done.wait(2)
    assert a.is_alive() and provider['calls']==[]
    release.set();a.join(4);b.join(4)
    assert not a.is_alive() and not b.is_alive() and results==[0]
    # 3.1.2 件 6: 共有の接続は遠隔で失効させず（calls==[]）、止まらずに退出を終える。
    assert leave_errors==[] and provider['calls']==[]
    assert leave.read('alpha')['phase']=='completed' and leave.read('alpha')['remote']=='unconfirmed_shared'
    assert accounts.load_token(accounts.load_account('beta'))['access_token']==value['access_token']
