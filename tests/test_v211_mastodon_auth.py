"""Mastodon registration/PKCE with local HTTP and generated fake credentials."""
import base64
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import stat
import threading
import urllib.parse

import pytest
from thth import accounts,admin_log,authclients,authflow,oauth
from thth.adapters import auth_mastodon as masto
from tests.test_v211_authflow import snapshot,opaque


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();home=tmp_path/'home';home.mkdir();ledgers=tmp_path/'accounts';ledgers.mkdir()
    for key,value in [('THTH_ROOT',root),('HOME',home),('XDG_CONFIG_HOME',home),('THTH_ACCOUNTS_DIR',ledgers)]:monkeypatch.setenv(key,str(value))
    monkeypatch.delenv('THTH_APPS_DIR',raising=False)
    cfg=json.loads((Path(__file__).parents[1]/'accounts.example/mastodon.json').read_text())
    cfg.update(account='alpha',project='demo',handle='demo',repo_dir=str(root/'repos/_none'),
               token=str(root/'alpha.token'),env=str(root/'alpha.env'))
    ledger=ledgers/'alpha.json'
    data=dict(root=root,home=home,cfg=cfg,ledger=ledger,client_id=opaque(),client_secret=opaque(),code=opaque(),token=opaque(),calls=[],behavior={},auth_queries=[])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):self.answer(None)
        def do_POST(self):self.answer(urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode(),keep_blank_values=True))
        def answer(self,form):
            data['calls'].append((self.command,self.path,form,self.headers.get('Authorization')))
            base=data['base'];behavior=data['behavior'];status=200
            if self.path=='/.well-known/oauth-authorization-server':
                value={'issuer':base+'/',**{k:base+p for k,p in masto.ENDPOINTS.items()},
                       'code_challenge_methods_supported':['S256'],'grant_types_supported':['authorization_code'],
                       'response_types_supported':['code'],'scopes_supported':list(masto.SCOPES),'token_endpoint_auth_methods_supported':['client_secret_post']}
                if 'metadata' in behavior:
                    change=behavior['metadata']
                    value=change(value) if callable(change) else change
                status=behavior.get('metadata_status',200)
            elif self.path=='/api/v1/apps':
                value={'client_id':data['client_id'],'client_secret':data['client_secret'],'redirect_uris':[masto.CALLBACK],'client_secret_expires_at':0}
                value.update(behavior.get('app',{}));status=behavior.get('app_status',200)
            elif self.path=='/oauth/token':
                value={'access_token':data['token'],'token_type':'Bearer','scope':' '.join(masto.SCOPES)}
                value.update(behavior.get('token',{}));status=behavior.get('token_status',200)
            elif self.path=='/api/v1/accounts/verify_credentials':
                value={'id':'123','acct':'demo','username':'demo'};value.update(behavior.get('me',{}));status=behavior.get('me_status',200)
            else:status=404;value={}
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(value).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);data['base']=f'http://127.0.0.1:{server.server_port}'
    cfg['instance']=data['base'];ledger.write_text(json.dumps(cfg));data['cfg']=accounts.load_account('alpha')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield data
    finally:server.shutdown();server.server_close();thread.join()


def run(env,**overrides):
    def human(value):env['auth_queries'].append(urllib.parse.parse_qs(urllib.parse.urlsplit(value).query))
    def entered():
        session=authflow._read_session('alpha')
        env['session']=session
        return masto.CALLBACK+'?'+urllib.parse.urlencode({'state':session['state'],'code':env['code']})
    lines=[]
    result=oauth.run_auth('alpha',by='operator',input_func=entered,human_output=human,log=lines.append,**overrides)
    env['lines']=lines
    return result


def client_path(env):return authclients.path_for('mastodon',env['base'],env['cfg'])


def test_registration_pkce_identity_response_scopes_and_private_cache(env,capsys):
    assert run(env)==0
    assert [p for _,p,_,_ in env['calls']]==['/.well-known/oauth-authorization-server','/api/v1/apps','/oauth/token','/api/v1/accounts/verify_credentials']
    app=env['calls'][1][2]
    assert app['scopes']==[' '.join(masto.SCOPES)] and app['redirect_uris']==[masto.CALLBACK]
    query=env['auth_queries'][0];session=env['session']
    assert query['code_challenge_method']==['S256'] and query['force_login']==['true']
    assert query['scope']==[' '.join(masto.SCOPES)]
    challenge=base64.urlsafe_b64encode(hashlib.sha256(session['code_verifier'].encode()).digest()).rstrip(b'=').decode()
    assert query['code_challenge']==[challenge] and 'code_verifier' not in query
    token_form=env['calls'][2][2]
    assert token_form['code_verifier']==[session['code_verifier']]
    assert token_form['client_secret']==[env['client_secret']] and token_form['redirect_uri']==[masto.CALLBACK]
    assert env['calls'][3][3]=='Bearer '+env['token']
    path=client_path(env);assert path.is_file() and stat.S_IMODE(path.stat().st_mode)==0o600
    assert env['root'] not in path.parents
    token=json.loads(Path(env['cfg']['token']).read_text())
    assert token['no_expiry'] is True and 'refresh_token' not in token and 'expires_in' not in token
    assert token['scopes']==masto.SCOPES and token['scopes_source']=='response'
    assert token['username']=='demo' and token['user_id']=='123' and token['auth_via']=='paste'
    rows,broken=admin_log.read();assert broken==0 and rows[-1]['diff']['auth_via']==[None,'paste']
    output='\n'.join(env['lines'])+json.dumps(rows)+capsys.readouterr().out
    for secret in [env['client_secret'],env['code'],env['token'],session['state'],session['read_key'],session['code_verifier']]:assert secret not in output
    assert run(env)==0
    assert sum(p=='/api/v1/apps' for _,p,_,_ in env['calls'])==1


