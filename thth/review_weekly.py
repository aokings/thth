"""週の振り返りの材料（`thth review weekly <口座|project> [--week YYYY-MM-DD] [--json]`・2026-11-25）。

依頼 `docs/依頼_THTH開発_週次の振り返りの材料とskill_2026-11-25.md`。週の結果を前の週と並べ、
「落ちた」を印にして 1 回で渡す。回し方は skill `thth-weekly-review`。

**数え方は作り直さない。** 既にある計算を束ねるだけ:

- 根の投稿の母集団と 24 時間の観測: `analytics_comparison._items`・`_population`（`analytics-report
  --compare-previous` と同じ。24 時間以上 30 時間未満の最初の観測・欠測と未成熟は 0 にしない）。
- 層（型・時刻帯・goal・添付の種類。リンクの有無は添付の種類の `link`）: `analytics_comparison._account`
  の `--by` と同じ計算を、過ぎた週の終わりで閉じて呼ぶ。
- フォロワーの増え: `analytics_weekly`（`--weekly-goals` の週の表・前週の日曜と週の最終日の差）。
- クリック: `analytics_clicks`（`--per-post-clicks`・Threads だけ）。
- 先週の宣言: `study_report`（`study-report` と同じ）。

**原因と推奨は出さない**（`analytics-report` と同じ）。落ち込みの印は「前の 2 週の平均の半分を切った」と
いう機械の印で、良い悪いの判断ではない。読むだけ。台帳を書かない。
"""
from __future__ import annotations

import datetime
import json
import sys

from . import accounts, after_cli, jst

WEEKS_BEFORE = 4
DROP_RATIO = 0.5
DROP_BASIS = "その週の値が、前の 2 週の平均の半分を切ったら dropped（平均が 0 以下・どれかが欠けたら判断しない）"
TOP_N = 5
# 判断しない理由（断りの符丁ではない。`--json` の reason の値）。
FEWER_WEEKS = "fewer_than_2_previous_weeks"
METRICS = ("views", "likes", "replies", "reposts")
LABELS = {"views": "表示", "likes": "いいね", "replies": "返信", "reposts": "拡散",
          "followers_delta": "フォロワーの増え"}
STRATA_BY = ("kind", "hour_band", "goal", "attachment_kind")
STRATA_LABELS = {"kind": "型", "hour_band": "時刻帯", "goal": "目的（goal）",
                 "attachment_kind": "添付の種類（リンクの有無は link の層）"}


class ReviewRefused(ValueError):
    """前向きな断り（符丁は目録 `refusal_catalog_part_review`）。"""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def refuse(code, message):
    """断る（`tools/count_refusals.py` が第 1 引数の符丁を拾う口）。"""
    raise ReviewRefused(code, message)


def _monday(day: datetime.date) -> datetime.date:
    return day - datetime.timedelta(days=day.weekday())


def _midnight(day: datetime.date) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, tzinfo=jst.JST)


def resolve_week(week, now) -> datetime.date:
    """週の月曜。省けば閉じた最後の週（今の週の前の週）。どの曜日の日付でもその週の月曜にそろえる。"""
    this_monday = _monday(now.date())
    if week is None:
        return this_monday - datetime.timedelta(days=7)
    try:
        day = datetime.date.fromisoformat(week) if isinstance(week, str) else None
    except ValueError:
        day = None
    if day is None:
        refuse("review_week_invalid", "--week は YYYY-MM-DD の日付です")
    monday = _monday(day)
    if monday > this_monday:
        refuse("review_week_invalid", "--week がまだ来ていない週です")
    oldest = this_monday - datetime.timedelta(weeks=_weekly_max() - WEEKS_BEFORE - 1)
    if monday < oldest:
        refuse("review_week_invalid",
                            f"--week は {oldest.isoformat()} 以降の週です（週の表の上限）")
    return monday


def _weekly_max():
    from . import analytics_weekly
    return analytics_weekly.MAX_WEEKS


def _resolve_target(target):
    """口座の名前ならその 1 つ、project の名前ならその口座の全部。"""
    if not isinstance(target, str) or not target.strip():
        refuse("review_target_unknown", "口座か project の名前が要ります")
    try:
        accounts.load_account(target)
        return "account", [target], []
    except accounts.AccountError:
        pass
    names, cannot_say = after_cli._resolve_names(account_name=None, project=target)
    if not names:
        refuse("review_target_unknown",
                            "その名前の口座も project も見つかりません")
    return "project", names, cannot_say


