"""Generate literal worker units; no daemon, credentials, or provider operations."""
import hashlib
import subprocess
import pytest
from thth import cli, systemd_gen


def test_worker_unit_cli_and_actual_worker_parser_agree(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no process execution'))
    credentials = '/srv/thth/private/report-credentials.json'
    rc = cli.main(['systemd', '--worker', '--credentials', credentials])
    capture = capsys.readouterr()
    assert rc == 0 and not capture.err
    unit = capture.out
    assert unit == systemd_gen.render_worker_service(credentials)
    assert 'Type=simple\n' in unit and 'Restart=on-failure\nRestartSec=5\n' in unit
    assert '[Timer]' not in unit and '--once' not in unit and 'Type=oneshot' not in unit
    assert 'User=wt\nEnvironment=THTH_ROOT=/srv/thth\n' in unit
    assert 'WantedBy=multi-user.target\n' in unit
    command = next(line.removeprefix('ExecStart=') for line in unit.splitlines() if line.startswith('ExecStart='))
    assert command.split() == ['/srv/thth/app/bin/thth', 'worker', '--credentials', credentials]
    parsed = cli.build_parser().parse_args(command.split()[1:])
    assert parsed.credentials == credentials and not parsed.once


@pytest.mark.parametrize('path', ['', 'relative.json', '/', '/a//b', '/a/../b', '/a/./b', '/a/', '/a b', '/a\nb', '/a\rb', '/a\tb', '/a\x00b', '/a"b', "/a'b", '/a\\b', '/a%ib', '/a${HOME}', '/a$b', '/a%b', '/a;b', '/日本語.json'])
def test_worker_unsafe_path_loud_reject(path, capsys):
    rc = cli.main(['systemd', '--worker', '--credentials', path])
    captured = capsys.readouterr()
    assert rc == 2 and not captured.out and 'credentials:' in captured.err


@pytest.mark.parametrize('arguments', [
    ['--worker'],
    ['alpha', '--worker', '--credentials', '/private/config.json'],
    ['--worker', '--maintain', '--credentials', '/private/config.json'],
    ['--worker', '--collect-only', '--credentials', '/private/config.json'],
    ['--maintain', '--credentials', '/private/config.json'],
])
def test_worker_missing_or_conflicting_arguments(arguments, capsys):
    assert cli.main(['systemd', *arguments]) == 2
    captured = capsys.readouterr()
    assert not captured.out and captured.err


def test_worker_never_reads_credential_contents(tmp_path, monkeypatch):
    path = tmp_path / 'private.json'
    path.write_text('synthetic-private-value')
    import builtins
    monkeypatch.setattr(builtins, 'open', lambda *a, **k: pytest.fail('must not read credential'))
    assert 'synthetic-private-value' not in systemd_gen.render_worker_service(str(path))


def test_existing_unit_bytes_match_c812_baseline():
    expected = {'render_timer': '4db3ca26da05078eebc9fa20126bae313ee84316fb8df2be0dd889fda7efd711', 'render_collect_timer': '69ca85f636080433f171d6b1fe76116be95c1f0791fc643884aecea42b49edf3', 'render_collect_service': '5e747d7e74ea2a46aeca800822501c0daef104bac05915f5becab5a6413f70af', 'render_maintain_timer': '1375bb2024cfa4d54290e4b0f058c8adc754906ae54496a48ec63427541974a5', 'render_maintain_service': '66d2ac74dd17dda00ce43a4e542c8f651e984cf904526935e7cc21304342a44d'}
    cfg = {"account": "synthetic-alpha", "tick_minutes": 10}
    for name, digest in expected.items():
        args = [cfg] if name in ("render_timer", "render_collect_timer") else []
        assert hashlib.sha256(getattr(systemd_gen, name)(*args).encode()).hexdigest() == digest
