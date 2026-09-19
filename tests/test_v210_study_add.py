import json
from pathlib import Path
import subprocess
import pytest
from thth import cli, study_cli, study_report
from tests.conftest import write_queue_file


def declaration(cfg):
    path=Path(cfg['repo_dir'])/'study.json'
    value=dict(schema_version=1,id='tea',account=cfg['name'],hypothesis='question',change='opening',decision={'status':'proposed'},baseline_post_ids=[],changed_post_ids=[])
    path.write_text(json.dumps(value));return path


def test_both_groups_and_queue_id_without_git_or_adoption(isolated_account_factory,capsys):
    cfg=isolated_account_factory('one');path=declaration(cfg)
    queue=write_queue_file(cfg['queue_dir'],'sent.md',fm_overrides={'account':'one','status':'posted','post_id':'CHANGED'})
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=cfg['repo_dir'])
    before=Path(queue).read_bytes()
    assert cli.main(['study','add',str(path),'BASE','--baseline','--by','tester','--json'])==0
    assert json.loads(capsys.readouterr().out)['group']=='baseline_post_ids'
    assert cli.main(['study','add',str(path),queue,'--by','tester','--json'])==0
    assert json.loads(capsys.readouterr().out)['post_id']=='CHANGED'
    result=json.loads(path.read_text())
    assert result['baseline_post_ids']==['BASE'] and result['changed_post_ids']==['CHANGED']
    assert result['decision']=={'status':'proposed'} and Path(queue).read_bytes()==before
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=cfg['repo_dir'])==revision
    unchanged=path.read_bytes()
    assert study_cli.add(path,'CHANGED',by='tester')['added'] is False
    assert path.read_bytes()==unchanged

@pytest.mark.parametrize('case',['no_by','bad_actor','missing_id','other_account','both_groups','fsync'])
def test_rejections_preserve_original_bytes(case,isolated_account_factory,monkeypatch):
    cfg=isolated_account_factory('one');path=declaration(cfg);target='POST';by='tester'
    if case=='no_by':by=None
    elif case=='bad_actor':by='tester\nforged'
    elif case in ('missing_id','other_account'):
        target=write_queue_file(cfg['queue_dir'],'draft.md',fm_overrides={'account':'other' if case=='other_account' else 'one','post_id':'POST' if case=='other_account' else None})
    elif case=='both_groups':study_cli.add(path,'POST',baseline=True,by=by)
    elif case=='fsync':
        def fail(fd):raise OSError('injected fsync failure')
        monkeypatch.setattr(study_cli.os,'fsync',fail)
    before=path.read_bytes()
    with pytest.raises((ValueError,OSError)):study_cli.add(path,target,by=by)
    assert path.read_bytes()==before and not list(path.parent.glob('.study.json.*'))


def test_bad_declaration_cannot_be_repaired_by_silent_write(isolated_account_factory):
    cfg=isolated_account_factory('one');path=declaration(cfg);path.write_text('{broken')
    with pytest.raises(study_report.StudyError):study_cli.add(path,'POST',by='tester')
    assert path.read_text()=='{broken'
