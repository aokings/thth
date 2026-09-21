"""Reply management belongs on the final container, never carousel children."""
import pytest
from thth import core,media,media_delivery
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_threads_video import mp4


CONTROLS=['everyone','accounts_you_follow','mentioned_only','parent_post_author_only','followers_only']


@pytest.mark.parametrize('control',CONTROLS)
@pytest.mark.parametrize('approval',[False,True])
@pytest.mark.parametrize('shape',['text','image','video','carousel'])
def test_reply_management_exact_final_container(env,wire,control,approval,shape):
    (env[2]/'v.mp4').write_bytes(mp4())
    fm={'post_options':{'reply_control':control,'reply_approvals':approval}}
    if shape!='text':fm['media']=[{'file':'a.png' if shape=='image' else 'v.mp4','alt':'media'}]
    if shape=='carousel':fm['media'].append({'file':'a.png','alt':'second'})
    result,journal,_=invoke(env,fm=fm,post=base.Post('body',reply_to='789'))
    assert result.post_id and journal['media']['phase']=='published'
    created=posts(wire,'/threads');final=created[-1][2]
    assert final['reply_control']==[control] and final['enable_reply_approvals']==['true' if approval else 'false']
    assert final['reply_to_id']==['789']
    if shape=='carousel':assert all('reply_control' not in c[2] and 'enable_reply_approvals' not in c[2] for c in created[:-1])
    if shape=='text':assert not env[3]['upload'] and len(wire['calls'])==3


@pytest.mark.parametrize('control',['private','EVERYONE','',True])
def test_invalid_reply_control_never_coerced(env,wire,control):
    with pytest.raises(media.MediaError):media.manifest_for({'post_options':{'reply_control':control}},env[0])
    assert not wire['calls']


def test_reply_metadata_only_does_not_authorize_empty_body(env,wire):
    fm={'post_options':{'reply_approvals':False}}
    result,_,_=invoke(env,fm=fm,post=base.Post(''))
    assert result.error=='media_text_required' and not wire['calls']
    assert media_delivery.lint_notes(env[0],fm,text='')==['media_text_required']


def test_reply_options_change_digest_and_final_veto(env,wire):
    fm={'post_options':{'reply_approvals':False}};a=media.prepared_component(media.manifest_for(fm,env[0]))
    fm['post_options']['reply_approvals']=True
    assert a!=media.prepared_component(media.manifest_for(fm,env[0]))
    result,journal,_=invoke(env,fm=fm,veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.error=='approval_stale' and journal['media']['phase']=='held' and not posts(wire,'/threads_publish')


def test_unknown_reply_setting_publication_no_retry(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm={'post_options':{'reply_control':'followers_only'}})
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


def approved_options(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome,options,replacement,body="本文。"):
    """Actual local Git approval reusable by subsequent option-only contracts."""
    import json,secrets
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import accounts,jst,threadthrow
    from thth.adapters.threads import ThreadsAdapter
    declaration=json.dumps(options)
    if version==1:
        raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## threads\n\n'+body+'\n')
        raw=raw.replace('\n---\n','\npost_options: '+declaration+'\n---\n',1)
    else:
        raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
        for i in (1,2):raw+='  - index: '+str(i)+'\n    post_options: '+declaration+'\n'
        raw+='---\n## threads\n'+body+'\n<!-- thth: 2/2 -->\n'+body+' next\n'
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='threads',base_url=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    token={'access_token':secrets.token_urlsafe(30),'user_id':'123'};monkeypatch.setattr(accounts,'load_token',lambda _:token)
    adapter=ThreadsAdapter(base_url=wire['url'],access_token=token['access_token'],user_id='123',wait_seconds=0)
    if outcome=='stale':wire['on_create']=lambda:path.write_text(path.read_text().replace(declaration,json.dumps(replacement)))
    if version==1:core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    else:threadthrow.publish_bundle('alpha',str(path.relative_to(pair['work'])),adapter_factory=lambda *a:adapter,now=jst.parse('2030-01-01T12:01:00+09:00'),log=lambda _:None,max_posts=1)
    if outcome=='success':assert posts(wire,'/threads_publish')
    else:assert not posts(wire,'/threads_publish') and json.dumps(replacement) in path.read_text()


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('outcome',['success','stale'])
def test_actual_git_approved_reply_options(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome):
    approved_options(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome,
        {'reply_control':'followers_only','reply_approvals':False},{'reply_control':'everyone','reply_approvals':True})
    assert posts(wire,'/threads')[0][2]['reply_control']==['followers_only']
