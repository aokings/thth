"""`after_you_posted`: 出したあとに、自分の投稿と絡みに行った返信がどう
受け取られたかを返す（設計「自分の泉」§2.2・§4）。

`posts` は所有の裏付けがある実測台帳の根投稿、`engagements` は絡みの台帳の
自分の返信。どちらも `thth/measured.py` の実測（`marks` に 24 を含む行）と
結び、本文・username は出力に 1 バイトも入れない。project の答えは account
ごとの節を並べるだけで、媒体・account をまたぐ合計や順位を作らない。

**読むだけ。何も書かない。API も git も触らない。**

規約（設計 §5・全部の口に共通）:

1. 数は `n`・期間（`window_days`）・揃えた条件（`aligned_on`）と一緒に。
2. 取れていない刻み（24h の marks が無い）は `null`・`covered: false`
   （0 と混ぜない）。
3. `n` が `min_n` に満たない群は中央値を返さない。
4. `one_thing_to_change` は 1 個（無ければ `null`）。時刻帯ごとの
   `reacted/n` を比べ、**各帯の n が `min_n` 以上のときだけ**。
5. 順位付け・おすすめは作らない（`one_thing_to_change` の 1 個を除く）。
"""
from __future__ import annotations

import datetime
import json
import math
import statistics
import sys

from . import accounts as accounts_mod
from . import engagements as engagements_mod
from . import jst
from . import measured as measured_mod
from . import queuefile as queuefile_mod
from . import replies as replies_mod
from . import threadshape as threadshape_mod
from . import topics as topics_mod

SCHEMA_SOURCE = "own-ledgers"

# 設計「自分の泉」§4・§5-2:「自分の泉なので低く」（横断の泉の 20 とは別の値）。
DEFAULT_MIN_N = 5
DEFAULT_WINDOW_DAYS = 30

# 揃えた条件（設計「自分の泉」§2.2 の出力例そのまま）。
ALIGNED_ON = "marks=24"
VIEWS_MARK = 24

HOUR_BAND_NAMES = tuple(name for name, _lo, _hi in threadshape_mod.HOUR_BANDS)


class AfterError(Exception):
    """問いが受け取れない（時刻帯が未知・期間や下限が 0 以下・author_key の形が違う）。

    **黙って空の答えを返さない**（loud reject）。
    """


def _reject(message: str) -> None:
    raise AfterError(message)


def _num(value):
    """見せる数。整数で表せるなら整数、それ以外は小数第 1 位まで。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else round(value, 1)
    return value


def _normalized_topic(value) -> tuple[str | None, bool]:
    """台帳の topic を安全に正規化する。`None` は正規、非文字列は壊れ。"""
    if value is None:
        return None, True
    if not isinstance(value, str):
        return None, False
    return queuefile_mod.normalize_topic(value), True


def _count_metric(value):
    """件数指標として信用できる有限の非負数。それ以外は欠測。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _stat(values: list, *, min_n: int) -> dict:
    """`{"median", "p25", "p75", "n"}`。**n < min_n なら中央値を返さない**（規約 3）。"""
    n = len(values)
    out = {"median": None, "p25": None, "p75": None, "n": n}
    if n == 0 or n < min_n:
        return out
    ordered = sorted(values)
    out["median"] = _num(statistics.median(ordered))
    if n >= 2:
        q25, _q50, q75 = statistics.quantiles(ordered, n=4, method="inclusive")
        out["p25"] = _num(q25)
        out["p75"] = _num(q75)
    return out


def _stat_median_only(values: list, *, min_n: int) -> dict:
    """`{"median", "n"}`（`views_24h` の形・設計「自分の泉」§2.2 の出力例）。"""
    n = len(values)
    out = {"median": None, "n": n}
    if n == 0 or n < min_n:
        return out
    out["median"] = _num(statistics.median(values))
    return out


def _measured_24h_metrics(measured_post: dict | None) -> tuple[dict, bool]:
    """`measured.load()["posts"]` の 1 件から、`marks` に 24 を含む行の
    `metrics` を採る。**24 の刻みが無ければ `({}, False)`**（0 と混ぜない）。
    """
    if not measured_post:
        return {}, False
    for row in measured_post.get("rows") or []:
        if 24 in (row.get("marks") or []):
            return (row.get("metrics") or {}), True
    return {}, False


