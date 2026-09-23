import json
import secrets
from types import SimpleNamespace
import pytest
from thth import accounts,admin_log,leave,leave_gate as gate,approval_relay
from tests.test_v212_server_writes import env,draft


@pytest.fixture
def worker(monkeypatch):
    calls=[]
    def request(kind,account,operation,body):
        assert (kind,account,operation,body)==('account','alpha','revoke',{})
        calls.append(account);return {'status':'revoked'}
    monkeypatch.setattr(approval_relay,'signed_request',request)
    return calls


def test_manual_medium_complete_owned_only_with_durable_stop(env,worker):
    draft(env);btoken=(env['root']/'secrets/beta.json').read_bytes()
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='unconfirmed_manual'
    assert set(result['deleted'])=={'ledger','token','state','managed_repo'}
    for path in ('accounts/alpha.json','secrets/alpha.json','state/alpha','repos/_server/alpha'):
        assert not (env['root']/path).exists()
    assert (env['root']/'secrets/beta.json').read_bytes()==btoken and gate.stopped('alpha')
    assert accounts.load_account('beta') and accounts.load_token(accounts.load_account('beta'))
    events,broken=admin_log.read(account='alpha',event='account_removed')
    assert not broken and len(events)==1
    assert dict(zip(events[0]['diff']['deleted_categories'][1],events[0]['diff']['deleted_counts'][1]))==result['deleted']
    assert all(type(n) is int for n in events[0]['diff']['deleted_counts'][1])
    assert leave.run('alpha',by='operator')==result and worker==['alpha']
    assert len(admin_log.read(account='alpha',event='account_removed')[0])==1


def test_worker_failure_stays_stopped_with_retry_token(env,worker,monkeypatch):
    cfg=accounts.load_account('alpha');token=(env['root']/'secrets/alpha.json').read_bytes()
    monkeypatch.setattr(approval_relay,'signed_request',lambda *a:(_ for _ in ()).throw(approval_relay.RelayError('synthetic')))
    assert leave.command(SimpleNamespace(name='alpha',by='operator',json=True))==2
    assert gate.stopped('alpha') and (env['root']/'secrets/alpha.json').read_bytes()==token
    assert leave.read('alpha')['phase']=='stopped' and admin_log.read(event='account_removed')[0]==[]
    with pytest.raises(accounts.AccountStopped):accounts.load_account('alpha')


def test_remote_failure_retry_uses_same_token_then_deletes(env,worker,monkeypatch):
    path=env['root']/'accounts/alpha.json';cfg=json.loads(path.read_text());cfg['media']='mastodon';cfg['instance']='https://example.invalid';path.write_text(json.dumps(cfg));calls=[]
    def revoke(cfg,token,progress,save):
        calls.append(token['access_token'])
        if len(calls)==1:raise ValueError('synthetic provider rejection')
        return 'confirmed'
    monkeypatch.setattr(leave,'revoke',revoke)
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert leave.read('alpha')['phase']=='remote_pending' and (env['root']/'secrets/alpha.json').exists()
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and result['remote']=='confirmed' and calls[0]==calls[1] and worker==['alpha']


def test_shared_env_and_external_repo_preserved(env,worker,tmp_path):
    shared=env['root']/'secrets/shared.env';shared.write_text('SYNTHETIC='+secrets.token_hex(16));shared.chmod(0o600)
    external=tmp_path/'external';external.mkdir();(external/'source').write_text('keep')
    for name in ('alpha','beta'):
        p=env['root']/('accounts/'+name+'.json');cfg=json.loads(p.read_text());cfg.update(env=str(shared),repo_dir=str(external));p.write_text(json.dumps(cfg))
    before=shared.read_bytes();result=leave.run('alpha',by='operator')
    assert result['preserved']==['env_shared','external_repo'] and shared.read_bytes()==before and (external/'source').read_text()=='keep'


def test_cleanup_failure_resumes_without_second_revocation(env,worker,monkeypatch):
    draft(env);real=leave._delete;seen=[]
    def delete(target):
        if target['category']=='state' and not seen:seen.append(1);raise OSError('synthetic delete failure')
        return real(target)
    monkeypatch.setattr(leave,'_delete',delete)
    with pytest.raises(OSError):leave.run('alpha',by='operator')
    assert leave.read('alpha')['phase']=='deleting' and not (env['root']/'secrets/alpha.json').exists()
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and worker==['alpha']


@pytest.mark.parametrize('phase',['deleting','deleted_pending_log','completed'])
def test_retry_after_state_deleted_never_recreates_account_lock(env,worker,monkeypatch,phase):
    draft(env);real=leave._save;failed=[]
    def save(row):
        trigger=(row['phase']==phase and ('state' in row['deleted']))
        if trigger and not failed:
            failed.append(1)
            # completed exercises rename-success/directory-fsync-failure visibility.
            if phase=='completed':real(row)
            raise OSError('synthetic journal durability failure')
        real(row)
    monkeypatch.setattr(leave,'_save',save)
    with pytest.raises(OSError):leave.run('alpha',by='operator')
    assert not (env['root']/'state/alpha').exists()
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and not (env['root']/'state/alpha').exists()
    assert worker==['alpha'] and len(admin_log.read(account='alpha',event='account_removed')[0])==1


