"""Server writes use dynamic fake credentials, loopback transports and local Git."""
import contextlib
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
import pytest
from thth import accounts, approval_jobs as jobs, approval_relay as relay, core, report_http, server_writes as writes
from thth.report_service import ReportServiceError, execute_report


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir(mode=0o700);(root/'accounts').mkdir();(root/'secrets').mkdir(mode=0o700)
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('HOME',str(home));monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    for key in ('THTH_REPORT_CREDENTIALS','THTH_REPORT_TOKEN','THTH_APP_DIR','THTH_APPS_DIR'):monkeypatch.delenv(key,raising=False)
    for name in ('alpha','beta'):
        token=root/'secrets'/(name+'.json');token.write_text(json.dumps({'access_token':secrets.token_urlsafe(32),'scopes':['threads_basic','threads_content_publish','threads_delete']}));token.chmod(0o600)
        cfg={key:None for key in accounts.REQUIRED_FIELDS}
        cfg.update(account=name,project=name,media='threads',handle='demo',repo_dir=str(root/'repos/_server'/name),queue_dir='queue',replies_dir='replies',token=str(token),production=True,hashtags=True,char_limit=500,
                   # 承認 job の道を確かめる試験（設計 3.12.0 で既定は none になった）。
                   approval='all')
        (root/'accounts'/(name+'.json')).write_text(json.dumps(cfg))
    bearer=secrets.token_urlsafe(32);config=tmp_path/'credentials.json'
    value=dict(schema_version=1,root=str(root),credentials=[dict(sha256=hashlib.sha256(bearer.encode()).hexdigest(),expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),revoked=False,accounts={'alpha':'alpha'},writes=True,actor='person')])
    config.write_text(json.dumps(value));config.chmod(0o600)
    connect=socket.socket.connect
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('external network'))
    return dict(root=root,path=config,value=value,bearer=bearer,connect=connect,context=report_http.load_credentials(config)[1][0][3])


def save_config(env):env['path'].write_text(json.dumps(env['value']))

def draft(env,**values):
    request=dict(operation='draft_put',account='alpha',body='投稿本文',publish_at='2030-01-01T12:00:00+09:00',**values)
    return writes.execute(env['context'],request)


class FakeRelay:
    def __init__(self):self.rows={};self.generation=secrets.token_urlsafe(32);self.consumes=0
    def __call__(self,kind,subject,operation,body):
        if kind=='person':return {'active':True,'locked':False,'generation':self.generation}
        if operation=='create':
            self.rows[subject]=dict(body,status='pending',expires_at=int(time.time()*1000)+600000)
            return {k:self.rows[subject][k] for k in ('status','expires_at')}
        row=self.rows[subject]
        if operation=='status':return {k:row[k] for k in ('status','expires_at')}
        if operation=='consume':
            assert row['status']=='approved';row['status']='consumed';self.consumes+=1
            return dict(job_id=row['job_id'],digest=row['digest'],account=row['account'],kind=row['kind'],approver=row['person'],generation=self.generation,approved_at=int(time.time()*1000),expires_at=row['expires_at'])
    def approve(self):
        for row in self.rows.values():row['status']='approved'


@pytest.fixture
def remote(monkeypatch):
    remote=FakeRelay();monkeypatch.setattr(relay,'signed_request',remote);return remote


def test_managed_draft_git_roundtrip_update_and_no_implicit_approval(env):
    first=draft(env)
    rows=execute_report(env['context'],dict(operation='draft_list',account='alpha'))['drafts']
    assert len(rows)==1 and rows[0]['body'].strip()=='投稿本文' and rows[0]['verified'] and rows[0]['status']=='draft'
    second=draft(env,draft_id=first['draft_id'],expected_revision=first['revision'])
    assert second['draft_id']==first['draft_id']
    with pytest.raises(ReportServiceError):draft(env,draft_id=first['draft_id'],expected_revision='0'*64)
    repo=env['root']/'repos/_server/alpha';bare=env['root']/'state/alpha/git-origin.git'
    assert subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD']).strip()==subprocess.check_output(['git','--git-dir',str(bare),'rev-parse','main']).strip()
    assert b'by=person via=http' in subprocess.check_output(['git','-C',str(repo),'log','-1','--format=%B'])
    assert 'approved_sha' not in next((repo/'queue').glob('*.md')).read_text()


