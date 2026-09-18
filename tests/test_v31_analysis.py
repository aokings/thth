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
    ledger['fetches'] = [{'post_id':'mine', 'collected_at': jst.iso(NOW)}]
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (False, None)
    ledger['replies'] = [{'post_id':'mine', 'collected_at': jst.iso(NOW), 'timestamp': jst.iso(POSTED+dt.timedelta(hours=2)), 'own': False, 'replied_to': {'id':'mine'}, 'author_key': e['author_key'], 'text':'SECRET'}]
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (True, 2)
    ledger['replies'][0]['replied_to'] = {'id': 'someone_else'}
    assert t.reaction('a', 'threads', e, POSTED, NOW) == (False, None)

def test_a3_skip_not_success(tmp_path, monkeypatch):
    import json
    from thth import collection_status as s
    monkeypatch.setattr(s.accounts, 'state_dir_for', lambda name: tmp_path)
    assert s.summarize('a', NOW)[0] is None
    p = tmp_path/'runs-2026-09.ndjson'
    rows = [{'account':'a', 'mode':'collect', 'action':'collect', 'run_id':'collect-'+jst.iso(POSTED), 'status':'ok','collected':0},
            {'account':'a', 'mode':'collect', 'action':'collect', 'run_id':'collect-'+jst.iso(NOW), 'status':'ok','collected':None}]
    p.write_text('\n'.join(map(json.dumps,rows)))
    value, reasons = s.summarize('a', NOW)
    assert value['last_success_at'] == jst.iso(POSTED)
    assert value['last_attempt_ok'] is None
    rows[-1]['collected'] = True
    p.write_text('\n'.join(map(json.dumps,rows)))
    assert s.summarize('a', NOW)[0]['last_attempt_ok'] is None

def test_a6_unknown_stratum_reconciles(isolated_account_factory):
    from tests.test_analytics_comparison import seed, report
    from thth import analytics_report
    a = isolated_account_factory()
    seed(a, 'p', NOW-dt.timedelta(days=3), value=0)
    result = analytics_report.answer(a['name'], now=NOW, compare_previous=True, by='kind', min_n=1)
    group = result['by_account'][a['name']]['posts']
    assert group['stratified']['strata']['unknown']['current']['n_total'] == 1
    assert group['stratified']['reconciliation']['current'] == {'sum_n_total':1,'n_total':1}
    assert group['stratified']['strata']['unknown']['current']['metrics']['views']['median'] == 0

def test_a7_collected_time_bounds_history(monkeypatch):
    from thth import analytics_shapes as s
    early = POSTED+dt.timedelta(hours=23)
    late = POSTED+dt.timedelta(hours=25)
    ledger = {'broken': [], 'fetches':[{'post_id':'p', 'collected_at':jst.iso(early)}, {'post_id':'p', 'collected_at':jst.iso(late)}], 'replies':[
        {'post_id':'p','id':'r','own':False,'username':'private','text':'SECRET','replied_to':{'id':'p'},
         'timestamp':jst.iso(POSTED+dt.timedelta(hours=1)), 'collected_at':jst.iso(late)}]}
    monkeypatch.setattr(s.replies,'load',lambda *a,**k:ledger)
    value=s.shape_at('a','p',POSTED,NOW)
    assert value['24']['replies_total']==0
    assert value['72']['replies_total']==1
    assert value['168'] is None
    assert 'SECRET' not in str(value) and 'private' not in str(value)
    ledger['fetches']=[]
    assert s.shape_at('a','p',POSTED,NOW)['24'] is None

def test_a9_forecast_only_immature():
    from thth.study_report import _eligibility_forecast
    late=NOW-dt.timedelta(hours=1)
    value=_eligibility_forecast({'evidence':[
        {'status':'immature','posted_at':jst.iso(NOW-dt.timedelta(hours=2))},
        {'status':'immature','posted_at':jst.iso(late)},
        {'status':'incomplete','posted_at':jst.iso(POSTED)}]})
    assert value=={'posts_immature':2, 'all_eligible_at':jst.iso(late+dt.timedelta(hours=24)), 'basis':'posted_at_plus_24h'}
    assert _eligibility_forecast({'evidence':[]}) is None

def test_a4_explicit_cli_write_and_readonly_delta(isolated_account_factory, monkeypatch, capsys):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from thth import operations_handoff as h, handoff_cursor as cursor, accounts
    from tests.test_operations_handoff import seed
    a=isolated_account_factory()
    name=a['name']
    monkeypatch.setattr(h.jst,'now_jst',lambda:NOW)
    seed(a,'draft.md','draft')
    path=Path(accounts.state_dir_for(name))/'handoff_cursor.json'
    args=SimpleNamespace(account=name,project=None,json=True,mark_read=False,by=None,since_last_read=True)
    assert h.cmd_handoff_report(args)==0
    assert not path.exists()
    args.mark_read=True
    assert h.cmd_handoff_report(args)==2
    assert not path.exists()
    args.by='reader'
    assert h.cmd_handoff_report(args)==0
    saved=path.read_bytes()
    node=h.answer(name,now=NOW,since_last_read=True)['by_account'][name]
    assert node['changes_since']['changes']==[]
    assert 'no_previous_session_cursor' not in node['cannot_say']
    seed(a,'draft2.md','draft')
    node=h.answer(name,now=NOW,since_last_read=True)['by_account'][name]
    assert next(r for r in node['changes_since']['changes'] if r['field']=='queue_counts.draft')['delta']==1
    assert path.read_bytes()==saved
    path.unlink()
    path.symlink_to(path.parent/'outside')
    assert cursor.read(name,NOW)[1]=='cursor_unreadable'
    args.mark_read=True
    assert h.cmd_handoff_report(args)==2
    assert not (path.parent/'outside').exists()

def test_a4_unknown_counts_not_zero():
    from thth.handoff_cursor import changes
    import copy
    base={'queue_counts':dict.fromkeys(['draft','approved_waiting','overdue','malformed','unattributed_malformed']),
          'inflight':{'present':None,'since':None},'notification_last_event_id':None,
          'notification_recorded_state':None,'run_last_attempt_at':None,'run_recorded_state':None,
          'last_post_observed_at':None,'sent_count':None}
    current=copy.deepcopy(base);current['queue_counts']['draft']=0
    assert changes(base,current)==[{'field':'queue_counts.draft','previous':None,'current':0,'delta':None}]
