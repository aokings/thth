import json
import pytest
from thth import cli, who_cli

@pytest.mark.parametrize('args',[['one','@a','@b'],['--project','p','@a','@b']])
def test_multiple_people_are_explicitly_refused(args,monkeypatch,capsys):
    monkeypatch.setattr(who_cli,'answer',lambda **kw:pytest.fail('must not silently select one'))
    assert cli.main(['who',*args,'--json'])==2
    output=capsys.readouterr()
    assert output.out=='' and '2 人' in output.err and '1 度に 1 人' in output.err

@pytest.mark.parametrize('args',[['one','@a'],['--project','p','@a']])
def test_single_person_contract_is_unchanged(args,monkeypatch,capsys):
    calls=[]
    def answer(**kw):calls.append(kw);return {'met':0}
    monkeypatch.setattr(who_cli,'answer',answer)
    assert cli.main(['who',*args,'--json'])==0
    assert json.loads(capsys.readouterr().out)=={'met':0} and calls[0]['username']=='@a'