def test_human_approval_worker_updates_same_frontmatter_and_status(env,remote):
    one=draft(env);result=writes.execute(env['context'],dict(operation='approval_request',account='alpha',draft_id=one['draft_id']))
    assert result['approval_url'].startswith('https://thth.me/approve/') and len(result['digest'])==64
    jobs.run_once(env['path']);assert jobs.status(env['context'],'alpha',result['job_id'])['status']=='pending'
    remote.approve();jobs.run_once(env['path'])
    assert jobs.status(env['context'],'alpha',result['job_id'])['status']=='completed'
    rows=execute_report(env['context'],dict(operation='queue',account='alpha'))['drafts'];assert rows[0]['status']=='approved' and rows[0]['verified']
    jobs.run_once(env['path']);assert remote.consumes==1
    observed=json.dumps(jobs.status(env['context'],'alpha',result['job_id']))
    assert result['approval_url'].split('/')[-1] not in observed and result['text'] not in observed


@pytest.mark.parametrize('phase',['registering','consuming','executing'])
def test_interrupted_intent_never_replays(env,remote,phase):
    one=draft(env);result=writes.execute(env['context'],dict(operation='approval_request',account='alpha',draft_id=one['draft_id']))
    path=jobs.directory('alpha')/(result['job_id']+'.json');value=json.loads(path.read_text());value['status']=phase;path.write_text(json.dumps(value));remote.approve()
    jobs.run_once(env['path']);jobs.run_once(env['path'])
    assert jobs.status(env['context'],'alpha',result['job_id'])['status']=='unknown' and remote.consumes==0


@pytest.mark.parametrize('change',['revoked','actor','writes','accounts','ledger','token','body'])
def test_pending_binding_changes_cannot_approve(env,remote,change):
    one=draft(env);result=writes.execute(env['context'],dict(operation='approval_request',account='alpha',draft_id=one['draft_id']))
    if change in ('revoked','actor','writes','accounts'):
        env['value']['credentials'][0][change]={'revoked':True,'actor':'different','writes':False,'accounts':{'beta':'beta'}}[change];save_config(env)
    elif change=='ledger':
        p=env['root']/'accounts/alpha.json';value=json.loads(p.read_text());value['handle']='changed';p.write_text(json.dumps(value))
    elif change=='token':
        p=env['root']/'secrets/alpha.json';value=json.loads(p.read_text());value['access_token']=secrets.token_urlsafe(32);p.write_text(json.dumps(value))
    else:
        p=next((env['root']/'repos/_server/alpha/queue').glob('*.md'));p.write_text(p.read_text().replace('投稿本文','別の本文'))
    remote.approve();jobs.run_once(env['path'])
    assert jobs.status(env['context'],'alpha',result['job_id'])['status'] in ('failed','unknown')
    assert 'approved_sha' not in next((env['root']/'repos/_server/alpha/queue').glob('*.md')).read_text()


@pytest.mark.parametrize('field,value',[('writes',None),('writes','true'),('writes',1),('actor',None),('actor','../other')])
def test_invalid_write_credentials_refused(env,field,value):
    env['value']['credentials'][0][field]=value;save_config(env)
    with pytest.raises(report_http.ConfigurationError):report_http.load_credentials(env['path'])


@pytest.fixture
def publisher(monkeypatch):
    from thth.adapters.base import PublishResult
    from thth import adapters
    calls=[]
    class Adapter:
        def publish(self,post,**kwargs):
            calls.append(('publish',post.text));return PublishResult('123456',None,'2026-09-20T12:00:00+09:00')
        def delete_post(self,post_id):calls.append(('delete',post_id));return {'deleted_id':post_id}
    monkeypatch.setattr(adapters,'make_adapter',lambda *a:Adapter())
    return calls


