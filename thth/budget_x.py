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
    owned=read_posts(OWNED);owned_spent,owned_held=posts_totals(owned,period)
    lists=read_posts(LISTS);lists_spent,lists_held=posts_totals(lists,period)
    comms=read_posts(COMMUNITIES);comms_spent,comms_held=posts_totals(comms,period)
    archive=read_posts(ARCHIVE);archive_spent,archive_held=posts_totals(archive,period)
    comms_spent+=archive_spent;comms_held+=archive_held
    extra={kind:read_posts(kind) for kind,_key,_label in PER_REQUEST}
    for ledger in extra.values():
        a,b=posts_totals(ledger,period);comms_spent+=a;comms_held+=b
    # 本人確認（user read）・他人の投稿の読み取り（posts read）・自分の投稿の数字
    # （owned read）・非公開リストの書き込み（list write）は**同じ月の上限**を分け合う（台帳は別々）。
    spent+=posts_spent+owned_spent+lists_spent+comms_spent;held+=posts_held+owned_held+lists_held+comms_held
    with localcontext() as ctx:
        ctx.prec=80
        cap=Decimal(p['amount'])/(Decimal(p['rate']) if p['currency']=='JPY' and _fx(p) else Decimal(1)) if _fx(p) else None
        available=max(Decimal(0),cap-spent-held) if cap is not None else None
        over=max(Decimal(0),spent+held-cap) if cap is not None else None
    reason='missing_rate' if cap is None else 'budget_exhausted' if not _can_reserve(p,spent+held+PRICE) else None
    periods=sorted({r['reservation_month'] for r in value['reservations'].values()}|{r['reservation_month'] for r in posts['reservations'].values()}
                   |{r['reservation_month'] for r in owned['reservations'].values()}
                   |{r['reservation_month'] for r in lists['reservations'].values()}
                   |{r['reservation_month'] for r in comms['reservations'].values()}
                   |{r['reservation_month'] for r in archive['reservations'].values()}
                   |{r['reservation_month'] for ledger in extra.values() for r in ledger['reservations'].values()}|{period})
    def month_row(m):
        a,b=totals(value,m);c,d=posts_totals(posts,m);e,f=posts_totals(owned,m);g,h=posts_totals(lists,m)
        i,j=posts_totals(comms,m)
        for ledger in (archive,*extra.values()):
            k,l=posts_totals(ledger,m);i+=k;j+=l
        return dict(spent_estimate_usd=decimal_text(a+c+e+g+i),held_usd=decimal_text(b+d+f+h+j))
    # medium は media の別名（10/15 調べ_JSONの欄 §5 1-7・media は残す）。
    return dict(media='x',medium='x',month_utc=period,policy=admin_log.clean(p),cap_usd=decimal_text(cap) if cap is not None else None,
        spent_estimate_usd=decimal_text(spent),held_usd=decimal_text(held),remaining_usd=decimal_text(available) if available is not None else None,
        over_cap_usd=decimal_text(over) if over is not None else None,read_refusal=reason,cannot_say=[x for x in (reason,'usage_not_observed','estimate_not_actual_charge') if x],
        price=dict(version=PRICE_VERSION,user_read_usd=decimal_text(PRICE),source=PRICE_SOURCE),usage=None,
        months={m:month_row(m) for m in periods},posts_read=_posts_report(posts,at),owned_read=_owned_report(owned,at),
        list_write=_lists_report(lists,at),community_read=_communities_report(comms,at),
        archive_read=_archive_report(archive,at),
        **{key:_requests_report(kind,extra[kind],at) for kind,key,_label in PER_REQUEST})


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
# 3.17.0: `thth list add|remove @handle` の handle → id（`GET /2/users/by/username/:username`）も
# 本人確認と同じ 1 回 0.010 USD の予約で引く（Users: Read）。
READ_PATH=re.compile(r'/2/users/(?:me|[0-9]{1,19}/tweets|by/username/[A-Za-z0-9_]{1,15})')


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

