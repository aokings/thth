import datetime
import json
from pathlib import Path
import pytest
from tests.test_thread_publish import thread_account, FakeAdapter, publish, bundle_text, write_and_push, NOW, REL, SEGMENTS
from tests.conftest import run_git, run_thth, approve_via_cli
from thth import bundle, threadrun, threadthrow, jst

def test_cli_throw_reaches_bundle(thread_account):
    current=jst.now_jst()
    write_and_push(thread_account['pair'],bundle_text(publish_at=(current-datetime.timedelta(minutes=1)).isoformat(),continue_until=(current+datetime.timedelta(hours=1)).isoformat()))
    r=run_thth(['throw',thread_account['account']['name']])
    print('CLI',r.returncode,r.stdout,r.stderr)
    assert '出すものが無い' not in r.stdout, 'Approved v2 bundle is invisible to normal throw entry'

@pytest.mark.parametrize('index',[1,3])
def test_remote_body_changed_during_publish(thread_account,tmp_path,index):
    pair=thread_account['pair']
    other=tmp_path/'other'
    run_git(str(tmp_path),['clone',pair['bare'],str(other)])
    run_git(str(other),['config','user.name','Reviewer'])
    run_git(str(other),['config','user.email','review@example.test'])
    class Racing(FakeAdapter):
        def publish(self,post,**kw):
            result=super().publish(post,**kw)
            if len(self.calls)==index:
                run_git(str(other),['pull','--ff-only'])
                p=other/REL
                p.write_text(p.read_text().replace(SEGMENTS[index-1],'REMOTE_BODY_B'))
                run_git(str(other),['add',REL]);run_git(str(other),['commit','-m','remote edit']);run_git(str(other),['push'])
            return result
    a=Racing();res=publish(thread_account,a)
    remote=run_git(pair['bare'],['show','HEAD:'+REL])
    print('REMOTE',index,[(x.action,x.reason) for x in res], 'mixed=', 'REMOTE_BODY_B' in remote.stdout and 'POST'+str(index) in remote.stdout)
    assert not ('REMOTE_BODY_B' in remote.stdout and 'POST'+str(index) in remote.stdout), 'Published A ID pushed alongside remote B body'

def test_deadline_checked_after_actual_adapter_wait(thread_account,monkeypatch):
    from thth.adapters.threads import ThreadsAdapter
    import thth.adapters.threads as module
    import inspect
    print('adapter signature',inspect.signature(ThreadsAdapter))
    clock=[NOW+datetime.timedelta(minutes=59,seconds=50)]
    monkeypatch.setattr(jst,'now_jst',lambda:clock[0])
    def wait(seconds): clock[0]+=datetime.timedelta(seconds=seconds)
    monkeypatch.setattr(module.time,'sleep',wait)
    adapter=ThreadsAdapter(user_id='fake-user',access_token='fake-token')
    calls=[]
    def fakepost(path,params):
        calls.append((path,clock[0].isoformat()))
        return {'id':'container'} if path.endswith('/threads') else {'id':'LATE_POST'}
    monkeypatch.setattr(adapter,'_post',fakepost)
    res=publish(thread_account,adapter,max_posts=1)
    print('DEADLINE',calls,[(x.action,x.reason) for x in res])
    assert not any(p.endswith('/threads_publish') for p,t in calls), 'Publish called after deadline during built-in wait'

def test_reapproval_updates_remaining_receipt(thread_account):
    a=FakeAdapter();first=publish(thread_account,a,max_posts=1)
    p=Path(thread_account['path']);text=p.read_text().replace(SEGMENTS[1],'NEW_SECOND_BODY')
    write_and_push(thread_account['pair'],text)
    approved=approve_via_cli(p)
    assert approved.returncode==0,approved.stdout+approved.stderr
    again=publish(thread_account,a,max_posts=1)
    row=threadrun.load(first[0].run_id)
    print('REAPPROVAL',[(r.action,r.reason) for r in again],row['posts'][1],bundle.parse(str(p)).posts[1])
    from thth.approval import segment_sha
    assert row['posts'][1]['text_sha256']==segment_sha('NEW_SECOND_BODY')

def test_expired_run_can_resume_after_reapproval(thread_account):
    a=FakeAdapter();first=publish(thread_account,a,max_posts=1)
    expired=publish(thread_account,a,now=NOW+datetime.timedelta(hours=2))
    p=Path(thread_account['path']);text=p.read_text().replace('2026-09-15T20:00:00+09:00','2026-09-16T20:00:00+09:00')
    write_and_push(thread_account['pair'],text)
    approved=approve_via_cli(p);assert approved.returncode==0,approved.stdout+approved.stderr
    resumed=publish(thread_account,a,now=NOW+datetime.timedelta(hours=2),max_posts=1)
    print('RESUME',[(r.action,r.reason) for r in resumed])
    assert resumed[0].action=='published'

def test_completed_bundle_rename_does_not_republish(thread_account):
    a=FakeAdapter();done=publish(thread_account,a)
    assert len(a.calls)==3
    work=thread_account['pair']['work'];new=REL.replace('thread.md','renamed.md')
    run_git(work,['mv',REL,new]);run_git(work,['commit','-m','rename only']);run_git(work,['push'])
    again=threadthrow.publish_bundle(thread_account['account']['name'],new,adapter_factory=lambda *_:a,now=NOW,max_posts=1)
    print('RENAME',[(r.action,r.reason) for r in again],len(a.calls))
    assert len(a.calls)==3, 'Renaming completed queue file republishes root'

def test_control_complete_bundle_not_republished(thread_account):
    a=FakeAdapter();publish(thread_account,a);publish(thread_account,a)
    assert len(a.calls)==3

def test_collect_sees_bundle_posts(thread_account):
    from thth import collect
    a=FakeAdapter();publish(thread_account,a)
    class Reader:
        def insights(self,pid): return {'views':10}
        def replies(self,pid): return []
        def account_insights(self,*args,**kwargs): return {}
    result=collect.collect_once(thread_account['account']['name'],adapter=Reader(),now=NOW+datetime.timedelta(hours=1))
    print('COLLECT',result)
    assert result['errors']==[]
    assert result['posts']==3, 'Actual collect path drops all v2 posts'
