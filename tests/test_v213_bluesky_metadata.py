"""Declared post metadata is approved, strict, local and preserved on wire."""
import copy
import json
import pytest
from thth import bluesky_metadata as md,media,media_delivery,core
from thth.adapters import base,bluesky
from tests.test_v213_bluesky_media import env,wire,invoke,calls
from tests.test_v213_bluesky_record import declaration


def feature(kind='link',value='https://not-fetched.invalid'):
    return {'$type':'app.bsky.richtext.facet#'+kind,{'link':'uri','mention':'did','tag':'tag'}[kind]:value}


def facet(start=0,end=3,features=None):return {'index':{'byteStart':start,'byteEnd':end},'features':[feature()] if features is None else features}


def options():return {'languages':['ja','en-US','x-private'],'labels':['Example-Label','未確認'],'tags':['extra','茶'],'facets':[facet()]}


@pytest.mark.parametrize('kind',['fileless','record','images','gallery','external'])
def test_all_metadata_exact_wire_and_no_lookup(env,wire,kind):
    fm={} if kind=='fileless' else declaration(kind)
    fm.setdefault('post_options',{}).update(options())
    result,journal,manifest=invoke(env,fm=fm,post=base.Post('茶本文',hashtags_allowed=True))
    assert result.post_id and journal['media']['phase']=='published'
    record=json.loads(calls(wire,'createRecord')[0][1])['record']
    assert record['langs']==options()['languages'] and record['tags']==['extra','茶']
    assert record['labels']=={'$type':'com.atproto.label.defs#selfLabels','values':[{'val':'Example-Label'},{'val':'未確認'}]}
    assert record['facets']==[facet()] and record['text']=='茶本文'
    assert ('embed' in record)==(kind!='fileless')
    assert not any(c[0]=='getPosts' for c in wire['calls'])
    assert manifest['post_options']['tags']==['extra','茶']


@pytest.mark.parametrize('key,value,ok',[
    ('languages',['ja','en','zh-Hant-TW'],True),('languages',['ja']*4,False),
    ('labels',['x']*10,True),('labels',['x']*11,False),('labels',['茶'*42+'ab'],True),('labels',['茶'*43],False),
    ('tags',['a']*8,True),('tags',['a']*9,False),('tags',['e\u0301'*64],True),('tags',['e\u0301'*65],False),
    ('tags',['a'+'\u0301'*319+'b'],True),('tags',['a'+'\u0301'*320],False),
])
def test_schema_caps_bytes_and_fixed_graphemes(env,wire,key,value,ok):
    result,_,_=invoke(env,fm={'post_options':{key:value}},post=base.Post('body',hashtags_allowed=True))
    assert bool(result.post_id)==ok
    if not ok:assert result.error.startswith('metadata_limit_exceeded:') and wire['calls']==[]


@pytest.mark.parametrize('tag,valid',[
    ('ja',True),('zh-cmn-Hans-CN',True),('de-CH-1901',True),('sl-rozaj-biske-1994',True),
    ('en-a-foo-b-bar-x-a',True),('x-a',True),('i-klingon',True),('sgn-BE-FR',True),
    ('en-GB-oed',True),('abcd',True),('abcdefghi',False),('en_US',False),('a',False),
    ('en-a-foo-a-bar',False),('sl-rozaj-rozaj',False),('en-x',False),('en-a',False),('日',False),
])
def test_language_well_formed_without_registry_fetch(tag,valid):assert md.language(tag)==valid


@pytest.mark.parametrize('values',[{'languages':[]},{'labels':[]},{'tags':[]},{'facets':[]}])
def test_explicit_empty_arrays_are_kept(env,wire,values):
    result,_,_=invoke(env,fm={'post_options':values},post=base.Post('body'));assert result.post_id
    record=json.loads(calls(wire,'createRecord')[0][1])['record'];key=next(iter(values))
    assert record[{'languages':'langs'}.get(key,key)]==({'$type':'com.atproto.label.defs#selfLabels','values':[]} if key=='labels' else [])


@pytest.mark.parametrize('text',['',' ','\n'])
def test_metadata_without_embed_does_not_authorize_empty_body(env,wire,text):
    result,_,_=invoke(env,fm={'post_options':{'languages':['ja']}},post=base.Post(text))
    assert result.error=='status_text_required: bluesky' and wire['calls']==[]


@pytest.mark.parametrize('range_',[(1,3),(0,2),(0,99)])
def test_utf8_midpoint_or_outside_refuses_before_session(env,wire,range_):
    result,_,_=invoke(env,fm={'post_options':{'facets':[facet(*range_)]}},post=base.Post('茶a'))
    assert result.error=='metadata_invalid: facet_utf8_range' and wire['calls']==[]


