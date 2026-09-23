"""目的ごとの物差し（設計 3.6.0 §A2・`analytics-report --compare-previous --by goal`）。

**目的ごとに、その目的に合う物差しで比べる。** 読むだけ。台帳を書かない。

| goal   | 物差し（投稿単位） | 投稿単位に無いとき |
|--------|--------------------|--------------------|
| reach  | 24h・72h の views（Threads・X）、likes＋reposts（Bluesky・Mastodon） | 媒体に無い指標は null と `metric_unavailable` |
| click  | —（投稿単位のクリックは一次資料に無い） | `cannot_say: per_post_clicks_unavailable`。click 目的の投稿が 1 本だけの日に限り、日次の clicks を並べる（`basis: single_click_post_day`・観察の差） |
| follow | —（投稿単位のフォローは API に無い・3.3.0 の照合） | `cannot_say: per_post_follows_unavailable`。日次の followers_count の前日差を、follow 目的の投稿が出た日と出ていない日で並べる（観察の差・因果とは言わない） |
| reply  | 24h の replies と、返信した人の異なり数（自分の account を除く） | |

**割らない**（設計 D）: Threads の account 日次の `clicks` を投稿の本数で割ったり、
followers の増分を投稿に配ったりしない——一次資料に無い数を作ることになる。
日次との並べ方は必ず `observational_difference: true`・`causal: false` の印つき。
"""
from __future__ import annotations

import collections
import datetime

from . import analytics_comparison as comparison
from . import goals, jst

MARKS = (24, 72)
LIKES_PLUS_REPOSTS = "likes_plus_reposts"
# 日次との並べ方に必ず付ける印（因果ではない・観察の差）。
OBSERVATIONAL = {"observational_difference": True, "causal": False,
                 "note": "観察の差（因果ではない）"}


def _stat(values, total, min_n, reason=None):
    """中央値と分母。n が min_n に届かなければ null と理由。"""
    median = comparison._median(values) if len(values) >= min_n else None
    return {"median": median, "n_eligible": len(values), "n_total": total,
            "reason": reason if reason else (None if median is not None else "below_min_n")}


def _in_window(members, start, end):
    return [item for item in members if start <= item[1] < end]


def _reach(medium, members, start, end, now, min_n):
    metric = "views" if goals.primary_metrics("reach", medium) == ("views",) else LIKES_PLUS_REPOSTS
    window = _in_window(members, start, end)
    by_mark = {}
    for mark in MARKS:
        values = []
        for _post_id, posted, post in window:
            observation, _rejected = comparison._observation(post, posted, now, mark)
            metrics = (observation or {}).get("metrics") or {}
            if metric == "views":
                value = metrics.get("views")
            else:
                likes, reposts = metrics.get("likes"), metrics.get("reposts")
                value = likes + reposts if likes is not None and reposts is not None else None
            if value is not None:
                values.append(value)
        by_mark[str(mark)] = _stat(values, len(window), min_n)
    unavailable = {"views": "metric_unavailable"} if metric != "views" else {}
    return {"goal": "reach", "basis": "per_post", "metric": metric, "by_mark": by_mark,
            "metric_unavailable": unavailable}


def _distinct_repliers(name, post_id, posted, now):
    """24 時間の窓の中の返信した人の異なり数（自分の account を除く）。言えなければ理由。"""
    from . import replies as replies_mod
    end = posted + datetime.timedelta(hours=24)
    if now < end:
        return None, "not_yet_24h"
    try:
        loaded = replies_mod.load(name, post_id=post_id)
    except Exception:   # noqa: BLE001 — 読めない台帳は「言えない」で返す
        return None, "replies_unreadable"
    if loaded.get("broken"):
        return None, "replies_unreadable"
    fetched = [jst.parse(row.get("collected_at")) for row in loaded["fetches"]]
    if not any(at is not None and at >= end for at in fetched):
        # 24 時間の窓が閉じたあとに取った記録が無い——窓の中の返信を取り切ったと言えない。
        return None, "no_fetch_after_24h"
    people = set()
    for row in loaded["replies"]:
        at = jst.parse(row.get("timestamp"))
        if at is None or not posted <= at < end:
            continue
        if row.get("own") is None:
            return None, "reply_author_unknown"
        if row.get("own") is False and row.get("username"):
            people.add(str(row["username"]).lower())
    return len(people), None


