"""Mastodon body-derived link previews and explicit no-file post options."""
import copy
import urllib.parse
import pytest
from thth import media,media_delivery
from thth.adapters import base
from tests.test_v213_mastodon_media import wire,env,posts
from tests.test_v213_mastodon_poll import publish,declaration

URL='https://external.invalid/path?q=one&v=two'

def link(**fields):return {'attachments':[{'type':'link','url':URL,**fields}]}


@pytest.mark.parametrize('body',[URL,'文 '+URL+' 続き','文\n'+URL+'\n続き'])
def test_exact_body_url_posts_without_fetch_upload_or_instance(env,wire,body):
    env[1].granted_scopes=['write:statuses'];wire['caps']={}
    result,journal,manifest=publish(env,link(),text=body)
    assert result.post_id=='100' and journal['media']['phase']=='published'
    assert manifest['attachments'][0]['url']==URL
    assert len(wire['calls'])==1 and wire['calls'][0][:2]==('POST','/api/v1/statuses')
    assert urllib.parse.parse_qs(wire['calls'][0][2].decode())=={'status':[body],'visibility':['public']}


@pytest.mark.parametrize('body',['no URL',URL+'/other','prefix'+URL,URL+'extra','https://external.invalid.evil/path?q=one&v=two'])
def test_not_same_complete_url_refuses_before_any_network(env,wire,body):
    result,journal,_=publish(env,link(),text=body)
    assert result.error=='link_text_mismatch: mastodon' and wire['calls']==[]
    assert journal['media']['phase']=='failed'


@pytest.mark.parametrize('fields',[{'title':''},{'description':'description'},{'thumbnail_file':'a.png','thumbnail_alt':'点'}])
def test_custom_card_fields_never_silently_drop(env,wire,fields):
    result,_,_=publish(env,link(**fields),text=URL)
    assert result.error=='unsupported_attachment: mastodon/custom_card_fields' and wire['calls']==[]


def test_options_only_exact_false_values_and_no_media_permission(env,wire):
    options={'language':'ja','sensitive':False,'spoiler_text':'','visibility':'unlisted'}
    env[1].granted_scopes=['write:statuses'];wire['caps']={}
    result,journal,_=publish(env,{'post_options':options},text='本文')
    assert result.post_id=='100' and journal['media']['phase']=='published'
    assert len(wire['calls'])==1
    assert urllib.parse.parse_qs(wire['calls'][0][2].decode(),keep_blank_values=True)=={'status':['本文'],'visibility':['unlisted'],'language':['ja'],'sensitive':['false'],'spoiler_text':['']}


@pytest.mark.parametrize('fm',[link(),{'post_options':{'language':'ja'}}])
def test_empty_body_refuses_even_with_typed_link_or_options(env,wire,fm):
    result,_,_=publish(env,fm,text='')
    assert result.error=='status_text_required: mastodon' and wire['calls']==[]


def test_focus_with_no_parent_is_not_ignored(env,wire):
    result,_,_=publish(env,{'post_options':{'focus':[]}})
    assert result.error=='unsupported_attachment: mastodon/fileless_focus' and wire['calls']==[]


def test_link_can_accompany_poll_quote_and_media(env,wire):
    wire['caps']['api_versions']={'mastodon':7}
    wire['poll']=[(200,{'id':'42','visibility':'public'})]
    wire['status']=(200,{'id':'100','visibility':'public'})
    fm=link();fm['attachments'].append({'type':'quote','uri':'42'});fm['media']=[{'file':'a.png','alt':'点'}]
    result,_,_=publish(env,fm,text=URL)
    assert result.post_id=='100'
    form=urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())
    assert form['status']==[URL] and form['quoted_status_id']==['42'] and form['media_ids[]']==['1']


def test_link_poll_pair_keeps_poll_fields(env,wire):
    wire['caps']['configuration']['polls']={'max_options':4,'max_characters_per_option':50,'min_expiration':300,'max_expiration':86400}
    fm=link();fm['attachments']+=declaration()['attachments']
    result,_,_=publish(env,fm,text=URL)
    assert result.post_id=='100'
    form=urllib.parse.parse_qs(posts(wire,'/api/v1/statuses')[0][2].decode())
    assert form['poll[expires_in]']==['300'] and form['status']==[URL]


def test_lint_is_local_when_no_capability_required(env,wire):
    assert media_delivery.lint_notes(env[0],{'post_options':{'sensitive':True}},text='本文')==[]
    assert media_delivery.lint_notes(env[0],link(),text=URL)[0].startswith('warning:')
    assert media_delivery.lint_notes(env[0],link(),text=URL+'/bad')==['link_text_mismatch: mastodon']
    assert wire['calls']==[]


def test_link_and_every_option_change_hash_key(env):
    cfg,adapter,_=env;fm=link();fm['post_options']={'language':'ja','sensitive':False,'spoiler_text':'one','visibility':'public'}
    original=media.manifest_for(fm,cfg);changes=[]
    for key,value in [('language','en'),('sensitive',True),('spoiler_text','two'),('visibility','unlisted')]:
        other=copy.deepcopy(fm);other['post_options'][key]=value;changes.append(other)
    other=copy.deepcopy(fm);other['attachments'][0]['url']=URL+'/different';changes.append(other)
    for other in changes:
        current=media.manifest_for(other,cfg)
        assert media.prepared_component(current)!=media.prepared_component(original)
        assert adapter._idempotency_key(base.Post(text=URL,media_manifest=current),'public')!=adapter._idempotency_key(base.Post(text=URL,media_manifest=original),'public')


@pytest.mark.parametrize('status,phase',[(422,'failed'),(500,'unknown')])
def test_fileless_status_failure_keeps_definite_vs_unknown(env,wire,status,phase):
    wire['status']=(status,{})
    result,journal,_=publish(env,link(),text=URL)
    assert result.post_id is None and journal['media']['phase']==phase
    assert len(posts(wire,'/api/v1/statuses'))==1


def test_link_final_stale_veto_before_post(env,wire):
    result,_,_=publish(env,link(),text=URL,veto=lambda:'approval_stale')
    assert result.error=='approval_stale' and wire['calls']==[]
