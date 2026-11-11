"""広場に置く手間を減らす（設計 3.8.0 §C）——`thth plaza post … --from …`。

材料の報告（r20260924-90aafc42・a51eaafa）: 置く側は、すでに手元にある出力（analytics-
report・after・study-report の数字、repo の文書、閉じた報告の返事）を人が写し直していた。
写し直すと数字が本文に入り、道具の観測と人の数字の区別が消える。

規律:

  (a) **observed は道具が付けた数字だけ**。`--from analytics-report|after` は、置く時点で
      道具がその計算を呼び直して数字（分母・期間・言えないこと）を `tool_numbers` に入れる。
      人や LLM の本文（`--body-file`）は読まない——本文の数字は従前どおり「本文」の欄。
      `--from study-report` は宣言を 3.4.0 と同じ観測の列にする（同じ計算）。
  (b) 本文には道具の下書き（数字の要約）を先頭に置き、人と LLM が解釈を下に足す。
  (c) **他人の返信・username を写さない**（project の範囲と同じ規律）。数字は集計だけ
      （投稿ごと・枝ごとの行は写さない）。
  (d) `--from-doc` は repo の中の md だけ（元の文書のパスと commit を持つ・本文は先頭
      4,000 字まで・秘密の検査は置く側の `plaza.post` と同じ）。commit と中身が
      違う（未 commit の変更がある）文書は置かない——「どの版の文書か」が言えないため。
  (e) `--from-report` は自分の project の閉じた報告の返事だけ（道具のコツとして写す）。
"""
from __future__ import annotations

import datetime
import os
import subprocess

from . import accounts, gitopts, jst, private_store

FROM_TOOLS = ("analytics-report", "after", "study-report")
DOC_MAX = 4000
DOC_FILE_MAX = 1024 * 1024
# 報告の返事を写す上限（本文 8,000 字に人の解釈を足す余地を残す）。
REPORT_REPLY_MAX = 6000
CANNOT_SAY_MAX = 20
CANNOT_SAY_CHARS = 200
DRAFT_HEAD = "［道具の下書き・数字は観測の欄が正（道具が付けた）。解釈は下に人か LLM が足す］"


def _error(reason):
    from . import plaza
    return plaza.PlazaError(reason)


# ------------------------------------------------------------ 道具の数字

def _stat(value):
    value = value or {}
    return {"median": value.get("median"), "n": value.get("n")}


def _node_numbers(node):
    """after の 1 account の節から、集計の数字だけ（投稿ごと・枝ごとの行は写さない）。"""
    posts = node.get("posts") or {}
    eng = node.get("engagements") or {}
    return {"posts": {"n": posts.get("n"), "views_24h": _stat(posts.get("views_24h"))},
            "engagements": {"n": eng.get("n"), "reacted": eng.get("reacted"),
                            "likes_24h": _stat(eng.get("likes_24h")),
                            "replies_back_24h": _stat(eng.get("replies_back_24h")),
                            "views_24h": _stat(eng.get("views_24h"))}}


def _cannot_say(lines):
    out = []
    for line in lines or []:
        if isinstance(line, str) and line.strip():
            out.append(private_store.fold_paths(line.replace("\n", " ").strip())[:CANNOT_SAY_CHARS])
    return out[:CANNOT_SAY_MAX]


def _observed(numbers):
    """道具の数字が 1 つでも取れていれば True（中央値か、0 でない件数）。"""
    posts, eng = numbers["posts"], numbers["engagements"]
    medians = [posts["views_24h"]["median"]] + [eng[key]["median"] for key in
                                                ("likes_24h", "replies_back_24h", "views_24h")]
    return any(value is not None for value in medians) or bool(posts["n"]) or bool(eng["n"])


