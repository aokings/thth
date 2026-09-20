"""A successful effect followed by failed completion storage stays unknown."""
import json
import subprocess
import pytest
from thth import approval_jobs as jobs
from tests.test_v212_server_writes import env, remote, publisher, writes, draft


@pytest.mark.parametrize('kind',['approve','send','retract'])
def test_completion_save_failure_is_unknown_and_restart_never_replays(env,remote,publisher,monkeypatch,kind):
    if kind=='approve':
        one=draft(env);request=dict(operation='approval_request',account='alpha',draft_id=one['draft_id'])
    elif kind=='retract':
        initial=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='first'))
        remote.approve();jobs.run_once(env['path'])
        assert jobs.status(env['context'],'alpha',initial['job_id'])['status']=='completed'
        request=dict(operation='retract_request',account='alpha',post_id='123456',reason='correction')
    else:request=dict(operation='send_request',account='alpha',body='first')
    result=writes.execute(env['context'],request);remote.approve()
    path=jobs.directory('alpha')/(result['job_id']+'.json');real_save=jobs._save;faults=[]
    def save(fd,job):
        if job['job_id']==result['job_id'] and job['status']=='completed' and not faults:
            assert json.loads(path.read_text())['status']=='executing'
            faults.append('completion_save');raise OSError('synthetic completion storage fault')
        return real_save(fd,job)
    monkeypatch.setattr(jobs,'_save',save)
    jobs.run_once(env['path'])
    status=jobs.status(env['context'],'alpha',result['job_id'])
    assert faults==['completion_save'] and status['status']=='unknown'
    assert status['reason']=='operation_outcome_unknown'
    if kind=='approve':
        repo=env['root']/'repos/_server/alpha'
        assert 'status: approved' in next((repo/'queue').glob('*.md')).read_text()
        before=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'])
    elif kind=='send':assert publisher==[('publish','first')]
    else:assert publisher==[('publish','first'),('delete','123456')]
    calls=list(publisher);consumes=remote.consumes;raw=path.read_bytes()
    # A new worker invocation reads the persisted unknown and performs no retry.
    monkeypatch.setattr(jobs,'_save',real_save)
    jobs.run_once(env['path']);jobs.run_once(env['path'])
    assert publisher==calls and remote.consumes==consumes and path.read_bytes()==raw
    if kind=='approve':assert subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'])==before
