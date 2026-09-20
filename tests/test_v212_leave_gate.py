"""Local account stop semantics, using isolated roots and dynamic fake values."""
import io
import json
import os
import threading
import time
import pytest
from thth import accounts, leave_gate as gate, server_files
from thth.adapters import ThreadsAdapter, BlueskyAdapter, MastodonAdapter
from thth.adapters.base import Post
from tests.test_v212_server_writes import env


def stop(account):
    with gate.lease(account,exclusive=True):
        with server_files.directory(gate.location(),create=True,private=True) as fd:
            server_files.replace_at(fd,account+'.json',server_files.encode({'account':account,'phase':'stopped'}),private=True)


def test_loader_and_old_config_stop_but_other_account_continues(env):
    a=accounts.load_account('alpha');b=accounts.load_account('beta')
    stop('alpha')
    with pytest.raises(accounts.AccountStopped):accounts.load_account('alpha')
    with pytest.raises(accounts.AccountStopped):accounts.load_token(a)
    with gate.recovery('alpha'):assert accounts.load_account('alpha')==a
    assert accounts.load_account('beta')==b and accounts.load_token(b)


def test_reused_adapter_preserves_type_and_refuses_after_stop(env,monkeypatch):
    calls=[]
    monkeypatch.setattr(ThreadsAdapter,'_get',lambda *a,**k:calls.append(1) or {'data':[]})
    token=accounts.load_token(accounts.load_account('alpha'));token['user_id']='synthetic-user'
    adapter=ThreadsAdapter.from_account(accounts.load_account('alpha'),token)
    assert isinstance(adapter,ThreadsAdapter)
    assert adapter.recent_posts()==[] and calls==[1]
    stop('alpha')
    with pytest.raises(accounts.AccountStopped):adapter.recent_posts()
    assert calls==[1]


def test_threads_stop_during_wait_is_veto_before_publish(env,monkeypatch):
    calls=[]
    monkeypatch.setenv('THTH_THREADS_WAIT_SECONDS','1')
    monkeypatch.setattr(ThreadsAdapter,'_post',lambda self,path,params:calls.append(path) or {'id':'123'})
    monkeypatch.setattr('thth.adapters.threads.time.sleep',lambda _:stop('alpha'))
    cfg=accounts.load_account('alpha');adapter=ThreadsAdapter.from_account(cfg,accounts.load_token(cfg))
    result=adapter.publish(Post(text='synthetic'),dry_run=False)
    assert len(calls)==1 and calls[0].endswith('/threads')
    assert result.failure=='publish_vetoed' and result.error=='account_stopped'


def test_exclusive_stop_waits_until_response_close_and_future_request_zero(env):
    entered=threading.Event();finished=threading.Event();calls=[]
    def stop_thread():entered.set();stop('alpha');finished.set()
    with gate.scope('alpha'):
        response=gate.urlopen(lambda *a,**k:calls.append(1) or io.BytesIO(b'answer'),'fake',timeout=1)
        thread=threading.Thread(target=stop_thread);thread.start();assert entered.wait(1)
        assert not finished.wait(.05)
        assert response.read()==b'answer';response.close()
        assert finished.wait(2);thread.join()
        with pytest.raises(accounts.AccountStopped):gate.urlopen(lambda *a,**k:calls.append(1),'fake',timeout=1)
    assert calls==[1]
    # An unrelated healthcheck has no inferred account identity.
    assert gate.urlopen(lambda *a,**k:'healthcheck','fake',timeout=1)=='healthcheck'


def test_injected_publish_lease_stops_without_ambiguous_result(env):
    calls=[]
    class Injected:
        def publish(self,*a,**k):calls.append(1)
    adapter=gate.bind(Injected(),accounts.load_account('alpha'));stop('alpha')
    result=adapter.publish(Post(text='synthetic'),dry_run=False)
    assert calls==[] and result.failure=='publish_vetoed'


def test_loader_identity_is_not_overridden_by_incorrect_ledger_account_field(env):
    path=env['root']/'accounts/alpha.json';value=json.loads(path.read_text());value['account']='beta';path.write_text(json.dumps(value))
    cfg=accounts.load_account('alpha');assert gate.name_for(cfg)=='alpha' and gate.name_for(cfg.copy())=='alpha'
    stop('alpha')
    with pytest.raises(accounts.AccountStopped):gate.check_config(cfg)


def test_injected_adapter_subclass_custom_transport_holds_lease(env):
    from thth.adapters.base import Adapter
    entered=threading.Event();release=threading.Event();stopped=threading.Event()
    class Injected(Adapter):
        def whoami(self):entered.set();assert release.wait(2);return {'user_id':'synthetic'}
    adapter=gate.bind(Injected(),accounts.load_account('alpha'));result=[]
    reader=threading.Thread(target=lambda:result.append(adapter.whoami()));reader.start();assert entered.wait(1)
    stopper=threading.Thread(target=lambda:(stop('alpha'),stopped.set()));stopper.start()
    assert not stopped.wait(.05);release.set();reader.join();stopper.join()
    assert result==[{'user_id':'synthetic'}] and stopped.is_set()
    with pytest.raises(accounts.AccountStopped):adapter.whoami()


