import json
from pathlib import Path
import pytest
from thth import cli, queuefile
from tests.conftest import write_queue_file


def test_directory_account_filter_preserves_two_stage_scope(isolated_account_factory,capsys):
    cfg=isolated_account_factory('one');isolated_account_factory('two')
    one=write_queue_file(cfg['queue_dir'],'one.md',fm_overrides={'account':'one','status':'draft'})
    two=write_queue_file(cfg['queue_dir'],'two.md',fm_overrides={'account':'two','status':'draft'})
    previous=Path(two).read_bytes()
    assert cli.main(['approve',cfg['queue_dir'],'--account','one','--json'])==1
    result=json.loads(capsys.readouterr().out)
    assert len(result['files'])==1 and result['files'][0]['file']==one
    assert queuefile.parse(one).front_matter['status']=='draft'
    assert cli.main(['approve',cfg['queue_dir'],'--account','one','--confirm',result['bundle_digest'],'--by','tester','--json'])==0
    assert json.loads(capsys.readouterr().out)['count']==1
    assert queuefile.parse(one).front_matter['status']=='approved' and Path(two).read_bytes()==previous


def test_explicit_foreign_file_does_not_bypass_account(isolated_account_factory,capsys):
    cfg=isolated_account_factory('one');isolated_account_factory('two')
    path=write_queue_file(cfg['queue_dir'],'two.md',fm_overrides={'account':'two'})
    before=Path(path).read_bytes()
    assert cli.main(['approve',path,'--account','one','--json'])==1
    assert '指定 account' in capsys.readouterr().err and Path(path).read_bytes()==before


def test_status_filter_is_not_an_option():
    with pytest.raises(SystemExit) as exc:cli.main(['approve','queue','--status','posted'])
    assert exc.value.code==2
