"""C15 spoilers keep overlapping ranges; styling's separate rule is not inherited."""
import json
import pytest
from thth import core,media,media_delivery
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_threads_video import mp4
from tests.test_v213_threads_replies import approved_options


@pytest.mark.parametrize('value',[True,False,[],[{'offset':1,'length':3},{'offset':2,'length':2},{'offset':1,'length':3}]])
@pytest.mark.parametrize('shape',['text','image','video','carousel'])
def test_exact_spoiler_parent_wire(env,wire,value,shape):
    (env[2]/'v.mp4').write_bytes(mp4())
    fm={'post_options':{'text_spoiler':value,'reply_control':'followers_only','reply_approvals':False}}
    if shape!='text':fm['media']=[{'file':'v.mp4' if shape=='video' else 'a.png','alt':'first'}]
    if shape=='carousel':fm['media'].append({'file':'v.mp4','alt':'second'})
    result,journal,_=invoke(env,fm=fm,post=base.Post('abcdef',reply_to='789'))
    assert result.post_id and journal['media']['phase']=='published'
    calls=posts(wire,'/threads');final=calls[-1][2]
    spans=[{'offset':0,'length':6}] if value is True else value if type(value) is list else []
    assert json.loads(final['text_entities'][0])==[dict(entity_type='SPOILER',**x) for x in spans]
    assert final['enable_reply_approvals']==['false'] and final['reply_to_id']==['789']
    assert all('text_entities' not in call[2] for call in calls[:-1])


@pytest.mark.parametrize('value',[False,[]])
def test_non_ascii_without_offset_allowed(env,wire,value):
    result,_,_=invoke(env,fm={'post_options':{'text_spoiler':value}},post=base.Post('茶と絵文字 🌿'))
    assert result.post_id and json.loads(posts(wire,'/threads')[0][2]['text_entities'][0])==[]


@pytest.mark.parametrize('value,body,reason',[
    (True,'','media_text_required'),(True,'茶','threads_offset_unit_unverified'),
    ([{'offset':0,'length':1}],'茶','threads_offset_unit_unverified'),
    ([{'offset':1,'length':2}],'ab','invalid_attachment: threads/text_spoiler_range'),
    ([{'offset':0,'length':1}]*11,'a','media_limit_exceeded: text_spoilers')])
def test_spoiler_loud_preflight_before_upload(env,wire,value,body,reason):
    fm={'media':[{'file':'a.png','alt':'image'}],'post_options':{'text_spoiler':value}}
    result,_,_=invoke(env,fm=fm,post=base.Post(body))
    assert result.error==reason and not wire['calls'] and not env[3]['upload']
    assert media_delivery.lint_notes(env[0],fm,text=body)==[reason]


def test_ten_identical_ranges_are_preserved(env,wire):
    result,_,_=invoke(env,fm={'post_options':{'text_spoiler':[{'offset':0,'length':1}]*10}},post=base.Post('a'))
    assert result.post_id and len(json.loads(posts(wire,'/threads')[0][2]['text_entities'][0]))==10


@pytest.mark.parametrize('value',[None,0,'true',{},[{}],[{'offset':True,'length':1}],[{'offset':0,'length':False}],[{'offset':-1,'length':1}],[{'offset':0,'length':0}],[{'offset':0,'length':1,'extra':1}]])
def test_strict_range_schema(env,wire,value):
    with pytest.raises(media.MediaError):media.manifest_for({'post_options':{'text_spoiler':value}},env[0])
    assert not wire['calls']


def test_digest_and_unknown_no_replay(env,wire):
    fm={'post_options':{'text_spoiler':[{'offset':0,'length':1}]}}
    old=media.prepared_component(media.manifest_for(fm,env[0]))
    fm['post_options']['text_spoiler'][0]['length']=2
    assert old!=media.prepared_component(media.manifest_for(fm,env[0]))
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm=fm,post=base.Post('body'))
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('outcome',['success','stale'])
def test_actual_git_spoiler_approval(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome):
    approved_options(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome,
        {'text_spoiler':[{'offset':0,'length':3}]},{'text_spoiler':False},body='ASCII body')
    assert json.loads(posts(wire,'/threads')[0][2]['text_entities'][0])==[{'entity_type':'SPOILER','offset':0,'length':3}]
