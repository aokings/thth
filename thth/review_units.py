"""週の振り返りの単位: シリーズ（ルーチン投稿）とキャンペーン（糸）（依頼 §1-2・2026-11-25）。

`thth review weekly` の口座の材料と同じ数え方（`review_weekly._week_numbers`・`analytics_comparison`）を、
`thth scenario` の単位で切って出す。**シリーズと糸の割り当ては scenario の台帳をそのまま使う**
（`scenario.load_ledger`・`scenario.build`・原稿の `series:`・`thread:` と `post_ids`）。数え方は作り直さない:

- シリーズ: `scenario` のシリーズごと（宣言の無い `series:` の札も、その名で 1 つ）。どの札も無い原稿は
  「未分類」として 1 つに束ねる（糸の原稿はキャンペーンの側に出る）。
- キャンペーン: 動いている糸は `scenario` の段ごとの進みに、その週の数（表示・返信・クリック・訪問）を添える。
  閉じた糸は宣言した段と、糸の期間の結果（本数・表示の和と中央値・クリック・訪問）を同じ形で並べる。
- 次の糸の種: `scenario seeds` をそのまま同じ束に入れる。

媒体・口座をまたいで足さない（単位の中も口座ごと）。落ち込みの印は単位ごとに、表示の和だけで見る
（フォロワーの増えはシリーズや糸に割れない）。読むだけ。
"""
from __future__ import annotations

import datetime

from . import jst

UNCLASSIFIED = "unclassified"
UNCLASSIFIED_JA = "未分類"
LIFETIME_START = datetime.datetime(2000, 1, 1, tzinfo=jst.JST)


def _ids_by_account(items, names, key, value):
    """`key`（series・thread）が `value` の、出した原稿の post_id を口座ごとに。`value` が UNCLASSIFIED なら札なし。"""
    from . import scenario
    out = {}
    for item in items:
        if item.account not in names or not scenario._is_posted(item) or not item.post_ids:
            continue
        if value == UNCLASSIFIED:
            if item.series or item.thread:
                continue
        elif getattr(item, key) != value:
            continue
        out.setdefault(item.account, set()).update(str(p) for p in item.post_ids)
    return out


def _unit_accounts(ids_by_account, loaded, mondays, now, min_n, clicks):
    from . import review_weekly as rw
    nodes = {}
    for name in sorted(ids_by_account):
        if name not in loaded:
            continue
        ids = ids_by_account[name]
        rows = [rw._week_numbers(loaded[name], m, now, min_n, ids=ids) for m in mondays]
        for row in rows:
            row.pop("_population")
            row["clicks_72h"] = rw._bucket_clicks(clicks.get(name), row["week_start"], ids)
        drops = [rw._drop(rows, i, metrics=("views",)) for i in range(len(rows))]
        for row, result in zip(rows, drops):
            row["drop"] = result["drop"]
        nodes[name] = {"medium": loaded[name]["cfg"].get("media"), "week": rows[-1], "weeks": rows,
                       "drop": drops[-1]["drop"], "drop_streak": rw._streak(drops),
                       "drop_detail": drops[-1]}
    return nodes


def _roll(nodes):
    flags = [node["drop"] for node in nodes.values()]
    return (True if any(f is True for f in flags) else
            False if any(f is False for f in flags) else None)


def _series(ledger, status, names, loaded, mondays, now, min_n, clicks):
    declared = {row["id"]: row for row in status["series"]}
    found = sorted({item.series for item in ledger.items if item.series and item.account in names})
    out = []
    for ident in list(declared) + [s for s in found if s not in declared] + [UNCLASSIFIED]:
        ids = _ids_by_account(ledger.items, names, "series", ident)
        nodes = _unit_accounts(ids, loaded, mondays, now, min_n, clicks)
        row = declared.get(ident) or {}
        out.append({"id": None if ident == UNCLASSIFIED else ident,
                    "label": UNCLASSIFIED_JA if ident == UNCLASSIFIED else (row.get("title") or ident),
                    "declared": ident in declared, "unclassified": ident == UNCLASSIFIED,
                    "stock": row.get("stock"), "next_slot": row.get("next_slot"),
                    "drop": _roll(nodes), "by_account": nodes})
    return out


def _week_clicks(block, start, end):
    """糸のクリックの行（`thread_clicks.thread_block` の posts）から、その週に出た投稿の分を足す。"""
    from .analytics_comparison import _timestamp
    values = [row["clicks_72h"] for row in block.get("posts") or []
              if row.get("clicks_72h") is not None
              and (lambda at: at is not None and start <= at < end)(_timestamp(row.get("posted_at")))]
    return {"sum": sum(values) if values else None, "n": len(values),
            "reason": None if values else "no_attributable_clicks_in_week"}