def test_adjacent_multifeature_and_duplicate_feature(env,wire):
    one=facet(0,3,[feature(),feature('mention','did:plc:test'),feature()]);two=facet(3,4,[feature('tag','a')])
    result,_,_=invoke(env,fm={'post_options':{'facets':[two,one,one]}},post=base.Post('茶a',hashtags_allowed=True))
    assert result.post_id
    got=json.loads(calls(wire,'createRecord')[0][1])['record']['facets']
    assert got==[facet(0,3,[feature(),feature('mention','did:plc:test')]),two]


def test_generated_same_facet_deduplicates_without_clearing_legacy(env,wire):
    text='茶 https://example.invalid #one';automatic=bluesky.build_facets(text,include_tags=True)
    result,_,_=invoke(env,fm={'post_options':{'facets':automatic+automatic}},post=base.Post(text,hashtags_allowed=True));assert result.post_id
    assert json.loads(calls(wire,'createRecord')[0][1])['record']['facets']==automatic


@pytest.mark.parametrize('flavor',['overlap','auto_conflict','features_order'])
def test_conflicts_are_loud_not_overwritten(env,wire,flavor):
    text='abc https://example.invalid';values=[facet(0,3),facet(2,4)]
    if flavor=='auto_conflict':values=[facet(4,len(text),[feature('link','https://different.invalid')])]
    if flavor=='features_order':values=[facet(0,3,[feature(),feature('mention','did:plc:test')]),facet(0,3,[feature('mention','did:plc:test'),feature()])]
    result,_,_=invoke(env,fm={'post_options':{'facets':values}},post=base.Post(text))
    assert result.error=='metadata_conflict: '+('overlap' if flavor=='overlap' else 'same_range') and wire['calls']==[]


@pytest.mark.parametrize('item',[feature('tag','e\u0301'*65),feature('mention','@not-a-did'),feature('link','not-a-uri'),feature('link','https://x/%GG')])
def test_feature_constraints_before_http(env,wire,item):
    result,_,_=invoke(env,fm={'post_options':{'facets':[facet(features=[item])]}},post=base.Post('abc'))
    assert result.error and wire['calls']==[]


@pytest.mark.parametrize('value',['mailto:person@example.invalid','https://example.invalid/something'])
def test_link_label_is_not_required_to_equal_target(env,wire,value):
    result,_,_=invoke(env,fm={'post_options':{'facets':[facet(features=[feature('link',value)])]}},post=base.Post('茶'))
    assert result.post_id and not any(c[0]=='getPosts' for c in wire['calls'])


@pytest.mark.parametrize('value',[{'tags':['extra']},{'facets':[facet(features=[feature('tag','extra')])]}])
def test_account_hashtags_false_cannot_be_bypassed(env,wire,value):
    result,_,_=invoke(env,fm={'post_options':value},post=base.Post('abc',hashtags_allowed=False))
    assert result.error=='hashtags_disabled_by_ledger' and wire['calls']==[]
    assert media_delivery.lint_notes(env[0],{'post_options':value},text='abc')==['hashtags_disabled_by_ledger']


def test_lint_and_publish_check_same_effective_text_and_collision(env,wire):
    cfg=env[0];cfg['hashtags']=True;fm={'post_options':{'facets':[facet(4,27,[feature('link','https://different.invalid')])]}}
    text='abc https://example.invalid'
    assert media_delivery.lint_notes(cfg,fm,text=text)==['metadata_conflict: same_range'] and wire['calls']==[]


def test_metadata_change_changes_approval(env):
    original={'post_options':options()};baseline=media.prepared_component(media.manifest_for(original,env[0]))
    for key,value in [('languages',['en']),('labels',['new']),('tags',['new']),('facets',[])]:
        other=copy.deepcopy(original);other['post_options'][key]=value
        assert media.prepared_component(media.manifest_for(other,env[0]))!=baseline