def test_existing_completed_requires_log_durability_before_success(env,worker,monkeypatch):
    leave.run('alpha',by='operator');real=leave.os.fsync;seen=[]
    def sync(fd):
        if admin_log._active_fd.get()==fd:
            seen.append(fd);raise OSError('synthetic audit fsync')
        return real(fd)
    monkeypatch.setattr(leave.os,'fsync',sync)
    with pytest.raises(OSError):leave.run('alpha',by='operator')
    assert seen and not (env['root']/'state/alpha').exists()


def test_credential_a_b_survives_a_removal_and_admin_retains_history(env,worker,monkeypatch):
    from thth import report_http,report_isolation,admin_report
    from thth.report_service import execute_report,execute_mcp_report
    item=env['value']['credentials'][0];item['accounts']['beta']='beta'
    admin=dict(item,sha256=secrets.token_hex(32),scope='admin',writes=False,accounts={})
    env['value']['credentials'].append(admin);env['path'].write_text(json.dumps(env['value']))
    old_user=report_http.load_credentials(env['path'])[1][0][3]
    leave.run('alpha',by='operator')
    root,credentials=report_http.load_credentials(env['path'])
    user=credentials[0][3];manager=credentials[1][3]
    assert dict(user.allowed_accounts)=={'beta':'beta'} and set(manager.allowed_accounts)=={'alpha','beta'}
    for context in (user,manager):report_isolation.validate_environment(root,context.allowed_accounts,allow_unreadable=context.scope=='admin',allow_empty=True)
    result=execute_report(user,dict(operation='operations_handoff',account='beta'))
    assert set(result['reports'])=={'beta'}
    removed=execute_report(manager,dict(operation='admin_account',account='alpha'))['by_account']['alpha']
    assert removed['ledger']=='removed' and removed['leave']['phase']=='completed'
    assert removed['last_change']['event']=='account_removed'
    log=execute_report(manager,dict(operation='admin_log',account='alpha'))
    assert len(log['events'])==1
    assert set(admin_report.answer('inventory')['by_account'])=={'alpha','beta'}
    load=accounts.load_account
    def scoped_load(name):
        assert name!='alpha', 'stopped ledger reached report calculation'
        return load(name)
    monkeypatch.setattr(accounts,'load_account',scoped_load)
    for context in (old_user,user):
        direct=execute_mcp_report(context,dict(operation='operations_handoff',account='beta'))
        assert set(direct['by_account'])=={'beta'}
        assert set(execute_report(context,dict(operation='operations_handoff',account='beta'))['reports'])=={'beta'}


def test_credential_only_removed_account_is_valid_but_has_no_effective_scope(env,worker):
    from thth import report_http,report_isolation
    leave.run('alpha',by='operator')
    root,credentials=report_http.load_credentials(env['path'])
    assert not credentials[0][3].allowed_accounts
    report_isolation.validate_environment(root,{},allow_empty=True)


def test_shared_manual_grant_does_not_tell_person_to_revoke_other_account(env,worker,monkeypatch,capsys):
    a=env['root']/'secrets/alpha.json';b=env['root']/'secrets/beta.json';b.write_bytes(a.read_bytes())
    assert leave.command(SimpleNamespace(name='alpha',by='operator',json=False))==0
    result=leave.read('alpha')
    # 3.1.2 件 6・裁定 09-23: 値だけの共有（別ファイル）なら自分の file は消し、相手の file は残す。
    assert result['remote']=='unconfirmed_shared' and not a.exists() and b.exists()
    out=capsys.readouterr().out
    assert '切り分け' in out and '本人が解除' not in out


def test_a_stop_does_not_invalidate_b_old_context_or_pending_job(env,monkeypatch):
    from thth import report_http,server_writes as writes,approval_jobs as jobs
    from tests.test_v212_server_writes import FakeRelay
    env['value']['credentials'][0]['accounts']['beta']='beta';env['path'].write_text(json.dumps(env['value']))
    context=report_http.load_credentials(env['path'])[1][0][3];remote=FakeRelay()
    def request(kind,subject,operation,body):
        if kind=='account':return {'status':'revoked'}
        return remote(kind,subject,operation,body)
    monkeypatch.setattr(approval_relay,'signed_request',request)
    draft_result=writes.execute(context,dict(operation='draft_put',account='beta',body='別 account の本文',publish_at='2030-01-01T12:00:00+09:00'))
    job=writes.execute(context,dict(operation='approval_request',account='beta',draft_id=draft_result['draft_id']))
    leave.run('alpha',by='operator');remote.approve();jobs.run_once(env['path'])
    assert jobs.status(context,'beta',job['job_id'])['status']=='completed'
    second=writes.execute(context,dict(operation='draft_put',account='beta',body='退出後の原稿',publish_at='2030-01-02T12:00:00+09:00'))
    assert second['draft_id']


