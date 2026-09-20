"""App presence events and authorization/probe observations use dynamic fakes only."""
import datetime
import io
import json
from pathlib import Path
import stat

import pytest
from thth import accounts,admin_log,admin_report,appconfig,appenv,authclients,authflow,cli,doctor,handoff_cursor,jst,oauth,scopes
from thth.adapters import auth_x
from tests.test_v211_authflow import env,opaque,no_lock,url


def cfg_media(env,media):
    cfg=dict(env['cfg'],media=media)
    env['ledger'].write_text(json.dumps(cfg));env['cfg']=accounts.load_account('alpha')
    return env['cfg']


def put_token(env,**values):
    token=dict(access_token=opaque(),username='demo',user_id='123',obtained_at=jst.iso(),auth_via='paste',scopes=None,scopes_source='unknown',**values)
    Path(env['cfg']['token']).write_text(json.dumps(token));Path(env['cfg']['token']).chmod(0o600)
    return token


def probe(ok=True,failure=None):
    return {'probes':[dict(key='search',permission='read:search',ok=ok,failure=failure)],'error':None}


def emit_fault(monkeypatch,fault):
    write=admin_log.os.write
    def emit(fd,data):
        if fault=='zero':raise admin_log.AdminLogError('fault')
        write(fd,data if fault=='fsync' else data[:len(data)//2])
        raise admin_log.AdminLogError('fault',appended=True,complete=fault=='fsync')
    monkeypatch.setattr(admin_log,'_emit',emit)


@pytest.mark.parametrize('media',['threads','x'])
def test_app_json_cli_reads_same_profile_store_presence_only(env,monkeypatch,capsys,media):
    values={'client_id':opaque(),'client_secret':opaque()}
    if media=='x':values.update(client_type='confidential',redirect_uri=auth_x.CALLBACK)
    monkeypatch.setattr('sys.stdin',io.StringIO(json.dumps(values)))
    assert cli.main(['app','set',media,'--stdin','--by','operator'])==0
    path=env['app'] if media=='threads' else authclients.path_for('x',None,{})
    if media=='threads':assert appenv.load_app_env()==(values['client_id'],values['client_secret'])
    else:
        cfg=cfg_media(env,'x');profile=auth_x.XAuthProfile.prepare(cfg)
        assert (profile.client_id,profile.client_secret)==(values['client_id'],values['client_secret'])
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    rows,broken=admin_log.read();assert not broken and len(rows)==1
    assert rows[0]['event']=='app_set' and rows[0]['account']=='app-'+media and rows[0]['by']=='operator'
    assert rows[0]['diff']=={key:['present' if media=='threads' else 'absent','present'] for key in ('client_id','client_secret')}
    output=capsys.readouterr().out+json.dumps(rows)
    assert all(v not in output for v in [values['client_id'],values['client_secret']])


@pytest.mark.parametrize('legacy',[False,True])
def test_app_by_before_any_read_input_or_write(env,legacy):
    before={str(p):p.read_bytes() for p in env['root'].rglob('*') if p.is_file()}
    reader=lambda:pytest.fail('input before actor')
    args=dict(input_func=reader,log=lambda _:None)
    assert (appenv.run_app_set(app_id='id',**args) if legacy else appconfig.run('x',stdin=True,**args))==2
    assert before=={str(p):p.read_bytes() for p in env['root'].rglob('*') if p.is_file()}


@pytest.mark.parametrize('media',['threads','x'])
@pytest.mark.parametrize('fault',['zero','partial','fsync'])
def test_app_save_fault_retains_append_contract(env,monkeypatch,media,fault):
    values={'client_id':opaque(),'client_secret':opaque()}
    if media=='x':values.update(client_type='confidential',redirect_uri=auth_x.CALLBACK)
    path=env['app'] if media=='threads' else authclients.path_for('x',None,{})
    if media=='x':authclients.write(path,values)
    old=path.read_bytes();values['client_secret']=opaque();lines=[]
    emit_fault(monkeypatch,fault)
    assert appconfig.run(media,stdin=True,by='operator',input_func=lambda:json.dumps(values),log=lines.append)==2
    assert (path.read_bytes()==old) is (fault=='zero')
    rows,broken=admin_log.read()
    assert len(rows)==(1 if fault=='fsync' else 0) and broken==(1 if fault=='partial' else 0)
    assert any(('unconfirmed' if fault=='fsync' else 'uncertain' if fault=='partial' else 'refused') in x for x in lines)
    assert values['client_secret'] not in ''.join(lines)


def test_app_input_has_no_global_lock_and_newer_client_is_preserved(env):
    replacement=appenv.render_app_env(opaque(),opaque())
    def read():
        no_lock(env);env['app'].write_text(replacement)
        return json.dumps({'client_id':opaque(),'client_secret':opaque()})
    assert appconfig.run(stdin=True,by='operator',input_func=read,log=lambda _:None)==2
    assert env['app'].read_text()==replacement and admin_log.read()[0]==[]


def test_app_subject_is_not_loaded_as_account_for_notifications(env,monkeypatch):
    original=accounts.load_account; loaded=[]
    def load(name):
        loaded.append(name)
        return original(name)
    monkeypatch.setattr(accounts,'load_account',load)
    assert appconfig.run(stdin=True,by='operator',input_func=lambda:json.dumps({'client_id':opaque(),'client_secret':opaque()}),log=lambda _:None)==0
    assert not any(name.startswith('app-') for name in loaded)
    assert not list(env['root'].rglob('*.pending'))


@pytest.mark.parametrize('bad',[{},[],{'client_id':'id'}, {'client_id':'id','client_secret':'x','unknown':1}])
def test_invalid_app_input_preserves_existing(env,bad):
    old=env['app'].read_bytes()
    assert appconfig.run(stdin=True,by='operator',input_func=lambda:json.dumps(bad),log=lambda _:None)==2
    assert env['app'].read_bytes()==old and admin_log.read()[0]==[]


def test_auth_observation_has_no_fabricated_probe_and_no_public_generations(env):
    token=put_token(env)
    assert doctor.record_auth('alpha',env['cfg'],token)
    view=doctor.read_observation('alpha')
    assert view['auth_via']=='paste' and view['auth_observed_at']==jst.iso()
    assert view['probed_at'] is None and view['probes']==[] and view['auth_current_credentials'] is True
    assert view['probe_current_credentials'] is False
    raw=handoff_cursor.read_snapshot('alpha','doctor.json')
    assert len(raw['auth_credential_generation'])==64
    for value in [view,admin_report._permissions('alpha',False)]:
        assert raw['auth_credential_generation'] not in json.dumps(value) and 'credential_generation' not in json.dumps(value)
    assert stat.S_IMODE((env['root']/'state/alpha/doctor.json').stat().st_mode)==0o600


def test_auth_probe_auth_history_does_not_imply_current_permissions(env,frozen_now_jst):
    cfg_media(env,'mastodon');token=put_token(env,no_expiry=True)
    assert doctor.record_auth('alpha',env['cfg'],token)
    at=jst.now_jst();frozen_now_jst(at+datetime.timedelta(seconds=1))
    doctor.record_observation('alpha',probe(False,'permission'))
    old=doctor.read_observation('alpha');assert old['probe_current_credentials'] is True
    frozen_now_jst(at+datetime.timedelta(seconds=2));token=put_token(env,no_expiry=True)
    assert doctor.record_auth('alpha',env['cfg'],token)
    new=doctor.read_observation('alpha')
    assert new['probed_at']==old['probed_at'] and new['probes']==old['probes']
    assert new['auth_observed_at']==jst.iso() and new['probe_current_credentials'] is False
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['missing_scopes'] is None and row['scopes_source'] is None


@pytest.mark.parametrize('entry',['doctor','admin'])
def test_probe_unknown_start_generation_cannot_be_rebound_at_finish(env,monkeypatch,entry):
    cfg_media(env,'mastodon');put_token(env,no_expiry=True)
    original=doctor._credential_generation;calls=[]
    def generation(cfg):
        calls.append(1)
        return None if len(calls)==1 else original(cfg)
    monkeypatch.setattr(doctor,'_credential_generation',generation)
    monkeypatch.setattr(doctor,'diagnose',lambda _:probe())
    if entry=='doctor':doctor.run_doctor('alpha',as_json=True,log=lambda _:None)
    else:admin_report._permissions('alpha',True)
    assert handoff_cursor.read_snapshot('alpha','doctor.json')['probe_credential_generation'] is None
    assert doctor.read_observation('alpha')['probe_current_credentials'] is False
    assert admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]['missing_scopes'] is None


@pytest.mark.parametrize('entry',['doctor','admin'])
def test_x_no_probe_time_no_arbitrary_auth_via_no_threads_expiry(env,monkeypatch,entry):
    cfg_media(env,'x');token=put_token(env)
    token['auth_via']=opaque();Path(env['cfg']['token']).write_text(json.dumps(token))
    monkeypatch.setattr('thth.adapters.make_adapter',lambda *a,**k:pytest.fail('SNS adapter for X'))
    if entry=='doctor':
        lines=[];assert doctor.run_doctor('alpha',as_json=True,log=lines.append)==2;value=json.loads(lines[0])
    else:
        value=admin_report._permissions('alpha',True)
        assert value['probed_at'] is None and value['source'] is None
    assert token['auth_via'] not in json.dumps(value)
    assert doctor.read_observation('alpha') is None
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['remaining_days'] is None and row['expires_at'] is None and row['auth_via'] is None


def test_mastodon_response_scopes_take_priority_over_old_permission_probe(env):
    cfg_media(env,'mastodon');token=put_token(env,no_expiry=True)
    token.update(scopes=list(scopes.MASTODON_SCOPES),scopes_source='response');Path(env['cfg']['token']).write_text(json.dumps(token))
    doctor.record_observation('alpha',probe(False,'permission'))
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['scopes_source']=='response' and row['missing_scopes']==[] and row['missing_scopes_inferred'] is False
    assert row['scopes']==scopes.MASTODON_SCOPES


@pytest.mark.parametrize('entry',['auth','threads_stdin','mastodon_stdin'])
@pytest.mark.parametrize('fault',['zero','partial','fsync','observation'])
def test_event_failure_never_updates_auth_observation_and_observation_failure_is_success(env,monkeypatch,entry,fault):
    if entry=='mastodon_stdin':cfg_media(env,'mastodon')
    old=put_token(env);assert doctor.record_auth('alpha',env['cfg'],old)
    path=env['root']/'state/alpha/doctor.json';before=path.read_bytes();lines=[]
    if fault=='observation':
        original=handoff_cursor.write_snapshot
        def write(account,name,value):
            if name=='doctor.json':raise OSError('fault '+env['access'])
            return original(account,name,value)
        monkeypatch.setattr(handoff_cursor,'write_snapshot',write)
    else:emit_fault(monkeypatch,fault)
    if entry=='auth':
        rc=oauth.run_auth('alpha',by='operator',input_func=lambda:url(authflow._read_session('alpha'),env['code']),human_output=lambda _:None,log=lines.append)
    else:
        class Adapter:
            def whoami(self):no_lock(env);return dict(user_id='123',username='demo')
        monkeypatch.setattr('thth.adapters.make_adapter',lambda *a,**k:Adapter())
        def read():no_lock(env);return env['access']
        rc=oauth.run_token_set('alpha',stdin=True,force=True,by='operator',input_func=read,log=lines.append)
    assert rc==(0 if fault=='observation' else 2) and path.read_bytes()==before
    assert (json.loads(Path(env['cfg']['token']).read_text())['access_token']==old['access_token']) is (fault=='zero')
    if fault=='observation':assert any('auth_observation_not_recorded' in x for x in lines)
    assert env['access'] not in ''.join(lines)


@pytest.mark.parametrize('media',['threads','mastodon'])
def test_legacy_token_set_input_and_network_free_lock_records_auth(env,monkeypatch,media):
    cfg_media(env,media)
    class Adapter:
        def whoami(self):no_lock(env);return dict(user_id='123',username='demo')
    monkeypatch.setattr('thth.adapters.make_adapter',lambda *a,**k:Adapter())
    def read():no_lock(env);return env['access']
    assert oauth.run_token_set('alpha',stdin=True,by='operator',input_func=read,log=lambda _:None)==0
    obs=doctor.read_observation('alpha');assert obs['auth_via']=='token_set' and obs['probed_at'] is None

from tests.test_v211_bluesky_stdin import env as bs_env,run as run_bs
from tests.test_v211_mastodon_auth import env as masto_env,run as run_masto,client_path
from tests.test_v211_authflow import relay


def test_bs_stdin_records_auth_without_oauth_scope(bs_env):
    assert run_bs(bs_env)==0
    obs=doctor.read_observation('alpha')
    assert obs['auth_via']=='token_set' and obs['probed_at'] is None
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['scopes'] is None and row['default_scopes'] is None and row['missing_scopes'] is None


@pytest.mark.parametrize('fault',['zero','partial','fsync','observation'])
def test_bs_event_failure_does_not_record_auth(bs_env,monkeypatch,fault):
    if fault=='observation':monkeypatch.setattr(handoff_cursor,'write_snapshot',lambda *a,**k:(_ for _ in ()).throw(OSError('fault')))
    else:emit_fault(monkeypatch,fault)
    assert run_bs(bs_env)==(0 if fault=='observation' else 2)
    assert doctor.read_observation('alpha') is None


def test_relay_success_records_relay_auth_without_probe(env,monkeypatch):
    with relay(env,monkeypatch):assert oauth.run_auth('alpha',by='operator',human_output=lambda _:None,log=lambda _:None)==0
    obs=doctor.read_observation('alpha');assert obs['auth_via']=='relay' and obs['probed_at'] is None


def test_mastodon_automatic_app_presence_by_then_token_event(masto_env):
    assert run_masto(masto_env)==0
    rows,broken=admin_log.read();assert broken==0
    assert [r['event'] for r in rows]==['app_set','token_set']
    assert rows[0]['account'].startswith('app-mastodon-') and rows[0]['by']=='operator'
    assert rows[0]['diff']=={k:['absent','present'] for k in ('client_id','client_secret')}
    assert all(v not in json.dumps(rows) for v in (masto_env['client_id'],masto_env['client_secret']))
    assert run_masto(masto_env)==0
    assert [r['event'] for r in admin_log.read()[0]].count('app_set')==1


@pytest.mark.parametrize('fault',['zero','partial','fsync'])
def test_mastodon_app_event_failure_does_not_start_auth(masto_env,monkeypatch,fault):
    emit_fault(monkeypatch,fault)
    assert run_masto(masto_env)==2
    assert client_path(masto_env).exists() is (fault!='zero')
    assert not Path(masto_env['cfg']['token']).exists() and authflow._read_session('alpha') is None
    assert not masto_env['auth_queries'] and doctor.read_observation('alpha') is None
    assert [call[1] for call in masto_env['calls']]==['/.well-known/oauth-authorization-server','/api/v1/apps']


@pytest.mark.parametrize('media',['threads','mastodon'])
@pytest.mark.parametrize('change',['token','ledger','flow'])
def test_manual_token_wait_cannot_overwrite_concurrent_change(env,monkeypatch,media,change):
    cfg_media(env,media);put_token(env)
    class Adapter:
        def whoami(self):return dict(user_id='123',username='demo')
    monkeypatch.setattr('thth.adapters.make_adapter',lambda *a,**k:Adapter())
    expected=[]
    def read():
        if change=='token':put_token(env)
        elif change=='ledger':env['ledger'].write_text(json.dumps({**env['cfg'],'handle':'different'}))
        else:authflow._write_session('alpha',dict(state=opaque(),created_at=jst.iso()))
        expected.append(Path(env['cfg']['token']).read_bytes());return env['access']
    assert oauth.run_token_set('alpha',stdin=True,force=True,by='operator',input_func=read,log=lambda _:None)==2
    assert Path(env['cfg']['token']).read_bytes()==expected[0] and doctor.read_observation('alpha') is None


def test_old_probe_retained_as_history_after_new_auth(env):
    cfg_media(env,'mastodon');token=put_token(env,no_expiry=True)
    old=dict(schema_version=1,account='alpha',probed_at=jst.iso(),probes=probe(False,'permission')['probes'],error=None)
    handoff_cursor.write_snapshot('alpha','doctor.json',old)
    assert doctor.read_observation('alpha')['probe_current_credentials'] is None
    assert doctor.record_auth('alpha',env['cfg'],token)
    new=doctor.read_observation('alpha')
    assert new['probed_at']==old['probed_at'] and new['probe_current_credentials'] is False
    assert admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]['missing_scopes'] is None

