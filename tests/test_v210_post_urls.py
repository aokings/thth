import json
from pathlib import Path
import urllib.error
import pytest
from thth import accounts, adapters, after_cli, cli, core, engagements, postid, thread_read, where_cli
from thth.adapters import bluesky

URI='at://did:plc:alice/app.bsky.feed.post/abc123'
URL='https://bsky.app/profile/alice.bsky.social/post/abc123'

@pytest.fixture
def resolved(monkeypatch):
    calls=[]
    def lookup(service,method,nsid,**kwargs):
        calls.append((service,method,nsid,kwargs));return {'did':'did:plc:alice'}
    monkeypatch.setattr(bluesky,'_xrpc',lookup)
    return calls


def test_public_handle_and_did_urls_have_identical_uri(resolved):
    cfg={'media':'bluesky'}
    assert postid.for_account(cfg,URL)==URI
    assert resolved==[(bluesky.DEFAULT_SERVICE,'GET','com.atproto.identity.resolveHandle',{'params':{'handle':'alice.bsky.social'},'bearer':None})]
    assert postid.for_account(cfg,URL.replace('alice.bsky.social','did:plc:alice'))==URI
    assert postid.for_account(cfg,URI)==URI and len(resolved)==1
    assert postid.for_account({'media':'mastodon'},URL)==URL

@pytest.mark.parametrize('url',[URL.replace('https:','http:'),URL.replace('bsky.app/','evil.invalid/'),URL+'?q=1',URL+'#x',URL+'/extra',URL.rsplit('/',1)[0]+'/', 'https://[broken'])
def test_bad_urls_are_bounded_without_lookup(url,resolved):
    with pytest.raises(postid.PostIdError,match='invalid_post_url'):
        postid.for_account({'media':'bluesky'},url)
    assert resolved==[]


def test_lookup_failure_explains_unresolved_without_credentials(monkeypatch):
    def fail(*a,**k):raise urllib.error.URLError('PRIVATE ERROR')
    monkeypatch.setattr(bluesky,'_xrpc',fail)
    with pytest.raises(postid.PostIdError,match='handle_unresolved') as exc:postid.for_account({'media':'bluesky'},URL)
    assert 'PRIVATE' not in str(exc.value)


def test_all_four_entries_use_the_canonical_id(isolated_account_factory,resolved,monkeypatch,capsys):
    cfg=isolated_account_factory('one',media='bluesky',handle='owner.bsky.social')
    config=accounts.load_account('one')
    # Same target must produce the same send confirmation digest.
    first=core.send_once('one',text='tea',reply_to=URL)
    second=core.send_once('one',text='tea',reply_to=URI)
    assert first.exit_code==second.exit_code==0 and first.digest==second.digest and first.digest
    calls=[]
    class Fake:
        def fetch_post(self,pid):calls.append(pid);return {'message_id':pid,'text':'root','username':'alice.bsky.social'}
        def conversation(self,pid,**kw):calls.append(pid);return []
    monkeypatch.setattr(accounts,'load_token',lambda cfg:{'app_password':'FAKE','identifier':'owner'})
    monkeypatch.setattr(adapters,'make_adapter',lambda *a:Fake())
    assert thread_read.answer('one',URL)['root']['post_id']==URI
    assert calls==[URI,URI]
    engagements.append(config,'one',dict(post_id='ANSWER',reply_to=URI,root_post=URI,author_key='a'*16,account='one',medium='bluesky',topic=None,kind=None,hour_band='朝',posted_at='2026-09-09T09:00:00+09:00',found_by='manual'))
    assert after_cli.answer('one',reply_to=URL,min_n=1)['engagements']['n']==1
    directory=Path(accounts.data_dirs(config,'one')['replies']);directory.mkdir(parents=True,exist_ok=True)
    (directory/(postid.to_filename(URI)+'.ndjson')).write_text(json.dumps({'id':'reply','post_id':URI,'username':'alice'})+'\n')
    capsys.readouterr()
    assert cli.main(['replies','one','--post',URL,'--json'])==0
    assert json.loads(capsys.readouterr().out)['replies'][0]['post_id']==URI


def test_where_human_shows_stable_id_below_web_url(capsys):
    post=dict(replied=None,timestamp=None,author='alice',author_key=None,preview='tea',permalink=URL,post_id=URI)
    where_cli._render_human(dict(account='one',project=None,words=['tea'],by_account={'one':dict(medium='bluesky',by_word={'tea':dict(material=dict(n=1,authors={'distinct':1},latest_timestamp=None),my_history=None,posts=[post])},by_tag=[],cannot_say=[])},cannot_say=[],provenance={'notes':[]}))
    output=capsys.readouterr().out
    assert output.index(URL)<output.index('post_id: '+URI)

@pytest.mark.parametrize('char', ['\n','\t','\r','\x00','\x1f','\x7f','\x85'])
def test_control_characters_are_rejected_before_resolver_or_send(char,resolved,isolated_account_factory):
    isolated_account_factory('one',media='bluesky')
    value=URL[:-1]+char+URL[-1]
    with pytest.raises(postid.PostIdError,match='invalid_post_url'):
        core.send_once('one',text='tea',reply_to=value)
    assert resolved==[]
