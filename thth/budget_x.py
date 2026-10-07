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
    value=read() if value is None else value;p=value['policy'];at=at or now();period=month(at);spent,held=totals(value,period)
    posts=read_posts();posts_spent,posts_held=posts_totals(posts,period)
    # 本人確認（user read）と他人の投稿の読み取り（posts read）は**同じ月の上限**を分け合う。
    spent+=posts_spent;held+=posts_held
    with localcontext() as ctx:
        ctx.prec=80
        cap=Decimal(p['amount'])/(Decimal(p['rate']) if p['currency']=='JPY' and _fx(p) else Decimal(1)) if _fx(p) else None
        available=max(Decimal(0),cap-spent-held) if cap is not None else None
        over=max(Decimal(0),spent+held-cap) if cap is not None else None
    reason='missing_rate' if cap is None else 'budget_exhausted' if not _can_reserve(p,spent+held+PRICE) else None
    periods=sorted({r['reservation_month'] for r in value['reservations'].values()}|{r['reservation_month'] for r in posts['reservations'].values()}|{period})
    def month_row(m):
        a,b=totals(value,m);c,d=posts_totals(posts,m)
        return dict(spent_estimate_usd=decimal_text(a+c),held_usd=decimal_text(b+d))
    return dict(media='x',month_utc=period,policy=admin_log.clean(p),cap_usd=decimal_text(cap) if cap is not None else None,
        spent_estimate_usd=decimal_text(spent),held_usd=decimal_text(held),remaining_usd=decimal_text(available) if available is not None else None,
        over_cap_usd=decimal_text(over) if over is not None else None,read_refusal=reason,cannot_say=[x for x in (reason,'usage_not_observed','estimate_not_actual_charge') if x],
        price=dict(version=PRICE_VERSION,user_read_usd=decimal_text(PRICE),source=PRICE_SOURCE),usage=None,
        months={m:month_row(m) for m in periods},posts_read=_posts_report(posts,at))


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


def _reserve(value,account,at,extra=Decimal(0)):
    if not accounts.name_is_safe(account):raise BudgetError('invalid_budget_account')
    p=value['policy'];spent,held=totals(value,month(at))
    # `extra` は同じ月の他人の投稿の読み取り（`budget_x_post_reads.json`）の確定と押さえ。
    if not _can_reserve(p,spent+held+extra+PRICE):raise BudgetError('budget_exhausted')
    identifier=secrets.token_hex(16)
    value['reservations'][identifier]=dict(account=account,state='reserved',usd=decimal_text(PRICE),price_version=PRICE_VERSION,
        policy_version=p['version'],reservation_month=month(at),dispatch_month=None,estimated_charge_month=None,at=timestamp(at),post_at=None,get_at=None)
    return identifier


@contextlib.contextmanager
def user_read(account):
    if _current.get() is not None:raise BudgetError('budget_nested_reservation')
    with locked() as fd:
        value=_read(fd);at=now();identifier=_reserve(value,account,at,_posts_liability(fd,at));_save(fd,value)
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
            row['state']='released';identifier=_reserve(value,row['account'],at,_posts_liability(fd,at));row=value['reservations'][identifier]
        row['state']='post_started';row['post_at']=timestamp(at);_save(fd,value)
        if row is not value['reservations'][holder['id']]:holder['id']=identifier


# 従量で計上される読み取りの口。**ここに無い path は予約を消費できない**
# ——綴りを 1 か所に置いて、予算の外を通る読み取りを構造で作らせない。
READ_PATH=re.compile(r'/2/users/(?:me|[0-9]{1,19}/tweets)')


def before_read(path):
    """token の POST を伴わない、単体の従量読み取り（adapter の `whoami`・`posts`）。

    予約の状態機械はそのまま使う（`post_at` は「この run が読み取りに踏み切った
    時刻」・`get_at` は発射そのもの）。**新しい状態も新しい欄も足さない**——
    記録の形が増えるほど、古い読み手が読み違える。
    """
    before_post();before_get(path)


