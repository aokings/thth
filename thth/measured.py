"""実測を台帳から機械的に並べる（T5・運用指摘 2026-09-12）。

**なぜ要るか。** 運用の担当が、VM の台帳（ndjson）を目で追って実測表を
作っていた。その結果、一晩で 2 回、読み違いが起きた。

  1. 返信の台帳の行（`marks: [1, 6]` が同居）を、views の行と取り違えた
  2. `お茶` の 6 時間値（views 42）が既に入っていたのを見落とし、
     「未取得」と報告した

**現物を目で追うのも十分に間違えます。** 機械的に並べる口が要る。

`thth/collect.py` が書いた `data/sns/insights/posts/*.ndjson`（投稿ごと）と
`data/sns/insights/account/*.ndjson`（アカウント日次）を読む。**読むだけ。
何も書かない。**
"""
from __future__ import annotations

import json
import os

from . import accounts as accounts_mod
from . import queuefile as queuefile_mod

# **期待する指標名の一覧**（運用指摘 2026-09-12）。台帳の全行を通して 1 度も
# 現れない名前を `missing_metrics` に出す。`clicks` は実装の穴で一度も記録
# されていなかった（2026-09-12 に修正済み）——この一覧はその再発を見つける
# ためのもの。**0 と混ぜない**のが要点（規約 12: 判らないものを判らないと言う）。
EXPECTED_METRIC_NAMES = (
    "views", "likes", "replies", "reposts", "quotes", "shares", "clicks",
    "followers_count",
)


def _read_ndjson(path: str) -> tuple[list, bool]:
    """1 本の ndjson を読む。`(行の配列, 壊れているか)`。

    `thth/replies.py` の `_read_replies_file()` と同じ流儀（この repo の規約）:
    **壊れと不存在を混ぜない**。ファイルが無ければ「まだ採っていないだけ」——
    `broken=False` で空を返す。JSON として読めない行が 1 行でもあれば、その
    ファイルは信用できないので `broken=True` にして**中身は 1 行も使わない**。
    壊れた行の手前までをこっそり混ぜると、「壊れて一部しか読めなかった」を
    「n 件だけだった」と取り違える。
    """
    if not os.path.exists(path):
        return [], False
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], True
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            return [], True
    return rows, False


def _form_for(queue_dir: str, file_name: str | None) -> str | None:
    """queue ファイルの front-matter から `form` を引く。**読むだけ**——検証しない。

    投稿の選定に使う口（`core.list_queue_files()`）ではないので、同期を確認した
    commit の中身と一致するかは問わない。連投の段（`thth/collect.py` の
    `_with_bundle_posts()`）は `file` に `"<元のファイル名>#<段番号>"` を残すので、
    `#` の手前で切って実物のファイルを探す。読めなければ（無い・壊れている）
    `None`（取れなかった、であって「型無し」ではないが、この口は区別しない——
    どちらも「判らない」なので `null` でよい）。
    """
    if not file_name:
        return None
    base = file_name.split("#", 1)[0]
    path = os.path.join(queue_dir, base)
    try:
        qf = queuefile_mod.parse(path)
    except OSError:
        return None
    if qf.malformed:
        return None
    return qf.front_matter.get("form")


def _mark_collapsed(rows: list) -> None:
    """刻みが同居している行に印を付ける（`rows` を書き換える）。

    **理由**: 25 時間後の 1 回の取得に `1h`・`6h`・`24h` の印が付いても、
    過去 3 時点を復元したわけではない。実際に測ったのは 1 点。「n 点測った」と
    読ませないための印。
    """
    for row in rows:
        marks = row.get("marks") or []
        row["marks_collapsed"] = len(marks) >= 2


def load(account_name: str) -> dict:
    """1 つの account の実測を台帳から機械的に並べる。

    **account をまたいで並べない。** ここは 1 account だけを扱う。複数 account を
    1 つの表にまとめる関数はここに作らない——混ぜると「型の差」と「アカウントの
    地力の差」が分離できなくなる（設計 §2 の表・§12.3 と同じ理由。
    `account_report.measured_views_by_account()` 参照）。

    戻り値:
      - `posts`: 投稿ごとの配列（1 投稿 1 要素）。`post_id`・`topic`・`form`・
        `file`・`posted_at` と、時系列の `rows`（`collected_at`・`age_hours`・
        `marks`・`marks_collapsed`・`metrics`）を持つ。
      - `account_daily`: アカウント日次の行（`date` と `metrics`）。
      - `broken`: 読めなかったファイルの名前（壊れと不存在を混ぜない）。
      - `missing_metrics`: 1 度も現れていない指標の名前。

    読むだけ。何も書かない。
    """
    account_cfg = accounts_mod.load_account(account_name)
    repo_dir = account_cfg.get("repo_dir") or ""
    queue_dir = os.path.join(repo_dir, account_cfg.get("queue_dir") or "")
    posts_dir = os.path.join(repo_dir, "data", "sns", "insights", "posts")
    account_daily_dir = os.path.join(repo_dir, "data", "sns", "insights", "account")

    broken: list = []
    posts: list = []
    seen_metric_names: set = set()

    if os.path.isdir(posts_dir):
        for name in sorted(os.listdir(posts_dir)):
            if not name.endswith(".ndjson"):
                continue
            rows, is_broken = _read_ndjson(os.path.join(posts_dir, name))
            if is_broken:
                broken.append(name)
                continue
            if not rows:
                continue
            _mark_collapsed(rows)
            rows.sort(key=lambda r: r.get("collected_at") or "")
            for row in rows:
                seen_metric_names.update((row.get("metrics") or {}).keys())

            first = rows[0]
            post_id = name[: -len(".ndjson")]
            posts.append({
                "post_id": post_id,
                "topic": first.get("topic"),
                "form": _form_for(queue_dir, first.get("file")),
                "file": first.get("file"),
                "posted_at": first.get("posted_at"),
                "rows": [
                    {
                        "collected_at": row.get("collected_at"),
                        "age_hours": row.get("age_hours"),
                        "marks": row.get("marks"),
                        "marks_collapsed": row.get("marks_collapsed", False),
                        "metrics": row.get("metrics"),
                    }
                    for row in rows
                ],
            })

    account_daily: list = []
    if os.path.isdir(account_daily_dir):
        for name in sorted(os.listdir(account_daily_dir)):
            if not name.endswith(".ndjson"):
                continue
            rows, is_broken = _read_ndjson(os.path.join(account_daily_dir, name))
            if is_broken:
                broken.append(name)
                continue
            for row in rows:
                seen_metric_names.update((row.get("metrics") or {}).keys())
                account_daily.append({"date": row.get("date"), "metrics": row.get("metrics")})

    missing_metrics = [m for m in EXPECTED_METRIC_NAMES if m not in seen_metric_names]

    posts.sort(key=lambda p: p["post_id"])
    account_daily.sort(key=lambda r: r.get("date") or "")

    return {
        "account": account_name,
        "posts": posts,
        "account_daily": account_daily,
        "broken": sorted(broken),
        "missing_metrics": missing_metrics,
    }
