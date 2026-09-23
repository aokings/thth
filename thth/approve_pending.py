"""承認の確定待ち（設計 3.7.0 §B3・`state/<account>/approve_pending.json`）。

`thth approve` の 1 段目（digest を出すだけ）が済み、2 段目（`--confirm`）が残って
いる原稿を控える。**本文は残さない**——原稿の repo 内の相対パス・digest・1 段目の
時刻・原稿の中身の sha256 だけ。

消えるとき:

  - 確定（`--confirm` が通った）・取り消し（`thth revoke`）で消す。
  - **本文の変更**: 原稿の中身の sha256 が 1 段目のときと違えば、読み手はその控えを
    無いものとして扱う（`split()` が `not_requested` に数える）。1 段目を打ち直すと
    書き直される（そのとき合わない控えは掃除する）。

読み手（queue・board・observe）は `split()` で `not_approved`（draft のまま）を
`awaiting_confirm`（1 段目が済み確定が残る）と `not_requested`（1 段目もまだ）に
分ける。確定待ちの原稿の publish_at まで `CONFIRM_DUE_HOURS` 時間を切ったら、
observe の次の一手（`kind: confirm_due`）と運用通知（増えたときだけ）。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

from . import accounts, jst

FILE = "approve_pending.json"
VERSION = 1
CONFIRM_DUE_HOURS = 3
AWAITING = "awaiting_confirm"
NOT_REQUESTED = "not_requested"


def path_for(account_name: str) -> str:
    return os.path.join(accounts.state_dir_for(account_name), FILE)


def file_sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _valid_item(item) -> bool:
    return (isinstance(item, dict) and isinstance(item.get("file"), str)
            and isinstance(item.get("digest"), str) and isinstance(item.get("at"), str)
            and isinstance(item.get("file_sha256"), str))


def load(account_name: str) -> dict:
    """`{repo 内の相対パス: 控え}`（無い・読めない・形違いは空——控えは知らせのための
    もので、読めないことで何かを止めない）。"""
    try:
        with open(path_for(account_name), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    items = data.get("items")
    if not isinstance(items, list):
        return {}
    return {item["file"]: item for item in items if _valid_item(item)}


def _save(account_name: str, items: dict) -> None:
    path = path_for(account_name)
    if not items:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION,
                   "items": [items[key] for key in sorted(items)]},
                  f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp, path)


def rel_path(path: str, repo_dir: str) -> str:
    return os.path.relpath(os.path.realpath(path), os.path.realpath(repo_dir))


def record(account_name: str, repo_dir: str, prepared: list, *, bundle_digest: str,
           at: str | None = None) -> None:
    """1 段目で控える（`prepared` は `thth approve` の準備の list・本文は読まない）。

    同じ account の控えのうち、原稿が消えた・中身が変わったものはここで掃除する。
    """
    at = at or jst.iso()
    items = load(account_name)
    for key, item in list(items.items()):
        current = file_sha256(os.path.join(repo_dir, key))
        if current is None or current != item["file_sha256"]:
            items.pop(key)
    for one in prepared:
        key = rel_path(one["path"], repo_dir)
        sha = file_sha256(one["path"])
        if sha is None:
            continue
        items[key] = {"file": key, "digest": one["digest"], "bundle_digest": bundle_digest,
                      "at": at, "file_sha256": sha}
    _save(account_name, items)


def clear(account_name: str, repo_dir: str, paths) -> None:
    """確定・取り消しで消す。"""
    items = load(account_name)
    changed = False
    for path in paths:
        if items.pop(rel_path(path, repo_dir), None) is not None:
            changed = True
    if changed:
        _save(account_name, items)


def split(account_name: str, account_cfg: dict, files, now) -> dict:
    """`not_approved`（draft のまま・post_id なし）を確定待ちと未依頼に分ける（読むだけ）。

    戻り値 `{"awaiting_confirm": [...], "not_requested": [...]}`。確定待ちの行は
    `{file, rel, publish_at, digest, at, hours_to_publish, due}`（`due` は publish_at まで
    `CONFIRM_DUE_HOURS` 時間を切ったか——過ぎていても True）。本文は持たない。
    """
    from . import queuefile
    repo_dir = accounts.resolved_repo_dir(account_cfg) if account_cfg else ""
    items = load(account_name) if repo_dir else {}
    awaiting, not_requested = [], []
    for qf in files:
        if getattr(qf, "malformed", False):
            continue
        fm = qf.front_matter
        if fm.get("account") != account_name or fm.get("status") != "draft" or fm.get("post_id"):
            continue
        rel = rel_path(qf.path, repo_dir) if repo_dir else os.path.basename(qf.path)
        item = items.get(rel)
        raw = fm.get("publish_at")
        try:
            publish_at = queuefile.parse_publish_at(raw) if isinstance(raw, str) else None
        except ValueError:
            publish_at = None
        row = {"file": os.path.basename(qf.path), "rel": rel, "publish_at": raw}
        if item is None or file_sha256(qf.path) != item["file_sha256"]:
            not_requested.append(row)
            continue
        hours = (round((publish_at - now).total_seconds() / 3600, 1)
                 if publish_at is not None else None)
        row.update({"digest": item["digest"], "at": item["at"], "hours_to_publish": hours,
                    "due": hours is not None and hours < CONFIRM_DUE_HOURS})
        awaiting.append(row)
    awaiting.sort(key=lambda row: (row["publish_at"] or "", row["file"]))
    return {AWAITING: awaiting, NOT_REQUESTED: not_requested}


def summary(split_result) -> dict:
    """数と最短の publish_at（「確定待ち n 本（最短の publish_at）」）。"""
    awaiting = split_result[AWAITING]
    times = [row["publish_at"] for row in awaiting if row["publish_at"]]
    return {"awaiting_confirm": len(awaiting), "not_requested": len(split_result[NOT_REQUESTED]),
            "awaiting_confirm_earliest_publish_at": min(times) if times else None,
            "confirm_due": sum(1 for row in awaiting if row["due"])}


def for_account(account_name: str, account_cfg: dict, *, now=None) -> dict:
    """queue の原稿を読んで分ける（同期を伴わない読み手の照合先で）。"""
    from . import core, writeback
    now = now if now is not None else jst.now_jst()
    if accounts.repo_state(account_cfg) != accounts.REPO_OK:
        return {AWAITING: [], NOT_REQUESTED: []}
    files = core.list_queue_files(
        account_cfg, tree_sha=writeback.upstream_sha(account_cfg.get("repo_dir")))
    return split(account_name, account_cfg, files, now)


def confirm_command(row) -> str:
    """確定の命令の 1 行（名乗りは人が入れる）。"""
    return f"thth approve {row['rel']} --confirm {row['digest']} --by <名前>"


def line(summary_row) -> str | None:
    """「確定待ち n 本（最短の publish_at）」の 1 行。無ければ None。"""
    n = summary_row.get("awaiting_confirm")
    if not n:
        return None
    return (f"確定待ち {n} 本（最短の publish_at {summary_row['awaiting_confirm_earliest_publish_at'] or '—'}）"
            "——thth approve の 1 段目が済み、--confirm が残っています（確定するまで出ません）")