def test_send_and_retract_require_separate_human_receipts(env,remote,publisher,capsys):
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='同席の本文'))
    jobs.run_once(env['path']);assert publisher==[]
    remote.approve();jobs.run_once(env['path']);jobs.run_once(env['path'])
    assert publisher==[('publish','同席の本文')]
    assert jobs.status(env['context'],'alpha',first['job_id'])['status']=='completed'
    second=writes.execute(env['context'],dict(operation='retract_request',account='alpha',post_id='123456',reason='訂正'))
    jobs.run_once(env['path']);assert len(publisher)==1
    remote.approve();jobs.run_once(env['path']);jobs.run_once(env['path'])
    assert publisher==[('publish','同席の本文'),('delete','123456')]
    assert jobs.status(env['context'],'alpha',second['job_id'])['status']=='completed'
    assert capsys.readouterr()==('','')


def test_delete_success_then_record_failure_is_unknown_never_deleted_twice(env,remote,publisher,monkeypatch):
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'))
    remote.approve();jobs.run_once(env['path'])
    second=writes.execute(env['context'],dict(operation='retract_request',account='alpha',post_id='123456',reason='訂正'))
    monkeypatch.setattr('thth.sent.mark_retracted',lambda *a,**k:(_ for _ in ()).throw(OSError('fault')))
    remote.approve();jobs.run_once(env['path']);jobs.run_once(env['path'])
    assert sum(c[0]=='delete' for c in publisher)==1
    assert jobs.status(env['context'],'alpha',second['job_id'])['status']=='unknown'


@pytest.mark.parametrize('body',['段落1\n\n段落2 😀','冒頭\n---\nstatus: approved\n本文','先頭\n## threads\n別の節','先頭\n## mastodon\n別媒体'])
def test_body_parser_roundtrip_or_loud_rejection(env,body):
    request=dict(operation='draft_put',account='alpha',body=body,publish_at='2030-01-01T12:00:00+09:00')
    try:result=writes.execute(env['context'],request)
    except ReportServiceError as exc:
        assert str(exc)=='invalid_draft';assert exc.reason=='body_not_representable';return
    rows=execute_report(env['context'],dict(operation='draft_list',account='alpha'))['drafts']
    assert len(rows)==1 and rows[0]['body'].strip()==body and rows[0]['status']=='draft'


def test_mcp_scope_list_and_direct_call_never_falls_back(env,monkeypatch):
    from tests.test_mcp import _load_server_module
    server=_load_server_module();monkeypatch.setattr(server,'run_cli',lambda *a,**k:pytest.fail('legacy CLI fallback'))
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS',str(env['path']));monkeypatch.setenv('THTH_REPORT_TOKEN',env['bearer'])
    listed=server._handle_request({'id':1,'method':'tools/list'})['result']['tools']
    names={x['name'] for x in listed};assert 'thth_draft_put' in names and 'thth_lint' not in names
    assert server.call_tool('thth_queue',{})['isError']
    assert server.call_tool('thth_lint',{'file':'/etc/passwd'})['isError']
    for field in ('operation','actor','by','person','confirm','digest','file'):
        assert server.call_tool('thth_send_request',dict(account='alpha',body='本文',**{field:'evil'}))['isError']
    assert server.call_tool('thth_queue',{'account':'beta'})['isError']
    env['value']['credentials'][0]['revoked']=True;save_config(env)
    assert server._handle_request({'id':2,'method':'tools/list'})['result']['tools']==[]
    assert server.call_tool('thth_queue',{'account':'alpha'})['isError']
    monkeypatch.setenv('THTH_REPORT_TOKEN','')
    assert server.call_tool('thth_queue',{'account':'alpha'})['isError']


def test_readonly_and_admin_credentials_never_list_or_call_writes(env,monkeypatch):
    from tests.test_mcp import _load_server_module
    from thth.server_writes import WRITE_OPERATIONS
    server=_load_server_module();monkeypatch.setenv('THTH_REPORT_CREDENTIALS',str(env['path']));monkeypatch.setenv('THTH_REPORT_TOKEN',env['bearer'])
    write_tools={'thth_'+operation for operation in WRITE_OPERATIONS}
    read_reports={'analytics_report','operations_handoff','study_report'}
    for scope in ('user','admin'):
        env['value']['credentials'][0].update(writes=False,scope=scope);save_config(env)
        names={x['name'] for x in server._handle_request({'id':1,'method':'tools/list'})['result']['tools']}
        assert read_reports <= names and names.isdisjoint(write_tools)
        for tool in write_tools:
            denied=server.call_tool(tool,{'account':'alpha','body':'本文'})
            assert denied['isError'] and denied['content'][0]['text']=='unsupported_operation'
        if scope=='admin':
            assert names=={x['name'] for x in server.ADMIN_TOOLS}|read_reports


