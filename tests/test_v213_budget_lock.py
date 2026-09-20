"""Budget-local contention waits; body and external operations never retry."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import pytest
from thth import budget_x as b,server_files
from tests.test_v212_budget import env,setcap


def test_contender_after_reservation_does_not_abort_owner(env,monkeypatch):
    setcap('0.010');original=server_files.lock_at;busy=threading.Event();released=[]
    @contextlib.contextmanager
    def observe(*a,**kw):
        try:
            with original(*a,**kw):yield
        except BlockingIOError:busy.set();raise
    monkeypatch.setattr(server_files,'lock_at',observe)
    code="""from thth import budget_x as b
import sys
with b.locked():
 print('held',flush=True);assert sys.stdin.readline().strip()=='release'
try:
 with b.user_read('alpha'):pass
except b.BudgetError as exc:print(str(exc),flush=True)
"""
    with b.user_read('alpha'):
        child=subprocess.Popen([sys.executable,'-B','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        assert child.stdout.readline().strip()=='held'
        def release():
            assert busy.wait(2);child.stdin.write('release\n');child.stdin.flush();released.append(True)
        thread=threading.Thread(target=release);thread.start()
        b.before_post();b.before_get('/2/users/me');b.observed({'data':{'id':'one'}})
        thread.join(3);assert released and not thread.is_alive()
        out,err=child.communicate(timeout=3);assert child.returncode==0 and not err and out.strip()=='budget_exhausted'
    value=b.report();assert value['spent_estimate_usd']=='0.010' and value['held_usd']=='0'
    # Lock/FD released after success, not retained across provider/body boundaries.
    with b.locked():pass


def test_timeout_preserves_reserved_liability_and_bounds_retries(env,monkeypatch):
    setcap('0.010');clock=[0.0];attempts=[];sleeps=[];original=server_files.lock_at
    monkeypatch.setattr(b.time,'monotonic',lambda:clock[0])
    def sleep(delay):sleeps.append(delay);clock[0]+=delay
    monkeypatch.setattr(b.time,'sleep',sleep)
    @contextlib.contextmanager
    def blocked(*a,**kw):attempts.append(clock[0]);raise BlockingIOError(35,'synthetic');yield
    with b.user_read('alpha'):
        before=(b.folder()/'budget_x.json').read_bytes();monkeypatch.setattr(server_files,'lock_at',blocked)
        with pytest.raises(b.BudgetError,match='^budget_busy$'):b.before_post()
        assert clock[0]==5 and max(sleeps)<=.01 and max(attempts)<5
        assert (b.folder()/'budget_x.json').read_bytes()==before and b.report()['held_usd']=='0.010'
        monkeypatch.setattr(server_files,'lock_at',original)
    assert b.report()['held_usd']=='0'


def test_caller_blocking_error_runs_once_and_unlocks(env,monkeypatch):
    count=[];sleep=[];monkeypatch.setattr(b.time,'sleep',lambda delay:sleep.append(delay))
    with pytest.raises(BlockingIOError):
        with b.locked():count.append(True);raise BlockingIOError(35,'caller body')
    assert count==[True] and not sleep
    with b.locked():pass


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','mode'])
def test_special_lock_leaf_is_not_retried(env,monkeypatch,kind):
    setcap('1');leaf=b.folder()/'budget_x.lock';leaf.unlink();target=Path(os.environ['TMPDIR'])/'unrelated';target.write_bytes(b'');target.chmod(0o600)
    if kind=='symlink':leaf.symlink_to(target)
    elif kind=='hardlink':os.link(target,leaf)
    elif kind=='fifo':os.mkfifo(leaf)
    else:leaf.write_bytes(b'');leaf.chmod(0o644)
    monkeypatch.setattr(b.time,'sleep',lambda _:pytest.fail('invalid leaf retry'))
    with pytest.raises((OSError,ValueError)):
        with b.locked():pytest.fail('invalid lock accepted')
    assert target.read_bytes()==b''


def test_waiting_budget_keeps_account_lease_and_stop_afterward(env,monkeypatch):
    from thth import leave_gate as gate,accounts
    setcap('1');original=server_files.lock_at;busy=threading.Event();observations=[]
    @contextlib.contextmanager
    def observe(*a,**kw):
        try:
            with original(*a,**kw):yield
        except BlockingIOError:busy.set();raise
    monkeypatch.setattr(server_files,'lock_at',observe)
    code="""from thth import budget_x as b
import sys
with b.locked():print('held',flush=True);assert sys.stdin.readline().strip()=='release'
"""
    holder=subprocess.Popen([sys.executable,'-B','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    assert holder.stdout.readline().strip()=='held'
    stop_probe="""import os,fcntl
from thth import leave_gate as g
fd=os.open(g.location()/'alpha.lock',os.O_RDWR|os.O_NOFOLLOW)
try:
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('unexpected-exclusive')
 except BlockingIOError:print('busy')
finally:os.close(fd)
"""
    def release():
        assert busy.wait(2)
        p=subprocess.run([sys.executable,'-B','-c',stop_probe],capture_output=True,text=True,timeout=3)
        observations.append((p.returncode,p.stdout.strip(),p.stderr))
        holder.stdin.write('release\n');holder.stdin.flush()
    with gate.lease('alpha'):
        thread=threading.Thread(target=release);thread.start()
        with b.locked():pass
        thread.join(4);assert observations==[(0,'busy','')]
    out,err=holder.communicate(timeout=3);assert holder.returncode==0 and not err
    with gate.lease('alpha',exclusive=True):
        p=gate.location()/'alpha.json';p.write_text('{}');p.chmod(0o600)
    with pytest.raises(accounts.AccountStopped,match='account_stopped'):
        with gate.lease('alpha'):pytest.fail('stopped call')


@pytest.mark.parametrize('outcome',['normal','body_error','timeout'])
def test_lock_leaf_fds_close_on_every_exit(env,monkeypatch,outcome):
    import fcntl
    original=os.open;opened=[];clock=[0.0]
    def opening(path,*a,**kw):
        fd=original(path,*a,**kw)
        if path=='budget_x.lock':opened.append(fd)
        return fd
    monkeypatch.setattr(server_files.os,'open',opening)
    if outcome=='timeout':
        monkeypatch.setattr(b.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(b.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
        monkeypatch.setattr(server_files.fcntl,'flock',lambda *a:(_ for _ in ()).throw(BlockingIOError(35,'busy')))
        with pytest.raises(b.BudgetError,match='budget_busy'):
            with b.locked():pytest.fail('entered')
    elif outcome=='body_error':
        with pytest.raises(RuntimeError):
            with b.locked():raise RuntimeError('caller')
    else:
        with b.locked():pass
    assert opened
    for fd in set(opened):
        with pytest.raises(OSError):os.fstat(fd)
