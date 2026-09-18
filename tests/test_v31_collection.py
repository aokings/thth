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