def before_get(path):
    holder=_current.get()
    if holder is None or not isinstance(path,str) or not READ_PATH.fullmatch(path):raise BudgetError('budget_reservation_required')
    with locked() as fd:
        value=_read(fd);row=value['reservations'][holder['id']]
        if row['state']!='post_started':raise BudgetError('budget_reservation_used')
        at=now();row['state']='get_started';row['get_at']=timestamp(at);row['dispatch_month']=month(at);_save(fd,value)


def observed(value):
    holder=_current.get()
    if holder is None:raise BudgetError('budget_reservation_required')
    identity=value.get('data')
    # 本人（dict）・一覧（list）・0 件の応答（`meta` だけ）のどれでも「読めた」。
    # **読めていないときだけ** settled にしないで抜ける（退出時に `uncertain`）。
    observed_read=(type(identity) is dict and isinstance(identity.get('id'),str) and bool(identity['id'])
                   or type(identity) is list or type(value.get('meta')) is dict)
    if not observed_read:return
    with locked() as fd:
        data=_read(fd);row=data['reservations'][holder['id']]
        if row['state']!='get_started':raise BudgetError('budget_reservation_used')
        row['state']='settled';row['estimated_charge_month']=row['reservation_month'];_save(fd,data)


# ----- 他人の投稿の読み取り（Posts: Read・設計 2026-10-08「X の検索と枝の読み取り」）
#
# 台帳は**別のファイル**（`budget_x_post_reads.json`）。本人確認の台帳（`budget_x.json`）の
# 形は変えない——古い読み手が読み違えないように。`budget_x_posts.json` は月間投稿数の
# 上限（`budget_x_posts`）がもう使っている名前なので、それとも重ねない。
# 錠は本人確認と同じ `budget_x.lock`（月の上限を両方で分け合うので、1 つの錠の下で数える）。
#
# 予約の状態: reserved（押さえた・未発射）→ get_started（GET を投げた）→ settled（応答の
# 本数で確定）。発射しなかったら released（枠を返す）。発射して応答が読めなかったら
# uncertain（**押さえた上限のまま使ったものとして数える**）。

POSTS_PRICE = Decimal('0.005')
POSTS_PRICE_VERSION = '2026-10-08-posts-read'
POSTS_FILE = 'budget_x_post_reads.json'
POSTS_STATES = {'reserved', 'get_started', 'settled', 'uncertain', 'released'}
POSTS_MAX_LIMIT = 100
DAILY_READS_KEY = 'x_daily_reads'
DEFAULT_DAILY_READS = 60
MAX_DAILY_READS = 10000
_posts_current = contextvars.ContextVar('thth_x_posts_read_reservation', default=None)
_MONTH = r'\d{4}-(?:0[1-9]|1[0-2])'


def posts_empty():return {'schema_version':1,'reservations':{}}


def jst_day(at):return jst.to_jst(at).date().isoformat()


