"""Real process flocks, local report data, no provider or operator credentials."""
import contextlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import pytest
from thth import accounts,cli,jst,leave_gate as gate,report_http,report_service as service,server_files
from tests.test_v212_server_writes import env,draft
from tests.test_mcp import _load_server_module


@contextlib.contextmanager
def exclusive(name='alpha',stopped=False):
    code="""import sys
from thth import leave_gate as g,server_files
with g.lease(sys.argv[1],exclusive=True):
 if sys.argv[2]=='1':
  with server_files.directory(g.location(),private=True) as fd:
   server_files.replace_at(fd,sys.argv[1]+'.json',b'{}',private=True)
 print('ready',flush=True)
 sys.stdin.readline()
"""
    child=subprocess.Popen([sys.executable,'-c',code,name,str(int(stopped))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        yield
    finally:
        stdout,stderr=child.communicate('\n',timeout=3)
        assert child.returncode==0 and not stderr


@pytest.fixture
def content(env):
    draft(env);repo=env['root']/'repos/_server/alpha'
    q=next((repo/'queue').glob('*.md'))
    policy=repo/'study.json';policy.write_text(json.dumps(dict(schema_version=1,id='synthetic-study',account='alpha',hypothesis='h',change='c',decision={'status':'proposed'},baseline_post_ids=['before'],changed_post_ids=['after'])))
    return q,policy


@pytest.mark.parametrize('command',[
    ['account','alpha','--json'],['posts','alpha','--json'],['doctor','alpha','--json'],
    ['replies','alpha','--json'],['measured','alpha','--json'],['threads','alpha','--json'],
    ['queue','alpha','--json'],['after','alpha','--json'],['analytics-report','alpha','--json'],
    ['handoff-report','alpha','--json'],['unanswered','alpha','--json'],['mentions','alpha','--json'],
    ['where','alpha','word','--json'],['who','alpha','@person','--json'],['thread','alpha','123','--json'],
    ['board','--json'],['queue','--json'],['account','--json'],
    ['profile','alpha','person','--json'],['location','search','alpha','cafe','--json'],
    ['ask','before-you-post','alpha','--topic','synthetic','--json'],
    ['topics','alpha','--json'],
    ['analytics-report','--project','alpha','--json'],['where','--project','alpha','word','--json'],
    ['topics','suggest','{queue}','--json'],['topics','decision','alpha','--json'],
    ['preview','{queue}','--json'],['lint','{queue}','--json'],['study-report','{study}','--json'],
])
def test_cli_reads_do_not_wait_or_call_core_when_leave_holds_exclusive(env,content,command,capsys):
    command=[x.replace('{queue}',str(content[0])).replace('{study}',str(content[1])) for x in command]
    with exclusive():
        start=time.monotonic();rc=cli.main(command);elapsed=time.monotonic()-start
    captured=capsys.readouterr();assert rc==2 and elapsed<1
    assert json.loads(captured.out)=={'error':'account_leaving','cannot_say':['account_leaving']}
    assert __import__('tests.conftest').conftest.is_refusal(captured.err, 'account_leaving')  # 3.1.2 §3.5・3.3.0 B3: 断りの理由行の後ろに打てる報告の 1 行


def test_dry_send_input_then_nonwaiting_lease(env,tmp_path,capsys):
    text=tmp_path/'text';text.write_text('synthetic body')
    with exclusive():
        start=time.monotonic();rc=cli.main(['send','alpha','--text-file',str(text)])
        assert time.monotonic()-start<1 and rc==2
    assert __import__('tests.conftest').conftest.is_refusal(capsys.readouterr().err, 'account_leaving')  # 3.1.2 §3.5・3.3.0 B3: 断りの理由行の後ろに打てる報告の 1 行
    assert not (env['root']/'state/alpha/sent').exists()


def test_shared_readers_coexist_and_exit_waits_for_them(env):
    entered=threading.Event();done=threading.Event()
    with gate.lease('alpha'):
        # Independent process shared acquisition succeeds while the first stays held.
        p=subprocess.run([sys.executable,'-c',"from thth.leave_gate import lease\nwith lease('alpha'):print('read')"],capture_output=True,text=True,timeout=2)
        assert p.returncode==0 and p.stdout.strip()=='read'
        def stopper():
            entered.set()
            with gate.lease('alpha',exclusive=True):done.set()
        worker=threading.Thread(target=stopper);worker.start();assert entered.wait(1);assert not done.wait(.05)
    worker.join(2);assert done.is_set()


@pytest.mark.parametrize('operation',['analytics_report','operations_handoff','study_report','queue'])
@pytest.mark.parametrize('via',['service','mcp'])
def test_pure_reports_take_lease_and_preserve_nonempty_success(env,content,monkeypatch,operation,via):
    args={'operation':operation,'account':'alpha'}
    if operation=='study_report':args={'operation':operation,'file':str(content[1])}
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS',str(env['path']));monkeypatch.setenv('THTH_REPORT_TOKEN',env['bearer'])
    server=_load_server_module()
    def call():
        if via=='mcp':return server.call_tool(operation if operation in ('analytics_report','operations_handoff','study_report') else 'thth_'+operation,{k:v for k,v in args.items() if k!='operation'})
        func=service.execute_mcp_report if operation=='study_report' else service.execute_report
        return func(env['context'],args)
    success=call();assert success
    if via=='mcp':assert not success.get('isError');success=json.loads(success['content'][0]['text'])
    if operation=='queue':assert len(success['drafts'])==1 and success['drafts'][0]['body'].strip()=='投稿本文'
    elif operation=='study_report':assert success['declaration']['id']=='synthetic-study'
    else:assert 'alpha' in (success.get('by_account') or success.get('reports'))
    with exclusive():
        start=time.monotonic()
        if via=='mcp':
            denied=call();assert denied.get('isError') and denied['content'][0]['text']=='account_leaving'
        else:
            with pytest.raises(service.ReportServiceError,match='^account_leaving$'):call()
        assert time.monotonic()-start<1


def test_real_http_returns_specific_503_without_wait(env,content,monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',env['connect'])
    with report_http.PrivateReportServer(env['path'],0) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with exclusive():
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
                start=time.monotonic();conn.request('POST','/report',json.dumps({'operation':'analytics_report','account':'alpha'}),{'Authorization':'Bearer '+env['bearer'],'Content-Type':'application/json'})
                response=conn.getresponse();payload=json.loads(response.read());conn.close()
                assert time.monotonic()-start<1 and response.status==503
                assert payload=={'error':'account_leaving','cannot_say':['account_leaving']}
        finally:server.shutdown();worker.join()


def test_excluded_scope_reason_only_while_owned_exclusive_is_live(env,content,monkeypatch):
    env['value']['credentials'][0]['accounts']['beta']='beta';env['path'].write_text(json.dumps(env['value']))
    with exclusive(stopped=True):
        context=report_http.load_credentials(env['path'])[1][0][3]
        assert dict(context.allowed_accounts)=={'beta':'beta'} and dict(context.excluded_accounts)=={'alpha':'alpha'}
        with pytest.raises(service.ReportServiceError,match='^account_leaving$'):service.execute_report(context,{'operation':'analytics_report','account':'alpha'})
        with pytest.raises(service.ReportServiceError,match='^scope_unavailable$'):service.execute_report(context,{'operation':'analytics_report','account':'foreign'})
        result=service.execute_report(context,{'operation':'operations_handoff','account':'beta'})
        assert set(result['reports'])=={'beta'} and 'excluded_accounts' not in json.dumps(result)
    with pytest.raises(service.ReportServiceError,match='^scope_unavailable$'):service.execute_report(context,{'operation':'analytics_report','account':'alpha'})
    with pytest.raises(TypeError):context.excluded_accounts['foreign']='foreign'


def test_real_cli_child_rejects_ex_under_deadline(env,content):
    with exclusive():
        try:
            result=subprocess.run([sys.executable,'-c',"from thth.cli import main;raise SystemExit(main(['queue','alpha','--json']))"],capture_output=True,text=True,timeout=2)
        except subprocess.TimeoutExpired:pytest.fail('reader waited for the exit drain')
    assert result.returncode==2 and json.loads(result.stdout)['cannot_say']==['account_leaving']
    assert __import__('tests.conftest').conftest.is_refusal(result.stderr, 'account_leaving')  # 3.1.2 §3.5・3.3.0 B3: 断りの理由行の後ろに打てる報告の 1 行


def test_report_holds_lease_through_calculation_and_release(env,monkeypatch):
    started=threading.Event();finished=threading.Event();worker=[]
    def calculate(name,**kwargs):
        def exiting():
            started.set()
            with gate.lease('alpha',exclusive=True):finished.set()
        thread=threading.Thread(target=exiting);thread.start();worker.append(thread)
        assert started.wait(1) and not finished.wait(.05)
        return {'n':1}
    monkeypatch.setattr(service.analytics_report,'answer',calculate)
    result=service.execute_report(env['context'],{'operation':'analytics_report','account':'alpha'})
    worker[0].join(2)
    assert result['reports']=={'alpha':{'n':1}} and finished.is_set()


def test_stopped_study_file_only_reports_live_busy_for_owned_repo(env,content):
    with exclusive(stopped=True):
        context=report_http.load_credentials(env['path'])[1][0][3]
        with pytest.raises(service.ReportServiceError,match='^account_leaving$'):
            service.execute_mcp_report(context,{'operation':'study_report','file':str(content[1])})
        with pytest.raises(service.ReportServiceError,match='^scope_unavailable$'):
            service.execute_mcp_report(context,{'operation':'study_report','file':str(env['root']/'secrets/beta.json')})
    with pytest.raises(service.ReportServiceError,match='^scope_unavailable$'):
        service.execute_mcp_report(context,{'operation':'study_report','file':str(content[1])})


def test_admin_historical_log_remains_available_during_exclusive(env):
    from thth import admin_log
    admin_log.append('account_updated','alpha',accounts.load_account('alpha'),by='operator',diff={'production':[False,True]})
    context=service.ReportContext({'alpha':'alpha'},scope='admin')
    with exclusive(stopped=True):
        result=service.execute_report(context,{'operation':'admin_log','account':'alpha'})
    assert len(result['events'])==1 and result['events'][0]['event']=='account_updated'


@pytest.mark.parametrize('kind',['fifo','symlink','nonempty'])
def test_read_lease_refuses_unsafe_coordination_file(env,kind,tmp_path,capsys):
    path=gate.location();path.mkdir(parents=True,mode=0o700);leaf=path/'alpha.lock'
    import os
    if kind=='fifo':os.mkfifo(leaf,0o600)
    elif kind=='symlink':leaf.symlink_to(tmp_path/'outside')
    else:leaf.write_bytes(b'not-empty');leaf.chmod(0o600)
    rc=cli.main(['queue','alpha','--json'])
    assert rc==2 and json.loads(capsys.readouterr().out)['cannot_say']==['account_stop_state_unreadable']


@pytest.mark.parametrize('command',[['after','--project','alpha','--json'],['where','--project','alpha','word','--json'],['analytics-report','--project','alpha','--json'],['handoff-report','--project','alpha','--json']])
def test_all_stopped_project_busy_is_not_successful_zero(env,content,command,capsys):
    with exclusive(stopped=True):
        start=time.monotonic();rc=cli.main(command)
        assert time.monotonic()-start<1 and rc==2
    assert json.loads(capsys.readouterr().out)['cannot_say']==['account_leaving']


def test_shared_project_keeps_active_b_and_empty_excluded_project_is_busy(env,content,capsys):
    cfg=env['root']/'accounts/beta.json';value=json.loads(cfg.read_text());value['project']='alpha';cfg.write_text(json.dumps(value))
    env['value']['credentials'][0]['accounts']['beta']='alpha';env['path'].write_text(json.dumps(env['value']))
    with exclusive(stopped=True):
        assert cli.main(['after','--project','alpha','--json'])==0
        assert set(json.loads(capsys.readouterr().out)['by_account'])=={'beta'}
        context=report_http.load_credentials(env['path'])[1][0][3]
        assert set(service.execute_report(context,{'operation':'analytics_report','project':'alpha'})['reports'])=={'beta'}
        only_a=service.ReportContext({},excluded_accounts={'alpha':'alpha'})
        with pytest.raises(service.ReportServiceError,match='^account_leaving$'):
            service.execute_report(only_a,{'operation':'analytics_report','project':'alpha'})


@pytest.mark.parametrize('via',['http','mcp'])
def test_stop_between_scope_selection_and_lease_keeps_busy_reason(env,content,monkeypatch,via):
    original=service._scoped_request
    with contextlib.ExitStack() as stack:
        def selection(*args):
            selected=original(*args)
            stack.enter_context(exclusive(stopped=True))
            return selected
        monkeypatch.setattr(service,'_scoped_request',selection)
        call=service.execute_report if via=='http' else service.execute_mcp_report
        with pytest.raises(service.ReportServiceError,match='^account_leaving$'):
            call(env['context'],{'operation':'analytics_report','account':'alpha'})
