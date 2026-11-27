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


# 括りの段 4（開く日に置き直す）で `thth-collect@.service`・`thth-map@.service` に印 `THTH_WS_FROM_ACCOUNT=1` を
# 足した。配った姿（c812 の基準）は collect が 5e747d7e…、map が ad097051…。開く日の手順は設計
# docs/設計_THTHアカウント（括り）_共通の層_叩き台_2026-11-26.md §10「残りと縛り」。ほかの単位は c812 のまま。
DEPLOYED_BEFORE_WS_MARK = {
    'render_collect_service': '5e747d7e74ea2a46aeca800822501c0daef104bac05915f5becab5a6413f70af',
    'render_map_service': 'ad097051d31243ee4f05bd9731d8e7de56e526c8712136942d20bef43a02df11',
}


def test_existing_unit_bytes_match_c812_baseline():
    expected = {'render_timer': '4db3ca26da05078eebc9fa20126bae313ee84316fb8df2be0dd889fda7efd711', 'render_collect_timer': '69ca85f636080433f171d6b1fe76116be95c1f0791fc643884aecea42b49edf3', 'render_collect_service': '71192deedee37f388c8e63e41b99a8e97d1b79d9405fba8b33f4efb0c172c400', 'render_maintain_timer': '1375bb2024cfa4d54290e4b0f058c8adc754906ae54496a48ec63427541974a5', 'render_maintain_service': '66d2ac74dd17dda00ce43a4e542c8f651e984cf904526935e7cc21304342a44d', 'render_map_service': '36ef76aa3d5afdae2842cc5edea84f9bbc99f425e6117a6cb731b8de35eb4043'}
    cfg = {"account": "synthetic-alpha", "tick_minutes": 10}
    for name, digest in expected.items():
        args = [cfg] if name in ("render_timer", "render_collect_timer") else []
        assert hashlib.sha256(getattr(systemd_gen, name)(*args).encode()).hexdigest() == digest


@pytest.mark.parametrize('name', sorted(DEPLOYED_BEFORE_WS_MARK))
def test_ws_mark_is_the_only_change_from_deployed(name):
    """配った姿との違いは、印の 2 行（説明の行と `Environment=THTH_WS_FROM_ACCOUNT=1`）だけ。"""
    unit = getattr(systemd_gen, name)()
    assert unit.count('Environment=THTH_WS_FROM_ACCOUNT=1\n') == 1
    lines = unit.splitlines(keepends=True)
    i = lines.index('Environment=THTH_WS_FROM_ACCOUNT=1\n')
    assert lines[i - 1].startswith('# 括りの印: ')
    before = ''.join(lines[:i - 1] + lines[i + 1:])
    assert hashlib.sha256(before.encode()).hexdigest() == DEPLOYED_BEFORE_WS_MARK[name]
    # 印は [Service] の中・ExecStart より前（systemd は Environment を ExecStart に渡す）。
    assert unit.index('[Service]') < unit.index('Environment=THTH_WS_FROM_ACCOUNT=1') < unit.index('ExecStart=')
