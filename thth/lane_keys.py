"""レーンの鍵の表（括りの段 2・設計 叩き台 §4.3・§8 R1）。

置き場は括りごとの `ws/<括り>/private/lane-keys.json`（main は根の `private/lane-keys.json`）・0600。
**資格のファイル（`private/report-credentials.json`）には欄を足さない**（R1）: main の版の `load_credentials` は
行の欄を決まった名前に絞り、知らない欄が 1 つあるとファイルごと読めなくする。レーンの鍵をここに置けば、
VM を古い版に戻しても、新しい鍵だけが使えなくなり、今の鍵（審査の鍵も）は動く。

行の形: {sha256, expires_at, revoked, actor, workspace, lane, writes}。口座は**持たない**: 照合のたびに
その括りの口座（口座の台帳と広告の台帳）を引く。あとで括りに足した口座も、同じ鍵で動く。
どの括りの表も、読めなければその括りのレーンの鍵だけを使わない（ほかの括りと資格のファイルの鍵は止めない）。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from pathlib import Path

from . import accounts

SCHEMA = "thth.lane-keys/1"
NAME = "lane-keys.json"
FIELDS = frozenset(("sha256", "expires_at", "revoked", "actor", "workspace", "lane", "writes"))
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,80}\Z")
KEYS_MAX = 64   # 1 つの括りの表の行の上限（取り消した行も数える）


class LaneKeysError(ValueError):
    pass


def path_for(workspace) -> Path:
    return Path(accounts.workspace_dir(workspace)) / "private" / NAME


def _valid_row(row, workspace) -> bool:
    from .relay import PERSON
    from .report_http import _expiry
    if type(row) is not dict or set(row) != FIELDS:
        return False
    try:
        _expiry(row["expires_at"])
    except (ValueError, TypeError):
        return False
    return (isinstance(row["sha256"], str) and HEX64.fullmatch(row["sha256"]) is not None
            and type(row["revoked"]) is bool and type(row["writes"]) is bool
            and isinstance(row["actor"], str) and PERSON.fullmatch(row["actor"]) is not None
            and row["workspace"] == workspace
            and isinstance(row["lane"], str) and LANE.fullmatch(row["lane"]) is not None)


def read(workspace) -> list:
    """その括りの表の行（無ければ空）。形が違えば LaneKeysError。"""
    from .invites import _read_credentials
    path = path_for(workspace)
    try:
        raw = _read_credentials(path)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise LaneKeysError("lane_keys_unreadable") from exc
    try:
        data = json.loads(raw)
    except ValueError:
        raise LaneKeysError("lane_keys_unreadable") from None
    if type(data) is not dict or set(data) != {"schema", "keys"} or data["schema"] != SCHEMA \
            or type(data["keys"]) is not list or len(data["keys"]) > KEYS_MAX \
            or not all(_valid_row(row, workspace) for row in data["keys"]) \
            or len({row["sha256"] for row in data["keys"]}) != len(data["keys"]):
        raise LaneKeysError("lane_keys_unreadable")
    return data["keys"]


def workspace_accounts(workspace) -> dict:
    """{口座: project}（括りの中の口座と広告の口座・照合のときの姿）。"""
    from .ads import ledger as ads_ledger
    from .relay import PERSON
    try:
        scope = accounts.use_workspace(workspace)
    except accounts.AccountError:
        raise LaneKeysError("workspace_unavailable") from None
    out = {}
    with scope:
        for name in accounts.list_account_names():
            try:
                out[name] = accounts.load_account(name).get("project")
            except (accounts.AccountError, OSError, ValueError):
                continue
        for name in ads_ledger.list_names():
            try:
                out.setdefault(name, ads_ledger.load(name).get("project"))
            except (accounts.AccountError, OSError, ValueError):
                continue
    return {name: project for name, project in sorted(out.items())
            if PERSON.fullmatch(name) and (project is None or isinstance(project, str) and project.strip())}


def contexts(credentials_path, taken=()) -> list:
    """全部の括りのレーンの鍵を (digest, 期限, 取り消し, ReportContext) で（`load_credentials` の形）。

    `taken` は資格のファイルにある digest（同じ hash の行があればそちらを使い、ここの行は使わない）。
    """
    from . import leave_gate
    from .report_http import _expiry
    from .report_service import ReportContext
    out = []
    for workspace in accounts.list_workspace_dirs():
        try:
            rows = read(workspace)
            if not rows:
                continue
            names = workspace_accounts(workspace)
            with accounts.use_workspace(workspace):
                stopped = {name for name in names if leave_gate.stopped(name)}
        except (LaneKeysError, accounts.AccountError, OSError, ValueError):
            print("lane_keys_unreadable: " + workspace, file=sys.stderr)
            continue
        for row in rows:
            if row["sha256"] in taken:
                continue
            context = ReportContext({n: p for n, p in names.items() if n not in stopped}, scope="user",
                                    writes=row["writes"], actor=row["actor"], credential_digest=row["sha256"],
                                    credentials_path=str(Path(credentials_path).absolute()),
                                    excluded_accounts={n: names[n] for n in sorted(stopped)},
                                    workspace=workspace, lane=row["lane"])
            out.append((row["sha256"], _expiry(row["expires_at"]), row["revoked"], context))
    return out


@contextlib.contextmanager
def _locked(workspace):
    from .invites import _locked_credentials
    path = path_for(workspace)
    os.makedirs(path.parent, mode=0o700, exist_ok=True)
    with _locked_credentials(path):
        yield path


def change(workspace, fn) -> bool:
    """表を排他の中で読み、`fn(rows)` が True を返したら書き換える（一時ファイル → rename・0600）。"""
    from .invites import _replace_credentials
    with _locked(workspace) as path:
        rows = read(workspace)
        if not fn(rows):
            return False
        if len(rows) > KEYS_MAX:
            raise LaneKeysError("credentials_full")
        data = {"schema": SCHEMA, "keys": rows}
        if not all(_valid_row(row, workspace) for row in rows):
            raise LaneKeysError("invalid_action")
        _replace_credentials(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())
    return True


def revoke(workspace, person, lane) -> bool:
    """その持ち主・括り・レーンの生きている鍵を取り消す（行は残して revoked: true）。"""
    def fn(rows):
        live = [row for row in rows if row["actor"] == person and row["lane"] == lane and not row["revoked"]]
        for row in live:
            row["revoked"] = True
        return bool(live)
    return change(workspace, fn)


def revoke_digest(workspace, person, sha256):
    """その持ち主のその hash の鍵を取り消す（リモート MCP のコネクタの取り消し・`thth logout --lane`・段 K）。

    戻り値は取り消した行の (レーン, 新しく取り消したか)。行が無ければ None。もう取り消してあれば (レーン, False)
    （sync が落ちて同じ頼みが 2 度来ても同じ結果）。ほかの持ち主の行は見つからないのと同じ。
    """
    found = {}

    def fn(rows):
        for row in rows:
            if row["sha256"] == sha256 and row["actor"] == person:
                found["lane"], found["changed"] = row["lane"], not row["revoked"]
                row["revoked"] = True
                return found["changed"]
        return False
    change(workspace, fn)
    return (found["lane"], found["changed"]) if found else None
