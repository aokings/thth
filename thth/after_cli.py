"""`after_you_posted`: 出したあとに、絡みに行った返信がどう受け取られたかを
返す（設計「自分の泉」§2.2・§4・T0-2 tracer）。

T0 で出すのは §2.2 の `engagements` の節だけ——`posts`（自分の投稿そのものの
実績）は T1 以降。**残すのは自分の行為と反応だけ**（設計 §4）: 絡みの台帳
（`thth/engagements.py`）の行と、`thth/measured.py` の実測（`marks` に 24 を
含む行）・`thth/replies.py` の返信台帳を結ぶだけで、本文・username は出力に
1 バイトも入らない。

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
import statistics
import sys

from . import accounts as accounts_mod
from . import engagements as engagements_mod
from . import jst
from . import measured as measured_mod
from . import queuefile as queuefile_mod
from . import replies as replies_mod
from . import threadshape as threadshape_mod

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


def _replies_back_from_ledger(account_name: str, post_id: str | None):
    """`metrics.replies` が無いときの二段目: 返信の台帳の他者返信の数。

    **一度も取得していない（台帳にその post_id の記録が無い）のか、取得して
    0 件だったのかを区別できないときは `None`**（判らないものを 0 にしない）。
    """
    if not post_id:
        return None
    try:
        result = replies_mod.load(account_name, post_id=post_id)
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


def answer(account_name: str, *, topic: str | None = None, kind: str | None = None,
           hour_band: str | None = None, reply_to: str | None = None,
           author_key: str | None = None, window_days: int = DEFAULT_WINDOW_DAYS,
           min_n: int = DEFAULT_MIN_N, now=None) -> dict:
    """`after_you_posted` の答え（設計「自分の泉」§2.2 の `engagements` の節）。

    `kind` は受け取るが、絡みの台帳に `kind` の欄が無いので**絞り込みには
    使わない**（T0 発注書の inputSchema と CLI 引数一覧の食い違い。報告参照）。
    """
    if not isinstance(window_days, int) or window_days <= 0:
        _reject(f"期間（--window-days）は 1 以上の整数です: {window_days!r}")
    if not isinstance(min_n, int) or min_n <= 0:
        _reject(f"下限（--min-n）は 1 以上の整数です: {min_n!r}")
    if hour_band is not None and hour_band not in HOUR_BAND_NAMES:
        _reject(f"時刻帯は {list(HOUR_BAND_NAMES)} のどれかです: {hour_band!r}")
    if author_key is not None and not engagements_mod.AUTHOR_KEY_RE.match(author_key):
        _reject(f"author_key は 16 進 16 桁です: {author_key!r}")

    now = now if now is not None else jst.now_jst()
    account_cfg = accounts_mod.load_account(account_name)
    since = now - datetime.timedelta(days=window_days)

    topic_norm = queuefile_mod.normalize_topic(topic) if topic is not None else None

    eng = engagements_mod.load(account_cfg, account_name)
    rows = eng["rows"]

    filtered = []
    for row in rows:
        posted_dt = jst.parse(row.get("posted_at"))
        if posted_dt is None or posted_dt < since:
            continue
        if reply_to is not None and row.get("reply_to") != reply_to:
            continue
        if author_key is not None and row.get("author_key") != author_key:
            continue
        if topic_norm is not None and row.get("topic") != topic_norm:
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
                            else _replies_back_from_ledger(account_name, post_id))
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

    n = len(by_branch)
    reacted = sum(1 for b in by_branch if _reacted(b))

    likes_values = [b["likes_24h"] for b in by_branch if b["likes_24h"] is not None]
    replies_values = [b["replies_back_24h"] for b in by_branch
                      if b["replies_back_24h"] is not None]
    views_values = [b["views_24h"] for b in by_branch if b["views_24h"] is not None]

    cannot_say: list = []
    if uncovered:
        cannot_say.append(f"24h の刻みが未採取: {uncovered} 本")

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
    summary = (f"{頭}絡みに行った返信 {n} 本のうち反応あり {reacted}"
              f"（直近 {window_days} 日）")

    return {
        "summary": summary,
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
                       "broken": eng["broken"], "updated": jst.iso(now)},
    }


def cmd_after(args) -> int:
    """`thth after <account> [--reply-to ID] [--author-key K] [--topic T]
    [--hour-band B] [--kind K] [--window-days N=30] [--min-n N=5] [--json]`。
    """
    as_json = bool(getattr(args, "json", False))
    try:
        result = answer(
            args.account, topic=args.topic, kind=args.kind, hour_band=args.hour_band,
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

    print(result["summary"])
    eng = result["engagements"]
    print(f"  n={eng['n']}  反応あり={eng['reacted']}")
    if result["cannot_say"]:
        for line in result["cannot_say"]:
            print(f"  言えない: {line}")
    if result["one_thing_to_change"]:
        print(f"  変えるなら: {result['one_thing_to_change']}")
    return 0
