"""施策の広場の観測（設計 3.4.0 §2・§5）——**数字は道具が付ける。**

1 件の施策（measure）は、媒体ごとの宣言（study-report の形: 仮説・変えたこと・
両群の投稿 ID・採用の時刻）を持つ。観測は宣言ごとに `study_report.answer()` を
そのまま呼んで作る——**同じ計算**（両群の分母・`observational_difference`・
欠測は null と理由）。ここで独自の集計はしない。

観測は置いた時点（`posted`）と更新時点（`refresh`）の値を履歴で持つ（後から
数字が変わったことが分かる）。**道具が付けた数字だけを「観測」と呼ぶ**——人や
LLM が本文に書いた数字は `plaza.text_numbers()` が「本文」として別の欄に出す。
"""
from __future__ import annotations

from . import accounts, jst

# 観測の欄の名前（人向けの表示でも同じ語を使う）。
LABEL = "観測（道具が付けた数字）"
TEXT_LABEL = "本文（人や LLM が書いた。数字は道具が確かめていない）"


def _metrics():
    from . import analytics_comparison
    return tuple(analytics_comparison.METRICS)


def _own_post(account, post_id, group):
    """自分の投稿の参照（先頭 60 字と permalink まで・送った記録から）。"""
    from . import plaza, sent
    preview = permalink = None
    try:
        row = sent.read(accounts.state_dir_for(account), post_id)
    except (OSError, ValueError, TypeError):
        row = None
    if isinstance(row, dict):
        if isinstance(row.get("text"), str):
            preview = plaza._preview(row["text"])
        for key in ("permalink", "url"):
            if isinstance(row.get(key), str) and row[key].startswith("https://"):
                permalink = row[key]
                break
    return {"group": group, "post_id": post_id, "preview": preview, "permalink": permalink}


def column(target, *, now, min_n, allowed_names=None):
    """宣言 1 つ（媒体 1 つ）の観測の列。取れなければ `observed: false` と静的な理由。"""
    from . import study_report
    declaration = target.get("declaration") or {}
    decision = declaration.get("decision") or {}
    base = {"account": target.get("account"), "medium": target.get("medium"),
            "declaration_id": declaration.get("id"), "decision_status": decision.get("status"),
            "decision_at": decision.get("at"), "observed": False, "reason": None,
            "denominators": None, "metrics": None, "excluded": [], "posts": [],
            "data_updated_at": None, "cannot_say": []}
    if decision.get("status") != "adopted":
        # 未採用の宣言は study-report と同じく台帳を読まない（分けて言う）。
        return {**base, "reason": "proposed_not_adopted"}
    try:
        result = study_report.answer(None, min_n=min_n, now=now,
                                     verified_declaration=declaration,
                                     allowed_names=allowed_names)
    except study_report.StudyError as error:
        return {**base, "reason": "scope_unavailable" if str(error) == "scope_unavailable"
                else "ledger_unavailable"}
    except (accounts.AccountError, OSError, ValueError, TypeError, KeyError, OverflowError):
        return {**base, "reason": "ledger_unavailable"}
    observations, comparison = result.get("observations"), result.get("comparison")
    if not observations or not comparison:
        return {**base, "reason": "ledger_unavailable"}
    metrics = {}
    for metric in _metrics():
        before = observations["baseline"]["metrics"][metric]
        after = observations["changed"]["metrics"][metric]
        entry = comparison[metric]
        metrics[metric] = {"baseline_median": before["median"], "changed_median": after["median"],
                           "baseline_n_eligible": before["n_eligible"],
                           "changed_n_eligible": after["n_eligible"],
                           "observational_difference": entry["absolute_median_change"],
                           "reason": entry["reason"]}
    denominators = {group: {"requested": population["n_requested"], "total": population["n_total"],
                            "eligible": population["n_eligible"], "missing": population["n_missing"],
                            "excluded": population["n_excluded"]}
                    for group, population in observations.items()}
    excluded = [{"group": group, "post_id": row["post_id"], "reason": row["reason"]}
                for group, population in observations.items() for row in population["excluded"]]
    posts = [_own_post(target["account"], row["post_id"], group)
             for group, population in observations.items() for row in population["evidence"]]
    return {**base, "observed": True, "denominators": denominators, "metrics": metrics,
            "excluded": excluded, "posts": posts, "data_updated_at": result.get("data_updated_at"),
            "cannot_say": list(result.get("cannot_say") or [])}


def observe(targets, *, now, min_n, by, trigger, allowed_names=None):
    """宣言ごとの観測を 1 回分（`posted` か `refresh`）。"""
    return {"at": jst.iso(now), "trigger": trigger, "by": by, "min_n": min_n,
            "columns": [column(target, now=now, min_n=min_n, allowed_names=allowed_names)
                        for target in targets]}


