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
