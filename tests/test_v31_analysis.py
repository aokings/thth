import datetime as dt
from thth import analytics_comparison as c, jst

NOW = jst.parse('2026-09-18T12:00:00+09:00')
POSTED = NOW - dt.timedelta(days=3)

def row(mark, age, value=0):
    return {'posted_at': jst.iso(POSTED), 'collected_at': jst.iso(POSTED + dt.timedelta(hours=age)),
            'marks': [mark], 'metrics': {'likes': value}}

def test_a1_strict_marks():
    post = {'rows': [row(1, 1.25, 8), row(1, 1, 0), row(6, 7.5), row(24, 25, 9)]}
    result = c._marks_population([('p', POSTED, post)], POSTED, NOW, NOW, 1)
    assert result['by_mark']['1']['metrics']['likes']['median'] == 0
    assert result['by_mark']['1']['metrics']['views']['median'] is None
    assert result['marks_by_post'][0]['marks']['6'] is None
    assert result['by_mark']['24']['metrics']['likes']['median'] == 9
    assert result['by_mark']['168']['n_eligible'] == 0
    assert c._marks_population([('p', POSTED, post)], POSTED, NOW, NOW, 2)['by_mark']['1']['metrics']['likes']['median'] is None

def test_a1_reject_collapsed_future_conflicting():
    rows = [row(1, 1), row(1, 2), row(1, 1)]
    rows[0]['marks_collapsed'] = True
    rows[2]['posted_at'] = jst.iso(POSTED-dt.timedelta(hours=1))
    assert c._observation({'rows': rows}, POSTED, NOW, 1)[0] is None
    assert c._observation({'rows': [row(168, 168)]}, POSTED, NOW, 168)[0] is None

def test_a5_spread_gate_and_odd_center():
    assert c._spread([1, 2, 3, 4], 5) == {'iqr': None, 'min': None, 'max': None, 'spread_reason': 'below_min_n'}
    assert c._spread([0], 1) == {'iqr': None, 'min': 0, 'max': 0, 'spread_reason': 'too_few_for_quartiles'}
    assert c._spread([1, 2, 3], 1)['iqr'] is None
    assert c._spread([1, 2, 50, 80, 100], 1)['iqr'] == 88.5
    assert c._spread([1, 2, 3, 4], 1)['iqr'] == 2

def test_a8_details_preserve_order_text():
    from thth.report_details import attach
    messages = ['timer_health_unknown', '自分の投稿の 24h views が欠測: 2 本', '未知の説明']
    result = attach({'cannot_say': list(messages)})
    assert result['cannot_say'] == messages
    assert result['cannot_say_details'] == [
        {'code': 'timer_health_unknown', 'text': messages[0]},
        {'code': 'post_views_missing', 'text': messages[1]},
        {'code': 'report_limitation', 'text': messages[2]}]

def test_a1_malformed_mark_types():
    for mark in (1, 6, 24, 72, 168):
        for bad in (True, False, str(mark), float(mark)):
            value = row(mark, mark)
            value['marks'] = [bad]
            assert c._observation({'rows': [value]}, POSTED, NOW + dt.timedelta(days=10), mark)[0] is None

def test_a2_reaction_identity_and_missing(monkeypatch):
    from thth import analytics_threads as t
    ledger = {'broken': [], 'unreadable_accounts': [], 'fetches': [], 'replies': []}
    monkeypatch.setattr(t.replies, 'load', lambda *a, **k: ledger)
    e = {'post_id': 'mine', 'author_key': '0123456789abcdef'}
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (None, None)
    ledger['fetches'] = [{'collected_at': jst.iso(NOW)}]
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (False, None)
    ledger['replies'] = [{'collected_at': jst.iso(NOW), 'timestamp': jst.iso(POSTED+dt.timedelta(hours=2)), 'own': False, 'replied_to': {'id':'mine'}, 'author_key': e['author_key'], 'text':'SECRET'}]
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (True, 2)
    ledger['replies'][0]['replied_to'] = {'id': 'someone_else'}
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (False, None)

def test_a3_skip_not_success(tmp_path, monkeypatch):
    import json
    from thth import collection_status as s
    monkeypatch.setattr(s.accounts, 'state_dir_for', lambda name: tmp_path)
    assert s.summarize('a', NOW)[0] is None
    p = tmp_path/'runs-2026-09.ndjson'
    rows = [{'account':'a', 'action':'collect', 'run_id':'collect-'+jst.iso(POSTED), 'status':'ok','collected':0},
            {'account':'a', 'action':'collect', 'run_id':'collect-'+jst.iso(NOW), 'status':'ok','collected':None}]
    p.write_text('\n'.join(map(json.dumps,rows)))
    value, reasons = s.summarize('a', NOW)
    assert value['last_success_at'] == jst.iso(POSTED)
    assert value['last_attempt_ok'] is None
    rows[-1]['collected'] = True
    p.write_text('\n'.join(map(json.dumps,rows)))
    assert s.summarize('a', NOW)[0]['last_attempt_ok'] is None
