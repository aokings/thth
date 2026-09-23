"""Local scoped facade. This is not authentication or filesystem isolation.

A trusted host constructs ReportContext after authenticating its caller, within
an already isolated execution environment. Never derive it from report input.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType

from . import accounts, after_cli, analytics_report, jst, operations_handoff, replies, leave_gate


class ReportServiceError(ValueError):
    """Bounded errors suitable for the caller; never include core exception text."""


@dataclass(frozen=True)
class ReportContext:
    """Host-trusted account -> project metadata, copied and immutable."""

    allowed_accounts: Mapping[str, str | None]
    scope: str = "user"
    writes: bool = False
    actor: str | None = None
    credential_digest: str | None = None
    credentials_path: str | None = None
    # Diagnostic-only original scope. Not authority and not an equality binding:
    # A stopping must not invalidate an otherwise unchanged B job capability.
    excluded_accounts: Mapping[str, str | None] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        if self.scope not in ("user", "admin") or not isinstance(self.allowed_accounts, Mapping):
            raise ReportServiceError("invalid_context")
        from .approval_relay import PERSON
        if (type(self.writes) is not bool or self.writes and (self.scope != 'user'
                or not isinstance(self.actor, str) or not PERSON.fullmatch(self.actor))
                or self.actor is not None and (not isinstance(self.actor, str) or not PERSON.fullmatch(self.actor))):
            raise ReportServiceError("invalid_context")
        allowed = dict(self.allowed_accounts)
        for account, project in allowed.items():
            if not isinstance(account, str) or not accounts.name_is_safe(account):
                raise ReportServiceError("invalid_context")
            if project is not None and (not isinstance(project, str) or not project.strip()):
                raise ReportServiceError("invalid_context")
        object.__setattr__(self, "allowed_accounts", MappingProxyType(allowed))
        if not isinstance(self.excluded_accounts, Mapping):raise ReportServiceError('invalid_context')
        excluded=dict(self.excluded_accounts)
        if any(not accounts.name_is_safe(name) or name in allowed or project is not None and
               (not isinstance(project,str) or not project.strip()) for name,project in excluded.items()):
            raise ReportServiceError('invalid_context')
        object.__setattr__(self,'excluded_accounts',MappingProxyType(excluded))


def execute_report(context: ReportContext, request: dict) -> dict:
    """Validate all scope/options before calling any core loader.

    A project expands ONLY through trusted context, not the machine's registry.
    No environment switching, arbitrary command, path or output artifact exists.
    """
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    operation = request.get("operation")
    if operation in ('draft_list', 'queue', 'request_status'):
        from .server_writes import read
        return read(context, request)
    if isinstance(operation, str) and operation.startswith("admin_"):
        if context.scope != "admin":
            raise ReportServiceError("unsupported_operation")
        from . import admin_log, admin_report
        name = operation[6:]
        if name not in admin_report.OPERATIONS:
            raise ReportServiceError("unsupported_operation")
        options = {"account", "limit"} if name == "account" else {"account", "since", "event"} if name == "log" else {"since_last_read"} if name == "diff" else set()
        if set(request) - {"operation"} - options:
            raise ReportServiceError("invalid_request")
        if "account" in request and request["account"] not in context.allowed_accounts:
            raise ReportServiceError("scope_unavailable")
        if "since_last_read" in request and type(request["since_last_read"]) is not bool:
            raise ReportServiceError("invalid_options")
        event = request.get("event")
        if name == "log" and event is not None and (not isinstance(event, str) or event not in admin_log.EVENTS):
            raise ReportServiceError("invalid_options")
        kwargs = {key: value for key, value in request.items() if key != "operation"}
        if name == "diff":
            kwargs.setdefault("since_last_read", True)
        try:
            return admin_report.answer(name, via="http", **kwargs)
        except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
            raise ReportServiceError("report_unavailable") from None
    operation, scope_key, scope_value, names, now, kwargs = _scoped_request(context, request)
    reports = {}
    try:
        with leave_gate.read_leases(set(_active_names(context)) | set(names)), replies.report_scope(_active_names(context)):
            for name in names:
                if operation == "analytics_report":
                    reports[name] = analytics_report.answer(name, now=now, **kwargs)
                else:
                    reports[name] = operations_handoff.answer(name, now=now, **kwargs)
    except accounts.AccountLeaving:
        raise ReportServiceError('account_leaving') from None
    except (accounts.AccountError, after_cli.AfterError, operations_handoff.HandoffError,
            OSError, ValueError, TypeError, KeyError, OverflowError):
        raise ReportServiceError("report_unavailable") from None
    return {"schema_version": 1, "report_type": "scoped_report_batch",
            "generated_at": jst.iso(now), "operation": operation,
            "scope": {scope_key: scope_value}, "reports": reports,
            "limitations": ["Host-trusted scope only; authentication and filesystem isolation are external",
                            "Projects contain only accounts allowed by this context",
                            "Underlying report freshness and coverage limits remain applicable"]}


def _active_names(context):
    from . import leave_gate
    return tuple(name for name in context.allowed_accounts if not leave_gate.stopped(name))


def check_excluded(context, name):
    if isinstance(name,str) and name in context.excluded_accounts:
        try:leave_gate.check_busy(name)
        except accounts.AccountLeaving:raise ReportServiceError('account_leaving') from None


def _scoped_request(context: ReportContext, request: dict):
    """One trusted account selector for the HTTP batch and legacy MCP shape.

    A leave stop filter can narrow ``names`` here without allowing either
    transport to enumerate the machine-wide account registry.
    """
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in {"analytics_report", "operations_handoff"}:
        raise ReportServiceError("unsupported_operation")
    options = {"window_days", "min_n", "compare_previous", "by"} if operation == "analytics_report" else {"since_last_read"}
    if set(request) - {"operation", "account", "project"} - options:
        raise ReportServiceError("invalid_request")
    # Presence, rather than truthiness, rejects null and ambiguous scope.
    if ("account" in request) == ("project" in request):
        raise ReportServiceError("invalid_scope")
    scope_key = "account" if "account" in request else "project"
    scope_value = request[scope_key]
    if not isinstance(scope_value, str) or not scope_value.strip():
        raise ReportServiceError("invalid_scope")
    if "compare_previous" in request and type(request["compare_previous"]) is not bool:
        raise ReportServiceError("invalid_options")
    if "by" in request and (request["by"] not in ("kind", "hour_band", "topic", "tag") or request.get("compare_previous") is not True):
        raise ReportServiceError("invalid_options")
    if "since_last_read" in request and type(request["since_last_read"]) is not bool:
        raise ReportServiceError("invalid_options")
    for key in options - {"compare_previous", "by", "since_last_read"}:
        if key in request and (type(request[key]) is not int or request[key] < 1):
            raise ReportServiceError("invalid_options")
    if scope_key == "account":
        check_excluded(context,scope_value)
        names = [scope_value] if scope_value in context.allowed_accounts else []
    else:
        names = sorted(name for name, project in context.allowed_accounts.items() if project == scope_value)
    if not names:
        if scope_key=='project':
            for name,project in context.excluded_accounts.items():
                if project==scope_value:check_excluded(context,name)
        # Same error for missing and existing-but-unpermitted resources.
        raise ReportServiceError("scope_unavailable")
    active = _active_names(context)
    selected = [name for name in names if name in active]
    if not selected:
        try:
            for name in names:leave_gate.check_busy(name)
        except accounts.AccountLeaving:raise ReportServiceError('account_leaving') from None
        raise ReportServiceError('scope_unavailable')
    names=selected
    now = jst.now_jst()
    kwargs = {key: request[key] for key in options if key in request}
    return operation, scope_key, scope_value, names, now, kwargs


def execute_mcp_report(context: ReportContext, request: dict) -> dict:
    """Keep the established MCP payload while using authenticated scope.

    This is intentionally distinct from HTTP's scoped_report_batch envelope.
    The underlying calculation functions remain shared with CLI and HTTP.
    """
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if request.get("operation") == "study_report":
        return _mcp_study(context, request)
    operation, scope_key, scope_value, names, now, kwargs = _scoped_request(context, request)
    account = scope_value if scope_key == "account" else None
    project = scope_value if scope_key == "project" else None
    try:
        with leave_gate.read_leases(set(_active_names(context)) | set(names)):
            if operation == "analytics_report":
                return analytics_report.answer(account, project=project, now=now,
                                               trusted_names=tuple(names),
                                               allowed_names=_active_names(context), **kwargs)
            return operations_handoff.answer(account, project=project, now=now,
                                             trusted_names=tuple(names),
                                             allowed_names=_active_names(context), **kwargs)
    except accounts.AccountLeaving:
        raise ReportServiceError('account_leaving') from None
    except (accounts.AccountError, after_cli.AfterError, operations_handoff.HandoffError,
            OSError, ValueError, TypeError, KeyError, OverflowError):
        raise ReportServiceError("report_unavailable") from None


def execute_morning(context: ReportContext, request: dict, invoked_as: str = "morning") -> dict:
    """毎朝の一枚（設計 3.1.0 §1）。**credential が許した account だけ。**

    サーバ型では栞を進めない（`operations_handoff` の MCP 経路が `mark_read` を
    受けないのと同じ規律・読む口は書かない）。進めなかったことは黙らず
    `cannot_say` の `server_mode_read_only` で言う。
    """
    from . import morning
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if set(request) - {"operation", "target", "mark"}:
        raise ReportServiceError("invalid_request")
    target = request.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ReportServiceError("invalid_scope")
    if "mark" in request and type(request["mark"]) is not bool:
        raise ReportServiceError("invalid_options")
    allowed = _active_names(context)
    if not allowed:
        raise ReportServiceError("scope_unavailable")
    try:
        with leave_gate.read_leases(set(allowed)), replies.report_scope(allowed):
            payload = morning.build(target, mark=False, allowed_names=allowed,
                                    invoked_as=invoked_as)
    except morning.MorningError:
        raise ReportServiceError("scope_unavailable") from None
    except accounts.AccountLeaving:
        raise ReportServiceError("account_leaving") from None
    except (accounts.AccountError, OSError, ValueError, TypeError, KeyError, OverflowError):
        raise ReportServiceError("report_unavailable") from None
    payload["cannot_say"] = sorted(set(payload["cannot_say"]) | {"server_mode_read_only"})
    return payload


def execute_map_show(context: ReportContext, request: dict) -> dict:
    """観測の地図（設計 3.5.0 §3）。**credential が許した project だけ・読むだけ。**

    世間の層は project の中だけ（照合 §6-7）。他の持ち主の project は、在っても無くても
    同じ `scope_unavailable`。
    """
    from . import map_store, map_view
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if context.scope != "user":
        raise ReportServiceError("unsupported_operation")
    if set(request) - {"operation", "project", "node", "since"}:
        raise ReportServiceError("invalid_request")
    for key in ("project", "node", "since"):
        if request.get(key) is not None and not isinstance(request[key], str):
            raise ReportServiceError("invalid_request")
    active = _active_names(context)
    allowed = {name: context.allowed_accounts[name] for name in active}
    if not allowed:
        raise ReportServiceError("scope_unavailable")
    try:
        with leave_gate.read_leases(set(active)):
            return map_view.show(request.get("project"), node=request.get("node"),
                                 since=request.get("since") or map_view.DEFAULT_SINCE,
                                 allowed=allowed)
    except map_store.MapError as error:
        raise ReportServiceError(str(error)) from None
    except accounts.AccountLeaving:
        raise ReportServiceError("account_leaving") from None
    except (accounts.AccountError, OSError, ValueError, TypeError, KeyError, OverflowError):
        raise ReportServiceError("report_unavailable") from None


REPORT_OPERATIONS = frozenset(('report_file', 'report_list', 'report_show', 'report_add'))


def _report_error(error):
    """`report_inbox.ReportError` を口の断りに写す（静的な符丁と、重複なら既存の id）。"""
    exc = ReportServiceError(str(error))
    exc.report_id = getattr(error, 'report_id', None)
    return exc


def execute_user_reports(context: ReportContext, request: dict) -> dict:
    """報告の口の利用者側（設計 3.1.2 §1）。**credential が account と project を決める。**

    - 置く: 誰が置いたかは credential の `actor`（要求の欄からは作らない）。
      書く口（`writes`）の credential でなくても置ける——SNS には何も出ない。
    - 読む: 自分の account と、その project の報告だけ。無い id と読めない id は
      同じ `report_not_found`。
    """
    from . import admin_log, report_inbox, server_writes
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if context.scope != 'user':
        raise ReportServiceError("unsupported_operation")
    operation = request.get("operation")
    projects = {project for project in context.allowed_accounts.values() if project}
    scope = report_inbox.Scope(context.allowed_accounts, projects)
    try:
        if operation == 'report_file':
            # `from_last_refusal: true`（設計 3.3.0 B2）なら kind（既定 friction）と
            # body（既定の 1 文）を省ける。再現手順はその account の直前の断りの控え。
            from_last = request.get('from_last_refusal', False)
            if type(from_last) is not bool:
                raise ReportServiceError("invalid_request")
            needed = {'account', 'title'} | (set() if from_last else {'kind', 'body'})
            if (set(request) - {'operation', 'account', 'kind', 'title', 'body', 'repro',
                                'from_last_refusal'}
                    or not needed <= set(request)):
                raise ReportServiceError("invalid_request")
            if any(not isinstance(request[key], str) for key in needed):
                raise ReportServiceError("invalid_request")
            for key in ('kind', 'body', 'repro'):
                if request.get(key) is not None and not isinstance(request[key], str):
                    raise ReportServiceError("invalid_request")
            account = request['account']
            # 再認証・停止・project の一致は書く口と同じ門（`write=False`）。
            cfg = server_writes.current(context, account)
            if not context.actor:
                raise ReportServiceError("by_required")
            # 台帳が指す秘密の値そのものも当てる（綴りの無い貼り付け）。
            admin_log.register_account_secrets(cfg, via='mcp')
            kind, body, repro = request.get('kind'), request.get('body'), request.get('repro')
            if from_last:
                kind, body, repro = report_inbox.from_last_refusal(
                    account, kind=kind, body=body, repro=repro)
            return report_inbox.file_report(
                account, kind=kind, title=request['title'], body=body,
                repro=repro, by=context.actor, via='mcp',
                project=context.allowed_accounts[account], medium=cfg.get('media'), trusted=True)
        if operation == 'report_list':
            if set(request) - {'operation', 'status'}:
                raise ReportServiceError("invalid_request")
            return report_inbox.list_reports(scope=scope, status=request.get('status') or 'all')
        if operation == 'report_show':
            if set(request) - {'operation', 'report_id'} or not isinstance(request.get('report_id'), str):
                raise ReportServiceError("invalid_request")
            return report_inbox.show(request['report_id'], scope=scope)
        if operation == 'report_add':
            # 報告した側の追記（設計 3.2.0 §4.5-1）。**自分の project の報告にだけ**
            # （範囲の外は無い報告と同じ `report_not_found`）。誰が足したかは
            # credential の `actor`（要求の欄からは作らない）。
            if (set(request) - {'operation', 'report_id', 'text'}
                    or any(not isinstance(request.get(key), str) for key in ('report_id', 'text'))):
                raise ReportServiceError("invalid_request")
            if not context.actor:
                raise ReportServiceError("by_required")
            return report_inbox.add(request['report_id'], by=context.actor, text=request['text'],
                                    scope=scope, via='mcp')
    except report_inbox.ReportError as error:
        raise _report_error(error) from None
    except accounts.AccountError:
        raise ReportServiceError("scope_unavailable") from None
    raise ReportServiceError("unsupported_operation")


ADMIN_REPORT_OPERATIONS = frozenset(('admin_reports_list', 'admin_reports_show',
                                     'admin_reports_reply', 'admin_reports_close'))


def execute_admin_reports(context: ReportContext, request: dict) -> dict:
    """報告の口の実装側（設計 3.1.2 §1）。**管理者は全 project を読む。**

    返事と閉じるは書く操作なので、予算・監視語と同じ門（credential の読み直し）を
    通し、`by` は要求に明示させる（管理者 credential の中に人の名前は無い）。
    """
    from . import report_inbox
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if context.scope != 'admin':
        raise ReportServiceError("unsupported_operation")
    operation = request.get("operation")
    shapes = {'admin_reports_list': ({'status'}, set()),
              'admin_reports_show': ({'report_id'}, {'report_id'}),
              'admin_reports_reply': ({'report_id', 'text', 'by'}, {'report_id', 'text', 'by'}),
              'admin_reports_close': ({'report_id', 'reason', 'version', 'by'},
                                      {'report_id', 'reason', 'version', 'by'})}
    if operation not in shapes:
        raise ReportServiceError("unsupported_operation")
    allowed, required = shapes[operation]
    if set(request) - allowed - {'operation'} or not required <= set(request):
        raise ReportServiceError("invalid_request")
    if any(request[key] is not None and not isinstance(request[key], str)
           for key in allowed & set(request)):
        raise ReportServiceError("invalid_request")
    try:
        if operation == 'admin_reports_list':
            return report_inbox.list_reports(scope=None, status=request.get('status') or 'open')
        if operation == 'admin_reports_show':
            return report_inbox.show(request['report_id'])
        _credential_unchanged(context)
        if operation == 'admin_reports_reply':
            return report_inbox.reply(request['report_id'], by=request['by'],
                                      text=request['text'], via='mcp')
        return report_inbox.close(request['report_id'], by=request['by'], reason=request['reason'],
                                  version=request['version'], via='mcp')
    except report_inbox.ReportError as error:
        raise _report_error(error) from None


PLAZA_OPERATIONS = frozenset(('plaza_post', 'plaza_list', 'plaza_show', 'plaza_reply',
                              'plaza_update'))
# 広場の口の要求の形（`許す鍵`・`要る鍵`）。型は MCP の inputSchema と下の検査で見る。
_PLAZA_SHAPES = {
    'plaza_post': ({'account', 'kind', 'title', 'body', 'scope', 'kind_detail', 'how',
                    'evidence_level', 'declarations', 'hypothesis', 'change', 'until', 'min_n'},
                   {'account', 'kind', 'title', 'body'}),
    'plaza_list': ({'project', 'open'}, set()),
    'plaza_show': ({'plaza_id'}, {'plaza_id'}),
    'plaza_reply': ({'plaza_id', 'account', 'kind', 'text', 'measure_id', 'result'},
                    {'plaza_id', 'account', 'kind'}),
    'plaza_update': ({'plaza_id', 'account', 'refresh', 'verdict', 'reason', 'visibility'},
                     {'plaza_id', 'account'}),
}
# `open` は MCP の口に無い（open にするのは人の CLI の二段確認だけ・masaru 裁定 09-23）。
_PLAZA_BOOLS = {'open', 'refresh'}


def _plaza_error(error):
    """`plaza.PlazaError` を口の断りに写す（静的な符丁と、重複なら既存の id）。"""
    exc = ReportServiceError(str(error))
    exc.plaza_id = getattr(error, 'plaza_id', None)
    return exc


def _plaza_request(request, operation):
    allowed, required = _PLAZA_SHAPES[operation]
    if set(request) - allowed - {'operation'} or not required <= set(request):
        raise ReportServiceError("invalid_request")
    for key, value in request.items():
        if key == 'operation' or value is None:
            continue
        if key in _PLAZA_BOOLS:
            if type(value) is not bool:
                raise ReportServiceError("invalid_request")
        elif key == 'min_n':
            if type(value) is not int:
                raise ReportServiceError("invalid_request")
        elif key == 'declarations':
            if type(value) is not list or any(type(row) is not dict for row in value):
                raise ReportServiceError("invalid_request")
        elif not isinstance(value, str):
            raise ReportServiceError("invalid_request")


def execute_user_plaza(context: ReportContext, request: dict) -> dict:
    """施策の広場の利用者側（設計 3.4.0 §4）。**credential が account と project を決める。**

    - 読む側は credential が許した account（とその project）だけ。他の持ち主の project
      範囲の書き込みは、無い id と同じ `plaza_not_found`。open は参加した持ち主どうし。
    - 置く・返す・更新するは、書く口と同じ門（再認証・停止・project の一致）を通す。
      誰が書いたかは credential の `actor`（要求の欄からは作らない）。SNS には何も出ない
      ので、書く口（writes）の credential でなくてもよい（報告の口と同じ）。
    """
    from . import admin_log, plaza, server_writes
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if context.scope != 'user':
        raise ReportServiceError("unsupported_operation")
    operation = request.get("operation")
    if operation not in PLAZA_OPERATIONS:
        raise ReportServiceError("unsupported_operation")
    _plaza_request(request, operation)
    viewer = plaza.Viewer(context.allowed_accounts)
    active = {name: context.allowed_accounts[name] for name in _active_names(context)}
    try:
        if operation == 'plaza_list':
            project = request.get('project')
            if project is not None:
                if project not in viewer.projects:
                    raise ReportServiceError("scope_unavailable")
                viewer = plaza.Viewer({name: value for name, value in context.allowed_accounts.items()
                                       if value == project})
            return plaza.list_posts(viewer, open_only=bool(request.get('open')))
        if operation == 'plaza_show':
            return plaza.show(request['plaza_id'], viewer)
        if request.get('visibility') == 'open':
            # 他の持ち主に見せる切り替えは人が CLI の二段確認で行う（MCP は project まで）。
            raise ReportServiceError("open_requires_cli")
        account = request['account']
        cfg = server_writes.current(context, account)
        if not context.actor:
            raise ReportServiceError("by_required")
        admin_log.register_account_secrets(cfg, via='mcp')
        with leave_gate.read_leases(set(active)):
            if operation == 'plaza_post':
                return plaza.post(
                    account, kind=request['kind'], title=request['title'], body=request['body'],
                    by=context.actor, scope_note=request.get('scope'),
                    kind_detail=request.get('kind_detail'), how=request.get('how'),
                    evidence_level=request.get('evidence_level') or 'stated',
                    declarations=request.get('declarations') or [],
                    hypothesis=request.get('hypothesis'), change=request.get('change'),
                    until=request.get('until'),
                    min_n=request['min_n'] if request.get('min_n') is not None else 5,
                    visibility='project', via='mcp',
                    project=context.allowed_accounts[account], medium=cfg.get('media'),
                    trusted_accounts=active)
            if operation == 'plaza_reply':
                return plaza.reply(request['plaza_id'], account=account, kind=request['kind'],
                                   text=request.get('text'), measure_id=request.get('measure_id'),
                                   result=request.get('result'), by=context.actor, viewer=viewer,
                                   via='mcp', trusted_accounts=active)
            return plaza.update(request['plaza_id'], account=account, by=context.actor,
                                viewer=viewer, refresh=bool(request.get('refresh')),
                                verdict=request.get('verdict'), reason=request.get('reason'),
                                visibility=request.get('visibility'), via='mcp',
                                trusted_accounts=active)
    except plaza.PlazaError as error:
        raise _plaza_error(error) from None
    except accounts.AccountLeaving:
        raise ReportServiceError('account_leaving') from None
    except accounts.AccountError:
        raise ReportServiceError("scope_unavailable") from None


ADMIN_PLAZA_OPERATIONS = frozenset(('admin_plaza_list', 'admin_plaza_show', 'admin_plaza_join',
                                    'admin_plaza_leave', 'admin_plaza_hide'))


def execute_admin_plaza(context: ReportContext, request: dict) -> dict:
    """施策の広場の管理者側（設計 3.4.0 §4）。全部を読み、参加・退出・非表示を書く。

    書く操作は予算・監視語と同じ門（credential の読み直し）を通し、`by` は要求に
    明示させる（管理者 credential の中に人の名前は無い）。
    """
    from . import plaza
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    if context.scope != 'admin':
        raise ReportServiceError("unsupported_operation")
    operation = request.get("operation")
    shapes = {'admin_plaza_list': (set(), set()),
              'admin_plaza_show': ({'plaza_id'}, {'plaza_id'}),
              'admin_plaza_join': ({'project', 'by'}, {'project', 'by'}),
              'admin_plaza_leave': ({'project', 'by'}, {'project', 'by'}),
              'admin_plaza_hide': ({'plaza_id', 'reason', 'by'}, {'plaza_id', 'reason', 'by'})}
    if operation not in shapes:
        raise ReportServiceError("unsupported_operation")
    allowed, required = shapes[operation]
    if set(request) - allowed - {'operation'} or not required <= set(request):
        raise ReportServiceError("invalid_request")
    if any(not isinstance(request[key], str) for key in allowed & set(request)):
        raise ReportServiceError("invalid_request")
    try:
        if operation == 'admin_plaza_list':
            return plaza.admin_list()
        if operation == 'admin_plaza_show':
            return plaza.show(request['plaza_id'], plaza.Viewer(admin=True))
        _credential_unchanged(context)
        if operation == 'admin_plaza_hide':
            return plaza.hide(request['plaza_id'], by=request['by'], reason=request['reason'],
                              via='mcp')
        return plaza.set_membership(request['project'], joined=operation == 'admin_plaza_join',
                                    by=request['by'], via='mcp')
    except plaza.PlazaError as error:
        raise _plaza_error(error) from None


def _open_directory_nofollow(path: Path) -> int:
    """Open every absolute repo ancestor without following a symlink."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _mcp_study(context: ReportContext, request: dict) -> dict:
    """Open an allowed repo file once, then calculate from those same bytes."""
    from . import study_report
    if set(request) - {"operation", "file", "min_n"}:
        raise ReportServiceError("invalid_request")
    file_arg = request.get("file")
    if not isinstance(file_arg, str) or not file_arg.strip():
        raise ReportServiceError("invalid_options")
    min_n = request.get("min_n", 5)
    if type(min_n) is not int or min_n < 1:
        raise ReportServiceError("invalid_options")
    now = jst.now_jst()
    path = Path(os.path.abspath(file_arg))
    try:
        with leave_gate.read_leases(_active_names(context)):
            repos = {}
            for name in _active_names(context):
                cfg = accounts.load_account(name)
                repo = accounts.resolved_repo_dir(cfg)
                if repo:
                    repos[name] = Path(repo)
            # The lexical path must be below an allowed repo. Each component is
            # then opened relative to an fd with O_NOFOLLOW, including the leaf.
            matches = [(name, repo, path.relative_to(repo)) for name, repo in repos.items()
                       if path.is_relative_to(repo)]
            if not matches:
                # A known, excluded account may still be draining. Inspect only
                # its trusted registry metadata, never the requested file here.
                from .report_isolation import read_registry_ledger
                candidates=set(context.excluded_accounts)|{
                    name for name in context.allowed_accounts if leave_gate.stopped(name)}
                for name in candidates:
                    try:
                        raw=read_registry_ledger(Path(accounts.accounts_dir())/(name+'.json'))
                        repo=accounts.resolved_repo_dir({'repo_dir':accounts._expand((raw or {}).get('repo_dir'))})
                    except (OSError,ValueError,TypeError):continue
                    if repo and path.is_relative_to(Path(repo)):
                        leave_gate.check_busy(name)
                raise ReportServiceError("scope_unavailable")
            declaration = None
            for _name, repo, relative in matches:
                if not relative.parts:
                    continue
                descriptor = _open_directory_nofollow(repo)
                try:
                    for component in relative.parts[:-1]:
                        next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                                  dir_fd=descriptor)
                        os.close(descriptor)
                        descriptor = next_descriptor
                    leaf = os.open(relative.parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                                   dir_fd=descriptor)
                    with os.fdopen(leaf, "rb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ReportServiceError("invalid_options")
                        data = stream.read(study_report.MAX_BYTES + 1)
                finally:
                    os.close(descriptor)
                declaration = study_report.parse_declaration_bytes(data, now)
                if declaration["account"] not in _active_names(context):
                    raise ReportServiceError("scope_unavailable")
                declared_repo = repos.get(declaration["account"])
                if declared_repo is not None and path.is_relative_to(declared_repo):
                    break
                declaration = None
            if declaration is None:
                raise ReportServiceError("scope_unavailable")
            return study_report.answer(None, min_n=min_n, now=now,
                                       verified_declaration=declaration,
                                       allowed_names=_active_names(context))
    except ReportServiceError:
        raise
    except accounts.AccountLeaving:
        raise ReportServiceError('account_leaving') from None
    except (accounts.AccountError, study_report.StudyError, OSError, ValueError, TypeError,
            KeyError, UnicodeError, RecursionError):
        raise ReportServiceError("report_unavailable") from None


def render_markdown(payload: dict) -> str:
    """Render the very same batch payload, without recomputation or I/O."""
    safe = analytics_report._markdown_text
    lines = ["# Scoped report batch", "", f"Operation: {safe(payload['operation'])}",
             f"Generated: {safe(payload['generated_at'])}", "",
             "Allowed account reports:", ""]
    lines.extend("- " + safe(account) for account in payload["reports"])
    lines += ["", *["- " + safe(value) for value in payload["limitations"]],
              "", "## Evidence and structured data", ""]
    lines += ["    " + line for line in json.dumps(payload, ensure_ascii=False, indent=2,
                                                   allow_nan=False).splitlines()]
    return "\n".join(lines) + "\n"


def _credential_unchanged(context):
    """Re-read the credential file: the same check the budget write makes."""
    from .report_http import load_credentials
    from datetime import datetime,timezone
    if not context.credentials_path or not context.credential_digest:raise ReportServiceError('invalid_context')
    root,credentials=load_credentials(Path(context.credentials_path))
    if Path(root).resolve()!=Path(accounts.thth_root()).resolve():raise ReportServiceError('invalid_context')
    found=next((item[3] for item in credentials if item[0]==context.credential_digest and not item[2] and datetime.now(timezone.utc)<item[1]),None)
    if found!=context:raise ReportServiceError('credential_changed')


def _admin_watch_set(context,request):
    """監視語の入れ替え（設計 3.1.0 §3）。**語は管理者が入れる**——scope も
    credential の範囲も予算の書き込みと同じ門を通す。"""
    if (set(request)-{'operation','account','words','by'}
        or not {'account','words','by'}<=set(request)):raise ReportServiceError('invalid_request')
    if not isinstance(request['account'],str) or not isinstance(request['by'],str):
        raise ReportServiceError('invalid_request')
    if not isinstance(request['words'],list) or not all(isinstance(word,str) for word in request['words']):
        raise ReportServiceError('invalid_request')
    if request['account'] not in context.allowed_accounts:raise ReportServiceError('scope_unavailable')
    from . import admin_log,watch_cli
    try:
        _credential_unchanged(context)
        return watch_cli.set_words(request['account'],request['words'],by=request['by'],via='mcp')
    except ReportServiceError:raise
    except admin_log.AdminLogError:raise ReportServiceError('watch_change_refused') from None
    except watch_cli.WatchError:raise ReportServiceError('invalid_options') from None
    except (accounts.AccountError,OSError,ValueError,TypeError):raise ReportServiceError('invalid_options') from None


def execute_admin_write(context,request):
    """Explicit MCP-only admin mutation; execute_report never dispatches here."""
    if type(context) is not ReportContext or context.scope!='admin':raise ReportServiceError('unsupported_operation')
    if type(request) is not dict:raise ReportServiceError('invalid_request')
    if request.get('operation')=='admin_watch_set':return _admin_watch_set(context,request)
    if (request.get('operation')!='admin_budget_set'
        or set(request)-{'operation','monthly','currency','rate','rate_source','by','kind'}
        or not {'monthly','by'}<=set(request)):raise ReportServiceError('invalid_request')
    # `kind` は口の選択（既定は 2.12 の読取予算）。**本数の口は金額の選択肢を
    # 受けない**——USD の上限と本数の上限を 1 つの要求で混ぜない。
    kind=request.get('kind','x_read')
    if kind not in ('x_read','x_posts'):raise ReportServiceError('invalid_request')
    if kind=='x_posts' and set(request)-{'operation','monthly','by','kind'}:
        raise ReportServiceError('invalid_request')
    from . import budget_x,admin_log
    def current():
        from .report_http import load_credentials
        from datetime import datetime,timezone
        if not context.credentials_path or not context.credential_digest:raise ReportServiceError('invalid_context')
        root,credentials=load_credentials(Path(context.credentials_path))
        if Path(root).resolve()!=Path(accounts.thth_root()).resolve():raise ReportServiceError('invalid_context')
        found=next((item[3] for item in credentials if item[0]==context.credential_digest and not item[2] and datetime.now(timezone.utc)<item[1]),None)
        if found!=context:raise ReportServiceError('credential_changed')
    try:
        current()
        if kind=='x_posts':
            from . import budget_x_posts
            return budget_x_posts.configure(request['monthly'],by=request['by'],via='mcp',before_save=current)
        return budget_x.configure(**{k:v for k,v in request.items() if k not in ('operation','kind')},via='mcp',before_save=current)
    except ReportServiceError:raise
    except admin_log.AdminLogError as exc:
        raise ReportServiceError('budget_change_durability_unconfirmed' if exc.complete else 'budget_change_partially_recorded' if exc.appended else 'budget_change_refused') from None
    except (OSError,ValueError,TypeError):raise ReportServiceError('invalid_options') from None
