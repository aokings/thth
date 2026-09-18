import datetime as dt
import json
from pathlib import Path
from thth import collection_context, jst

NOW=jst.parse('2026-10-01T01:00:00+09:00')

def daily(value,age,**extra):
    return {'account':'a','date':'ignored','collected_at':jst.iso(NOW-dt.timedelta(hours=age)),
            'metrics':{'followers_count':value},**extra}

def test_recent_daily_across_month_and_date_ignored(tmp_path):
    (tmp_path/'a-2026-09.ndjson').write_text(json.dumps(daily(120,2,date='2026-09-29')))
    value=collection_context.followers('a',tmp_path,NOW)
    assert value=={'followers_count':120,'followers_count_at':'2026-09-30T23:00:00+09:00',
                  'staleness_hours':2.0,'source':'insights_account'}
    (tmp_path/'a-2026-10.ndjson').write_text(json.dumps(daily(121,1.234)))
    value=collection_context.followers('a',tmp_path,NOW)
    assert value['followers_count']==121 and value['staleness_hours']==1.23

def test_recent_daily_inclusive_48h_and_no_old_fallback(tmp_path):
    p=tmp_path/'a-2026-09.ndjson'
    p.write_text(json.dumps(daily(0,48)))
    assert collection_context.followers('a',tmp_path,NOW)['followers_count']==0
    p.write_text(json.dumps(daily(2,49)))
    assert collection_context.followers('a',tmp_path,NOW) is None
    p.write_text('\n'.join(map(json.dumps,[daily(2,3),daily(True,1)])))
    assert collection_context.followers('a',tmp_path,NOW) is None

def test_recent_daily_collect_reason_and_no_retrofit(tmp_path,isolated_account_factory,monkeypatch):
    from tests.test_v31_collection import setup_route,Adapter,POSTED,daily_path,daily_row
    from thth import collect,accounts
    a=setup_route(tmp_path,isolated_account_factory,monkeypatch,'sent')
    now=POSTED+dt.timedelta(hours=1)
    p=daily_path(a)
    p.write_text(json.dumps(daily_row(a,9,collected_at=jst.iso(now-dt.timedelta(hours=49)))))
    collect.collect_once(a['name'],adapter=Adapter(),now=now,log=lambda _:None)
    out=Path(accounts.data_dirs(a,a['name'])['insights_posts'])/'P.ndjson'
    before=out.read_bytes()
    assert json.loads(before)['context'] is None
    assert json.loads(before)['context_reason']=='no_recent_daily_record'
    p.write_text(json.dumps(daily_row(a,0,collected_at=jst.iso(now))))
    collect.collect_once(a['name'],adapter=Adapter(),now=POSTED+dt.timedelta(hours=6),log=lambda _:None)
    assert out.read_bytes().startswith(before)
    assert json.loads(out.read_text().splitlines()[-1])['context']['followers_count']==0
