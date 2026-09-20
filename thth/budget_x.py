"""USD estimates for X reads; reservations are not provider billing receipts."""
import contextlib
import contextvars
from decimal import Decimal, localcontext
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from . import accounts, admin_log, jst, server_files

PRICE = Decimal('0.010')
PRICE_VERSION = '2026-09-20-user-read'
PRICE_SOURCE = 'https://docs.x.com/x-api/getting-started/pricing'
_current = contextvars.ContextVar('thth_x_read_reservation', default=None)
LOCK_WAIT_SECONDS = 5.0
LOCK_RETRY_SECONDS = 0.01
STATES = {'reserved', 'post_started', 'get_started', 'uncertain', 'settled', 'released'}


class BudgetError(ValueError):
    pass


def number(value, *, positive=False):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,18})?',value):
        raise BudgetError('invalid_budget_amount')
    amount=Decimal(value)
    if positive and amount<=0:raise BudgetError('invalid_budget_rate')
    return amount


def decimal_text(value):
    return format(value,'f')


def now():return jst.now_jst().astimezone(datetime.timezone.utc)
def month(at):return at.astimezone(datetime.timezone.utc).strftime('%Y-%m')
def timestamp(at):return at.astimezone(datetime.timezone.utc).isoformat()
def folder():return Path(accounts.thth_root()).resolve()/'state'/'_admin'


def policy(monthly='0',currency='USD',rate=None,rate_source=None,*,version=0,at=None):
    number(monthly)
    if currency not in ('USD','JPY'):raise BudgetError('invalid_budget_currency')
    if currency=='JPY':
        number(rate,positive=True)
        if (not isinstance(rate_source,str) or not rate_source.strip() or len(rate_source)>512
                or any(ord(c)<32 or ord(c)==127 for c in rate_source)):
            raise BudgetError('invalid_budget_rate_source')
    elif rate is not None or rate_source is not None:raise BudgetError('invalid_budget_rate')
    return dict(version=version,amount=monthly,currency=currency,rate=rate,rate_source=rate_source,at=timestamp(at or now()))


def empty():return {'schema_version':1,'policy':policy(),'policy_history':[],'reservations':{}}


def _valid_policy(value):
    if (type(value) is not dict or set(value)!={'version','amount','currency','rate','rate_source','at'}
            or type(value['version']) is not int or value['version']<0 or not jst.parse(value['at'])):raise BudgetError('budget_unreadable')
    number(value['amount'])
    if value['currency'] not in ('USD','JPY'):raise BudgetError('budget_unreadable')
    # Legacy/missing FX provenance is readable as unknown, never as zero money.
    if value['rate'] is not None:number(value['rate'],positive=True)
    if value['rate_source'] is not None and (not isinstance(value['rate_source'],str) or len(value['rate_source'])>512):raise BudgetError('budget_unreadable')