# ------------------------------------------------------------ 週ごとの数

def _week_numbers(loaded, monday, now, min_n, ids=None):
    from . import analytics_comparison as comparison
    start = _midnight(monday)
    end = _midnight(monday + datetime.timedelta(days=7))
    roots = loaded["root_items"] if ids is None else [item for item in loaded["root_items"] if item[0] in ids]
    population = comparison._population(roots, start, end, now, min_n)
    eligible = [row for row in population["evidence"] if row["observation"]]
    totals = {}
    for key in METRICS:
        values = [row["observation"]["metrics"][key] for row in eligible
                  if row["observation"]["metrics"][key] is not None]
        if population["n_total"] == 0:
            reason = "no_root_posts_in_week"
        elif not values:
            reason = "no_24h_observation_in_week"
        else:
            reason = None
        totals[key] = {"sum": sum(values) if reason is None else None, "n": len(values),
                       "reason": reason}
    views = [row["observation"]["metrics"]["views"] for row in eligible
             if row["observation"]["metrics"]["views"] is not None]
    own_thread = sum(1 for post_id, post in loaded["by_id"].items()
                     if post.get("reply_to") and post_id not in loaded["own_engagements"]
                     and (ids is None or post_id in ids) and _in(post.get("posted_at"), start, end))
    # 他の人への返信はシリーズや糸の原稿に結ばれない（単位で切るときは null）。
    others = None if ids is not None else sum(1 for _pid, posted, _post in loaded["reply_items"]
                                              if posted is not None and start <= posted < end)
    not_counted = {
        "immature": [row["post_id"] for row in population["evidence"] if row["status"] == "immature"],
        "incomplete": [row["post_id"] for row in population["evidence"] if row["status"] == "incomplete"],
    }
    return {"week_start": monday.isoformat(),
            "week_end": (monday + datetime.timedelta(days=6)).isoformat(),
            "partial": end > now,
            "posts": {"roots": population["n_total"], "own_thread_replies": own_thread,
                      "replies_to_others": others},
            "totals": totals,
            "per_post_24h": {"views_median": population["metrics"]["views"]["median"],
                             "views_max": max(views) if views else None,
                             "n": len(views), "min_n": min_n,
                             "median_reason": None if len(views) >= min_n else "below_min_n"},
            "n_counted": len(eligible), "n_immature": population["n_immature"],
            "n_incomplete": population["n_incomplete"], "not_counted": not_counted,
            "_population": population}


def _in(raw, start, end):
    from .analytics_comparison import _timestamp
    at = _timestamp(raw)
    return at is not None and start <= at < end


def _followers(name, mondays, now):
    from . import analytics_weekly
    this_monday = _monday(now.date())
    weeks = (this_monday - min(mondays)).days // 7 + 1
    table = analytics_weekly.answer(name, weeks=weeks, now=now)
    by_start = {row["week_start"]: row["followers"] for row in table["weeks"]}
    out = {}
    for monday in mondays:
        f = by_start.get(monday.isoformat()) or {}
        out[monday.isoformat()] = {"delta": f.get("delta"), "start": f.get("start"),
                                   "end": f.get("end"), "start_date": f.get("start_date"),
                                   "end_date": f.get("end_date"), "reason": f.get("reason")}
    return out


def _click_rows(name, medium, mondays, now):
    """Threads の投稿ごとの 72 時間のクリック（`analytics_clicks`）の行。`(行, 理由)`。ほかの媒体は行なしと理由。"""
    if medium != "threads":
        return [], f"per_post_clicks_unavailable:{medium}"
    from . import analytics_clicks
    payload = analytics_clicks.answer(name, since=jst.iso(_midnight(min(mondays))), now=now)
    return payload["posts"], None