def tool_numbers(kind, target, *, account, project, now, window_days=None, trusted_accounts=None):
    """`--from analytics-report|after <account>`: 置く時点で道具が計算を呼び直した数字。

    `target` は置く account と同じ持ち主（同じ project）の account だけ。
    """
    from . import after_cli, analytics_report, plaza
    if kind not in ("analytics-report", "after") or not accounts.name_is_safe(target):
        raise _error("invalid_from")
    owners = plaza._owner_accounts(account, project, trusted_accounts)
    if target not in owners:
        raise _error("from_out_of_scope")
    default = (analytics_report.DEFAULT_WINDOW_DAYS if kind == "analytics-report"
               else after_cli.DEFAULT_WINDOW_DAYS)
    window = default if window_days is None else window_days
    if type(window) is not int or not 1 <= window <= 365:
        raise _error("invalid_from")
    allowed = tuple(trusted_accounts) if trusted_accounts is not None else None
    try:
        if kind == "analytics-report":
            payload = analytics_report.answer(target, window_days=window, now=now,
                                              allowed_names=allowed)
            node = payload["by_account"][target]
            period = {key: payload["period"].get(key) for key in ("start", "end", "window_days",
                                                                  "basis", "timezone")}
            cannot = list(node.get("cannot_say") or []) + list(payload.get("cannot_say") or [])
        else:
            node = after_cli.answer(target, window_days=window, now=now, allowed_names=allowed)
            start = jst.to_jst(now) - datetime.timedelta(days=window)
            period = {"start": jst.iso(start), "end": jst.iso(now), "window_days": window,
                      "basis": "posted_at", "timezone": "Asia/Tokyo"}
            cannot = list(node.get("cannot_say") or [])
    except (after_cli.AfterError, accounts.AccountError, OSError, ValueError, TypeError, KeyError):
        raise _error("from_unavailable") from None
    numbers = _node_numbers(node)
    command = f"thth {kind} {target} --window-days {window}"
    return {"source": kind, "target": target, "medium": owners[target], "command": command,
            "at": jst.iso(now), "period": period, "numbers": numbers,
            "cannot_say": _cannot_say(cannot), "observed": _observed(numbers)}


def _fmt(stat):
    if stat["median"] is None:
        return f"言えない（n={stat['n']}）"
    return f"{stat['median']}（n={stat['n']}）"


def numbers_draft(entry):
    """`tool_numbers` の下書き（本文の先頭に置く・数字は観測の欄と同じもの）。"""
    numbers, period = entry["numbers"], entry["period"]
    posts, eng = numbers["posts"], numbers["engagements"]
    lines = [DRAFT_HEAD,
             f"出し直し: {entry['command']}（{entry['at']}）",
             f"期間: 直近 {period.get('window_days')} 日（{period.get('start')} 〜 {period.get('end')}・"
             f"{period.get('basis')}）",
             f"自分の投稿 {posts['n']} 本・24h views 中央値 {_fmt(posts['views_24h'])}",
             f"絡みに行った返信 {eng['n']} 本・反応あり {eng['reacted']}・likes 中央値 "
             f"{_fmt(eng['likes_24h'])}・返ってきた返信 中央値 {_fmt(eng['replies_back_24h'])}"]
    if entry["cannot_say"]:
        lines.append("言えないこと: " + "／".join(entry["cannot_say"]))
    return "\n".join(lines)


# ------------------------------------------------------------ 糸 1 本の結果（5.8.0 段 4）

SCENARIO_SOURCE = "scenario"
# 反応を測る媒体（SNS だけ。はてなブログ・はてブ・サイト・広告は段の本数と訪問・広告の欄で見る）。
REACTION_METRICS = ("views_24h", "likes_24h", "replies_back_24h")
REACTION_LABELS = {"views_24h": "views", "likes_24h": "likes", "replies_back_24h": "返ってきた返信"}
# 段の行から観測に写す鍵（数と状態だけ。原稿の名前・題・タグの中身・本文は写さない）。
STAGE_KEYS = ("name", "label", "medium", "need", "done", "pending", "drafts", "conflict", "satisfied",
              "satisfied_by", "replies", "overdue", "replies_within_hours", "target")
ADS_CANNOT = {"spend_minor": "ads_spend_not_recorded", "link_clicks": "ads_clicks_not_recorded"}
# 原稿の台帳が読めないときの符丁（`scenario.load_ledger` の cannot_say）。このとき本数は 0 と言わない。
LEDGER_UNREADABLE = ("queue_unreadable", "account_unreadable", "repo_unreadable")
# open の写しに残す Threads のクリックの鍵（5.11.0 段 7・数と分母と符丁だけ）。
CLICKS_OPEN_KEYS = ("label", "source", "medium", "basis", "window", "clicks_72h", "n", "of", "reasons",
                    "other_media", "cannot_say")
