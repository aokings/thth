"""Explicit external embeds; local PDS only, no card-origin fetching."""
import base64
import copy
import hashlib
import json
import pytest
from thth import media, media_delivery
from thth.adapters import base, bluesky_media as bm
from tests.test_v213_bluesky_media import wire,env,invoke,calls,expected_cid


def ref(n=1):
    cid='b'+base64.b32encode(bytes([1,113,18,32])+bytes([n])*32).decode().lower().rstrip('=')
    return {'uri':'at://did:plc:author/app.bsky.feed.post/item'+str(n),'cid':cid}


def card(thumb=False):
    row={'type':'link','url':'https://card-origin.invalid/article','title':'表題 🌿','description':'説明\nsecond line','associated_refs':[ref(1),ref(2)]}
    if thumb:row.update(thumbnail_file='a.png',thumbnail_alt='承認表示のみ')
    return {'attachments':[row]}


@pytest.mark.parametrize('thumb',[False,True])
def test_actual_embed_fields_bytes_digest_and_no_card_fetch(env,wire,thumb):
    fm=card(thumb);result,journal,manifest=invoke(env,fm=fm,post=base.Post(''))
    assert result.post_id and journal['media']['phase']=='published'
    record=json.loads(calls(wire,'createRecord')[0][1])['record'];external=record['embed']['external']
    assert record['text']=='' and record['embed']['$type']=='app.bsky.embed.external'
    assert external['uri']==fm['attachments'][0]['url'] and external['title']=='表題 🌿' and external['description']=='説明\nsecond line'
    assert external['associatedRefs']==[ref(1),ref(2)]
    assert set(external)=={'uri','title','description','associatedRefs'}|({'thumb'} if thumb else set())
    uploaded=calls(wire,'uploadBlob');assert len(uploaded)==int(thumb)
    if thumb:
        row=manifest['files'][0];assert row['role']=='thumbnail' and row['alt']=='承認表示のみ'
        assert external['thumb']['ref']['$link']==expected_cid(uploaded[0][1])
        assert hashlib.sha256(uploaded[0][1]).hexdigest()==row['public_sha256']
        assert any('no thumbnail alt field' in v for v in media_delivery.lint_notes(env[0],fm))
    assert [c[0] for c in wire['calls']]==['com.atproto.server.createSession']+(['com.atproto.repo.uploadBlob'] if thumb else [])+['com.atproto.repo.createRecord']
    assert journal['media']['manifest']==manifest


@pytest.mark.parametrize('size,valid',[(1_000_000,True),(1_000_001,False),(2_000_000,False)])
def test_external_thumbnail_has_own_limit(env,wire,size,valid):
    manifest=media.manifest_for(card(True),env[0]);manifest['files'][0]['public_size']=size
    assert (bm.intent_error(manifest) is None)==valid
    assert wire['calls']==[]


@pytest.mark.parametrize('key,value',[('url','https://card-origin.invalid/other'),('title','changed'),('description','changed'),('associated_refs',[ref(2),ref(1)]),('thumbnail_alt','changed')])
def test_all_external_intent_changes_approval_component(env,key,value):
    a=card(True);b=copy.deepcopy(a);b['attachments'][0][key]=value
    assert media.prepared_component(media.manifest_for(a,env[0]))!=media.prepared_component(media.manifest_for(b,env[0]))


@pytest.mark.parametrize('value',[
    None,{},[{}],[{'uri':'at://did:plc:a/app.bsky.feed.post/x','cid':'bad'}],
    [{**ref(),'uri':'https://card-origin.invalid'}],[{**ref(),'uri':'at://did:plc:a/app.bsky.feed.post/..'}],
    [{**ref(),'uri':'at://did:plc:a/app.bsky.feed.post/x?foo'}],[{**ref(),'uri':'at://did:plc:a/app.bsky.feed.post/x\n'}],
    [{**ref(),'cid':expected_cid(b'blob not record')}],[{**ref(),'extra':True}],
])
def test_invalid_associated_refs_before_session(env,wire,value):
    fm=card();fm['attachments'][0]['associated_refs']=value
    with pytest.raises(media.MediaError):media.manifest_for(fm,env[0])
    assert wire['calls']==[]


@pytest.mark.parametrize('uri',[
    'at://alice.example/com.example.fooBar/~1:2',
    'at://did:method:val:two/com.example.fooBar/self',
])
def test_strong_reference_is_not_restricted_to_feed_post_or_blessed_did(env,uri):
    fm=card();fm['attachments'][0]['associated_refs']=[{**ref(),'uri':uri}]
    assert media.manifest_for(fm,env[0])['attachments'][0]['associated_refs'][0]['uri']==uri