@pytest.mark.parametrize('media',['threads','mastodon','x'])
@pytest.mark.parametrize('shape',['full','partial','unknown'])
def test_three_oauth_media_report_response_scope_difference(env,media,shape):
    cfg_media(env,media)
    expected=scopes.DEFAULT_SCOPES if media=='threads' else scopes.MASTODON_SCOPES if media=='mastodon' else auth_x.SCOPES
    token=put_token(env,expires_in=7200)
    token.update(scopes=list(expected) if shape=='full' else list(expected[:-1]) if shape=='partial' else None,
                 scopes_source='response' if shape!='unknown' else 'unknown')
    Path(env['cfg']['token']).write_text(json.dumps(token))
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['default_scopes']==expected
    assert row['missing_scopes']==([] if shape=='full' else [expected[-1]] if shape=='partial' else None)
    if shape!='unknown':assert row['scopes_source']=='response'


def test_version_client_help_and_current_guide_links():
    from thth import __version__
    root=Path(__file__).parents[1]
    assert __version__==(Path(__file__).resolve().parents[1]/'thth'/'VERSION').read_text().strip()  # follow the VERSION file, not a literal
    server=json.loads((root/'server.json').read_text());assert server['version']==__version__==server['packages'][0]['version']
    guide=(root/'docs/導入_承認を押すだけ.md').read_text()
    assert all(s in guide for s in ['masaru','利用者自身が VM','接続」ページはまだ','client_type','confidential','auth_observed_at','probed_at','op read','300 秒','600 秒','30 秒','5 分','1 日 1 回','auth-only','App Password','--stdin','--by'])
    for name in ['README.md','README.en.md','llms.txt','docs/README.md']:
        assert '導入_承認を押すだけ.md' in (root/name).read_text()
    for name in ['導入_自分のMetaアプリで動かす.md','導入_Mastodon_2026-09-13.md','導入_Bluesky_2026-09-13.md']:
        value=(root/'docs'/name).read_text();assert '導入_承認を押すだけ.md' in value and '現在の導入手順ではありません' in value

