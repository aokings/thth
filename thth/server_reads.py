"""利用者 scope の読む口（設計 3.14.0 §3.2）。

書く口（`server_writes.execute`）と同じ資格・同じ口座の範囲で、今の CLI の読む命令を
呼ぶだけ。**判断・整形を足さない**——返すのは CLI の `--json` と同じ形。足すのは次だけ:

- 資格の範囲（範囲外は `invalid_scope`・退出中は `account_leaving`）。止まった口座
  （安全装置）でも読むのは許す。
- `limit`（省略 20・1〜100）で一覧を先頭から切る。
- `collect` は口座ごとに 10 分に 1 回まで（`collect_too_soon` と `next_at`）。
- 断りは静的な符丁。媒体の API の失敗は `upstream_unavailable` と短い理由
  （秘密・URL・path を落とした 1 行）。自由文の欄（取り直しの `errors` 等）も同じく落とす。

記録: 読むだけの口は口座の記録に書かない。`collect`・`refresh` は CLI と同じく記録を
更新し、`runs-*.ndjson` への 1 行も CLI と同じ（`collect`・`mentions`）。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import re
import sys
from pathlib import Path

from . import accounts, jst
from .report_service import ReportContext, ReportServiceError

OPERATIONS = frozenset(('posts', 'replies', 'measured', 'collect', 'mentions',
                        'topics_search', 'profile', 'location_search'))
# 断りの符丁（`server_writes.SAFE_ERRORS` に足す）。
REASONS = frozenset(('collect_too_soon', 'upstream_unavailable', 'read_unavailable'))
# `upstream_unavailable` に添える静的な理由（媒体の返した文を写せないとき）。
UPSTREAM_DETAILS = frozenset(('standard_access', 'collect_failed'))

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
COLLECT_INTERVAL = _dt.timedelta(minutes=10)
REASON_CHARS = 160

# operation → (受ける欄, 必須の欄)。`account` と `operation` は常に受ける。
_FIELDS = {
    'posts': (('limit', 'refresh'), ()),
    'replies': (('limit', 'refresh', 'post_id'), ()),
    'measured': (('limit', 'post_id'), ()),
    'collect': ((), ()),
    'mentions': (('limit', 'refresh'), ()),
    'topics_search': (('query', 'limit'), ('query',)),
    'profile': (('username',), ('username',)),
    'location_search': (('query',), ('query',)),
}

_URL = re.compile(r'https?://\S+')
_PATH = re.compile(r'(?<![\w.])(?:~|\.{1,2})?(?:/[\w.@+\-]+){2,}/?')


def _now():
    return jst.now_jst()


def error(reason, detail=None, next_at=None):
    exc = ReportServiceError(reason)
    exc.reason = detail
    exc.next_at = next_at
    raise exc


def short(text):
    """媒体の返した理由を 1 行に（秘密・URL・path を落とす）。空なら None。"""
    if not isinstance(text, str) or not text.strip():
        return None
    from . import redact
    value = redact.redact(text) or ''
    value = _URL.sub('<url>', value)
    value = _PATH.sub('<path>', value)
    value = ' '.join(value.split())
    return value[:REASON_CHARS] or None


def _validate(request):
    operation = request.get('operation')
    keys, required = _FIELDS[operation]
    if set(request) - set(keys) - {'operation', 'account'}:
        error('invalid_request')
    if not isinstance(request.get('account'), str):
        error('invalid_scope')
    for name in required:
        if not isinstance(request.get(name), str) or not request[name].strip():
            error('invalid_request')
    if 'limit' in request and (type(request['limit']) is not int or not 1 <= request['limit'] <= MAX_LIMIT):
        error('invalid_options')
    if 'refresh' in request and type(request['refresh']) is not bool:
        error('invalid_request')
    if 'post_id' in request and (not isinstance(request['post_id'], str) or not request['post_id'].strip()):
        error('invalid_request')
    for name in ('query', 'username'):
        if name in request and len(request[name]) > 200:
            error('invalid_request')


def _scope(context, account):
    """範囲外は `invalid_scope`。退出中は `account_leaving`（`current` が言う）。"""
    if type(context) is not ReportContext or context.scope != 'user':
        error('scope_unavailable')
    from .report_service import check_excluded
    check_excluded(context, account)
    if account not in context.allowed_accounts:
        error('invalid_scope')
    from .server_writes import current
    return current(context, account)


def _limit(request):
    return request.get('limit', DEFAULT_LIMIT)


# --------------------------------------------------------------------------
# 断りの写し
# --------------------------------------------------------------------------

def _refuse(kind, message):
    if kind == 'unsupported':
        error('unsupported_operation')
    if kind in ('no_token', 'not_granted'):
        error('permission_unavailable')
    if kind == 'narrowed':
        error('upstream_unavailable', 'standard_access')
    if kind == 'account':
        error('read_unavailable')
    error('upstream_unavailable', short(message))


def _scrub_refresh(refresh):
    """取り直しの自由文（repo の path・push の理由が混ざりうる）を 1 行に落とす。形は同じ。"""
    if not isinstance(refresh, dict):
        return refresh
    out = dict(refresh)
    out['errors'] = [short(item) for item in refresh.get('errors') or []]
    out['failed'] = [dict(item, reason=short(item.get('reason'))) for item in refresh.get('failed') or []]
    return out


# --------------------------------------------------------------------------
# 口ごとの中身（CLI の関数を呼ぶだけ）
# --------------------------------------------------------------------------

def _posts(account, request, cfg):
    from . import account_report
    # `thth posts` は毎回 API から引く（記録は突合にだけ使う）。`refresh` は同じ動き。
    result = account_report.recent_posts(account, limit=_limit(request))
    if result.get('error'):
        error('upstream_unavailable', short(result['error']))
    return result


def _replies(account, request, cfg):
    from . import cli, postid
    post = request.get('post_id')
    if post:
        try:
            post = postid.for_account(cfg, post)
        except postid.PostIdError:
            error('invalid_request')
    result, refresh = cli.replies_json(account, post=post, refresh=request.get('refresh', False),
                                       wait=0, log=lambda _line: None)
    result = dict(result, replies=result['replies'][:_limit(request)])
    if refresh is not None:
        result['refresh'] = _scrub_refresh(refresh)
    return result


def _measured(account, request, cfg):
    from . import cli
    result = cli.measured_json(account, post=request.get('post_id'))
    return dict(result, posts=result['posts'][:_limit(request)])


def _collect_slot(account):
    """口座ごとに 10 分に 1 回まで。印は `state/<口座>/reads/collect.json`（0600）。

    時刻は採る**前に**刻む——失敗した採取も 1 回に数える（連打で媒体の上限を食わない）。
    同じ口座の採取が重なれば `account_busy`。
    """
    from . import server_files
    directory = Path(accounts.state_dir_for(account)) / 'reads'
    stack = contextlib.ExitStack()
    fd = stack.enter_context(server_files.directory(directory, create=True, private=True))
    try:
        stack.enter_context(server_files.lock_at(fd, 'collect.lock'))
    except BlockingIOError:
        stack.close()
        error('account_busy')
    try:
        now = _now()
        try:
            last = json.loads(server_files.read_at(fd, 'collect.json', private=True)).get('at')
            last = _dt.datetime.fromisoformat(last) if isinstance(last, str) else None
        except FileNotFoundError:
            last = None
        except (ValueError, AttributeError, TypeError):
            last = None
        if last is not None and last.tzinfo is not None and now < last + COLLECT_INTERVAL:
            error('collect_too_soon', next_at=jst.iso(last + COLLECT_INTERVAL))
        server_files.replace_at(fd, 'collect.json', server_files.encode({'at': jst.iso(now)}), private=True)
    except BaseException:
        stack.close()
        raise
    return stack


def _collect(account, request, cfg):
    from . import collect as collect_mod
    lines = []
    with _collect_slot(account):
        # `thth collect <account>` と同じ入口（runs に 1 行残る）。
        rc = collect_mod.run_collect(account, log=lines.append, trigger=collect_mod.TRIGGER_MANUAL)
    if rc:
        error('upstream_unavailable', short(lines[-1] if lines else None) or 'collect_failed')
    return _measured(account, {'operation': 'measured', 'account': account}, cfg)


def _mentions(account, request, cfg):
    from . import threads_read_cli as reads
    observed = None
    try:
        rows = reads._fetch(account, capability='mentions',
                            call=lambda adapter: reads.mentions_call(adapter, account),
                            narrowed_note=reads.MENTIONS_NARROWED_NOTE)
        observed = len(rows)
    except reads.ReadFailed as failed:
        reads.record_mentions_run(account, observed, failed.rc)
        _refuse(failed.kind, failed.message)
    reads.record_mentions_run(account, observed, 0)
    # `mentions` は毎回 API から引く（記録は持たない）。`refresh` は同じ動き。
    return reads.mentions_json(account, rows[:_limit(request)])


def _topics_search(account, request, cfg):
    from . import threads_read_cli as reads
    query = request['query'].strip()
    limit = _limit(request)
    try:
        result = reads._fetch(account, capability='keyword_search',
                              call=lambda adapter: reads.search_call(adapter, account, query,
                                                                     search_type='TOP', limit=limit),
                              narrowed_note=reads.SEARCH_NARROWED_NOTE)
    except reads.ReadFailed as failed:
        _refuse(failed.kind, failed.message)
    material = reads.search_json(result, account, query, search_type='TOP', limit=limit)
    # 集計と指す先だけ（本文は入らない・保存しない）。自由文の理由は 1 行に落とす。
    material['tag_cannot_say'] = short(material.get('tag_cannot_say'))
    material['replied_lookup'] = dict(material['replied_lookup'],
                                      reason=short(material['replied_lookup'].get('reason')))
    return material


def _profile(account, request, cfg):
    from . import threads_read_cli as reads
    username = request['username']
    try:
        profile = reads._fetch(account, capability='profile_lookup',
                               call=lambda adapter: adapter.profile_lookup(username),
                               narrowed_note=reads.STANDARD_ACCESS_NOTE)
    except reads.ReadFailed as failed:
        _refuse(failed.kind, failed.message)
    return reads.profile_json(account, profile)


def _location_search(account, request, cfg):
    from . import retract_cli
    query = request['query']
    try:
        rows = retract_cli.location_rows(account, query)
    except retract_cli.LocationFailed as failed:
        _refuse(failed.kind, failed.message)
    return retract_cli.location_json(account, query, rows)


_HANDLERS = {
    'posts': _posts, 'replies': _replies, 'measured': _measured, 'collect': _collect,
    'mentions': _mentions, 'topics_search': _topics_search, 'profile': _profile,
    'location_search': _location_search,
}


def execute(context, request):
    """読む口の入口（`report_service.execute_report` から来る）。"""
    if type(request) is not dict or request.get('operation') not in OPERATIONS:
        error('unsupported_operation')
    _validate(request)
    account = request['account']
    cfg = _scope(context, account)
    from . import leave_gate
    try:
        with leave_gate.lease(account):
            # CLI の関数は人向けに print しうる。stdio の MCP では stdout が道なので、
            # 読む口の間は stderr へ逃がす（答えは戻り値だけ）。
            with contextlib.redirect_stdout(sys.stderr):
                return _HANDLERS[request['operation']](account, request, cfg)
    except accounts.AccountLeaving:
        error('account_leaving')
    except ReportServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
        error('read_unavailable')
