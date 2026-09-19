"""Read-only linkage of unverified study declarations and owned observations."""
from __future__ import annotations

import datetime
import json
import os
import stat
import sys

from . import accounts, after_cli, analytics_comparison as comparison, engagements, jst, measured
from .analytics_report import _markdown_text

MAX_BYTES = 1024 * 1024
MAX_IDS = 1000


class StudyError(ValueError):
    """Bounded diagnostic, never echo document values or source contents."""


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - set(optional):
        raise StudyError("施策JSONの必須キー・未知キー・object形式を確認してください")


def _string(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise StudyError("施策JSONの文字列が空・不正型・長さ上限超過です")


def _time(value):
    try:
        return jst.parse(value)
    except (ValueError, OverflowError):
        return None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StudyError("施策JSONに重複キーがあります")
        result[key] = value
    return result


def _reject_constant(_):
    raise StudyError("施策JSONに非有限数があります")


def load_declaration(path, now):
    try:
        # Nonblocking open prevents FIFO input from waiting indefinitely.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise StudyError("施策JSONは通常ファイルで指定してください")
            data = stream.read(MAX_BYTES + 1)
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, StudyError):
            raise
        raise StudyError("施策JSONファイルを読めません") from None
    if len(data) > MAX_BYTES:
        raise StudyError("施策JSONの上限は1MiBです")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise StudyError("施策JSONを解釈できません（形式・重複キーを確認）") from None
    return validate_declaration(value, now)


def validate_declaration(value, now):
    required = {"schema_version", "id", "account", "hypothesis", "change", "decision", "baseline_post_ids", "changed_post_ids"}
    _keys(value, required)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise StudyError("施策JSONのschema_versionは1だけに対応しています")
    for key, maximum in (("id", 256), ("account", 256), ("hypothesis", 4000), ("change", 4000)):
        _string(value[key], maximum)
    decision = value["decision"]
    _keys(decision, {"status"}, {"by", "at"})
    if decision["status"] not in ("proposed", "adopted"):
        raise StudyError("decision.statusはproposedまたはadoptedです")
    if decision["status"] == "adopted" and not {"by", "at"} <= set(decision):
        raise StudyError("採用宣言にはbyとatが必要です")
    if "by" in decision:
        _string(decision["by"], 256)
    if "at" in decision:
        at = _time(decision["at"])
        if at is None or at > now:
            raise StudyError("decision.atは生成時刻以前のtimezone付き日時です")
    for key in ("baseline_post_ids", "changed_post_ids"):
        ids = value[key]
        if not isinstance(ids, list) or len(ids) > MAX_IDS:
            raise StudyError("投稿IDは各群1000件以内の配列です")
        for post_id in ids:
            _string(post_id, 2048)
        if len(ids) != len(set(ids)):
            raise StudyError("投稿IDが群内で重複しています")
    if set(value["baseline_post_ids"]) & set(value["changed_post_ids"]):
        raise StudyError("投稿IDが両群で重複しています")
    return value


def _eligibility_forecast(population):
    immature = [row for row in population["evidence"] if row["status"] == "immature"]
    if not immature:
        return None
    return {"posts_immature": len(immature),
            "all_eligible_at": jst.iso(max(_time(row["posted_at"]) for row in immature) + datetime.timedelta(hours=24)),
            "basis": "posted_at_plus_24h"}


from .report_details import detailed