def _visits(block):
    return {"sessions": block.get("sessions"), "engaged_sessions": block.get("engaged_sessions"),
            "as_of": block.get("as_of"), "cannot_say": block.get("cannot_say")}


def _lifetime(ids_by_account, loaded, now, min_n):
    from . import analytics_comparison as comparison
    out = {}
    for name in sorted(ids_by_account):
        if name not in loaded:
            continue
        ids = ids_by_account[name]
        items = [item for item in loaded[name]["root_items"] if item[0] in ids]
        population = comparison._population(items, LIFETIME_START, now, now, min_n)
        views = [row["observation"]["metrics"]["views"] for row in population["evidence"]
                 if row["observation"] and row["observation"]["metrics"]["views"] is not None]
        out[name] = {"posts": population["n_total"], "n_counted": len(views),
                     "n_immature": population["n_immature"], "n_incomplete": population["n_incomplete"],
                     "views_sum": sum(views) if views else None,
                     "views_median": population["metrics"]["views"]["median"]}
    return out


def _campaigns(ledger, status, names, loaded, mondays, now, min_n, clicks):
    from . import site_visits, thread_clicks
    monday = mondays[-1]
    start = datetime.datetime(monday.year, monday.month, monday.day, tzinfo=jst.JST)
    end = start + datetime.timedelta(days=7)
    records, cannot = site_visits.read_all(ledger.project)
    limited = not set(ledger.accounts) <= set(names)
    out = []
    for row in status["threads"]:
        ids = _ids_by_account(ledger.items, names, "thread", row["id"])
        if limited and not any(i.thread == row["id"] and i.account in names for i in ledger.items):
            continue     # 口座で絞ったとき、その口座の原稿の無い糸は出さない
        nodes = _unit_accounts(ids, loaded, mondays, now, min_n, clicks)
        block = thread_clicks.thread_block(ledger, row["id"])
        entry = {"id": row["id"], "title": row["title"], "goal_text": row["goal_text"],
                 "state": row["state"], "stage": row["stage"],
                 "stages": [{key: stage.get(key) for key in ("name", "label", "medium", "need", "done",
                                                              "pending", "replies", "satisfied")}
                            for stage in row["stages"]],
                 "stalled": row["stalled"], "next": row["next"],
                 "period": site_visits.thread_period(ledger, row["id"], row),
                 "by_account": nodes}
        if row["state"] == "closed":
            entry.update(drop=None, drop_reason="closed",
                          result={"events": row["events"],
                                  "by_account": _lifetime(ids, loaded, now, min_n),
                                  "clicks_72h": {key: block.get(key) for key in block if key != "posts"},
                                  "visits": _visits(site_visits.thread_block(
                                      ledger.project, ledger, row["id"], records, cannot, status=row))})
        else:
            week = {"from": monday.isoformat(), "to": (monday + datetime.timedelta(days=6)).isoformat()}
            entry.update(drop=_roll(nodes), drop_reason=None,
                         this_week={"clicks_72h": _week_clicks(block, start, end),
                                    "visits": _visits(site_visits.thread_block(
                                        ledger.project, ledger, row["id"], records, cannot, period=week))})
        out.append(entry)
    return out


def build(target, names, mondays, now, min_n, loaded, clicks):
    """シリーズ・キャンペーン・次の糸の種。scenario の台帳を 1 回だけ読む。"""
    from . import scenario, scenario_seeds
    try:
        project, scenario_names, hidden = scenario._scoped(target, None)
    except ValueError:
        return {"series": [], "campaigns": [], "seeds": None, "cannot_say": ["scenario_target_unknown"]}
    ledger = scenario.load_ledger(project, scenario_names, now=now, hidden=hidden)
    status = scenario.build(project, now=now, ledger=ledger)
    scope = set(names)
    series = _series(ledger, status, scope, loaded, mondays, now, min_n, clicks)
    campaigns = _campaigns(ledger, status, scope, loaded, mondays, now, min_n, clicks)
    try:
        seeds = scenario_seeds.build(project, now=now)
    except ValueError as exc:
        seeds = {"error": str(exc)}
    return {"project": project, "series": series, "campaigns": campaigns, "seeds": seeds,
            "cannot_say": sorted(status["cannot_say"])}