# 他の持ち主に見せてよい段の名（skill thth-flow の語彙）。それ以外は「その他」。
OPEN_STAGE_NAMES = ("collect", "diary", "article", "announce", "warm", "promote", "selfbm", "boost")


def _scenario_names(project_arg, *, account, project, trusted_accounts):
    """`--from scenario <project> <id>` の project は置く account と同じ project だけ。読む口座の名前。"""
    from . import scenario
    if not accounts.name_is_safe(project_arg):
        raise _error("invalid_from")
    try:
        found, names = scenario.resolve(project_arg)
    except (accounts.AccountError, OSError, ValueError, TypeError):
        raise _error("from_unavailable") from None
    if not names or found != project or not project:
        raise _error("from_out_of_scope")
    if trusted_accounts is not None:
        names = [name for name in names if trusted_accounts.get(name) == project]
        if account not in names:
            raise _error("from_out_of_scope")
    return found, names


def _stat_block(values, total, min_n):
    """1 つの指標: 取れた本数（n）・合計・中央値（n が min_n 未満なら言わない＝after と同じ規約）。"""
    from . import after_cli
    stat = after_cli._stat_median_only(values, min_n=min_n)
    return {"n": len(values), "of": total, "sum": sum(values) if values else None,
            "median": stat["median"]}


def _reactions(items, min_n):
    """糸の SNS の投稿の反応（媒体ごと）。1 本の数は `thth after` と同じ計算（`after_cli.reaction_metrics`:
    24h の刻みの実測・返信は返信の台帳の他者の数）。連投は先頭の投稿で数える（段の本数と同じ分母）。"""
    from . import after_cli, scenario
    by_medium, cannot = {}, set()
    lookups = {}
    for item in items:
        if item.medium not in scenario.SNS_MEDIA or not scenario._is_posted(item):
            continue
        row = by_medium.setdefault(item.medium, {"posts": 0, "covered": 0,
                                                 "values": {key: [] for key in REACTION_METRICS}})
        row["posts"] += 1
        post_id = next((pid for pid in item.post_ids if isinstance(pid, str) and pid), None)
        if post_id is None:
            cannot.add("post_id_missing")
            continue
        if item.account not in lookups:
            try:
                lookups[item.account] = after_cli.reaction_lookup(item.account)
            except Exception:  # noqa: BLE001 — 読めない実測は数えない（0 と混ぜない）
                lookups[item.account] = None
                cannot.add("measured_unreadable")
        lookup = lookups[item.account]
        if lookup is None:
            continue
        try:
            metrics = lookup(post_id)
        except Exception:  # noqa: BLE001
            cannot.add("measured_unreadable")
            continue
        if metrics.get("covered"):
            row["covered"] += 1
        for key in REACTION_METRICS:
            value = metrics.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row["values"][key].append(value)
    out = {}
    for medium in sorted(by_medium):
        row = by_medium[medium]
        out[medium] = {"posts": row["posts"], "covered_24h": row["covered"], "min_n": min_n,
                       **{key: _stat_block(row["values"][key], row["posts"], min_n)
                          for key in REACTION_METRICS}}
        if row["covered"] < row["posts"]:
            cannot.add("measured_24h_missing")
    return out, sorted(cannot)


