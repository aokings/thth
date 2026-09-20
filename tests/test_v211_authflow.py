"""2.11 authorization boundaries, generated credentials and local-only HTTP."""
import contextlib
import datetime
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import threading
import urllib.parse

import pytest
from thth import accounts, admin_log, authflow, cli, jst, oauth
from thth.adapters.auth_threads import ThreadsAuthProfile


def opaque():
    return secrets.token_urlsafe(32)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root=tmp_path/'root';root.mkdir()
    home=tmp_path/'home';home.mkdir()
    ledgers=tmp_path/'accounts';ledgers.mkdir()
    monkeypatch.setenv('HOME',str(home));monkeypatch.setenv('XDG_CONFIG_HOME',str(home))
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(ledgers))
    app=tmp_path/'app.env';client,private=opaque(),opaque()
    app.write_text(f'THREADS_APP_ID={client}\nTHREADS_APP_SECRET={private}\n');app.chmod(0o600)
    monkeypatch.setenv('THTH_APP_ENV_PATH',str(app))
    cfg=json.loads((Path(__file__).parents[1]/'accounts.example/threads.json').read_text())
    cfg.update(account='alpha',project='demo',handle='demo',repo_dir=str(root/'repos/_none'),
               token=str(root/'alpha.token'),env=str(root/'alpha.env'),redirect_uri='https://thth.me/callback/')
    path=ledgers/'alpha.json';path.write_text(json.dumps(cfg))
    cfg=accounts.load_account('alpha')
    short,access,code=opaque(),opaque(),opaque()
    monkeypatch.setattr(oauth,'exchange_short_lived_token',lambda *a,**k: {'access_token':short})
    monkeypatch.setattr(oauth,'exchange_long_lived_token',lambda *a,**k: {'access_token':access,'expires_in':5184000})
    monkeypatch.setattr(oauth,'fetch_me',lambda *a,**k:{'id':'12345','username':'demo'})
    monkeypatch.setattr(oauth,'fetch_token_scopes',lambda *a,**k:['threads_basic'])
    profile=ThreadsAuthProfile(client,private,cfg['redirect_uri'],list(oauth.scopes_mod.DEFAULT_SCOPES))
    return dict(root=root,home=home,ledger=path,cfg=cfg,profile=profile,code=code,access=access,short=short,app=app,private=private)


def url(session, code):
    return 'https://thth.me/callback/?'+urllib.parse.urlencode({'state':session['state'],'code':code})


