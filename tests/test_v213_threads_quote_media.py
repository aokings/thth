"""C20: quote is placed only on the final image/video/carousel container."""
import json
import pytest
from thth import core,media,media_delivery
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_threads_quote import quote
from tests.test_v213_mastodon_media import video_timing


def fm_for(env,order):
    (env[2]/'b.png').write_bytes((env[2]/'a.png').read_bytes())
    (env[2]/'v.mp4').write_bytes(video_timing([(30,1000)],scale=30000))
    fm=quote();fm['media']=[{'file':name,'alt':'説明'+str(i)} for i,name in enumerate(order)]
    return fm


@pytest.mark.parametrize('order',[['a.png'],['v.mp4'],['a.png','b.png'],['a.png','v.mp4'],['v.mp4','a.png']])
def test_quote_goes_only_on_final_container_and_public_bytes_unchanged(env,wire,order):
    fm=fm_for(env,order);result,journal,manifest=invoke(env,fm=fm,post=base.Post('引用本文',reply_to='777',topic='topic'))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    creates=posts(wire,'/threads');final=creates[-1][2]
    assert final['quote_post_id']==['456'] and final['reply_to_id']==['777'] and final['topic_tag']==['topic']
    assert final['media_type']==['CAROUSEL' if len(order)>1 else 'VIDEO' if order[0].endswith('mp4') else 'IMAGE']
    if len(order)>1:assert all('quote_post_id' not in c[2] for c in creates[:-1])
    assert len([c for c in creates if 'quote_post_id' in c[2]])==1
    assert len(posts(wire,'/threads_publish'))==1 and env[3]['results']==[True]*len(order)
    assert wire['fetched']==env[3]['upload'] and len(wire['fetched'])==len(order)
    assert [r['alt'] for r in manifest['files']]==[r['alt'] for r in fm['media']]


def test_quote_media_keeps_text_link_restriction(env,wire):
    fm=fm_for(env,['a.png']);fm['attachments'].append({'type':'link','url':'https://outside.invalid'})
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: threads/link_requires_text' and wire['calls']==[]


def test_quote_target_change_before_parent_or_publish_never_publicizes(env,wire):
    fm=fm_for(env,['a.png','v.mp4'])
    result,journal,_=invoke(env,fm=fm,veto=lambda:'approval_stale' if posts(wire,'/threads') else None)
    assert result.error=='approval_stale' and result.failure=='media_held'
    assert len(posts(wire,'/threads'))==1 and not posts(wire,'/threads_publish')
    assert journal['media']['remote_ids'] and env[3]['results']==[False]


def test_carousel_quote_unknown_publish_no_second_post(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm=fm_for(env,['v.mp4','a.png']))
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count and len(posts(wire,'/threads_publish'))==1
