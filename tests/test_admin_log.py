import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from thth import account_cli, accounts, admin_log, oauth

@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv('THTH_ROOT', str(tmp_path))
    monkeypatch.setenv('THTH_ACCOUNTS_DIR', str(tmp_path / 'accounts'))
    return tmp_path

def args(**kw):
    value = dict(name='test-threads', by='tester', media='threads', project='test', handle='tester',
                 instance=None, repo_dir=None, redirect_uri=None, force=False, json=True)
    value.update(kw)
    return SimpleNamespace(**value)

def test_actor_required_before_any_write(root):
    assert account_cli.cmd_add(args(by=None)) == 2
    assert not (root / 'accounts').exists()
    for fun in (oauth.run_auth, oauth.run_auth_bluesky, oauth.run_token_set):
        assert fun('missing', log=lambda _: None) == 2

def test_creation_and_force_preserve_provenance(root):
    assert account_cli.cmd_add(args()) == 0
    path = root / 'accounts/test-threads.json'
    old = json.loads(path.read_text())
    assert old['provenance']['created_by'] == 'tester'
    assert old['provenance']['created_via'] == 'cli'
    assert account_cli.cmd_add(args(force=True, by='second', handle='changed')) == 0
    current = json.loads(path.read_text())
    assert current['provenance'] == old['provenance']
    assert current['handle'] == 'changed'
    rows, broken = admin_log.read()
    assert [r['event'] for r in rows] == ['account_added', 'account_updated']
    assert rows[-1]['diff']['handle'] == ['tester', 'changed']
    assert not broken
    before = path.read_bytes()
    assert account_cli.cmd_add(args(force=True, by=None)) == 2
    assert path.read_bytes() == before

def test_log_append_mode_secret_and_broken(root):
    cfg = {'media': 'threads'}
    diff = admin_log.difference({'token':'FAKE_OLD_TOKEN','env':'FAKE_OLD_SECRET','notification_email':'a@example.test'},
                               {'token':'FAKE_NEW_TOKEN','env':'FAKE_NEW_SECRET','notification_email':'b@example.test'})
    admin_log.append('token_set', 'demo', cfg, by='tester', diff=diff)
    path = root / 'state/_admin/accounts.ndjson'
    first = path.read_bytes()
    admin_log.append('production_enabled', 'demo', cfg, by='tester', diff={'production':[False,True]})
    assert path.read_bytes().startswith(first)
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'FAKE_' not in path.read_text() and '@' not in path.read_text()
    assert json.loads(first)['diff']['token'] == ['present','present']
    with path.open('a') as f: f.write('broken\n[]\n')
    rows, broken = admin_log.read(event='token_set')
    assert len(rows) == 1 and broken == 2

def test_log_symlink_refused(root):
    target = root / 'outside'; target.write_text('unchanged')
    (root / 'state/_admin').mkdir(parents=True)
    (root / 'state/_admin/accounts.ndjson').symlink_to(target)
    with pytest.raises(OSError):
        admin_log.append('token_set', 'demo', {'media':'threads'}, by='tester')
    assert target.read_text() == 'unchanged'

def test_revoke_logs_no_secret(root):
    assert account_cli.cmd_add(args()) == 0
    path = root / 'accounts/test-threads.json'
    cfg = json.loads(path.read_text()); token = root / 'fake.token'
    cfg['token'] = str(token); path.write_text(json.dumps(cfg))
    token.write_text('{"access_token":"FAKE_PRIVATE_TOKEN"}')
    assert oauth.run_token_revoke('test-threads', by=None) == 2
    assert token.exists()
    assert oauth.run_token_revoke('test-threads', by='tester') == 0
    assert not token.exists()
    rows, _ = admin_log.read(event='token_revoked')
    assert rows[0]['diff']['token'] == ['present','absent']

@pytest.mark.parametrize('bad', ['permissions','symlink','fifo'])
def test_invalid_log_blocks_mutation(root, bad):
    assert account_cli.cmd_add(args()) == 0
    path = root/'accounts/test-threads.json'; old = path.read_bytes()
    log=root/'state/_admin/accounts.ndjson'
    if bad=='permissions': log.chmod(0o644)
    else:
        log.unlink()
        if bad=='symlink': log.symlink_to(root/'outside')
        else: os.mkfifo(log)
    assert account_cli.cmd_add(args(force=True,by='second',handle='changed'))==2
    assert path.read_bytes()==old

def test_log_rejects_malformed_diff_and_scrubs_other_columns(root):
    admin_log.append('token_set','demo',{'media':'threads'},by='tester')
    path=root/'state/_admin/accounts.ndjson'; row=json.loads(path.read_text())
    row['diff']={'token':'FAKE_RAW_TOKEN'}
    with path.open('a') as f:f.write(json.dumps(row)+'\n')
    rows,broken=admin_log.read()
    assert broken==1 and len(rows)==1 and 'FAKE_RAW_TOKEN' not in json.dumps(rows)