def _measured_24h_row(measured_post: dict | None) -> tuple[dict | None, bool]:
    """24h の印がある実測行と coverage。

    `marks` は実経過 24 時間ぴったりではないので、`age_hours` と
    `marks_collapsed` も呼び出し側へ渡す。印が無ければ `(None, False)`。
    """
    if not measured_post:
        return None, False
    for row in measured_post.get("rows") or []:
        if VIEWS_MARK in (row.get("marks") or []):
            return row, True
    return None, False


def _kind_lookup(account_name: str):
    """現在の topic shelf の `kind` を account 優先で引く。

    `topics.kind_of()` は account 自身の記録を優先し、無ければ共有記録へ
    フォールバックする。投稿時点の歴史属性ではないことは provenance と
    `cannot_say` に出す。
    """
    cache: dict[str, str | None] = {}
    broken = False

    def lookup(topic: str | None) -> str | None:
        nonlocal broken
        if not topic:
            return None
        if topic not in cache:
            try:
                cache[topic] = topics_mod.kind_of(topic, account_name)
            except topics_mod.ShelfBroken:
                broken = True
                cache[topic] = None
        return cache[topic]

    def is_broken() -> bool:
        return broken

    return lookup, is_broken


def _replies_back_from_ledger(account_name: str, post_id: str | None,
                              *, allowed_names=None):
    """`metrics.replies` が無いときの二段目: 返信の台帳の他者返信の数。

    **一度も取得していない（台帳にその post_id の記録が無い）のか、取得して
    0 件だったのかを区別できないときは `None`**（判らないものを 0 にしない）。
    """
    if not post_id:
        return None
    try:
        result = replies_mod.load(account_name, post_id=post_id,
                                  allowed_names=allowed_names)
    except accounts_mod.AccountError:
        return None
    if result["broken"]:
        return None
    counts = result["counts"]
    if counts["fetches"] == 0 and counts["replies"] == 0:
        return None
    return counts["other"]


def reaction_metrics(account_name: str, measured_by_post: dict, post_id: str | None) -> dict:
    """1 件の `post_id`（絡みの台帳の自分の返信）の反応（`views_24h`・
    `likes_24h`・`replies_back_24h`・`covered`）。`_measured_24h_metrics()`・
    `_replies_back_from_ledger()` を組み立てる、この口の中の唯一の場所——
    `who_is_this`（`who_cli._account_node()`・T3-1）と `thread_read`・
    `where_cli` の `you_and_them.last_reaction`（T3-2）が共通してここを呼ぶ
    （発注 T3-2「同じ計算を 2 か所に置かない」）。
    """
    metrics, covered = _measured_24h_metrics(measured_by_post.get(post_id))
    views_24h = metrics.get("views") if covered else None
    likes_24h = metrics.get("likes") if covered else None
    replies_metric = metrics.get("replies") if covered else None
    replies_back_24h = (replies_metric if replies_metric is not None
                        else _replies_back_from_ledger(account_name, post_id))
    return {"views_24h": views_24h, "likes_24h": likes_24h,
           "replies_back_24h": replies_back_24h, "covered": covered}


def reaction_lookup(account_name: str):
    """`account_name` の実測を 1 回読み、`post_id → reaction_metrics()` の
    関数を返す（`thread_read`・`where_cli` の `you_and_them.last_reaction`
    （T3-2）向けの便利口。実測を読むのも反応を組み立てるのも、どちらも
    `after_cli` の中の 1 か所だけで行う）。
    """
    measured_result = measured_mod.load(account_name)
    measured_by_post = {p["post_id"]: p for p in measured_result["posts"]}

    def _for(post_id):
        return reaction_metrics(account_name, measured_by_post, post_id)

    return _for


def _reacted(branch: dict) -> bool:
    return (branch["likes_24h"] or 0) >= 1 or (branch["replies_back_24h"] or 0) >= 1


