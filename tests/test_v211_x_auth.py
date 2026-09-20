"""X auth-only and refresh: loopback HTTP, generated credentials, no real API."""
import base64
import datetime
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import stat
import threading
import urllib.parse

import pytest
from thth import accounts,admin_log,authclients,authflow,cli,jst,maintain,oauth
from thth import adapters
from thth.adapters import auth_x as x
from tests.test_v211_authflow import snapshot,opaque,no_lock


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();home=tmp_path/'home';home.mkdir();ledgers=tmp_path/'accounts';ledgers.mkdir()
    for key,value in [('THTH_ROOT',root),('HOME',home),('XDG_CONFIG_HOME',home),('THTH_ACCOUNTS_DIR',ledgers)]:monkeypatch.setenv(key,str(value))
    monkeypatch.delenv('THTH_APPS_DIR',raising=False)
    cfg=json.loads((Path(__file__).parents[1]/'accounts.example/x.json').read_text())
    cfg.update(account='alpha',project='demo',handle='demo',repo_dir=str(root/'repos/_none'),token=str(root/'alpha.token'),env=str(root/'alpha.env'))
    ledger=ledgers/'alpha.json';ledger.write_text(json.dumps(cfg));cfg=accounts.load_account('alpha')
    data=dict(root=root,home=home,cfg=cfg,ledger=ledger,client_id=opaque(),client_secret=opaque(),code=opaque(),token=opaque(),refresh=opaque(),calls=[],behavior={},queries=[],lines=[],hook=None)
    path=authclients.path_for('x',None,cfg);data['client_path']=path
    data['client']={'client_id':data['client_id'],'client_secret':data['client_secret'],'client_type':'confidential','redirect_uri':x.CALLBACK}
    authclients.write(path,data['client'])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):self.answer(None)
        def do_POST(self):self.answer(urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode(),keep_blank_values=True))
        def answer(self,form):
            data['calls'].append((self.command,self.path,form,self.headers.get('Authorization')))
            if data['hook']:data['hook']()
            if self.path=='/2/oauth2/token':
                body=dict(access_token=data['token'],refresh_token=data['refresh'],scope=' '.join(x.SCOPES),expires_in=7200,token_type='Bearer')
                body.update(data['behavior'].get('token',{}));status=data['behavior'].get('status',200)
            elif self.path=='/2/users/me':
                body={'data':dict(id='123',username='demo',**{})};body['data'].update(data['behavior'].get('me',{}));status=data['behavior'].get('me_status',200)
            else:body={};status=404
            self.send_response(status);self.end_headers();self.wfile.write(json.dumps(body).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    monkeypatch.setenv('THTH_X_BASE_URL',f'http://127.0.0.1:{server.server_port}')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield data
    finally:server.shutdown();server.server_close();thread.join()


def entered(env):
    env['session']=authflow._read_session('alpha')
    return x.CALLBACK+'?'+urllib.parse.urlencode({'state':env['session']['state'],'code':env['code']})


def run(env,**kw):
    return oauth.run_auth('alpha',by='operator',input_func=lambda:entered(env),human_output=lambda value:env['queries'].append(urllib.parse.parse_qs(urllib.parse.urlsplit(value).query)),log=env['lines'].append,**kw)


def saved(env):return json.loads(Path(env['cfg']['token']).read_text())


def old_token(env,*,seconds=100,**updates):
    now=jst.now_jst();token=dict(access_token=opaque(),refresh_token=opaque(),user_id='123',username='demo',scopes=x.SCOPES,scopes_source='response',obtained_at=jst.iso(now-datetime.timedelta(seconds=7200-seconds)),expires_in=7200,auth_via='relay')
    token.update(updates);path=Path(env['cfg']['token']);path.write_text(json.dumps(token));path.chmod(0o600)
    return path.read_bytes(),now


def test_success_pkce_basic_observed_expiry_and_auth_only(env,capsys):
    env['hook']=lambda:no_lock(env)
    assert run(env)==0
    query=env['queries'][0];session=env['session'];token=saved(env)
    assert query['code_challenge_method']==['S256'] and query['scope']==[' '.join(x.SCOPES)]
    assert query['code_challenge']==[base64.urlsafe_b64encode(hashlib.sha256(session['code_verifier'].encode()).digest()).rstrip(b'=').decode()]
    method,path,form,header=env['calls'][0]
    assert (method,path)==('POST','/2/oauth2/token')
    assert set(form)=={'grant_type','code','redirect_uri','code_verifier'}
    assert form['code_verifier']==[session['code_verifier']] and form['code']==[env['code']]
    assert header=='Basic '+base64.b64encode((env['client_id']+':'+env['client_secret']).encode()).decode()
    assert env['calls'][1][3]=='Bearer '+env['token']
    assert token['refresh_token']==env['refresh'] and token['expires_in']==7200
    assert (jst.parse(token['expires_at'])-jst.parse(token['obtained_at'])).total_seconds()==7200
    assert token['scopes']==x.SCOPES and token['scopes_source']=='response' and token['auth_via']=='paste'
    assert stat.S_IMODE(Path(env['cfg']['token']).stat().st_mode)==0o600
    assert adapters.capabilities_for('x')==set()
    with pytest.raises(adapters.UnknownMedium):adapters.make_adapter(env['cfg'],token)
    output='\n'.join(env['lines'])+json.dumps(admin_log.read())+capsys.readouterr().out
    for value in [env['token'],env['refresh'],env['client_secret'],env['code'],session['state'],session['read_key'],session['code_verifier']]:assert value not in output


@pytest.mark.parametrize('seconds,ok',[(0,True),(29,True),(30,False),(31,False)])
def test_relay_30_seconds_is_receipt_not_authorization_start(env,monkeypatch,seconds,ok):
    now=jst.now_jst();monkeypatch.setattr(jst,'now_jst',lambda:now)
    profile=x.XAuthProfile.prepare(env['cfg']);session=authflow.begin('alpha',env['cfg'],profile)
    session['created_at']=jst.iso(now-datetime.timedelta(seconds=500));authflow._write_session('alpha',session)
    def relay(s,register=False,**kw):return (201,{}) if register else (200,{'code':env['code'],'received_at':jst.iso(now-datetime.timedelta(seconds=seconds))})
    monkeypatch.setattr(authflow,'begin',lambda *a,**k:session)
    monkeypatch.setattr(authflow,'relay_request',relay)
    rc=oauth.run_auth('alpha',by='operator',human_output=lambda _:None,log=env['lines'].append)
    assert rc==(0 if ok else 2)
    assert bool(env['calls']) is ok
    if ok:assert saved(env)['auth_via']=='relay'
    else:assert any('x_code_expired' in line for line in env['lines'])


@pytest.mark.parametrize('field,value',[('expires_in',None),('expires_in',0),('expires_in',False),('expires_in','7200'),('scope',None),('scope','tweet.read'),('refresh_token',None),('access_token',None),('token_type','wrong')])
def test_response_missing_or_invalid_never_guesses_and_preserves_old(env,field,value):
    before,_=old_token(env);env['behavior']['token']={field:value}
    assert run(env)==2 and Path(env['cfg']['token']).read_bytes()==before
    assert [row[1] for row in env['calls']]==['/2/oauth2/token'] and admin_log.read()[0]==[]


@pytest.mark.parametrize('values',[{'id':''},{'id':{}},{'username':None},{'username':'other'}])
def test_identity_invalid_refuses(env,values):
    before,_=old_token(env);env['behavior']['me']=values
    assert run(env)==2 and Path(env['cfg']['token']).read_bytes()==before


@pytest.mark.parametrize('status',[400,401,403,500])
def test_provider_refusal_does_not_retry_or_leak(env,status):
    before,_=old_token(env);env['behavior']['status']=status
    assert run(env)==2 and Path(env['cfg']['token']).read_bytes()==before
    assert len(env['calls'])==1 and any(f'x_auth_http_{status}' in line for line in env['lines'])
    assert env['token'] not in '\n'.join(env['lines'])


@pytest.mark.parametrize('change',[{'client_type':'public'},{'client_secret':''},{'redirect_uri':'https://outside.invalid/callback'}])
def test_client_confidential_and_callback_required(env,change):
    authclients.write(env['client_path'],{**env['client'],**change})
    assert run(env)==2 and env['calls']==[] and env['queries']==[]


@pytest.mark.parametrize('origin',['http://outside.invalid','https://outside.invalid','https://user@api.x.com','https://api.x.com/other','https://api.x.com?x=y','https://api.x.com\n'])
def test_token_endpoint_external_override_refused(env,monkeypatch,origin):
    monkeypatch.setenv('THTH_X_BASE_URL',origin)
    assert run(env)==2 and env['calls']==[]


def test_refresh_rotation_short_lock_and_maintain(env):
    before,now=old_token(env);env['hook']=lambda:no_lock(env)
    assert maintain.inspect('alpha',now=now)['state']==maintain.REFRESH_DUE
    assert oauth.run_refresh('alpha',now=now,log=env['lines'].append)==0
    token=saved(env)
    assert token['refresh_token']==env['refresh'] and token['refresh_token']!=json.loads(before)['refresh_token']
    assert env['calls'][0][2]=={'grant_type':['refresh_token'],'refresh_token':[json.loads(before)['refresh_token']]}
    assert env['calls'][0][3].startswith('Basic ')
    assert token['auth_via']=='relay' and token['obtained_at']==jst.iso(now)
    assert maintain.inspect('alpha',now=now)['state']==maintain.OK
    assert admin_log.read()[0][-1]['event']=='token_refreshed'


@pytest.mark.parametrize('seconds,state',[(7200,maintain.OK),(301,maintain.OK),(300,maintain.REFRESH_DUE),(0,maintain.REFRESH_DUE),(-100,maintain.REFRESH_DUE)])
def test_x_expiry_threshold_does_not_use_threads_days(env,seconds,state):
    _,now=old_token(env,seconds=seconds)
    assert maintain.inspect('alpha',now=now)['state']==state


@pytest.mark.parametrize('expiry',[None,False,'7200',0,-1,10**100])
def test_unknown_expiry_not_threads_60days(env,expiry):
    before,now=old_token(env,expires_in=expiry)
    assert maintain.inspect('alpha',now=now)['state']==maintain.UNREADABLE
    assert oauth.run_refresh('alpha',now=now,force=True,log=env['lines'].append)==2
    assert env['calls']==[] and Path(env['cfg']['token']).read_bytes()==before


@pytest.mark.parametrize('kind',['http','rotation_missing','scope','identity','id_change'])
def test_failed_refresh_preserves_old_credentials(env,kind):
    before,now=old_token(env)
    if kind=='http':env['behavior']['status']=400
    elif kind=='rotation_missing':env['behavior']['token']={'refresh_token':None}
    elif kind=='scope':env['behavior']['token']={'scope':'tweet.read'}
    else:env['behavior']['me']={'id':'456'} if kind=='id_change' else {'username':'other'}
    assert oauth.run_refresh('alpha',now=now,log=env['lines'].append)==2
    assert Path(env['cfg']['token']).read_bytes()==before and admin_log.read()[0]==[]


@pytest.mark.parametrize('change',['token','client','ledger','flow'])
def test_competing_update_during_refresh_is_not_overwritten(env,change):
    _,now=old_token(env);expected=[]
    def hook():
        env['hook']=None;no_lock(env)
        if change=='token':Path(env['cfg']['token']).write_text(json.dumps({'new':opaque()}))
        elif change=='client':authclients.write(env['client_path'],{**env['client'],'client_secret':opaque()})
        elif change=='ledger':env['ledger'].write_text(json.dumps({**env['cfg'],'handle':'new'}))
        else:authflow.begin('alpha',env['cfg'],x.XAuthProfile.prepare(env['cfg']))
        expected.append(Path(env['cfg']['token']).read_bytes())
    env['hook']=hook
    assert oauth.run_refresh('alpha',now=now,log=env['lines'].append)==2
    assert Path(env['cfg']['token']).read_bytes()==expected[0]
    assert admin_log.read()[0]==[]


def test_check_and_rehearse_are_readonly(env,monkeypatch):
    _,now=old_token(env);before=snapshot(env['root'].parent)
    assert oauth.run_refresh('alpha',check=True,now=now,log=env['lines'].append)==0
    clock=[0];monkeypatch.setattr(authflow.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(authflow.time,'sleep',lambda x:clock.__setitem__(0,clock[0]+x))
    calls=[];monkeypatch.setattr(authflow,'relay_request',lambda s,**kw:(calls.append(kw) or (404,{})))
    human=[]
    assert oauth.run_auth('alpha',by='operator',rehearse=True,human_output=human.append,log=env['lines'].append)==2
    assert len(calls)==300 and len(human)==1 and env['calls']==[]
    assert snapshot(env['root'].parent)==before


def test_cli_account_add_then_auth_reaches_x_profile(env,monkeypatch):
    assert cli.main(['account','add','newx','--media','x','--project','demo','--handle','demo','--by','operator'])==0
    monkeypatch.setattr('builtins.input',lambda:x.CALLBACK+'?'+urllib.parse.urlencode({'code':env['code'],'state':authflow._read_session('newx')['state']}))
    assert cli.main(['auth','newx','--by','operator','--paste'])==0
    cfg=accounts.load_account('newx');assert cfg['production'] is False and cfg['scheduled'] is False
    assert accounts.load_token(cfg)['user_id']=='123' and adapters.capabilities_for('x')==set()


@pytest.mark.parametrize('fault',['zero','partial','fsync','rollback'])
def test_refresh_log_failure_retention_and_rollback_boundary(env,monkeypatch,fault):
    before,now=old_token(env);original=admin_log.os.write
    def emit(fd,data):
        if fault in ('zero','rollback'):raise admin_log.AdminLogError('fault')
        n=original(fd,data if fault=='fsync' else data[:len(data)//2])
        raise admin_log.AdminLogError('fault',appended=n>0,complete=fault=='fsync')
    monkeypatch.setattr(admin_log,'_emit',emit)
    restore=authflow.secrets_fs.atomic_write_text
    def rollback(*a,**kw):
        assert admin_log._active_fd.get() is not None
        if fault=='rollback':raise OSError(env['token'])
        return restore(*a,**kw)
    monkeypatch.setattr(authflow.secrets_fs,'atomic_write_text',rollback)
    assert oauth.run_refresh('alpha',now=now,log=env['lines'].append)==2
    if fault=='zero':assert Path(env['cfg']['token']).read_bytes()==before
    else:
        assert saved(env)['access_token']==env['token'] and saved(env)['refresh_token']==env['refresh']
        assert any('uncertain' in line or 'unconfirmed' in line for line in env['lines'])
    assert env['token'] not in '\n'.join(env['lines'])


def test_maintain_updates_expired_access_with_refresh_token(env):
    _,now=old_token(env,seconds=-60)
    assert maintain.run_maintain('alpha',now=now,as_json=True,log=env['lines'].append)==0
    result=json.loads(env['lines'][-1])['accounts'][0]
    assert result['state']==maintain.REFRESHED and result['refreshed'] is True
    assert saved(env)['expires_in']==7200 and len(env['calls'])==2


def test_unknown_relay_receipt_never_exchanges(env,monkeypatch):
    monkeypatch.setattr(authflow,'relay_request',lambda s,register=False,**kw:(201,{}) if register else (200,{'code':env['code']}))
    assert oauth.run_auth('alpha',by='operator',human_output=lambda _:None,log=env['lines'].append)==2
    assert env['calls']==[] and any('relay_invalid_code' in line for line in env['lines'])


def test_resume_uses_saved_verifier_and_cannot_replay(env):
    profile=x.XAuthProfile.prepare(env['cfg']);session=authflow.begin('alpha',env['cfg'],profile)
    value=x.CALLBACK+'?'+urllib.parse.urlencode({'state':session['state'],'code':env['code']})
    assert oauth.run_auth('alpha',code=value,by='operator',human_output=lambda _:pytest.fail('resume URL'),log=env['lines'].append)==0
    assert env['calls'][0][2]['code_verifier']==[session['code_verifier']]
    before=Path(env['cfg']['token']).read_bytes()
    assert oauth.run_auth('alpha',code=value,by='operator',log=env['lines'].append)==2
    assert Path(env['cfg']['token']).read_bytes()==before and len(env['calls'])==2


@pytest.mark.parametrize('change',['token','client','ledger','flow'])
def test_auth_wait_competing_update_does_not_overwrite(env,change):
    before,_=old_token(env);expected=[]
    def entered_changed():
        value=entered(env);no_lock(env)
        if change=='token':Path(env['cfg']['token']).write_text(json.dumps({'new':opaque()}))
        elif change=='client':authclients.write(env['client_path'],{**env['client'],'client_secret':opaque()})
        elif change=='ledger':env['ledger'].write_text(json.dumps({**env['cfg'],'handle':'new'}))
        else:authflow.begin('alpha',env['cfg'],x.XAuthProfile.prepare(env['cfg']))
        expected.append(Path(env['cfg']['token']).read_bytes());return value
    assert oauth.run_auth('alpha',by='operator',input_func=entered_changed,human_output=lambda _:None,log=env['lines'].append)==2
    assert Path(env['cfg']['token']).read_bytes()==expected[0] and admin_log.read()[0]==[]


def test_missing_refresh_never_causes_maintain_http(env):
    _,now=old_token(env,refresh_token=None)
    assert maintain.inspect('alpha',now=now)['state']==maintain.TOKEN_INCOMPLETE
    assert maintain.run_maintain('alpha',now=now,log=lambda _:None)==1 and env['calls']==[]



@pytest.mark.parametrize('token',[{}, {'access_token':'present'}])
def test_incomplete_x_token_guides_to_working_auth_not_token_set(env,token):
    Path(env['cfg']['token']).write_text(json.dumps(token))
    row=maintain.inspect('alpha',now=jst.now_jst())
    assert row['state']==maintain.TOKEN_INCOMPLETE
    assert 'thth auth <account> --by <actor>' in row['message']
    assert 'thth token set' not in row['message']
    assert run(env)==0 and saved(env)['scopes_source']=='response'
