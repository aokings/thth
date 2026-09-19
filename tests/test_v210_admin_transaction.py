"""P3 administration transactions: one invocation and post-lock notices."""
from thth import accounts, admin_log


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
