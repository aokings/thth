"""Corrupt private storage never authorizes work or redirects managed Git."""
import copy
import hashlib
import json
import os
import shutil
import stat
from types import SimpleNamespace
import pytest
from thth import worker as jobs, managed_repo, server_files
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


# 3.13.0: 承認 job は作らない。3.12.0 までに残った job は常駐の起動時に expired にして残す（消さない）。
def leftover(env,job_id,status,**extra):
    directory=jobs.directory('alpha');directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.chmod(0o700)
    path=directory/(job_id+'.json')
    path.write_text(json.dumps(dict(schema_version=1,job_id=job_id,account='alpha',kind='send',status=status,**extra)));path.chmod(0o600)
    return path


def test_leftover_approval_jobs_are_expired_and_kept(env,remote,publisher):
    pending=leftover(env,'p'*43,'pending',token='t'*43,read_key='r'*43)
    ready=leftover(env,'r'*43,'ready')
    done=leftover(env,'c'*43,'completed',post_id='1')
    broken=jobs.directory('alpha')/('b'*43+'.json');broken.write_bytes(b'{');broken.chmod(0o600)
    before={p:p.read_bytes() for p in (done,broken)}
    assert jobs.expire_leftover_jobs()==2
    for path in (pending,ready):
        value=json.loads(path.read_text());assert value['status']=='expired' and value['reason']=='approval_page_removed'
        assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert json.loads(pending.read_text())['token']=='t'*43, 'kept as a record'
    assert {p:p.read_bytes() for p in (done,broken)}==before
    assert jobs.expire_leftover_jobs()==0
    assert publisher==[] and all(kind!='session' for kind,_ in remote.calls)


def test_worker_start_expires_leftover_jobs_without_any_session_call(env,remote,publisher):
    pending=leftover(env,'q'*43,'pending')
    assert jobs.command(SimpleNamespace(once=True,credentials=str(env['path'])))==0
    assert json.loads(pending.read_text())['status']=='expired'
    assert publisher==[] and all(kind!='session' for kind,_ in remote.calls)


def test_storage_walk_ignores_entries_that_vanish(env,monkeypatch):
    # git の自動 gc が作る短命な maintenance.lock が listdir と stat の間に消えても
    # 検証は落ちない（CI で実際に競合した）。危険な実在 entry の拒否は上の試験のまま。
    draft(env)
    clone,origin=managed_repo.locations('alpha')
    real=os.listdir
    monkeypatch.setattr(managed_repo.os,'listdir',lambda fd:real(fd)+['maintenance.lock'] if isinstance(fd,int) else real(fd))
    assert managed_repo.validate(clone)==(clone,origin)
    response=writes.execute(env['context'],dict(operation='draft_put',account='alpha',body='second body',publish_at='2030-01-01T12:00:00+09:00'))
    assert response['account']=='alpha'