def snapshot(path):
    return {str(p.relative_to(path)):(stat.S_IMODE(p.stat().st_mode),hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in path.rglob('*')}


def no_lock(env):
    path=env['root']/'state/_admin/accounts.ndjson'
    child=subprocess.run([sys.executable,'-c','import os,fcntl,sys;f=os.open(sys.argv[1],os.O_RDONLY);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)',str(path)],capture_output=True,timeout=3)
    assert child.returncode==0


@contextlib.contextmanager
def relay(env, monkeypatch, statuses=None):
    calls=[];states={};statuses=list(statuses or [])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(('POST',self.path,data));states[self.path]=data['read_key_hash']
            self.send_response(201);self.end_headers();self.wfile.write(b'{"status":"pending"}')
        def do_GET(self):
            key=self.headers.get('Authorization','').removeprefix('Bearer ')
            calls.append(('GET',self.path,hashlib.sha256(key.encode()).hexdigest()))
            status=statuses.pop(0) if statuses else 200
            if states.get(self.path)!=hashlib.sha256(key.encode()).hexdigest():status=401
            no_lock(env)
            self.send_response(status);self.end_headers()
            if status==200:self.wfile.write(json.dumps({'code':env['code'],'received_at':jst.iso()}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('THTH_AUTH_RELAY_BASE_URL',f'http://127.0.0.1:{server.server_port}')
    try:yield calls
    finally:server.shutdown();server.server_close();thread.join()


def test_real_http_relay_human_url_only_presence_log(env, monkeypatch, capsys):
    lines=[];human=[]
    with relay(env,monkeypatch) as calls:
        assert oauth.run_auth('alpha',by='operator',log=lines.append,human_output=human.append)==0
    assert len(human)==1
    state=urllib.parse.parse_qs(urllib.parse.urlsplit(human[0]).query)['state'][0]
    assert len(state)==43
    assert [x[0] for x in calls]==['POST','GET']
    assert calls[0][2]['read_key_hash']==calls[1][2]
    assert set(calls[0][2])=={'read_key_hash'}
    token=json.loads(Path(env['cfg']['token']).read_text())
    assert token['access_token']==env['access'] and token['auth_via']=='relay'
    assert token['scopes_source']=='response'
    assert stat.S_IMODE(Path(env['cfg']['token']).stat().st_mode)==0o600
    assert authflow._read_session('alpha') is None
    rows,broken=admin_log.read();assert broken==0 and len(rows)==1
    assert rows[0]['diff']=={'token':['absent','present'],'auth_via':[None,'relay']}
    output='\n'.join(lines)+json.dumps(rows)+capsys.readouterr().out
    for value in [state,env['private'],env['access'],env['short'],env['code']]:assert value not in output
    assert not (env['root']/'logs').exists()


def test_paste_wait_and_exchange_have_no_lock(env, monkeypatch):
    human=[]
    def entered():
        no_lock(env)
        return url(authflow._read_session('alpha'),env['code'])
    def exchange(*a,**k):
        no_lock(env);return {'access_token':env['short']}
    monkeypatch.setattr(oauth,'exchange_short_lived_token',exchange)
    assert oauth.run_auth('alpha',by='operator',input_func=entered,human_output=human.append,log=lambda _:None)==0
    assert len(human)==1


@pytest.mark.parametrize('change',['flow','ledger','client'])
def test_stale_flow_cannot_save_after_newer_flow_or_binding_change(env, change):
    def entered():
        old=authflow._read_session('alpha')
        if change=='flow':authflow.begin('alpha',env['cfg'],env['profile'])
        elif change=='ledger':
            cfg=json.loads(env['ledger'].read_text());cfg['handle']='changed';env['ledger'].write_text(json.dumps(cfg))
        else:env['app'].write_text('THREADS_APP_ID='+opaque()+'\nTHREADS_APP_SECRET='+opaque()+'\n')
        return url(old,env['code'])
    lines=[]
    assert oauth.run_auth('alpha',by='operator',input_func=entered,human_output=lambda _:None,log=lines.append)==2
    assert not Path(env['cfg']['token']).exists()
    assert admin_log.read()[0]==[]
    assert any('auth_session_changed' in x for x in lines)


@pytest.mark.parametrize('fault',['zero','partial','fsync'])
def test_log_failure_snapshot_is_latest_and_rollback_runs_under_lock(env, monkeypatch, fault):
    path=Path(env['cfg']['token']);old=opaque();path.write_text(old);path.chmod(0o600)
    latest=opaque();path.write_text(latest);original_write=admin_log.os.write
    def entered():
        no_lock(env)
        return url(authflow._read_session('alpha'),env['code'])
    def emit(fd,data):
        if fault=='zero':raise admin_log.AdminLogError('fault')
        written=original_write(fd,data if fault=='fsync' else data[:len(data)//2])
        raise admin_log.AdminLogError('fault',appended=written>0,complete=fault=='fsync')
    original=authflow.secrets_fs.atomic_write_text
    def restore(*args,**kwargs):
        assert admin_log._active_fd.get() is not None
        return original(*args,**kwargs)
    monkeypatch.setattr(authflow.secrets_fs,'atomic_write_text',restore)
    monkeypatch.setattr(admin_log,'_emit',emit)
    lines=[]
    assert oauth.run_auth('alpha',by='operator',input_func=entered,human_output=lambda _:None,log=lines.append)==2
    if fault=='zero':
        assert path.read_text()==latest
        assert authflow._read_session('alpha') is not None
    else:
        assert json.loads(path.read_text())['access_token']==env['access']
        assert authflow._read_session('alpha') is None
        assert any('uncertain' in x or 'unconfirmed' in x for x in lines)
    assert old not in path.read_text()


def test_rollback_fault_is_loud_and_not_success(env,monkeypatch):
    Path(env['cfg']['token']).write_text(opaque())
    monkeypatch.setattr(admin_log,'_emit',lambda *a:(_ for _ in ()).throw(admin_log.AdminLogError('fault')))
    monkeypatch.setattr(authflow.secrets_fs,'atomic_write_text',lambda *a,**k:(_ for _ in ()).throw(OSError('private '+env['access'])))
    lines=[]
    assert oauth.run_auth('alpha',by='operator',input_func=lambda:url(authflow._read_session('alpha'),env['code']),human_output=lambda _:None,log=lines.append)==2
    assert lines[-1]=='auth_rollback_failed_outcome_uncertain: 保存状態を確認してください'
    assert env['access'] not in ''.join(lines)


@pytest.mark.parametrize('kind',['missing','future','at600','unknown_keys','duplicate_state'])
def test_resume_invalid_or_expired_is_closed(env,monkeypatch,kind):
    now=jst.now_jst();monkeypatch.setattr(jst,'now_jst',lambda:now)
    session=authflow.begin('alpha',env['cfg'],env['profile'])
    if kind=='missing':authflow._clear_session('alpha')
    if kind in ('future','at600'):
        session['created_at']=jst.iso(now+datetime.timedelta(seconds=1 if kind=='future' else -600))
        authflow._write_session('alpha',session)
    if kind=='unknown_keys':
        session['extra']=True;authflow._write_session('alpha',session)
    code=url(session,env['code'])+('&state='+session['state'] if kind=='duplicate_state' else '')
    assert oauth.run_auth('alpha',by='operator',code=code,log=lambda _:None,human_output=lambda _:None)==2
    assert not Path(env['cfg']['token']).exists()


def test_legacy_resume_and_second_replay_refused(env):
    state=opaque();oauth._save_auth_state('alpha',state)
    code=url({'state':state},env['code'])
    assert oauth.run_auth('alpha',by='operator',code=code,log=lambda _:None,human_output=lambda _:pytest.fail('resume emitted URL'))==0
    before=Path(env['cfg']['token']).read_bytes()
    assert oauth.run_auth('alpha',by='operator',code=code,log=lambda _:None)==2
    assert Path(env['cfg']['token']).read_bytes()==before
    assert len(admin_log.read()[0])==1


@pytest.mark.parametrize('existing',[False,True])
@pytest.mark.parametrize('status',[404,429,200])
def test_rehearse_ram_only_timeout_and_unexpected_code(env,monkeypatch,existing,status):
    if existing:
        authflow.begin('alpha',env['cfg'],env['profile'])
        Path(env['cfg']['token']).write_text(opaque())
        (env['root']/'doctor.json').write_text('{}')
        env['app'].chmod(0o644) # rehearsal must not "repair" app permissions
    else:
        env['home'].rmdir() # even a missing HOME stays absent
    before=snapshot(env['root'].parent)
    clock=[0];calls=[]
    monkeypatch.setattr(authflow.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(authflow.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def request(session,**kwargs):
        assert not kwargs.get('register');calls.append(session.copy())
        return status, {'code':env['code'],'received_at':jst.iso()}
    monkeypatch.setattr(authflow,'relay_request',request)
    lines=[]
    assert cli.main(['auth','alpha','--by','operator','--rehearse'])==2
    assert snapshot(env['root'].parent)==before
    assert clock[0]==(0 if status==200 else 600)
    assert len(calls)==(1 if status==200 else 300)
    assert len(calls[0]['state'])==43 and calls[0]['state']!=calls[0]['read_key']


def test_no_by_before_any_write_or_network(env,monkeypatch):
    before=snapshot(env['root'].parent)
    monkeypatch.setattr(authflow,'relay_request',lambda *a,**k:pytest.fail('network'))
    assert oauth.run_auth('alpha',by=None,log=lambda _:None)==2
    assert snapshot(env['root'].parent)==before

@pytest.mark.parametrize('operation',['set','refresh','revoke'])
@pytest.mark.parametrize('append_fault',[False,True])
def test_waiting_auth_rejects_credential_change_before_save(env,monkeypatch,operation,append_fault):
    path=Path(env['cfg']['token']);path.write_text(opaque());path.chmod(0o600)
    latest=opaque()
    def entered():
        session=authflow._read_session('alpha')
        with admin_log.transaction():
            if operation=='revoke':path.unlink()
            else:path.write_text(latest)
        return url(session,env['code'])
    if append_fault:
        monkeypatch.setattr(admin_log,'_emit',lambda *a:pytest.fail('reached append after credential conflict'))
    lines=[]
    assert oauth.run_auth('alpha',by='operator',input_func=entered,human_output=lambda _:None,log=lines.append)==2
    assert any('auth_credential_changed' in x for x in lines)
    if operation=='revoke':assert not path.exists()
    else:assert path.read_text()==latest
    assert admin_log.read()[0]==[]

@pytest.mark.parametrize('when',['register','poll'])
def test_relay_failure_uses_same_flow_paste_without_logger_url(env,monkeypatch,when):
    import builtins
    seen=[];human=[];lines=[]
    def request(session,register=False,**kwargs):
        seen.append((register,session['state']))
        return (503,{}) if register==(when=='register') else (201,{})
    monkeypatch.setattr(authflow,'relay_request',request)
    monkeypatch.setattr(builtins,'input',lambda:url(authflow._read_session('alpha'),env['code']))
    assert oauth.run_auth('alpha',by='operator',log=lines.append,human_output=human.append)==0
    token=json.loads(Path(env['cfg']['token']).read_text());assert token['auth_via']=='paste'
    assert len(human)==1 and len(set(s for _,s in seen))==1
    assert seen[0][1] not in '\n'.join(lines)


@pytest.mark.parametrize('kind',['stale','future','empty','oversized','invalid_time'])
def test_invalid_relay_code_never_exchanges_or_falls_back(env,monkeypatch,kind):
    import builtins
    now=jst.now_jst();value={'code':env['code'],'received_at':jst.iso(now)}
    if kind=='stale':value['received_at']=jst.iso(now-datetime.timedelta(seconds=300))
    if kind=='future':value['received_at']=jst.iso(now+datetime.timedelta(seconds=10))
    if kind=='empty':value['code']=''
    if kind=='oversized':value['code']=opaque()*100
    if kind=='invalid_time':value['received_at']='bad'
    monkeypatch.setattr(authflow,'relay_request',lambda session,register=False,**kw:(201,{}) if register else (200,value))
    monkeypatch.setattr(oauth,'exchange_short_lived_token',lambda *a,**kw:pytest.fail('exchange'))
    monkeypatch.setattr(builtins,'input',lambda:pytest.fail('paste fallback'))
    assert oauth.run_auth('alpha',by='operator',log=lambda _:None,human_output=lambda _:None)==2
    assert not Path(env['cfg']['token']).exists()


def test_rehearse_human_url_one_line_and_missing_client_no_writes(env,monkeypatch):
    human=[];lines=[];calls=[]
    clock=[0];monkeypatch.setattr(authflow.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(authflow.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    monkeypatch.setattr(authflow,'relay_request',lambda s,**kw:(calls.append((s,kw)) or (404,{})))
    before=snapshot(env['root'].parent)
    assert oauth.run_auth('alpha',by='operator',rehearse=True,human_output=human.append,log=lines.append)==2
    assert len(human)==1 and len(human[0].splitlines())==1
    state=urllib.parse.parse_qs(urllib.parse.urlsplit(human[0]).query)['state'][0]
    assert state==calls[0][0]['state'] and state not in '\n'.join(lines)
    assert calls[0][0]['read_key'] not in human[0]
    assert all(not kw.get('register') for _,kw in calls)
    assert snapshot(env['root'].parent)==before
    env['app'].unlink();before=snapshot(env['root'].parent);human.clear();calls.clear();lines.clear()
    assert oauth.run_auth('alpha',by='operator',rehearse=True,human_output=human.append,log=lines.append)==2
    assert not human and not calls and lines==['rehearse_client_id_unavailable']
    assert snapshot(env['root'].parent)==before


@pytest.mark.parametrize('kind',['symlink','fifo'])
def test_unsafe_session_refuses_without_token_or_outside_write(env,kind):
    folder=env['root']/'state/alpha';folder.mkdir(parents=True)
    target=folder/'auth_state.json';outside=env['root']/'outside';outside.write_text(opaque());original=outside.read_bytes()
    if kind=='symlink':target.symlink_to(outside)
    else:os.mkfifo(target)
    assert oauth.run_auth('alpha',by='operator',input_func=lambda:pytest.fail('input'),human_output=lambda _:pytest.fail('URL'),log=lambda _:None)==2
    assert outside.read_bytes()==original and not Path(env['cfg']['token']).exists()


def test_app_removed_while_waiting_is_bounded(env):
    def entered():
        session=authflow._read_session('alpha');env['app'].unlink();return url(session,env['code'])
    lines=[]
    assert oauth.run_auth('alpha',by='operator',input_func=entered,human_output=lambda _:None,log=lines.append)==2
    assert lines[-1]=='auth_failed: 保存しませんでした'
    assert not Path(env['cfg']['token']).exists()


def test_nested_admin_transaction_cannot_hold_lock_during_auth(env):
    with admin_log.transaction():
        assert oauth.run_auth('alpha',by='operator',input_func=lambda:pytest.fail('input'),human_output=lambda _:pytest.fail('URL'),log=lambda _:None)==2
    assert authflow._read_session('alpha') is None

@pytest.mark.parametrize('endpoint',[
    'http://graph.threads.net','http://external.example','https://external.example',
    'https://user:pass@graph.threads.net','https://graph.threads.net.attacker.example',
    'https://graph.threads.net/other','https://graph.threads.net?to=elsewhere',
    'https://graph.threads.net#fragment','https://graph.threads.net:444',
    'https://graph.threads.net\n',
])
def test_wrong_token_origin_rejected_before_session_or_exchange(env,monkeypatch,endpoint):
    monkeypatch.setenv('THTH_THREADS_BASE_URL',endpoint)
    before=snapshot(env['root'])
    monkeypatch.setattr(oauth,'exchange_short_lived_token',lambda *a,**k:pytest.fail('exchange'))
    lines=[]
    assert oauth.run_auth('alpha',by='operator',input_func=lambda:pytest.fail('input'),human_output=lambda _:pytest.fail('URL'),log=lines.append)==1
    assert snapshot(env['root'])==before
    assert 'auth_token_endpoint_invalid' in lines[0]
    assert endpoint not in '\n'.join(lines)

@pytest.mark.parametrize('systemd',[False,True])
@pytest.mark.parametrize('binary',[False,True])
def test_auth_and_rehearse_do_not_depend_on_systemd(env,monkeypatch,systemd,binary):
    import shlex
    bindir=env['root'].parent/'bin';bindir.mkdir();marker=env['root'].parent/'systemctl-called'
    if binary:
        executable=bindir/'systemctl';executable.write_text('#!/bin/sh\necho called > '+shlex.quote(str(marker))+'\nexit 99\n');executable.chmod(0o755)
    monkeypatch.setenv('PATH',str(bindir))
    cfg=json.loads(env['ledger'].read_text());cfg['systemd']=systemd;env['ledger'].write_text(json.dumps(cfg))
    assert oauth.run_auth('alpha',by='operator',input_func=lambda:url(authflow._read_session('alpha'),env['code']),human_output=lambda _:None,log=lambda _:None)==0
    before=snapshot(env['root'].parent)
    clock=[0];monkeypatch.setattr(authflow.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(authflow.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    monkeypatch.setattr(authflow,'relay_request',lambda *a,**k:(404,{}))
    assert oauth.run_auth('alpha',by='operator',rehearse=True,human_output=lambda _:None,log=lambda _:None)==2
    assert clock[0]==600 and not marker.exists()
    assert snapshot(env['root'].parent)==before

@pytest.mark.parametrize('control',['\r','\n','\t'])
def test_paste_internal_control_rejected_before_url_parser(env,control):
    session=authflow.begin('alpha',env['cfg'],env['profile'])
    raw=url(session,env['code'])+control+'suffix'
    with pytest.raises(authflow.FlowError,match='auth_paste_control_character'):
        authflow.paste(raw,session)
    assert authflow.paste('  '+url(session,env['code'])+'\n',session)==env['code']
