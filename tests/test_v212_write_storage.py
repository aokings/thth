"""Corrupt private storage never authorizes work or redirects managed Git."""
import copy
import hashlib
import json
import os
import shutil
import stat
from types import SimpleNamespace
import pytest
from thth import approval_jobs as jobs, managed_repo, server_files
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env, remote, publisher, draft, writes


def tree_bytes(path):
    return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('store',['clone','bare'])
@pytest.mark.parametrize('entry',['objects','refs','HEAD','config','packed-refs','index','logs/HEAD','objects/info/commit-graph'])
@pytest.mark.parametrize('attack',['symlink','hardlink','fifo'])
def test_git_store_redirects_refused_before_any_write(env,tmp_path,store,entry,attack):
    draft(env)
    clone,origin=managed_repo.locations('alpha');base=clone/'.git' if store=='clone' else origin
    target=base/entry;target.parent.mkdir(parents=True,exist_ok=True)
    outside=tmp_path/'outside';outside.mkdir()
    if target.is_dir():
        if attack=='hardlink':
            # A regular leaf inside the directory can alias an external inode.
            target=target/'aliased';source=outside/'value';source.write_bytes(b'outside')
        else:
            shutil.move(str(target),str(outside/'store'));source=outside/'store'
    else:
        source=outside/'value';source.write_bytes(target.read_bytes() if target.exists() else b'outside')
        target.unlink(missing_ok=True)
    if attack=='symlink':target.symlink_to(source,target_is_directory=source.is_dir())
    elif attack=='hardlink':os.link(source,target)
    else:os.mkfifo(target,0o600)
    before=tree_bytes(outside)
    with pytest.raises((OSError,ValueError)):managed_repo.validate(clone)
    with pytest.raises(ReportServiceError):
        writes.execute(env['context'],dict(operation='draft_put',account='alpha',body='second body',publish_at='2030-01-01T12:00:00+09:00'))
    assert tree_bytes(outside)==before
    assert len(list((clone/'queue').glob('*.md')))==1


def created(env):
    response=writes.execute(env['context'],dict(operation='send_request',account='alpha',body='synthetic body'))
    path=jobs.directory('alpha')/(response['job_id']+'.json')
    return path,json.loads(path.read_text())


CORRUPT=[('status',[]),('status',{}),('status',None),('status',3),('status','invented'),
 ('schema_version',True),('expires_at',True),('expires_at','future'),('token',{}),('read_key',[]),
 ('account','beta'),('kind','retract'),('actor','other'),('via',[]),('credential_digest',False),
 ('request',[]),('binding',None),('digest','0'*64),('reason',{'secret':'opaque'}),('post_id',{}),
 ('request.account','beta'),('request.operation','retract_request'),('request.body',{}),
 ('binding.actor','other'),('binding.kind','approve'),('binding.account','beta'),
 ('binding.text',[]),('binding.context',[]),('binding.context.media',{}),
 ('binding.context.options',{}),('binding.source',{}),('binding.ledger',[]),('binding.credential_generation',None)]


@pytest.mark.parametrize('field,value',CORRUPT)
def test_corrupt_job_refused_and_worker_continues(env,remote,publisher,capsys,field,value):
    path,bad=created(env);good_path,good=created(env)
    node=bad;parts=field.split('.')
    for part in parts[:-1]:node=node[part]
    node[parts[-1]]=value
    path.write_text(json.dumps(bad));before=path.read_bytes()
    remote.approve()
    # Actual command dispatch must continue after the bad file, with no traceback.
    assert jobs.command(SimpleNamespace(credentials=str(env['path']),once=True))==0
    assert path.read_bytes()==before
    assert json.loads(good_path.read_text())['status']=='completed'
    assert publisher==[('publish','synthetic body')] and remote.consumes==1
    with pytest.raises(ReportServiceError):jobs.status(env['context'],'alpha',bad['job_id'])
    assert capsys.readouterr()==('','')


@pytest.mark.parametrize('field',['job_id','digest','account','kind','approver','generation','approved_at','expires_at'])
def test_corrupt_ready_receipt_cannot_publish(env,remote,publisher,field):
    path,job=created(env);remote.approve()
    receipt=remote('session',job['token'],'consume',{'read_key':job['read_key']})
    job.update(status='ready',receipt=receipt);job['receipt'][field]=False
    path.write_text(json.dumps(job));before=path.read_bytes()
    jobs.run_once(env['path'])
    assert publisher==[] and path.read_bytes()==before


def test_expired_ready_receipt_never_publishes(env,remote,publisher,monkeypatch):
    path,job=created(env);remote.approve()
    job.update(status='ready',receipt=remote('session',job['token'],'consume',{}))
    path.write_text(json.dumps(job));monkeypatch.setattr(jobs.time,'time',lambda:job['expires_at']/1000)
    jobs.run_once(env['path']);assert publisher==[]
    assert json.loads(path.read_text())['status']=='expired'


@pytest.mark.parametrize('raw',[b'null',b'[]',b'3',b'{',b'['*1500+b'0'+b']'*1500])
def test_unreadable_job_kept_and_next_valid_job_runs(env,remote,publisher,capsys,raw):
    path,bad=created(env);good_path,_=created(env);path.write_bytes(raw);remote.approve()
    assert jobs.command(SimpleNamespace(credentials=str(env['path']),once=True))==0
    assert path.read_bytes()==raw and json.loads(good_path.read_text())['status']=='completed'
    assert publisher==[('publish','synthetic body')] and capsys.readouterr()==('','')


@pytest.mark.parametrize('field',['schema_version','job_id','token','read_key','account','kind','actor','credential_digest',
                                 'request','binding','digest','status','expires_at','via'])
def test_missing_required_job_field_no_remote_or_effect(env,remote,publisher,field):
    path,job=created(env);del job[field];path.write_text(json.dumps(job));before=path.read_bytes();remote.approve()
    jobs.run_once(env['path'])
    assert path.read_bytes()==before and publisher==[] and remote.consumes==0


def test_future_ready_receipt_no_effect(env,remote,publisher):
    path,job=created(env);remote.approve()
    job.update(status='ready',receipt=remote('session',job['token'],'consume',{}))
    job['receipt']['approved_at']=job['expires_at']-1
    path.write_text(json.dumps(job));jobs.run_once(env['path'])
    assert publisher==[]
    assert json.loads(path.read_text())['status']=='failed'
