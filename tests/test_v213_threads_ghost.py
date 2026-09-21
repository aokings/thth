import json
import pytest
from thth import core,media,media_delivery
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_threads_replies import approved_options


@pytest.mark.parametrize('spoiler',[None,False,True,[{'offset':1,'length':2},{'offset':1,'length':2}]])
def test_ghost_text_and_spoiler_exact_wire(env,wire,spoiler):
    options={'ghost':True}
    if spoiler is not None:options['text_spoiler']=spoiler
    fm={'post_options':options};result,journal,_=invoke(env,fm=fm,post=base.Post('body'))
    assert result.post_id and journal['media']['phase']=='published' and not env[3]['upload']
    value=posts(wire,'/threads')[0][2]
    assert value['is_ghost_post']==['true'] and value['media_type']==['TEXT']
    if spoiler is not None:
        ranges=[{'offset':0,'length':4}] if spoiler is True else spoiler if isinstance(spoiler,list) else []
        assert json.loads(value['text_entities'][0])==[dict(entity_type='SPOILER',**x) for x in ranges]
    assert any('24 hours' in x for x in media_delivery.lint_notes(env[0],fm,text='body'))


@pytest.mark.parametrize('shape',['text','image'])
def test_explicit_false_preserves_normal_features(env,wire,shape):
    fm={'post_options':{'ghost':False,'reply_approvals':True}}
    if shape=='image':fm['media']=[{'file':'a.png','alt':'image'}]
    result,_,_=invoke(env,fm=fm,post=base.Post('本文',reply_to='789',topic='word'))
    assert result.post_id
    value=posts(wire,'/threads')[-1][2]
    assert value['is_ghost_post']==['false'] and value['enable_reply_approvals']==['true'] and value['reply_to_id']==['789']


@pytest.mark.parametrize('extra',[
    {'media':[{'file':'a.png','alt':'image'}]},
    {'attachments':[{'type':'quote','uri':'123'}]},
    {'attachments':[{'type':'gif','provider':'GIPHY','id':'opaque'}]},
    {'post_options':{'ghost':True,'reply_approvals':True}},
    {'post_options':{'ghost':True,'reply_approvals':False}},
    {'post_options':{'ghost':True,'reply_control':'everyone'}},
])
def test_ghost_rejects_incompatible_intent_without_effect(env,wire,extra):
    fm={'post_options':{'ghost':True},**extra}
    result,_,_=invoke(env,fm=fm,post=base.Post('body'))
    assert result.error.startswith('unsupported_attachment: threads/ghost_') and not wire['calls'] and not env[3]['upload']
    assert media_delivery.lint_notes(env[0],fm,text='body')==[result.error]


@pytest.mark.parametrize('field,value,reason',[
    ('reply_to','123','ghost_reply'),('topic','word','ghost_combination'),
    ('location_id','123','ghost_combination'),('share_to_instagram',True,'ghost_combination')])
def test_ghost_rejects_legacy_feature_fields(env,wire,field,value,reason):
    fm={'post_options':{'ghost':True},field:value}
    result,_,_=invoke(env,fm=fm,post=base.Post('body',**{field:value}))
    assert result.error=='unsupported_attachment: threads/'+reason and not wire['calls']
    assert media_delivery.lint_notes(env[0],fm,text='body')==[result.error]


def test_ghost_requires_body_and_changes_digest(env,wire):
    fm={'post_options':{'ghost':True}}
    result,_,_=invoke(env,fm=fm,post=base.Post(''))
    assert result.error=='media_text_required' and not wire['calls']
    before=media.prepared_component(media.manifest_for(fm,env[0]));fm['post_options']['ghost']=False
    assert before!=media.prepared_component(media.manifest_for(fm,env[0]))


def test_ghost_unknown_has_no_automatic_republication(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm={'post_options':{'ghost':True}},post=base.Post('body'))
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('outcome',['success','stale'])
def test_actual_git_ghost_approval(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome):
    approved_options(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome,{'ghost':True},{'ghost':False})
    assert posts(wire,'/threads')[0][2]['is_ghost_post']==['true']
