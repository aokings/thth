"""Private App Password stdin and human auth; dynamic fake values, loopback only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import stat
import string
import threading

import pytest
from thth import accounts,admin_log,authflow,cli,jst,oauth
from thth.adapters import bluesky
from tests.test_v211_authflow import snapshot,opaque,no_lock


def password():return '-'.join(''.join(secrets.choice(string.ascii_lowercase) for _ in range(4)) for _ in range(4))


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();home=tmp_path/'home';home.mkdir();ledgers=tmp_path/'accounts';ledgers.mkdir()
    for key,value in [('THTH_ROOT',root),('HOME',home),('XDG_CONFIG_HOME',home),('THTH_ACCOUNTS_DIR',ledgers)]:monkeypatch.setenv(key,str(value))
    cfg=json.loads((Path(__file__).parents[1]/'accounts.example/bluesky.json').read_text())
    cfg.update(account='alpha',project='demo',handle='demo.bsky.social',repo_dir=str(root/'repos/_none'),token=str(root/'alpha.token'),env=str(root/'alpha.env'))
    data=dict(root=root,home=home,cfg=cfg,ledger=ledgers/'alpha.json',password=password(),access=opaque(),refresh=opaque(),calls=[],behavior={},lines=[],hook=None)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            data['calls'].append((self.path,value))
            if data['hook']:data['hook']()
            body={'did':'did:plc:example','handle':'demo.bsky.social','accessJwt':data['access'],'refreshJwt':data['refresh']}
            body.update(data['behavior'].get('body',{}));status=data['behavior'].get('status',200)
            if status!=200:body={'message':data['password']+' '+data['access']+' '+data['refresh'],'error':'failure'}
            self.send_response(status);self.end_headers();self.wfile.write(json.dumps(body).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cfg['service']=f'http://127.0.0.1:{server.server_port}'
    data['ledger'].write_text(json.dumps(cfg));data['cfg']=accounts.load_account('alpha')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield data
    finally:server.shutdown();server.server_close();thread.join()


def run(env,**kwargs):return oauth.run_token_set('alpha',stdin=True,by='operator',input_func=lambda:env['password']+'\n',log=env['lines'].append,**kwargs)
def saved(env):return json.loads(Path(env['cfg']['token']).read_text())


def test_stdin_identifier_from_ledger_private_password_not_access_token(env,monkeypatch,capsys):
    env['hook']=lambda:no_lock(env)
    monkeypatch.setattr('sys.stdin',io.StringIO(env['password']+'\n'))
    assert cli.main(['token','set','alpha','--stdin','--by','operator'])==0
    row=saved(env)
    assert row['identifier']=='demo.bsky.social' and row['app_password']==env['password']
    assert row['did']==row['user_id']=='did:plc:example' and row['handle']==row['username']=='demo.bsky.social'
    assert row['auth_via']=='token_set' and row['scopes'] is None and row['scopes_source']=='unknown' and row['no_expiry'] is True
    assert not set(row)&{'accessJwt','refreshJwt','access_token','expires_in','expires_at'}
    assert env['calls']==[('/xrpc/com.atproto.server.createSession',{'identifier':'demo.bsky.social','password':env['password']})]
    assert stat.S_IMODE(Path(env['cfg']['token']).stat().st_mode)==0o600
    rows,broken=admin_log.read();assert broken==0 and rows[-1]['diff']=={'token':['absent','present'],'auth_via':[None,'token_set']}
    output=capsys.readouterr().out+json.dumps(rows)
    for value in [env['password'],env['access'],env['refresh']]:assert value not in output


@pytest.mark.parametrize('entry',['stdin','human'])
def test_input_wait_and_api_do_not_hold_global_lock(env,entry):
    def reader():no_lock(env);return env['password']
    env['hook']=lambda:no_lock(env)
    if entry=='stdin':rc=oauth.run_token_set('alpha',stdin=True,by='operator',input_func=reader,log=env['lines'].append)
    else:rc=oauth.run_auth('alpha',by='operator',identifier_input=lambda:'@DEMO.BSKY.SOCIAL',password_input=reader,log=env['lines'].append)
    assert rc==0 and saved(env)['auth_via']=='token_set'


@pytest.mark.parametrize('values',[{'did':None},{'did':{}},{'did':[]},{'did':''},{'did':'did:plc:x\n'},{'handle':'demo.bsky.social\n'},{'handle':None},{'handle':{}},{'handle':[]},{'handle':''},{'handle':'other.bsky.social'},{'accessJwt':{}},{'accessJwt':None}])
@pytest.mark.parametrize('entry',['stdin','human'])
def test_missing_typed_identity_rejected_for_both_entries(env,values,entry):
    env['behavior']['body']=values
    old=opaque();Path(env['cfg']['token']).write_text(old)
    rc=run(env,force=True) if entry=='stdin' else oauth.run_auth('alpha',by='operator',identifier_input=lambda:'demo.bsky.social',password_input=lambda:env['password'],log=env['lines'].append)
    assert rc==1 and Path(env['cfg']['token']).read_text()==old and admin_log.read()[0]==[]
    for value in [env['password'],env['access'],env['refresh']]:assert value not in '\n'.join(env['lines'])


@pytest.mark.parametrize('status',[400,401,403,500])
def test_echoing_provider_failure_preserves_old_and_no_secrets(env,status):
    old=opaque();Path(env['cfg']['token']).write_text(old);env['behavior']['status']=status
    assert run(env,force=True)==1 and Path(env['cfg']['token']).read_text()==old
    assert admin_log.read()[0]==[]
    for value in [env['password'],env['access'],env['refresh']]:assert value not in '\n'.join(env['lines'])


def test_force_gate_before_input_and_generation_after_wait(env):
    old=opaque();Path(env['cfg']['token']).write_text(old)
    assert oauth.run_token_set('alpha',stdin=True,by='operator',input_func=lambda:pytest.fail('input consumed'),log=env['lines'].append)==1
    assert env['calls']==[] and Path(env['cfg']['token']).read_text()==old
    assert run(env,force=True)==0 and saved(env)['app_password']==env['password']


@pytest.mark.parametrize('when',['input','http'])
@pytest.mark.parametrize('change',['token','revoke','ledger','flow'])
def test_competing_mutation_preserved(env,when,change):
    old=opaque();Path(env['cfg']['token']).write_text(old);expected=[]
    def mutate():
        env['hook']=None;no_lock(env)
        if change=='token':Path(env['cfg']['token']).write_text(opaque())
        elif change=='revoke':Path(env['cfg']['token']).unlink()
        elif change=='ledger':env['ledger'].write_text(json.dumps({**env['cfg'],'handle':'changed.bsky.social'}))
        else:authflow._write_session('alpha',{'state':opaque(),'created_at':jst.iso()})
        expected.append(Path(env['cfg']['token']).read_bytes() if Path(env['cfg']['token']).exists() else None)
    def reader():
        if when=='input':mutate()
        return env['password']
    if when=='http':env['hook']=mutate
    assert oauth.run_token_set('alpha',stdin=True,force=True,by='operator',input_func=reader,log=env['lines'].append)==2
    actual=Path(env['cfg']['token']).read_bytes() if Path(env['cfg']['token']).exists() else None
    assert actual==expected[0] and admin_log.read()[0]==[]


@pytest.mark.parametrize('fault',['zero','partial','fsync','rollback'])
def test_event_failure_rollback_or_uncertain_retention(env,monkeypatch,fault):
    old=opaque();Path(env['cfg']['token']).write_text(old);original=admin_log.os.write
    def emit(fd,data):
        if fault in ('zero','rollback'):raise admin_log.AdminLogError('fault')
        n=original(fd,data if fault=='fsync' else data[:len(data)//2])
        raise admin_log.AdminLogError('fault',appended=n>0,complete=fault=='fsync')
    monkeypatch.setattr(admin_log,'_emit',emit)
    restore=authflow.secrets_fs.atomic_write_text
    def rollback(*a,**kw):
        assert admin_log._active_fd.get() is not None
        if fault=='rollback':raise OSError(env['password'])
        return restore(*a,**kw)
    monkeypatch.setattr(authflow.secrets_fs,'atomic_write_text',rollback)
    assert run(env,force=True)==2
    if fault=='zero':assert Path(env['cfg']['token']).read_text()==old
    else:
        assert saved(env)['app_password']==env['password']
        assert any('uncertain' in line or 'unconfirmed' in line for line in env['lines'])
    assert env['password'] not in '\n'.join(env['lines'])


@pytest.mark.parametrize('raw',['','not-an-app-password','{"app_password":"ignored"}'])
def test_app_password_only_no_access_token_extraction(env,raw):
    env['password']=raw
    assert run(env)==1 and env['calls']==[]
    assert not Path(env['cfg']['token']).exists()


def test_actor_before_read_or_write(env):
    before=snapshot(env['root'].parent)
    assert oauth.run_token_set('alpha',stdin=True,input_func=lambda:pytest.fail('input'),log=lambda _:None)==2
    assert snapshot(env['root'].parent)==before and env['calls']==[]


def test_ledger_did_mismatch_refuses(env):
    env['ledger'].write_text(json.dumps({**env['cfg'],'user_id':'did:plc:someoneelse'}))
    assert run(env)==1 and not Path(env['cfg']['token']).exists()


def test_stdin_help_distinguishes_media(capsys):
    parser=cli.build_parser()
    # Public parser output, not a duplicate of the help implementation.
    with pytest.raises(SystemExit) as stop:parser.parse_args(['token','set','--help'])
    assert stop.value.code==0
    output=capsys.readouterr().out
    assert "Bluesky" in output and "App Password" in output and "Threads/Mastodon" in output and "台帳" in output



@pytest.mark.parametrize('service',['http://outside.invalid','https://user@bsky.social','https://bsky.social/path','https://bsky.social?secret=x'])
def test_wrong_service_rejected_before_read_or_api(env,service):
    env['ledger'].write_text(json.dumps({**env['cfg'],'service':service}))
    assert oauth.run_token_set('alpha',stdin=True,by='operator',input_func=lambda:pytest.fail('read input'),log=env['lines'].append)==2
    assert env['calls']==[]


def test_later_completed_password_change_wins_over_waiting_old_request(env):
    from concurrent.futures import ThreadPoolExecutor
    ready=threading.Event();release=threading.Event();first=password();second=password()
    def reader():
        ready.set();assert release.wait(10);return first
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(oauth.run_token_set,'alpha',stdin=True,by='operator',input_func=reader,log=env['lines'].append)
        assert ready.wait(10)
        try:
            assert oauth.run_token_set('alpha',stdin=True,by='operator',input_func=lambda:second,log=env['lines'].append)==0
        finally:release.set()
        assert pending.result(timeout=10)==2
    assert saved(env)['app_password']==second and len(admin_log.read()[0])==1


def test_log_zero_failure_removes_new_token(env,monkeypatch):
    monkeypatch.setattr(admin_log,'_emit',lambda *a:(_ for _ in ()).throw(admin_log.AdminLogError('fault')))
    assert run(env)==2 and not Path(env['cfg']['token']).exists()


@pytest.mark.parametrize('kind',['symlink','fifo'])
def test_unsafe_token_refused_before_input(env,kind):
    import os
    path=Path(env['cfg']['token']);target=env['root']/'untouched';target.write_text('fixture')
    if kind=='symlink':path.symlink_to(target)
    else:os.mkfifo(path)
    assert oauth.run_token_set('alpha',stdin=True,by='operator',force=True,input_func=lambda:pytest.fail('input'),log=env['lines'].append)==2
    assert target.read_text()=='fixture' and env['calls']==[]



def test_direct_legacy_auth_requires_explicit_actor_before_input(env):
    before=snapshot(env['root'].parent)
    assert oauth.run_auth_bluesky('alpha',password_input=lambda:pytest.fail('input'),log=env['lines'].append)==2
    assert '--by' in '\n'.join(env['lines']) and snapshot(env['root'].parent)==before
