"""X の月間投稿数（本数）。読取予算（USD・`budget_x`）とは**別の口**。

設計 2.14.0 §0・§2。X の書込みは tier の月間上限に従う。thth は**自分が出した
本数を数え**、上限に達したら要求を 1 本も出さずに止める——

- 上限は管理者が入れる: `thth admin budget x-posts --monthly <本数> --by <名前>`。
  **既定は 0**（入れるまで X は投稿しない・静的理由 `x_post_budget_exhausted`）。
- 数える単位は **UTC の月**（`state/<account>/x-posts-<YYYY-MM>.json`・0600・fsync）。
- **成功と「結果不明」を別々に数える**。不明は自動で投げ直さないが、**枠は
  使ったものとして残す**——向こうに出ている可能性を 0 と見なさない。

`budget_x` と同じ状態機械（予約 → 発射 → 確定）を、金額でなく本数で回す。
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import sys

from . import accounts, admin_log, jst, server_files

# 予約 1 件の状態。`released`（発射しなかった）だけが枠を返す。
STATES = ('reserved', 'dispatched', 'settled', 'unknown', 'released')
HELD_STATES = ('reserved', 'dispatched')
COUNTED_STATES = ('settled', 'unknown', 'reserved', 'dispatched')
MONTH = re.compile(r'\d{4}-(?:0[1-9]|1[0-2])')
MAX_ENTRIES = 100000
EXHAUSTED = 'x_post_budget_exhausted'
CANNOT_SAY = 'post_budget_exhausted'


class PostBudgetError(ValueError):
    pass


def now():
    return jst.now_jst().astimezone(datetime.timezone.utc)


def month(at):
    return at.astimezone(datetime.timezone.utc).strftime('%Y-%m')


def timestamp(at):
    return at.astimezone(datetime.timezone.utc).isoformat()


def admin_folder():
    return Path(accounts.thth_root()).resolve() / 'state' / '_admin'


def account_folder(account):
    if not accounts.name_is_safe(account):
        raise PostBudgetError('invalid_budget_account')
    return Path(accounts.state_dir_for(account)).resolve()


def counts_name(period):
    return 'x-posts-' + period + '.json'


# ----- 上限（管理者が入れる） ------------------------------------------------

def policy(monthly=0, *, version=0, at=None):
    if type(monthly) is not int or type(monthly) is bool or not 0 <= monthly <= 1000000:
        raise PostBudgetError('invalid_post_budget_amount')
    return dict(version=version, monthly=monthly, at=timestamp(at or now()))


def empty_policy():
    return {'schema_version': 1, 'policy': policy(), 'policy_history': []}


def _valid_policy(value):
    if (type(value) is not dict or set(value) != {'version', 'monthly', 'at'}
            or type(value['version']) is not int or value['version'] < 0
            or not jst.parse(value['at'])):
        raise PostBudgetError('post_budget_unreadable')
    policy(value['monthly'])


def _validate_policy_file(value):
    if (type(value) is not dict or set(value) != {'schema_version', 'policy', 'policy_history'}
            or value.get('schema_version') != 1
            or type(value['policy_history']) is not list or len(value['policy_history']) > 10000):
        raise PostBudgetError('post_budget_unreadable')
    _valid_policy(value['policy'])
    for version, item in enumerate(value['policy_history']):
        _valid_policy(item)
        if item['version'] != version:
            raise PostBudgetError('post_budget_unreadable')
    if value['policy']['version'] != len(value['policy_history']):
        raise PostBudgetError('post_budget_unreadable')
    return value


def _read_policy(fd):
    try:
        from .handoff_cursor import _pairs
        return _validate_policy_file(json.loads(
            server_files.read_at(fd, 'budget_x_posts.json', private=True, maximum=1048576),
            object_pairs_hook=_pairs))
    except FileNotFoundError:
        return empty_policy()
    except (TypeError, KeyError, json.JSONDecodeError):
        raise PostBudgetError('post_budget_unreadable') from None


def _save_policy(fd, value):
    server_files.replace_at(fd, 'budget_x_posts.json',
                            server_files.encode(_validate_policy_file(value)), private=True)


@contextlib.contextmanager
def policy_locked():
    if admin_log._active_fd.get() is not None:
        raise PostBudgetError('post_budget_lock_order_refused')
    Path(accounts.thth_root()).mkdir(parents=True, exist_ok=True, mode=0o700)
    with server_files.directory(admin_folder(), create=True, private=True) as fd:
        with server_files.lock_at(fd, 'budget_x_posts.lock'):
            yield fd


def read_policy():
    try:
        with server_files.directory(admin_folder(), private=True) as fd:
            return _read_policy(fd)
    except FileNotFoundError:
        return empty_policy()


def monthly_cap():
    return read_policy()['policy']['monthly']


# ----- 数え上げ（アカウントごと・UTC 月） ------------------------------------

def empty_counts(account, period):
    return {'schema_version': 1, 'media': 'x', 'account': account, 'month_utc': period,
            'entries': {}}


def _validate_counts(value, account, period):
    if (type(value) is not dict
            or set(value) != {'schema_version', 'media', 'account', 'month_utc', 'entries'}
            or value.get('schema_version') != 1 or value.get('media') != 'x'
            or value.get('account') != account or value.get('month_utc') != period
            or type(value['entries']) is not dict or len(value['entries']) > MAX_ENTRIES):
        raise PostBudgetError('post_budget_unreadable')
    for key, row in value['entries'].items():
        if (not re.fullmatch(r'[0-9a-f]{32}', key) or type(row) is not dict
                or set(row) != {'state', 'at', 'dispatch_at', 'policy_version'}
                or row['state'] not in STATES or not jst.parse(row['at'])
                or type(row['policy_version']) is not int or row['policy_version'] < 0):
            raise PostBudgetError('post_budget_unreadable')
        dispatched = row['dispatch_at']
        if dispatched is not None and not jst.parse(dispatched):
            raise PostBudgetError('post_budget_unreadable')
        if (row['state'] in ('dispatched', 'settled', 'unknown')) != (dispatched is not None):
            raise PostBudgetError('post_budget_unreadable')
        if month(jst.parse(row['at'])) != period:
            raise PostBudgetError('post_budget_unreadable')
    return value


def _read_counts(fd, account, period):
    try:
        from .handoff_cursor import _pairs
        return _validate_counts(json.loads(
            server_files.read_at(fd, counts_name(period), private=True, maximum=16777216),
            object_pairs_hook=_pairs), account, period)
    except FileNotFoundError:
        return empty_counts(account, period)
    except (TypeError, KeyError, json.JSONDecodeError):
        raise PostBudgetError('post_budget_unreadable') from None


def _save_counts(fd, value, account, period):
    raw = server_files.encode(_validate_counts(value, account, period))
    if len(raw) > 16777216:
        raise PostBudgetError('post_budget_storage_full')
    server_files.replace_at(fd, counts_name(period), raw, private=True)


@contextlib.contextmanager
def _counts_locked(account, period):
    folder = account_folder(account)
    # アカウントの state ディレクトリは他の記録と共用なので、**ディレクトリの
    # モードは変えない**。数え上げのファイルと錠だけを 0600 で置く。
    with server_files.directory(folder, create=True) as fd:
        with server_files.lock_at(fd, 'x-posts.lock'):
            yield fd


def totals(value):
    out = {state: 0 for state in STATES}
    for row in value['entries'].values():
        out[row['state']] += 1
    return out


def used(value):
    return sum(count for state, count in totals(value).items() if state in COUNTED_STATES)


# ----- 予約 → 発射 → 確定 ----------------------------------------------------

class Slot:
    """1 本ぶんの枠。**要求の前に取り、応答の後で確定する。**"""

    def __init__(self, account, period, identifier):
        self.account = account
        self.period = period
        self.identifier = identifier
        self.state = 'reserved'

    def _move(self, target, *, dispatch=False):
        with _counts_locked(self.account, self.period) as fd:
            value = _read_counts(fd, self.account, self.period)
            row = value['entries'].get(self.identifier)
            if row is None:
                raise PostBudgetError('post_budget_reservation_missing')
            row['state'] = target
            if dispatch and row['dispatch_at'] is None:
                row['dispatch_at'] = timestamp(now())
            _save_counts(fd, value, self.account, self.period)
        self.state = target

    def dispatched(self):
        """要求を出す直前。以後、返事が無くても**枠は使ったものとして残す**。"""
        if self.state != 'reserved':
            raise PostBudgetError('post_budget_reservation_used')
        self._move('dispatched', dispatch=True)

    def settle(self):
        if self.state != 'dispatched':
            raise PostBudgetError('post_budget_reservation_used')
        self._move('settled')

    def finish(self):
        """後始末。発射していなければ返し、返事を見ていなければ**不明**として残す。"""
        try:
            if self.state == 'reserved':
                self._move('released')
            elif self.state == 'dispatched':
                self._move('unknown')
        except (OSError, ValueError):
            # 記録係が転んだせいで結果を取り違えない。枠は次の run で読み直す。
            pass


def reserve(account, *, at=None):
    at = at or now()
    period = month(at)
    cap = monthly_cap()
    with _counts_locked(account, period) as fd:
        value = _read_counts(fd, account, period)
        if used(value) + 1 > cap:
            raise PostBudgetError(EXHAUSTED)
        identifier = secrets.token_hex(16)
        value['entries'][identifier] = dict(state='reserved', at=timestamp(at), dispatch_at=None,
                                            policy_version=read_policy()['policy']['version'])
        _save_counts(fd, value, account, period)
    return Slot(account, period, identifier)


# ----- 報告と設定 ------------------------------------------------------------

def report(account=None, *, at=None):
    period = month(at or now())
    cap = monthly_cap()
    rows = {}
    names = [account] if account else accounts.list_account_names()
    for name in names:
        try:
            with _counts_locked(name, period) as fd:
                value = _read_counts(fd, name, period)
        except (OSError, ValueError, PostBudgetError):
            continue
        counted = totals(value)
        rows[name] = dict(posted=counted['settled'], unknown=counted['unknown'],
                          held=counted['reserved'] + counted['dispatched'],
                          used=used(value), remaining=max(0, cap - used(value)))
    reason = CANNOT_SAY if cap == 0 or all(row['remaining'] == 0 for row in rows.values()) and rows else None
    return dict(media='x', kind='x_posts', month_utc=period, monthly=cap,
                policy=admin_log.clean(read_policy()['policy']), accounts=rows,
                post_refusal=CANNOT_SAY if cap == 0 else None,
                cannot_say=[x for x in (reason, 'tier_limit_not_observed') if x])


def configure(monthly, *, by, via='cli', before_save=None):
    """上限を入れ替える（**管理者だけ**・変更は presence-only で記録に残す）。"""
    admin_log.actor(by)
    if isinstance(monthly, str):
        if not re.fullmatch(r'0|[1-9][0-9]{0,6}', monthly):
            raise PostBudgetError('invalid_post_budget_amount')
        monthly = int(monthly)
    requested = policy(monthly)
    with policy_locked() as fd:
        value = _read_policy(fd)
        before = dict(value['policy'])
        requested['version'] = before['version'] + 1
        try:
            previous = server_files.read_at(fd, 'budget_x_posts.json', private=True, maximum=1048576)
        except FileNotFoundError:
            previous = None
        changed = False

        def rollback():
            if not changed:
                return
            if previous is None:
                try:
                    os.unlink('budget_x_posts.json', dir_fd=fd)
                    os.fsync(fd)
                except FileNotFoundError:
                    pass
            else:
                server_files.replace_at(fd, 'budget_x_posts.json', previous, private=True)

        with admin_log.transaction(rollback=rollback):
            if before_save is not None:
                before_save()
            value['policy_history'].append(before)
            value['policy'] = requested
            changed = True
            _save_policy(fd, value)
            admin_log.append('budget_set', 'x-posts', {'media': 'x'}, by=by, via=via,
                             diff={'x_posts_budget': ['present' if before['monthly'] else 'absent',
                                                      'present' if requested['monthly'] else 'absent']})
    return report()


def command(args):
    """`thth admin budget x-posts [--monthly N --by 名前]`。"""
    try:
        if args.monthly is None:
            if args.by is not None:
                raise PostBudgetError('invalid_budget_options')
            result = report()
        else:
            result = configure(args.monthly, by=args.by)
        print(json.dumps(result, ensure_ascii=False) if args.json
              else 'X 月間投稿数（UTC 月・tier の実上限は Developer Portal の表示）\n'
                   + json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except admin_log.AdminLogError as exc:
        reason = ('budget_change_durability_unconfirmed' if exc.complete
                  else 'budget_change_partially_recorded' if exc.appended else 'budget_change_refused')
    except (OSError, ValueError, TypeError):
        reason = 'post_budget_unavailable_or_invalid_options'
    print(reason, file=sys.stderr)
    if args.json:
        print(json.dumps({'cannot_say': [reason]}))
    return 2