def _bucket_clicks(found, week_start, ids=None):
    """クリックの行をその週（と、単位なら post_id）に絞って足す。"""
    rows, reason = found if found is not None else ([], "per_post_clicks_unavailable")
    if reason is not None:
        return {"sum": None, "n": 0, "reason": reason}
    monday = datetime.date.fromisoformat(week_start)
    start, end = _midnight(monday), _midnight(monday + datetime.timedelta(days=7))
    values = [row["clicks_72h"] for row in rows
              if _in(row["posted_at"], start, end) and row["clicks_72h"] is not None
              and (ids is None or str(row["post_id"]) in ids)]
    return {"sum": sum(values) if values else None, "n": len(values),
            "reason": None if values else "no_attributable_clicks_in_week"}


def _streak(drops):
    streak = 0
    for result in reversed(drops):
        if result["drop"] is not True:
            break
        streak += 1
    return streak


def _drop(rows, index, metrics=("views", "followers_delta")):
    """前の 2 週の平均の半分を切ったか（表示の和・フォロワーの増え。シリーズと糸は表示の和だけ）。"""
    if index < 2:
        return {"drop": None, "signals": [], "reason": FEWER_WEEKS}
    signals = []
    for metric in metrics:
        def value(row):
            return row["totals"]["views"]["sum"] if metric == "views" else row["followers"]["delta"]
        current = value(rows[index])
        previous = [value(rows[index - 1]), value(rows[index - 2])]
        signal = {"metric": metric, "label": LABELS[metric], "value": current,
                  "previous_2_weeks": previous, "previous_average": None, "ratio": None,
                  "change": None, "dropped": None, "reason": None}
        if current is None or None in previous:
            signal["reason"] = "value_missing"
        else:
            average = sum(previous) / 2
            signal["previous_average"] = average
            if average <= 0:
                signal["reason"] = "previous_average_not_positive"
            else:
                signal.update(ratio=round(current / average, 3), change=current - average,
                              dropped=current < average * DROP_RATIO)
        signals.append(signal)
    judged = [s["dropped"] for s in signals if s["dropped"] is not None]
    return {"drop": (True if any(judged) else False) if judged else None,
            "signals": signals, "reason": None if judged else "no_metric_judged"}


# ------------------------------------------------------------ 当たりと外れ・層

def _post_row(name, row, by_id, recorded, kind_of):
    from . import goals, threadshape
    from .analytics_comparison import _timestamp
    post = by_id.get(row["post_id"]) or {}
    observation = row["observation"] or {}
    kinds = observation.get("attachment_kinds")
    topic, valid = after_cli._normalized_topic(post.get("topic"))
    return {"post_id": row["post_id"], "file": post.get("file"), "posted_at": row["posted_at"],
            "topic": topic if valid else None, "kind": kind_of(topic) if valid else None,
            "goal": goals.goal_for(recorded, row["post_id"]),
            "hour_band": threadshape.hour_band(_timestamp(row["posted_at"])),
            "has_link": ("link" in kinds) if isinstance(kinds, list) else None,
            "attachment_kinds": kinds if isinstance(kinds, list) else None,
            "metrics_24h": observation.get("metrics")}


def _hits(name, population, by_id):
    from . import goals
    recorded = goals.recorded_goals(name)
    kind_of, _broken = after_cli._kind_lookup(name)
    eligible = [row for row in population["evidence"]
                if row["observation"] and row["observation"]["metrics"]["views"] is not None]
    ranked = sorted(eligible, key=lambda r: (-r["observation"]["metrics"]["views"], r["post_id"]))
    top = ranked[:TOP_N]
    top_ids = {row["post_id"] for row in top}
    bottom = [row for row in reversed(ranked) if row["post_id"] not in top_ids][:TOP_N]
    return ([_post_row(name, row, by_id, recorded, kind_of) for row in top],
            [_post_row(name, row, by_id, recorded, kind_of) for row in bottom])


def _brief(population):
    return {"n_total": population["n_total"], "n_eligible": population["n_eligible"],
            "n_missing": population["n_missing"],
            "views_median": population["metrics"]["views"]["median"],
            "likes_median": population["metrics"]["likes"]["median"]}


def _strata(name, monday, now, min_n):
    from . import analytics_comparison as comparison
    start = _midnight(monday)
    previous_start = start - datetime.timedelta(days=7)
    end = _midnight(monday + datetime.timedelta(days=7))
    out = {}
    for by in STRATA_BY:
        node = comparison._account(name, previous_start, start, now, min_n, by, end=min(end, now))
        stratified = node["posts"]["stratified"]
        out[by] = {"label": STRATA_LABELS[by],
                   "strata": {label: {"this_week": _brief(group["current"]),
                                      "previous_week": _brief(group["previous"])}
                              for label, group in stratified["strata"].items()
                              if group["current"]["n_total"] or group["previous"]["n_total"]}}
    return out


