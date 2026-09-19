"""P3 administration transactions: one invocation and post-lock notices."""
import fcntl
import json
import os
import subprocess
import sys

from thth import accounts, admin_log, admin_notifications


def test_guarded_account_error_mid_operation_is_not_retried(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('THTH_ROOT', str(tmp_path))
    token = tmp_path / 'token.json'
    token.write_text('SAFE_ORIGINAL', encoding='utf-8')
    token.chmod(0o600)
    monkeypatch.setattr(accounts, 'load_account', lambda name: {'token': str(token)})
    calls = []

    def halfway(name, *, by):
        calls.append(name)
        token.write_text('SAFE_CHANGED', encoding='utf-8')
        raise accounts.AccountError('fixture bounded account failure')

    assert admin_log.guarded(halfway)('alpha', by='audit') == 2
    assert calls == ['alpha']
    assert token.read_text(encoding='utf-8') == 'SAFE_ORIGINAL'
    assert token.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr() == ('', 'fixture bounded account failure\n')
    assert (tmp_path / 'state/_admin/accounts.ndjson').read_bytes() == b''


def test_guarded_account_error_before_operation_keeps_original_diagnostic(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('THTH_ROOT', str(tmp_path))
    calls = []

    def unavailable(name):
        raise accounts.AccountError('台帳が無い: alpha')

    monkeypatch.setattr(accounts, 'load_account', unavailable)

    def operation(name, *, by):
        calls.append(name)
        return 0

    assert admin_log.guarded(operation)('alpha', by='audit') == 2
    assert calls == []
    assert capsys.readouterr() == ('', '台帳が無い: alpha\n')
    assert not (tmp_path / 'state/_admin/accounts.ndjson').exists()


def test_nested_transaction_notifies_in_order_after_durable_unlock(tmp_path, monkeypatch):
    monkeypatch.setenv('THTH_ROOT', str(tmp_path))
    monkeypatch.setattr(accounts, 'load_account', lambda name: {'media': 'threads'})
    log = tmp_path / 'state/_admin/accounts.ndjson'
    emitted = []
    original_emit = admin_log._emit

    def observed_emit(fd, data):
        original_emit(fd, data)
        emitted.append(data)

    monkeypatch.setattr(admin_log, '_emit', observed_emit)
    notices = []

    def fake_notify(event, cfg):
        assert len(emitted) == 1
        assert admin_log._active_fd.get() is None
        rows = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        assert [row['event'] for row in rows] == [
            'account_updated', 'production_enabled', 'token_set']
        assert event == rows[len(notices)]
        assert 'FAKE_PRIVATE_TOKEN' not in log.read_text(encoding='utf-8')
        assert 'FAKE_PRIVATE_TOKEN' not in json.dumps(event)
        fd = os.open(log, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        child = subprocess.run([sys.executable, '-c',
            'import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDONLY); '
            'fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); os.close(f)', str(log)],
            capture_output=True, text=True, timeout=5)
        assert child.returncode == 0, child.stderr
        notices.append(event['event'])

    monkeypatch.setattr(admin_notifications, 'notify', fake_notify)
    with admin_log.transaction():
        admin_log.append('account_updated', 'alpha', {'media': 'threads'}, by='audit')
        with admin_log.transaction():
            admin_log.append('production_enabled', 'alpha', {'media': 'threads'}, by='audit')
        assert notices == []
        assert log.read_bytes() == b''
        admin_log.append('token_set', 'alpha', {'media': 'threads'}, by='audit',
                         diff={'token': ['FAKE_PRIVATE_TOKEN', 'present']})
    assert notices == ['account_updated', 'production_enabled', 'token_set']
    assert len(emitted) == 1


def test_notice_exception_does_not_rollback_durable_guarded_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('THTH_ROOT', str(tmp_path))
    token = tmp_path / 'token.json'
    token.write_text('SAFE_ORIGINAL', encoding='utf-8')
    token.chmod(0o600)
    monkeypatch.setattr(accounts, 'load_account', lambda name: {'token': str(token), 'media': 'threads'})
    attempts = []

    def failing_notify(event, cfg):
        attempts.append(event['event'])
        raise RuntimeError('FAKE_PRIVATE_TOKEN notification failed')

    monkeypatch.setattr(admin_notifications, 'notify', failing_notify)

    @admin_log.guarded
    def operation(name, *, by):
        token.write_text('SAFE_CHANGED', encoding='utf-8')
        admin_log.append('token_set', name, {'media': 'threads'}, by=by)
        admin_log.append('production_enabled', name, {'media': 'threads'}, by=by)
        return 0

    assert operation('alpha', by='audit') == 0
    assert token.read_text(encoding='utf-8') == 'SAFE_CHANGED'
    assert attempts == ['token_set', 'production_enabled']
    rows, broken = admin_log.read()
    assert broken == 0
    assert [row['event'] for row in rows] == attempts
    out, err = capsys.readouterr()
    assert out == ''
    assert err == 'admin_notification_pending\nadmin_notification_pending\n'
    assert 'FAKE_PRIVATE_TOKEN' not in err
