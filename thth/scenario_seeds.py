"""`thth scenario seeds <project> [--days N] [--limit N] [--json]`: 次の糸の種を、合図ごとの列で並べる
（5.9.0・設計 効果測定と連動 §3 段 5「戻りの輪」）。

**読むだけ・書かない・選ばない。** 選ぶのは窓口（と人）。THTH は数の多い順に並べるだけ。

列（合図）:

  1. 返信の多かった投稿: 自分の投稿の 24h の「返ってきた返信」（`thth after` と同じ 1 本の計算・
     `after_cli.reaction_lookup`）。
  2. 訪問の多かった告知: 5.7.0 の `site_visits` の控えで、原稿に結べた campaign の sessions。
     控えが無ければ列ごと `cannot_say: site_visits_not_imported`（0 と言わない）。
  3. よく見られた投稿: 24h の views。
  4. 閉じた糸の続き: 窓の中で閉じた糸の、宣言にあって使わなかった媒体（`plaza_from._tried_media` と同じ計算）。
     5.10.0: 閉じていなくても全段が済んだ糸（`state: done`）で、最後の動きが窓の中のものも入れる。

規律（設計 §3 段 5）:

  - 並べ方は数の多い順だけ（`sort_basis` を出す）。重みづけ・点数・合成の順位は作らない。
    投稿の列は**媒体ごとに**並べる（媒体をまたいで順位を作らない・`thth after` の規約 5）。
    訪問の列はサイトごとに並べる（GA4 の窓がサイトで違う）。
  - n が 5（`after_cli.DEFAULT_MIN_N`）に満たない群は、中央値も比べも言わない（`comparable: false`）。
  - 出さないもの: 他人の返信の本文と username（数だけ）・他人の投稿・広告の費用の比べ（段 4 の観測にある）。
  - 窓（`--days`・既定 28 日）の外の投稿・糸は数えない。訪問は窓が重なる控えだけ。
  - 0 の行は種として並べない（数には入れる）。読めない数は 0 と混ぜない。
"""
from __future__ import annotations

import datetime

from . import jst
from . import scenario as scenario_mod

REPORT_TYPE = "scenario_seeds"
DEFAULT_DAYS = 28
DEFAULT_LIMIT = 5
MAX_DAYS = 400
MAX_LIMIT = 50
NEXT_LINE = "種にするなら: 糸の宣言を書く（skill thth-flow の『糸を始める』）"
TIE_BREAK_POSTS = "同じ数は投稿の新しい順"
# 訪問の列で原稿に結べなかった理由（`site_visits.tie` の符丁のうち原稿が決まらないもの・no_thread は原稿があるので並べる）。
UNTIED_REASONS = ("not_a_campaign", "no_manuscript", "ambiguous")

POST_COLUMNS = (
    ("replies", "返信の多かった投稿", "replies_back_24h",
     "24h の返ってきた返信（他人の返信の数・thth after と同じ計算）"),
    ("views", "よく見られた投稿", "views_24h", "24h の views（thth after と同じ計算）"),
)