@pytest.mark.parametrize('failure',['404','array','missing_s256','plain','missing_grant','missing_scope','issuer','authorize','token','registration','missing_auth_method'])
def test_metadata_unknown_or_wrong_endpoint_never_registers(env,failure):
    if failure=='404':env['behavior']['metadata_status']=404
    elif failure=='array':env['behavior']['metadata']=[]
    else:
        def change(v):
            key={'missing_s256':'code_challenge_methods_supported','plain':'code_challenge_methods_supported','missing_grant':'grant_types_supported',
                 'missing_scope':'scopes_supported','missing_auth_method':'token_endpoint_auth_methods_supported'}.get(failure)
            if key:v[key]=['plain'] if failure=='plain' else []
            else:v[{'issuer':'issuer','authorize':'authorization_endpoint','token':'token_endpoint','registration':'app_registration_endpoint'}[failure]]='https://outside.example/other'
            return v
        env['behavior']['metadata']=change
    old=opaque();Path(env['cfg']['token']).write_text(old)
    assert run(env)==2
    assert Path(env['cfg']['token']).read_text()==old
    assert [p for _,p,_,_ in env['calls']]==['/.well-known/oauth-authorization-server']
    assert not client_path(env).exists() and not env['auth_queries']


@pytest.mark.parametrize('values',[{'client_id':''},{'client_secret':None},{'client_secret':[]},{'redirect_uris':['https://outside.example/']},{'client_secret_expires_at':1},{'client_secret_expires_at':False}])
def test_invalid_registration_response_never_stores_or_authorizes(env,values):
    env['behavior']['app']=values
    assert run(env)==2
    assert not client_path(env).exists() and not env['auth_queries']
    assert not Path(env['cfg']['token']).exists()


@pytest.mark.parametrize('values',[{'access_token':''},{'scope':None},{'scope':'read write'},{'scope':'read:accounts'},{'token_type':'wrong'}])
def test_token_scope_missing_refuses_before_identity_and_old_token_preserved(env,values):
    old=opaque();Path(env['cfg']['token']).write_text(old);env['behavior']['token']=values
    assert run(env)==2
    assert Path(env['cfg']['token']).read_text()==old
    assert not any(p=='/api/v1/accounts/verify_credentials' for _,p,_,_ in env['calls'])
    assert admin_log.read()[0]==[]


@pytest.mark.parametrize('values',[{'id':''},{'id':{}},{'acct':None},{'acct':{}},{'acct':'someone_else'}])
def test_identity_must_have_id_and_matching_acct(env,values):
    old=opaque();Path(env['cfg']['token']).write_text(old);env['behavior']['me']=values
    assert run(env)==2
    assert Path(env['cfg']['token']).read_text()==old and admin_log.read()[0]==[]


@pytest.mark.parametrize('status',[400,401,403,500])
def test_token_exchange_http_failures_do_not_save_or_echo(env,status):
    env['behavior']['token_status']=status
    env['behavior']['token']={'error':env['client_secret']+env['code']}
    assert run(env)==2
    assert not Path(env['cfg']['token']).exists()
    assert env['client_secret'] not in '\n'.join(env['lines']) and env['code'] not in '\n'.join(env['lines'])


def test_origin_normalization_and_store_instance_separation(env):
    assert masto.origin('https://EXAMPLE.org:443/')=='https://example.org'
    assert masto.origin('http://LOCALHOST:80/')=='http://localhost'
    a=authclients.path_for('mastodon',masto.origin('https://EXAMPLE.org:443/'),env['cfg'])
    b=authclients.path_for('mastodon',masto.origin('https://example.org'),env['cfg'])
    c=authclients.path_for('mastodon',masto.origin('https://example.org:444'),env['cfg'])
    assert a==b and a!=c


@pytest.mark.parametrize('instance',['http://outside.example','https://user:pass@example.org','https://example.org/path','https://example.org?query','https://example.org#fragment','https://exam\nple.org'])
def test_unsafe_instance_before_http(env,instance):
    cfg=json.loads(env['ledger'].read_text());cfg['instance']=instance;env['ledger'].write_text(json.dumps(cfg))
    assert run(env)==2 and env['calls']==[]


def test_callback_override_refused_and_project_store_refused(env,monkeypatch):
    assert run(env,redirect_uri='https://other.example/callback')==2 and env['calls']==[]
    monkeypatch.setenv('THTH_APPS_DIR',str(env['root']/'apps'))
    assert run(env)==2 and env['calls']==[]
    assert not (env['root']/'apps').exists()