@detailed
def answer(path, *, min_n=5, now=None):
    if type(min_n) is not int or min_n < 1:
        raise StudyError("min_nは1以上の整数です")
    now = now if now is not None else jst.now_jst()
    if not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise StudyError("nowはtimezone付き日時です")
    now = jst.to_jst(now).replace(microsecond=0)
    declaration = load_declaration(path, now)
    result = {"report_type": "study_review", "schema_version": 1,
              "generated_at": jst.iso(now), "declaration": declaration,
              "decision_provenance": "user_declared_unverified",
              "interpretation": "observational_difference", "min_n": min_n,
              "measurement": comparison._measurement_contract(),
              "selection": {"basis": "explicit_post_ids_and_posted_at", "timezone": "Asia/Tokyo",
                            "baseline": "posted_at < decision.at", "changed": "decision.at <= posted_at < generated_at"},
              "observations": None, "comparison": None, "data_updated_at": None, "eligibility_forecast": None,
              "data_updated_at_scope": "selected_observations", "cannot_say": [],
              "limitations": ["採用宣言は利用者の入力。本人確認・投稿承認を証明しない",
                              "差は観測値の差であり、因果効果・成功失敗・推奨ではない",
                              "根投稿の明示IDだけを対象とし、SNS上の全投稿や型の真正性を保証しない",
                              "測定は24時間以上30時間未満。母数と時間条件以外の交絡は調整しない",
                              "data_updated_atは採用観測の最大時刻であり全台帳の鮮度ではない"],
              "provenance": {"source": "user-declaration-and-own-ledgers", "advice": "excluded"}}
    if declaration["decision"]["status"] == "proposed":
        result["cannot_say"].append("未採用の提案なので観測台帳を読まず、比較集計を行わない")
        return result
    name = declaration["account"]
    try:
        cfg = accounts.load_account(name)
        measured_result = measured.load(name, observation_metadata=True)
        eng = engagements.load(cfg, name)
    except (accounts.AccountError, ValueError, TypeError, AttributeError, OverflowError):
        raise StudyError("対象accountまたは比較に必要な台帳を読めません") from None
    by_id = {str(post["post_id"]): post for post in measured_result["posts"]}
    known_replies = {str(row["post_id"]) for row in eng["rows"]
                     if isinstance(row, dict) and row.get("account") == name and row.get("post_id")}
    decision_at = _time(declaration["decision"]["at"])
    populations = {}
    for group in ("baseline", "changed"):
        items, excluded = [], []
        for post_id in declaration[group + "_post_ids"]:
            post = by_id.get(post_id)
            reason = "unknown_or_unowned_id" if post is None else comparison._root_exclusion(post, known_reply=post_id in known_replies)
            posted = _time(post.get("posted_at")) if post else None
            if reason is None:
                if posted is None:
                    reason = "invalid_posted_at"
                elif posted >= now:
                    reason = "at_or_after_report_time"
                elif (group == "baseline" and posted >= decision_at) or (group == "changed" and posted < decision_at):
                    reason = "wrong_side_of_decision_time"
            if reason:
                excluded.append({"post_id": post_id, "reason": reason})
            else:
                items.append((post_id, posted, post))
        # The common population helper applies exact same measurement and metric
        # selection as period comparisons; IDs and decision time select the group.
        start = min((item[1] for item in items), default=now)
        population = comparison._population(items, start, now, now, min_n)
        from .analytics_shapes import attach as attach_shapes
        attach_shapes(name, population, now)
        population.update(n_requested=len(declaration[group + "_post_ids"]), excluded=excluded,
                          n_excluded=len(excluded))
        populations[group] = population
    result["observations"] = populations
    result["comparison"] = comparison._differences(populations["baseline"], populations["changed"], min_n)
    updates = [p["data_updated_at"] for p in populations.values() if p["data_updated_at"]]
    result["data_updated_at"] = max(updates) if updates else None
    result["incomplete_sources"] = {"measured_files": len(measured_result.get("broken", [])), "engagement_files": eng.get("broken", 0)}
    if any(result["incomplete_sources"].values()):
        result["cannot_say"].append("読めない台帳があるため対象投稿や分類を網羅できない")
    if any(not entry["comparable"] for entry in result["comparison"].values()):
        result["cannot_say"].append("一方または両群の有効母数が不足する指標は差を算出しない")
    result["eligibility_forecast"] = _eligibility_forecast(populations["changed"])
    if result["eligibility_forecast"] is not None:
        result["cannot_say"].append("forecast_assumes_collection_runs")
    return result


def render_markdown(payload):
    declaration = payload["declaration"]
    lines = ["# Study review", "", f"施策: {_markdown_text(declaration['id'])}",
             f"宣言: {_markdown_text(declaration['decision']['status'])}（利用者入力・本人確認なし）",
             f"仮説: {_markdown_text(declaration['hypothesis'])}",
             f"変更: {_markdown_text(declaration['change'])}", ""]
    if payload["observations"] is not None:
        for label, population in payload["observations"].items():
            lines.append(f"{label}: 指定 {population['n_requested']}、対象 {population['n_total']}、時間適合 {population['n_eligible']}、欠測 {population['n_missing']}、除外 {population['n_excluded']}")
        for key, entry in payload["comparison"].items():
            delta = entry["absolute_median_change"]
            lines.append(f"- {key} 中央値の差: {delta if delta is not None else '判断不可'}（観測上の差）")
    lines += ["", "## 制約", ""]
    lines += ["- " + _markdown_text(line) for line in payload["limitations"] + payload["cannot_say"]]
    lines += ["", "## 宣言と観測の構造化データ", ""]
    lines += ["    " + line for line in json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).splitlines()]
    return "\n".join(lines) + "\n"


def cmd_study_report(args):
    try:
        result = answer(args.file, min_n=args.min_n)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) if args.json else render_markdown(result))
    except (StudyError, after_cli.AfterError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0
