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

    Root must be explicit, have no group/other permissions, and be owned by the running identity.
    Reject symlinks/special files at root and in report-readable trees and ancestors.
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

    # Inspect the root's immediate entries without descending unrelated repos.
    # Read locations and every existing ancestor are checked before loaders run.
    seen = set()

    def inspect(entry):
        if entry in seen:
            return
        seen.add(entry)
        if len(seen) > 100_000:
            raise IsolationError("report_tree_too_large")
        info = entry.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise IsolationError("unsafe_report_tree")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise IsolationError("unsafe_report_tree")

    for entry in resolved.iterdir():
        inspect(entry)

    def scan(path):
        # Lexical ancestry is important: resolve() alone would hide symlinks.
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(resolved):
            raise IsolationError("resource_outside_root")
        for ancestor in reversed((path, *path.parents)):
            if ancestor == resolved or not ancestor.is_relative_to(resolved):
                continue
            if ancestor.exists() or ancestor.is_symlink():
                inspect(ancestor)
        if path.is_dir():
            for directory, dirs, files in os.walk(path, followlinks=False, onerror=_walk_error):
                for name in dirs + files:
                    inspect(Path(directory) / name)

    scan(account_dir)  # Account definitions must be safe before load_account opens them.

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
                    scan(os.path.join(repo, value))
        for field in ("env", "token"):
            if cfg.get(field):
                inside(cfg[field])
        inside(accounts.state_dir_for(name))
        scan(accounts.state_dir_for(name))
        for path in accounts.data_dirs(cfg, name).values():
            inside(path)
            scan(path)


def _walk_error(_):
    raise IsolationError("unreadable_report_tree")
