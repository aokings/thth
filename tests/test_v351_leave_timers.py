"""3.5.1 件 1: 退出したら、その account の systemd unit を止める（報告の口 r20260923-653324b9）。

実測（09-23）: `thth account leave` 完了後も `thth@<name>.timer` が active のまま空回りし、
`thth admin timers` の記録にも残った。ここでは本物の sudo・systemctl を呼ばず、
`timer_cleanup._run`・`_which` を偽物に差し替えて:
- (a) sudo が通る → disabled に 2 unit・pending は空。再実行で sudo を叩き直さない。
- (b) sudo が通らない → 退出は completed のまま・pending に unit・打てる命令の 1 行。
- (c) unit が無い → sudo を呼ばない。systemctl が無い → 何も呼ばない。
- (d) `admin timers` が台帳の無い account の unit を `orphan_unit` と命令つきで出し、
  timers.json からその account の記録を消す。
"""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from thth import admin_report, handoff_cursor, leave, timer_cleanup
from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)

UNITS = ['thth@alpha.timer', 'thth-collect@alpha.timer']


class FakeSystem:
    """systemctl と sudo の偽物。呼ばれた命令を全部残す。"""

    def __init__(self, listed=(), sudo_rc=0, have=('systemctl', 'sudo')):
        self.listed, self.sudo_rc, self.have, self.calls = list(listed), sudo_rc, have, []
        self.state = 'enabled'

    def which(self, name):
        return '/usr/bin/' + name if name in self.have else None

    def run(self, args, **kwargs):
        self.calls.append(list(args))
        assert kwargs.get('timeout') and kwargs.get('stdin') is not None  # 待ち続けない・訊かれない
        if args[:2] == ['systemctl', 'list-unit-files']:
            patterns = args[4:]
            rows = [unit for unit in self.listed
                    if unit in patterns or any(Path(unit).match(p) for p in patterns)]
            out = ''.join(f'{unit} {self.state} enabled\n' for unit in rows)
            return SimpleNamespace(returncode=0 if rows else 1, stdout=out, stderr='')
        if args[0] == 'sudo':
            return SimpleNamespace(returncode=self.sudo_rc, stdout='',
                                   stderr='' if self.sudo_rc == 0 else 'sudo: a password is required')
        pytest.fail('unexpected command: %r' % (args,))

    def sudo_calls(self):
        return [call for call in self.calls if call[0] == 'sudo']


@pytest.fixture
def system(monkeypatch):
    def install(**kwargs):
        fake = FakeSystem(**kwargs)
        monkeypatch.setattr(timer_cleanup, '_run', fake.run)
        monkeypatch.setattr(timer_cleanup, '_which', fake.which)
        return fake
    return install


def test_a_sudoが通れば2つのunitを止めて再実行では叩かない(env, worker, system):
    fake = system(listed=UNITS)
    result = leave.run('alpha', by='operator')
    assert result['phase'] == 'completed'
    assert result['timers'] == {'disabled': UNITS, 'pending': [], 'reason': None}
    assert fake.sudo_calls() == [['sudo', '-n', 'systemctl', 'disable', '--now', *UNITS]]
    assert leave.read('alpha')['timers'] == result['timers']
    # 打ち直しても sudo は 1 回きり（記録を返すだけ）。
    assert leave.run('alpha', by='operator') == result
    assert len(fake.sudo_calls()) == 1


def test_b_sudoが通らなくても退出は完了し打てる命令を出す(env, worker, system):
    fake = system(listed=UNITS + ['thth-collect@alpha.service'], sudo_rc=1)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = leave.command(SimpleNamespace(name='alpha', by='operator', json=False))
    assert rc == 0
    row = leave.read('alpha')
    assert row['phase'] == 'completed'
    everything = UNITS + ['thth-collect@alpha.service']
    assert row['timers'] == {'disabled': [], 'pending': everything, 'reason': 'sudo_failed'}
    lines = out.getvalue().splitlines()
    assert lines[0] == 'local_complete: alpha'
    assert ('timer_cleanup_pending: sudo systemctl disable --now ' + ' '.join(everything)) in lines
    assert len(fake.sudo_calls()) == 1  # 1 回だけ試す


def test_b_sudoが無くても完了でjsonにも記録(env, worker, system):
    system(listed=UNITS, have=('systemctl',))
    out = io.StringIO()
    with redirect_stdout(out):
        assert leave.command(SimpleNamespace(name='alpha', by='operator', json=True)) == 0
    payload = json.loads(out.getvalue())
    assert payload['phase'] == 'completed'
    assert payload['timers'] == {'disabled': [], 'pending': UNITS, 'reason': 'sudo_unavailable'}