def _validate(value):
    if (type(value) is not dict or set(value)!={'schema_version','policy','policy_history','reservations'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or type(value['policy_history']) is not list or len(value['policy_history'])>10000
            or type(value['reservations']) is not dict or len(value['reservations'])>100000):raise BudgetError('budget_unreadable')
    _valid_policy(value['policy'])
    for version,item in enumerate(value['policy_history']):
        _valid_policy(item)
        if item['version']!=version:raise BudgetError('budget_unreadable')
    if value['policy']['version']!=len(value['policy_history']):raise BudgetError('budget_unreadable')
    for key,row in value['reservations'].items():
        if (not re.fullmatch(r'[0-9a-f]{32}',key) or type(row) is not dict
            or set(row)!={'account','state','usd','price_version','policy_version','reservation_month','dispatch_month','estimated_charge_month','at','post_at','get_at'}
            or not accounts.name_is_safe(row['account']) or not isinstance(row['state'],str) or row['state'] not in STATES
            or row['price_version']!=PRICE_VERSION or type(row['policy_version']) is not int or row['policy_version']<0
            or not isinstance(row['reservation_month'],str) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',row['reservation_month'])
            or not jst.parse(row['at'])):raise BudgetError('budget_unreadable')
        if number(row['usd'])!=PRICE:raise BudgetError('budget_unreadable')
        for key in ('post_at','get_at'):
            if row[key] is not None and not jst.parse(row[key]):raise BudgetError('budget_unreadable')
        for key in ('dispatch_month','estimated_charge_month'):
            if row[key] is not None and (not isinstance(row[key],str) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',row[key])):raise BudgetError('budget_unreadable')
        if row['state']=='settled' and row['estimated_charge_month']!=row['reservation_month']:raise BudgetError('budget_unreadable')
        if row['state'] in ('get_started','uncertain','settled') and (row['get_at'] is None or row['post_at'] is None):raise BudgetError('budget_unreadable')
        at=jst.parse(row['at']);post=jst.parse(row['post_at']);get=jst.parse(row['get_at'])
        if month(at)!=row['reservation_month'] or row['policy_version']>value['policy']['version']:raise BudgetError('budget_unreadable')
        if post and (post<at or month(post)!=row['reservation_month']) or get and (not post or get<post or month(get)!=row['dispatch_month']):raise BudgetError('budget_unreadable')
        if row['state']=='reserved' and post is not None:raise BudgetError('budget_unreadable')
        if row['state']=='post_started' and post is None:raise BudgetError('budget_unreadable')
        if row['state'] in ('reserved','post_started','released') and (get is not None or row['dispatch_month'] is not None):raise BudgetError('budget_unreadable')
        if row['state']!='settled' and row['estimated_charge_month'] is not None:raise BudgetError('budget_unreadable')

    return value


def _read(fd):
    try:
        from .handoff_cursor import _pairs
        return _validate(json.loads(server_files.read_at(fd,'budget_x.json',private=True,maximum=16777216),object_pairs_hook=_pairs))
    except FileNotFoundError:return empty()
    except (TypeError,KeyError,json.JSONDecodeError):raise BudgetError('budget_unreadable') from None


def _save(fd,value):
    raw=server_files.encode(_validate(value))
    if len(raw)>16777216:raise BudgetError('budget_storage_full')
    server_files.replace_at(fd,'budget_x.json',raw,private=True)


@contextlib.contextmanager
def locked():
    if admin_log._active_fd.get() is not None:raise BudgetError('budget_lock_order_refused')
    Path(accounts.thth_root()).mkdir(parents=True,exist_ok=True,mode=0o700)
    with server_files.directory(folder(),create=True,private=True) as fd:
        # Only lock acquisition may retry; never the caller body or provider I/O.
        deadline=time.monotonic()+LOCK_WAIT_SECONDS
        with contextlib.ExitStack() as stack:
            while True:
                try:
                    stack.enter_context(server_files.lock_at(fd,'budget_x.lock'))
                    break
                except BlockingIOError:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise BudgetError('budget_busy') from None
                    time.sleep(min(LOCK_RETRY_SECONDS,remaining))
                    if time.monotonic()>=deadline:raise BudgetError('budget_busy') from None
            yield fd


def read():
    try:
        with server_files.directory(folder(),private=True) as fd:return _read(fd)
    except FileNotFoundError:return empty()


def totals(value,period):
    spent=Decimal(0);held=Decimal(0)
    for row in value['reservations'].values():
        if row['reservation_month']!=period:continue
        if row['state']=='settled':spent+=Decimal(row['usd'])
        elif row['state']!='released':held+=Decimal(row['usd'])
    return spent,held


def _fx(p):
    return p['currency']=='USD' or p['rate'] is not None and bool(p['rate_source'] and p['rate_source'].strip())


def _can_reserve(p,liability):
    if not _fx(p):raise BudgetError('missing_rate')
    with localcontext() as ctx:
        ctx.prec=80
        return liability*(Decimal(p['rate']) if p['currency']=='JPY' else Decimal(1))<=Decimal(p['amount'])


def report(value=None,*,at=None):
    value=read() if value is None else value;p=value['policy'];period=month(at or now());spent,held=totals(value,period)
    with localcontext() as ctx:
        ctx.prec=80
        cap=Decimal(p['amount'])/(Decimal(p['rate']) if p['currency']=='JPY' and _fx(p) else Decimal(1)) if _fx(p) else None
        available=max(Decimal(0),cap-spent-held) if cap is not None else None
        over=max(Decimal(0),spent+held-cap) if cap is not None else None
    reason='missing_rate' if cap is None else 'budget_exhausted' if not _can_reserve(p,spent+held+PRICE) else None
    periods=sorted({r['reservation_month'] for r in value['reservations'].values()}|{period})
    return dict(media='x',month_utc=period,policy=admin_log.clean(p),cap_usd=decimal_text(cap) if cap is not None else None,
        spent_estimate_usd=decimal_text(spent),held_usd=decimal_text(held),remaining_usd=decimal_text(available) if available is not None else None,
        over_cap_usd=decimal_text(over) if over is not None else None,read_refusal=reason,cannot_say=[x for x in (reason,'usage_not_observed','estimate_not_actual_charge') if x],
        price=dict(version=PRICE_VERSION,user_read_usd=decimal_text(PRICE),source=PRICE_SOURCE),usage=None,
        months={m:dict(zip(('spent_estimate_usd','held_usd'),map(decimal_text,totals(value,m)))) for m in periods})


def configure(monthly,*,currency='USD',rate=None,rate_source=None,by,via='cli',before_save=None):
    admin_log.actor(by);requested=policy(monthly,currency,rate,rate_source)
    with locked() as fd:
        value=_read(fd);before=dict(value['policy']);requested['version']=before['version']+1
        try:previous=server_files.read_at(fd,'budget_x.json',private=True,maximum=16777216)
        except FileNotFoundError:previous=None
        changed=False
        def rollback():
            if not changed:return
            if previous is None:
                try:os.unlink('budget_x.json',dir_fd=fd);os.fsync(fd)
                except FileNotFoundError:pass
            else:server_files.replace_at(fd,'budget_x.json',previous,private=True)
        with admin_log.transaction(rollback=rollback):
            if before_save is not None:before_save()
            value['policy_history'].append(before);value['policy']=requested;changed=True;_save(fd,value)
            admin_log.append('budget_set','x',{'media':'x'},by=by,via=via,diff={'budget':[before,requested]})
        return report(value)


def _reserve(value,account,at):
    if not accounts.name_is_safe(account):raise BudgetError('invalid_budget_account')
    p=value['policy'];spent,held=totals(value,month(at))
    if not _can_reserve(p,spent+held+PRICE):raise BudgetError('budget_exhausted')
    identifier=secrets.token_hex(16)
    value['reservations'][identifier]=dict(account=account,state='reserved',usd=decimal_text(PRICE),price_version=PRICE_VERSION,
        policy_version=p['version'],reservation_month=month(at),dispatch_month=None,estimated_charge_month=None,at=timestamp(at),post_at=None,get_at=None)
    return identifier


@contextlib.contextmanager
def user_read(account):
    if _current.get() is not None:raise BudgetError('budget_nested_reservation')
    with locked() as fd:
        value=_read(fd);identifier=_reserve(value,account,now());_save(fd,value)
    holder={'id':identifier};reset=_current.set(holder)
    try:yield
    finally:
        _current.reset(reset)
        with locked() as fd:
            value=_read(fd);row=value['reservations'][holder['id']]
            if row['state'] in ('reserved','post_started'):
                row['state']='released';_save(fd,value) # no read dispatched, even if token POST failed
            elif row['state']=='get_started':
                row['state']='uncertain';_save(fd,value)


def before_post():
    holder=_current.get()
    if holder is None:raise BudgetError('budget_reservation_required')
    with locked() as fd:
        value=_read(fd);row=value['reservations'][holder['id']];at=now()
        if row['state']!='reserved':raise BudgetError('budget_reservation_used')
        if row['reservation_month']!=month(at):
            row['state']='released';identifier=_reserve(value,row['account'],at);row=value['reservations'][identifier]
        row['state']='post_started';row['post_at']=timestamp(at);_save(fd,value)
        if row is not value['reservations'][holder['id']]:holder['id']=identifier


def before_get(path):
    holder=_current.get()
    if holder is None or path!='/2/users/me':raise BudgetError('budget_reservation_required')
    with locked() as fd:
        value=_read(fd);row=value['reservations'][holder['id']]
        if row['state']!='post_started':raise BudgetError('budget_reservation_used')
        at=now();row['state']='get_started';row['get_at']=timestamp(at);row['dispatch_month']=month(at);_save(fd,value)


def observed(value):
    holder=_current.get()
    if holder is None:raise BudgetError('budget_reservation_required')
    identity=value.get('data')
    if type(identity) is not dict or not isinstance(identity.get('id'),str) or not identity['id']:return
    with locked() as fd:
        data=_read(fd);row=data['reservations'][holder['id']]
        if row['state']!='get_started':raise BudgetError('budget_reservation_used')
        row['state']='settled';row['estimated_charge_month']=row['reservation_month'];_save(fd,data)


def command(args):
    try:
        result=configure(args.monthly,currency=args.currency,rate=args.rate,rate_source=args.rate_source,by=args.by) if args.monthly is not None else report()
        if args.monthly is None and (args.rate is not None or args.rate_source is not None or args.by is not None):raise BudgetError('invalid_budget_options')
        print(json.dumps(result,ensure_ascii=False) if args.json else 'X 読取予算: 推定計上（請求実費ではない）\n'+json.dumps(result,ensure_ascii=False,indent=2));return 0
    except admin_log.AdminLogError as exc:
        reason='budget_change_durability_unconfirmed' if exc.complete else 'budget_change_partially_recorded' if exc.appended else 'budget_change_refused'
    except (OSError,ValueError,TypeError):reason='budget_unavailable_or_invalid_options'
    print(reason,file=sys.stderr)
    if args.json:print(json.dumps({'cannot_say':[reason]}))
    return 2


def register(commands):
    parser=commands.add_parser('budget',help='X の月次読取予算（推定USD・UTC月）');parser.add_argument('media',choices=['x'])
    parser.add_argument('--monthly');parser.add_argument('--currency',choices=['USD','JPY'],default='USD')
    parser.add_argument('--rate');parser.add_argument('--rate-source');parser.add_argument('--by');parser.add_argument('--json',action='store_true');parser.set_defaults(func=command)
