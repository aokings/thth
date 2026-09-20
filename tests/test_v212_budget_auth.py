import json
from pathlib import Path
import pytest
from thth import budget_x as b,oauth,authflow,httpsafe
from thth.adapters import auth_x as x
from tests.test_v211_x_auth import env,run,old_token,saved


@pytest.mark.parametrize('operation',['auth','refresh'])
def test_default_zero_refuses_before_irreversible_post_with_visible_reason(env,operation):
    (b.folder()/'budget_x.json').unlink()
    before,_=old_token(env)
    rc=run(env) if operation=='auth' else oauth.run_refresh('alpha',force=True,log=env['lines'].append)
    assert rc==2 and env['calls']==[] and Path(env['cfg']['token']).read_bytes()==before
    assert any('budget_exhausted' in line for line in env['lines'])
    assert b.read()['reservations']=={}


@pytest.mark.parametrize('operation',['auth','refresh'])
def test_reservation_is_durable_before_post_and_settled_after_user_return(env,operation):
    b.configure('0.010',by='operator');observed=[]
    def hook():
        rows=list(b.read()['reservations'].values());assert len(rows)==1
        observed.append((env['calls'][-1][1],rows[0]['state'],b.report()['held_usd']))
    env['hook']=hook
    if operation=='auth':assert run(env)==0
    else:old_token(env);assert oauth.run_refresh('alpha',force=True,log=env['lines'].append)==0
    assert observed==[('/2/oauth2/token','post_started','0.010'),('/2/users/me','get_started','0.010')]
    assert saved(env)['user_id']=='123' and b.report()['spent_estimate_usd']=='0.010' and b.report()['held_usd']=='0'
    assert run(env)==2 and len(env['calls'])==2


@pytest.mark.parametrize('failure',['identity','commit'])
def test_successful_user_read_is_charged_even_when_auth_fails(env,monkeypatch,failure):
    before,_=old_token(env)
    if failure=='identity':env['behavior']['me']={'username':'wrong'}
    else:monkeypatch.setattr(authflow,'commit',lambda *a,**k:(_ for _ in ()).throw(ValueError('synthetic')))
    assert run(env)==2 and Path(env['cfg']['token']).read_bytes()==before
    assert b.report()['spent_estimate_usd']=='0.010' and b.report()['held_usd']=='0'


def test_get_timeout_holds_reservation_but_token_response_failure_does_not(env,monkeypatch):
    original=httpsafe.urlopen
    def request(req,**kw):
        if req.full_url.endswith('/2/users/me'):raise TimeoutError('synthetic')
        return original(req,**kw)
    monkeypatch.setattr(httpsafe,'urlopen',request)
    assert run(env)==2 and b.report()['held_usd']=='0.010'
    monkeypatch.setattr(httpsafe,'urlopen',original);env['behavior']['token']={'scope':'tweet.read'}
    assert run(env)==2 and b.report()['held_usd']=='0.010'
    assert sorted(r['state'] for r in b.read()['reservations'].values())==['released','uncertain']


def test_transport_rejects_unreserved_or_unknown_read_and_does_not_budget_post(env):
    with pytest.raises(b.BudgetError,match='reservation_required'):x.request('/2/users/me',token=env['token'])
    with b.user_read('alpha'):
        b.before_post()
        with pytest.raises(b.BudgetError,match='reservation_required'):x.request('/2/unknown',token=env['token'])
    assert env['calls']==[]
    b.configure('0',by='operator')
    with pytest.raises(x.FlowError):x.request('/2/tweets',data={},token=env['token'])
    assert [c[1] for c in env['calls']]==['/2/tweets'] # future transport contract, not a posting adapter


def test_missing_fx_read_refusal_visible_in_auth(env):
    b.configure('3',currency='JPY',rate='150',rate_source='synthetic 2026-09-20',by='operator')
    with b.locked() as fd:
        data=b._read(fd);data['policy']['rate_source']=None;b._save(fd,data)
    assert run(env)==2 and env['calls']==[] and any('missing_rate' in line for line in env['lines'])