def test_late_runs_and_maintain_do_not_recreate_removed_state(env,worker):
    from thth import runs,maintain,jst
    leave.run('alpha',by='operator')
    runs.record_minimal('alpha',dict(account='alpha',action='synthetic',status='ok',mode='read',error=None))
    maintain.run_maintain('alpha',log=lambda _:None)
    assert not (env['root']/'state/alpha').exists()


@pytest.mark.parametrize('field,value',[('phase',[]),('cfg',{}),('deleted',{'token':'1'}),('targets',[{'category':'state','path':'/tmp'}]),('token_shared','yes')])
def test_corrupt_journal_refuses_effect_and_preserves_bytes(env,worker,monkeypatch,field,value):
    real=leave.revoke
    monkeypatch.setattr(leave,'revoke',lambda *a:(_ for _ in ()).throw(ValueError('synthetic')))
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    p=gate.location()/'alpha.json';row=json.loads(p.read_text());row[field]=value;p.write_text(json.dumps(row));before=p.read_bytes();token=(env['root']/'secrets/alpha.json').read_bytes()
    monkeypatch.setattr(leave,'revoke',lambda *a:pytest.fail('provider reached'))
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert p.read_bytes()==before and (env['root']/'secrets/alpha.json').read_bytes()==token


@pytest.mark.parametrize('target',['accounts/beta.json','state/_admin/private.json','state/_leave/private.json','state/beta/unreferenced.json','secrets/unowned.json'])
def test_root_containment_alone_does_not_prove_credential_ownership(env,worker,target):
    path=env['root']/target
    if not path.exists():path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
    path.chmod(0o600);original=path.read_bytes()
    ledger=env['root']/'accounts/alpha.json';cfg=json.loads(ledger.read_text());cfg['token']=str(path);ledger.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert path.read_bytes()==original and worker==[] and (env['root']/'accounts/alpha.json').exists()


@pytest.mark.parametrize('global_name',['THTH_APPS_DIR','THTH_APP_ENV_PATH'])
@pytest.mark.parametrize('tree',['state/alpha','repos/_server/alpha'])
def test_owned_tree_cannot_contain_global_client_configuration(env,worker,monkeypatch,global_name,tree):
    draft(env);path=env['root']/tree/'global-credential'
    monkeypatch.setenv(global_name,str(path))
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert worker==[] and (env['root']/'secrets/alpha.json').exists()


@pytest.mark.parametrize('mode',['zero','partial','fsync'])
def test_removed_event_failure_retains_stop_and_retry_appends_without_rewriting_prefix(env,worker,monkeypatch,mode):
    real=admin_log._emit;failed=[]
    def emit(fd,data):
        if b'account_removed' not in data or failed:return real(fd,data)
        failed.append(1)
        if mode=='zero':raise admin_log.AdminLogError('synthetic',appended=False)
        import os
        os.write(fd,data[:len(data)//2] if mode=='partial' else data)
        raise admin_log.AdminLogError('synthetic',appended=True,complete=mode=='fsync')
    monkeypatch.setattr(admin_log,'_emit',emit)
    with pytest.raises(admin_log.AdminLogError):leave.run('alpha',by='operator')
    path=env['root']/'state/_admin/accounts.ndjson';prefix=path.read_bytes()
    assert leave.read('alpha')['phase']=='deleted_pending_log' and not (env['root']/'state/alpha').exists()
    result=leave.run('alpha',by='operator');rows,broken=admin_log.read(account='alpha',event='account_removed')
    assert result['phase']=='completed' and len(rows)==1 and broken==int(mode=='partial')
    assert path.read_bytes().startswith(prefix) and worker==['alpha']
    assert not (env['root']/'state/alpha').exists()


@pytest.mark.parametrize('other',['alpha2','alpha-other','alpha.foo'])
def test_secret_leaf_namespace_does_not_overlap_other_account_name(env,worker,other):
    base=json.loads((env['root']/'accounts/beta.json').read_text());base['account']=other
    (env['root']/('accounts/'+other+'.json')).write_text(json.dumps(base))
    token=env['root']/('secrets/'+other+'.json');token.write_text('{}');token.chmod(0o600)
    p=env['root']/'accounts/alpha.json';cfg=json.loads(p.read_text());cfg['token']=str(token);p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):leave.run('alpha',by='operator')
    assert token.read_text()=='{}' and worker==[]


def test_account_without_repo_can_leave_without_inventing_a_repo(env,worker):
    p=env['root']/'accounts/alpha.json';cfg=json.loads(p.read_text());cfg['repo_dir']=None;p.write_text(json.dumps(cfg))
    result=leave.run('alpha',by='operator')
    assert result['phase']=='completed' and 'managed_repo' not in result['deleted']
    assert not (env['root']/'repos').exists()