def test_c_unitが無ければsudoを呼ばない(env, worker, system):
    fake = system(listed=['thth@beta.timer'])
    result = leave.run('alpha', by='operator')
    assert result['timers'] == {'disabled': [], 'pending': [], 'reason': 'no_units'}
    assert fake.sudo_calls() == []
    assert [call[:2] for call in fake.calls] == [['systemctl', 'list-unit-files']]


def test_c_systemctlが無ければ何も呼ばない(env, worker, system):
    fake = system(have=())
    out = io.StringIO()
    with redirect_stdout(out):
        assert leave.command(SimpleNamespace(name='alpha', by='operator', json=False)) == 0
    assert fake.calls == []
    assert leave.read('alpha')['timers']['reason'] == 'systemctl_unavailable'
    assert 'timer_cleanup' not in out.getvalue()


def test_3_5_0の完了rowもそのまま読める(env, worker, system):
    system(have=())
    leave.run('alpha', by='operator')
    row = leave.read('alpha')
    del row['timers']
    leave._save(row)
    assert 'timers' not in leave.read('alpha')
    # 形の崩れた記録は読まない（他 account の unit・知らない理由）。
    for bad in ({'disabled': ['thth@beta.timer'], 'pending': [], 'reason': None},
                {'disabled': [], 'pending': [], 'reason': 'made_up'}):
        leave._save(dict(row, timers=bad))
        with pytest.raises(ValueError, match='leave_journal_unreadable'):
            leave.read('alpha')


@pytest.fixture
def systemd_show(monkeypatch):
    """`admin timers` の観測（`systemctl show`）の偽物。台帳のある account の分。"""
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda p: True if str(p) == '/run/systemd/system' else real_is_dir(p))

    def run(args, **kwargs):
        assert args[:2] == ['systemctl', 'show']
        if args[2].endswith('.service'):
            return SimpleNamespace(returncode=0, stdout='ExecMainStatus=0\n')
        return SimpleNamespace(returncode=0, stdout=f'Id={args[2]}\nLoadState=loaded\nActiveState=active\n')
    monkeypatch.setattr(admin_report.subprocess, 'run', run)


def _unit(name):
    return dict(unit=name, active='active', last_trigger=None, next_trigger=None, exec_main_status=0)


def test_d_admin_timersが台帳の無いaccountのunitをorphanとして出す(env, worker, system, systemd_show):
    handoff_cursor.write_snapshot('_admin', 'timers.json', dict(schema_version=1, by_account={
        'alpha': dict(observed_at='2026-09-23T12:00:00+09:00', timer_reason=None,
                      units=[_unit('thth@alpha.timer'), _unit('thth-maintain.timer')]),
        'beta': dict(observed_at='2026-09-23T12:00:00+09:00', timer_reason=None,
                     units=[_unit('thth@beta.timer')])}))
    system(listed=UNITS, sudo_rc=1)
    leave.run('alpha', by='operator')
    command = 'sudo systemctl disable --now ' + ' '.join(UNITS)

    # HTTP は前回の記録と退出の記録だけで言う（systemctl を呼ばない）。
    cached = admin_report.answer('timers', via='http')
    assert [row['account'] for row in cached['orphans']] == ['alpha']
    assert cached['orphans'][0]['reason'] == 'orphan_unit'
    assert cached['orphans'][0]['command'] == command
    assert cached['orphan_listing_reason'] == 'not_observed_here'

    report = admin_report.answer('timers')
    assert set(report['by_account']) == {'beta'}
    assert report['orphans'] == [{'account': 'alpha', 'units': UNITS, 'reason': 'orphan_unit',
                                  'sources': ['leave_pending', 'systemd', 'timers_snapshot'],
                                  'command': command}]
    assert report['orphan_listing_reason'] is None
    # 次の更新で timers.json から台帳の無い account の記録が消える。
    stored = json.loads((env['root'] / 'state/_admin/timers.json').read_text())
    assert set(stored['by_account']) == {'beta'}
    # 台帳のある account の unit は orphan にしない。
    assert all(row['account'] != 'beta' for row in report['orphans'])


def test_d_止めた後はorphanが消える(env, worker, system, systemd_show):
    fake = system(listed=UNITS)
    leave.run('alpha', by='operator')
    fake.state = 'disabled'  # 止めた後も unit file は残る
    report = admin_report.answer('timers')
    assert report['orphans'] == []