def test_error_body_is_drained_under_lease_then_memory_only(env):
    import io,urllib.error
    entered=threading.Event();release=threading.Event();stopped=threading.Event();errors=[]
    class Body(io.BytesIO):
        def read(self,*a):entered.set();assert release.wait(2);return super().read(*a)
    def opener(*a,**kw):raise urllib.error.HTTPError('https://example.invalid',403,'denied',{},Body(b'{"error":"synthetic"}'))
    def read():
        with gate.scope('alpha'):
            try:gate.urlopen(opener,None,timeout=1)
            except urllib.error.HTTPError as exc:errors.append(exc)
    reader=threading.Thread(target=read);reader.start();assert entered.wait(1)
    stopper=threading.Thread(target=lambda:(stop('alpha'),stopped.set()));stopper.start();assert not stopped.wait(.05)
    release.set();reader.join();stopper.join()
    assert stopped.is_set() and len(errors)==1 and errors[0].read()==b'{"error":"synthetic"}'


def test_late_observation_and_cursor_do_not_recreate_removed_state(env):
    from thth import doctor,handoff_cursor
    stop('alpha')
    for call in (lambda:doctor.record_observation('alpha',{'probes':[]}),lambda:handoff_cursor.write_snapshot('alpha','handoff_cursor.json',{})):
        with pytest.raises(accounts.AccountStopped):call()
    assert not (env['root']/'state/alpha').exists()


def test_missing_by_and_stopped_account_add_do_not_recreate_ledger(env,capsys):
    from thth import cli
    stop('alpha');p=env['root']/'accounts/alpha.json';before=p.read_bytes()
    assert cli.main(['account','add','alpha','--media','threads','--project','alpha','--force','--by','operator'])==2
    assert p.read_bytes()==before
    assert 'account_stopped' in capsys.readouterr().err


@pytest.mark.parametrize('command',[
    ['account','alpha','--json'],['doctor','alpha','--json'],['posts','alpha','--json'],
    ['mentions','alpha','--json'],['where','alpha','--word','synthetic','--json'],
    ['thread','alpha','123','--json'],['run','alpha'],['collect','alpha'],
    ['auth','alpha','--by','operator'],['token','set','alpha','--stdin','--by','operator'],
    ['token','revoke','alpha','--by','operator'],['refresh','alpha','--force'],
])
def test_stopped_public_entries_do_not_touch_provider_or_recreate_state(env,monkeypatch,capsys,command):
    from thth import cli
    import socket
    calls=[]
    def denied(*a,**k):calls.append(1);raise AssertionError('provider touched')
    monkeypatch.setattr(socket.socket,'connect',denied)
    stop('alpha');before=(env['root']/'secrets/alpha.json').read_bytes()
    result=cli.main(command)
    output=capsys.readouterr()
    assert calls==[] and (env['root']/'secrets/alpha.json').read_bytes()==before
    assert not (env['root']/'state/alpha').exists()
    assert 'Traceback' not in output.err


def test_real_process_request_lease_is_drained_before_stop(env):
    import subprocess,sys
    script='''
import sys
from thth import leave_gate,accounts
with leave_gate.scope('alpha'),leave_gate.lease():
 print('leased',flush=True)
 sys.stdin.readline()
print('released',flush=True)
sys.stdin.readline()
try:
 with leave_gate.scope('alpha'),leave_gate.lease():print('unexpected',flush=True)
except accounts.AccountStopped:print('blocked',flush=True)
'''
    process=subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ))
    thread=None;done=threading.Event()
    try:
        assert process.stdout.readline().strip()=='leased'
        thread=threading.Thread(target=lambda:(stop('alpha'),done.set()));thread.start()
        assert not done.wait(.05)
        process.stdin.write('release\n');process.stdin.flush()
        assert process.stdout.readline().strip()=='released' and done.wait(3)
        process.stdin.write('check\n');process.stdin.flush()
        assert process.stdout.readline().strip()=='blocked'
        assert process.wait(timeout=3)==0 and process.stderr.read()==''
    finally:
        if process.poll() is None:process.kill();process.wait()
        if thread:thread.join(3)


@pytest.mark.parametrize('name,filename',[('_alice','handoff_cursor.json'),('_alice','doctor.json'),('_admin','handoff_cursor.json'),('_admin','doctor.json')])
def test_underscore_accounts_do_not_bypass_snapshot_stop(env,name,filename):
    from thth import handoff_cursor
    stop(name)
    with pytest.raises(accounts.AccountStopped):handoff_cursor.write_snapshot(name,filename,{'synthetic':True})
    assert not (env['root']/'state'/name/filename).exists()
