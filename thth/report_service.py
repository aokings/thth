"""Local scoped facade. This is not authentication or filesystem isolation.

A trusted host constructs ReportContext after authenticating its caller, within
an already isolated execution environment. Never derive it from report input.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType

from . import accounts, after_cli, analytics_report, jst, operations_handoff, replies


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
        with replies.report_scope(context.allowed_accounts):
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
        names = [scope_value] if scope_value in context.allowed_accounts else []
    else:
        names = sorted(name for name, project in context.allowed_accounts.items() if project == scope_value)
    if not names:
        # Same error for missing and existing-but-unpermitted resources.
        raise ReportServiceError("scope_unavailable")
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
        if operation == "analytics_report":
            return analytics_report.answer(account, project=project, now=now,
                                           trusted_names=tuple(names),
                                           allowed_names=tuple(context.allowed_accounts), **kwargs)
        return operations_handoff.answer(account, project=project, now=now,
                                         trusted_names=tuple(names),
                                         allowed_names=tuple(context.allowed_accounts), **kwargs)
    except (accounts.AccountError, after_cli.AfterError, operations_handoff.HandoffError,
            OSError, ValueError, TypeError, KeyError, OverflowError):
        raise ReportServiceError("report_unavailable") from None


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
        repos = {}
        for name in context.allowed_accounts:
            cfg = accounts.load_account(name)
            repo = accounts.resolved_repo_dir(cfg)
            if repo:
                repos[name] = Path(repo)
        # The lexical path must be below an allowed repo. Each component is
        # then opened relative to an fd with O_NOFOLLOW, including the leaf.
        matches = [(name, repo, path.relative_to(repo)) for name, repo in repos.items()
                   if path.is_relative_to(repo)]
        if not matches:
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
            if declaration["account"] not in context.allowed_accounts:
                raise ReportServiceError("scope_unavailable")
            declared_repo = repos.get(declaration["account"])
            if declared_repo is not None and path.is_relative_to(declared_repo):
                break
            declaration = None
        if declaration is None:
            raise ReportServiceError("scope_unavailable")
        return study_report.answer(None, min_n=min_n, now=now,
                                   verified_declaration=declaration,
                                   allowed_names=tuple(context.allowed_accounts))
    except ReportServiceError:
        raise
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
