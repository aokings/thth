import datetime as dt
import json
from pathlib import Path
import pytest
from thth import collect as c, accounts, queuefile, sent, jst

POSTED=jst.parse('2026-09-01T00:00:00+09:00')

class Adapter:
    def __init__(self):
        self.insight_calls=[];self.reply_calls=[];self.fail=set()
    def capabilities(self):return set()
    def insights(self,pid):
        self.insight_calls.append(pid)
        if 'insights' in self.fail:raise RuntimeError('fixture fail')
        return {'metrics':{'views':0,'likes':2,'replies':3,'reposts':4,'quotes':5},'available':['views','likes','replies','reposts','quotes']}
    def conversation(self,pid):
        self.reply_calls.append(pid)
        if 'replies' in self.fail:raise RuntimeError('fixture fail')
        return []

def setup_route(tmp_path,factory,monkeypatch,route,collect_days=14,medium='threads'):
    a=factory(name='test-'+medium,media=medium,collect_days=collect_days)
    monkeypatch.setattr(c.writeback,'upstream_sha',lambda repo:None)
    files=[]
    path=tmp_path/'queue.md'
    if route=='queue':
        path.write_text(f'---\nthth: 1\naccount: {a["name"]}\nstatus: posted\npost_id: P\nposted_at: {jst.iso(POSTED)}\npublish_at: {jst.iso(POSTED)}\n---\n## {medium}\n\nfixture\n')
        files=[queuefile.parse_text(path.read_text(),str(path))]
    elif route=='sent':
        sent.write(accounts.state_dir_for(a['name']),post_id='P',text='fixture',body_hash='fixture',sent_at=jst.iso(POSTED))
    else:
        path.write_text(f'---\nthth: 2\naccount: {a["name"]}\nstatus: posted\npublish_at: {jst.iso(POSTED)}\ncontinue_until: {jst.iso(POSTED+dt.timedelta(hours=1))}\nposts:\n  - index: 1\n    post_id: P\n    posted_at: {jst.iso(POSTED)}\n---\n## {medium}\n\nfixture\n')
        files=[queuefile.parse_text(path.read_text(),str(path))]
    monkeypatch.setattr(c.core,'list_queue_files',lambda *a,**k:files)
    return a

@pytest.mark.parametrize('route',['queue','sent','bundle'])
@pytest.mark.parametrize('days',[14,40,60])
def test_c1_forty_day_clock(tmp_path,isolated_account_factory,monkeypatch,route,days):
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,route,days)
    adapter=Adapter()
    for hour in [0,1,6,24,72,168,*range(192,961,24)]:
        result=c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=hour),log=lambda x:None)
        assert not result['errors']
    assert adapter.insight_calls==['P']*6
    assert adapter.reply_calls==['P']*5
    dirs=accounts.data_dirs(a,a['name'])
    rows=c._read_ndjson(str(Path(dirs['insights_posts'])/'P.ndjson'))
    assert [r['marks'] for r in rows]==[[1],[6],[24],[72],[168],[720]]
    assert c.metric_window_days()==38

def test_c1_independent_retry_and_metric_window(tmp_path,isolated_account_factory,monkeypatch):
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,'sent',60)
    adapter=Adapter();adapter.fail={'insights'}
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=720),log=lambda x:None)
    adapter.fail=set()
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=901),log=lambda x:None)
    assert len(adapter.insight_calls)==2 and len(adapter.reply_calls)==1
    dirs=accounts.data_dirs(a,a['name'])
    rows=c._read_ndjson(str(Path(dirs['insights_posts'])/'P.ndjson'))
    from thth.analytics_comparison import _observation
    assert _observation({'rows':rows},POSTED,POSTED+dt.timedelta(hours=902),720)[0] is None
    # An absent ledger cannot extend the metric window using collect_days=60.
    (Path(dirs['insights_posts'])/'P.ndjson').unlink()
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=913),log=lambda x:None)
    assert len(adapter.insight_calls)==2

def test_c1_reply_failure_retries_without_extra_insights(tmp_path,isolated_account_factory,monkeypatch):
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,'queue')
    adapter=Adapter();adapter.fail={'replies'}
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=168),log=lambda x:None)
    adapter.fail=set()
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=169),log=lambda x:None)
    assert len(adapter.insight_calls)==1 and len(adapter.reply_calls)==2
    targets,_=c._refresh_targets(a['name'],a,now=POSTED+dt.timedelta(hours=720),errors=[],post_id=None)
    assert targets==[]

def daily_path(a):
    dirs=accounts.data_dirs(a,a['name'])
    path=Path(dirs['insights_account'])/f'{a["name"]}-2026-09.ndjson'
    path.parent.mkdir(parents=True,exist_ok=True)
    return path

def daily_row(a,value,**extra):
    return {'account':a['name'],'date':'2026-09-01','collected_at':jst.iso(POSTED),
            'metrics':{'followers_count':value},**extra}

@pytest.mark.parametrize('route',['queue','sent','bundle'])
def test_c2_context_at_append_no_backfill(tmp_path,isolated_account_factory,monkeypatch,route):
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,route)
    adapter=Adapter()
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=1),log=lambda x:None)
    p=Path(accounts.data_dirs(a,a['name'])['insights_posts'])/'P.ndjson'
    before=p.read_bytes();assert json.loads(before)['context'] is None
    daily_path(a).write_text(json.dumps(daily_row(a,0))+'\n')
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=2),log=lambda x:None)
    assert p.read_bytes()==before  # no new mark: no retrofit
    c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=6),log=lambda x:None)
    assert p.read_bytes().startswith(before)
    assert c._read_ndjson(str(p))[-1]['context']=={'followers_count':0,'followers_count_at':jst.iso(POSTED),'staleness_hours':6.0,'source':'insights_account'}
    assert len(adapter.insight_calls)==2