def test_actual_http_short_request_and_scoped_status(env,remote,monkeypatch):
    def connect(sock,address):
        assert address[0]=='127.0.0.1';return env['connect'](sock,address)
    monkeypatch.setattr(socket.socket,'connect',connect)
    with report_http.PrivateReportServer(env['path'],0) as server:
        thread=threading.Thread(target=server.serve_forever);thread.start()
        def call(target,data):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request('POST',target,json.dumps(data),{'Authorization':'Bearer '+env['bearer'],'Content-Type':'application/json'})
            response=connection.getresponse();result=response.status,json.loads(response.read());connection.close();return result
        try:
            start=time.monotonic();code,data=call('/write',dict(operation='send_request',account='alpha',body='HTTP本文'))
            assert code==200 and time.monotonic()-start<3 and data['status']=='pending' and remote.consumes==0
            status=call('/report',dict(operation='request_status',account='alpha',job_id=data['job_id']))
            assert status[0]==200 and status[1]['status']=='pending'
            assert data['approval_url'].split('/')[-1] not in json.dumps(status)
            assert call('/write',dict(operation='send_request',account='beta',body='他人'))[0]==400
            assert call('/write',dict(operation='send_request',account='alpha',body='本文',actor='other'))[0]!=200
            assert call('/write',dict(operation='draft_put',account='alpha',body='a\n## mastodon\nb',publish_at='2030-01-01T12:00:00+09:00'))== (400,{'error':'invalid_draft','reason':'body_not_representable'})
        finally:server.shutdown();thread.join(3)


def test_actual_stdio_server_scopes_and_json_stdout(env):
    source=Path(__file__).resolve().parents[1]
    requests=[{'id':1,'method':'tools/call','params':{'name':'thth_draft_put','arguments':dict(account='alpha',body='stdio本文',publish_at='2030-01-01T12:00:00+09:00')}},
              {'id':2,'method':'tools/call','params':{'name':'thth_queue','arguments':{'account':'alpha'}}},
              {'id':3,'method':'tools/call','params':{'name':'thth_queue','arguments':{'account':'beta'}}},
              {'id':4,'method':'tools/call','params':{'name':'thth_lint','arguments':{'file':'/etc/passwd'}}}]
    import sys
    result=subprocess.run([sys.executable,str(source/'mcp/server.py')],input='\n'.join(json.dumps(r) for r in requests)+'\n',text=True,capture_output=True,
          env={**os.environ,'PYTHONPATH':str(source),'THTH_REPORT_CREDENTIALS':str(env['path']),'THTH_REPORT_TOKEN':env['bearer']},timeout=15)
    assert result.returncode==0 and result.stderr==''
    rows=[json.loads(row)['result'] for row in result.stdout.splitlines()]
    assert len(rows)==4 and not rows[0].get('isError')
    queue=json.loads(rows[1]['content'][0]['text']);assert queue['drafts'][0]['body'].strip()=='stdio本文'
    assert rows[2]['isError'] and rows[3]['isError']
    assert env['bearer'] not in result.stdout and str(env['root']) not in result.stdout