def open_column(entry, sanitize):
    """他の持ち主が読む列（account・宣言の id・投稿 ID・他人の ID を落とす）。

    自分の投稿は先頭 60 字（他人の情報を落としたもの）と permalink まで。
    除外は理由ごとの件数だけ（ID は出さない——`unknown_or_unowned_id` は他人の
    投稿の ID でありうる）。
    """
    counts = {}
    for row in entry.get("excluded") or []:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    posts = []
    for row in entry.get("posts") or []:
        preview = sanitize(row["preview"])[0] if row.get("preview") else None
        posts.append({"group": row["group"], "preview": preview, "permalink": row.get("permalink")})
    return {"medium": entry.get("medium"), "decision_status": entry.get("decision_status"),
            "decision_at": entry.get("decision_at"), "observed": entry.get("observed"),
            "reason": entry.get("reason"), "denominators": entry.get("denominators"),
            "metrics": entry.get("metrics"), "excluded_counts": counts, "posts": posts,
            "data_updated_at": entry.get("data_updated_at")}


def _columns(observation, level):
    if observation is None:
        return []
    # 同じ project（own）と同じ持ち主の組（owner・3.8.0）は原本の列、他の持ち主は写しの列。
    return list(observation.get("columns") or []) if level in ("own", "owner") \
        else list(observation.get("open_columns") or [])


def observation_view(record, level):
    """観測の欄（道具が付けた数字だけ）。施策でない・宣言が無ければ None。"""
    history = record.get("observations") or []
    if record.get("kind") != "measure" or not history:
        return None
    first, latest = history[0], history[-1]

    def entry(observation):
        return {"at": observation["at"], "trigger": observation["trigger"],
                "min_n": observation.get("min_n"), "columns": _columns(observation, level)}

    return {"source": "tool", "label": LABEL, "interpretation": "observational_difference",
            "first": entry(first), "latest": entry(latest), "n_history": len(history),
            "changed_since_first": (None if len(history) < 2 else
                                    [c.get("metrics") for c in _columns(first, level)]
                                    != [c.get("metrics") for c in _columns(latest, level)])}


def observation_reason(record):
    if record.get("kind") != "measure":
        return "not_a_measure"
    if not record.get("observations"):
        return "no_declaration"
    return None


def comparison_table(record, level, linked=()):
    """**媒体をまたぐ比較の表**（設計 §5）。列 = 媒体（この施策の宣言と、`tried`・
    `trial`〔追試・§9-2〕でつながった施策の宣言）、行 = 指標。値は `observational_difference` と両群の分母。
    欠測は null と理由。取れる列が 1 つも無ければ None。"""
    sources = [(record, level, "this")] + [(row, row_level, kind) for row, row_level, kind in linked]
    columns, rows = [], {metric: [] for metric in _metrics()}
    for source, source_level, kind in sources:
        history = source.get("observations") or []
        if source.get("kind") != "measure" or not history:
            continue
        latest = history[-1]
        for entry in _columns(latest, source_level):
            columns.append({"plaza_id": source["plaza_id"], "source": kind,
                            "medium": entry.get("medium"),
                            "account": (entry.get("account") if source_level in ("own", "owner")
                                        else None),
                            "observed_at": latest["at"], "observed": entry.get("observed"),
                            "reason": entry.get("reason")})
            for metric in rows:
                value = (entry.get("metrics") or {}).get(metric)
                rows[metric].append(
                    {"observational_difference": value["observational_difference"],
                     "baseline_n_eligible": value["baseline_n_eligible"],
                     "changed_n_eligible": value["changed_n_eligible"],
                     "reason": value["reason"]} if value else
                    {"observational_difference": None, "baseline_n_eligible": None,
                     "changed_n_eligible": None, "reason": entry.get("reason") or "not_observed"})
    if not columns:
        return None
    from . import plaza
    return {"source": "tool", "label": LABEL, "interpretation": "observational_difference",
            "metrics": list(rows), "columns": columns, "rows": rows,
            # 追試の数（§9-2）。再現した・しなかった・試していないを同じ重さで並べる。
            "trials": plaza.trial_counts(record)}


def verdict_view(verdict, level, record=None):
    if not verdict:
        return None
    reason = verdict.get("reason")
    if level not in ("own", "owner"):
        reason = ((record or {}).get("open_copy") or {}).get("verdict_reason")
    return {"verdict": verdict["verdict"], "at": verdict.get("at"), "reason": reason,
            "by": verdict.get("by") if level in ("own", "owner") else None}