def _visits_block(block):
    """`site_visits.thread_block` から数と窓だけ（campaign の名前・着地の頁は写さない）。"""
    sites = []
    for site in block.get("sites") or []:
        sites.append({"site": site.get("site"), "window": site.get("window"),
                      "generated_at": site.get("generated_at"),
                      "sessions": site.get("sessions"), "engaged_sessions": site.get("engaged_sessions"),
                      "by_medium": dict(site.get("by_medium") or {}),
                      "n_campaigns": len({c.get("campaign") for c in site.get("campaigns") or []}),
                      "stale": bool(site.get("stale")), "cannot_say": site.get("cannot_say")})
        # 5.11.0 段 8（鍵は足すだけ）: 窓の決め方と、日ごとに足したときの日の数。
        sites[-1]["window_basis"] = site.get("window_basis")
        for key in ("days_in_period", "days_covered", "days_missing", "days_pending", "days_unavailable"):
            if key in site:
                sites[-1][key] = site[key]
    return {"label": block.get("label"), "source": block.get("source"), "as_of": block.get("as_of"),
            "sessions": block.get("sessions"), "engaged_sessions": block.get("engaged_sessions"),
            "window_basis": block.get("window_basis"), "period": block.get("period"),
            "sites": sites, "cannot_say": block.get("cannot_say")}


def _ads_block(stages):
    """広告の段（boost・媒体 ads）。使った額と link_click は記録に無いので null と理由。Meta は呼ばない。"""
    row = next((s for s in stages if s.get("medium") == "ads"), None)
    if row is None:
        return {"declared": False, "started": None, "paused": None, "budget_minor": None, "currency": None,
                "spend_minor": None, "link_clicks": None,
                "cannot_say": {"started": "no_ads_stage", **ADS_CANNOT}}
    reasons = dict(ADS_CANNOT)
    if row.get("done") is None:
        reasons["started"] = "ads_account_missing"
    return {"declared": True, "started": row.get("done"), "paused": row.get("pending"),
            "budget_minor": row.get("budget_minor"), "currency": row.get("currency"),
            "spend_minor": None, "link_clicks": None, "cannot_say": reasons}


def _tried_media(stages, reactions):
    """この糸で実際に使った媒体（段の本数が 1 本以上・SNS は媒体ごと）と、宣言したのに 0 本の媒体。"""
    tried, declared = set(), set()
    for row in stages:
        medium = row.get("medium")
        media = medium if isinstance(medium, list) else [medium]
        for m in media:
            if m in (None, "site"):
                continue
            declared.add(m)
            if isinstance(row.get("by_medium"), dict):
                if (row["by_medium"].get(m) or {}).get("done"):
                    tried.add(m)
            elif row.get("done"):
                tried.add(m)
    tried |= {m for m, row in reactions.items() if row["posts"]}
    return sorted(tried), sorted(declared - tried)