# ------------------------------------------------------------ 先週の宣言

def _studies(paths, names, now, min_n):
    from . import study_report
    out = []
    for path in paths or ():
        try:
            result = study_report.answer(path, min_n=min_n, now=now)
        except (study_report.StudyError, after_cli.AfterError) as exc:
            out.append({"file": path, "error": str(exc)})
            continue
        declaration = result["declaration"]
        if declaration["account"] not in names:
            out.append({"file": path, "error": "宣言の口座がこの振り返りの対象にありません"})
            continue
        observations = result["observations"] or {}
        out.append({"file": path, "account": declaration["account"], "id": declaration["id"],
                    "hypothesis": declaration["hypothesis"], "change": declaration["change"],
                    "period": declaration.get("period"), "status": declaration["decision"]["status"],
                    "selection": result["selection"],
                    "groups": {group: _brief(population) for group, population in observations.items()},
                    "comparison": result["comparison"], "cannot_say": result["cannot_say"]})
    return out


# ------------------------------------------------------------ 束ねる

def _mondays(monday):
    return [monday - datetime.timedelta(weeks=back) for back in range(WEEKS_BEFORE, -1, -1)]


def _node(name, monday, now, min_n, cache=None):
    from . import analytics_comparison as comparison
    loaded = comparison._items(name)
    medium = loaded["cfg"].get("media")
    mondays = _mondays(monday)
    rows = [_week_numbers(loaded, m, now, min_n) for m in mondays]
    followers = _followers(name, mondays, now)
    clicks = _click_rows(name, medium, mondays, now)
    if cache is not None:
        cache[name] = (loaded, clicks)     # シリーズと糸の単位が同じ台帳を使う（読み直さない）
    for row in rows:
        row["followers"] = followers[row["week_start"]]
        row["clicks_72h"] = _bucket_clicks(clicks, row["week_start"])
    drops = [_drop(rows, i) for i in range(len(rows))]
    streak = _streak(drops)
    target = rows[-1]
    population = target.pop("_population")
    for row in rows[:-1]:
        row.pop("_population")
    for row, result in zip(rows, drops):
        row["drop"] = result["drop"]
    top, bottom = _hits(name, population, loaded["by_id"])
    cannot_say = []
    broken = len(loaded["measured"].get("broken", [])) + (loaded["engagements"].get("broken", 0) or 0)
    if broken:
        cannot_say.append("読めない台帳があり、週の本数と和が足りないかもしれません")
    if target["n_immature"] or target["n_incomplete"]:
        cannot_say.append("その週に 24 時間の数が無い投稿があり、和はその分だけ小さく出ています（0 とは数えていない）")
    if target["partial"]:
        cannot_say.append("その週はまだ閉じていません（途中の数）")
    return {"medium": medium, "week": target, "weeks": rows,
            "drop": drops[-1]["drop"], "drop_streak": streak,
            "drop_detail": drops[-1],
            "top": top, "bottom": bottom,
            "strata": _strata(name, monday, now, min_n),
            "cannot_say": cannot_say}


from .report_details import detailed


