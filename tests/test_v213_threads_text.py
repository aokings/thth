"""Long text's new count and ASCII-only uncertain offset contract."""
import json
import pytest
from thth import accounts,core,media,media_delivery,media_relay
from thth.adapters import base
from tests.test_v213_threads_media import env,wire,invoke,posts


def text_attachment(value='long body',**fields):return {'attachments':[dict(type='text',text=value,**fields)]}


@pytest.mark.parametrize('value',['x'*10000,'茶'*10000,'😀'*10000,'e\u0301'*5000])
def test_plaintext_codepoint_boundary_and_wire(env,wire,monkeypatch,value):
    monkeypatch.setattr(media_relay,'MediaRelay',lambda *a:pytest.fail('long text relay'))
    result,journal,_=invoke(env,fm=text_attachment(value),post=base.Post(''))
    assert result.post_id=='100' and journal['media']['phase']=='published'
    assert json.loads(posts(wire,'/threads')[0][2]['text_attachment'][0])=={'plaintext':value}
    assert len(wire['calls'])==3 and not env[3]['upload'] and not wire['fetched']
    assert any('provisional Unicode code points' in note for note in media_delivery.lint_notes(env[0],text_attachment(value),text=''))


@pytest.mark.parametrize('value',['x'*10001,'茶'*10001,'😀'*10001,'e\u0301'*5000+'z'])
def test_over_limit_precedes_network(env,wire,value):
    result,_,_=invoke(env,fm=text_attachment(value))
    assert result.error=='media_limit_exceeded: text_attachment_characters' and not wire['calls']


def style(offset=0,length=1,names=None):return dict(offset=offset,length=length,styling_info=names or ['bold'])


def test_ascii_styling_adjacent_ranges_and_link_exact_wire(env,wire):
    styles=[style(2,2,['highlight','underline','strikethrough']),style(0,2,['bold','italic'])]
    fm=text_attachment('ABCD',styles=styles,link='https://card.invalid')
    result,_,_=invoke(env,fm=fm)
    assert result.post_id and json.loads(posts(wire,'/threads')[0][2]['text_attachment'][0])=={'plaintext':'ABCD','text_with_styling_info':styles,'link_attachment_url':'https://card.invalid'}
    assert not wire['fetched']


@pytest.mark.parametrize('value',['A茶B','A😀B','e\u0301'])
def test_nonascii_offsets_are_unverified_without_guessing(env,wire,value):
    result,_,_=invoke(env,fm=text_attachment(value,styles=[style()]))
    assert result.error=='threads_offset_unit_unverified' and not wire['calls']


@pytest.mark.parametrize('styles,reason',[
    ([style(0,2),style(1,1)],'style_overlap'),([style(0,1),style(0,1)],'style_overlap'),
    ([style(3,2)],'style_range'),([style(names=['BOLD'])],'style_name'),
    ([dict(offset=0,length=1,styling_info=[])],'style_name'),
])
def test_invalid_styles_have_static_reason(env,wire,styles,reason):
    result,_,_=invoke(env,fm=text_attachment('ABCD',styles=styles))
    assert result.error=='invalid_attachment: threads/'+reason and not wire['calls']


@pytest.mark.parametrize('count',[5,6])
def test_combined_links_are_counted_without_fetch(env,wire,count):
    urls=['https://h'+str(i)+'.invalid' for i in range(count)]
    fm=text_attachment(' '.join(urls[1:]),link=urls[0])
    result,_,_=invoke(env,fm=fm,post=base.Post(urls[0]))
    if count==5:assert result.post_id
    else:assert result.error=='media_limit_exceeded: links' and not wire['calls']
    assert not wire['fetched']


def test_distinct_explicit_link_locations_are_refused(env,wire):
    fm=text_attachment(link='https://a.invalid');fm['attachments'].append({'type':'link','url':'https://b.invalid'})
    result,_,_=invoke(env,fm=fm)
    assert result.error=='invalid_attachment: threads/text_link_conflict' and not wire['calls']


