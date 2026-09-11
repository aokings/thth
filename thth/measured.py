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

**所有 account を根拠付きで選別する**（外部レビュー再判定 M3・2026-09-12）。
`repo_dir` は account ごとに分かれている前提だったが、共有された場合に選別が
無く、他 account の投稿・日次が混ざっていた。根拠は 2 つあり、記録され方が
違うので選び方も分ける:

  - 投稿ごとの台帳（`posts/*.ndjson`）の行は `account` を持たない。唯一の
    手がかりは `file`（採取当時の queue ファイル名）なので、queue_dir の
    実ファイルを開いて front-matter の `account` を見る（`_form_for()` と
    同じ解決）。ファイルが無い・壊れている・`account` が無ければ「不明」
    （`posts_unknown_ownership`）——他 account と判った場合と違い、**この
    account かもしれないので、値を混ぜずに区別して出す**。
  - アカウント日次（`account/*.ndjson`）は `thth/collect.py` の
    `_collect_account_daily()` がファイル名そのものに `<account>-<年月>` を
    刻んでいる。ファイル名が一致しないものは他 account と判っているので、
    ただ除く（「不明」にはしない）。

**採取時点に帰属する値と、現在の原稿から引いた値を区別する**（M4）。`form`
は保存時ではなく「いま queue ファイルにある値」を読んでいるだけなので、
`form`（過去の型であるかのような名前）を出すのをやめ、`form_now` と
`form_source: "current_draft"` で「いまの原稿から引いた値」だと明示する。
採取時点の型は台帳に元から無いので、無いものは無いと出す（推定で埋めない）。
"""
from __future__ import annotations

import json
import os
import re

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


def _account_for(queue_dir: str, file_name: str | None) -> str | None:
    """投稿ごとの台帳の行から**所有 account を根拠付きで引く**（M3）。

    `posts/*.ndjson` の行そのものは `account` を持たない（`thth/collect.py`
    が書いていない）ので、`file`（採取当時の queue ファイル名）を手がかりに
    queue_dir の実ファイルを開き、front-matter の `account` を見る。
    `_form_for()` と同じ解決（連投の段は `#` の手前で切って実物を探す）。

    読めなければ（無い・壊れている・`account` が無い）`None`——**「不明」**。
    共有 repo で他 account の投稿が既に無くなっている場合、ここで `None` を
    返すことで「この account の投稿だと推定して混ぜる」のを避ける。
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
    return qf.front_matter.get("account")


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
      - `posts`: **この account が所有すると根拠付きで判った**投稿ごとの配列
        （1 投稿 1 要素）。`post_id`・`topic`・`form_now`（いまの queue
        ファイルから引いた値。過去の型ではない）・`form_source`
        （`"current_draft"` 固定。`form_now` の由来を明示する）・`file`・
        `posted_at` と、時系列の `rows`（`collected_at`・`age_hours`・
        `marks`・`marks_collapsed`・`metrics`）を持つ。
      - `posts_unknown_ownership`: 所有 account を**判別できなかった**投稿
        台帳の post_id（共有 repo で、対応する queue ファイルが無い・壊れて
        いる・`account` が無い場合）。この account の実測へ推定で混ぜず、
        ここに分けて出す——`posts` にも他 account の分にも入らない。
      - `account_daily`: アカウント日次の行（`date` と `metrics`）。ファイル名
        `<account>-<年月>.ndjson` が一致するものだけ（M3）。
      - `broken`: 読めなかった・信用できなかったファイルの名前（壊れと
        不存在を混ぜない。ファイル名の account と中身の `account` が食い違う
        場合もここに入る）。
      - `missing_metrics`: 1 度も現れていない指標の名前（他 account の行を
        含めずに判定する）。

    読むだけ。何も書かない。
    """
    account_cfg = accounts_mod.load_account(account_name)
    repo_dir = account_cfg.get("repo_dir") or ""
    queue_dir = os.path.join(repo_dir, account_cfg.get("queue_dir") or "")
    posts_dir = os.path.join(repo_dir, "data", "sns", "insights", "posts")
    account_daily_dir = os.path.join(repo_dir, "data", "sns", "insights", "account")

    broken: list = []
    posts: list = []
    unknown_posts: list = []
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

            post_id = name[: -len(".ndjson")]
            first = rows[0]
            # **所有 account を根拠付きで選別する**（M3）。行そのものに
            # `account` が無いので、`file`（採取当時の queue ファイル名）を
            # 手がかりに queue_dir の実ファイルの front-matter を見る。
            owner = _account_for(queue_dir, first.get("file"))
            if owner is None:
                # 不明——この account かもしれないが判別できない。混ぜない。
                unknown_posts.append(post_id)
                continue
            if owner != account_name:
                # 他 account と判っている。不明ではなく、単に自分のではない。
                continue

            _mark_collapsed(rows)
            rows.sort(key=lambda r: r.get("collected_at") or "")
            for row in rows:
                seen_metric_names.update((row.get("metrics") or {}).keys())

            posts.append({
                "post_id": post_id,
                "topic": first.get("topic"),
                "form_now": _form_for(queue_dir, first.get("file")),
                "form_source": "current_draft",
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
    # **アカウント日次はファイル名そのものが根拠**（`thth/collect.py` の
    # `_collect_account_daily()` が `<account>-<年月>.ndjson` で書く）。
    # 一致しないファイルは他 account と判っているので、不明にはせず、ただ除く。
    daily_name_re = re.compile(r"^" + re.escape(account_name) + r"-\d{4}-\d{2}\.ndjson$")
    if os.path.isdir(account_daily_dir):
        for name in sorted(os.listdir(account_daily_dir)):
            if not name.endswith(".ndjson"):
                continue
            if not daily_name_re.match(name):
                continue
            rows, is_broken = _read_ndjson(os.path.join(account_daily_dir, name))
            if is_broken:
                broken.append(name)
                continue
            # ファイル名の account と、行の中身の `account` が食い違う行が
            # あれば、このファイルは信用できない（ファイル名だけを根拠に
            # 混ぜない）——壊れとして扱う。
            if any(r.get("account") not in (None, account_name) for r in rows):
                broken.append(name)
                continue
            for row in rows:
                seen_metric_names.update((row.get("metrics") or {}).keys())
                account_daily.append({"date": row.get("date"), "metrics": row.get("metrics")})

    missing_metrics = [m for m in EXPECTED_METRIC_NAMES if m not in seen_metric_names]

    posts.sort(key=lambda p: p["post_id"])
    unknown_posts.sort()
    account_daily.sort(key=lambda r: r.get("date") or "")

    return {
        "account": account_name,
        "posts": posts,
        "posts_unknown_ownership": unknown_posts,
        "account_daily": account_daily,
        "broken": sorted(broken),
        "missing_metrics": missing_metrics,
    }
