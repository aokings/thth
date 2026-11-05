"""Fail-closed configuration preflight, not an OS sandbox.

Trusted hosts still need distinct OS identities/mounts. This rejects accidental
cross-root paths and symlinks; it cannot prevent changes after validation.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import stat
from collections.abc import Mapping

from . import accounts


class IsolationError(ValueError):
    """Bounded diagnostic that never includes paths or account values."""


def registry_project(value):
    project = value.get('project') if isinstance(value, dict) else None
    return project if isinstance(project, str) and project.strip() else None


def read_registry_ledger(path):
    """A safe regular ledger can be unreadable without making it an unsafe tree."""
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise IsolationError('invalid_registry')
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise IsolationError('invalid_registry')
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            return None
        return value if isinstance(value, dict) else None
    finally:
        os.close(directory)


def validate_environment(root: str, allowed_accounts: Mapping, *, allow_unreadable=False, allow_empty=False) -> None:
    """Validate dedicated root and configured scope without reading secrets.

    Root must be explicit, have no group/other permissions, and be owned by the running identity.
    Reject symlinks/special files at root and in report-readable trees and ancestors.
    All configured account resource paths must remain beneath that root.
    Filesystem mutation races require host-enforced isolation, not this check.
    """
    try:
        _validate(root, allowed_accounts, allow_unreadable=allow_unreadable, allow_empty=allow_empty)
    except IsolationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError,
            accounts.AccountError, RuntimeError):
        raise IsolationError("invalid_report_environment") from None


def _validate(root, allowed, *, allow_unreadable=False, allow_empty=False):
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
    if (not allowed and not allow_empty) or any(not accounts.name_is_safe(name) for name in allowed):
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
        # Canonicalize only the trusted root prefix (e.g. macOS /var -> /private/var).
        # Never resolve the suffix: in-root symlinks must remain visible to lstat.
        lexical_root = Path(os.path.abspath(root))
        if path.is_relative_to(lexical_root):
            path = resolved / path.relative_to(lexical_root)
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
                    # A child listed then removed (e.g. a ledger's temp file after os.replace)
                    # cannot be read through this tree; skip it instead of failing the preflight.
                    try:
                        inspect(Path(directory) / name)
                    except FileNotFoundError:
                        continue

    scan(account_dir)  # Account definitions must be safe before load_account opens them.
    scan(resolved / "state" / "_leave")
    scan(resolved / "state" / "_admin")  # Shared administrator state is report-readable too.
    # 報告の口（設計 3.1.2）の置き場も共有の読み場。symlink・特殊ファイルを同じ門で断る。
    scan(resolved / "state" / "_reports")
    # 広告の口座の台帳（設計 広告の資格と遠くの道 案 B）。行の名前が `adaccounts/` にあれば、その台帳も
    # 同じ門で見る。置き場は行に広告の名前が無くても歩く（symlink・特殊ファイルを今より多く断る側）。
    ad_dir = resolved / "adaccounts"
    if ad_dir.exists() or ad_dir.is_symlink():
        scan(ad_dir)

    def inside(path):
        if not isinstance(path, str) or not os.path.isabs(path):
            raise IsolationError("resource_outside_root")
        if not Path(path).resolve().is_relative_to(resolved):
            raise IsolationError("resource_outside_root")

    for name, project in allowed.items():
        if not isinstance(project, (str, type(None))):
            raise IsolationError("invalid_report_scope")
        try:
            kind = accounts.ledger_kind(name)
        except accounts.AccountError:
            raise IsolationError(accounts.AMBIGUOUS) from None
        if kind == accounts.ADACCOUNT_KIND:
            _validate_adaccount(name, project, ad_dir, scan, inside, allow_unreadable=allow_unreadable)
            continue
        # Even a corrupt ledger has a report-readable state subtree.
        inside(accounts.state_dir_for(name))
        scan(accounts.state_dir_for(name))
        if allow_unreadable:
            try:raw = read_registry_ledger(account_dir / (name + '.json'))
            except FileNotFoundError:
                from . import leave_gate
                if leave_gate.stopped(name):continue
                raise
            if raw is None:
                continue
            try:
                cfg = accounts.load_account(name)
            except (accounts.AccountError, TypeError, ValueError, KeyError):
                # Partial dictionaries still declare resource paths. Validate them
                # before allowing the admin row to describe missing settings.
                cfg = dict(raw)
                for key in ('repo_dir', 'env', 'token'):
                    cfg[key] = accounts._expand(raw.get(key))
        else:
            cfg = accounts.load_account(name)
        configured_project = registry_project(cfg) if allow_unreadable else cfg.get("project")
        if cfg.get("account", name) != name or configured_project != project:
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


def _validate_adaccount(name, project, ad_dir, scan, inside, *, allow_unreadable=False):
    """広告の口座の 1 行（`adaccounts/<名前>.json`）。口座の行と同じ門: 置き場・台帳の形・project・資源が root の下。"""
    from .ads import ledger as ads_ledger
    if Path(ads_ledger.adaccounts_dir()).resolve() != ad_dir:
        raise IsolationError("account_registry_mismatch")
    # 遠くの道の依頼の印は口座の種類に依らず `state/<名前>/requests/`（設計 §3.4）。広告の状態は `adaccount_state/`。
    for path in (accounts.state_dir_for(name), ads_ledger.state_dir_for(name)):
        inside(path)
        scan(path)
    raw = read_registry_ledger(ad_dir / (name + ".json"))  # regular・nlink 1・O_NOFOLLOW
    if allow_unreadable:
        if raw is None:
            return
        cfg = dict(raw, token=accounts._expand(raw.get("token")))
    else:
        cfg = ads_ledger.load(name)
    if cfg.get("adaccount") != name or registry_project(cfg) != project:
        raise IsolationError("account_scope_mismatch")
    if cfg.get("token"):
        inside(cfg["token"])


def _walk_error(_):
    raise IsolationError("unreadable_report_tree")