@pytest.mark.parametrize('what',['ledger','token','credential'])
def test_change_after_account_lock_before_execution_refused(env,remote,publisher,monkeypatch,what):
    from thth import server_files
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'))
    original=server_files.account_locks
    @contextlib.contextmanager
    def swapped(account,cfg):
        with original(account,cfg):
            if what=='ledger':
                path=env['root']/'accounts/alpha.json';value=json.loads(path.read_text());value['production']=False;path.write_text(json.dumps(value))
            elif what=='token':
                path=env['root']/'secrets/alpha.json';value=json.loads(path.read_text());value['access_token']=secrets.token_urlsafe(32);path.write_text(json.dumps(value))
            else:env['value']['credentials'][0]['revoked']=True;save_config(env)
            yield
    monkeypatch.setattr(server_files,'account_locks',swapped);remote.approve();jobs.run_once(env['path'])
    assert publisher==[] and jobs.status(env['context'],'alpha',first['job_id'])['status']=='failed'


def test_ready_lock_contention_resumes_once_and_rechecks_person(env,remote,publisher):
    from thth import server_files
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'));remote.approve()
    with server_files.account_locks('alpha',accounts.load_account('alpha')):
        jobs.run_once(env['path'])
        assert jobs.status(env['context'],'alpha',first['job_id'])['status']=='ready' and publisher==[]
    jobs.run_once(env['path']);jobs.run_once(env['path']);assert len(publisher)==1 and remote.consumes==1


def test_two_workers_cannot_consume_or_execute_one_job_twice(env,remote,publisher):
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'));remote.approve()
    barrier=threading.Barrier(3)
    def worker():barrier.wait();jobs.run_once(env['path'])
    workers=[threading.Thread(target=worker) for _ in range(2)]
    for worker in workers:worker.start()
    barrier.wait()
    for worker in workers:worker.join(5);assert not worker.is_alive()
    assert len(publisher)==1 and remote.consumes==1 and jobs.status(env['context'],'alpha',first['job_id'])['status']=='completed'


@pytest.mark.parametrize('phase',['consuming','executing'])
def test_journal_fsync_failure_never_replays(env,remote,publisher,monkeypatch,phase):
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'));remote.approve()
    original=jobs._save;seen=[]
    def fault(fd,job):
        original(fd,job)
        if job['status']==phase and not seen:seen.append(1);raise OSError('fsync fault')
    monkeypatch.setattr(jobs,'_save',fault)
    jobs.run_once(env['path']);jobs.run_once(env['path'])
    assert seen and publisher==[] and jobs.status(env['context'],'alpha',first['job_id'])['status']=='unknown'


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo'])
def test_draft_leaf_is_never_followed_or_overwritten(env,kind):
    one=draft(env);path=next((env['root']/'repos/_server/alpha/queue').glob('*.md'));old=path.read_bytes();path.unlink()
    target=env['root']/'secrets/target';target.write_bytes(old);target.chmod(0o600)
    if kind=='symlink':path.symlink_to(target)
    elif kind=='hardlink':os.link(target,path)
    else:os.mkfifo(path,0o600)
    with pytest.raises((ReportServiceError,ValueError)):
        draft(env,draft_id=one['draft_id'],expected_revision=one['revision'])
    assert target.read_bytes()==old


def test_managed_git_ignores_host_hooks_and_refuses_remote_repoint(env,tmp_path,monkeypatch):
    hook=tmp_path/'hooks';hook.mkdir();marker=tmp_path/'hook-ran'
    script=hook/'post-commit';script.write_text('#!/bin/sh\ntouch '+str(marker)+'\n');script.chmod(0o700)
    config=tmp_path/'gitconfig';config.write_text('[core]\n hooksPath = '+str(hook)+'\n[commit]\n gpgsign = true\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(config));monkeypatch.setenv('GIT_CONFIG_COUNT','1');monkeypatch.setenv('GIT_CONFIG_KEY_0','core.hooksPath');monkeypatch.setenv('GIT_CONFIG_VALUE_0',str(hook))
    one=draft(env);assert not marker.exists()
    repo=env['root']/'repos/_server/alpha';gitconfig=repo/'.git/config';before=next((repo/'queue').glob('*.md')).read_bytes()
    gitconfig.write_text(gitconfig.read_text().replace(str(env['root']/'state/alpha/git-origin.git'),'https://external.invalid/repo'))
    with pytest.raises((ReportServiceError,ValueError)):draft(env,draft_id=one['draft_id'],expected_revision=one['revision'])
    assert next((repo/'queue').glob('*.md')).read_bytes()==before and not marker.exists()


def test_two_processes_claim_one_job_once(env,remote,monkeypatch):
    import multiprocessing
    from thth import adapters
    from thth.adapters.base import PublishResult
    ctx=multiprocessing.get_context('fork');consumes=ctx.Value('i',0);publishes=ctx.Value('i',0)
    class Adapter:
        def publish(self,post,**kwargs):
            with publishes.get_lock():publishes.value+=1
            return PublishResult('123456',None,'2026-09-20T12:00:00+09:00')
    monkeypatch.setattr(adapters,'make_adapter',lambda *a:Adapter())
    def request(*args):
        if args[2]=='consume':
            with consumes.get_lock():consumes.value+=1
        return remote(*args)
    monkeypatch.setattr(relay,'signed_request',request)
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='並行公開'));remote.approve()
    barrier=ctx.Barrier(3)
    def worker():barrier.wait();jobs.run_once(env['path'])
    workers=[ctx.Process(target=worker) for _ in range(2)]
    for worker in workers:worker.start()
    barrier.wait()
    for worker in workers:worker.join(15);assert worker.exitcode==0
    assert consumes.value==1 and publishes.value==1 and jobs.status(env['context'],'alpha',first['job_id'])['status']=='completed'


