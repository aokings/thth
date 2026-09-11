"""スレッド連投の実行記録（設計 §2・§3・§4・工程 3〜5）。

**Git で同期済みであることと、THTH が実際に公開した記録であることは別**
（Codex 最終条件 1）。原稿は利用者 repo にあって人が書き換えられる。
公開記録は THTH が自分で書いた事実。**両方が揃って初めて親が決まる。**

`run_id` は**最初の公開要求より前に発行して永続化する。** 1 段目の `post_id` を
実行 ID にする案は採らない——**1 段目の公開結果が不明になった場合、ID がまだ
無く、`inflight` も進行状態も作れない。** 結果不明はいちばん守りたい場面なので、
そこで使えない識別子は使えない。
"""
from __future__ import annotations

import json
import os
import re
import secrets

from . import accounts as accounts_mod
from . import jst

# 段の状態（設計 §3.1）。**「どこまで出たか」ではなく「何が確定しているか」。**
PENDING = "pending"        # 未着手
REQUESTED = "requested"    # 公開要求を送った。**結果は未確定**
PUBLISHED = "published"    # 公開を確認し、post_id を得た
UNRESOLVED = "unresolved"  # 結果が分からない。**自動で再送しない**
STOPPED = "stopped"        # 実行側が停止を確認した

_RUN_ID_RE = re.compile(r"^run-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")


class RunError(Exception):
    """実行記録が読めない・食い違う。**推測で続けない。**"""


def runs_dir() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", "threads")


def new_run_id(now=None) -> str:
    """**公開要求より前**に発行する実行 ID。"""
    now = now if now is not None else jst.now_jst()
    return f"run-{now.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"


def run_path(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id or ""):
        raise RunError(f"run_id の形が違います: {run_id!r}")
    return os.path.join(runs_dir(), f"{run_id}.json")