def _one_thing_to_change(by_branch: list, *, min_n: int) -> tuple[str | None, str | None]:
    """時刻帯ごとの `reacted/n` を比べ、**各帯の n が `min_n` 以上のときだけ** 1 個
    返す（設計「自分の泉」§4・規約 4）。`(message, cannot_say_reason)`——
    どちらか一方だけが非 `None`。**順位・おすすめはこれ以外に作らない。**
    """
    by_band: dict[str, list] = {}
    for b in by_branch:
        band = b.get("hour_band")
        if band is None:
            continue
        by_band.setdefault(band, []).append(b)

    qualifying = {}
    for band, rows in by_band.items():
        if len(rows) < min_n:
            continue
        reacted = sum(1 for r in rows if _reacted(r))
        qualifying[band] = (reacted, len(rows), reacted / len(rows))

    if len(qualifying) < 2:
        return None, "帯ごとの n が足りない"

    worst_band = min(qualifying, key=lambda b: qualifying[b][2])
    best_band = max(qualifying, key=lambda b: qualifying[b][2])
    if qualifying[worst_band][2] >= qualifying[best_band][2]:
        return None, "帯ごとの反応に差が無い"

    w_reacted, w_n, _rate = qualifying[worst_band]
    message = f"{worst_band}の帯は反応 {w_reacted}/{w_n}。{best_band}に回す"
    return message, None


def _validate_filters(*, kind: str | None, hour_band: str | None,
                      author_key: str | None, window_days: int, min_n: int) -> None:
    if not isinstance(window_days, int) or window_days <= 0:
        _reject(f"期間（--window-days）は 1 以上の整数です: {window_days!r}")
    if not isinstance(min_n, int) or min_n <= 0:
        _reject(f"下限（--min-n）は 1 以上の整数です: {min_n!r}")
    if kind is not None and kind not in topics_mod.KINDS:
        _reject(f"知らない型です: {kind!r}。知っている型: {'・'.join(topics_mod.KINDS)}")
    if hour_band is not None and hour_band not in HOUR_BAND_NAMES:
        _reject(f"時刻帯は {list(HOUR_BAND_NAMES)} のどれかです: {hour_band!r}")
    if author_key is not None and not engagements_mod.AUTHOR_KEY_RE.match(author_key):
        _reject(f"author_key は 16 進 16 桁です: {author_key!r}")


def _resolve_names(*, account_name: str | None, project: str | None,
                   trusted_names=None) -> tuple[list, list]:
    if account_name is not None:
        if trusted_names is not None and account_name not in trusted_names:
            _reject("scope_unavailable")
        return [account_name], []
    # A server host supplies names from its authenticated account/project scope.
    # Never enumerate the global registry for that path: a project name is not
    # proof that every account in the project belongs to the caller.
    if trusted_names is not None:
        names = sorted(set(trusted_names))
        return names, [] if names else [f"project={project!r} に一致する account がありません"]
    names, cannot_say = [], []
    for name in accounts_mod.list_account_names():
        try:
            cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError as exc:
            cannot_say.append(f"{name}: {exc}")
            continue
        if cfg.get("project") == project:
            names.append(name)
    if not names:
        cannot_say.append(f"project={project!r} に一致する account がありません")
    return names, cannot_say