def scenario_numbers(project_arg, thread_id, *, account, project, now, trusted_accounts=None):
    """`--from scenario <project> <糸の id>`: 糸 1 本の結果を道具が観測として付ける（設計 効果測定と連動 §3 段 4）。

    付けるもの（数と状態だけ・人や LLM の本文は読まない）:
      - 段ごとの本数（`thth scenario` と同じ計算）。
      - 媒体ごとの反応（`thth after` と同じ 1 本の計算・分母は糸のその媒体の投稿の本数）。
      - 本記事への訪問（5.7.0 の site_visits の控え。無ければ null と `site_visits_not_imported`）。
      - 広告の段（始めた本数・通しの予算。使った額と link_click は記録に無いので null と理由）。
    他人の本文・username・原稿の名前と題は写さない。
    """
    from . import after_cli, scenario, site_visits
    if not isinstance(thread_id, str) or not scenario.ID_RE.fullmatch(thread_id):
        raise _error("invalid_from")
    from . import plaza
    found, names = _scenario_names(project_arg, account=account, project=project,
                                   trusted_accounts=trusted_accounts)
    try:
        ledger = scenario.load_ledger(found, names, now=now)
        decl = next((d for d in scenario.usable_declarations(ledger) if d.id == thread_id), None)
        if decl is None or decl.kind != "thread":
            raise _error("from_scenario_not_found")
        status = scenario.thread_status(decl, ledger)
        items = [item for item in ledger.items if item.thread == decl.id]
        min_n = after_cli.DEFAULT_MIN_N
        reactions, reaction_cannot = _reactions(items, min_n)
        visits = _visits_block(site_visits.thread_block(found, ledger, decl.id, status=status))
        # 5.11.0 段 7: Threads のリンクのクリック（THTH の実測・72h）。訪問とは別の欄（数と分母と理由だけ）。
        from . import thread_clicks
        clicks = thread_clicks.thread_block(ledger, decl.id, with_posts=False)
    except plaza.PlazaError:
        raise
    except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
        raise _error("from_unavailable") from None
    stages = [{key: row[key] for key in STAGE_KEYS if key in row} for row in status["stages"]]
    for out, row in zip(stages, status["stages"]):
        if isinstance(row.get("by_medium"), dict):
            out["by_medium"] = {m: {"done": v.get("done"), "pending": v.get("pending")}
                                for m, v in row["by_medium"].items()}
        if row.get("medium") == "ads":
            out["budget_minor"], out["currency"] = row.get("budget_minor"), row.get("currency")
    ads = _ads_block(status["stages"])
    tried, declared_untried = _tried_media(status["stages"], reactions)
    # 窓: 糸の始まり（`started`・無ければ最初の出来事か投稿）から、閉じた日（無ければ今）まで。
    started = scenario._date_at(scenario._plain(decl.fm.get("started")))
    if started is None:
        moments = [scenario._date_at(e["at"]) for e in decl.events] + [
            item.posted_at for item in items if item.posted_at is not None]
        started = min((m for m in moments if m is not None), default=None)
    closed_at = max((scenario._date_at(e["at"]) for e in decl.events if e["name"] == "closed"
                     and scenario._date_at(e["at"]) is not None), default=None)
    period = {"start": jst.iso(started) if started else None,
              "end": jst.iso(closed_at) if closed_at else jst.iso(now),
              "basis": "thread_started_to_closed" if closed_at else "thread_started_to_now",
              "timezone": "Asia/Tokyo"}
    cannot = sorted(set(status["cannot_say"]) | set(ledger.cannot_say) | set(reaction_cannot)
                    | ({visits["cannot_say"]} if visits["cannot_say"] else set()))
    # 原稿の台帳が読めなかったら、反応と段の本数は「0」でなく言えない（監査 段4 の 3）。
    unreadable = sorted(set(ledger.cannot_say) & set(LEDGER_UNREADABLE))
    numbers = {"thread": decl.id, "state": status["state"], "stage": status["stage"],
               "stages": stages, "reactions": reactions, "site_visits": visits, "ads": ads,
               "threads_clicks": clicks,
               "media_tried": tried, "media_declared_untried": declared_untried,
               "posts_cannot_say": unreadable[0] if unreadable else None}
    # observed の印は、道具が自分の台帳から取った数があるときだけ（監査 段4 の 1）: 24h の実測が取れた投稿・
    # 訪問の控えの数・広告の口座の記録で始めた広告・言えた Threads のクリック（5.11.0）。段の本数（人が書く
    # 出来事・原稿の status）は根拠にしない。
    observed = (any(row["covered_24h"] for row in reactions.values())
                or visits["sessions"] is not None
                or (isinstance(ads["started"], int) and ads["started"] > 0)
                or clicks["n"] > 0)
    command = f"thth scenario show {found} {decl.id}"
    title = scenario._plain(decl.fm.get("title"))
    return {"source": SCENARIO_SOURCE, "target": decl.id, "project": found,
            "medium": (ledger.accounts.get(account) or {}).get("media"), "command": command,
            "at": jst.iso(now), "period": period,
            "numbers": numbers, "cannot_say": cannot[:CANNOT_SAY_MAX], "observed": bool(observed),
            "title": scenario._head(title) if title else None}