@pytest.mark.parametrize('bad',['mode','symlink','fifo'])
def test_mastodon_registration_rejects_known_bad_log_before_post(masto_env,monkeypatch,bad):
    import os
    path=masto_env['root']/'state/_admin/accounts.ndjson';path.parent.mkdir(parents=True)
    if bad=='mode':path.write_text('');path.chmod(0o644)
    elif bad=='symlink':
        other=masto_env['home']/'outside';other.write_text('');path.symlink_to(other)
    else:os.mkfifo(path)
    assert run_masto(masto_env)==2
    assert not any(method=='POST' for method,_,_,_ in masto_env['calls'])
    assert not client_path(masto_env).exists() and not Path(masto_env['cfg']['token']).exists()

@pytest.mark.parametrize('media',['threads','x'])
@pytest.mark.parametrize('source',['requested','unknown',None])
@pytest.mark.parametrize('partial',[False,True])
def test_requested_scope_list_is_not_an_observed_grant(env,media,source,partial):
    cfg_media(env,media)
    expected=scopes.DEFAULT_SCOPES if media=='threads' else auth_x.SCOPES
    token=put_token(env,expires_in=7200);actual=list(expected[:-1] if partial else expected)
    token.update(scopes=actual,scopes_source=source);Path(env['cfg']['token']).write_text(json.dumps(token))
    row=admin_report.answer('tokens',account='alpha',via='http')['tokens'][0]
    assert row['scopes']==actual and row['scopes_source']==(source or 'unknown')
    assert row['missing_scopes'] is None


def test_app_raw_and_fresh_reader_contain_presence_not_values(env):
    import subprocess,sys
    values={'client_id':opaque(),'client_secret':opaque()}
    assert appconfig.run(stdin=True,by='operator',input_func=lambda:json.dumps(values),log=lambda _:None)==0
    raw=(env['root']/'state/_admin/accounts.ndjson').read_text()
    fresh=subprocess.run([sys.executable,'-c','from thth import admin_log; import json; print(json.dumps(admin_log.read()))'],capture_output=True,text=True,check=True)
    assert all(value not in raw+fresh.stdout+fresh.stderr for value in values.values())
    rows,broken=json.loads(fresh.stdout);assert not broken and rows[0]['diff']=={k:['present','present'] for k in values}