def _reply(name, members, start, end, now, min_n):
    window = _in_window(members, start, end)
    replies, people, reasons = [], [], collections.Counter()
    for post_id, posted, post in window:
        observation, _rejected = comparison._observation(post, posted, now, 24)
        value = ((observation or {}).get("metrics") or {}).get("replies")
        if value is not None:
            replies.append(value)
        count, reason = _distinct_repliers(name, post_id, posted, now)
        if count is None:
            reasons[reason] += 1
        else:
            people.append(count)
    return {"goal": "reply", "basis": "per_post",
            "replies_24h": _stat(replies, len(window), min_n),
            "distinct_repliers_24h": {**_stat(people, len(window), min_n),
                                      "excludes": "own_accounts",
                                      "missing_reasons": dict(reasons)}}


def _day(posted):
    return jst.to_jst(posted).date().isoformat()


def _daily_index(account_daily):
    out = {}
    for row in account_daily or []:
        if isinstance(row, dict) and isinstance(row.get("date"), str):
            out[row["date"]] = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return out


def _click(members, goal_days, daily, start, end):
    """click: 投稿単位は言えない。click 目的の投稿が 1 本だけの日に限り日次の clicks を並べる。"""
    # **投稿単位のクリックは一次資料に無い**（設計 D）。日次の clicks をこの投稿に
    # 割り当てない——1 本だけの日の日次を並べるのは「その日の account 全体の数」として。
    per_post = None
    days, multiple, without = [], 0, 0
    for date in sorted({_day(posted) for _pid, posted, _post in _in_window(members, start, end)}):
        post_ids = goal_days.get(date, [])
        if len(post_ids) != 1:
            multiple += 1
            continue
        metrics = daily.get(date)
        if metrics is None or not isinstance(metrics.get("clicks"), (int, float)):
            without += 1
            continue
        days.append({"date": date, "post_id": post_ids[0], "clicks": metrics["clicks"],
                     "clicks_by_url": metrics.get("clicks_by_url")})
    return {"goal": "click", "basis": "account_daily", "per_post": per_post,
            "cannot_say": [goals.PER_POST_CANNOT_SAY["click"]],
            "daily": {"basis": "single_click_post_day", **OBSERVATIONAL, "days": days,
                      "n_days": len(days), "excluded_days_multiple_click_posts": multiple,
                      "days_without_daily_clicks": without,
                      "reason": None if daily else "account_daily_unavailable"}}


def _follow(goal_days, daily, start, end, min_n):
    """follow: 投稿単位は言えない。前日差を follow 目的の投稿が出た日と出ていない日で並べる。"""
    groups = {"with_goal_posts": [], "without_goal_posts": []}
    # 日は JST の日付で [開始日, 終了日)。日次は閉じた日だけ記録される（`collect`）。
    day, last = jst.to_jst(start).date(), jst.to_jst(end).date()
    while day < last:
        today, yesterday = day.isoformat(), (day - datetime.timedelta(days=1)).isoformat()
        now_count = (daily.get(today) or {}).get("followers_count")
        before = (daily.get(yesterday) or {}).get("followers_count")
        if isinstance(now_count, int) and isinstance(before, int) \
                and not isinstance(now_count, bool) and not isinstance(before, bool):
            key = "with_goal_posts" if goal_days.get(today) else "without_goal_posts"
            groups[key].append({"date": today, "delta": now_count - before})
        day += datetime.timedelta(days=1)
    out = {}
    for key, rows in groups.items():
        values = [row["delta"] for row in rows]
        out[key] = {"n_days": len(rows),
                    "median_delta": comparison._median(values) if len(values) >= min_n else None,
                    "days": rows}
    both = all(out[key]["median_delta"] is not None for key in out)
    difference = (out["with_goal_posts"]["median_delta"] - out["without_goal_posts"]["median_delta"]
                  if both else None)
    return {"goal": "follow", "basis": "account_daily", "per_post": None,
            "cannot_say": [goals.PER_POST_CANNOT_SAY["follow"]],
            "daily": {"basis": "followers_count_day_over_day", **OBSERVATIONAL, **out,
                      "difference_of_medians": difference,
                      "reason": (None if both else
                                 "account_daily_unavailable" if not daily else "insufficient_days")}}


def yardstick(goal, *, name, medium, members, goal_days, daily, start, end, now, min_n):
    """その目的の物差しを 1 期間ぶん。目的なし・不明は物差しを選ばない（None）。"""
    if goal == "reach":
        return _reach(medium, members, start, end, now, min_n)
    if goal == "reply":
        return _reply(name, members, start, end, now, min_n)
    if goal == "click":
        return _click(members, goal_days.get("click", {}), daily, start, end)
    if goal == "follow":
        return _follow(goal_days.get("follow", {}), daily, start, end, min_n)
    return None