@detailed
def answer(target, *, week=None, now=None, min_n=after_cli.DEFAULT_MIN_N, studies=()):
    """週の振り返りの材料（CLI の文と `--json` が同じものから作る）。"""
    if type(min_n) is not int or min_n < 1:
        raise after_cli.AfterError("min_n は 1 以上の整数です")
    now = jst.to_jst(now if now is not None else jst.now_jst()).replace(microsecond=0)
    monday = resolve_week(week, now)
    scope, names, cannot_say = _resolve_target(target)
    nodes, cache = {}, {}
    for name in names:
        try:
            nodes[name] = _node(name, monday, now, min_n, cache)
        except accounts.AccountError as exc:
            if scope == "account":
                raise
            cannot_say.append(f"{name}: {exc}")
    if not nodes:
        refuse("review_target_unknown", "読める口座がありません")
    from . import review_units
    units = review_units.build(target, list(nodes), _mondays(monday), now, min_n,
                               {name: cache[name][0] for name in cache},
                               {name: cache[name][1] for name in cache})
    drop_units = [{"unit": "account", "id": name, "account": name}
                  for name, node in sorted(nodes.items()) if node["drop"] is True]
    for unit, key in (("series", "series"), ("campaign", "campaigns")):
        for entry in units[key]:
            for name, node in entry["by_account"].items():
                if node["drop"] is True:
                    drop_units.append({"unit": unit, "id": entry["id"], "account": name})
    cannot_say += [f"scenario: {code}" for code in units["cannot_say"]]
    return {"schema_version": 1, "report_type": "weekly_review",
            "generated_at": jst.iso(now), "target": target, "scope": scope,
            "week": {"start": monday.isoformat(),
                     "end": (monday + datetime.timedelta(days=6)).isoformat(),
                     "basis": "月曜始まり（JST）・posted_at の週", "weeks_before": WEEKS_BEFORE},
            "min_n": min_n, "drop_rule": {"ratio": DROP_RATIO, "basis": DROP_BASIS,
                                         "metrics": ["views", "followers_delta"]},
            # 印は 3 つの単位（口座・シリーズ・糸）それぞれ。`drop` はどれか 1 つでも立てば true。
            "drop": bool(drop_units),
            "drop_accounts": sorted(name for name, node in nodes.items() if node["drop"] is True),
            "drop_units": drop_units,
            "by_account": nodes,
            "series": units["series"], "campaigns": units["campaigns"], "seeds": units["seeds"],
            "studies": _studies(studies, set(nodes), now, min_n),
            "cannot_say": cannot_say,
            "limitations": [
                "原因・推奨・次の手は出さない。読むのは利用者とその LLM",
                "表示・いいね・返信・拡散の和は、根の投稿の 24 時間以上 30 時間未満の最初の観測の和"
                "（analytics-report --compare-previous と同じ測り方）。24 時間の数が無い投稿は和に入れず、0 とも数えない",
                "フォロワーの増えは前週の日曜と週の最終日の差（analytics-report --weekly-goals と同じ）。欠ければ null",
                "クリックは Threads の投稿ごとの 72 時間（analytics-report --per-post-clicks と同じ）。ほかの媒体は null",
                "落ち込みの印は前の 2 週の平均との機械の比べ。良い悪いの判断ではない",
                "媒体・口座をまたいだ和や順位は作らない",
                "シリーズと糸は thth scenario の割り当て（原稿の series:・thread:）のまま。札の無い原稿は未分類。"
                "シリーズと糸の印は表示の和だけで見る（フォロワーの増えは単位に割れない）",
            ],
            "provenance": {"source": "own-ledgers",
                           "calculation": "analytics_comparison・analytics_weekly・analytics_clicks・study_report・scenario・scenario_seeds",
                           "advice": "excluded"}}


# ------------------------------------------------------------ 人の読む形

def _n(value):
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def _signed(value):
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + _n(value)


REASON_JA = {
    "no_root_posts_in_week": "根の投稿が無い週",
    "no_24h_observation_in_week": "24 時間の数がまだ無い",
    "value_missing": "数が欠けていて比べられない",
    "previous_average_not_positive": "前の 2 週の平均が 0 以下で比べられない",
    "fewer_than_2_previous_weeks": "前の 2 週が無い",
    "no_metric_judged": "比べられる数が無い",
    "week_not_closed": "週がまだ閉じていない",
    "followers_missing_start": "週の初めのフォロワー数が無い",
    "followers_missing_end": "週の終わりのフォロワー数が無い",
    "below_min_n": "本数が少なく中央値を出さない",
}


def _why(reason):
    if reason is None:
        return ""
    if reason.startswith("per_post_clicks_unavailable:"):
        return "この媒体は投稿ごとのクリックを取れない"
    return REASON_JA.get(reason, reason)


