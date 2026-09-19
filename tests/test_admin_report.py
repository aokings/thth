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
