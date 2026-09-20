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