def _account_answer(account_name: str, *, topic: str | None, kind: str | None,
                    hour_band: str | None, reply_to: str | None,
                    author_key: str | None, window_days: int, min_n: int,
                    now, allowed_names=None) -> dict:
    """1 account の節。数値はこの関数の外へ持ち出さない。"""

    account_cfg = accounts_mod.load_account(account_name)
    from . import postid
    try:
        reply_to = postid.for_account(account_cfg, reply_to)
    except postid.PostIdError as exc:
        _reject(str(exc))
    since = now - datetime.timedelta(days=window_days)
    topic_norm = queuefile_mod.normalize_topic(topic) if topic is not None else None
    kind_of, kind_shelf_broken = _kind_lookup(account_name)

    eng = engagements_mod.load(account_cfg, account_name)
    raw_rows = eng["rows"]
    malformed_engagement_rows = sum(1 for row in raw_rows if not isinstance(row, dict))
    all_rows = [row for row in raw_rows if isinstance(row, dict)]
    rows = [row for row in all_rows if row.get("account") == account_name]
    engagement_unattributed = sum(1 for row in all_rows if not row.get("account"))
    engagement_other_account = sum(
        1 for row in all_rows if row.get("account") not in (None, account_name))
    known_engagement_ids = {
        str(row["post_id"]) for row in rows if row.get("post_id") not in (None, "")}

    filtered = []
    engagement_bad_time = 0
    engagement_kind_unknown = 0
    engagement_topic_invalid = 0
    for row in rows:
        posted_dt = jst.parse(row.get("posted_at"))
        if posted_dt is None:
            engagement_bad_time += 1
            continue
        if posted_dt < since or posted_dt > now:
            continue
        if reply_to is not None and row.get("reply_to") != reply_to:
            continue
        if author_key is not None and row.get("author_key") != author_key:
            continue
        if topic_norm is not None and row.get("topic") != topic_norm:
            continue
        row_topic, topic_valid = _normalized_topic(row.get("topic"))
        if not topic_valid:
            engagement_topic_invalid += 1
            continue
        if kind is not None:
            row_kind = kind_of(row_topic)
            if row_kind is None:
                engagement_kind_unknown += 1
                continue
            if row_kind != kind:
                continue
        if hour_band is not None and row.get("hour_band") != hour_band:
            continue
        filtered.append(row)

    measured_result = measured_mod.load(account_name)
    measured_by_post = {p["post_id"]: p for p in measured_result["posts"]}

    by_branch = []
    uncovered = 0
    for row in filtered:
        post_id = row.get("post_id")
        metrics, covered = _measured_24h_metrics(measured_by_post.get(post_id))
        views_24h = metrics.get("views") if covered else None
        likes_24h = metrics.get("likes") if covered else None
        replies_metric = metrics.get("replies") if covered else None
        replies_back_24h = (replies_metric if replies_metric is not None
                            else _replies_back_from_ledger(account_name, post_id,
                                                           allowed_names=allowed_names))
        if not covered:
            uncovered += 1
        by_branch.append({
            "post_id": post_id, "reply_to": row.get("reply_to"),
            "root": row.get("root_post"), "posted_at": row.get("posted_at"),
            "hour_band": row.get("hour_band"), "author_key": row.get("author_key"),
            "topic": row.get("topic"),
            "views_24h": _num(views_24h), "likes_24h": _num(likes_24h),
            "replies_back_24h": _num(replies_back_24h), "covered": covered,
        })

    # 自分の根投稿。母数は `measured.load()` が所有を裏付けられた投稿だけ。
    # 絡みの台帳にある post_id は、実測側の reply_to が古い/欠落でも返信と判る。
    posts_by_post = []
    posts_reply_unknown = 0
    posts_bad_time = 0
    posts_kind_unknown = 0
    posts_uncovered = 0
    posts_views_missing = 0
    posts_topic_invalid = 0
    for post in measured_result["posts"]:
        # この 2 条件は絡みに行った返信の台帳にだけ存在する。根投稿を無条件で
        # 並べると「この枝/相手での自分の投稿」に見えるので、節ごと対象外にする。
        if reply_to is not None or author_key is not None:
            continue
        post_id = str(post.get("post_id") or "")
        if post_id in known_engagement_ids or post.get("reply_to"):
            continue
        # sent-only 採集は collect が reply_to=None を注入するが、送信記録そのものに
        # 根/返信の区別が無い。明示 null の根とは扱わない。
        if post.get("source") == "sent" or not post.get("reply_to_known"):
            posts_reply_unknown += 1
            continue
        posted_dt = jst.parse(post.get("posted_at"))
        if posted_dt is None:
            posts_bad_time += 1
            continue
        if posted_dt < since or posted_dt > now:
            continue
        post_topic, topic_valid = _normalized_topic(post.get("topic"))
        if not topic_valid:
            posts_topic_invalid += 1
            continue
        if topic_norm is not None and post_topic != topic_norm:
            continue
        if kind is not None:
            post_kind = kind_of(post_topic)
            if post_kind is None:
                posts_kind_unknown += 1
                continue
            if post_kind != kind:
                continue
        band = threadshape_mod.hour_band(posted_dt)
        if hour_band is not None and band != hour_band:
            continue

        mark_row, covered = _measured_24h_row(post)
        metrics = (mark_row or {}).get("metrics") or {}
        views = _count_metric(metrics.get("views")) if covered else None
        if not covered:
            posts_uncovered += 1
        elif views is None:
            posts_views_missing += 1
        posts_by_post.append({
            "post_id": post_id, "posted_at": post.get("posted_at"),
            "topic": post_topic, "hour_band": band,
            "views_24h": _num(views), "covered": covered,
            "age_hours": (mark_row or {}).get("age_hours"),
            "marks_collapsed": bool((mark_row or {}).get("marks_collapsed", False)),
        })

    n = len(by_branch)
    reacted = sum(1 for b in by_branch if _reacted(b))

    likes_values = [b["likes_24h"] for b in by_branch if b["likes_24h"] is not None]
    replies_values = [b["replies_back_24h"] for b in by_branch
                      if b["replies_back_24h"] is not None]
    views_values = [b["views_24h"] for b in by_branch if b["views_24h"] is not None]

    post_views_values = [p["views_24h"] for p in posts_by_post
                         if p["views_24h"] is not None]
    posts_stat = _stat_median_only(post_views_values, min_n=min_n)

    cannot_say: list = []
    if uncovered:
        cannot_say.append(f"24h の刻みが未採取: {uncovered} 本")
    if posts_uncovered:
        cannot_say.append(f"自分の投稿の 24h の刻みが未採取: {posts_uncovered} 本")
    if posts_views_missing:
        cannot_say.append(f"自分の投稿の 24h views が欠測: {posts_views_missing} 本")
    if posts_stat["median"] is None:
        cannot_say.append(
            f"自分の投稿の 24h views 中央値: n={posts_stat['n']}（{min_n} 未満）")
    for label, values in (
            ("返信の 24h likes 中央値", likes_values),
            ("返信の 24h replies 中央値", replies_values),
            ("返信の 24h views 中央値", views_values)):
        if len(values) < min_n:
            cannot_say.append(f"{label}: n={len(values)}（{min_n} 未満）")
    if reply_to is not None or author_key is not None:
        cannot_say.append(
            "reply_to / author_key は返信だけの条件なので、自分の根投稿は対象外")
    if engagement_unattributed:
        cannot_say.append(
            f"account が無く帰属不明の絡み記録を数えなかった: {engagement_unattributed} 本")
    if engagement_other_account:
        cannot_say.append(
            f"別 account の絡み記録を数えなかった: {engagement_other_account} 本")
    if engagement_bad_time:
        cannot_say.append(
            f"posted_at が読めず数えなかった絡み記録: {engagement_bad_time} 本")
    if malformed_engagement_rows:
        cannot_say.append(
            f"object でなく数えなかった絡み記録: {malformed_engagement_rows} 本")
    if engagement_topic_invalid:
        cannot_say.append(
            f"topic が文字列でなく数えなかった絡み記録: {engagement_topic_invalid} 本")
    if posts_reply_unknown:
        cannot_say.append(
            f"根投稿か返信か判らず数えなかった実測投稿: {posts_reply_unknown} 本")
    if posts_bad_time:
        cannot_say.append(
            f"posted_at が読めず数えなかった実測投稿: {posts_bad_time} 本")
    if posts_topic_invalid:
        cannot_say.append(
            f"topic が文字列でなく数えなかった実測投稿: {posts_topic_invalid} 本")
    if measured_result.get("posts_unknown_ownership"):
        cannot_say.append(
            "所有 account が判らず数えなかった実測投稿: "
            f"{len(measured_result['posts_unknown_ownership'])} 本")
    if measured_result.get("broken"):
        cannot_say.append(
            f"読めない実測台帳: {len(measured_result['broken'])} ファイル")
    if eng.get("broken"):
        cannot_say.append(f"読めない絡み台帳: {eng['broken']} ファイル")
    if kind is not None:
        cannot_say.append(
            "型は投稿時点の記録ではなく、現在の topic shelf で照合した"
            "（account の記録を優先し、無ければ共有記録）")
        if engagement_kind_unknown + posts_kind_unknown:
            cannot_say.append(
                "現在の型が判らず数えなかった記録: "
                f"{engagement_kind_unknown + posts_kind_unknown} 本")
        if kind_shelf_broken():
            cannot_say.append("topic shelf が壊れているため型を確認できない")

    one_thing_to_change, band_reason = _one_thing_to_change(by_branch, min_n=min_n)
    if band_reason:
        cannot_say.append(band_reason)

    条件 = []
    if topic_norm:
        条件.append(f"語「{topic_norm}」")
    if hour_band:
        条件.append(f"時刻帯「{hour_band}」")
    if reply_to:
        条件.append(f"reply_to={reply_to}")
    頭 = "・".join(条件) + "の" if 条件 else ""
    views_summary = (f"24h views 中央値 {posts_stat['median']}（n={posts_stat['n']}）"
                     if posts_stat["median"] is not None
                     else f"24h views 中央値は言えない（n={posts_stat['n']}）")
    summary = (f"{頭}自分の投稿 {len(posts_by_post)} 本、{views_summary}。"
               f"絡みに行った返信 {n} 本のうち反応あり {reacted}"
               f"（直近 {window_days} 日）")

    return {
        "summary": summary,
        "posts": {"n": len(posts_by_post), "views_24h": posts_stat,
                  "by_post": posts_by_post},
        "engagements": {
            "n": n,
            "reacted": reacted,
            "likes_24h": _stat(likes_values, min_n=min_n),
            "replies_back_24h": _stat(replies_values, min_n=min_n),
            "views_24h": _stat_median_only(views_values, min_n=min_n),
            "by_branch": by_branch,
        },
        "comparable": {"window_days": window_days, "aligned_on": ALIGNED_ON,
                       "medium": account_cfg.get("media"), "account": account_name},
        "cannot_say": cannot_say,
        "one_thing_to_change": one_thing_to_change,
        "provenance": {"source": SCHEMA_SOURCE,
                       "engagement_file_count": eng["file_count"],
                       "broken": eng["broken"],
                       "posts_population": "owned-measured-root-posts",
                       "kind_source": "current-topic-shelf-account-preferred-shared-fallback",
                       "updated": jst.iso(now)},
    }


