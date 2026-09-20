"""Exact USD estimates; synthetic FX and fake transports, no live billing."""
import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import threading
import pytest
from thth import admin_log,budget_x as b,cli,jst,server_files


@pytest.fixture
def env(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir(mode=0o700);home=tmp_path/'home';home.mkdir()
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'));monkeypatch.setenv('HOME',str(home))
    for key in ('THTH_REPORT_CREDENTIALS','THTH_REPORT_TOKEN','THTH_APP_DIR'):monkeypatch.delenv(key,raising=False)
    (root/'accounts').mkdir()
    return root


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
def setcap(value='0.020',**kw):return b.configure(value,by='operator',**kw)
def one():
    with b.user_read('alpha'):
        b.before_post();b.before_get('/2/users/me');b.observed({'data':{'id':'synthetic'}})


def test_default_zero_read_only_and_no_reservation(env,capsys):
    before=snapshot(env)
    assert cli.main(['admin','budget','x','--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['cap_usd']=='0' and result['read_refusal']=='budget_exhausted' and result['usage'] is None
    assert snapshot(env)==before
    with pytest.raises(b.BudgetError,match='budget_exhausted'):
        with b.user_read('alpha'):pytest.fail('zero budget allowed token POST')
    assert b.read()['reservations']=={}


def test_set_without_auth_and_exact_spent_hold_audit(env):
    setcap();row=b.report();assert row['remaining_usd']=='0.020'
    with b.user_read('alpha'):
        assert b.report()['held_usd']=='0.010';b.before_post();b.before_get('/2/users/me');b.observed({'data':{'id':'fake'}})
        assert b.report()['spent_estimate_usd']=='0.010'
        with pytest.raises(b.BudgetError,match='used'):b.observed({'data':{'id':'fake'}})
    assert b.report()['remaining_usd']=='0.010' and b.report()['held_usd']=='0'
    events,broken=admin_log.read(event='budget_set');assert not broken and len(events)==1
    event=events[0];assert event['by']=='operator' and event['account']=='x' and event['diff']['budget'][0]['amount']=='0' and event['diff']['budget'][1]['amount']=='0.020'
    assert not (env/'state/x').exists()


def test_cap_and_fx_changes_preserve_usd_cost(env):
    setcap();one()
    assert setcap('0.005')['over_cap_usd']=='0.005'
    assert setcap('0.030')['remaining_usd']=='0.020'
    assert setcap('3',currency='JPY',rate='150',rate_source='synthetic 2026-09-20')['remaining_usd']=='0.010'
    value=setcap('3',currency='JPY',rate='200',rate_source='synthetic 2026-09-21')
    assert value['cap_usd']=='0.015' and value['remaining_usd']=='0.005' and value['spent_estimate_usd']=='0.010'
    assert len(b.read()['policy_history'])==5


@pytest.mark.parametrize('monthly,currency,rate,source',[('NaN','USD',None,None),('-1','USD',None,None),('1e3','USD',None,None),(True,'USD',None,None),('1','JPY',None,None),('1','JPY','0','source'),('1','JPY','150',''),('1','USD','1','source')])
def test_invalid_configuration_and_missing_actor_have_no_writes(env,monthly,currency,rate,source):
    before=snapshot(env)
    with pytest.raises(ValueError):b.configure(monthly,currency=currency,rate=rate,rate_source=source,by='operator')
    with pytest.raises(ValueError):b.configure('1',by=None)
    assert snapshot(env)==before


def test_jpy_missing_legacy_rate_is_unknown_not_reset(env):
    setcap('3',currency='JPY',rate='150',rate_source='synthetic date')
    with b.locked() as fd:
        data=b._read(fd);data['policy']['rate']=None;b._save(fd,data)
    before=snapshot(env);result=b.report()
    assert result['remaining_usd'] is None and result['cap_usd'] is None and result['read_refusal']=='missing_rate'
    assert snapshot(env)==before
    with pytest.raises(b.BudgetError,match='missing_rate'):
        with b.user_read('alpha'):pass


def test_no_float_rounding_or_jpy_division_admission(env):
    setcap('0.03');one();one();one()
    assert b.report()['spent_estimate_usd']=='0.030' and b.report()['remaining_usd']=='0'
    with pytest.raises(b.BudgetError):
        with b.user_read('alpha'):pass
    setcap('4',currency='JPY',rate='133.333333333333333334',rate_source='synthetic boundary')
    assert b.report()['read_refusal']=='budget_exhausted'
    assert b._can_reserve(b.policy('1',currency='JPY',rate='100.000000000000000001',rate_source='synthetic'),Decimal('0.010')) is False


def test_timeout_remains_held_and_restart_does_not_refund(env):
    setcap('0.010')
    with pytest.raises(TimeoutError):
        with b.user_read('alpha'):
            b.before_post();b.before_get('/2/users/me');raise TimeoutError('synthetic')
    assert next(iter(b.read()['reservations'].values()))['state']=='uncertain'
    assert b.report()['held_usd']=='0.010' and b.report()['spent_estimate_usd']=='0'
    with pytest.raises(b.BudgetError,match='budget_exhausted'):
        with b.user_read('beta'):pytest.fail('unresolved read was refunded')
    result=subprocess.run([os.sys.executable,'-c','from thth.budget_x import report; import json; print(json.dumps(report()))'],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['held_usd']=='0.010'
    assert setcap('0.005')['over_cap_usd']=='0.005'


def test_proven_no_get_releases_but_post_dispatched_crash_does_not(env):
    setcap('0.010')
    with pytest.raises(ValueError):
        with b.user_read('alpha'):b.before_post();raise ValueError('token response invalid')
    assert b.report()['remaining_usd']=='0.010'
    with b.locked() as fd:
        value=b._read(fd);key=b._reserve(value,'alpha',b.now());value['reservations'][key]['state']='post_started';value['reservations'][key]['post_at']=b.timestamp(b.now());b._save(fd,value)
    assert b.report()['held_usd']=='0.010'


def test_utc_month_after_post_keeps_original_charge_month(env,monkeypatch):
    clock=[jst.parse('2026-10-01T08:59:59+09:00')];monkeypatch.setattr(b,'now',lambda:clock[0])
    setcap('0.010')
    with b.user_read('alpha'):
        b.before_post();clock[0]=jst.parse('2026-10-01T09:00:01+09:00');setcap('0')
        b.before_get('/2/users/me');b.observed({'data':{'id':'one'}})
    row=next(iter(b.read()['reservations'].values()))
    assert (row['reservation_month'],row['dispatch_month'],row['estimated_charge_month'])==('2026-09','2026-10','2026-09')
    assert b.report()['months']['2026-09']['spent_estimate_usd']=='0.010' and b.report()['spent_estimate_usd']=='0'


def test_month_before_post_rechecks_new_month_cap(env,monkeypatch):
    clock=[jst.parse('2026-09-30T23:59:59Z')];monkeypatch.setattr(b,'now',lambda:clock[0]);setcap('0.010')
    with pytest.raises(b.BudgetError,match='budget_exhausted'):
        with b.user_read('alpha'):
            clock[0]=jst.parse('2026-10-01T00:00:01Z');setcap('0');b.before_post()
    assert all(r['state']=='released' for r in b.read()['reservations'].values())
    assert b.report()['months']['2026-09']['held_usd']=='0'


def test_two_processes_cap_one_cannot_both_reserve(env):
    setcap('0.010')
    code="""from thth import budget_x as b
import time
try:
 with b.user_read('alpha'):
  b.before_post(); b.before_get('/2/users/me'); time.sleep(.2); b.observed({'data':{'id':'one'}})
 print('accepted')
except (OSError,ValueError): print('refused')
"""
    procs=[subprocess.Popen([os.sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    results=[p.communicate(timeout=5) for p in procs]
    assert sorted(o.strip() for o,e in results)==['accepted','refused'] and all(not e for o,e in results)
    assert b.report()['spent_estimate_usd']=='0.010' and b.report()['held_usd']=='0'


@pytest.mark.parametrize('mode',['zero','partial','fsync'])
def test_budget_set_audit_failure_preserves_existing_contract(env,monkeypatch,mode):
    setcap('1');before=(b.folder()/'budget_x.json').read_bytes();prefix=(b.folder()/'accounts.ndjson').read_bytes()
    def fail(fd,data):
        if mode=='partial':os.write(fd,data[:len(data)//2])
        elif mode=='fsync':os.write(fd,data)
        raise admin_log.AdminLogError('synthetic',appended=mode!='zero',complete=mode=='fsync')
    monkeypatch.setattr(admin_log,'_emit',fail)
    with pytest.raises(admin_log.AdminLogError):setcap('2')
    after=(b.folder()/'budget_x.json').read_bytes()
    assert (after==before) is (mode=='zero') and (b.folder()/'accounts.ndjson').read_bytes().startswith(prefix)
    if mode!='zero':assert b.report()['policy']['amount']=='2'


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','malformed'])
def test_unsafe_or_bad_storage_read_does_not_mutate_or_reset(env,tmp_path,kind):
    (env/'state/_admin').mkdir(parents=True,mode=0o700);p=b.folder()/'budget_x.json';outside=tmp_path/'outside';outside.write_text('sentinel');outside.chmod(0o600)
    if kind=='symlink':p.symlink_to(outside)
    elif kind=='hardlink':os.link(outside,p)
    elif kind=='fifo':os.mkfifo(p,0o600)
    else:p.write_text('{bad');p.chmod(0o600)
    with pytest.raises((OSError,ValueError)):b.report()
    assert outside.read_text()=='sentinel' and not (b.folder()/'budget_x.lock').exists()


@pytest.mark.parametrize('field,value',[
    ('state',[]),('state','released'),('reservation_month','2020-01'),
    ('dispatch_month','2020-01'),('estimated_charge_month','2020-01'),
    ('get_at',None),('post_at',None),('policy_version',99),('usd','0'),
])
def test_corrupt_held_state_is_refused_without_reset(env,field,value,capsys):
    setcap('0.010')
    with b.user_read('alpha'):
        b.before_post();b.before_get('/2/users/me')
    path=b.folder()/'budget_x.json';data=json.loads(path.read_text())
    next(iter(data['reservations'].values()))[field]=value
    path.write_text(json.dumps(data));before=snapshot(env)
    assert cli.main(['admin','budget','x','--json'])==2
    assert 'cannot_say' in json.loads(capsys.readouterr().out)
    assert snapshot(env)==before
    with pytest.raises((ValueError,TypeError)):setcap('10')
    assert snapshot(env)==before


def test_duplicate_keys_are_not_accepted_or_replaced(env):
    setcap();path=b.folder()/'budget_x.json'
    path.write_text(path.read_text().replace('"schema_version":1','"schema_version":1,"schema_version":1'))
    before=snapshot(env)
    with pytest.raises(ValueError):b.report()
    with pytest.raises(ValueError):setcap('10')
    assert snapshot(env)==before


def test_fx_change_preserves_outstanding_hold_and_history(env):
    setcap('3',currency='JPY',rate='150',rate_source='synthetic 2026-09-20')
    with b.user_read('alpha'):
        b.before_post();b.before_get('/2/users/me')
    result=setcap('1',currency='JPY',rate='200',rate_source='synthetic 2026-09-21')
    assert result['held_usd']=='0.010' and result['over_cap_usd']=='0.005'
    data=b.read();assert data['policy_history'][-1]['rate']=='150'
    assert next(iter(data['reservations'].values()))['policy_version']==1


def test_new_month_before_post_rebooks_once(env,monkeypatch):
    clock=[jst.parse('2026-09-30T23:59:59Z')];monkeypatch.setattr(b,'now',lambda:clock[0]);setcap('0.010')
    with b.user_read('alpha'):
        clock[0]=jst.parse('2026-10-01T00:00:01Z');b.before_post();b.before_get('/2/users/me');b.observed({'data':{'id':'one'}})
    data=b.read();assert sorted(row['state'] for row in data['reservations'].values())==['released','settled']
    assert b.report()['months']=={'2026-09':{'spent_estimate_usd':'0','held_usd':'0'},'2026-10':{'spent_estimate_usd':'0.010','held_usd':'0'}}


@pytest.mark.parametrize('phase',['reservation','post','get','settlement'])
def test_storage_failure_never_dispatches_without_durable_reservation(env,monkeypatch,phase):
    setcap('0.010');original=b._save;effects=[]
    def save(fd,value):
        states={row['state'] for row in value['reservations'].values()}
        target={'reservation':'reserved','post':'post_started','get':'get_started','settlement':'settled'}[phase]
        if target in states:raise OSError('synthetic storage fault')
        return original(fd,value)
    monkeypatch.setattr(b,'_save',save)
    with pytest.raises(OSError):
        with b.user_read('alpha'):
            b.before_post();effects.append('post');b.before_get('/2/users/me');effects.append('get');b.observed({'data':{'id':'one'}})
    assert effects=={'reservation':[],'post':[],'get':['post'],'settlement':['post','get']}[phase]
    assert b.report()['held_usd']==('0.010' if phase=='settlement' else '0')
    assert b.report()['spent_estimate_usd']=='0'