def goal_days(items, recorded):
    """`{goal: {date: [post_id…]}}`——その日に出たその目的の投稿（account の根と返信を合わせて）。"""
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    seen = set()
    for post_id, posted, _post in items:
        if post_id in seen or posted is None:
            continue
        seen.add(post_id)
        goal = goals.goal_for(recorded, post_id)
        if goal in goals.GOALS:
            out[goal][_day(posted)].append(post_id)
    return {goal: {day: sorted(ids) for day, ids in days.items()} for goal, days in out.items()}


def strata(*, name, medium, items, all_items, account_daily, recorded,
           previous_start, current_start, now, min_n):
    """`--by goal` の層。目的ごとの母集団（既存の 24h の比較）と、目的ごとの物差し。"""
    groups = {label: [] for label in goals.LAYERS}
    for item in items:
        groups.setdefault(goals.goal_for(recorded, item[0]), []).append(item)
    days = goal_days(all_items, recorded)
    daily = _daily_index(account_daily)
    out = {}
    for label, members in groups.items():
        before = comparison._population(members, previous_start, current_start, now, min_n)
        after = comparison._population(members, current_start, now, now, min_n)
        out[label] = {"previous": before, "current": after,
                      "comparison": comparison._differences(before, after, min_n),
                      "label": goals.LABELS.get(label, label),
                      "yardstick": {
                          period: yardstick(label, name=name, medium=medium, members=members,
                                            goal_days=days, daily=daily, start=start, end=end,
                                            now=now, min_n=min_n)
                          for period, start, end in (("previous", previous_start, current_start),
                                                     ("current", current_start, now))}}
    return {"by": "goal", "strata": out,
            "goal_source": "recorded_at_publish",
            "goal_basis": ("公開の時点の記録（sent・連投の実行記録）。いまの原稿は読まない。"
                           "記録に goal の欄が無い投稿（3.6.0 より前）は unrecorded・"
                           "欄があって目的が無ければ none"),
            "reconciliation": {period: {
                "sum_n_total": sum(out[label][period]["n_total"] for label in out),
                "n_total": sum(1 for _pid, posted, _post in items
                               if (previous_start <= posted < current_start if period == "previous"
                                   else current_start <= posted < now))}
                for period in ("previous", "current")}}


def _fmt(value):
    return "判断不可" if value is None else value


def markdown_lines(stratified) -> list:
    """`render_markdown` に足す目的ごとの行（posts の現在期間）。"""
    lines = []
    for label, group in stratified["strata"].items():
        n = group["current"]["n_total"]
        yard = group["yardstick"]["current"]
        head = f"- goal {label}（{group['label']}）: 現在期間 n={n}"
        if yard is None:
            lines.append(head + "。物差しを選びません（目的が無い）")
        elif label == "reach":
            parts = [f"{mark}h {yard['metric']} 中央値 {_fmt(stat['median'])}（有効 n={stat['n_eligible']}/{stat['n_total']}）"
                     for mark, stat in yard["by_mark"].items()]
            lines.append(head + "。" + "・".join(parts))
        elif label == "reply":
            r, p = yard["replies_24h"], yard["distinct_repliers_24h"]
            lines.append(head + f"。24h replies 中央値 {_fmt(r['median'])}（有効 n={r['n_eligible']}）"
                         f"・返信した人の異なり数 中央値 {_fmt(p['median'])}（有効 n={p['n_eligible']}・自分を除く）")
        elif label == "click":
            daily = yard["daily"]
            lines.append(head + "。投稿単位のクリックは言えません（per_post_clicks_unavailable）。"
                         f"click の投稿が 1 本だけの日の日次 clicks: {daily['n_days']} 日"
                         f"（2 本以上の日 {daily['excluded_days_multiple_click_posts']} 日は並べない・"
                         f"{daily['note']}）")
        elif label == "follow":
            daily = yard["daily"]
            with_, without = daily["with_goal_posts"], daily["without_goal_posts"]
            lines.append(head + "。投稿単位のフォローは言えません（per_post_follows_unavailable）。"
                         f"followers の前日差の中央値: follow の投稿が出た日 {_fmt(with_['median_delta'])}"
                         f"（{with_['n_days']} 日）・出ていない日 {_fmt(without['median_delta'])}"
                         f"（{without['n_days']} 日）——{daily['note']}")
    return lines
