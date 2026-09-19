import datetime
import json
from pathlib import Path
import pytest
from thth import accounts, account_cli, admin_log, admin_report, analytics_report, collection_status, doctor, jst, operations_handoff
from tests.test_admin_log import args

@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('THTH_ROOT', str(tmp_path))
    monkeypatch.setenv('THTH_ACCOUNTS_DIR', str(tmp_path / 'accounts'))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(doctor, 'diagnose', lambda _: pytest.fail('unexpected API probe'))
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda p: False if str(p) == '/run/systemd/system' else real_is_dir(p))
    repo = tmp_path / 'repo'; (repo / 'data/sns/queue').mkdir(parents=True)
    assert account_cli.cmd_add(args(repo_dir=str(repo))) == 0
    path = tmp_path / 'accounts/test-threads.json'; cfg = json.loads(path.read_text())
    cfg.update(token=str(tmp_path / 'fake.token'), env=str(tmp_path / 'fake.env'), notification_email='fake-private@example.test')
    cfg.pop('provenance'); cfg.pop('scheduled')
    path.write_text(json.dumps(cfg))
    now = datetime.datetime(2026,9,19,tzinfo=datetime.timezone.utc)
    (tmp_path/'fake.token').write_text(json.dumps(dict(access_token='FAKE_PRIVATE_TOKEN_12345',obtained_at=jst.iso(now-datetime.timedelta(days=55)),expires_in=60*86400,user_id='123',username='tester',scopes=['threads_basic'],scopes_source='response')))
    (tmp_path/'fake.token').chmod(0o600)
    (tmp_path/'fake.env').write_text('CLIENT_SECRET=FAKE_APP_SECRET_98765\n')
    state=tmp_path/'state/test-threads';state.mkdir(parents=True)
    (state/'runs-2026-09.ndjson').write_text(json.dumps(dict(account='test-threads',run_id='collect-'+jst.iso(now),mode='collect',action='collect',status='ok',collected=4,error='FAKE_PRIVATE_TOKEN_12345 FAKE_APP_SECRET_98765 fake-private@example.test'))+'\n')
    return tmp_path, now, accounts.load_account('test-threads')

@pytest.mark.parametrize('field', ['identity','raw_config','repo','provenance','token','permissions','queue','inflight_last_sent','collection','notifications','timer','last_change'])
def test_inventory_table_one_case_per_row(fixture, field):
    root, now, cfg=fixture
    result=admin_report.answer(now=now); row=result['by_account']['test-threads']
    handoff=operations_handoff._account('test-threads',cfg,now)
    if field=='identity': assert (row['account'],row['project'],row['medium'],row['handle'],row['instance'])==('test-threads','test','threads','tester',None)
    elif field=='raw_config': assert row['scheduled'] is None and row['defaults']['scheduled'] is False
    elif field=='repo': assert row['repo']['ahead'] is None and row['repo']['repo_reason']=='repo_broken'
    elif field=='provenance': assert all(x is None for x in row['provenance'].values()) and row['provenance_reason']=='recorded_before_2.9.0'
    elif field=='token': assert row['token']['remaining_days']==5 and row['token']['mode_ok'] and row['token']['handle_matches']
    elif field=='permissions': assert row['permissions']['probed_at'] is None and row['permissions']['result'] is None
    elif field=='queue': assert row['queue']==handoff['queue']
    elif field=='inflight_last_sent': assert all(row[k]==handoff[k] for k in ('inflight','last_post','sent_count'))
    elif field=='collection': assert row['collection']==collection_status.summarize('test-threads',now)[0]
    elif field=='notifications': assert row['notifications']['mail_pending']==handoff['notifications']['mail_pending']
    elif field=='timer': assert row['timer']['units'] is None and row['timer']['timer_reason']
    elif field=='last_change': assert row['last_change']['event']=='account_added'
    assert result['summary']['accounts']==1 and result['summary']['token_within_seven_days']==1

def test_detail_secret_scan_and_probe_optin(fixture, monkeypatch):
    root, now, cfg=fixture
    monkeypatch.setattr(doctor,'diagnose', lambda name: dict(probes=[dict(key='me',ok=True,http=200)]))
    value=admin_report.answer('account',account='test-threads',probe=True,now=now)
    row=value['by_account']['test-threads']
    assert row['permissions']['result']['probes'][0]['http']==200
    assert row['runs'][0]['collected']==4
    assert row['ledger_fields']['token']['present'] is True
    for rendered in (json.dumps(value),admin_report.render_markdown(value)):
        assert all(secret not in rendered for secret in ('FAKE_PRIVATE_TOKEN_12345','FAKE_APP_SECRET_98765','fake-private@example.test'))
    assert row['analytics_collection']==analytics_report.answer('test-threads',now=now)['by_account']['test-threads']['collection']

def test_unreadable_and_missing_sources_retained(fixture):
    root,now,cfg=fixture
    (root/'accounts/broken.json').write_text('{')
    (root/'fake.token').unlink()
    value=admin_report.answer(now=now)
    assert value['by_account']['broken']['ledger']=='unreadable'
    token=value['by_account']['test-threads']['token']
    assert token['present'] is False and all(token[k] is None for k in admin_report.TOKEN_FIELDS)
    assert value['summary']['accounts']==2

