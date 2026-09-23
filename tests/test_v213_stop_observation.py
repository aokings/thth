"""Unsafe stop storage is observable without weakening the fail-closed gate."""
import json
import os
from pathlib import Path
import pytest
from thth import accounts,cli,doctor,leave_gate,runs,stop_observation


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path.resolve()/'root';root.mkdir(mode=0o700)
    for n in ('accounts','state','repos'):(root/n).mkdir(mode=0o755)
    (root/'accounts/alpha.json').write_text('{}')
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    return root


def snapshot(root):
    return {str(p.relative_to(root)):(p.lstat().st_mode,p.read_bytes() if p.is_file() and not p.is_symlink() else None) for p in root.rglob('*')}


@pytest.mark.parametrize('state,expected',[('stopped','account_stopped'),('unsafe','account_stop_state_unreadable')])
def test_run_reason_and_no_side_effect(env,monkeypatch,capsys,state,expected):
    if state=='stopped':
        d=env/'state/_leave';d.mkdir(mode=0o700);(d/'alpha.json').write_text('{}')
    else:(env/'state').chmod(0o775)
    before=snapshot(env)
    monkeypatch.setattr('thth.core.throw_once',lambda *a,**kw:pytest.fail('provider/throw'))
    monkeypatch.setenv('HEALTHCHECK_URL','https://healthcheck.invalid/private')
    monkeypatch.setattr('thth.healthcheck.notify',lambda *a,**kw:pytest.fail('healthcheck notification'))
    monkeypatch.setattr('thth.incident.notify',lambda *a,**kw:pytest.fail('incident notification'))
    assert cli.main(['run','alpha'])==2
    shown=capsys.readouterr();assert __import__('tests.conftest').conftest.is_refusal(shown.err, expected)  # 3.1.2 §3.5・3.3.0 B3: 断りの理由行の後ろに打てる報告の 1 行
    value=json.loads(shown.out)
    assert value=={'account':'alpha','mode':'production','action':'skip','status':'error','error':expected,
                   'runs_recorded':False,'record_unavailable':expected}
    assert str(env) not in shown.out and '775' not in shown.out
    assert snapshot(env)==before


@pytest.mark.parametrize('directory',['state','repos','accounts'])
def test_doctor_775_static_mode_warning_no_probe_or_repair(env,monkeypatch,capsys,directory):
    (env/directory).chmod(0o775);before=snapshot(env)
    monkeypatch.setattr(accounts,'load_token',lambda *a:pytest.fail('token read'))
    assert cli.main(['doctor','alpha','--json'])==2
    out=capsys.readouterr();value=json.loads(out.out)
    row=next(r for r in value['directory_checks'] if r['directory']==directory)
    assert row=={'directory':directory,'present':True,'mode':'0775','warning':'unsafe_directory_mode'}
    assert value['error']==('account_stop_state_unreadable' if directory=='state' else 'unsafe_directory_mode')
    assert value['probes']==[] and str(env) not in out.out and snapshot(env)==before


@pytest.mark.parametrize('state,expected',[('stopped','account_stopped'),('unsafe','account_stop_state_unreadable')])
def test_board_stop_row_never_looks_healthy(env,monkeypatch,state,expected):
    from thth import report
    if state=='stopped':
        d=env/'state/_leave';d.mkdir(mode=0o700);(d/'alpha.json').write_text('{}')
    else:(env/'state').chmod(0o775)
    before=snapshot(env);value=report.board_summary();row=value['accounts'][0]
    assert row=={'account':'alpha','error':expected,'stop_reason':expected,'cannot_say':[expected]}
    assert 'running' not in row and snapshot(env)==before


def test_static_safe_missing_and_symlink_categories(env):
    assert all(not r['warning'] for r in stop_observation.directories())
    (env/'repos').rmdir();row=next(r for r in stop_observation.directories() if r['directory']=='repos')
    assert row['present'] is False and row['warning'] is None
    outside=env.parent/'outside';outside.mkdir();(env/'repos').symlink_to(outside)
    row=next(r for r in stop_observation.directories() if r['directory']=='repos');assert row['warning']=='unsafe_directory_type'


def test_stop_reason_allowlist_and_adapter_error_preserved(env):
    from thth.adapters.base import Post
    class Fake:
        def publish(self,*a,**kw):pytest.fail('publish called')
    adapter=leave_gate.bind(Fake(),{'account':'alpha'});(env/'state').chmod(0o775)
    result=adapter.publish(Post('body'),dry_run=False)
    assert result.failure=='publish_vetoed' and result.error=='account_stop_state_unreadable'
    assert stop_observation.reason(accounts.AccountStopped('private arbitrary exception'))=='account_stopped'


def test_runs_does_not_bypass_unsafe_storage(env):
    (env/'state').chmod(0o775);before=snapshot(env)
    with pytest.raises((OSError,ValueError,accounts.AccountStopped)):
        runs.append_run(str(env/'state/alpha'),{'account':'alpha','run_id':'test','mode':'production','action':'skip','status':'error','error':'account_stop_state_unreadable'},'2030-01')
    assert snapshot(env)==before


def test_safe_runs_record_keeps_static_unreadable_reason(env):
    record={'account':'alpha','run_id':'test','mode':'production','action':'skip','status':'error','error':'account_stop_state_unreadable'}
    path=runs.append_run(str(env/'state/alpha'),record,'2030-01')
    value=json.loads(Path(path).read_text());assert value['error']=='account_stop_state_unreadable' and value['action']=='skip'


def test_doctor_static_warning_still_respects_live_exclusive_leave(env,monkeypatch,capsys):
    (env/'repos').chmod(0o775);before=snapshot(env)
    def busy(account):
        assert account=='alpha'
        raise accounts.AccountLeaving('account_leaving')
    monkeypatch.setattr(leave_gate,'check_busy',busy)
    assert cli.main(['doctor','alpha','--json'])==2
    assert json.loads(capsys.readouterr().out)=={'error':'account_leaving','cannot_say':['account_leaving']}
    assert snapshot(env)==before


@pytest.mark.parametrize('state,expected',[('stopped','account_stopped'),('unsafe','account_stop_state_unreadable')])
def test_direct_doctor_static_reason_has_no_probe(env,monkeypatch,state,expected):
    if state=='stopped':
        d=env/'state/_leave';d.mkdir(mode=0o700);(d/'alpha.json').write_text('{}')
    else:(env/'state').chmod(0o775)
    before=snapshot(env)
    monkeypatch.setattr(accounts,'load_token',lambda *a:pytest.fail('token read'))
    result=doctor.diagnose('alpha')
    assert result['error']==expected and result['probes']==[]
    assert snapshot(env)==before


def test_doctor_static_mode_with_real_other_process_exclusive(env,capsys):
    from tests.test_v212_read_coordination import exclusive
    with exclusive():
        (env/'repos').chmod(0o775);before=snapshot(env)
        assert cli.main(['doctor','alpha','--json'])==2
        assert json.loads(capsys.readouterr().out)=={'error':'account_leaving','cannot_say':['account_leaving']}
        assert snapshot(env)==before