# ----- 自分の投稿の数字（Owned Reads・masaru 10/12 決定 1）
#
# `GET /2/tweets?ids=…`（自分が THTH から出した投稿だけ・最大 100 本を 1 回で）。料金表の
# Owned Reads は**返ってきた 1 本ごとに 0.001 USD**で、他人の投稿（0.005）とは別の段。
# 台帳も**別のファイル**（`budget_x_owned_reads.json`）にし、形は他人の投稿の台帳と同じ
# （予約→発射→応答の本数で確定・発射して読めなければ押さえた上限のまま uncertain）。
# 月の上限（`thth admin budget x`）は 3 つの台帳で分け合う。1 日の本数の上限
# （`x_daily_reads`）は他人の投稿の読み取りの決まりなので、こちらには掛けない。
POSTS = 'posts'
OWNED = 'owned'
OWNED_PRICE = Decimal('0.001')
OWNED_PRICE_VERSION = '2026-10-12-owned-read'
OWNED_FILE = 'budget_x_owned_reads.json'
# ----- 非公開リストの書き込み（3.17.0・`thth list create|add|remove`）
#
# 料金表: List: Create 0.010 USD／要求・List: Manage（members の追加と外し）0.005 USD／要求。
# 台帳は**別のファイル**（`budget_x_list_writes.json`）で、形は他人の投稿の台帳と同じ
# （押さえ→発射→応答で確定・発射して応答が読めなければ押さえたまま uncertain）。
# **単位は 0.005 USD**——作るは 2 単位・足す／外すは 1 単位として `limit`／`count` に数える
# （1 つの台帳・1 つの単価のまま 2 つの値段を載せるため）。月の上限は他の 3 つと分け合う。
# 1 日の本数（`x_daily_reads`）は他人の投稿の読み取りの決まりなので掛けない。
LISTS = 'lists'
LIST_UNIT_PRICE = Decimal('0.005')
LIST_PRICE_VERSION = '2026-10-12-list-write'
LIST_FILE = 'budget_x_list_writes.json'
LIST_CREATE_UNITS = 2
LIST_MANAGE_UNITS = 1
# ----- コミュニティの検索（3.19.0・`thth community search`）
#
# 料金表: Community: Read 0.005 USD／resource（返ってきたコミュニティ 1 つごと）。台帳は
# **別のファイル**（`budget_x_community_reads.json`）で、形は他人の投稿の台帳と同じ（押さえ→
# 発射→応答の数で確定・発射して応答が読めなければ押さえた上限のまま uncertain）。月の上限は
# 他の台帳と分け合う。**1 日の本数（`x_daily_reads`）には数えない**——あれは他人の**投稿**を
# 読む口（返す出向き）の決まりで、コミュニティの名前と人数の一覧は投稿ではないから
# （owned・lists と同じ扱い）。
COMMUNITIES = 'communities'
COMMUNITY_PRICE = Decimal('0.005')
COMMUNITY_PRICE_VERSION = '2026-10-13-community-read'
COMMUNITY_FILE = 'budget_x_community_reads.json'
# ----- トレンドと語ごとの投稿数（3.19.0・`thth trends`・`thth counts`）
#
# 料金表（**L1** docs.x.com pricing「Charged per request」）: Trends 0.010 USD／request・
# Counts: Recent 0.005 USD／request。**要求 1 回が 1 単位**（返ってきた数によらない）。台帳は
# それぞれ別のファイルで、形は他と同じ（押さえ→発射→確定。断りの応答は 0 で確定・応答が
# 読めなければ押さえたまま uncertain）。月の上限は他の台帳と分け合い、1 日の本数には数えない。
TRENDS = 'trends'
TRENDS_PRICE = Decimal('0.010')
TRENDS_PRICE_VERSION = '2026-10-13-trends'
TRENDS_FILE = 'budget_x_trends.json'
COUNTS = 'counts'
COUNTS_PRICE = Decimal('0.005')
COUNTS_PRICE_VERSION = '2026-10-13-counts-recent'
COUNTS_FILE = 'budget_x_counts.json'
# ----- 全期間の検索と件数（4.5.0・`thth archive`・設計 4.5.0「X の全期間の検索」）
#
# 料金表: 投稿は Posts: Read（返った 1 本 0.005 USD）・件数は Counts: All（1 回 0.010 USD）。
# 全期間の投稿の読み取りは**材料集め**（人に返す出向きではない）なので、他人の投稿の台帳と
# **別のファイル**（`budget_x_archive_reads.json`）に数え、1 日の本数も別の鍵
# （`x_archive_daily_reads`・既定 0＝読まない）で縛る。**今の `x_daily_reads`（既定 60）は
# 緩めないし、こちらの読みはそちらに数えない。** 月の上限は他の台帳と分け合う。
ARCHIVE = 'archive'
ARCHIVE_PRICE = POSTS_PRICE
ARCHIVE_PRICE_VERSION = '2026-10-21-posts-read-archive'
ARCHIVE_FILE = 'budget_x_archive_reads.json'
ARCHIVE_DAILY_READS_KEY = 'x_archive_daily_reads'
DEFAULT_ARCHIVE_DAILY_READS = 0
MAX_ARCHIVE_DAILY_READS = 10000
COUNTS_ALL = 'counts_all'
COUNTS_ALL_PRICE = Decimal('0.010')
COUNTS_ALL_PRICE_VERSION = '2026-10-21-counts-all'
COUNTS_ALL_FILE = 'budget_x_counts_all.json'
_LEDGERS = {POSTS: (POSTS_FILE, POSTS_PRICE, POSTS_PRICE_VERSION),
            ARCHIVE: (ARCHIVE_FILE, ARCHIVE_PRICE, ARCHIVE_PRICE_VERSION),
            COUNTS_ALL: (COUNTS_ALL_FILE, COUNTS_ALL_PRICE, COUNTS_ALL_PRICE_VERSION),
            OWNED: (OWNED_FILE, OWNED_PRICE, OWNED_PRICE_VERSION),
            LISTS: (LIST_FILE, LIST_UNIT_PRICE, LIST_PRICE_VERSION),
            COMMUNITIES: (COMMUNITY_FILE, COMMUNITY_PRICE, COMMUNITY_PRICE_VERSION),
            TRENDS: (TRENDS_FILE, TRENDS_PRICE, TRENDS_PRICE_VERSION),
            COUNTS: (COUNTS_FILE, COUNTS_PRICE, COUNTS_PRICE_VERSION)}