def _atomic_write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load(run_id: str) -> dict | None:
    path = run_path(run_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        row = json.load(f)
    if row.get("run_id") != run_id:
        raise RunError(f"実行記録の run_id が食い違います: {run_id}")
    return row


def save(row: dict) -> dict:
    _atomic_write(run_path(row["run_id"]), row)
    return row


def start(*, account: str, rel_path: str, bundle_sha: str, segment_count: int,
           segment_shas: list, continue_until: str, by: str, now=None) -> dict:
    """実行を始める。**公開要求を送る前に呼ぶ。**"""
    now = now if now is not None else jst.now_jst()
    row = {
        "run_id": new_run_id(now),
        "account": account,
        "rel_path": rel_path,
        "bundle_sha": bundle_sha,
        "continue_until": continue_until,
        "started_at": now.isoformat(),
        "started_by": by,
        "root_post_id": None,
        "stop_confirmed_at": None,
        "stop_reason": None,
        "posts": [
            {"index": i, "state": PENDING, "post_id": None, "posted_at": None,
             "reply_to": None, "container_id": None,
             "text_sha256": segment_shas[i - 1] if i <= len(segment_shas) else None,
             "bundle_sha": bundle_sha, "last_ok": None, "note": None}
            for i in range(1, segment_count + 1)
        ],
    }
    return save(row)


def find_open(account: str, rel_path: str) -> dict | None:
    """その原稿の**進行中の実行**を返す（無ければ None）。

    **同じ束を 2 つの実行が進めない**ための照会。呼び出し側はこれを
    **ロックの中で**行う（state に書くだけでは排他にならない・Codex 最終条件 3）。
    """
    directory = runs_dir()
    if not os.path.isdir(directory):
        return None
    found = None
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if row.get("account") != account or row.get("rel_path") != rel_path:
            continue
        if is_finished(row):
            continue
        if found is None or row.get("started_at", "") > found.get("started_at", ""):
            found = row
    return found


def find_latest(account: str, rel_path: str) -> dict | None:
    """その原稿の**いちばん新しい実行**（終わっていても返す）。

    **終わった実行を見ないと、同じ束をもう一度最初から出してしまう**
    （実装中に踏んだ: 3 段出し切ったあと `find_open` が None を返し、
    4 周目で新しい実行が始まって 1 段目を再公開した）。
    """
    directory = runs_dir()
    if not os.path.isdir(directory):
        return None
    found = None
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if row.get("account") != account or row.get("rel_path") != rel_path:
            continue
        if found is None or row.get("started_at", "") > found.get("started_at", ""):
            found = row
    return found


def open_runs(account: str) -> list:
    """その account の**終わっていない実行**（未解決を含む）。"""
    directory = runs_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if row.get("account") == account and not is_finished(row):
            out.append(row)
    return out


def has_unresolved(account: str) -> list:
    """**未解決の公開結果**がある実行（設計 §3.3）。

    これがある間は、**同じ account の別投稿へ進まない**（Codex 最終条件 3）。
    """
    return [r for r in open_runs(account)
            if any(p["state"] in (REQUESTED, UNRESOLVED) for p in r["posts"])]


def is_finished(row: dict) -> bool:
    if row.get("stop_confirmed_at"):
        return True
    return all(p["state"] == PUBLISHED for p in row.get("posts", []))


def next_index(row: dict) -> int | None:
    """次に出す段。**未解決があれば None**（自動で進まない）。"""
    if row.get("stop_confirmed_at"):
        return None
    for post in row["posts"]:
        if post["state"] in (REQUESTED, UNRESOLVED):
            return None
        if post["state"] == PENDING:
            return post["index"]
    return None


def mark(row: dict, index: int, state: str, **fields) -> dict:
    post = row["posts"][index - 1]
    if post["index"] != index:
        raise RunError(f"段の並びが壊れています: {index}")
    post["state"] = state
    post.update(fields)
    if state == PUBLISHED and index == 1:
        row["root_post_id"] = post.get("post_id")
    return save(row)


def confirm_stop(row: dict, reason: str, now=None) -> dict:
    """**実行側が停止を確認した**（設計 §4.2）。

    「停止要求済み」（原稿の `revoked_at`）とは別。**こちらは公開側の記録。**
    """
    now = now if now is not None else jst.now_jst()
    row["stop_confirmed_at"] = now.isoformat()
    row["stop_reason"] = reason
    return save(row)


def resolve_parent(row: dict, index: int, *, draft_posts: list,
                    account: str) -> str:
    """`index` 段目の**返信先 `post_id`** を決める（設計 §2.2）。

    **全部一致しなければ出さない。** 条件は設計 §2.2 の 6 つ。
    ここで返すのは「THTH 自身が公開したと記録している投稿の ID」であって、
    **原稿に書かれている文字列ではない。**
    """
    if index <= 1:
        return ""
    if row.get("account") != account:
        raise RunError("実行記録の account が違います")

    parent = row["posts"][index - 2]
    for earlier in row["posts"][: index - 1]:
        if earlier["state"] != PUBLISHED or not earlier["post_id"]:
            raise RunError(
                f"{earlier['index']} 段目がまだ公開されていません"
                f"（state: {earlier['state']}）。先の段から順に出します")
        # **別の実行で出した投稿を拾わない。**
        draft = _draft_post(draft_posts, earlier["index"])
        if draft is None:
            raise RunError(f"原稿に {earlier['index']} 段目の記録がありません")
        if _unquote(draft.get("run_id")) != row["run_id"]:
            raise RunError(
                f"{earlier['index']} 段目の記録が別の実行のものです"
                f"（原稿: {_unquote(draft.get('run_id'))} / "
                f"いまの実行: {row['run_id']}）")
        # **原稿の記録と永続化した公開記録が一致すること**（§2.1.1）。
        if _unquote(draft.get("post_id")) != earlier["post_id"]:
            raise RunError(
                f"{earlier['index']} 段目の post_id が原稿と公開記録で"
                f"食い違います（原稿: {_unquote(draft.get('post_id'))} / "
                f"記録: {earlier['post_id']}）")
    return parent["post_id"]


def _draft_post(draft_posts: list, index: int) -> dict | None:
    for post in draft_posts:
        if str(post.get("index")) == str(index):
            return post
    return None


def _unquote(value):
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def frozen_records(row: dict) -> list:
    """**公開済み部分の記録**（承認の対象に入る・設計 §5）。"""
    return [{"index": p["index"], "post_id": p["post_id"],
             "text_sha256": p["text_sha256"]}
            for p in row["posts"] if p["state"] == PUBLISHED]


def summary(row: dict) -> dict:
    """人に見せる形（`revoke` と board・設計 §4.2）。

    **「N 段目以降は未公開」と断定しない。** 確認できた段・要求中の段・
    未着手の段を**分けて**出す（Codex 最終条件 3）。
    """
    published, requested, pending, unresolved = [], [], [], []
    for post in row["posts"]:
        {PUBLISHED: published, REQUESTED: requested,
         PENDING: pending, UNRESOLVED: unresolved,
         STOPPED: pending}[post["state"]].append(post["index"])
    return {
        "run_id": row["run_id"], "account": row["account"],
        "rel_path": row["rel_path"], "root_post_id": row.get("root_post_id"),
        "published": published, "requested": requested,
        "unresolved": unresolved, "pending": pending,
        "stop_confirmed_at": row.get("stop_confirmed_at"),
        "continue_until": row.get("continue_until"),
    }


def stop_report(account: str, rel_path: str) -> dict | None:
    """`revoke` が人に見せる形（設計 §4.2・Codex 最終条件 3）。

    **「N 段目以降は未公開」と断定しない。**
    確認できた段・要求中の段・未着手の段を**分けて**出す。
    **「次の確認点は約 10 分後」とも書かない**——束は同じ起動の中で進むので
    固定ではない。
    """
    row = find_latest(account, rel_path)
    if row is None:
        return None
    return summary(row)


def format_stop_report(report: dict | None) -> str:
    if report is None:
        return ("この原稿はまだ実行されていません（公開された段はありません）。")
    lines = []
    lines.append(f"  公開を確認できた段: {_列(report['published'])}")
    if report["requested"]:
        lines.append(f"  要求中（結果が未確定）の段: {_列(report['requested'])}")
    if report["unresolved"]:
        lines.append(f"  結果が分からない段: {_列(report['unresolved'])}")
    lines.append(f"  未着手の段: {_列(report['pending'])}")
    if report["stop_confirmed_at"]:
        lines.append(f"  実行側が停止を確認済み: {report['stop_confirmed_at']}")
    return "\n".join(lines)


def _列(values: list) -> str:
    return "、".join(str(v) for v in values) if values else "なし"
