"""Local scoped facade. This is not authentication or filesystem isolation.

A trusted host constructs ReportContext after authenticating its caller, within
an already isolated execution environment. Never derive it from report input.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from types import MappingProxyType

from . import accounts, after_cli, analytics_report, jst, operations_handoff


class ReportServiceError(ValueError):
    """Bounded errors suitable for the caller; never include core exception text."""


@dataclass(frozen=True)
class ReportContext:
    """Host-trusted account -> project metadata, copied and immutable."""

    allowed_accounts: Mapping[str, str | None]
    scope: str = "user"

    def __post_init__(self):
        if self.scope not in ("user", "admin") or not isinstance(self.allowed_accounts, Mapping):
            raise ReportServiceError("invalid_context")
        allowed = dict(self.allowed_accounts)
        for account, project in allowed.items():
            if not isinstance(account, str) or not accounts.name_is_safe(account):
                raise ReportServiceError("invalid_context")
            if project is not None and (not isinstance(project, str) or not project.strip()):
                raise ReportServiceError("invalid_context")
        object.__setattr__(self, "allowed_accounts", MappingProxyType(allowed))


def execute_report(context: ReportContext, request: dict) -> dict:
    """Validate all scope/options before calling any core loader.

    A project expands ONLY through trusted context, not the machine's registry.
    No environment switching, arbitrary command, path or output artifact exists.
    """
    if type(context) is not ReportContext or type(request) is not dict:
        raise ReportServiceError("invalid_request")
    operation = request.get("operation")
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
        names = [scope_value] if scope_value in context.allowed_accounts else []
    else:
        names = sorted(name for name, project in context.allowed_accounts.items() if project == scope_value)
    if not names:
        # Same error for missing and existing-but-unpermitted resources.
        raise ReportServiceError("scope_unavailable")
    now = jst.now_jst()
    kwargs = {key: request[key] for key in options if key in request}
    reports = {}
    try:
        for name in names:
            if operation == "analytics_report":
                reports[name] = analytics_report.answer(name, now=now, **kwargs)
            else:
                reports[name] = operations_handoff.answer(name, now=now, **kwargs)
    except (accounts.AccountError, after_cli.AfterError, operations_handoff.HandoffError,
            OSError, ValueError, TypeError, KeyError, OverflowError):
        raise ReportServiceError("report_unavailable") from None
    return {"schema_version": 1, "report_type": "scoped_report_batch",
            "generated_at": jst.iso(now), "operation": operation,
            "scope": {scope_key: scope_value}, "reports": reports,
            "limitations": ["Host-trusted scope only; authentication and filesystem isolation are external",
                            "Projects contain only accounts allowed by this context",
                            "Underlying report freshness and coverage limits remain applicable"]}


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
