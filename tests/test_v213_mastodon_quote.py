"""Quote/policy intent is explicit, scoped, capability checked and durable."""
import copy
import urllib.parse
import pytest
from thth import media,media_delivery
from thth.adapters import base,mastodon_media as mm
from tests.test_v213_mastodon_media import wire,env,posts
from tests.test_v213_mastodon_poll import publish,declaration


def quote(**options):
    return {'attachments':[{'type':'quote','uri':'42'}],'post_options':options}


@pytest.fixture
def quoteenv(env,wire):
    wire['caps']['api_versions']={'mastodon':7}
    wire['caps']['configuration']['polls']={'max_options':4,'max_characters_per_option':50,'min_expiration':300,'max_expiration':86400}
    wire['poll']=[(200,{'id':'42','visibility':'public'})]
    wire['status']=(200,{'id':'100','visibility':'public'})
    env[1].granted_scopes=['write:statuses','read:statuses']
    return env


@pytest.mark.parametrize('policy',[None,'public','followers','nobody'])
@pytest.mark.parametrize('with_file',[False,True])
def test_quote_exact_wire_with_or_without_file(quoteenv,wire,policy,with_file):
    fm=quote(**({'quote_approval_policy':policy} if policy else {}))
    if with_file:
        fm['media']=[{'file':'a.png','alt':'点'}];quoteenv[1].granted_scopes.append('write:media')
    result,journal,manifest=publish(quoteenv,fm)
    assert result.post_id=='100' and journal['media']['phase']=='published'
    sent=posts(wire,'/api/v1/statuses');assert len(sent)==1
    form=urllib.parse.parse_qs(sent[0][2].decode())
    assert form['quoted_status_id']==['42']
    assert form.get('quote_approval_policy')==([policy] if policy else None)
    assert len(posts(wire,'/api/v2/media'))==int(with_file)
    target=[v for v in wire['calls'] if v[1]=='/api/v1/statuses/42'];assert len(target)==1
    assert wire['calls'].index(target[0])<wire['calls'].index(sent[0])


def test_quote_poll_combination_does_not_drop_either(quoteenv,wire):
    fm=quote(quote_approval_policy='followers');fm['attachments']+=declaration()['attachments']
    result,_,_=publish(quoteenv,fm);assert result.post_id=='100'
    form=urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())
    assert form['quoted_status_id']==['42'] and form['poll[options][]']==['一つ 🌱','もう一つ']
    assert form['quote_approval_policy']==['followers'] and not posts(wire,'/api/v2/media')


@pytest.mark.parametrize('api',[None,{}, {'mastodon':None},{'mastodon':True},{'mastodon':'7'},{'mastodon':-1},{'mastodon':6}])
def test_quote_unknown_old_capability_refuses_no_side_effect(quoteenv,wire,api):
    wire['caps']['api_versions']=api
    result,journal,_=publish(quoteenv,quote())
    assert result.post_id is None and ('quote_capability_unavailable' in result.error or 'quote_requires_api_7' in result.error)
    assert not any(v[0]=='POST' for v in wire['calls']) and len(wire['calls'])==1
    assert journal['media']['phase']=='failed'


@pytest.mark.parametrize('target',[
    {'id':'42','visibility':'private'},{'id':'42','visibility':'direct'}, {'id':'42'},
    {'id':'43','visibility':'public'},{'id':42,'visibility':'public'},
])
def test_quote_private_unknown_or_mismatched_target_refuses_before_upload(quoteenv,wire,target):
    wire['poll']=[(200,target)];quoteenv[1].granted_scopes.append('write:media')
    result,journal,_=publish(quoteenv,{**quote(),'media':[{'file':'a.png','alt':'点'}]})
    assert result.post_id is None and result.error.startswith('quote_target_')
    assert not any(v[0]=='POST' for v in wire['calls']) and journal['media']['phase']=='failed'


@pytest.mark.parametrize('code',[403,404,422])
def test_quote_target_provider_rejection_never_degrades_to_text(quoteenv,wire,code):
    wire['poll']=[(code,{'error':'untrusted'})]
    result,_,_=publish(quoteenv,quote())
    assert result.post_id is None and not posts(wire,'/api/v1/statuses')
    assert 'untrusted' not in result.error


@pytest.mark.parametrize('uri',['https://example.invalid/42','٤٢','42/../../x',' 42'])
def test_quote_target_is_local_numeric_id_no_external_resolution(quoteenv,wire,uri):
    fm=quote();fm['attachments'][0]['uri']=uri
    result,_,_=publish(quoteenv,fm)
    assert result.error=='invalid_quote_target: mastodon/local_status_id_required' and wire['calls']==[]


def test_non_bsky_cid_is_not_silently_discarded(quoteenv,wire):
    fm=quote();fm['attachments'][0]['cid']='not-a-mastodon-field'
    result,_,_=publish(quoteenv,fm)
    assert result.error=='invalid_quote_target: mastodon/local_status_id_required' and wire['calls']==[]


@pytest.mark.parametrize('policy',['public','followers','nobody'])
def test_policy_only_requires_no_file_or_media_caps(quoteenv,wire,policy):
    wire['caps']={'api_versions':{'mastodon':7}}
    result,journal,_=publish(quoteenv,{'post_options':{'quote_approval_policy':policy}})
    assert result.post_id=='100' and journal['media']['phase']=='published'
    form=urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())
    assert form['quote_approval_policy']==[policy] and 'quoted_status_id' not in form
    assert len(wire['calls'])==2


@pytest.mark.parametrize('fm',[quote(),{'post_options':{'quote_approval_policy':'nobody'}}])
def test_fileless_quote_requires_nonempty_body_before_network(quoteenv,wire,fm):
    result,_,_=publish(quoteenv,fm,text='')
    assert result.error=='quote_text_required: mastodon' and wire['calls']==[]


def test_quote_and_policy_changes_approval_and_key(quoteenv):
    cfg,adapter,_=quoteenv
    original=media.manifest_for(quote(quote_approval_policy='public'),cfg)
    changed=quote(quote_approval_policy='public');changed['attachments'][0]['uri']='43'
    for fm in [changed,quote(quote_approval_policy='followers')]:
        current=media.manifest_for(fm,cfg)
        assert media.prepared_component(original)!=media.prepared_component(current)
        assert adapter._idempotency_key(base.Post(text='a',media_manifest=original),'public')!=adapter._idempotency_key(base.Post(text='a',media_manifest=current),'public')


def test_quote_fresh_lint_and_final_veto(quoteenv,wire):
    notes=media_delivery.lint_notes(quoteenv[0],quote(),text='a')
    assert len(notes)==1 and 'API=7' in notes[0] and len(wire['calls'])==1
    wire['calls'].clear()
    calls=0
    def veto():
        nonlocal calls
        calls+=1
        return 'approval_stale' if calls>=2 else None
    result,_,_=publish(quoteenv,quote(),veto=veto)
    assert result.error=='approval_stale' and not posts(wire,'/api/v1/statuses')


def test_quote_response_visibility_must_not_claim_public_success(quoteenv,wire):
    wire['status']=(200,{'id':'100','visibility':'private'})
    result,journal,_=publish(quoteenv,quote())
    assert result.post_id is None and result.failure=='media_ambiguous'
    assert result.error=='quote_result_non_public: mastodon' and journal['media']['phase']=='unknown'
    assert len(posts(wire,'/api/v1/statuses'))==1