def scenario_open_numbers(numbers):
    """open の写し（他の持ち主に見える）の scenario の数（監査 段4 の 2）。数と媒体と状態だけ。

    落とすもの: 糸の id・サイトのホスト名・段の名と target（既知の語彙の段は日本語の名、ほかは「その他」）・
    現在の段の名。文字列は道具の符丁か媒体の名か、決めた日本語だけが残る。
    """
    from . import scenario
    numbers = numbers or {}
    stages = []
    for row in numbers.get("stages") or []:
        name = row.get("name")
        out = {"label": scenario.STAGE_JA[name] if name in OPEN_STAGE_NAMES else "その他"}
        for key in ("medium", "need", "done", "pending", "drafts", "satisfied", "replies", "overdue",
                    "budget_minor", "currency"):
            if key in row:
                out[key] = row[key]
        if isinstance(row.get("by_medium"), dict):
            out["by_medium"] = row["by_medium"]
        stages.append(out)
    visits = numbers.get("site_visits") or {}
    sites = [{key: site.get(key) for key in ("window", "sessions", "engaged_sessions", "by_medium",
                                               "n_campaigns", "stale", "cannot_say")}
             # 5.11.0 段 8: 窓の決め方と、日ごとに足したときの欠けた日・まだ来ない日（数と符丁だけ）。
             | {key: site[key] for key in ("window_basis", "days_missing", "days_pending") if key in site}
             for site in visits.get("sites") or []]
    stage = numbers.get("stage") or {}
    return {"state": numbers.get("state"), "stage": {"index": stage.get("index"), "of": stage.get("of")},
            "stages": stages, "reactions": numbers.get("reactions") or {},
            "site_visits": {key: visits.get(key) for key in ("label", "source", "as_of", "sessions",
                                                             "engaged_sessions", "cannot_say")}
            | ({"window_basis": visits["window_basis"]} if visits.get("window_basis") else {})
            | {"sites": sites, "n_sites": len(sites)},
            "ads": numbers.get("ads") or {},
            # 5.11.0 段 7: Threads のクリック（数・分母・理由の符丁・媒体の名だけ。原稿の名前と post_id は持たない）。
            **({"threads_clicks": {key: (numbers["threads_clicks"] or {}).get(key) for key in CLICKS_OPEN_KEYS}}
               if "threads_clicks" in numbers else {}),
            "media_tried": list(numbers.get("media_tried") or []),
            "media_declared_untried": list(numbers.get("media_declared_untried") or []),
            "posts_cannot_say": numbers.get("posts_cannot_say")}


def _yen(value, currency):
    return "—" if value is None else f"{value} {currency or ''}".rstrip()


def _reaction_line(medium, row):
    parts = []
    for key in REACTION_METRICS:
        block = row[key]
        if not block["n"]:
            parts.append(f"{REACTION_LABELS[key]} 取れない")
            continue
        median = (f"中央値 {block['median']}" if block["median"] is not None
                  else f"中央値は言えない（n<{row['min_n']}）")
        parts.append(f"{REACTION_LABELS[key]} 計 {block['sum']}（{block['n']}/{block['of']} 本・{median}）")
    return f"{medium} {row['posts']} 本（24h 取れた {row['covered_24h']}）: " + "・".join(parts)


