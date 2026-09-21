"""Provider GIF IDs are declared effects, not files fetched by thth."""
import copy
import json
import pytest
from thth import accounts,core,media,media_delivery,media_relay
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts


def gif(value='synthetic-id'):return {'attachments':[{'type':'gif','provider':'GIPHY','id':value}]}


@pytest.mark.parametrize('text',['本文',''])
def test_exact_giphy_wire_no_relay_no_fetch(env,wire,monkeypatch,text):
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('remote GIF must not create R2'))
    result,journal,_=invoke(env,fm=gif(),post=base.Post(text,reply_to='789'))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    body=posts(wire,'/threads')[0][2]
    assert body['media_type']==['TEXT'] and body['text']==[text] and body['reply_to_id']==['789']
    assert json.loads(body['gif_attachment'][0])=={'gif_id':'synthetic-id','provider':'GIPHY'}
    assert len(wire['calls'])==3 and len(posts(wire,'/threads_publish'))==1 and env[3]['upload']==[] and wire['fetched']==[]


@pytest.mark.parametrize('provider',['TENOR','giphy','other','GIPHY '])
def test_unconfirmed_provider_never_reaches_graph(env,wire,provider):
    fm=gif();fm['attachments'][0]['provider']=provider
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: threads/gif_provider' and wire['calls']==[]
    assert media_delivery.lint_notes(env[0],fm,text='本文')==[result.error]


def test_unknown_field_is_not_dropped(env,wire):
    fm=gif();fm['attachments'][0]['url']='https://not-fetched.invalid'
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert wire['calls']==[]


def test_remote_gif_and_local_file_are_not_conflated(env,wire):
    fm=gif();fm['media']=[{'file':'a.png','alt':'local image'}]
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert wire['calls']==[] and env[3]['upload']==[]


def test_opaque_id_is_preserved_as_json_and_bound(env,wire):
    value='quote" slash\\ id';fm=gif(value)
    result,_,manifest=invoke(env,fm=fm);assert result.post_id
    assert json.loads(posts(wire,'/threads')[0][2]['gif_attachment'][0])['gif_id']==value
    other=media.manifest_for(gif('changed'),env[0]);assert media.prepared_component(manifest)!=media.prepared_component(other)


def test_remote_gif_final_veto_holds_existing_container(env,wire):
    result,journal,_=invoke(env,fm=gif(),veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.error=='approval_stale' and result.failure=='media_held' and journal['media']['remote_ids']
    assert not posts(wire,'/threads_publish') and env[3]['results']==[]


def test_unknown_gif_publication_no_replay(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm=gif());assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('outcome',['success','stale'])
def test_git_approved_provider_id(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import queuefile
    from thth.adapters.threads import ThreadsAdapter
    import secrets
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## threads\n\nGIF本文。\n')
    raw=raw.replace('\n---\n','\nattachments: '+json.dumps(gif()['attachments'])+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='threads',base_url=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    assert wire['calls']==[]
    token={'access_token':secrets.token_urlsafe(30),'user_id':'123'};monkeypatch.setattr(accounts,'load_token',lambda _:token)
    adapter=ThreadsAdapter(base_url=wire['url'],access_token=token['access_token'],user_id='123',wait_seconds=0)
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('fileless relay'))
    if outcome=='stale':wire['on_create']=lambda:path.write_text(path.read_text().replace('synthetic-id','changed-id'))
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        assert json.loads(posts(wire,'/threads')[0][2]['gif_attachment'][0])['gif_id']=='synthetic-id'
    else:assert result.error=='approval_stale' and queuefile.parse(str(path)).get('status')=='approved' and not posts(wire,'/threads_publish')