def answer(account_name: str | None = None, *, project: str | None = None,
           topic: str | None = None, kind: str | None = None,
           hour_band: str | None = None, reply_to: str | None = None,
           author_key: str | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
           min_n: int = DEFAULT_MIN_N, now=None, trusted_names=None,
           allowed_names=None) -> dict:
    """単一 account は従来の形、project は `by_account` に同じ節を並べる。"""
    if account_name and project:
        _reject("account と --project は同時に指定できません")
    if not account_name and not project:
        _reject("account か --project のどちらかが要ります")
    _validate_filters(kind=kind, hour_band=hour_band, author_key=author_key,
                      window_days=window_days, min_n=min_n)
    now = now if now is not None else jst.now_jst()
    names, top_cannot_say = _resolve_names(account_name=account_name, project=project,
                                            trusted_names=trusted_names)
    if project is None:
        return _account_answer(
            account_name, topic=topic, kind=kind, hour_band=hour_band,
            reply_to=reply_to, author_key=author_key, window_days=window_days,
            min_n=min_n, now=now, allowed_names=allowed_names)

    by_account = {}
    for name in names:
        try:
            by_account[name] = _account_answer(
                name, topic=topic, kind=kind, hour_band=hour_band,
                reply_to=reply_to, author_key=author_key, window_days=window_days,
                min_n=min_n, now=now, allowed_names=allowed_names)
        except accounts_mod.AccountError as exc:
            top_cannot_say.append(f"{name}: {exc}")
    return {
        "project": project, "by_account": by_account,
        "cannot_say": top_cannot_say,
        "provenance": {"source": SCHEMA_SOURCE, "updated": jst.iso(now)},
    }