def test_resume_keeps_verifier_and_rehearse_sends_only_relay_get(env,monkeypatch):
    profile=masto.MastodonAuthProfile.prepare(env['cfg'])
    session=authflow.begin('alpha',env['cfg'],profile)
    before_calls=len(env['calls'])
    code=masto.CALLBACK+'?'+urllib.parse.urlencode({'state':session['state'],'code':env['code']})
    assert oauth.run_auth('alpha',by='operator',code=code,human_output=lambda _:pytest.fail('new URL'),log=lambda _:None)==0
    assert [p for _,p,_,_ in env['calls'][before_calls:]]==['/oauth/token','/api/v1/accounts/verify_credentials']
    assert env['calls'][-2][2]['code_verifier']==[session['code_verifier']]
    before=snapshot(env['root'].parent);before_calls=len(env['calls']);clock=[0];relay_calls=[];human=[]
    monkeypatch.setattr(authflow.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(authflow.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    monkeypatch.setattr(authflow,'relay_request',lambda s,**kw:(relay_calls.append((s,kw)) or (404,{})))
    assert oauth.run_auth('alpha',by='operator',rehearse=True,human_output=human.append,log=lambda _:None)==2
    assert snapshot(env['root'].parent)==before and len(env['calls'])==before_calls
    assert len(relay_calls)==300 and not any(kw.get('register') for _,kw in relay_calls)
    assert len(human)==1 and 'code_challenge_method=S256' in human[0]
    client_path(env).unlink();before=snapshot(env['root'].parent);relay_calls.clear();human.clear()
    assert oauth.run_auth('alpha',by='operator',rehearse=True,human_output=human.append,log=lambda _:None)==2
    assert not relay_calls and not human and snapshot(env['root'].parent)==before

@pytest.mark.parametrize('change',['secret','origin','callback'])
def test_client_change_during_wait_refuses_old_flow_save(env,change):
    def entered():
        session=authflow._read_session('alpha');path=client_path(env);client=authclients.read(path)
        client[{'secret':'client_secret','origin':'instance','callback':'redirect_uri'}[change]]=opaque() if change=='secret' else 'https://outside.example'
        authclients.write(path,client)
        return masto.CALLBACK+'?'+urllib.parse.urlencode({'state':session['state'],'code':env['code']})
    assert oauth.run_auth('alpha',by='operator',input_func=entered,human_output=lambda _:None,log=lambda _:None)==2
    assert not Path(env['cfg']['token']).exists() and admin_log.read()[0]==[]


@pytest.mark.parametrize('kind',['symlink','fifo','mode'])
def test_private_cache_special_or_public_mode_never_overwritten(env,kind):
    import os
    path=client_path(env);path.parent.mkdir(parents=True);outside=env['home']/'outside';outside.write_text(opaque());before=outside.read_bytes()
    if kind=='symlink':path.symlink_to(outside)
    elif kind=='fifo':os.mkfifo(path)
    else:path.write_text('invalid');path.chmod(0o644)
    assert run(env)==2 and outside.read_bytes()==before
    assert not any(p=='/api/v1/apps' for _,p,_,_ in env['calls'])
    assert not Path(env['cfg']['token']).exists()


def test_registration_write_failure_keeps_old_token_and_no_client(env,monkeypatch):
    old=opaque();Path(env['cfg']['token']).write_text(old)
    monkeypatch.setattr(authclients.os,'fsync',lambda *a:(_ for _ in ()).throw(OSError(env['client_secret'])))
    assert run(env)==2
    assert Path(env['cfg']['token']).read_text()==old and not client_path(env).exists()
    assert not list(client_path(env).parent.glob('.client-*'))
    assert env['client_secret'] not in '\n'.join(env['lines'])


def test_parallel_registration_uses_one_per_instance_client(env):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        profiles=list(pool.map(lambda _:masto.MastodonAuthProfile.prepare(env['cfg']),range(4)))
    assert sum(path=='/api/v1/apps' for _,path,_,_ in env['calls'])==1
    assert all(p.client_id==env['client_id'] for p in profiles)


@pytest.mark.parametrize('unexpected',[{'expires_in':3600},{'expires_in':None},{'refresh_token':'generated'}])
def test_nonstandard_lifetime_is_not_misreported_as_no_expiry(env,unexpected):
    if 'refresh_token' in unexpected:unexpected={'refresh_token':opaque()}
    env['behavior']['token']=unexpected
    assert run(env)==2 and not Path(env['cfg']['token']).exists()

@pytest.mark.parametrize('error',['network','incomplete'])
def test_metadata_transport_errors_are_bounded(env,monkeypatch,error):
    import http.client
    from thth import httpsafe
    def broken(*args,**kwargs):
        if error=='network':raise OSError(env['client_secret'])
        raise http.client.IncompleteRead(env['client_secret'].encode())
    monkeypatch.setattr(httpsafe,'urlopen',broken)
    assert run(env)==2 and not client_path(env).exists()
    assert env['client_secret'] not in '\n'.join(env['lines'])
