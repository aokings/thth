"""遠くの道（thth.me の鍵・`thth login` のあと）の広告の口（設計 広告の資格と遠くの道 2026-11-06 §3）。

    ads_doctor                  thth ads doctor <名前>
    ads_show                    thth ads show <名前>（`--offline` は出さない: snapshot は VM の記録）
    ads_insights                thth ads insights <名前> [--preset|--since|--until|--level|--by]
    ads_estimate                thth ads estimate <名前> --targeting <JSON> [--goal]
    ads_catalog_list|products   thth ads catalog list|products <名前> …
    ads_leads                   thth ads leads <名前> --page <id> [--form <id>] [--limit]
    ads_network                 thth ads network <名前> [--since|--until]
    ads_shop                    thth ads shop <名前>
    ads_boost                   thth ads boost <名前> --object-story-id … --lifetime … --end …（全部 PAUSED）
    ads_pause                   thth ads pause <名前> --all|--id …（止める向き）
    ads_stop                    thth ads stop <名前>（止める向き）

**手元の道の命令の関数（`thth/ads/cli.py` の `cmd_*`）をそのまま VM で呼ぶ**（`server_meta` と同じ作り）。柵は同じ 1 本:

- 資格は `server_writes.current(kind="adaccount")`（鍵・口座の範囲・leave_gate の止め・書く口は writes の資格・
  台帳が `adaccounts/` にあること）。`by` は資格の actor（依頼の欄から名乗らせない）・`via` は `api`。
- 台帳の `production`・`ads_enabled`・上限・止めの印・通貨は手元の関数がそのまま見る。**遠くの道に旗・上限を
  触る口は無い**（`budget`・`auth`・`add` を出さない）。配信を始める・延ばす・止めの印を外す（`activate`・
  `extend`・`resume`・`end`・`watch`）も出さない（masaru 11/06・伺い (2)・1 段目も）。
- 関数が出す文（stdout・stderr）は捕まえて答えの JSON だけを返す。VM の置き場（skill の path・台帳の path）は
  答えから落とす。
- 答えのうちリード・フォーム・商品・カタログ・狙う相手（`SECRET_FIELDS`）と断りの文は、依頼の印
  （`api_requests` の `requests/<id>.json`）に書かない（`for_record`）。答えは 1 回だけ端末に届く。

MCP には出さない（`server_writes.WRITE_OPERATIONS` に入れない）。広告の鍵で MCP の `/report` を呼ぶと
`report_unavailable` のまま（審査員の鍵は `thth login` の道の鍵・`report_service` は直さない）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re

from . import accounts
from .report_service import ReportServiceError

# operation → (受ける欄, 必須の欄)。`account` と `operation` は常に受ける。
_FIELDS = {
    'ads_doctor': ((), ()),
    'ads_show': ((), ()),
    'ads_insights': (('preset', 'since', 'until', 'level', 'breakdown'), ()),
    'ads_estimate': (('targeting', 'goal'), ('targeting',)),
    'ads_catalog_list': ((), ()),
    'ads_catalog_products': (('catalog', 'limit'), ('catalog',)),
    'ads_leads': (('page', 'form', 'limit'), ()),
    'ads_network': (('since', 'until'), ()),
    'ads_shop': ((), ()),
    'ads_boost': (('object_story_id', 'lifetime', 'start', 'end', 'platforms', 'objective', 'url_tags',
                   'utm_campaign', 'landing_url'), ('object_story_id', 'lifetime', 'end')),
    'ads_pause': (('all', 'ids'), ()),
    'ads_stop': ((), ()),
}
OPERATIONS = frozenset(_FIELDS)
# 読むだけの口（writes の無い鍵でも通る）。書く 3 つはお金が動かない（boost は全部 PAUSED・pause・stop は止める向き）。
READS = frozenset(('ads_doctor', 'ads_show', 'ads_insights', 'ads_estimate', 'ads_catalog_list',
                   'ads_catalog_products', 'ads_leads', 'ads_network', 'ads_shop'))
WRITES = OPERATIONS - READS
_INTS = {'limit': (1, 100), 'lifetime': (1, 10 ** 12)}
_BOOLS = frozenset(('all',))
IDS_MAX = 50
FIELD_MAX = 4_000
_DIGITS = re.compile(r'[0-9]{1,32}\Z')
# 手元の口の符丁のうち、遠くの道でもそのまま言うもの（目録の doors に remote がある）。
REASONS = frozenset((
    'ads_not_production', 'ads_disabled', 'ads_days_over_ceiling', 'ads_lifetime_over_ceiling',
    'ads_token_missing', 'ads_token_expired', 'ads_token_invalid', 'ads_not_granted', 'ads_account_not_active',
    'ads_page_unknown', 'ads_target_missing', 'ads_form_other_page', 'ads_catalog_other_business',
    'ads_business_unknown', 'ads_object_story_id_invalid', 'ads_post_invalid', 'ads_time_invalid',
    'ads_time_range_invalid', 'ads_end_time_required', 'ads_platform_unsupported', 'ads_instagram_unlinked',
    'ads_objective_invalid', 'ads_url_tags_invalid', 'ads_utm_campaign_invalid', 'ads_landing_url_invalid',
    'ads_amount_invalid', 'ads_id_invalid',
    # Threads を出し先に（11/07）。手元と同じ符丁で、作る前に断る（何も作っていない）。
    'ads_threads_needs_instagram', 'ads_threads_unlinked', 'ads_threads_not_granted', 'ads_threads_unreadable',
    'ads_threads_scope_missing'))
# 依頼の印に書かない欄（リード＝第三者の個人情報・フォーム名・商品・カタログ・狙う相手）。
SECRET_FIELDS = ('leads', 'forms', 'products', 'catalogs', 'targeting')
_CODE = re.compile(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)+\Z')
_HEAD = re.compile(r'([a-z][a-z0-9]*(?:_[a-z0-9]+)+): ?(.*)\Z', re.S)
_TAIL = re.compile(r'（([a-z][a-z0-9]*(?:_[a-z0-9]+)+)）')
# 手元の広告の口座の止めの印（`ads/ledger.require_active`）は遠くの道では口座の止めと同じ言い方。
_RENAMED = {'adaccount_stopped': 'account_stopped'}


def error(reason, detail=None):
    exc = ReportServiceError(reason)
    exc.reason = detail
    raise exc


def _validate(request):
    from .ads import graphread, graphwrite
    keys, required = _FIELDS[request['operation']]
    if set(request) - set(keys) - {'operation', 'account'}:
        error('invalid_request')
    if not isinstance(request.get('account'), str):
        error('scope_unavailable')
    for name in required:
        if request.get(name) is None:
            error('invalid_request')
    choices = {'preset': graphread.PRESETS, 'level': graphread.LEVELS, 'breakdown': tuple(graphread.BREAKDOWNS),
               'goal': graphread.OPTIMIZATION_GOALS, 'objective': tuple(graphwrite.OBJECTIVES)}
    for name in keys:
        value = request.get(name)
        if value is None:
            continue
        if name in _INTS:
            low, high = _INTS[name]
            if type(value) is not int or not low <= value <= high:
                error('invalid_request')
        elif name in _BOOLS:
            if type(value) is not bool:
                error('invalid_request')
        elif name == 'ids':
            if (type(value) is not list or not 1 <= len(value) <= IDS_MAX
                    or any(not isinstance(v, str) or not _DIGITS.fullmatch(v) for v in value)):
                error('invalid_request')
        elif not isinstance(value, str) or not value.strip() or len(value) > FIELD_MAX:
            error('invalid_request')
        elif name in choices and value not in choices[name]:
            error('invalid_request')
    # 手元の `--targeting @ファイル` は端末のファイル。VM のファイルは読ませない（CLI が中身を送る）。
    if isinstance(request.get('targeting'), str) and request['targeting'].lstrip().startswith('@'):
        error('invalid_request')


def for_record(operation, result):
    """依頼の印（`requests/<id>.json`）に書く形。リード・フォーム・商品などは落とし、落とした欄の名前を `not_kept` に。"""
    if operation not in OPERATIONS or type(result) is not dict:
        return result
    dropped = [name for name in SECRET_FIELDS if result.get(name) is not None]
    # 断りの文は Meta の `error.message` を写すことがある（`server_meta.for_record` と同じ）。印には符丁だけ。
    if result.get('error') and result.get('reason') is not None:
        dropped.append('reason')
    dropped = sorted(dropped)
    if not dropped:
        return result
    return {**result, **{name: None for name in dropped}, 'not_kept': dropped}


# --------------------------------------------------------------------------
# 手元の命令の引数（`thth ads …` の parser の既定と同じ形）
# --------------------------------------------------------------------------

def _command(operation, account, request, actor):
    from .ads import cli as ads_cli
    base = dict(name=account, json=True)
    get = request.get
    if operation == 'ads_doctor':
        return ads_cli.cmd_doctor, base
    if operation == 'ads_show':
        return ads_cli.cmd_show, dict(base, offline=False)
    if operation == 'ads_insights':
        return ads_cli.cmd_insights, dict(base, preset=get('preset') or 'last_30d', since=get('since'),
                                          until=get('until'), level=get('level') or 'account', by=get('breakdown'))
    if operation == 'ads_estimate':
        return ads_cli.cmd_estimate, dict(base, targeting=request['targeting'], goal=get('goal') or 'LINK_CLICKS')
    if operation == 'ads_catalog_list':
        return ads_cli.cmd_catalog, dict(base, catalog_verb='list')
    if operation == 'ads_catalog_products':
        return ads_cli.cmd_catalog, dict(base, catalog_verb='products', catalog=request['catalog'],
                                         limit=get('limit') or ads_cli.LIST_DEFAULT)
    if operation == 'ads_leads':
        return ads_cli.cmd_leads, dict(base, page=get('page'), form=get('form'), limit=get('limit') or ads_cli.LIST_DEFAULT)
    if operation == 'ads_network':
        return ads_cli.cmd_network, dict(base, since=get('since'), until=get('until'))
    if operation == 'ads_shop':
        return ads_cli.cmd_shop, base
    writer = dict(base, by=actor, via='api')
    if operation == 'ads_boost':
        return ads_cli.cmd_boost, dict(writer, post=None, object_story_id=request['object_story_id'],
                                       lifetime=request['lifetime'], start=get('start') or 'now', end=request['end'],
                                       platforms=get('platforms') or 'facebook',
                                       objective=get('objective') or 'engagement', url_tags=get('url_tags'),
                                       utm_campaign=get('utm_campaign'), landing_url=get('landing_url'))
    if operation == 'ads_pause':
        return ads_cli.cmd_pause, dict(writer, all=bool(get('all')), id=list(get('ids') or ()))
    return ads_cli.cmd_stop, writer


def _scrub(value, hidden):
    """答えから VM の置き場（skill の path・THTH_ROOT）を落とす。`path` の欄は None に。"""
    if isinstance(value, dict):
        return {k: (None if k == 'path' and isinstance(v, str) else _scrub(v, hidden)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, hidden) for v in value]
    if isinstance(value, str):
        for text in hidden:
            value = value.replace(text, '<path>')
    return value


def _hidden():
    from .ads import guide
    out = [guide.skill_path(), accounts.thth_root()]
    return sorted({text for text in out if isinstance(text, str) and len(text) > 1}, key=len, reverse=True)


def _call(operation, account, request, actor):
    func, fields = _command(operation, account, request, actor)
    out, err = io.StringIO(), io.StringIO()
    # 手元の関数の文（リード・商品・狙う相手を含みうる）は VM のログへ出さない。答えは JSON だけ。
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = func(argparse.Namespace(**fields))
    try:
        payload = json.loads(out.getvalue())
    except ValueError:
        payload = None
    # doctor は × が 1 つあると rc 1（手元と同じ）。boost の result failed・unconfirmed は rc 3 で答えはある。
    ok = rc == 0 or (operation == 'ads_doctor' and rc == 1) or (
        rc == 3 and type(payload) is dict and payload.get('result') in ('failed', 'unconfirmed'))
    if ok and type(payload) is dict and payload.get('ok') is not False:
        # 答えの名前の欄は手元と同じ `adaccount`（`account` は show の Meta の広告アカウントの欄）。
        return _scrub(payload, _hidden())
    _refused(payload, err.getvalue())


def _refused(payload, err):
    from .server_reads import short
    from .server_writes import SAFE_ERRORS
    code = message = None
    if type(payload) is dict:
        code, message = payload.get('code') or payload.get('error'), payload.get('message')
    if not (isinstance(code, str) and _CODE.fullmatch(code)):
        code = None
        # 1 行目は skill の在り処（`ads/cli._fail`）。2 行目からが「符丁: 文」か「文（符丁）」。
        text = ' '.join(line.strip() for line in err.splitlines()[1:] if line.strip())
        head = _HEAD.match(text)
        tail = _TAIL.findall(text)
        if head:
            code, message = head.group(1), head.group(2)
        elif tail:
            code, message = tail[-1], text
        else:
            message = text
    code = _RENAMED.get(code, code)
    if code not in SAFE_ERRORS:
        message = f'{code}: {message}' if code and message else (message or code)
        code = 'remote_command_refused'
    error(code, short(_scrub(message, _hidden()) if isinstance(message, str) else None))


def execute(context, request):
    """遠くの道の広告の口の入口（`server_writes.serve` から来る）。"""
    if type(request) is not dict or request.get('operation') not in OPERATIONS:
        error('unsupported_operation')
    _validate(request)
    operation, account = request['operation'], request['account']
    from . import leave_gate
    from .server_writes import current
    try:
        current(context, account, write=operation in WRITES, kind=accounts.ADACCOUNT_KIND)
        with leave_gate.lease(account):
            return _call(operation, account, request, context.actor)
    except accounts.AccountLeaving:
        error('account_leaving')
    except accounts.AccountStopped:
        error('account_stopped')
    except ReportServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
        error('read_unavailable' if operation in READS else 'write_unavailable')
