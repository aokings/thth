"""Explicit quote references, alone or surrounding an existing media embed."""
import copy
import json
import pytest
from thth import accounts,core,media,media_delivery
from thth.adapters import base
from tests.test_v213_bluesky_media import env,wire,invoke,calls
from tests.test_v213_bluesky_external import card,ref


def quote(n=1):return {'type':'quote',**ref(n)}


def declaration(kind):
    fm={'attachments':[quote()]}
    if kind in ('images','gallery'):
        fm['media']=[{'file':'a.png','alt':'画像 alt'}]
        if kind=='gallery':fm['post_options']={'gallery':True}
    if kind=='external':fm['attachments']+=card(True)['attachments']
    return fm


@pytest.mark.parametrize('kind',['record','images','gallery','external'])
def test_actual_exact_embed_without_quote_fetch(env,wire,kind):
    fm=declaration(kind);result,journal,manifest=invoke(env,fm=fm,post=base.Post(''))
    assert result.post_id and journal['media']['phase']=='published'
    record=json.loads(calls(wire,'createRecord')[0][1])['record'];embed=record['embed']
    quoted={'$type':'app.bsky.embed.record','record':ref()}
    if kind=='record':assert embed==quoted and not calls(wire,'uploadBlob')
    else:
        assert set(embed)=={'$type','record','media'} and embed['$type']=='app.bsky.embed.recordWithMedia'
        assert embed['record']==quoted and embed['media']['$type']=='app.bsky.embed.'+kind
        assert len(calls(wire,'uploadBlob'))==1
        if kind=='images':assert embed['media']['images'][0]['alt']=='画像 alt'
        if kind=='gallery':assert embed['media']['items'][0]['alt']=='画像 alt'
        if kind=='external':assert embed['media']['external']['associatedRefs']==[ref(1),ref(2)]
    assert record['text']=='' and not [c for c in wire['calls'] if c[0]=='getPosts']
    assert manifest['attachments'][0]==quote()


@pytest.mark.parametrize('reverse',[False,True])
def test_quote_and_card_order_does_not_choose_wrong_embed(env,wire,reverse):
    fm=declaration('external')
    if reverse:fm['attachments'].reverse()
    result,_,_=invoke(env,fm=fm);assert result.post_id
    e=json.loads(calls(wire,'createRecord')[0][1])['record']['embed']
    assert e['record']['record']==ref() and e['media']['external']['uri']==card()['attachments'][0]['url']


@pytest.mark.parametrize('field,value',[('uri','https://wrong.invalid'),('cid','bad'),('uri','at://did:plc:a/app.bsky.feed.post/..')])
def test_invalid_quote_never_starts_session(env,wire,field,value):
    fm=declaration('images');fm['attachments'][0][field]=value
    try:result,_,_=invoke(env,fm=fm)
    except media.MediaError:pass
    else:assert result.error=='invalid_attachment: bluesky/quote_reference' and result.post_id is None
    assert wire['calls']==[]


@pytest.mark.parametrize('field,value',[('uri',ref(2)['uri']),('cid',ref(2)['cid'])])
def test_quote_binding_changes_approval(env,field,value):
    fm=declaration('images');other=copy.deepcopy(fm);other['attachments'][0][field]=value
    assert media.prepared_component(media.manifest_for(fm,env[0]))!=media.prepared_component(media.manifest_for(other,env[0]))


@pytest.mark.parametrize('kind',['record','images','gallery','external'])
def test_final_stale_no_record_and_known_blob_retained(env,wire,kind):
    fm=declaration(kind)
    result,journal,_=invoke(env,fm=fm,veto=lambda:'approval_stale' if (calls(wire,'uploadBlob') if kind!='record' else wire['calls']) else None)
    assert not result.post_id and not calls(wire,'createRecord')
    assert journal['media']['phase']==('failed' if kind=='record' else 'held')


def test_quote_only_unknown_stays_durable_and_no_replay(env,wire):
    wire['record_status']=500;result,journal,_=invoke(env,fm=declaration('record'))
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count and len(calls(wire,'createRecord'))==1


def test_quote_only_does_not_silently_discard_gallery(env,wire):
    fm=declaration('record');fm['post_options']={'gallery':True}
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: bluesky/no_images' and wire['calls']==[]


@pytest.mark.parametrize('outcome',['success','stale','unknown'])
def test_actual_git_quote_approval_and_final_binding(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import queuefile
    from thth.adapters.bluesky import BlueskyAdapter
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## bluesky\n\n引用本文。\n',media='bluesky')
    raw=raw.replace('\n---\n','\nattachments: '+json.dumps([quote()])+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='bluesky',service=wire['url'],production=True,hashtags=False,char_limit=300,quiet_hours=None,min_interval_hours=0)
    result=approve_via_cli(path);assert result.returncode==0,result.stderr
    assert wire['calls']==[]
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    adapter=BlueskyAdapter(service=wire['url'],identifier='demo.test',app_password=wire['password']);original=adapter.session
    def session():
        value=original()
        if outcome=='stale':path.write_text(path.read_text().replace(ref()['cid'],ref(2)['cid']))
        return value
    monkeypatch.setattr(adapter,'session',session)
    if outcome=='unknown':wire['record_status']=500
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        assert json.loads(calls(wire,'createRecord')[0][1])['record']['embed']['record']==ref()
    else:
        assert result.exit_code!=0 and queuefile.parse(str(path)).get('status')=='approved'
        if outcome=='stale':assert result.error=='approval_stale' and not calls(wire,'createRecord')
        count=len(wire['calls']);again=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
        if outcome=='unknown':assert again.action=='inflight'
        assert len(wire['calls'])==count
