"""Fail-closed configuration preflight, not an OS sandbox.

Trusted hosts still need distinct OS identities/mounts. This rejects accidental
cross-root paths and symlinks; it cannot prevent changes after validation.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
from collections.abc import Mapping

from . import accounts


class IsolationError(ValueError):
    """Bounded diagnostic that never includes paths or account values."""


def validate_environment(root: str, allowed_accounts: Mapping) -> None:
    """Validate dedicated root and configured scope without reading secrets.

    Root must be explicit, private (0700), and owned by the running identity.
    Reject every symlink/special file in the root, including dangling symlinks.
    All configured account resource paths must remain beneath that root.
    Filesystem mutation races require host-enforced isolation, not this check.
    """
    try:
        _validate(root, allowed_accounts)
    except IsolationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError,
            accounts.AccountError, RuntimeError):
        raise IsolationError("invalid_report_environment") from None


def _validate(root, allowed):
    if not isinstance(root, str) or not os.path.isabs(root) or not isinstance(allowed, Mapping):
        raise IsolationError("invalid_report_environment")
    base = Path(root)
    resolved = base.resolve(strict=True)
    info = base.stat()
    if (not base.is_dir() or base.is_symlink()
            or info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise IsolationError("private_root_required")
    if not os.environ.get("THTH_ROOT") or Path(accounts.thth_root()).resolve() != resolved:
        raise IsolationError("root_mismatch")
    account_dir = resolved / "accounts"
    if not account_dir.is_dir() or Path(accounts.accounts_dir_info()["path"]).resolve() != account_dir:
        raise IsolationError("account_registry_mismatch")

    # Check names before constructing paths or opening account definitions.
    if not allowed or any(not accounts.name_is_safe(name) for name in allowed):
        raise IsolationError("invalid_report_scope")

    # No content is read here. Bound traversal so a bad deployment fails closed.
    count = 0
    for directory, dirs, files in os.walk(resolved, followlinks=False, onerror=_walk_error):
        for name in dirs + files:
            count += 1
            if count > 100_000:
                raise IsolationError("report_tree_too_large")
            entry = Path(directory) / name
            entry_stat = entry.lstat()
            if not (stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISREG(entry_stat.st_mode)):
                raise IsolationError("unsafe_report_tree")
            # Hard links could expose a file outside the dedicated tree.
            if stat.S_ISREG(entry_stat.st_mode) and entry_stat.st_nlink > 1:
                raise IsolationError("unsafe_report_tree")

    def inside(path):
        if not isinstance(path, str) or not os.path.isabs(path):
            raise IsolationError("resource_outside_root")
        if not Path(path).resolve().is_relative_to(resolved):
            raise IsolationError("resource_outside_root")

    for name, project in allowed.items():
        if not isinstance(project, (str, type(None))):
            raise IsolationError("invalid_report_scope")
        cfg = accounts.load_account(name)
        if cfg.get("account") != name or cfg.get("project") != project:
            raise IsolationError("account_scope_mismatch")
        repo = cfg.get("repo_dir")
        if repo:
            inside(repo)
            for field in ("queue_dir", "replies_dir"):
                value = cfg.get(field)
                if value:
                    inside(os.path.join(repo, value))
        for field in ("env", "token"):
            if cfg.get(field):
                inside(cfg[field])
        inside(accounts.state_dir_for(name))
        for path in accounts.data_dirs(cfg, name).values():
            inside(path)


def _walk_error(_):
    raise IsolationError("unreadable_report_tree")
