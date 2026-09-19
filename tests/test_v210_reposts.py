import datetime
import pytest
from thth import analytics_comparison as comparison, analytics_report, jst
from tests.test_analytics_comparison import seed, NOW

@pytest.mark.parametrize('medium',['threads','bluesky','mastodon'])
@pytest.mark.parametrize('value',[None,0,7])
def test_reposts_use_observed_values_even_without_views(medium,value):
    posted=NOW-datetime.timedelta(days=3)
    observations=[]
    for mark in (1,6,24):
        metrics=dict(likes=2,replies=3)
        if value is not None:metrics['reposts']=value
        observations.append(dict(posted_at=jst.iso(posted),collected_at=jst.iso(posted+datetime.timedelta(hours=mark)),marks=[mark],metrics=metrics,medium=medium))
    result=comparison._marks_population([('POST',posted,{'rows':observations})],posted,NOW,NOW,1)
    for mark in ('1','6','24'):
        row=result['by_mark'][mark]['metrics']
        assert row['views']['median'] is None
        assert row['likes']['median']==2 and row['replies']['median']==3
        assert row['reposts']['median']==value
        assert row['reposts']['n_eligible']==(0 if value is None else 1)


def test_project_reposts_never_sum_accounts(isolated_account_factory):
    first=isolated_account_factory('one',media='bluesky',project='tea')
    second=isolated_account_factory('two',media='mastodon',project='tea')
    for cfg,value in ((first,4),(second,9)):
        seed(cfg,cfg['name']+'POST',NOW-datetime.timedelta(days=3),extra={'metrics':{'likes':1,'replies':2,'reposts':value}})
    result=analytics_report.answer(project='tea',now=NOW,min_n=1,compare_previous=True)
    assert 'metrics' not in result and 'reposts' not in result
    for cfg,value in ((first,4),(second,9)):
        node=result['by_account'][cfg['name']]['posts']['current']
        assert node['metrics']['reposts']['median']==value
