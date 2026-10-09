"""反応（再投稿・いいね）の記録（設計 3.15.0 §2.4）。

置き場は `state/<account>/reactions/`。**1 件 1 ファイル**（0600・置き場は 0700）:

  `kind`        repost | like
  `post_id`     反応した投稿の id（Bluesky は AT URI）
  `platform_id` SNS が返した反応の id（Threads は再投稿の id・Bluesky は記録の uri・
                Mastodon は boost の Status の id か元の id・X は元の id）。取り消しはこれを使う
  `at`          反応した時刻（JST）
  `undone_at`   取り消した時刻（無ければ null）。取り消しは**同じファイルに足す**
                （`sent/` の `retracted_at` と同じ流儀・消さない）
  `by`・`via`   誰が・どの口から（cli・mcp）

**相手の投稿の本文は保存しない。** 退会（`thth account leave`）は `state/<account>/` を
丸ごと消すので、この置き場も消える。

冪等: 取り消していない記録（`undone_at` が null）が同じ `kind`・`post_id` にあれば、
SNS に送らずにそれを返す（`active()`）。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets

from . import jst, secrets_fs
from . import postid as postid_mod

KINDS = ("repost", "like")
VIAS = ("cli", "mcp")
DIR_NAME = "reactions"


def dir_for(state_dir: str) -> str:
    return os.path.join(state_dir, DIR_NAME)


def _name(kind: str, post_id: str) -> str:
    # post_id をそのままファイル名にしない（Bluesky の AT URI は `/` を含む）。
    digest = hashlib.sha256(post_id.encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}-{secrets.token_hex(4)}.json"


def records(state_dir: str) -> list:
    """記録を全部読む（壊れた 1 本で全部を落とさない・`path` を添える）。"""
    d = dir_for(state_dir)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if (not isinstance(row, dict) or row.get("kind") not in KINDS
                or not isinstance(row.get("post_id"), str)):
            continue
        row["path"] = path
        out.append(row)
    return out


def active(state_dir: str, kind: str, post_id: str) -> dict | None:
    """取り消していない同じ反応（いちばん新しいもの）。無ければ None。"""
    rows = [row for row in records(state_dir)
            if row["kind"] == kind and row["post_id"] == post_id and not row.get("undone_at")]
    rows.sort(key=lambda row: str(row.get("at") or ""))
    return rows[-1] if rows else None


def _public(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "path"}


def write(state_dir: str, *, kind: str, post_id: str, platform_id: str, by: str,
          via: str = "cli", at: str | None = None, media: str | None = None) -> dict:
    """反応を 1 件書く。戻りは書いた記録（`path` つき）。"""
    if kind not in KINDS:
        raise ValueError("invalid_reaction_kind")
    if via not in VIAS:
        raise ValueError("invalid_via")
    if not postid_mod.is_usable(post_id) or not isinstance(platform_id, str) or not platform_id:
        raise ValueError("invalid_reaction_record")
    row = {"kind": kind, "post_id": post_id, "platform_id": platform_id,
           "at": at or jst.iso(), "undone_at": None, "by": by, "via": via}
    if media:
        row["media"] = media
    path = os.path.join(dir_for(state_dir), _name(kind, post_id))
    secrets_fs.atomic_write_json(path, row, mode=0o600, dir_mode=0o700)
    return {**row, "path": path}


def mark_undone(row: dict, *, by: str, via: str = "cli", at: str | None = None) -> dict:
    """記録に `undone_at`・`undone_by`・`undone_via` を足す（消さない）。"""
    if via not in VIAS:
        raise ValueError("invalid_via")
    path = row["path"]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("invalid_reaction_record")
    data["undone_at"] = at or jst.iso()
    data["undone_by"] = by
    data["undone_via"] = via
    secrets_fs.atomic_write_json(path, data, mode=0o600, dir_mode=0o700)
    return {**data, "path": path}


def events(state_dir: str) -> list:
    """数える材料: 反応の時刻と取り消しの時刻（JST の datetime の list）。読めない時刻は数えない。"""
    out = []
    for row in records(state_dir):
        for key in ("at", "undone_at"):
            at = jst.parse(row.get(key))
            if at is not None:
                out.append(at)
    return out


def public(row: dict) -> dict:
    """出力用（ファイルの path を除く）。"""
    return _public(row)