def _validate_posts(value):
    if (type(value) is not dict or set(value)!={'schema_version','reservations'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or type(value['reservations']) is not dict or len(value['reservations'])>100000):raise BudgetError('budget_unreadable')
    for key,row in value['reservations'].items():
        if (not re.fullmatch(r'[0-9a-f]{32}',key) or type(row) is not dict
                or set(row)!={'account','state','limit','count','usd','price_version','reservation_month','day_jst','at','get_at'}
                or not accounts.name_is_safe(row['account']) or not isinstance(row['state'],str) or row['state'] not in POSTS_STATES
                or type(row['limit']) is not int or not 1<=row['limit']<=POSTS_MAX_LIMIT
                or row['price_version']!=POSTS_PRICE_VERSION
                or not isinstance(row['reservation_month'],str) or not re.fullmatch(_MONTH,row['reservation_month'])
                or not isinstance(row['day_jst'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',row['day_jst'])
                or not isinstance(row['at'],str) or not jst.parse(row['at'])):raise BudgetError('budget_unreadable')
        at=jst.parse(row['at'])
        if month(at)!=row['reservation_month'] or jst_day(at)!=row['day_jst']:raise BudgetError('budget_unreadable')
        if row['state']=='settled':
            if type(row['count']) is not int or not 0<=row['count']<=row['limit']:raise BudgetError('budget_unreadable')
            expected=POSTS_PRICE*row['count']
        else:
            if row['count'] is not None:raise BudgetError('budget_unreadable')
            expected=POSTS_PRICE*row['limit']
        if number(row['usd'])!=expected:raise BudgetError('budget_unreadable')
        if row['state'] in ('reserved','released'):
            if row['get_at'] is not None:raise BudgetError('budget_unreadable')
        else:
            get=jst.parse(row['get_at']) if isinstance(row['get_at'],str) else None
            if get is None or get<at:raise BudgetError('budget_unreadable')
    return value


def _read_posts(fd):
    try:
        from .handoff_cursor import _pairs
        return _validate_posts(json.loads(server_files.read_at(fd,POSTS_FILE,private=True,maximum=16777216),object_pairs_hook=_pairs))
    except FileNotFoundError:return posts_empty()
    except (TypeError,KeyError,json.JSONDecodeError):raise BudgetError('budget_unreadable') from None


def _save_posts(fd,value):
    raw=server_files.encode(_validate_posts(value))
    if len(raw)>16777216:raise BudgetError('budget_storage_full')
    server_files.replace_at(fd,POSTS_FILE,raw,private=True)


def read_posts():
    try:
        with server_files.directory(folder(),private=True) as fd:return _read_posts(fd)
    except FileNotFoundError:return posts_empty()


def posts_totals(value,period):
    """その月の `(確定, 押さえ)`。**uncertain は押さえた上限のまま確定の側に数える。**"""
    spent=Decimal(0);held=Decimal(0)
    for row in value['reservations'].values():
        if row['reservation_month']!=period or row['state']=='released':continue
        if row['state'] in ('settled','uncertain'):spent+=Decimal(row['usd'])
        else:held+=Decimal(row['usd'])
    return spent,held


def posts_today(value,account,day):
    """その account のその JST の日に読んだ（読むかもしれない）本数。確定は実数・他は上限。"""
    n=0
    for row in value['reservations'].values():
        if row['account']!=account or row['day_jst']!=day or row['state']=='released':continue
        n+=row['count'] if row['state']=='settled' else row['limit']
    return n


def _posts_liability(fd,at):
    spent,held=posts_totals(_read_posts(fd),month(at));return spent+held


def daily_cap(account):
    """台帳の `x_daily_reads`（無ければ 60）。**読めない値は断る**（黙って既定に戻さない）。"""
    try:cfg=accounts.load_account(account)
    except accounts.AccountError:raise BudgetError('invalid_budget_account') from None
    value=(cfg or {}).get(DAILY_READS_KEY)
    if value is None:return DEFAULT_DAILY_READS
    if type(value) is not int or not 0<=value<=MAX_DAILY_READS:raise BudgetError('x_daily_read_cap_unreadable')
    return value


class PostsRead:
    """`posts_read()` の中で渡す手。`dispatch()` を GET の直前に・`settle(n)` を応答の後に。"""

    def __init__(self,identifier,account,limit):
        self.id=identifier;self.account=account;self.limit=limit;self.count=None;self.state='reserved'

    def _move(self,expected,change):
        with locked() as fd:
            value=_read_posts(fd);row=value['reservations'][self.id]
            if row['state']!=expected:raise BudgetError('budget_reservation_used')
            change(row);_save_posts(fd,value);self.state=row['state']

    def dispatch(self):
        def change(row):row['state']='get_started';row['get_at']=timestamp(now())
        self._move('reserved',change)

    def settle(self,count):
        """応答に入っていた投稿の本数で確定する。上限を超える数は確定しない（退出時に uncertain）。"""
        if type(count) is not int or not 0<=count<=self.limit:return
        def change(row):row['state']='settled';row['count']=count;row['usd']=decimal_text(POSTS_PRICE*count)
        self._move('get_started',change);self.count=count


@contextlib.contextmanager
def posts_read(account,limit):
    """他人の投稿を最大 `limit` 本読む 1 回の要求の予約（読む前に押さえ・応答の本数で確定）。

    断り: `budget_exhausted`（月の上限・本人確認と合わせて）・`x_daily_read_cap`（その
    account の JST の 1 日の本数）。どちらも**要求の前**に断る。
    """
    if type(limit) is not int or not 1<=limit<=POSTS_MAX_LIMIT:raise BudgetError('invalid_posts_read_limit')
    if not accounts.name_is_safe(account):raise BudgetError('invalid_budget_account')
    if _posts_current.get() is not None:raise BudgetError('budget_nested_reservation')
    cap=daily_cap(account)
    with locked() as fd:
        value=_read(fd);posts=_read_posts(fd);at=now();period=month(at);day=jst_day(at)
        spent,held=totals(value,period);posts_spent,posts_held=posts_totals(posts,period)
        if not _can_reserve(value['policy'],spent+held+posts_spent+posts_held+POSTS_PRICE*limit):raise BudgetError('budget_exhausted')
        if posts_today(posts,account,day)+limit>cap:raise BudgetError('x_daily_read_cap')
        identifier=secrets.token_hex(16)
        posts['reservations'][identifier]=dict(account=account,state='reserved',limit=limit,count=None,
            usd=decimal_text(POSTS_PRICE*limit),price_version=POSTS_PRICE_VERSION,reservation_month=period,
            day_jst=day,at=timestamp(at),get_at=None)
        _save_posts(fd,posts)
    handle=PostsRead(identifier,account,limit);reset=_posts_current.set(handle)
    try:yield handle
    finally:
        _posts_current.reset(reset)
        with locked() as fd:
            posts=_read_posts(fd);row=posts['reservations'][identifier]
            if row['state']=='reserved':row['state']='released';_save_posts(fd,posts)  # 投げていない
            elif row['state']=='get_started':row['state']='uncertain';_save_posts(fd,posts)  # 投げて読めなかった


def posts_status(account,*,at=None):
    """表示の数字（今日のその account の本数と上限・今月の合計と上限）。読むだけ。"""
    at=at or now();whole=report(at=at)
    try:cap=daily_cap(account)
    except BudgetError:cap=None
    return dict(today=posts_today(read_posts(),account,jst_day(at)),daily_cap=cap,day_jst=jst_day(at),
                month_utc=whole['month_utc'],month_used_usd=decimal_text(Decimal(whole['spent_estimate_usd'])+Decimal(whole['held_usd'])),
                cap_usd=whole['cap_usd'])


def _posts_report(posts,at):
    period=month(at);day=jst_day(at);spent,held=posts_totals(posts,period)
    names=sorted({row['account'] for row in posts['reservations'].values() if row['day_jst']==day})
    today={}
    for name in names:
        try:cap=daily_cap(name)
        except BudgetError:cap=None
        today[name]=dict(posts=posts_today(posts,name,day),daily_cap=cap)
    uncertain=sum(1 for row in posts['reservations'].values() if row['reservation_month']==period and row['state']=='uncertain')
    return dict(price=dict(version=POSTS_PRICE_VERSION,post_read_usd=decimal_text(POSTS_PRICE),source=PRICE_SOURCE),
                day_jst=day,default_daily_cap=DEFAULT_DAILY_READS,today_by_account=today,
                month=dict(month_utc=period,spent_estimate_usd=decimal_text(spent),held_usd=decimal_text(held),uncertain=uncertain))


def command(args):
    if args.media=='x-posts':
        # 本数の口（`thth/budget_x_posts.py`）。金額の選択肢はここでは受けない
        # ——USD の予算と本数の上限を 1 つの語で混ぜない（設計 2.14.0 §0）。
        from . import budget_x_posts
        if args.currency!='USD' or args.rate is not None or args.rate_source is not None:
            print('invalid_budget_options',file=sys.stderr)
            if args.json:print(json.dumps({'cannot_say':['invalid_budget_options']}))
            return 2
        return budget_x_posts.command(args)
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
    parser=commands.add_parser('budget',help='X の月次読取予算（推定USD）と月間投稿数（本数）・UTC 月');parser.add_argument('media',choices=['x','x-posts'])
    parser.add_argument('--monthly');parser.add_argument('--currency',choices=['USD','JPY'],default='USD')
    parser.add_argument('--rate');parser.add_argument('--rate-source');parser.add_argument('--by');parser.add_argument('--json',action='store_true');parser.set_defaults(func=command)
