import json
import pytest
from thth import accounts, adapters, cli, engagements, jst, runs, where_cli
from thth.adapters import bluesky, mastodon
NOW=jst.parse('2026-09-09T10:00:00+09:00')

@pytest.fixture
def rows():
    def row(pid,key,stamp='2026-09-09T00:00:00Z',**extra):
        return dict(message_id=pid,author_key=key,username=key,timestamp=stamp,text='PRIVATE',**extra)
    return [row('old','old','2026-09-01T00:00:00Z'),row('engaged','a'*16),row('first','b'*16,reply_count=3),row('second','b'*16),row('unknown1',None),row('unknown2',None)]


def setup(name,media,rows,isolated_account_factory,monkeypatch):
    isolated_account_factory(name,media=media)
    monkeypatch.setattr(accounts,'load_token',lambda cfg:{'access_token':'FAKE','identifier':'a','app_password':'FAKE'})
    calls=[]
    class Fake:
        def keyword_search(self,word,**kwargs):calls.append(kwargs);return rows
        def tag_search(self,*a,**kw):calls.append({'tag_since':kw['since']});return {'n':0}
        def tag_observation(self,*a,**kw):calls.append({'tag_since':kw['since']});return {'n':0}
    monkeypatch.setattr(adapters,'make_adapter',lambda *a:Fake())
    engagements.append(accounts.load_account(name),name,dict(account=name,medium=media,author_key='a'*16,post_id='mine',reply_to='other',posted_at='2026-09-09T09:00:00+09:00'))
    return calls


def test_mastodon_filters_share_one_population_and_record_counts(rows,isolated_account_factory,monkeypatch,capsys):
    calls=setup('one','mastodon',rows,isolated_account_factory,monkeypatch)
    assert cli.main(['where','one','tea','--since','7d','--exclude-engaged','--max-per-author','1','--json'])==0
    result=json.loads(capsys.readouterr().out);entry=result['by_account']['one']['by_word']['tea']
    assert [r['post_id'] for r in entry['posts']]==['first','unknown1','unknown2']
    assert entry['dropped']=={'since':1,'exclude_engaged':1,'max_per_author':1}
    assert entry['material']['n']==3 and entry['material']['authors']['distinct']==1
    assert [r['reply_count'] for r in entry['posts']]==[3,None,None]
    assert runs.read_runs(accounts.state_dir_for('one'))[-1]['n']==3
    assert 'since' not in calls[-1]


def test_bsky_since_is_not_shadowed_by_tag_window_or_created_at(rows,isolated_account_factory,monkeypatch):
    calls=setup('one','bluesky',rows,isolated_account_factory,monkeypatch)
    node,why=where_cli._account_node('one',['tea'],search_type='TOP',limit=20,now=NOW,since='7d')
    assert why is None and calls[-1]['since']=='2026-09-02T10:00:00+09:00'
    assert calls[0]['tag_since']=='2026-09-08T10:00:00+09:00'
    assert node['by_word']['tea']['material']['n']==6
    assert node['by_word']['tea']['dropped']['since'] is None
    assert node['by_tag'][0]['window_basis']=='independent_24h'


def test_adapter_transmits_since_and_reply_counts(monkeypatch):
    instance=bluesky.BlueskyAdapter();calls=[]
    monkeypatch.setattr(instance,'_request',lambda *a,**kw:(calls.append(kw) or {'posts':[]}))
    assert instance.keyword_search('tea',since='2026-09-02T00:00:00Z')==[]
    assert calls[0]['params']['since']=='2026-09-02T00:00:00Z'
    b=instance._search_row({'uri':'at://x/y/z','replyCount':4})
    m=mastodon.MastodonAdapter(instance='https://example.invalid',access_token='FAKE')._search_row({'id':'123','replies_count':5})
    assert where_cli._posts_with_preview([b,m,{}],{})[0]['reply_count']==4
    assert where_cli._posts_with_preview([b,m,{}],{})[1]['reply_count']==5
    assert where_cli._posts_with_preview([b,m,{}],{})[2]['reply_count'] is None

@pytest.mark.parametrize('opts',[{'since':'bad'},{'max_per_author':0},{'max_per_author':True}])
def test_invalid_filters_before_network(opts,isolated_account_factory,monkeypatch):
    isolated_account_factory('one',media='bluesky')
    monkeypatch.setattr(adapters,'make_adapter',lambda *a:pytest.fail('invalid filters reached adapter'))
    with pytest.raises(where_cli.WhereError):where_cli.answer(account_name='one',words=['tea'],**opts)
