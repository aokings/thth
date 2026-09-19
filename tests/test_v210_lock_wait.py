"""Real flock deadlines, rehearsal no-write, and read-side lock exclusion."""
import json
import subprocess
import sys
import time
from pathlib import Path
import pytest
from thth import accounts, cli, core, lock, runs


def files(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.startswith('runs-')}


@pytest.mark.parametrize('body', ['本文', 'x'*501, 'bad\x00text', ''])
def test_send_rehearsal_has_no_lock_or_sns_ledger_write(body, isolated_account_factory, monkeypatch):
    cfg=isolated_account_factory('readonly',production=True)
    root=Path(accounts.thth_root());before=files(root)
    monkeypatch.setattr(lock.AccountLock,'acquire',lambda self:pytest.fail('rehearsal acquired lock'))
    result=core.send_once('readonly',text=body,adapter_factory=lambda *a:pytest.fail('network'))
    assert result.action!='locked'
    assert files(root)==before
    recorded=runs.read_runs(accounts.state_dir_for('readonly'))
    assert len(recorded)==(0 if body=='' else 1)
    if recorded:
        assert recorded[0]['mode']=='rehearsal' and recorded[0]['action']=='skip'
        assert recorded[0]['post_id'] is None and recorded[0]['file'] is None


@pytest.mark.parametrize('wait,release,success', [(0,0.3,False),(0.04,0.3,False),(0.7,0.12,True)])
def test_real_flock_waits_until_release_or_deadline(tmp_path, wait, release, success):
    path=str(tmp_path/'lock')
    script='import fcntl,sys,time; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("held",flush=True); time.sleep(float(sys.argv[2]))'
    child=subprocess.Popen([sys.executable,'-c',script,path,str(release)],stdout=subprocess.PIPE,text=True)
    candidate=lock.AccountLock(path)
    try:
        assert child.stdout.readline().strip()=='held'
        start=time.monotonic()
        if success:
            lock.acquire(candidate,wait)
            assert time.monotonic()-start>=0.08
        else:
            with pytest.raises(lock.LockBusy,match='--wait'):
                lock.acquire(candidate,wait)
            assert wait<=time.monotonic()-start<0.25
    finally:
        candidate.release();child.wait(timeout=2)


@pytest.mark.parametrize('command,tail', [('send',['a']),('approve',['a.md']),('revoke',['a.md']),('replies',['a','--refresh']),('retract',['a','P'])])
def test_all_write_parsers_accept_wait_and_reject_invalid(command,tail):
    parser=cli.build_parser()
    assert parser.parse_args([command,*tail,'--wait','1.25']).wait==1.25
    for value in ('-1','nan','inf'):
        with pytest.raises(SystemExit):parser.parse_args([command,*tail,'--wait',value])


@pytest.mark.parametrize('command', ['after','replies','handoff-report','study-report','analytics-report','posts','measured','account','board'])
def test_local_reads_never_acquire_locks(command,isolated_account_factory,monkeypatch,capsys,tmp_path):
    isolated_account_factory('reader')
    monkeypatch.setattr(lock.AccountLock,'acquire',lambda self:pytest.fail('read acquired lock'))
    argv=[command,'reader','--json'] if command!='board' else ['board','--json']
    if command=='study-report':
        policy=tmp_path/'policy.json'
        policy.write_text(json.dumps({'schema_version':1,'id':'study','account':'reader','hypothesis':'h','change':'c','decision':{'status':'proposed'},'baseline_post_ids':[],'changed_post_ids':[]}))
        argv=[command,str(policy),'--json']
    assert cli.main(argv)==(1 if command=="posts" else 0)
    assert json.loads(capsys.readouterr().out) is not None


@pytest.mark.parametrize('command,tail', [('where',['reader','word']),('thread',['reader','123'])])
def test_network_reads_never_acquire_locks(command,tail,isolated_account_factory,monkeypatch,capsys):
    from thth import adapters
    cfg=isolated_account_factory('reader')
    Path(accounts.load_account('reader')['token']).write_text(json.dumps({'access_token':'FAKE_TOKEN'}))
    class ReadAdapter:
        def keyword_search(self,*a,**k):return []
        def fetch_post(self,pid):return {'id':pid,'text':'root','username':'other'}
        def conversation(self,*a,**k):return []
    monkeypatch.setattr(adapters,'make_adapter',lambda *a,**k:ReadAdapter())
    monkeypatch.setattr(lock.AccountLock,'acquire',lambda self:pytest.fail('read acquired lock'))
    assert cli.main([command,*tail,'--json'])==0
    assert json.loads(capsys.readouterr().out) is not None


def test_dry_send_while_other_process_holds_both_locks(isolated_account_factory):
    cfg=isolated_account_factory('held',production=True)
    script=('from thth.lock import AccountLock; import sys; '
            'a=AccountLock(sys.argv[1]);b=AccountLock(sys.argv[2]);'
            'a.acquire();b.acquire();print("held",flush=True);sys.stdin.readline();b.release();a.release()')
    paths=[accounts.repo_lock_path_for(cfg['repo_dir']),accounts.account_lock_path_for('held')]
    child=subprocess.Popen([sys.executable,'-c',script,*paths],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='held'
        before=files(Path(accounts.thth_root()))
        result=core.send_once('held',text='PRIVATE REHEARSAL BODY',log=lambda line:None)
        assert result.exit_code==0 and result.digest
        assert files(Path(accounts.thth_root()))==before
        rows=runs.read_runs(accounts.state_dir_for('held'))
        assert len(rows)==1 and rows[0]['action']=='skip' and rows[0]['mode']=='rehearsal'
        assert 'PRIVATE REHEARSAL BODY' not in json.dumps(rows)
    finally:
        child.communicate('release\n',timeout=2)
