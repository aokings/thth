"""記事別トピック提案の保存（設計 §8・§9・§13 P1）。

**中身から決まる ID で、一度書いたら上書きしない。** 同じ入力を二度送っても
同じ ID になるので、重複しない（冪等）。判断・記事・観測・profile 履歴は
作成後に書き換えない。active profile だけは、履歴を残したうえで更新する。

**壊れた記録を「観測なし」と偽らない**（設計 §8・受け入れ T14）。読めなかった
ファイルは**壊れたものとして名前を返す**。空だったのか読めなかったのかを、
呼び出し側が区別できるようにする。

**このロックと、投稿の clone/account ロックを同時に持たない**（設計 §8）。
提案の保存が失敗しても、投稿のロックや inflight には触らない。
"""
from __future__ import annotations

import json
import os
import re

from . import accounts as accounts_mod
from . import lock as lock_mod
from . import topic_models as models

KINDS = ("articles", "observations", "decisions", "proposals")
_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,200}$")


class StoreError(Exception):
    """保存・読取ができない。**推測で続けない。**"""


def root() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", "topic_advice")


def _kind_dir(kind: str) -> str:
    if kind not in KINDS:
        raise StoreError(f"知らない種類です: {kind!r}")
    return os.path.join(root(), kind)


def _id_filename(record_id: str) -> str:
    """ID をファイル名にする。**任意のパスを連結させない**（設計 §8）。"""
    if not _ID_RE.match(record_id or ""):
        raise StoreError(f"ID の形が違います: {record_id!r}")
    return record_id.replace("sha256:", "") + ".json"


def _account_filename(account: str) -> str:
    if not _SEGMENT_RE.match(account or ""):
        raise StoreError(f"account の形が違います: {account!r}")
    return account + ".json"


def _atomic_write(path: str, payload: dict) -> None:
    """固有の一時ファイル → flush/fsync → rename（設計 §8）。

    **同名固定の `.tmp` を使わない**——複数プロセスが同時に書くと壊れる。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _store_lock():
    os.makedirs(root(), exist_ok=True)
    return lock_mod.AccountLock(os.path.join(root(), ".store.lock"))


def put(kind: str, record: dict, *, id_key: str) -> tuple:
    """記録を 1 つ保存する。`(記録, 新しく書いたか)`。

    **同じ ID が既にあれば書かない**（冪等・受け入れ T13）。中身から ID が決まる
    ので、同じ入力の再送は同じファイルになる。
    """
    record_id = record.get(id_key)
    path = os.path.join(_kind_dir(kind), _id_filename(record_id))
    lock = _store_lock()
    try:
        lock.acquire()
    except lock_mod.LockBusy as e:
        raise StoreError("ほかの実行が保存中です。少し待ってからやり直してください") from e
    try:
        if os.path.exists(path):
            return read_json(path), False
        _atomic_write(path, record)
        return record, True
    finally:
        lock.release()


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get(kind: str, record_id: str) -> dict | None:
    path = os.path.join(_kind_dir(kind), _id_filename(record_id))
    if not os.path.exists(path):
        return None
    return read_json(path)


def load_all(kind: str) -> tuple:
    """`(読めた記録, 壊れていた ID)`（設計 §8・受け入れ T14）。

    **壊れたファイルを「無い」ことにしない。** 読めなかったものは ID を返す。
    呼び出し側は「観測が無い」と「観測が読めない」を区別できる。
    """
    directory = _kind_dir(kind)
    rows, broken = [], []
    if not os.path.isdir(directory):
        return rows, broken
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        path = os.path.join(directory, name)
        try:
            rows.append(read_json(path))
        except (OSError, ValueError):
            broken.append("sha256:" + name[:-len(".json")])
    return rows, broken


# --- profile（active は履歴を残して更新する・設計 §8・§10） ------------------

def profile_path(account: str) -> str:
    return os.path.join(root(), "profiles", _account_filename(account))


def get_profile(account: str) -> dict | None:
    path = profile_path(account)
    if not os.path.exists(path):
        return None
    return read_json(path)


def set_profile(profile: dict) -> dict:
    """active profile を更新する。**旧版は履歴に残す**（設計 §8）。"""
    account = profile["account"]
    lock = _store_lock()
    try:
        lock.acquire()
    except lock_mod.LockBusy as e:
        raise StoreError("ほかの実行が保存中です。少し待ってからやり直してください") from e
    try:
        old = get_profile(account)
        if old:
            _atomic_write(os.path.join(root(), "profile_history",
                                        _id_filename(old["profile_version"])), old)
        _atomic_write(profile_path(account), profile)
        return profile
    finally:
        lock.release()


# --- 旧記録の読み取り（設計 §9） --------------------------------------------

def legacy_observations() -> list:
    """既存 `state/topics.json` を**参考証拠として読む**（設計 §9）。

    **元データは変更しない。読むだけ。** 当時の判断は
    `provenance: legacy` として残し、**成功の実証としては扱わない**
    （旧記録だけで `recommended` に昇格しない）。

    **`by` から account を推測して埋めない**（masaru 指示 2026-09-11）。
    """
    from . import topics as topics_mod

    out = []
    for row in topics_mod.load()["checks"]:
        out.append({
            "topic": row["topic"],
            "normalized_topic": row["topic"],
            "provider": "legacy_note",
            "provenance": "legacy",
            "retrieved_at": row.get("checked_at"),
            "submitted_by": row.get("by"),
            "status": row.get("status"),          # 無ければ None のまま（推測しない）
            "audience": row.get("audience") or None,
            "kind": row.get("kind"),
            "account": row.get("account"),        # 46 件はすべて None
            "legacy_verdict": row.get("verdict"),
            "samples": [],                        # 旧記録は投稿例を持たない
            "coverage": None,
        })
    return out
