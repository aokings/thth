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

# 種類ごとの ID の項目名。**読むたびに中身から計算しなおして照合する。**
ID_KEY = {"articles": "article_id", "observations": "observation_id",
          "decisions": "decision_id", "proposals": "proposal_id"}
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
        problem = verify(kind, record, filename_id=record_id)
        if problem:
            raise StoreError(f"保存しようとした記録が壊れています: {problem}")
        if os.path.exists(path):
            stored = read_json(path)
            # **同じ ID なら中身も同じはず。** 違えば、どちらかが書き換わって
            # いる（独立レビュー 2026-09-11・指摘 5）。黙って古いほうを返さない。
            if models.canonical_json(stored) != models.canonical_json(record):
                raise StoreError(
                    f"同じ ID で中身の違う記録が保存されています（{record_id}）。"
                    f"保存済みのファイルが書き換わっている可能性があります")
            return stored, False
        _atomic_write(path, record)
        return record, True
    finally:
        lock.release()


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verify_profile(profile: dict, *, account: str | None = None) -> str | None:
    """profile が自分の `profile_version` と合っているか（独立レビュー第 2 巡 P1-1）。

    **profile を例外にしない。** 観測・記事・候補比較・判断には読むたびの照合を
    入れたのに、**profile だけ `read_json` のままだった。** そのため
    `profile_version` を据え置いて `status` を `confirmed` に、`basis` を空に
    書き換えると、**未確定の方針に基づく判断が確定扱いで保存できた。**
    """
    claimed = profile.get("profile_version")
    if not isinstance(claimed, str) or not _ID_RE.match(claimed):
        return "profile_version がありません（または形が違います）"
    if account is not None and profile.get("account") != account:
        return (f"profile の account が違います"
                f"（{profile.get('account')!r} / {account!r}）")
    try:
        rebuilt = models.build_profile(
            {k: v for k, v in profile.items()
             if k not in ("profile_version", "schema_version")})
    except models.SchemaError as e:
        return f"profile が schema に合いません: {e}"
    if rebuilt["profile_version"] != claimed:
        return (f"中身が profile_version と合いません（保存後に書き換わって"
                f"います。{claimed[:19]}… のはずが "
                f"{rebuilt['profile_version'][:19]}…）")
    return None


def verify(kind: str, record: dict, *, filename_id: str | None = None) -> str | None:
    """記録が自分の ID と合っているか。合っていれば None、違えば理由。

    **「ID が変わっていない＝証拠が変わっていない」は、照合して初めて言える**
    （独立レビュー 2026-09-11・指摘 5）。以前は JSON として読めるかしか見て
    いなかったので、**ファイルを手で書き換えても同じ ID のまま通った**——
    `status: partial・投稿例 0 件` の観測を `ok・3 件` に差し替えて、
    元の判断を `recommended` として保存できた。

    内容アドレスは、**読むたびに計算しなおさなければ内容アドレスではない。**
    """
    id_key = ID_KEY.get(kind)
    if id_key is None:
        return None
    claimed = record.get(id_key)
    if not isinstance(claimed, str) or not _ID_RE.match(claimed):
        return f"{id_key} がありません（または形が違います）"
    if filename_id is not None and filename_id != claimed:
        return f"ファイル名と {id_key} が違います（{filename_id} / {claimed}）"
    actual = models.content_id(record, exclude=(id_key,))
    if actual != claimed:
        return (f"中身が {id_key} と合いません（保存後に書き換わっています。"
                 f"{claimed[:19]}… のはずが {actual[:19]}…）")
    return None


def get(kind: str, record_id: str) -> dict | None:
    path = os.path.join(_kind_dir(kind), _id_filename(record_id))
    if not os.path.exists(path):
        return None
    row = read_json(path)
    problem = verify(kind, row, filename_id=record_id)
    if problem:
        raise StoreError(f"{kind}/{record_id}: {problem}")
    return row


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
        record_id = "sha256:" + name[:-len(".json")]
        try:
            row = read_json(path)
        except (OSError, ValueError):
            broken.append(record_id)
            continue
        # **JSON として読めることは、壊れていないことではない**（指摘 5）。
        # 手で書き換えられた記録は「読めた」側に入れない。
        if verify(kind, row, filename_id=record_id):
            broken.append(record_id)
            continue
        rows.append(row)
    return rows, broken


# --- profile（active は履歴を残して更新する・設計 §8・§10） ------------------

def profile_path(account: str) -> str:
    return os.path.join(root(), "profiles", _account_filename(account))


def get_profile(account: str) -> dict | None:
    """active profile。**読むたびに中身と `profile_version` を照合する。**"""
    path = profile_path(account)
    if not os.path.exists(path):
        return None
    profile = read_json(path)
    problem = verify_profile(profile, account=account)
    if problem:
        raise StoreError(f"profiles/{account}: {problem}")
    return profile


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
