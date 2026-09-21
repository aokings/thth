"""Text link attachment: same approval intent, no R2 and no URL fetch."""
import copy
import json
import pytest
from thth import accounts,core,media,media_delivery,media_relay
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts


def link(url='https://outside.invalid/article'):return {'attachments':[{'type':'link','url':url}]}


@pytest.mark.parametrize('text',['本文','', 'https://outside.invalid/article'])
def test_text_link_exact_wire_without_relay_or_origin_fetch(env,wire,monkeypatch,text):
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('fileless R2 constructor'))
    result,journal,_=invoke(env,fm=link(),post=base.Post(text,reply_to='777',topic='話題',location_id='888',share_to_instagram=True))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    creates=posts(wire,'/threads');assert len(creates)==1
    got=creates[0][2]
    assert got['media_type']==['TEXT'] and got['text']==[text] and got['link_attachment']==['https://outside.invalid/article']
    assert got['reply_to_id']==['777'] and got['topic_tag']==['話題'] and got['location_id']==['888'] and got['crossreshare_to_ig']==['true']
    assert not any(k in got for k in ('image_url','video_url','children','auto_publish_text'))
    assert len(posts(wire,'/threads_publish'))==1 and len(wire['calls'])==3
    assert env[3]['upload']==env[3]['grants']==env[3]['results']==[] and wire['fetched']==[]


@pytest.mark.parametrize('count,ok',[(4,True),(5,False)])
def test_unique_link_limit_and_lint_before_http(env,wire,count,ok):
    text=' '.join('https://outside.invalid/'+str(i) for i in range(count))
    fm=link();notes=media_delivery.lint_notes(env[0],fm,text=text)
    result,_,_=invoke(env,fm=fm,post=base.Post(text))
    assert bool(result.post_id)==ok
    if ok:assert notes==[]
    else:assert notes==['media_limit_exceeded: links'] and result.error==notes[0] and wire['calls']==[]


def test_same_link_counts_once_and_five_is_permitted(env,wire):
    url='https://outside.invalid/article';text=' '.join([url,url]+['https://outside.invalid/'+str(i) for i in range(4)])
    result,_,_=invoke(env,fm=link(),post=base.Post(text));assert result.post_id


@pytest.mark.parametrize('key,value',[('title','x'),('description','x'),('associated_refs',[])])
def test_card_fields_are_never_silently_discarded(env,wire,key,value):
    fm=link();fm['attachments'][0][key]=value
    try:result,_,_=invoke(env,fm=fm)
    except media.MediaError:pass
    else:assert result.error=='unsupported_attachment: threads/link_option'
    assert wire['calls']==[]


def test_link_is_text_only_not_a_hidden_image_option(env,wire):
    fm=link();fm['media']=[{'file':'a.png','alt':'x'}]
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: threads/link_requires_text' and wire['calls']==[] and env[3]['upload']==[]


@pytest.mark.parametrize('moment',['before','ready'])
def test_final_veto_has_no_publish_and_preserves_container(env,wire,moment):
    reason=lambda:'approval_stale' if moment=='before' or wire['calls'] else None
    result,journal,_=invoke(env,fm=link(),veto=reason)
    assert not result.post_id and result.error=='approval_stale' and not posts(wire,'/threads_publish')
    if moment=='before':assert wire['calls']==[]
    else:assert journal['media']['phase']=='held' and journal['media']['remote_ids'] and result.failure=='media_held'


@pytest.mark.parametrize('where',['create','publish'])
def test_unknown_post_is_durable_and_not_replayed(env,wire,where):
    wire[where]=(500,{})
    result,journal,_=invoke(env,fm=link())
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    before=len(wire['calls'])
    again=core.send_once('alpha',text='本文',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==before


@pytest.mark.parametrize('status',['ERROR','EXPIRED'])
def test_known_container_failure_is_held(env,wire,status):
    wire['poll']=[(200,{'status':status})]
    result,journal,_=invoke(env,fm=link())
    assert result.failure=='media_held' and journal['media']['phase']=='held' and journal['media']['remote_ids']
    assert not posts(wire,'/threads_publish') and env[3]['results']==[]


def test_link_target_changes_approval(env):
    a=media.manifest_for(link(),env[0]);b=media.manifest_for(link('https://outside.invalid/changed'),env[0])
    assert media.prepared_component(a)!=media.prepared_component(b)


@pytest.mark.parametrize('outcome',['success','stale','unknown'])
def test_actual_git_link_approval_to_wire(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import queuefile
    from thth.adapters.threads import ThreadsAdapter
    import secrets
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## threads\n\nリンク本文。\n')
    raw=raw.replace('\n---\n','\nattachments: '+json.dumps(link()['attachments'])+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='threads',base_url=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    assert wire['calls']==[]
    token={'access_token':secrets.token_urlsafe(30),'user_id':'123'}
    monkeypatch.setattr(accounts,'load_token',lambda _:token)
    adapter=ThreadsAdapter(base_url=wire['url'],access_token=token['access_token'],user_id='123',wait_seconds=0)
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('fileless relay'))
    if outcome=='stale':wire['on_create']=lambda:path.write_text(path.read_text().replace('outside.invalid/article','outside.invalid/changed'))
    if outcome=='unknown':wire['publish']=(500,{})
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        assert posts(wire,'/threads')[0][2]['link_attachment']==['https://outside.invalid/article']
    else:
        assert result.exit_code!=0 and queuefile.parse(str(path)).get('status')=='approved'
        if outcome=='stale':assert result.error=='approval_stale' and not posts(wire,'/threads_publish')
        count=len(wire['calls']);again=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
        if outcome=='unknown':assert again.action=='inflight'
        assert len(wire['calls'])==count


def test_v2_link_intents_lint_and_digest(env,wire):
    from thth import bundle,lint
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    attachments: '+json.dumps(link()['attachments'])+'\n'
    raw+='  - index: 2\n    attachments: '+json.dumps(link('https://outside.invalid/second')['attachments'])+'\n---\n## threads\n一段目\n<!-- thth: 2/2 -->\n二段目\n'
    path=env[2]/'bundle.md';path.write_text(raw);parsed=bundle.parse(str(path));assert not parsed.malformed
    assert not [n for n in lint.lint_file(str(path)) if not lint.is_warning(n)]
    a,b=[media.prepared_component(media.manifest_for(row,env[0])) for row in parsed.posts]
    assert a!=b and wire['calls']==[]


def test_ready_after_deadline_never_publishes(env,wire,monkeypatch):
    from thth.adapters import threads_media as tm
    clock={'now':0};monkeypatch.setattr(tm.time,'monotonic',lambda:clock['now'])
    wire['on_poll']=lambda:clock.update(now=121)
    result,journal,_=invoke(env,fm=link())
    assert result.error=='media_processing_timeout' and result.failure=='media_held'
    assert journal['media']['remote_ids'] and not posts(wire,'/threads_publish')


@pytest.mark.parametrize('failure_at',[1,2])
def test_published_journal_boundary_remains_precise(env,wire,monkeypatch,failure_at):
    original=media_delivery._save;calls={'published':0}
    def save(name,data):
        if data.get('media',{}).get('phase')=='published':
            calls['published']+=1
            if calls['published']==failure_at:raise OSError('synthetic save')
        return original(name,data)
    monkeypatch.setattr(media_delivery,'_save',save)
    result,journal,_=invoke(env,fm=link())
    assert len(posts(wire,'/threads_publish'))==1
    if failure_at==1:assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    else:assert result.post_id=='100' and journal['media']['phase']=='published' and journal['media']['post_id']=='100'
    assert env[3]['results']==[]