@pytest.mark.parametrize('fault',['zero','partial','fsync'])
def test_request_audit_fault_precedes_remote_registration(env,remote,monkeypatch,fault):
    from thth import admin_log
    def emit(fd,data):
        if fault!='zero':os.write(fd,data if fault=='fsync' else data[:len(data)//2])
        raise admin_log.AdminLogError('fault',appended=fault!='zero',complete=fault=='fsync')
    monkeypatch.setattr(admin_log,'_emit',emit)
    with pytest.raises(ReportServiceError):writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'))
    assert remote.rows=={} and remote.consumes==0
    jobs.run_once(env['path'])
    rows=[json.loads(p.read_text()) for p in jobs.directory('alpha').glob('*.json')]
    assert len(rows)==1 and rows[0]['status']=='unknown'


def test_temporary_poll_failure_keeps_original_deadline_then_timeout(env,remote,monkeypatch):
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'))
    monkeypatch.setattr(relay,'signed_request',lambda *a:(_ for _ in ()).throw(relay.RelayError('approval_relay_outcome_unknown',status=503)))
    jobs.run_once(env['path']);assert jobs.status(env['context'],'alpha',first['job_id'])['status']=='pending'
    monkeypatch.setattr(jobs.time,'time',lambda:first['expires_at']/1000)
    jobs.run_once(env['path']);status=jobs.status(env['context'],'alpha',first['job_id'])
    assert status['status']=='expired' and status['reason']=='approval_timeout' and remote.consumes==0


def test_explicit_managed_configuration_never_repoints_existing_project(env):
    ledger=env['root']/'accounts/alpha.json';value=json.loads(ledger.read_text());repo=env['root']/'repos/existing';repo.mkdir(parents=True)
    sentinel=repo/'original.md';sentinel.write_text('existing project');value['repo_dir']=str(repo);ledger.write_text(json.dumps(value))
    with pytest.raises(ReportServiceError,match='managed_repo_required'):draft(env)
    assert sentinel.read_text()=='existing project' and not (repo/'.git').exists()


def test_documented_account_add_with_managed_repo_reaches_first_draft(env):
    from thth import cli
    repo=env['root']/'repos/_server/gamma-threads'
    assert cli.main(['account','add','gamma-threads','--media','threads','--handle','gamma','--project','gamma','--repo-dir',str(repo),'--by','operator'])==0
    cfg=accounts.load_account('gamma-threads');assert cfg['queue_dir']=='docs/sns/queue' and cfg['repo_dir']==str(repo)
    # Existing private-report deployment requires token/env inside its isolated root.
    ledger=env['root']/'accounts/gamma-threads.json';value=json.loads(ledger.read_text())
    value.update(token=str(env['root']/'secrets/gamma-threads.token'),env=str(env['root']/'secrets/gamma-threads.env'))
    ledger.write_text(json.dumps(value))
    token=Path(value['token']);token.write_text(json.dumps({'access_token':secrets.token_urlsafe(32),'auth_via':'paste'}));token.chmod(0o600)
    env['value']['credentials'][0]['accounts']={'gamma-threads':'gamma'};save_config(env)
    context=report_http.load_credentials(env['path'])[1][0][3]
    result=writes.execute(context,dict(operation='draft_put',account='gamma-threads',body='招待された本文',publish_at='2030-01-01T12:00:00+09:00'))
    rows=execute_report(context,dict(operation='draft_list',account='gamma-threads'))['drafts']
    assert rows[0]['draft_id']==result['draft_id'] and rows[0]['verified'] and (repo/'docs/sns/queue').is_dir()



def test_committed_draft_change_after_receipt_never_approves_new_body(env,remote):
    from thth import managed_repo
    first=draft(env);result=writes.execute(env['context'],dict(operation='approval_request',account='alpha',draft_id=first['draft_id']))
    repo=env['root']/'repos/_server/alpha';path=next((repo/'queue').glob('*.md'))
    path.write_text(path.read_text().replace('投稿本文','変更した本文'))
    assert managed_repo.run(repo,['add','queue']).returncode==0
    assert managed_repo.run(repo,['commit','-m','Concurrent draft edit']).returncode==0
    assert managed_repo.run(repo,['push']).returncode==0
    remote.approve();jobs.run_once(env['path'])
    assert jobs.status(env['context'],'alpha',result['job_id'])['status']=='failed'
    assert 'status: draft' in path.read_text() and 'approved_sha:' not in path.read_text()


def test_person_rotation_after_receipt_before_execute_refuses(env,remote,publisher):
    from thth import server_files
    result=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'));remote.approve()
    with server_files.account_locks('alpha',accounts.load_account('alpha')):jobs.run_once(env['path'])
    assert jobs.status(env['context'],'alpha',result['job_id'])['status']=='ready'
    remote.generation=secrets.token_urlsafe(32);jobs.run_once(env['path'])
    assert publisher==[] and jobs.status(env['context'],'alpha',result['job_id'])['status']=='failed'


def test_poll_wait_holds_no_repo_account_or_global_admin_lock(env,remote,monkeypatch):
    from thth import server_files
    import sys
    first=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='本文'))
    cfg=accounts.load_account('alpha')
    with server_files.account_locks('alpha',cfg):pass
    paths=[accounts.repo_lock_path_for(cfg['repo_dir']),accounts.account_lock_path_for('alpha'),str(env['root']/'state/_admin/accounts.ndjson')]
    checks=[]
    def request(*args):
        if args[2]=='status':
            proc=subprocess.run([sys.executable,'-c','import fcntl,os,sys; files=[os.open(p,os.O_RDONLY) for p in sys.argv[1:]]; [fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB) for f in files]',*paths],capture_output=True,timeout=3)
            assert proc.returncode==0;checks.append(1)
        return remote(*args)
    monkeypatch.setattr(relay,'signed_request',request);jobs.run_once(env['path'])
    assert checks==[1] and jobs.status(env['context'],'alpha',first['job_id'])['status']=='pending'


def test_secrets_and_approval_capabilities_only_in_dedicated_url(env,remote,publisher,capsys):
    result=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='私的な公開本文'))
    token=result['approval_url'].split('/')[-1]
    path=jobs.directory('alpha')/(result['job_id']+'.json');private=json.loads(path.read_text());assert path.stat().st_mode & 0o777==0o600
    remote.approve();jobs.run_once(env['path'])
    status=jobs.status(env['context'],'alpha',result['job_id'])
    safe=dict(result);safe.pop('approval_url')
    logs=[p.read_text() for p in (env['root']/'state').rglob('*.ndjson')]
    output=capsys.readouterr();surface=json.dumps([safe,status,logs,output.out,output.err],ensure_ascii=False)
    credential=json.loads((env['root']/'secrets/alpha.json').read_text())['access_token']
    assert all(value not in surface for value in (token,private['read_key'],credential,env['bearer']))
    assert '私的な公開本文' not in ''.join(logs)+json.dumps(status)
