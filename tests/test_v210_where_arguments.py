import json
import pytest
from thth import cli, where_cli


@pytest.mark.parametrize('arguments,account,project,words',[
    (['one','tea','--recent','coffee'],'one',None,['tea','coffee']),
    (['one','--recent','tea','coffee'],'one',None,['tea','coffee']),
    (['--recent','one','tea','--limit','2','coffee'],'one',None,['tea','coffee']),
    (['--project','p','tea','--recent','coffee'],None,'p',['tea','coffee']),
    (['one','--word','tea','--word','coffee'],'one',None,['tea','coffee']),
    (['--word','tea','one','coffee'],'one',None,['coffee','tea']),
    (['--project','p','--word','tea','--word','coffee'],None,'p',['tea','coffee']),
])
def test_options_and_words_are_not_lost(arguments,account,project,words,monkeypatch,capsys):
    calls=[]
    def answer(**kwargs):calls.append(kwargs);return {'words':kwargs['words']}
    monkeypatch.setattr(where_cli,'answer',answer)
    assert cli.main(['where',*arguments,'--json'])==0
    assert calls[0]['account_name']==account and calls[0]['project']==project
    assert calls[0]['words']==words
    assert json.loads(capsys.readouterr().out)=={'words':words}


def test_unknown_options_are_not_reinterpreted_as_words(monkeypatch):
    monkeypatch.setattr(where_cli,'answer',lambda **kwargs:pytest.fail('invalid option reached API'))
    with pytest.raises(SystemExit):cli.main(['where','one','tea','--unknown','coffee'])
