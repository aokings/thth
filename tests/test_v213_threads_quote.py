"""Threads quote uses the declared remote ID without resolving a URL."""
import json
import pytest
from thth import accounts,core,media,media_delivery,media_relay
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts
from tests.test_v213_threads_link import link


def quote(value='456'):return {'attachments':[{'type':'quote','uri':value}]}


@pytest.mark.parametrize('with_link',[False,True])
def test_quote_exact_text_wire_no_reference_or_relay(env,wire,monkeypatch,with_link):
    fm=quote()
    if with_link:fm['attachments']+=link()['attachments']
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('fileless relay'))
    result,journal,_=invoke(env,fm=fm,post=base.Post('',reply_to='789',topic='topic'))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    body=posts(wire,'/threads')[0][2]
    assert body['quote_post_id']==['456'] and body['media_type']==['TEXT'] and body['text']==['']
    assert body['reply_to_id']==['789'] and body['topic_tag']==['topic']
    assert ('link_attachment' in body)==with_link and len(posts(wire,'/threads_publish'))==1
    assert len(wire['calls'])==3 and not any('/456' in c[1] for c in wire['calls']) and env[3]['upload']==[]


@pytest.mark.parametrize('value',['https://www.threads.net/@person/post/abc','at://did:plc:a/post/x','-1','1.0','１２３','12\n3'])
def test_quote_id_is_not_a_resolution_or_coercion_request(env,wire,value):
    fm=quote(value)
    try:result,_,_=invoke(env,fm=fm)
    except media.MediaError:pass
    else:assert result.error=='invalid_attachment: threads/quote_id'
    assert wire['calls']==[]


def test_unimplemented_quote_effect_is_not_dropped(env,wire):
    fm=quote();fm['attachments'][0]['cid']='bafyexample'
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: threads/quote_option'
    assert wire['calls']==[]


def test_target_is_bound_to_manifest(env):
    a=media.manifest_for(quote(),env[0]);b=media.manifest_for(quote('457'),env[0])
    assert media.prepared_component(a)!=media.prepared_component(b)


def test_final_gate_holds_container_without_publication(env,wire):
    result,journal,_=invoke(env,fm=quote(),veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.error=='approval_stale' and result.failure=='media_held'
    assert journal['media']['remote_ids'] and journal['media']['phase']=='held' and not posts(wire,'/threads_publish')


def test_unknown_quote_publication_never_retries(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm=quote());assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('outcome',['success','stale'])
def test_git_approved_quote_target(tmp_path,isolated_account_factory,wire,monkeypatch,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import queuefile
    from thth.adapters.threads import ThreadsAdapter
    import secrets
    raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## threads\n\n引用本文。\n')
    raw=raw.replace('\n---\n','\nattachments: '+json.dumps(quote()['attachments'])+'\n---\n',1)
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='threads',base_url=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    assert wire['calls']==[]
    token={'access_token':secrets.token_urlsafe(30),'user_id':'123'};monkeypatch.setattr(accounts,'load_token',lambda _:token)
    adapter=ThreadsAdapter(base_url=wire['url'],access_token=token['access_token'],user_id='123',wait_seconds=0)
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('fileless relay'))
    if outcome=='stale':wire['on_create']=lambda:path.write_text(path.read_text().replace('"456"','"457"'))
    result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    if outcome=='success':
        assert result.action=='post' and queuefile.parse(str(path)).get('status')=='posted'
        assert posts(wire,'/threads')[0][2]['quote_post_id']==['456']
    else:assert result.error=='approval_stale' and queuefile.parse(str(path)).get('status')=='approved' and not posts(wire,'/threads_publish')


def test_v2_quote_targets_are_separate(env,wire):
    from thth import bundle,lint
    raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
    for i in (1,2):raw+='  - index: '+str(i)+'\n    attachments: '+json.dumps(quote(str(455+i))['attachments'])+'\n'
    raw+='---\n## threads\n一段目\n<!-- thth: 2/2 -->\n二段目\n'
    path=env[2]/'bundle.md';path.write_text(raw);parsed=bundle.parse(str(path));assert not parsed.malformed
    assert not [n for n in lint.lint_file(str(path)) if not lint.is_warning(n)]
    a,b=[media.prepared_component(media.manifest_for(row,env[0])) for row in parsed.posts]
    assert a!=b and wire['calls']==[]