# 要求ごとに数える台帳（`report()` の内訳の行・`_posts_liability` で月の上限に入る）。
PER_REQUEST = ((TRENDS, 'trends_read', 'トレンド'),
               (COUNTS, 'counts_read', '語ごとの投稿数'),
               (COUNTS_ALL, 'counts_all_read', '語ごとの投稿数（全期間）'))
_PER_REQUEST_API = {TRENDS: 'Trends', COUNTS: 'Counts: Recent', COUNTS_ALL: 'Counts: All'}
_MONTH = r'\d{4}-(?:0[1-9]|1[0-2])'


def posts_empty():return {'schema_version':1,'reservations':{}}


def jst_day(at):return jst.to_jst(at).date().isoformat()


def _validate_posts(value,kind=POSTS):
    _file,price,version=_LEDGERS[kind]
    if (type(value) is not dict or set(value)!={'schema_version','reservations'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or type(value['reservations']) is not dict or len(value['reservations'])>100000):raise BudgetError('budget_unreadable')
    for key,row in value['reservations'].items():
        if (not re.fullmatch(r'[0-9a-f]{32}',key) or type(row) is not dict
                or set(row)!={'account','state','limit','count','usd','price_version','reservation_month','day_jst','at','get_at'}
                or not accounts.name_is_safe(row['account']) or not isinstance(row['state'],str) or row['state'] not in POSTS_STATES
                or type(row['limit']) is not int or not 1<=row['limit']<=POSTS_MAX_LIMIT
                or row['price_version']!=version
                or not isinstance(row['reservation_month'],str) or not re.fullmatch(_MONTH,row['reservation_month'])
                or not isinstance(row['day_jst'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',row['day_jst'])
                or not isinstance(row['at'],str) or not jst.parse(row['at'])):raise BudgetError('budget_unreadable')
        at=jst.parse(row['at'])
        if month(at)!=row['reservation_month'] or jst_day(at)!=row['day_jst']:raise BudgetError('budget_unreadable')
        if row['state']=='settled':
            if type(row['count']) is not int or not 0<=row['count']<=row['limit']:raise BudgetError('budget_unreadable')
            expected=price*row['count']
        else:
            if row['count'] is not None:raise BudgetError('budget_unreadable')
            expected=price*row['limit']
        if number(row['usd'])!=expected:raise BudgetError('budget_unreadable')
        if row['state'] in ('reserved','released'):
            if row['get_at'] is not None:raise BudgetError('budget_unreadable')
        else:
            get=jst.parse(row['get_at']) if isinstance(row['get_at'],str) else None
            if get is None or get<at:raise BudgetError('budget_unreadable')
    return value


def _read_posts(fd,kind=POSTS):
    try:
        from .handoff_cursor import _pairs
        return _validate_posts(json.loads(server_files.read_at(fd,_LEDGERS[kind][0],private=True,maximum=16777216),object_pairs_hook=_pairs),kind)
    except FileNotFoundError:return posts_empty()
    except (TypeError,KeyError,json.JSONDecodeError):raise BudgetError('budget_unreadable') from None


def _save_posts(fd,value,kind=POSTS):
    raw=server_files.encode(_validate_posts(value,kind))
    if len(raw)>16777216:raise BudgetError('budget_storage_full')
    server_files.replace_at(fd,_LEDGERS[kind][0],raw,private=True)


def read_posts(kind=POSTS):
    try:
        with server_files.directory(folder(),private=True) as fd:return _read_posts(fd,kind)
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
    """本人確認の台帳の外にある、その月の確定と押さえ（他人の投稿＋自分の投稿の数字）。"""
    total=Decimal(0)
    for kind in (POSTS,OWNED,LISTS,COMMUNITIES,TRENDS,COUNTS,ARCHIVE,COUNTS_ALL):
        spent,held=posts_totals(_read_posts(fd,kind),month(at));total+=spent+held
    return total


def daily_cap(account):
    """台帳の `x_daily_reads`（無ければ 60）。**読めない値は断る**（黙って既定に戻さない）。"""
    try:cfg=accounts.load_account(account)
    except accounts.AccountError:raise BudgetError('invalid_budget_account') from None
    value=(cfg or {}).get(DAILY_READS_KEY)
    if value is None:return DEFAULT_DAILY_READS
    if type(value) is not int or not 0<=value<=MAX_DAILY_READS:raise BudgetError('x_daily_read_cap_unreadable')
    return value


def archive_daily_cap(account):
    """台帳の `x_archive_daily_reads`（無ければ 0＝全期間は読まない）。読めない値は断る。"""
    try:cfg=accounts.load_account(account)
    except accounts.AccountError:raise BudgetError('invalid_budget_account') from None
    value=(cfg or {}).get(ARCHIVE_DAILY_READS_KEY)
    if value is None:return DEFAULT_ARCHIVE_DAILY_READS
    if type(value) is not int or not 0<=value<=MAX_ARCHIVE_DAILY_READS:raise BudgetError('x_archive_daily_read_cap_unreadable')
    return value


class PostsRead:
    """`posts_read()` の中で渡す手。`dispatch()` を GET の直前に・`settle(n)` を応答の後に。"""

    def __init__(self,identifier,account,limit,kind=POSTS):
        self.id=identifier;self.account=account;self.limit=limit;self.count=None;self.state='reserved';self.kind=kind

    def _move(self,expected,change):
        with locked() as fd:
            value=_read_posts(fd,self.kind);row=value['reservations'][self.id]
            if row['state']!=expected:raise BudgetError('budget_reservation_used')
            change(row);_save_posts(fd,value,self.kind);self.state=row['state']

    def dispatch(self):
        def change(row):row['state']='get_started';row['get_at']=timestamp(now())
        self._move('reserved',change)

    def settle(self,count):
        """応答に入っていた投稿の本数で確定する。上限を超える数は確定しない（退出時に uncertain）。"""
        if type(count) is not int or not 0<=count<=self.limit:return
        price=_LEDGERS[self.kind][1]
        def change(row):row['state']='settled';row['count']=count;row['usd']=decimal_text(price*count)
        self._move('get_started',change);self.count=count


@contextlib.contextmanager
def posts_read(account,limit):
    """他人の投稿を最大 `limit` 本読む 1 回の要求の予約（読む前に押さえ・応答の本数で確定）。

    断り: `budget_exhausted`（月の上限・本人確認と自分の投稿の数字と合わせて）・
    `x_daily_read_cap`（その account の JST の 1 日の本数）。どちらも**要求の前**に断る。
    """
    with _ledger_read(POSTS,account,limit) as handle:yield handle


@contextlib.contextmanager
def owned_read(account,limit):
    """自分の投稿の数字（Owned Reads）を最大 `limit` 本読む 1 回の要求の予約。

    作法は `posts_read` と同じ（押さえ→発射→応答の本数で確定・読めなければ uncertain）。
    断りは `budget_exhausted` だけ（1 日の本数の上限は他人の投稿の決まりなので掛けない）。
    """
    with _ledger_read(OWNED,account,limit) as handle:yield handle


@contextlib.contextmanager
def list_write(account,units):
    """非公開リストの書き込み 1 回の予約（作る 2 単位・足す／外す 1 単位・1 単位 0.005 USD）。

    作法は `posts_read` と同じ（押さえ→`dispatch()`→`settle(units)`・断りの応答は `settle(0)`・
    応答が読めなければ押さえたまま uncertain）。断りは `budget_exhausted` だけ。
    """
    with _ledger_read(LISTS,account,units) as handle:yield handle


@contextlib.contextmanager
def community_read(account,limit):
    """コミュニティの検索 1 回の予約（最大 `limit` 件・1 件 0.005 USD・3.19.0）。

    作法は `posts_read` と同じ（押さえ→`dispatch()`→`settle(n)`・断りの応答は `settle(0)`・
    応答が読めなければ押さえたまま uncertain）。断りは `budget_exhausted` だけ。
    """
    with _ledger_read(COMMUNITIES,account,limit) as handle:yield handle


@contextlib.contextmanager
def request_read(kind,account):
    """要求 1 回 = 1 単位の読み取り（`TRENDS`・`COUNTS`・3.19.0）の予約。作法は `posts_read` と同じ。"""
    if kind not in (TRENDS,COUNTS,COUNTS_ALL):raise BudgetError('invalid_budget_kind')
    with _ledger_read(kind,account,1) as handle:yield handle


@contextlib.contextmanager
def archive_read(account,limit):
    """全期間の検索の 1 ページ（最大 `limit` 本）の予約（4.5.0）。作法は `posts_read` と同じ。

    断り: `budget_exhausted`（月の上限・他の台帳と合わせて）・`x_archive_daily_read_cap`
    （その account の JST の 1 日の全期間の本数・`x_archive_daily_reads`・既定 0）。どちらも要求の前。
    """
    with _ledger_read(ARCHIVE,account,limit) as handle:yield handle


@contextlib.contextmanager
def _ledger_read(kind,account,limit):
    if type(limit) is not int or not 1<=limit<=POSTS_MAX_LIMIT:raise BudgetError('invalid_posts_read_limit')
    if not accounts.name_is_safe(account):raise BudgetError('invalid_budget_account')
    if _posts_current.get() is not None:raise BudgetError('budget_nested_reservation')
    price=_LEDGERS[kind][1];version=_LEDGERS[kind][2]
    cap=daily_cap(account) if kind==POSTS else archive_daily_cap(account) if kind==ARCHIVE else None
    with locked() as fd:
        value=_read(fd);mine=_read_posts(fd,kind);at=now();period=month(at);day=jst_day(at)
        spent,held=totals(value,period)
        if not _can_reserve(value['policy'],spent+held+_posts_liability(fd,at)+price*limit):raise BudgetError('budget_exhausted')
        if cap is not None and posts_today(mine,account,day)+limit>cap:
            raise BudgetError('x_archive_daily_read_cap' if kind==ARCHIVE else 'x_daily_read_cap')
        identifier=secrets.token_hex(16)
        mine['reservations'][identifier]=dict(account=account,state='reserved',limit=limit,count=None,
            usd=decimal_text(price*limit),price_version=version,reservation_month=period,
            day_jst=day,at=timestamp(at),get_at=None)
        _save_posts(fd,mine,kind)
    handle=PostsRead(identifier,account,limit,kind);reset=_posts_current.set(handle)
    try:yield handle
    finally:
        _posts_current.reset(reset)
        with locked() as fd:
            mine=_read_posts(fd,kind);row=mine['reservations'][identifier]
            if row['state']=='reserved':row['state']='released';_save_posts(fd,mine,kind)  # 投げていない
            elif row['state']=='get_started':row['state']='uncertain';_save_posts(fd,mine,kind)  # 投げて読めなかった


def posts_status(account,*,at=None):
    """表示の数字（今日のその account の本数と上限・今月の合計と上限）。読むだけ。"""
    at=at or now();whole=report(at=at)
    try:cap=daily_cap(account)
    except BudgetError:cap=None
    return dict(today=posts_today(read_posts(),account,jst_day(at)),daily_cap=cap,day_jst=jst_day(at),
                month_utc=whole['month_utc'],month_used_usd=decimal_text(Decimal(whole['spent_estimate_usd'])+Decimal(whole['held_usd'])),
                cap_usd=whole['cap_usd'])


def archive_status(account,*,at=None):
    """全期間の読みの表示の数字（今日のその account の本数と上限・今月の合計と上限）。読むだけ。"""
    at=at or now();whole=report(at=at)
    try:cap=archive_daily_cap(account)
    except BudgetError:cap=None
    return dict(today=posts_today(read_posts(ARCHIVE),account,jst_day(at)),daily_cap=cap,day_jst=jst_day(at),
                month_utc=whole['month_utc'],month_used_usd=decimal_text(Decimal(whole['spent_estimate_usd'])+Decimal(whole['held_usd'])),
                cap_usd=whole['cap_usd'])


def _archive_report(archive,at):
    period=month(at);day=jst_day(at);spent,held=posts_totals(archive,period)
    rows=[row for row in archive['reservations'].values() if row['reservation_month']==period and row['state']!='released']
    names=sorted({row['account'] for row in archive['reservations'].values() if row['day_jst']==day})
    today={}
    for name in names:
        try:cap=archive_daily_cap(name)
        except BudgetError:cap=None
        today[name]=dict(posts=posts_today(archive,name,day),daily_cap=cap)
    posts=sum(row['count'] if row['state']=='settled' else row['limit'] for row in rows)
    return dict(price=dict(version=ARCHIVE_PRICE_VERSION,post_read_usd=decimal_text(ARCHIVE_PRICE),source=PRICE_SOURCE),
                what='full_archive_search',day_jst=day,default_daily_cap=DEFAULT_ARCHIVE_DAILY_READS,today_by_account=today,
                month=dict(month_utc=period,requests=len(rows),posts=posts,spent_estimate_usd=decimal_text(spent),
                           held_usd=decimal_text(held),uncertain=sum(1 for row in rows if row['state']=='uncertain')))


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


def _owned_report(owned,at):
    period=month(at);spent,held=posts_totals(owned,period)
    uncertain=sum(1 for row in owned['reservations'].values() if row['reservation_month']==period and row['state']=='uncertain')
    posts=sum(row['count'] if row['state']=='settled' else row['limit'] for row in owned['reservations'].values()
              if row['reservation_month']==period and row['state']!='released')
    return dict(price=dict(version=OWNED_PRICE_VERSION,owned_read_usd=decimal_text(OWNED_PRICE),source=PRICE_SOURCE),
                what='own_posts_public_metrics',
                month=dict(month_utc=period,posts=posts,spent_estimate_usd=decimal_text(spent),held_usd=decimal_text(held),uncertain=uncertain))


def _lists_report(lists,at):
    period=month(at);spent,held=posts_totals(lists,period)
    rows=[row for row in lists['reservations'].values() if row['reservation_month']==period and row['state']!='released']
    uncertain=sum(1 for row in rows if row['state']=='uncertain')
    return dict(price=dict(version=LIST_PRICE_VERSION,unit_usd=decimal_text(LIST_UNIT_PRICE),
                           create_usd=decimal_text(LIST_UNIT_PRICE*LIST_CREATE_UNITS),
                           manage_usd=decimal_text(LIST_UNIT_PRICE*LIST_MANAGE_UNITS),source=PRICE_SOURCE),
                what='private_list_writes',
                month=dict(month_utc=period,requests=len(rows),spent_estimate_usd=decimal_text(spent),
                           held_usd=decimal_text(held),uncertain=uncertain))


def _communities_report(comms,at):
    period=month(at);spent,held=posts_totals(comms,period)
    rows=[row for row in comms['reservations'].values() if row['reservation_month']==period and row['state']!='released']
    uncertain=sum(1 for row in rows if row['state']=='uncertain')
    communities=sum(row['count'] if row['state']=='settled' else row['limit'] for row in rows)
    return dict(price=dict(version=COMMUNITY_PRICE_VERSION,community_read_usd=decimal_text(COMMUNITY_PRICE),source=PRICE_SOURCE),
                what='community_search',
                month=dict(month_utc=period,requests=len(rows),communities=communities,
                           spent_estimate_usd=decimal_text(spent),held_usd=decimal_text(held),uncertain=uncertain))


def _requests_report(kind,ledger,at):
    period=month(at);spent,held=posts_totals(ledger,period);_file,price,version=_LEDGERS[kind]
    rows=[row for row in ledger['reservations'].values() if row['reservation_month']==period and row['state']!='released']
    return dict(price=dict(version=version,request_usd=decimal_text(price),source=PRICE_SOURCE),what=kind,
                month=dict(month_utc=period,requests=len(rows),spent_estimate_usd=decimal_text(spent),
                           held_usd=decimal_text(held),uncertain=sum(1 for row in rows if row['state']=='uncertain')))


def cost_lines(whole):
    """`report()` の内訳を人の画面の行に（doctor・admin）。推定であって請求の実費ではない。"""
    out=[f"X の読み取り{'と書き込み' if whole.get('list_write') else ''}（{whole['month_utc']}・推定・請求の実費ではない）: 合計 ${whole['spent_estimate_usd']}"
         f"（押さえ ${whole['held_usd']}）/ 上限 ${whole['cap_usd'] if whole['cap_usd'] is not None else '不明'}"]
    posts=whole.get('posts_read') or {};owned=whole.get('owned_read') or {};lists=whole.get('list_write') or {}
    comms=whole.get('community_read') or {}
    if posts:
        m=posts['month']
        out.append(f"  他人の投稿（Posts: Read・1 本 ${posts['price']['post_read_usd']}）: ${m['spent_estimate_usd']}（押さえ ${m['held_usd']}）")
    if owned:
        m=owned['month']
        out.append(f"  自分の投稿の数字（Owned Reads・1 本 ${owned['price']['owned_read_usd']}）: {m['posts']} 本・${m['spent_estimate_usd']}（押さえ ${m['held_usd']}）")
    if lists:
        m=lists['month']
        out.append(f"  非公開リストの書き込み（作る ${lists['price']['create_usd']}・足す／外す ${lists['price']['manage_usd']}）: {m['requests']} 回・${m['spent_estimate_usd']}（押さえ ${m['held_usd']}）")
    if comms and comms['month']['requests']:
        m=comms['month']
        out.append(f"  コミュニティの検索（Community: Read・1 件 ${comms['price']['community_read_usd']}）: {m['requests']} 回・{m['communities']} 件・${m['spent_estimate_usd']}（押さえ ${m['held_usd']}）")
    archive=whole.get('archive_read') or {}
    if archive and archive['month']['requests']:
        m=archive['month']
        out.append(f"  全期間の検索（Posts: Read・1 本 ${archive['price']['post_read_usd']}）: {m['requests']} 回・{m['posts']} 本・${m['spent_estimate_usd']}（押さえ ${m['held_usd']}）")
    for kind,key,label in PER_REQUEST:
        row=whole.get(key) or {}
        if row and row['month']['requests']:
            m=row['month']
            out.append(f"  {label}（{_PER_REQUEST_API[kind]}・1 回 ${row['price']['request_usd']}）: {m['requests']} 回・${m['spent_estimate_usd']}（押さえ ${m['held_usd']}）")
    return out


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
        print(json.dumps(result,ensure_ascii=False) if args.json else 'X 読取予算: 推定計上（請求実費ではない）\n'+'\n'.join(cost_lines(result))+'\n'+json.dumps(result,ensure_ascii=False,indent=2));return 0
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