def render_text(payload) -> str:
    from .analytics_report import _markdown_text as t
    week = payload["week"]
    lines = [f"# 週の振り返りの材料 {t(payload['target'])}（{week['start']} の週・{week['start']}〜{week['end']}）", "",
             "THTH は数を並べるだけで、原因と次の手は出しません。決めるのは読む人です"
             "（回し方は skill thth-weekly-review）。", ""]
    for name, node in payload["by_account"].items():
        target = node["week"]
        lines += [f"## {t(name)}（{t(node['medium'])}）", ""]
        if node["drop"] is True:
            more = f"・{node['drop_streak']} 週続いています" if node["drop_streak"] >= 2 else ""
            lines.append(f"**落ち込みの印: あり**（前の 2 週の平均の半分を切りました{more}）")
        elif node["drop"] is False:
            lines.append("落ち込みの印: なし（前の 2 週の平均の半分は切っていません）")
        else:
            lines.append(f"落ち込みの印: 判断できません（{_why(node['drop_detail']['reason'])}）")
        for s in node["drop_detail"]["signals"]:
            if s["dropped"] is None:
                lines.append(f"- {s['label']}: {_n(s['value'])}（{_why(s['reason'])}）")
            else:
                mark = "半分を切った" if s["dropped"] else "半分は切っていない"
                lines.append(f"- {s['label']}: {_signed(s['value']) if s['metric'] == 'followers_delta' else _n(s['value'])}。"
                             f"前の 2 週の平均 {_n(s['previous_average'])} の {s['ratio']} 倍"
                             f"（{_signed(s['change'])}・{mark}）")
        lines += ["", "### 週の並び", "",
                  "| 週の始まり | 根 | 自分への返信 | 他の人への返信 | 表示 | いいね | 返信 | 拡散 | フォロワーの増え | クリック | 24 時間の数が無い | 印 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in node["weeks"]:
            posts, totals = row["posts"], row["totals"]
            mark = {True: "落ち込み", False: "", None: "—"}[row["drop"]]
            lines.append(
                f"| {row['week_start']}{'（途中）' if row['partial'] else ''} | {posts['roots']} | "
                f"{posts['own_thread_replies']} | {posts['replies_to_others']} | "
                + " | ".join(_n(totals[k]["sum"]) for k in METRICS)
                + f" | {_signed(row['followers']['delta'])} | {_n(row['clicks_72h']['sum'])} | "
                f"{row['n_immature'] + row['n_incomplete']} | {mark} |")
        per = target["per_post_24h"]
        median = (_n(per["views_median"]) if per["views_median"] is not None
                  else f"出さない（{_why(per['median_reason'])}・{per['n']} 本）")
        lines += ["", "### 1 本あたり（24 時間の表示）", "",
                  f"中央値 {median}・最大 {_n(per['views_max'])}（数えた {per['n']} 本）", ""]
        for title, rows in (("当たり（表示の上位 5 本）", node["top"]), ("外れ（表示の下位 5 本）", node["bottom"])):
            lines += [f"### {title}", ""]
            if not rows:
                empty = ("（数えた投稿が 5 本以下で、当たりと重なるので出しません）" if node["top"]
                         else "（24 時間の数がある投稿がありません）")
                lines += [empty, ""]
                continue
            lines += ["| 原稿 | 型 | topic | goal | 時刻帯 | リンク | 添付 | 表示 | いいね | 返信 |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
            for r in rows:
                m = r["metrics_24h"] or {}
                link = {True: "あり", False: "なし", None: "不明"}[r["has_link"]]
                kinds = "・".join(r["attachment_kinds"]) if r["attachment_kinds"] else "不明"
                lines.append(f"| {t(r['file'] or r['post_id'])} | {t(r['kind'] or '—')} | {t(r['topic'] or '—')} | "
                             f"{t(r['goal'])} | {t(r['hour_band'] or '—')} | {link} | {t(kinds)} | "
                             f"{_n(m.get('views'))} | {_n(m.get('likes'))} | {_n(m.get('replies'))} |")
            lines.append("")
        lines += ["### 層ごとの表示の中央値（その週 ← 前の週）", ""]
        for by, group in node["strata"].items():
            cells = [f"{t(label)} {_n(v['this_week']['views_median'])}（{v['this_week']['n_eligible']} 本）"
                     f" ← {_n(v['previous_week']['views_median'])}（{v['previous_week']['n_eligible']} 本）"
                     for label, v in group["strata"].items()]
            lines.append(f"- {group['label']}: " + ("・".join(cells) if cells else "投稿なし"))
        lines += ["", f"中央値は {payload['min_n']} 本に満たない層では出しません（—）。", ""]
        lines += ["### 数えていない投稿（0 とは数えていません）", ""]
        nc = target["not_counted"]
        lines.append(f"- 24 時間に満たない（未成熟）: {len(nc['immature'])} 本"
                     + (f"（{t('・'.join(nc['immature']))}）" if nc["immature"] else ""))
        lines.append(f"- 24 時間の数が採れていない（欠測）: {len(nc['incomplete'])} 本"
                     + (f"（{t('・'.join(nc['incomplete']))}）" if nc["incomplete"] else ""))
        lines += ["- " + t(reason) for reason in node["cannot_say"]]
        lines.append("")
    lines += _units_text(payload, t)
    lines += ["## 先週の宣言（study）", ""]
    if not payload["studies"]:
        lines += ["渡されていません。先週 thth study add で宣言したなら、--study <施策JSON> を付けると"
                  "その結果もここに並びます。", ""]
    for study in payload["studies"]:
        if "error" in study:
            lines.append(f"- {t(study['file'])}: 読めませんでした（{t(study['error'])}）。"
                         "ファイルの場所と形を thth study-report <施策JSON> で確かめてください")
            continue
        lines.append(f"- {t(study['id'])}（{t(study['account'])}）: 変えたこと「{t(study['change'])}」")
        for group, label in (("baseline", "比べる前"), ("changed", "変えたあと")):
            g = study["groups"].get(group)
            if g:
                lines.append(f"  - {label}: {g['n_total']} 本（数えた {g['n_eligible']}・数えていない {g['n_missing']}）"
                             f"・表示の中央値 {_n(g['views_median'])}")
        delta = (study["comparison"] or {}).get("views", {}).get("absolute_median_change")
        lines.append(f"  - 表示の中央値の差: {_signed(delta) if delta is not None else '比べられない（本数が足りない）'}（観測の差で、効果とは言えない）")
    if payload["studies"]:
        lines.append("")
    lines += ["## この材料で言えないこと", ""]
    lines += ["- " + t(item) for item in payload["limitations"] + payload["cannot_say"]]
    return "\n".join(lines) + "\n"


STATE_JA = {"active": "動いている", "stalled": "止まっている", "done": "全部の段が済んだ", "closed": "閉じた"}


def _unit_line(t, label, name, node):
    week, detail = node["week"], node["drop_detail"]
    signal = next(iter(detail["signals"]), None)
    if node["drop"] is True:
        mark = "落ち込み" + (f"（{node['drop_streak']} 週続き）" if node["drop_streak"] >= 2 else "")
    elif node["drop"] is False:
        mark = "なし"
    else:
        mark = "判断できない" + (f"（{_why(signal['reason'])}）" if signal and signal["reason"] else
                              f"（{_why(detail['reason'])}）")
    average = _n(signal["previous_average"]) if signal else "—"
    return (f"| {t(label)} | {t(name)} | {week['posts']['roots']} | {_n(week['totals']['views']['sum'])} | "
            f"{_n(week['per_post_24h']['views_median'])} | {average} | "
            f"{week['n_immature'] + week['n_incomplete']} | {mark} |")


def _units_text(payload, t):
    lines = ["## シリーズ（ルーチン投稿）ごと", "",
             "シリーズは thth scenario の割り当て（原稿の series:）。札の無い原稿は「未分類」に束ねています。"
             "印は表示の和だけで見ます。", "",
             "| シリーズ | 口座 | 根 | 表示 | 1 本あたりの中央値 | 前の 2 週の平均 | 24 時間の数が無い | 印 |",
             "|---|---|---|---|---|---|---|---|"]
    rows = 0
    for entry in payload["series"]:
        label = entry["label"] + ("" if entry["declared"] or entry["unclassified"] else "（宣言なし）")
        for name, node in entry["by_account"].items():
            lines.append(_unit_line(t, label, name, node))
            rows += 1
    if not rows:
        lines.append("| （出した原稿がありません） | | | | | | | |")
    lines += ["", "## キャンペーン（糸）ごと", ""]
    if not payload["campaigns"]:
        lines += ["宣言した糸がありません（thth scenario <project> で確かめられます）。", ""]
    for entry in payload["campaigns"]:
        stage = entry["stage"]
        title = f"{t(entry['id'])}" + (f"「{t(entry['title'])}」" if entry["title"] else "")
        lines.append(f"### {title}（{STATE_JA.get(entry['state'], entry['state'])}・"
                     f"段 {stage['index']}/{stage['of']} {t(stage['name'] or '—')}）")
        steps = []
        for row in entry["stages"]:
            done = row["replies"] if row.get("need") is None else row.get("done")
            need = row.get("need")
            steps.append(f"{t(row['label'] or row['name'])} {_n(done)}"
                         + (f"/{need}" if need is not None else "") + ("（済み）" if row["satisfied"] else ""))
        lines.append("- 段: " + "・".join(steps))
        if entry["stalled"]:
            lines.append(f"- 止まり: {t(entry['stalled'].get('label') or '')}")
        for name, node in entry["by_account"].items():
            if entry["state"] == "closed":
                break      # 閉じた糸はその週の数でなく、下の結果を並べる
            week = node["week"]
            lines.append(f"- {t(name)}: その週 {week['posts']['roots']} 本・表示 {_n(week['totals']['views']['sum'])}"
                         f"・返信 {_n(week['totals']['replies']['sum'])}"
                         + ("" if entry["state"] == "closed" else
                            f"・印 {({True: '落ち込み', False: 'なし', None: '判断できない'})[node['drop']]}"))
        if entry["state"] == "closed":
            result = entry["result"]
            for name, r in result["by_account"].items():
                lines.append(f"- 結果 {t(name)}: {r['posts']} 本・表示の和 {_n(r['views_sum'])}"
                             f"・中央値 {_n(r['views_median'])}（数えた {r['n_counted']} 本）")
            visits = result["visits"]
            lines.append(f"- 結果 訪問: {_n(visits['sessions'])}"
                         + (f"（{t(visits['cannot_say'])}）" if visits["cannot_say"] else "")
                         + f"・クリック（72 時間）: {_n(result['clicks_72h'].get('clicks_72h'))}")
            closed = [e for e in result["events"] if e.get("name") == "closed"]
            if closed:
                lines.append(f"- 閉じた日: {t(closed[-1].get('at') or '')}")
        else:
            week = entry["this_week"]
            visits = week["visits"]
            lines.append(f"- その週のクリック（72 時間）: {_n(week['clicks_72h']['sum'])}・訪問: {_n(visits['sessions'])}"
                         + (f"（{t(visits['cannot_say'])}）" if visits["cannot_say"] else ""))
        lines.append("")
    lines += ["## 次の糸の種（thth scenario seeds）", ""]
    seeds = payload["seeds"]
    if not seeds or "error" in seeds:
        lines += ["並べられませんでした。thth scenario seeds <project> で確かめてください。", ""]
    else:
        from .scenario_cli import render_seeds
        collected = []
        render_seeds(seeds, out=collected.append)
        lines += ["    " + line for line in collected] + [""]
    while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
        lines.pop()
    return lines


def _refuse(args, code, message):
    from . import refusal
    return refusal.emit(args, code, line=f"{code}: {message}",
                        payload={"ok": False, "error": code, "message": message}, rc=2)


def cmd(args) -> int:
    try:
        payload = answer(args.target, week=args.week, min_n=args.min_n, studies=args.study or ())
    except ReviewRefused as exc:
        return _refuse(args, exc.code, exc.message)
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (after_cli.AfterError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        sys.stdout.write(render_text(payload))
    return 0


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser("review", help="振り返りの材料（週の結果と前の週との並び・落ち込みの印。読むだけ）")
    ops = p.add_subparsers(dest="review_command", required=True)
    w = ops.add_parser("weekly", help="週の結果を前の 4 週と並べ、落ち込みの印・当たりと外れ・層・先週の宣言を出す")
    w.add_argument("target", help="口座か project の名前")
    w.add_argument("--week", default=None, help="見る週（YYYY-MM-DD・その週の月曜にそろえる。既定は閉じた最後の週）")
    w.add_argument("--study", action="append", default=None, metavar="施策JSON",
                   help="先週 study add で宣言した施策JSON（何本でも）。結果を同じ束に入れる")
    w.add_argument("--min-n", dest="min_n", type=int, default=after_cli.DEFAULT_MIN_N,
                   help=f"中央値を出す下限（既定 {after_cli.DEFAULT_MIN_N}）")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd)