def scenario_draft(entry):
    """`scenario_numbers` の下書き（本文の先頭・数字は観測の欄と同じもの）。"""
    numbers, period = entry["numbers"], entry["period"]
    lines = [DRAFT_HEAD,
             f"出し直し: {entry['command']}（{entry['at']}）",
             f"期間: {period.get('start') or '—'} 〜 {period.get('end')}（{period.get('basis')}）・"
             f"糸の状態 {numbers['state']}"]
    stage_parts = []
    for row in numbers["stages"]:
        if "replies" in row:
            stage_parts.append(f"{row.get('label') or row['name']} 返信 "
                               f"{'—' if row['replies'] is None else row['replies']}")
            continue
        done = "—" if row.get("done") is None else row["done"]
        stage_parts.append(f"{row.get('label') or row['name']} {done}/{row.get('need')}")
    unreadable = numbers.get("posts_cannot_say")
    lines.append("段（済み/要る）: " + ("・".join(stage_parts) or "—")
                 + (f"（原稿の台帳が読めない・{unreadable}。本数は読めた分だけ）" if unreadable else ""))
    if numbers["reactions"]:
        lines.append("反応（1 本ごとの 24h・thth after と同じ計算）:")
        for medium, row in numbers["reactions"].items():
            lines.append("  " + _reaction_line(medium, row))
        if unreadable:
            lines.append(f"  （原稿の台帳が読めない・{unreadable}。上は読めた原稿の分だけ）")
    elif unreadable:
        lines.append(f"反応: 言えない（{unreadable}）")
    else:
        lines.append("反応: SNS の投稿がまだ無い（0 本）")
    visits = numbers["site_visits"]
    if visits["sessions"] is not None:
        windows = "・".join(f"{s.get('site') or 'サイト'} {(s.get('window') or {}).get('from')}〜"
                            f"{(s.get('window') or {}).get('to')}"
                            for s in visits["sites"] if s.get("sessions") is not None)
        lines.append(f"本記事への訪問: {visits['sessions']}（engaged {visits['engaged_sessions'] if visits['engaged_sessions'] is not None else '—'}・"
                     f"{windows}・{visits['source']}）")
    else:
        lines.append(f"本記事への訪問: 言えない（{visits['cannot_say']}）")
    clicks = numbers.get("threads_clicks")
    if clicks is not None:
        # 5.11.0 段 7: 訪問（GA4）とは別の欄。足さない。
        total = "言えない" if clicks.get("clicks_72h") is None else str(clicks["clicks_72h"])
        reasons = "・".join(f"{k} {v}" for k, v in (clicks.get("reasons") or {}).items())
        others = "・".join(f"{m} —（{node.get('cannot_say')}:{m}）"
                           for m, node in (clicks.get("other_media") or {}).items())
        lines.append(f"Threads のリンクのクリック（THTH の実測・72h・訪問とは別）: {total}"
                     f"（言える {clicks.get('n')}/{clicks.get('of')} 本"
                     + (f"・言えない {reasons}" if reasons else "") + "）"
                     + (f"・{others}" if others else ""))
    ads = numbers["ads"]
    if not ads["declared"]:
        lines.append("広告: 段が無い（no_ads_stage）")
    else:
        started = "言えない（ads_account_missing）" if ads["started"] is None else f"{ads['started']} 本"
        lines.append(f"広告: 始めた {started}・止めたまま {'—' if ads['paused'] is None else ads['paused']}・"
                     f"通しの予算 {_yen(ads['budget_minor'], ads['currency'])}・使った額 言えない"
                     f"（{ADS_CANNOT['spend_minor']}）・link_click 言えない（{ADS_CANNOT['link_clicks']}）")
    lines.append("使った媒体: " + ("・".join(numbers["media_tried"]) or "—")
                 + (("／宣言したが読めた原稿では 0 本（言えない・" + unreadable + "）: " if unreadable
                     else "／宣言したが 0 本: ") + "・".join(numbers["media_declared_untried"])
                    if numbers["media_declared_untried"] else ""))
    if entry["cannot_say"]:
        lines.append("言えないこと: " + "／".join(entry["cannot_say"]))
    return "\n".join(lines)


def study_draft(observation, command):
    """`--from study-report` の下書き（3.4.0 の観測の列から・同じ数字）。"""
    lines = [DRAFT_HEAD, f"出し直し: {command}（{observation['at']}）"]
    for column in observation.get("columns") or []:
        if not column.get("observed"):
            lines.append(f"{column.get('medium') or '—'}: 取れない（{column.get('reason')}）")
            continue
        base = column["denominators"]["baseline"]
        changed = column["denominators"]["changed"]
        diffs = []
        for metric, value in (column.get("metrics") or {}).items():
            diff = value.get("observational_difference")
            diffs.append(f"{metric} " + ("—（" + str(value.get("reason")) + "）" if diff is None
                                         else f"{'+' if diff > 0 else ''}{diff}"))
        lines.append(f"{column.get('medium') or '—'}: 採用 {column.get('decision_at')}・"
                     f"前 {base['eligible']}/{base['requested']} 件・後 {changed['eligible']}/"
                     f"{changed['requested']} 件（時間適合/指定）・" + "・".join(diffs))
        if column.get("cannot_say"):
            lines.append("言えないこと: " + "／".join(_cannot_say(column["cannot_say"])))
    return "\n".join(lines)


