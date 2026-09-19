"""Administrator acceptance repairs; all observations and credentials are fixtures."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from thth import account_report, admin_report, handoff_cursor, report_http
from thth.report_service import execute_report
from tests.test_admin_http_mcp import credentials
from tests.test_mcp import _load_server_module


@pytest.fixture
def systemd(monkeypatch):
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda p: True if str(p) == '/run/systemd/system' else real_is_dir(p))
    calls = []
    def run(args, **kwargs):
        assert args[:2] == ['systemctl', 'show']
        calls.append(args[2])
        if args[2].endswith('.service'):
            return SimpleNamespace(returncode=0, stdout='ExecMainStatus=7\n')
        return SimpleNamespace(returncode=0, stdout=(f'Id={args[2]}\nLoadState=loaded\nActiveState=active\n'
                              'LastTriggerUSec=Fri 2026-09-18 21:00:00 JST\nNextElapseUSecRealtime=Sat 2026-09-19 21:00:00 JST\n'))
    monkeypatch.setattr(admin_report.subprocess, 'run', run)
    return calls


def test_timer_cli_snapshot_http_and_mcp_inventory(credentials, systemd, monkeypatch):
    path, token, _ = credentials
    root, creds = report_http.load_credentials(path)
    observed = admin_report.answer('timers')['by_account']
    cache = Path(root) / 'state/_admin/timers.json'
    assert cache.stat().st_mode & 0o777 == 0o600
    assert json.loads(cache.read_text())['by_account'] == observed
    assert observed['first']['units'][0]['unit'] == 'thth@first.timer'
    assert tuple(u['unit'] for u in observed['first']['units']) == account_report.timer_units('first')
    assert observed['first']['units'][0]['exec_main_status'] == 7
    assert observed['first']['observed_at'] and observed['first']['units'][0]['active'] == 'active'
    assert 'thth-run@first.timer' not in systemd
    before = cache.read_bytes(), cache.stat().st_mtime_ns
    monkeypatch.setattr(admin_report.subprocess, 'run', lambda *a, **k: pytest.fail('remote observation'))
    monkeypatch.setattr(handoff_cursor, 'write_snapshot', lambda *a, **k: pytest.fail('remote write'))
    assert execute_report(creds[0][3], {'operation': 'admin_timers'})['by_account'] == observed
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS', str(path)); monkeypatch.setenv('THTH_REPORT_TOKEN', token)
    server = _load_server_module()
    payload = json.loads(server.call_tool('thth_admin_inventory', {})['content'][0]['text'])
    assert {n: r['timer'] for n, r in payload['by_account'].items()} == observed
    assert (cache.read_bytes(), cache.stat().st_mtime_ns) == before


def test_timer_uses_account_report_unit_helper(credentials, systemd, monkeypatch):
    monkeypatch.setattr(account_report, 'timer_units', lambda _: ('installed.timer',))
    assert admin_report.timer('first')['units'][0]['unit'] == 'installed.timer'
    assert systemd == ['installed.timer', 'installed.service']


@pytest.mark.parametrize('fault', ['fsync', 'replace', 'symlink', 'fifo', 'parent_symlink'])
def test_timer_snapshot_failure_preserves_previous_or_external(credentials, systemd, monkeypatch, fault):
    path, _, _ = credentials
    root = Path(report_http.load_credentials(path)[0])
    admin_report.answer('timers')
    cache = root / 'state/_admin/timers.json'
    original = cache.read_bytes()
    external = path.parent / 'outside'; external.mkdir()
    victim = external / 'timers.json'; victim.write_bytes(b'EXTERNAL_UNCHANGED')
    if fault == 'symlink':
        cache.unlink(); cache.symlink_to(victim)
    elif fault == 'fifo':
        cache.unlink(); os.mkfifo(cache)
    elif fault == 'parent_symlink':
        cache.unlink(); cache.parent.rmdir(); cache.parent.symlink_to(external)
    else:
        def fail(*a, **k): raise OSError('FAKE_SECRET_WRITE_FAILURE')
        monkeypatch.setattr(handoff_cursor.os, fault, fail)
    with pytest.raises(ValueError, match='^timer_snapshot_unavailable$'):
        admin_report.answer('timers')
    assert victim.read_bytes() == b'EXTERNAL_UNCHANGED'
    if fault in ('fsync', 'replace'):
        assert cache.read_bytes() == original
        assert sorted(p.name for p in cache.parent.iterdir()) == ['timers.json']


def test_inventory_does_not_record_timer_and_non_systemd_preserves_cache(credentials, systemd, monkeypatch):
    path, _, _ = credentials
    root = Path(report_http.load_credentials(path)[0]); cache = root / 'state/_admin/timers.json'
    admin_report.answer('inventory')
    assert not cache.exists()
    admin_report.answer('timers'); before = cache.read_bytes()
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda p: False if str(p) == '/run/systemd/system' else real_is_dir(p))
    result = admin_report.answer('timers')
    assert result['by_account']['first']['timer_reason'] == 'systemd_unavailable'
    assert cache.read_bytes() == before