@pytest.mark.parametrize('status,phase',[(400,'failed'),(403,'failed'),(500,'unknown')])
def test_fileless_post_refusal_and_uncertain_outcome_are_distinct(env,wire,status,phase):
    wire['record_status']=status;result,journal,_=invoke(env,fm=card())
    assert result.post_id is None and journal['media']['phase']==phase
    assert len(calls(wire,'createRecord'))==1 and not calls(wire,'uploadBlob')


def test_fileless_final_stale_never_posts(env,wire):
    result,journal,_=invoke(env,fm=card(),veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.error=='approval_stale' and journal['media']['phase']=='failed'
    assert not calls(wire,'createRecord')


def test_thumbnail_final_stale_retains_known_blob(env,wire):
    result,journal,_=invoke(env,fm=card(True),veto=lambda:'approval_stale' if calls(wire,'uploadBlob') else None)
    assert result.failure=='media_held' and journal['media']['phase']=='held' and len(journal['media']['remote_ids'])==1
    assert not calls(wire,'createRecord')


def test_external_keeps_empty_fields_and_empty_reference_array(env,wire):
    fm=card();fm['attachments'][0].update(title='',description='',associated_refs=[])
    result,_,_=invoke(env,fm=fm);assert result.post_id
    e=json.loads(calls(wire,'createRecord')[0][1])['record']['embed']['external']
    assert e['title']==e['description']=='' and e['associatedRefs']==[]


@pytest.mark.parametrize('phase',['publishing','published'])
def test_fileless_durable_failure_and_restart_never_reposts(env,wire,monkeypatch,phase):
    from thth import accounts,core
    original=media_delivery._save
    def save(name,data):
        if data['media']['phase']==phase:raise OSError('synthetic')
        return original(name,data)
    monkeypatch.setattr(media_delivery,'_save',save)
    result,journal,_=invoke(env,fm=card())
    assert result.post_id is None and result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    assert len(calls(wire,'createRecord'))==int(phase=='published')
    count=len(wire['calls'])
    again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('outcome',['success','stale','unknown'])
def test_actual_git_approval_fileless_card_and_no_replay(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import accounts,core,queuefile
    from thth.adapters.bluesky import BlueskyAdapter
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## bluesky\n\nカード本文。\n',media='bluesky')
    raw=raw.replace('\n---\n','\nattachments: '+json.dumps(card()['attachments'],ensure_ascii=False)+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    cfg=isolated_account_factory(name='alpha',repo_dir=pair['work'],media='bluesky',service=wire['url'],production=True,hashtags=False,char_limit=300,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    assert wire['calls']==[]
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    adapter=BlueskyAdapter(service=wire['url'],identifier='demo.test',app_password=wire['password'])
    original=adapter.session
    def session():
        value=original()
        if outcome=='stale':path.write_text(path.read_text().replace('表題 🌿','変更した表題'))
        return value
    monkeypatch.setattr(adapter,'session',session)
    if outcome=='unknown':wire['record_status']=500
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    assert not calls(wire,'uploadBlob')
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        assert len(calls(wire,'createRecord'))==1
    else:
        assert result.exit_code!=0 and queuefile.parse(str(path)).get('status')=='approved'
        if outcome=='stale':assert result.error=='approval_stale'
        else:assert result.action=='inflight'
        before=len(wire['calls'])
        again=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
        if outcome=='unknown':assert again.action=='inflight'
        assert len(wire['calls'])==before


def test_v2_each_card_keeps_its_own_intent(env,wire):
    from thth import bundle,lint
    a=card()['attachments'];b=card()['attachments'];b[0]['description']='二段目'
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    attachments: '+json.dumps(a,ensure_ascii=False)+'\n'
    raw+='  - index: 2\n    attachments: '+json.dumps(b,ensure_ascii=False)+'\n---\n## bluesky\n一段目\n<!-- thth: 2/2 -->\n二段目\n'
    path=env[2]/'bundle.md';path.write_text(raw)
    parsed=bundle.parse(str(path));assert not parsed.malformed
    assert not [v for v in lint.lint_file(str(path)) if not lint.is_warning(v)]
    assert media.prepared_component(media.manifest_for({'attachments':a},env[0]))!=media.prepared_component(media.manifest_for({'attachments':b},env[0]))
    assert wire['calls']==[]
