"""運営者の CLI（`thth admin draft put`）だけの診断（設計 3.12.0 §6-5）。

書き込みの口（`server_writes.execute`）は、置き場の検査で断ったときも外へは
`write_unavailable` とだけ言う。MCP やサーバの口ではそれが正しい（パスや置き場の
形を外へ出さない）。ところが**運営者が手で打った命令**でも同じ 1 語しか出ず、
`THTH_ROOT` を付け忘れた（`root_mismatch`）のか、state の親のモードが緩い
（`unsafe_server_directory`）のかが分からなかった（09-25 夜）。

ここは `write_unavailable` が返ったあとに、運営者の CLI だけが呼ぶ。同じ検査を
もう一度なぞって、理由を 1 語と次の一手にする。書き込みの筋には手を入れない。
秘密は読まない（資格情報の置き場の `root` だけを見る）。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from . import accounts

# 置き場の検査の理由ごとの次の一手。`{root}` は資格情報に書かれた置き場。
ISOLATION_HINTS = {
    "root_mismatch": "THTH_ROOT={root} を付けて打ってください",
    "private_root_required": "{root} のモードを 700 にし、持ち主を今の利用者にしてください",
    "account_registry_mismatch": "THTH_ROOT={root} を付けて、{root}/accounts の台帳を読む形で打ってください",
}
ISOLATION_FALLBACK = "置き場の検査で断りました（thth doctor で {root} の中を確かめてください）"


def _isolation(context):
    """資格情報の置き場の検査をなぞる。断る理由があれば (理由, 次の一手)。"""
    path = getattr(context, "credentials_path", None)
    if not path:
        return None
    from .report_http import load_credentials
    from .report_isolation import IsolationError, validate_environment
    try:
        root, _ = load_credentials(Path(path))
    except Exception:
        return None
    try:
        validate_environment(root, context.allowed_accounts)
    except IsolationError as exc:
        reason = str(exc)
        return reason, ISOLATION_HINTS.get(reason, ISOLATION_FALLBACK).format(root=root)
    except Exception:
        return None
    return None


def _candidates(account):
    """書き込みの口が `server_files.directory` で開く置き場（private か）。"""
    from . import server_writes
    rows = []
    try:
        cfg = accounts.load_account(account)
    except Exception:
        return rows
    try:
        rows.append((Path(server_writes._queue(cfg)[1]), False))
    except Exception:
        pass
    repo = cfg.get("repo_dir")
    if isinstance(repo, str):
        rows.append((Path(accounts.repo_lock_path_for(repo)).parent, False))
    rows.append((Path(accounts.account_lock_path_for(account)).parent, False))
    return rows


def _loose(root, path, private):
    """root から path まで、`server_files.directory` と同じ見方で最初に断る段。"""
    try:
        parts = path.absolute().relative_to(root).parts
    except ValueError:
        return None
    current = root
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            return None  # まだ無い段は作るときに 700 になる。
        if not stat.S_ISDIR(info.st_mode):
            return None
        relative = str(current.relative_to(root))
        if info.st_uid != os.getuid():
            return "unsafe_server_owner", f"{relative} の持ち主を今の利用者にしてください（THTH_ROOT の下）"
        last = index == len(parts) - 1
        if info.st_mode & 0o022 or (last and private and info.st_mode & 0o077):
            return "unsafe_server_directory", f"{relative} のモードを 700 にしてください（THTH_ROOT の下・chmod 700）"
    return None


def diagnose(context, account):
    """`write_unavailable` の中身を運営者向けに 1 語と次の一手で返す。分からなければ None。"""
    found = _isolation(context)
    if found:
        return found
    try:
        root = Path(accounts.thth_root()).resolve()
        for path, private in _candidates(account):
            found = _loose(root, path, private)
            if found:
                return found
    except Exception:
        return None
    return None


def explain(exc, context, account):
    """運営者の CLI の口: `write_unavailable` なら理由を差し替えた例外を返す（他はそのまま）。"""
    from .report_service import ReportServiceError
    if not isinstance(exc, ReportServiceError) or str(exc) != "write_unavailable":
        return exc
    found = diagnose(context, account)
    if not found:
        return exc
    reason, hint = found
    better = ReportServiceError(reason)
    better.reason = hint
    return better
