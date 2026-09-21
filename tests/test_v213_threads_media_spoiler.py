import pytest
from thth import media,media_delivery
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_threads_video import mp4


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('shape',['image','video','carousel'])
def test_media_spoiler_exact_parent_scope(env,wire,enabled,shape):
    (env[2]/'v.mp4').write_bytes(mp4())
    fm={'media':[{'file':'a.png' if shape=='image' else 'v.mp4','alt':'first'}],
        'post_options':{'media_spoiler':enabled,'reply_control':'followers_only','reply_approvals':False}}
    if shape=='carousel':fm['media'].append({'file':'a.png','alt':'second'})
    result,journal,_=invoke(env,fm=fm)
    assert result.post_id and journal['media']['phase']=='published'
    created=posts(wire,'/threads');final=created[-1][2]
    assert final['is_spoiler_media']==['true' if enabled else 'false']
    assert final['reply_control']==['followers_only'] and final['enable_reply_approvals']==['false']
    assert all('is_spoiler_media' not in c[2] for c in created[:-1])


@pytest.mark.parametrize('enabled',[False,True])
def test_fileless_spoiler_rejected_before_graph(env,wire,enabled):
    fm={'post_options':{'media_spoiler':enabled}}
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: threads/media_spoiler_requires_media' and not wire['calls']
    assert media_delivery.lint_notes(env[0],fm,text='body')==[result.error]


@pytest.mark.parametrize('value',['true',0,[],None])
def test_spoiler_must_be_explicit_boolean(env,value):
    with pytest.raises(media.MediaError):media.manifest_for({'post_options':{'media_spoiler':value}},env[0])


def test_spoiler_toggle_changes_approval_and_final_gate(env,wire):
    fm={'media':[{'file':'a.png','alt':'image'}],'post_options':{'media_spoiler':False}}
    old=media.prepared_component(media.manifest_for(fm,env[0]));fm['post_options']['media_spoiler']=True
    assert old!=media.prepared_component(media.manifest_for(fm,env[0]))
    result,journal,_=invoke(env,fm=fm,veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.failure=='media_held' and not posts(wire,'/threads_publish') and journal['media']['remote_ids']