@pytest.mark.parametrize('field,value',[('text','other'),('link','https://changed.invalid'),('styles',[style(1)])])
def test_every_declared_text_field_is_bound(env,field,value):
    fm=text_attachment('ABCD',link='https://a.invalid',styles=[style()]);before=media.prepared_component(media.manifest_for(fm,env[0]))
    fm['attachments'][0][field]=value;after=media.prepared_component(media.manifest_for(fm,env[0]))
    assert before!=after


def test_unknown_text_publication_not_replayed(env,wire):
    wire['publish']=(500,{})
    result,journal,_=invoke(env,fm=text_attachment());assert result.failure=='media_ambiguous' and journal['media']['phase']=='unknown'
    count=len(wire['calls']);again=core.send_once('alpha',text='body',production_flag=True,confirm='unused',adapter_factory=lambda *a:env[1],log=lambda _:None)
    assert again.action=='inflight' and len(wire['calls'])==count


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('outcome',['success','stale'])
def test_actual_git_approved_long_text(tmp_path,isolated_account_factory,wire,monkeypatch,version,outcome):
    from pathlib import Path
    from tests.conftest import init_git_pair,make_queue_text,approve_via_cli
    from thth import jst,threadthrow
    from thth.adapters.threads import ThreadsAdapter
    import secrets
    declared=text_attachment('approved long text',styles=[style(0,8)])['attachments']
    if version==1:
        raw=make_queue_text({'account':'alpha','status':'draft','approved_sha':'','topic':''},body='## threads\n\n本文。\n')
        raw=raw.replace('\n---\n','\nattachments: '+json.dumps(declared)+'\n---\n',1)
    else:
        raw='---\nthth: 2\naccount: alpha\npublish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft\nposts:\n'
        for i in (1,2):raw+='  - index: '+str(i)+'\n    attachments: '+json.dumps(declared)+'\n'
        raw+='---\n## threads\n本文。\n<!-- thth: 2/2 -->\n次の本文。\n'
    pair=init_git_pair(tmp_path,seed_content=raw);path=Path(pair['queue_dir'])/'a.md'
    isolated_account_factory(name='alpha',repo_dir=pair['work'],media='threads',base_url=wire['url'],production=True,hashtags=False,char_limit=500,quiet_hours=None,min_interval_hours=0)
    approved=approve_via_cli(path);assert approved.returncode==0,approved.stderr
    token={'access_token':secrets.token_urlsafe(30),'user_id':'123'};monkeypatch.setattr(accounts,'load_token',lambda _:token)
    adapter=ThreadsAdapter(base_url=wire['url'],access_token=token['access_token'],user_id='123',wait_seconds=0)
    if outcome=='stale':wire['on_create']=lambda:path.write_text(path.read_text().replace('approved long text','changed long text'))
    if version==1:core.throw_once('alpha',production_flag=True,adapter_factory=lambda *a:adapter,log=lambda _:None,bypass_pace=True)
    else:threadthrow.publish_bundle('alpha',str(path.relative_to(pair['work'])),adapter_factory=lambda *a:adapter,now=jst.parse('2030-01-01T12:01:00+09:00'),log=lambda _:None,max_posts=1)
    if outcome=='success':assert posts(wire,'/threads_publish') and json.loads(posts(wire,'/threads')[0][2]['text_attachment'][0])['plaintext']=='approved long text'
    else:assert not posts(wire,'/threads_publish') and 'changed long text' in path.read_text()


def test_repeated_known_style_names_are_preserved(env,wire):
    styles=[style(0,2,['bold','bold','italic'])]
    result,_,_=invoke(env,fm=text_attachment('ABCD',styles=styles))
    assert result.post_id and json.loads(posts(wire,'/threads')[0][2]['text_attachment'][0])['text_with_styling_info']==styles
