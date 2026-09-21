"""C15/C20 poll payload and provisional new-field counter."""
import json
import pytest
from thth import accounts,core,media,media_delivery,media_relay
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts


def poll(options=None):return {'attachments':[{'type':'poll','options':options or ['first','second']}]}


@pytest.mark.parametrize('options', [['a','b'],['茶'*25,'x','y'],['😀'*25,'e\u0301'*12+'z','c','d']])
@pytest.mark.parametrize('text',['','本文'])
def test_exact_poll_wire_without_r2(env,wire,monkeypatch,options,text):
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('poll relay'))
    result,journal,_=invoke(env,fm=poll(options),post=base.Post(text,reply_to='789'))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    payload=posts(wire,'/threads')[0][2]
    assert payload['text']==[text] and payload['reply_to_id']==['789'] and payload['media_type']==['TEXT']
    assert json.loads(payload['poll_attachment'][0])==dict(zip(('option_a','option_b','option_c','option_d'),options))
    assert len(wire['calls'])==3 and env[3]['upload']==[] and wire['fetched']==[]
    notes=media_delivery.lint_notes(env[0],poll(options),text=text)
    assert any('provisional Unicode code points' in note for note in notes)


@pytest.mark.parametrize('options',[['a','b','c','d','e'],['x'*26,'b'],['茶'*26,'b'],['😀'*26,'b'],['e\u0301'*13,'b']])
def test_poll_limits_stop_before_provider(env,wire,options):
    result,_,_=invoke(env,fm=poll(options))
    assert result.error.startswith('media_limit_exceeded: poll_') and not wire['calls']
    assert any(n.startswith('media_limit_exceeded: poll_') for n in media_delivery.lint_notes(env[0],poll(options),text='body'))


@pytest.mark.parametrize('options',[[],['one'],['','two'],['a','a']])
def test_structural_option_errors_are_not_coerced(env,wire,options):
    with pytest.raises(media.MediaError):media.manifest_for({'attachments':[{'type':'poll','options':options}]},env[0])
    assert wire['calls']==[]


@pytest.mark.parametrize('key,value',[('expires_in',60),('multiple',True),('multiple',False),('hide_totals',False),('hide_totals',True)])
def test_unsupported_poll_fields_are_not_silently_dropped(env,wire,key,value):
    fm=poll();fm['attachments'][0][key]=value
    result,_,_=invoke(env,fm=fm)
    assert result.error=='unsupported_attachment: threads/poll_option' and not wire['calls']


@pytest.mark.parametrize('extra',[{'type':'link','url':'https://no-fetch.invalid'}, {'type':'text','text':'long'}, {'type':'gif','provider':'GIPHY','id':'fake'}])
def test_poll_incompatible_attachment_never_reaches_provider(env,wire,extra):
    fm=poll();fm['attachments'].append(extra)
    try:result,_,_=invoke(env,fm=fm)
    except media.MediaError:pass
    else:assert result.error=='unsupported_attachment: threads/poll_combination'
    assert not wire['calls']


def test_poll_quote_common_parameter_is_preserved(env,wire):
    fm=poll();fm['attachments'].append({'type':'quote','uri':'456'})
    result,_,_=invoke(env,fm=fm)
    assert result.post_id and posts(wire,'/threads')[0][2]['quote_post_id']==['456']


def test_poll_option_order_and_content_are_approval_intent(env):
    a=media.manifest_for(poll(['a','b']),env[0]);b=media.manifest_for(poll(['b','a']),env[0]);c=media.manifest_for(poll(['a','c']),env[0])
    assert len({media.prepared_component(x) for x in (a,b,c)})==3


def test_poll_final_veto_retains_known_container(env,wire):
    result,journal,_=invoke(env,fm=poll(),veto=lambda:'approval_stale' if wire['calls'] else None)
    assert result.error=='approval_stale' and result.failure=='media_held' and journal['media']['remote_ids']
    assert not posts(wire,'/threads_publish')


def test_poll_unknown_publication_is_not_replayed(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm=poll());assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('outcome',['success','stale'])
def test_actual_git_approval_poll_options(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import queuefile
    from thth.adapters.threads import ThreadsAdapter
    import secrets
    if version==1:
        raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## threads\n\n投票本文。\n')
        raw=raw.replace('\n---\n','\nattachments: '+json.dumps(poll()['attachments'])+'\n---\n',1)
    else:
        raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n  - index: 1\n    attachments: '+json.dumps(poll()['attachments'])+'\n  - index: 2\n    attachments: '+json.dumps(poll(['third','fourth'])['attachments'])+'\n---\n## threads\n投票本文。\n<!-- thth: 2/2 -->\n次の投票。\n'
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='threads',base_url=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    token={'access_token':secrets.token_urlsafe(30),'user_id':'123'};monkeypatch.setattr(accounts,'load_token',lambda _:token)
    adapter=ThreadsAdapter(base_url=wire['url'],access_token=token['access_token'],user_id='123',wait_seconds=0)
    if outcome=='stale':wire['on_create']=lambda:path.write_text(path.read_text().replace('first','changed'))
    if version==1:
        result=core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    else:
        from thth import threadthrow
        from thth import jst
        result=threadthrow.publish_bundle('alpha',str(path.relative_to(pair['work'])),adapter_factory=lambda *a:adapter,now=jst.parse('2030-01-01T12:01:00+09:00'),log=lambda _:None,max_posts=1)
    if outcome=='success':assert posts(wire,'/threads_publish') and json.loads(posts(wire,'/threads')[0][2]['poll_attachment'][0])['option_a']=='first'
    else:assert not posts(wire,'/threads_publish') and 'changed' in path.read_text()