class SeedsError(ValueError):
    """問いが受け取れない（`--days`・`--limit` が範囲の外）。`code` は静的な符丁。"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _positive(value, maximum, code):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise SeedsError(code)
    return value


def _in_window(at, since, now) -> bool:
    """窓の判定（ここ 1 か所）。窓は `since`（今から days 日前）から今まで。"""
    return at is not None and since <= at <= now


# ------------------------------------------------------------------ 投稿（列 1・3）

def _own_posts(ledger, since, now):
    """窓の中の自分の SNS の投稿（queue の原稿で投稿済みのもの）と、1 本ずつの 24h の反応。

    戻り値 `(行の列, 言えないこと)`。行は `{"item", "metrics"}`。反応は `after_cli.reaction_lookup`
    （`thth after` と同じ計算）の数だけを使う——他人の返信の本文と username はここに来ない。
    連投は先頭の投稿で数える（段 4 の反応と同じ分母）。
    列の `posts` は「窓の中で反応の数を引けた本数」（実測が読めない・post_id が無い投稿は入らず `skipped_counts`）、
    `n` はそのうち指標の値が取れた本数。窓の始まりちょうどの投稿は入る（`thth after` と同じ `since <= 投稿 <= 今`）。
    """
    from . import after_cli
    rows, cannot, lookups = [], {}, {}

    def note(code):
        cannot[code] = cannot.get(code, 0) + 1

    for item in ledger.items:
        if item.medium not in scenario_mod.SNS_MEDIA or not scenario_mod._is_posted(item):
            continue
        if item.posted_at is None:
            note("posted_at_missing")
            continue
        if not _in_window(item.posted_at, since, now):
            continue
        post_id = next((pid for pid in item.post_ids if isinstance(pid, str) and pid), None)
        if post_id is None:
            note("post_id_missing")
            continue
        if item.account not in lookups:
            try:
                lookups[item.account] = after_cli.reaction_lookup(item.account)
            except Exception:  # noqa: BLE001 — 読めない実測は数えない（0 と混ぜない）
                lookups[item.account] = None
        lookup = lookups[item.account]
        if lookup is None:
            note("measured_unreadable")
            continue
        try:
            metrics = lookup(post_id)
        except Exception:  # noqa: BLE001
            note("measured_unreadable")
            continue
        rows.append({"item": item, "metrics": metrics if isinstance(metrics, dict) else {}})
    return rows, cannot


def _count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value) if float(value).is_integer() else value


def _group_stat(values, posts, min_n):
    """群の数: `posts`（窓の中の本数）・`n`（数の取れた本数）・中央値（n が min_n 未満なら言わない）。"""
    from . import after_cli
    stat = after_cli._stat(values, min_n=min_n)
    return {"posts": posts, "n": stat["n"], "min_n": min_n, "median": stat["median"],
            "p25": stat["p25"], "p75": stat["p75"], "comparable": stat["n"] >= min_n and stat["n"] > 0}


def metric_available(metric, medium) -> bool:
    """その媒体で投稿単位に取れる指標か（5.10.0）。views は `goals.REACH_METRICS` に views のある媒体だけ
    （Bluesky・Mastodon は views を持たない）。取れない媒体は `below_min_n` でなく `metric_unavailable:<媒体>`。"""
    from . import goals
    if metric == "views_24h":
        return "views" in goals.REACH_METRICS.get(medium, ("views",))
    return True


def _post_column(key, label, metric, basis, posts, cannot, *, limit, min_n, window):
    by_medium = {}
    for row in posts:
        item = row["item"]
        group = by_medium.setdefault(item.medium, {"posts": 0, "values": [], "rows": [], "uncovered": 0})
        group["posts"] += 1
        value = _count(row["metrics"].get(metric))
        if not row["metrics"].get("covered"):
            group["uncovered"] += 1
        if value is None:
            continue
        group["values"].append(value)
        if value <= 0:
            continue   # 0 は種として並べない（数には入れる）
        group["rows"].append({"count": value, "medium": item.medium, "account": item.account,
                              "file": item.file, "thread": item.thread, "head": item.head,
                              "posted_at": jst.iso(item.posted_at),
                              "covered_24h": bool(row["metrics"].get("covered"))})
    out_media, codes = {}, sorted(cannot)
    for medium in sorted(by_medium):
        group = by_medium[medium]
        ordered = sorted(group["rows"], key=lambda r: r["posted_at"], reverse=True)
        ordered.sort(key=lambda r: r["count"], reverse=True)
        node = _group_stat(group["values"], group["posts"], min_n)
        node["uncovered_24h"] = group["uncovered"]
        node["rows"] = ordered[:limit]
        node["more"] = max(0, len(ordered) - limit)
        if not metric_available(metric, medium):
            node["metric_unavailable"] = True
            codes.append(f"metric_unavailable:{medium}")
        elif not node["comparable"]:
            codes.append(f"below_min_n:{medium}")
        out_media[medium] = node
    if not by_medium:
        codes.append("no_posts_in_window")
    return {"key": key, "label": label, "metric": metric, "measure": basis,
            "sort_basis": f"{metric} の多い順・{TIE_BREAK_POSTS}・媒体ごと（媒体をまたいで並べない）",
            "window": window, "by_medium": out_media, "cannot_say": sorted(set(codes)), "next": NEXT_LINE}


# ------------------------------------------------------------------ 訪問（列 2）

def _visits_column(ledger, since, *, limit, min_n):
    from . import site_visits
    column = {"key": "visits", "label": "訪問の多かった告知", "metric": "sessions",
              "measure": site_visits.LABEL, "source": site_visits.SOURCE,
              "sort_basis": "sessions の多い順・同じ数は原稿のパスの順・サイトごと（窓の違うサイトは混ぜない）",
              "window_basis": "GA4 の窓（hub が決める）が --days の窓と重なる控えだけ",
              "as_of": None, "sites": [], "cannot_say": [], "next": NEXT_LINE}
    records, cannot = site_visits.load(ledger.project)
    if not records:
        column["cannot_say"] = ["site_visits_unreadable" if cannot else "site_visits_not_imported"]
        return column
    column["as_of"] = max((r.get("generated_at") for r in records if r.get("generated_at")), default=None)
    index = site_visits._index(ledger.items)
    since_day = since.date().isoformat()
    for record in records:
        window = record.get("window") or {}
        site = {"site": record.get("site"), "window": window, "generated_at": record.get("generated_at"),
                "stale": bool(record.get("newer_unavailable")), "rows": [], "more": 0, "cannot_say": None}
        if (window.get("to") or "") < since_day:
            site["cannot_say"] = "outside_window"
            column["sites"].append(site)
            continue
        if record.get("rows") is None:
            site["cannot_say"] = "site_visits_unavailable"
            column["sites"].append(site)
            continue
        by_path, untied, untied_sessions, untied_unknown = {}, {}, 0, False
        for row in record["rows"]:
            _thread, item, reason = site_visits.tie(row.get("campaign"), index)
            if item is None:
                # 原稿に結べない campaign は種にしない。名前は出さず、数だけ（`tie_rows` の untied_campaigns の形）。
                untied.setdefault((row.get("campaign") or "").strip().lower(), reason)
                if isinstance(row.get("sessions"), int):
                    untied_sessions += row["sessions"]
                else:
                    untied_unknown = True
                continue
            path = item.rel or item.file
            node = by_path.setdefault(path, {"path": path, "file": item.file, "thread": item.thread,
                                             "sessions": 0, "engaged_sessions": 0})
            for key in ("sessions", "engaged_sessions"):
                value = row.get(key)
                node[key] = node[key] + value if isinstance(value, int) and node[key] is not None else None
        values = [n["sessions"] for n in by_path.values() if n["sessions"] is not None]
        stat = _group_stat(values, len(by_path), min_n)
        ranked = sorted((n for n in by_path.values() if n["sessions"]), key=lambda n: n["path"])
        ranked.sort(key=lambda n: n["sessions"], reverse=True)
        site.update({"manuscripts": stat["posts"], "n": stat["n"], "min_n": min_n, "median": stat["median"],
                     "comparable": stat["comparable"], "rows": ranked[:limit],
                     "more": max(0, len(ranked) - limit),
                     "untied": {"campaigns": len(untied), "sessions": None if untied_unknown else untied_sessions,
                                "by_reason": {r: sum(1 for v in untied.values() if v == r)
                                              for r in UNTIED_REASONS}}})
        if not by_path:
            site["cannot_say"] = "no_tied_campaign"
        elif not stat["comparable"]:
            site["cannot_say"] = "below_min_n"
        column["sites"].append(site)
    shown = [s for s in column["sites"] if s["cannot_say"] not in ("outside_window", "site_visits_unavailable")]
    if not shown:
        codes = {s["cannot_say"] for s in column["sites"]}
        column["cannot_say"] = ["outside_window" if codes == {"outside_window"} else "site_visits_unavailable"]
    if cannot:
        column["read_problems"] = sorted(cannot)
    return column


# ------------------------------------------------------------------ 閉じた糸（列 4）

def _closed_column(ledger, since, now, *, limit):
    from . import plaza_from
    rows, cannot = [], set()
    for decl in scenario_mod.usable_declarations(ledger):
        if decl.kind != "thread":
            continue
        closed = [scenario_mod._date_at(e["at"]) for e in decl.events if e["name"] == "closed"]
        closed_at = max((c for c in closed if c is not None), default=None)
        status = None
        if closed_at is None:
            # 5.10.0: 閉じていなくても全段が済んだ糸（`state: done`）も入れる。窓は最後の動き（`done.since`）で測る。
            status = scenario_mod.thread_status(decl, ledger)
            if status["state"] != "done":
                continue
            moment = jst.parse((status.get("done") or {}).get("since"))
        else:
            moment = closed_at
        # 窓の判定は日の単位（閉じた日・済んだ日の 00:00 を窓の始まりの日と比べる）。
        if moment is None or not _in_window(moment, datetime.datetime.combine(since.date(), datetime.time(),
                                                                              tzinfo=jst.JST), now):
            continue
        status = status or scenario_mod.thread_status(decl, ledger)
        if status["state"] not in ("closed", "done"):
            continue
        posts = {}
        for item in ledger.items:
            if item.thread == decl.id and item.medium in scenario_mod.SNS_MEDIA and scenario_mod._is_posted(item):
                posts.setdefault(item.medium, {"posts": 0})["posts"] += 1
        tried, untried = plaza_from._tried_media(status["stages"], posts)
        # 段の本数が読めない（`done: null`・例: 広告の口座が無い・はてなの口座が無い）媒体は「使わなかった」と
        # 言わない（0 と混ぜない）。`media_unknown` に分けて出す。
        unknown = set()
        for row in status["stages"]:
            if "done" in row and row.get("done") is None:
                medium = row.get("medium")
                unknown |= set(medium if isinstance(medium, list) else [medium])
        unknown &= set(untried)
        untried = [m for m in untried if m not in unknown]
        cannot |= set(status["cannot_say"])
        if not untried:
            continue
        title = scenario_mod._plain(decl.fm.get("title"))
        rows.append({"count": len(untried), "thread": decl.id, "title": scenario_mod._head(title) if title else None,
                     "closed_at": jst.iso(closed_at) if closed_at else None,
                     # 5.10.0（鍵は足すだけ）: closed か done か・done のときの最後の動き。
                     "state": status["state"], "done_at": jst.iso(moment) if closed_at is None else None,
                     "media_declared_untried": untried, "media_tried": tried,
                     "media_unknown": sorted(unknown)})
    ordered = sorted(rows, key=lambda r: (r["closed_at"] or r["done_at"], r["thread"]), reverse=True)
    ordered.sort(key=lambda r: r["count"], reverse=True)
    codes = sorted(cannot | ({"no_closed_thread_in_window"} if not rows else set()))
    return {"key": "closed_threads", "label": "閉じた・済んだ糸の続き", "metric": "media_declared_untried",
            "measure": "宣言にあって使わなかった媒体（plaza の「試していない媒体」と同じ計算）",
            "sort_basis": "使わなかった媒体の数の多い順・同じ数は閉じた日（済んだ糸は最後の動き）の新しい順",
            "rows": ordered[:limit], "more": max(0, len(ordered) - limit),
            "cannot_say": codes, "next": NEXT_LINE}


# ------------------------------------------------------------------ 全体

def build(target, *, days=DEFAULT_DAYS, limit=DEFAULT_LIMIT, now=None, allowed_names=None) -> dict:
    """`thth scenario seeds <project> --json` の中身。**読むだけ。** 対象が無ければ ValueError（`target_unknown`）。"""
    from . import after_cli, flow_skill
    days = _positive(days, MAX_DAYS, "seeds_days_invalid")
    limit = _positive(limit, MAX_LIMIT, "seeds_limit_invalid")
    now = now if now is not None else jst.now_jst()
    project, names, hidden = scenario_mod._scoped(target, allowed_names)
    ledger = scenario_mod.load_ledger(project, names, now=now, hidden=hidden)
    since = now - datetime.timedelta(days=days)
    window = {"days": days, "start": jst.iso(since), "end": jst.iso(now), "basis": "posted_at",
              "timezone": "Asia/Tokyo"}
    min_n = after_cli.DEFAULT_MIN_N
    posts, post_cannot = _own_posts(ledger, since, now)
    by_key = {key: _post_column(key, label, metric, basis, posts, post_cannot, limit=limit, min_n=min_n,
                                window=window)
              for key, label, metric, basis in POST_COLUMNS}
    columns = [by_key["replies"], _visits_column(ledger, since, limit=limit, min_n=min_n), by_key["views"],
               _closed_column(ledger, since, now, limit=limit)]
    cannot = set(ledger.cannot_say)
    for column in columns:
        cannot |= {code for code in column["cannot_say"] if ":" not in code}
    return {"schema": scenario_mod.SCHEMA, "report_type": REPORT_TYPE, "generated_at": jst.iso(ledger.now),
            "skill": flow_skill.skill_ref(), "project": ledger.project, "accounts": sorted(ledger.accounts),
            "window": window, "limit": limit, "min_n": min_n,
            "chooses": "窓口（THTH は並べるだけ・選ばない）",
            "skipped_counts": {code: post_cannot[code] for code in sorted(post_cannot)},
            "columns": columns, "cannot_say": sorted(cannot)}