@pytest.mark.parametrize('value',[None,True,-1,float('nan'),float('inf'),1.5,'2'])
def test_c2_invalid_counts_are_missing(tmp_path,value):
    from thth.collection_context import followers
    a={'name':'a'};p=tmp_path/'a-2026-09.ndjson'
    p.write_text(json.dumps(daily_row(a,value)))
    assert followers('a',tmp_path,POSTED+dt.timedelta(hours=1)) is None

@pytest.mark.parametrize('change',[{'account':'other'}, {'collected_at':'2026-09-01T12:00:00+09:00'}, {'collected_at':'invalid'}])
def test_c2_scope_and_time_missing(tmp_path,change):
    from thth.collection_context import followers
    p=tmp_path/'a-2026-09.ndjson';p.write_text(json.dumps(daily_row({'name':'a'},7,**change)))
    assert followers('a',tmp_path,POSTED+dt.timedelta(hours=1)) is None

def test_c2_conflict_and_broken_fail_closed(tmp_path):
    from thth.collection_context import followers
    p=tmp_path/'a-2026-09.ndjson'
    rows=[daily_row({'name':'a'},7),daily_row({'name':'a'},8)]
    p.write_text('\n'.join(map(json.dumps,rows)))
    assert followers('a',tmp_path,POSTED+dt.timedelta(hours=1)) is None
    p.write_text(json.dumps(rows[0])+'\n{broken')
    assert followers('a',tmp_path,POSTED+dt.timedelta(hours=1)) is None
    p.write_text(json.dumps(rows[0]))
    # UTC previous date is JST current date: use the observation's JST date.
    utc_now=(POSTED+dt.timedelta(hours=1)).astimezone(dt.timezone.utc)
    assert followers('a',tmp_path,utc_now)['followers_count']==7

@pytest.mark.parametrize('medium',['threads','bluesky','mastodon'])
@pytest.mark.parametrize('route',['queue','sent','bundle'])
def test_c5_media_marks_and_native_metrics(tmp_path,isolated_account_factory,monkeypatch,medium,route):
    from thth.adapters import bluesky, mastodon
    import socket
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,route,medium=medium)
    def no_network(*a,**k):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket,'socket',no_network)
    adapter=Adapter()
    if medium=='bluesky':
        native=object.__new__(bluesky.BlueskyAdapter)
        native._post_view=lambda pid:{'likeCount':2,'replyCount':3,'repostCount':4,'quoteCount':5}
    elif medium=='mastodon':
        native=object.__new__(mastodon.MastodonAdapter)
        native._get_json=lambda *a,**k:{'favourites_count':2,'replies_count':3,'reblogs_count':4}
    else:native=None
    if native:
        def insights(pid):
            adapter.insight_calls.append(pid)
            return native.insights(pid)
        adapter.insights=insights
    for hour in [1,6,24,72,168,720,721,960]:
        result=c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=hour),log=lambda x:None)
        assert not result['errors']
    rows=c._read_ndjson(str(Path(accounts.data_dirs(a,a['name'])['insights_posts'])/'P.ndjson'))
    assert len(adapter.insight_calls)==6 and len(adapter.reply_calls)==5
    assert [r['marks'] for r in rows]==[[1],[6],[24],[72],[168],[720]]
    for row in rows:
        assert row['account']==a['name'] and row['medium']==medium
        assert {k:row['metrics'][k] for k in ('likes','replies','reposts')}=={'likes':2,'replies':3,'reposts':4}
        assert row['metrics']['views']==(0 if medium=='threads' else None)
        assert row['metrics']['quotes']==(None if medium=='mastodon' else 5)
    from thth.analytics_comparison import _marks_population
    report=_marks_population([('P',POSTED,{'rows':rows})],POSTED,POSTED+dt.timedelta(days=41),POSTED+dt.timedelta(days=41),1)
    assert report['by_mark']['720']['metrics']['views']['median']==(0 if medium=='threads' else None)

def test_c2_corrupt_optional_context_does_not_stop_collection(tmp_path,isolated_account_factory,monkeypatch):
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,'sent')
    path=daily_path(a)
    bad_inputs=[
        '['*10000+'0'+']'*10000,
        json.dumps(daily_row(a,7,collected_at='invalid')), 
        json.dumps(daily_row(a,7)).replace('"followers_count": 7','"followers_count": 0, "followers_count": 999')]
    adapter=Adapter()
    for hour,bad in zip((1,6,24),bad_inputs):
        path.write_text(bad)
        from thth.collection_context import followers
        assert followers(a['name'],path.parent,POSTED+dt.timedelta(hours=1)) is None
        result=c.collect_once(a['name'],adapter=adapter,now=POSTED+dt.timedelta(hours=hour),log=lambda x:None)
        assert not result['errors']
    rows=c._read_ndjson(str(Path(accounts.data_dirs(a,a['name'])['insights_posts'])/'P.ndjson'))
    assert len(rows)==3 and all(row['context'] is None for row in rows)