def test_metadata_only_final_stale_and_unknown_do_not_retry(env,wire):
    fm={'post_options':{'languages':['ja']}}
    result,_,_=invoke(env,fm=fm,veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.error=='approval_stale' and not calls(wire,'createRecord')
    wire['record_status']=500;result,journal,_=invoke(env,fm=fm)
    assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('kind',['labels','tags'])
def test_empty_lexicon_string_is_not_a_closed_vocabulary(env,wire,kind):
    result,_,_=invoke(env,fm={'post_options':{kind:['']}},post=base.Post('body',hashtags_allowed=True))
    assert result.post_id
    value=json.loads(calls(wire,'createRecord')[0][1])['record'][kind]
    assert value==({'$type':'com.atproto.label.defs#selfLabels','values':[{'val':''}]} if kind=='labels' else [''])


@pytest.mark.parametrize('value',[[],{},False,None])
def test_malformed_feature_type_is_static_not_traceback(env,wire,value):
    item=feature();item['$type']=value
    with pytest.raises(media.MediaError):media.manifest_for({'post_options':{'facets':[facet(features=[item])]}},env[0])
    with pytest.raises(media.MediaError):md.validate({'facets':[facet(features=[item])]})
    assert wire['calls']==[]


def test_v2_lint_uses_first_topic_and_preserves_second_segment(env,wire):
    from thth import lint,bundle
    cfg=env[0];cfg['hashtags']=True
    first={'languages':['ja'],'facets':[facet(4,8,[feature('mention','did:plc:example')])]}
    raw='---\nthth: 2\naccount: alpha\ntopic: cat\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    raw+='  - index: 1\n    post_options: '+json.dumps(first)+'\n  - index: 2\n    post_options: {"languages":["en"]}\n---\n## bluesky\nabc\n<!-- thth: 2/2 -->\nsecond\n'
    path=env[2]/'bundle.md';path.write_text(raw);assert not bundle.parse(str(path)).malformed
    notes=lint.lint_file(str(path))
    assert 'metadata_conflict: same_range' in notes and 'metadata_invalid: facet_utf8_range' not in notes
    assert wire['calls']==[]


@pytest.mark.parametrize('outcome',['success','stale','unknown'])
def test_actual_git_metadata_approval_and_provider_gate(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import accounts,queuefile
    from thth.adapters.bluesky import BlueskyAdapter
    opts={'languages':['ja'],'labels':['unregistered-label'],'facets':[facet(0,3)]}
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## bluesky\n\n茶本文。\n',media='bluesky')
    raw=raw.replace('\n---\n','\npost_options: '+json.dumps(opts,ensure_ascii=False)+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='bluesky',service=wire['url'],production=True,hashtags=False,char_limit=300,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    assert wire['calls']==[]
    monkeypatch.setattr(accounts,'load_token',lambda _:{'identifier':'demo.test','app_password':wire['password']})
    adapter=BlueskyAdapter(service=wire['url'],identifier='demo.test',app_password=wire['password']);original=adapter.session
    def session():
        value=original()
        if outcome=='stale':path.write_text(path.read_text().replace('unregistered-label','changed-label'))
        return value
    monkeypatch.setattr(adapter,'session',session)
    if outcome=='unknown':wire['record_status']=500
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        record=json.loads(calls(wire,'createRecord')[0][1])['record']
        assert record['langs']==['ja'] and record['facets']==[facet()] and record['labels']['values']==[{'val':'unregistered-label'}]
    else:
        assert result.exit_code!=0 and queuefile.parse(str(path)).get('status')=='approved'
        if outcome=='stale':assert result.error=='approval_stale' and not calls(wire,'createRecord')
        count=len(wire['calls']);again=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
        if outcome=='unknown':assert again.action=='inflight'
        assert len(wire['calls'])==count


def test_legacy_body_topic_counter_does_not_use_metadata_tables(env,monkeypatch):
    from thth import graphemes,tags
    monkeypatch.setattr(graphemes,'count',lambda *a,**k:pytest.fail('legacy counter changed'))
    text='旧本文 #one';cfg={'media':'bluesky','hashtags':True,'max_hashtags':3}
    assert tags.errors('bluesky',text,'topic',cfg)==[]
    assert bluesky.count('e\u0301')==1
    record=env[1]._post_record(base.Post(text,topic='topic',hashtags_allowed=True))
    assert record['text']==text+'\n#topic' and len(record['facets'])==2


def test_explicit_tags_use_lexicon_eight_not_body_max_hashtags(env,wire):
    from thth import lint
    cfg=env[0];cfg['hashtags']=True;cfg['max_hashtags']=0
    options={'tags':['t'+str(i) for i in range(8)]}
    raw='---\nthth: 1\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\nstatus: draft\npost_options: '+json.dumps(options)+'\n---\n## bluesky\n本文\n'
    path=env[2]/'tags.md';path.write_text(raw)
    assert not [n for n in lint.lint_file(str(path)) if not lint.is_warning(n)]
    result,_,_=invoke(env,fm={'post_options':options},post=base.Post('本文',hashtags_allowed=True))
    assert result.post_id and json.loads(calls(wire,'createRecord')[0][1])['record']['tags']==options['tags']
