"""Read-only period comparison. Counts never cross accounts or population kinds."""
from __future__ import annotations

import collections
import datetime
import json
import math

from . import accounts, after_cli, engagements, jst, measured

METRICS = ("views", "likes", "replies")



def _timestamp(raw):
    try:
        return jst.parse(raw)
    except (ValueError, OverflowError):
        return None

def _measurement_contract():
    return {"mark": 24, "minimum_age_hours_inclusive": 24,
            "maximum_age_hours_exclusive": 30, "selection": "earliest_eligible_observation",
            "age_basis": "collected_at_minus_posted_at", "collapsed_marks_allowed": False,
            "quartile_method": "tukey_hinges", "quartile_algorithm": "median_of_halves_excluding_odd_center"}


def _metric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    # Finite nonnegative operands can overflow when summed before division.
    return ordered[middle - 1] / 2 + ordered[middle] / 2


def _spread(values, min_n):
    ordered = sorted(values)
    n = len(ordered)
    if n < min_n:
        return {"iqr": None, "min": None, "max": None, "spread_reason": "below_min_n"}
    middle = n // 2
    return {"iqr": _median(ordered[(n + 1) // 2:]) - _median(ordered[:middle]) if n >= 4 else None,
            "min": ordered[0], "max": ordered[-1],
            "spread_reason": None if n >= 4 else "too_few_for_quartiles"}


def _observation(post, posted, now, mark=24):
    """Select one earliest time-eligible observation, independently of metric values."""
    candidates, rejected = [], collections.Counter()
    for row in (post or {}).get("rows", []):
        if not isinstance(row, dict):
            rejected["malformed_row"] += 1
            continue
        collected = _timestamp(row.get("collected_at"))
        marks = row.get("marks")
        reason = None
        if _timestamp(row.get("posted_at")) != posted:
            reason = "invalid_or_conflicting_posted_at"
        elif collected is None:
            reason = "invalid_collected_at"
        elif collected > now:
            reason = "future_observation"
        elif not isinstance(marks, list) or any(type(m) is not int for m in marks) or marks != [mark] or row.get("marks_collapsed"):
            reason = f"missing_or_collapsed_{mark}h_mark"
        else:
            age = (collected - posted).total_seconds() / 3600
            if age < mark:
                reason = "premature_observation"
            elif age >= mark * 1.25:
                reason = "late_observation"
        if reason:
            rejected[reason] += 1
        else:
            # A deterministic tie breaker avoids depending on file order. Do not
            # pick a later row just because it has a missing metric filled in.
            candidates.append((collected, json.dumps(row, sort_keys=True), row, age))
    if not candidates:
        return None, dict(rejected)
    collected, _tie, row, age = min(candidates, key=lambda item: item[:2])
    metrics = row.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    return {"collected_at": jst.iso(collected), "actual_age_hours": age,
            "metrics": {key: _metric(metrics.get(key)) for key in METRICS}}, dict(rejected)


def _population(items, start, end, now, min_n):
    items = list(items)
    evidence = []
    for post_id, posted, measured_post in items:
        if not start <= posted < end:
            continue
        observation, rejected = _observation(measured_post, posted, now)
        immature = (now - posted).total_seconds() < 24 * 3600
        status = "eligible" if observation else "immature" if immature else "incomplete"
        evidence.append({"post_id": post_id, "posted_at": jst.iso(posted),
                         "status": status, "observation": observation,
                         "rejected_observations": rejected,
                         "missing_reason": None if observation else (
                             "not_yet_24h" if immature else "no_eligible_24h_observation")})
    evidence.sort(key=lambda row: (row["posted_at"], row["post_id"]))
    metrics = {}
    for key in METRICS:
        values = [row["observation"]["metrics"][key] for row in evidence
                  if row["observation"] and row["observation"]["metrics"][key] is not None]
        metrics[key] = {"n_total": len(evidence), "n_eligible": len(values),
                        "n_missing": len(evidence) - len(values),
                        "median": _median(values) if len(values) >= min_n else None, **_spread(values, min_n)}
    counts = collections.Counter(row["status"] for row in evidence)
    times = [row["observation"]["collected_at"] for row in evidence if row["observation"]]
    return {"n_total": len(evidence), "n_eligible": counts["eligible"],
            "n_missing": counts["immature"] + counts["incomplete"],
            "n_immature": counts["immature"], "n_incomplete": counts["incomplete"],
            "metrics": metrics, "evidence": evidence,
            "data_updated_at": max(times) if times else None,
            **_marks_population(items, start, end, now, min_n)}


def _marks_population(items, start, end, now, min_n):
    from .collect import AGE_MARKS_HOURS
    by_post = []
    for post_id, posted, post in items:
        if not start <= posted < end:
            continue
        marks, rejected, reasons = {}, {}, {}
        for mark in AGE_MARKS_HOURS:
            key = str(mark)
            marks[key], rejected[key] = _observation(post, posted, now, mark)
            reasons[key] = None if marks[key] else (
                "not_yet_mark" if now < posted + datetime.timedelta(hours=mark)
                else "no_eligible_mark_observation")
        by_post.append({"post_id": post_id, "posted_at": jst.iso(posted), "marks": marks,
                        "missing_reasons": reasons, "rejected_observations": rejected})
    by_post.sort(key=lambda row: (row["posted_at"], row["post_id"]))
    by_mark = {}
    for mark in AGE_MARKS_HOURS:
        key = str(mark)
        observations = [p["marks"][key] for p in by_post if p["marks"][key]]
        metrics = {}
        for metric in METRICS:
            values = [o["metrics"][metric] for o in observations if o["metrics"][metric] is not None]
            metrics[metric] = {"n_eligible": len(values),
                               "median": _median(values) if len(values) >= min_n else None, **_spread(values, min_n)}
        by_mark[key] = {"n_total": len(by_post), "n_eligible": len(observations), "metrics": metrics,
                        "measurement": {"minimum_age_hours_inclusive": mark,
                            "maximum_age_hours_exclusive": mark * 1.25,
                            "selection": "earliest_eligible_observation", "collapsed_marks_allowed": False}}
    return {"by_mark": by_mark, "marks_by_post": by_post}


def _root_exclusion(post, *, known_reply=False):
    """Shared root-population gate for period and explicitly declared studies."""
    if known_reply or post.get("reply_to"):
        return "reply_not_root"
    if post.get("source") != "queue" or not post.get("reply_to_known"):
        return "root_or_reply_unknown"
    if any(row.get("reply_to") or row.get("source") != "queue"
           for row in post.get("rows", []) if isinstance(row, dict)):
        return "conflicting_root_classification"
    if _timestamp(post.get("posted_at")) is None:
        return "invalid_root_posted_at"
    return None


def _differences(previous, current, min_n):
    deltas = {}
    for metric in METRICS:
        before, after = previous["metrics"][metric], current["metrics"][metric]
        eligible = before["n_eligible"] >= min_n and after["n_eligible"] >= min_n
        deltas[metric] = {"absolute_median_change": after["median"] - before["median"] if eligible else None,
                          "comparable": eligible,
                          "comparability_scope": "measurement_band_and_minimum_sample_only",
                          "reason": None if eligible else "insufficient_samples_in_one_or_both_periods"}
    return deltas


def _account(name, previous_start, current_start, now, min_n, by=None):
    cfg = accounts.load_account(name)
    # Existing loader supplies account-at-observation ownership guarantees. A
    # malformed ledger that prevents loading is an error, never an empty sample.
    try:
        measured_result = measured.load(name, observation_metadata=True)
        eng = engagements.load(cfg, name)
    except (TypeError, ValueError, AttributeError) as exc:
        raise after_cli.AfterError(f"{name}: 比較に必要な台帳の形式を読めません") from exc
    exclusions = collections.Counter()
    by_id = {str(p["post_id"]): p for p in measured_result["posts"]}
    own_engagements = collections.defaultdict(list)
    for row in eng["rows"]:
        if not isinstance(row, dict) or row.get("account") != name or not row.get("post_id"):
            exclusions["unattributed_or_invalid_engagement"] += 1
            continue
        own_engagements[str(row["post_id"])].append(row)
    reply_items = []
    for post_id, rows in own_engagements.items():
        times = [_timestamp(row.get("posted_at")) for row in rows]
        if None in times or len(set(times)) != 1:
            exclusions["invalid_or_conflicting_engagement_posted_at"] += 1
            continue
        posted = times[0]
        post = by_id.get(post_id)
        if post and _timestamp(post.get("posted_at")) != posted:
            exclusions["conflicting_measured_engagement_posted_at"] += 1
            continue
        reply_items.append((post_id, posted, post))
    root_items = []
    for post_id, post in by_id.items():
        reason = _root_exclusion(post, known_reply=post_id in own_engagements)
        if reason:
            if reason != "reply_not_root":
                exclusions[reason] += 1
            continue
        posted = _timestamp(post.get("posted_at"))
        root_items.append((post_id, posted, post))
    exclusions["unknown_ownership"] = len(measured_result.get("posts_unknown_ownership", []))
    broken = {"measured_files": len(measured_result.get("broken", [])),
              "engagement_files": eng.get("broken", 0)}
    node = {"medium": cfg.get("media"), "excluded_records": dict(exclusions),
            "incomplete_sources": broken, "cannot_say": []}
    from .collection_status import summarize as collection_summary
    node["collection"], collection_reasons = collection_summary(name, now)
    node["cannot_say"].extend(collection_reasons)
    for kind, items in (("posts", root_items), ("engagements", reply_items)):
        previous = _population(items, previous_start, current_start, now, min_n)
        current = _population(items, current_start, now, now, min_n)
        deltas = _differences(previous, current, min_n)
        node[kind] = {"previous": previous, "current": current, "comparison": deltas}
        if by:
            from . import threadshape, topics
            lookup, shelf_broken = after_cli._kind_lookup(name)
            groups = collections.defaultdict(list)
            for item in items:
                pid, posted, post = item
                source = own_engagements[pid][0] if kind == "engagements" else post
                topic, valid = after_cli._normalized_topic(source.get("topic"))
                value = (topic if by == "topic" and valid else
                         threadshape.hour_band(posted) if by == "hour_band" else
                         lookup(topic) if by == "kind" and valid else None)
                if by == "kind" and value not in topics.KINDS:
                    value = None
                groups[value or "unknown"].append(item)
            strata = {}
            for label, members in sorted(groups.items()):
                before = _population(members, previous_start, current_start, now, min_n)
                after = _population(members, current_start, now, now, min_n)
                if before["n_total"] or after["n_total"]:
                    strata[label] = {"previous": before, "current": after,
                                     "comparison": _differences(before, after, min_n)}
            node[kind]["stratified"] = {"by": by, "strata": strata,
                "reconciliation": {period: {"sum_n_total": sum(g[period]["n_total"] for g in strata.values()),
                                            "n_total": node[kind][period]["n_total"]}
                                   for period in ("previous", "current")},
                "kind_basis": "current_topic_shelf" if by == "kind" else None,
                "kind_shelf_broken": shelf_broken() if by == "kind" else None}

    from .analytics_threads import summarize
    node["engagements"].update(summarize(name, cfg, eng, by_id, current_start, now, now, min_n))
    if any(broken.values()):
        node["cannot_say"].append("読めない台帳があり、母集団全体の件数・変化は判断できない")
    node["cannot_say"].append("比較は観測できた標本だけ。差の原因・施策の効果・推奨行動は判断しない")
    return node


from .report_details import detailed

@detailed
def answer(account_name, *, project, window_days, min_n, now, by=None):
    try:
        current_start = now - datetime.timedelta(days=window_days)
        previous_start = current_start - datetime.timedelta(days=window_days)
    except OverflowError as exc:
        raise after_cli.AfterError("window_days が比較日時の範囲を超えています") from exc
    names, cannot_say = after_cli._resolve_names(account_name=account_name, project=project)
    nodes = {}
    for name in names:
        try:
            nodes[name] = _account(name, previous_start, current_start, now, min_n, by)
        except accounts.AccountError as exc:
            if project is None:
                raise
            cannot_say.append(f"{name}: {exc}")
    if not nodes:
        raise after_cli.AfterError("project に読める account がありません")
    periods = {}
    for label, start, end in (("previous", previous_start, current_start), ("current", current_start, now)):
        periods[label] = {"start": jst.iso(start), "end": jst.iso(end),
                          "start_inclusive": True, "end_inclusive": False,
                          "timezone": "Asia/Tokyo", "basis": "posted_at", "window_days": window_days}
    updates = [node[kind][period]["data_updated_at"] for node in nodes.values()
               for kind in ("posts", "engagements") for period in periods
               if node[kind][period]["data_updated_at"]]
    return {"report_type": "period_comparison", "schema_version": 1,
            "generated_at": jst.iso(now), "data_updated_at": max(updates) if updates else None,
            "data_updated_at_basis": "latest_selected_observation",
            "data_updated_at_scope": "selected_observations", "periods": periods,
            "filters": {"account": account_name, "project": project}, "min_n": min_n,
            "measurement": _measurement_contract(),
            "by_account": nodes, "cannot_say": cannot_say,
            "limitations": ["24時間ちょうどの測定ではなく24時間以上30時間未満の観測",
                            "data_updated_atは採用観測だけの最終時刻。全台帳の鮮度・最終採取試行ではない",
                            "comparableは測定時間帯と母数だけ。原稿の型や変更理由を含むmeasured.comparabilityは未検証",
                            "現在期間の新しい投稿は未成熟。欠測を0と扱わない",
                            "根投稿は所有確認済み実測台帳、返信はaccount一致の絡み台帳が母集団。SNS上の全投稿ではない",
                            "返信台帳の累積件数による補完は行わない",
                            "媒体・account・根投稿と返信を混ぜず、因果推論・推奨・百分率変化を出さない"],
            "provenance": {"source": "own-ledgers", "calculation": "analytics_comparison", "advice": "excluded"}}


def render_markdown(payload):
    from .analytics_report import _markdown_text
    lines = ["# Period comparison", ""]
    for label, period in payload["periods"].items():
        lines.append(f"{label}: {period['start']} ≦ posted_at < {period['end']}（Asia/Tokyo）")
    lines += [f"生成: {payload['generated_at']}。採用観測の最終更新: {payload['data_updated_at'] or '不明'}。",
              "測定条件: 投稿後24時間以上30時間未満、単独の24h刻み。", ""]
    for name, node in payload["by_account"].items():
        lines += [f"## {_markdown_text(name)}", ""]
        for kind in ("posts", "engagements"):
            group = node[kind]
            lines.append(f"{kind}: 前期間 n={group['previous']['n_total']}、現在期間 n={group['current']['n_total']}")
            for metric, change in group["comparison"].items():
                delta = change["absolute_median_change"]
                ns = [group[p]["metrics"][metric]["n_eligible"] for p in ("previous", "current")]
                lines.append(f"- {metric} 中央値の差: {delta if delta is not None else '判断不可'}（有効 n={ns[0]} → {ns[1]}）")
        lines += ["- " + _markdown_text(reason) for reason in node["cannot_say"]]
    lines += ["", "## 制約", ""]
    lines += ["- " + _markdown_text(reason) for reason in payload["limitations"] + payload["cannot_say"]]
    lines += ["", "## 根拠と構造化データ", ""]
    lines += ["    " + line for line in json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).splitlines()]
    return "\n".join(lines) + "\n"