def cmd_after(args) -> int:
    """`thth after <account> [--reply-to ID] [--author-key K] [--topic T]
    [--hour-band B] [--kind K] [--window-days N=30] [--min-n N=5] [--json]`。
    """
    as_json = bool(getattr(args, "json", False))
    try:
        result = answer(
            args.account, project=getattr(args, "project", None),
            topic=args.topic, kind=args.kind, hour_band=args.hour_band,
            reply_to=args.reply_to, author_key=args.author_key,
            window_days=args.window_days, min_n=args.min_n)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1
    except AfterError as e:
        print(str(e), file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    is_project = "by_account" in result
    nodes = result["by_account"] if is_project else {args.account: result}
    if is_project:
        print(f"after  project {result['project']}")
    for name, node in nodes.items():
        if is_project:
            print(f"\n[{name}]")
        print(node["summary"])
        eng = node["engagements"]
        print(f"  投稿 n={node['posts']['n']}  返信 n={eng['n']}  反応あり={eng['reacted']}")
        for line in node["cannot_say"]:
            print(f"  言えない: {line}")
        if node["one_thing_to_change"]:
            print(f"  変えるなら: {node['one_thing_to_change']}")
    for line in result.get("cannot_say", []) if is_project else []:
        print(f"言えない: {line}")
    return 0