def human_part(body):
    """本文のうち人や LLM が書いた部分（道具の下書きを除く）。「本文の数字」はここから数える。"""
    if not isinstance(body, str) or not body.startswith(DRAFT_HEAD):
        return body
    parts = body.split("\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def compose(draft, human):
    """道具の下書き＋人や LLM の本文（無ければ下書きだけ）。"""
    human = human.strip() if isinstance(human, str) else ""
    return draft + ("\n\n" + human if human else "")


# ------------------------------------------------------------ 文書

def _git(repo, *args):
    return subprocess.run(gitopts.git(repo, *args), capture_output=True, text=True,
                          timeout=30, check=False)


def from_doc(path, *, account):
    """`--from-doc <repo の中の md>`: 題・本文（先頭 4,000 字）・元の文書のパスと commit。"""
    if not isinstance(path, str) or not path.strip():
        raise _error("invalid_from_doc")
    try:
        cfg = accounts.load_account(account)
    except accounts.AccountError:
        raise _error("account_unavailable") from None
    repo = cfg.get("repo_dir")
    if not isinstance(repo, str) or not os.path.isdir(repo):
        raise _error("from_doc_outside_repo")
    root = os.path.realpath(repo)
    candidate = path if os.path.isabs(path) else os.path.join(root, path)
    real = os.path.realpath(candidate)
    if not real.startswith(root + os.sep):
        raise _error("from_doc_outside_repo")
    rel = os.path.relpath(real, root)
    if not rel.endswith(".md") or ".git" in rel.split(os.sep):
        raise _error("invalid_from_doc")
    try:
        if not os.path.isfile(real) or os.path.islink(candidate):
            raise _error("invalid_from_doc")
        with open(real, "rb") as stream:
            data = stream.read(DOC_FILE_MAX + 1)
    except OSError:
        raise _error("invalid_from_doc") from None
    if len(data) > DOC_FILE_MAX:
        raise _error("invalid_from_doc")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise _error("invalid_from_doc") from None
    try:
        tracked = _git(root, "ls-files", "--error-unmatch", "--", rel)
        clean = _git(root, "diff", "--quiet", "HEAD", "--", rel)
        log = _git(root, "log", "-1", "--format=%H", "--", rel)
    except (OSError, subprocess.SubprocessError):
        raise _error("from_doc_not_committed") from None
    commit = log.stdout.strip()
    if tracked.returncode != 0 or clean.returncode != 0 or len(commit) < 7:
        raise _error("from_doc_not_committed")
    body_text = text.strip()
    truncated = len(body_text) > DOC_MAX
    title = None
    for line in body_text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    title = (title or os.path.splitext(os.path.basename(rel))[0])[:120]
    head = f"［元の文書: {rel}@{commit[:7]}" + ("・先頭 4,000 字" if truncated else "") + "］"
    return {"title": title, "body": head + "\n" + body_text[:DOC_MAX],
            "source": {"kind": "doc", "path": rel, "commit": commit, "truncated": truncated,
                       "chars": len(body_text)}}


# ------------------------------------------------------------ 閉じた報告

def from_report(report_id, *, account, project):
    """`--from-report <report_id>`: 自分の project の閉じた報告の返事を道具のコツとして写す。

    範囲の外の報告と無い報告は同じ `from_report_not_found`（在ることを漏らさない）。
    写すのは実装側の返事だけ（報告した側の追記は写さない）。
    """
    from . import report_inbox
    scope = report_inbox.Scope([] if project else [account], [project] if project else [])
    try:
        record = report_inbox.show(report_id, scope=scope)
    except report_inbox.ReportError:
        raise _error("from_report_not_found") from None
    if record.get("status") != "closed":
        raise _error("from_report_open")
    replies = [row["text"] for row in record.get("replies") or []
               if row.get("role") != "reporter" and isinstance(row.get("text"), str)]
    if not replies:
        raise _error("from_report_no_reply")
    closed = record.get("closed") or {}
    head = (f"［報告 {record['report_id']} の返事（閉じた版 {closed.get('version')}・"
            f"{closed.get('reason')}）］")
    text = "\n\n".join(replies)
    if len(text) > REPORT_REPLY_MAX:
        text = text[:REPORT_REPLY_MAX] + "（以下略）"
    return {"title": record["title"][:120], "body": head + "\n" + text,
            "source": {"kind": "report", "report_id": record["report_id"],
                       "closed_version": closed.get("version"),
                       "closed_reason": closed.get("reason")}}