def test_tokens_release_and_diff(fixture):
    root,now,cfg=fixture
    from thth import report
    token=admin_report.answer('tokens',now=now)['tokens'][0]
    assert token['remaining_days']==5
    assert 'threads_basic' not in token['missing_scopes']
    assert token['last_refresh'] is None
    release=admin_report.answer('release',now=now)['release']
    assert all(release[k]==v for k,v in report.release_summary().items())
    cursor=root/'state/_admin/admin_cursor.json'
    first=admin_report.answer('diff',since_last_read=True,now=now)
    assert first['cannot_say']==['no_previous_session_cursor'] and not cursor.exists()
    with pytest.raises(ValueError):admin_report.answer('diff',since_last_read=True,mark_read=True,now=now)
    admin_report.answer('diff',since_last_read=True,mark_read=True,by='tester',now=now)
    assert cursor.stat().st_mode & 0o777==0o600
    same=admin_report.answer('diff',since_last_read=True,now=now)
    assert same['changes']==[]
    path=root/'accounts/test-threads.json';cfg=json.loads(path.read_text());cfg['production']=True;path.write_text(json.dumps(cfg))
    changed=admin_report.answer('diff',since_last_read=True,now=now)
    assert any(c['field']=='production' and c['previous'] is False and c['current'] is True for c in changed['changes'])
    assert json.loads(cursor.read_text())['snapshot']['by_account']['test-threads']['production'] is False

def test_http_never_probes_or_marks_and_timers_cached_only(fixture,monkeypatch):
    root,now,cfg=fixture
    monkeypatch.setattr(admin_report.subprocess,'run',lambda *a,**k:pytest.fail('systemctl/git must not run'))
    assert admin_report.answer('timers',via='http',now=now)['by_account']['test-threads']['units'] is None
    with pytest.raises(ValueError):admin_report.answer('inventory',probe=True,via='http',now=now)
    with pytest.raises(ValueError):admin_report.answer('diff',since_last_read=True,mark_read=True,by='tester',via='http',now=now)

@pytest.mark.parametrize('node',['bad',{'token':{'password':'FAKE_CURSOR_PASSWORD'}}])
def test_corrupt_cursor_is_unreadable(fixture,node):
    root,now,cfg=fixture
    admin_report.answer('diff',since_last_read=True,mark_read=True,by='tester',now=now)
    path=root/'state/_admin/admin_cursor.json';value=json.loads(path.read_text());value['snapshot']['by_account']['test-threads']=node;path.write_text(json.dumps(value))
    result=admin_report.answer('diff',since_last_read=True,now=now)
    assert result['cannot_say']==['cursor_unreadable'] and 'FAKE_CURSOR_PASSWORD' not in json.dumps(result)

def test_bad_provenance_and_nested_secret_do_not_leak(fixture):
    root,now,cfg=fixture
    path=root/'accounts/test-threads.json';value=json.loads(path.read_text());value.update(provenance='bad',handle={'password':'FAKE_METADATA_PASSWORD'},production={'client_secret':'FAKE_METADATA_APP_SECRET'});path.write_text(json.dumps(value))
    result=admin_report.answer(now=now)
    text=json.dumps(result)
    assert 'FAKE_METADATA_PASSWORD' not in text and 'FAKE_METADATA_APP_SECRET' not in text
    assert result['by_account']['test-threads']['provenance_reason']=='provenance_unreadable'

def test_refresh_history_skips_nonrefresh_maintenance(fixture):
    root,now,cfg=fixture
    path=root/'state/test-threads/runs-2026-09.ndjson'
    path.write_text('\n'.join(json.dumps(row) for row in [
        dict(account='test-threads',action='maintain',run_id='maintain-2026-09-18T12:00:00+09:00',status='error',refreshed=False,error='refresh_failed'),
        dict(account='test-threads',action='maintain',run_id='maintain-2026-09-19T12:00:00+09:00',status='ok',refreshed=False,error=None)]))
    result=admin_report.answer('tokens',now=now)
    assert result['tokens'][0]['last_refresh']['error']=='refresh_failed'

def test_http_diff_does_not_read_external_app_env(fixture,monkeypatch):
    root,now,cfg=fixture
    from thth import appenv
    monkeypatch.setattr(appenv,'default_path',lambda:'/outside/private/app.env')
    original=appenv._parse_env_file
    def parse(path):
        assert path!='/outside/private/app.env'
        return original(path)
    monkeypatch.setattr(appenv,'_parse_env_file',parse)
    admin_report.answer('diff',since_last_read=True,via='http',now=now)

def test_nested_bluesky_credentials_are_scrubbed(fixture):
    root,now,cfg=fixture
    path=root/'accounts/test-threads.json';value=json.loads(path.read_text());value['handle']={'accessJwt':'FAKE_BSKY_ACCESS_JWT','refreshJwt':'FAKE_BSKY_REFRESH_JWT'};path.write_text(json.dumps(value))
    assert 'FAKE_BSKY_' not in json.dumps(admin_report.answer(now=now))

def test_missing_required_setting_is_null_not_default(fixture):
    root,now,cfg=fixture
    path=root/'accounts/test-threads.json';value=json.loads(path.read_text());value.pop('quiet_hours');path.write_text(json.dumps(value))
    result=admin_report.answer(now=now)['by_account']['test-threads']
    assert result['ledger']=='available' and result['quiet_hours'] is None
    assert result['defaults']['quiet_hours'] is not None
    assert 'ledger_fields_unavailable' in result['cannot_say']

def test_malformed_timer_cache_is_unknown(fixture):
    root,now,cfg=fixture
    (root/'state/_admin/timers.json').write_text('{"by_account": []}')
    result=admin_report.answer('timers',via='http',now=now)
    assert result['by_account']['test-threads']['units'] is None
    assert result['by_account']['test-threads']['timer_reason']=='timer_observation_unavailable'
