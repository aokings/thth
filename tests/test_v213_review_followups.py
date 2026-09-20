"""Reacceptance followups: input meaning, stop directory facts, relay transport."""
import json
import pytest
from thth import accounts,cli,leave_gate


@pytest.mark.parametrize('name',['../x','../../etc/passwd','a/b','..','.','','x\x00y','日本語'])
def test_run_invalid_name_before_any_storage_or_effect(monkeypatch,capsys,name):
    def forbidden(*a,**kw):pytest.fail('invalid name reached storage/effect')
    for module,attr in [(leave_gate,'require_active'),(accounts,'state_dir_for'),(accounts,'load_account')]:
        monkeypatch.setattr(module,attr,forbidden)
    monkeypatch.setattr('thth.core.throw_once',forbidden)
    monkeypatch.setattr('thth.healthcheck.notify',forbidden)
    monkeypatch.setattr('thth.incident.notify',forbidden)
    assert cli.main(['run',name])==2
    captured=capsys.readouterr();value=json.loads(captured.out)
    assert captured.err.strip()=='invalid_account_name'
    assert value['error']==value['record_unavailable']=='invalid_account_name'
    assert value['account'] is None and value['runs_recorded'] is False
    assert '使えない字' in value['message'] and 'account_stopped' not in captured.out
    assert name not in captured.out if name and name not in ('..','.') else True


@pytest.mark.parametrize('directory',['state/_leave','state/_leave/coordination'])
def test_doctor_actual_stop_directories_warn_without_write(tmp_path,monkeypatch,capsys,directory):
    from thth import stop_observation
    root=tmp_path.resolve()/'root';(root/'state/_leave/coordination').mkdir(parents=True,mode=0o700)
    (root/'state/_leave').chmod(0o700);(root/directory).chmod(0o775)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    before={str(p.relative_to(root)):p.lstat().st_mode for p in root.rglob('*')}
    monkeypatch.setattr(accounts,'load_token',lambda *a:pytest.fail('token read'))
    assert cli.main(['doctor','alpha','--json'])==2
    captured=capsys.readouterr();value=json.loads(captured.out)
    row=next(r for r in value['directory_checks'] if r['directory']==directory)
    assert row['warning']=='unsafe_directory_mode' and row['present'] is True
    assert value['probes']==[] and str(root) not in captured.out
    assert before=={str(p.relative_to(root)):p.lstat().st_mode for p in root.rglob('*')}


def test_doctor_does_not_follow_stop_directory_symlink(tmp_path,monkeypatch):
    from thth import stop_observation
    root=tmp_path.resolve()/'root';(root/'state').mkdir(parents=True)
    outside=tmp_path.resolve()/'outside';(outside/'coordination').mkdir(parents=True)
    (root/'state/_leave').symlink_to(outside,target_is_directory=True)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    rows={r['directory']:r for r in stop_observation.directories()}
    assert rows['state/_leave']['warning']=='unsafe_directory_type'
    assert rows['state/_leave/coordination']['warning']=='directory_unreadable'
    assert rows['state/_leave/coordination']['mode'] is None
