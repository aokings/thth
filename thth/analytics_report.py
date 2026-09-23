"""Read-only, versioned activity snapshot built from the existing after calculation."""
from __future__ import annotations

import copy
import datetime
import json
import sys

from . import accounts, after_cli, jst

DEFAULT_WINDOW_DAYS = 7
# `goal`（設計 3.6.0 §A2）: 投稿の目的ごとの層と、目的ごとの物差し。
BY_CHOICES = ("kind", "hour_band", "topic", "tag", "attachment_kind", "goal")


from .report_details import detailed

@detailed
def answer(account_name=None, *, project=None, window_days=DEFAULT_WINDOW_DAYS,
           min_n=after_cli.DEFAULT_MIN_N, now=None, compare_previous=False, by=None,
           trusted_names=None, allowed_names=None):
    """One payload for CLI Markdown/JSON and MCP; never collect or persist data."""
    for label, value in (("account", account_name), ("project", project)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise after_cli.AfterError(f"{label} は空でない文字列です")
    if (account_name is None) == (project is None):
        raise after_cli.AfterError("account か project のどちらか一方が要ります")
    for label, value in (("window_days", window_days), ("min_n", min_n)):
        if type(value) is not int or value < 1:
            raise after_cli.AfterError(f"{label} は 1 以上の整数です")
    if type(compare_previous) is not bool:
        raise after_cli.AfterError("compare_previous は boolean です")
    # `kind` はトピックの棚（型）で、`attachment_kind` は添付の種類（第 10 段）。
    # **別の層**なので `kind` の語彙には足さない（設計 2.13.0 §5）。
    if by is not None and (by not in BY_CHOICES or not compare_previous):
        raise after_cli.AfterError("by は compare_previous と " + "/".join(BY_CHOICES) + " の指定が必要です")
    now = now if now is not None else jst.now_jst()
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        raise after_cli.AfterError("now はタイムゾーン付きの日時です")
    # Match the precision of the reported boundaries and the existing timestamps.
    now = jst.to_jst(now).replace(microsecond=0)
    try:
        start = now - datetime.timedelta(days=window_days)
    except OverflowError as exc:
        raise after_cli.AfterError("window_days が日時の範囲を超えています") from exc
    if compare_previous:
        from . import analytics_comparison
        return analytics_comparison.answer(account_name, project=project, window_days=window_days,
                                           min_n=min_n, now=now, by=by,
                                           trusted_names=trusted_names,
                                           allowed_names=allowed_names)
    source = after_cli.answer(account_name, project=project, window_days=window_days,
                              min_n=min_n, now=now, trusted_names=trusted_names,
                              allowed_names=allowed_names)
    nodes = source["by_account"] if project is not None else {account_name: source}
    if not nodes:
        raise after_cli.AfterError("project に読める account がありません")
    nodes = copy.deepcopy(nodes)
    for name, node in nodes.items():
        # Advice is not an observation. Keep it outside this snapshot contract.
        node.pop("one_thing_to_change", None)
        node["provenance"].pop("updated", None)
        from .collection_status import summarize as collection_summary
        node["collection"], collection_reasons = collection_summary(name, now)
        node["cannot_say"].extend(collection_reasons)
        from . import analytics_comparison as comparison, measured, engagements
        cfg = accounts.load_account(name)
        ledger = measured.load(name, observation_metadata=True)
        reply_ids = {str(row.get("post_id")) for row in engagements.load(cfg, name)["rows"]
                     if isinstance(row, dict) and row.get("account") == name}
        items = [(str(p["post_id"]), comparison._timestamp(p.get("posted_at")), p)
                 for p in ledger["posts"]
                 if not comparison._root_exclusion(p, known_reply=str(p["post_id"]) in reply_ids)]
        # Legacy snapshot includes its upper boundary; additive strict marks do too.
        curves = comparison._marks_population(items, start, now + datetime.timedelta(microseconds=1), now, min_n)
        from .analytics_shapes import attach as attach_shapes
        attach_shapes(name, curves, now, allowed_names=allowed_names)
        node["posts"].update(curves)
        node["posts"]["views_24h"].update(comparison._spread(
            [p["views_24h"] for p in node["posts"]["by_post"] if p["views_24h"] is not None], min_n))
        for key in ("views_24h", "likes_24h", "replies_back_24h"):
            node["engagements"][key].update(comparison._spread(
                [p[key] for p in node["engagements"]["by_branch"] if p[key] is not None], min_n))

        from .analytics_threads import summarize
        node["engagements"].update(summarize(name, cfg, engagements.load(cfg, name),
            {str(p["post_id"]): p for p in ledger["posts"]}, start,
            now + datetime.timedelta(microseconds=1), now, min_n,
            allowed_names=allowed_names))
        lookup = {p["post_id"]: p for p in curves["marks_by_post"]}
        for post in node["posts"]["by_post"]:
            post["marks"] = lookup.get(str(post["post_id"]), {}).get("marks")
            post["shape_at"] = lookup.get(str(post["post_id"]), {}).get("shape_at")

    return {
        "schema_version": 1, "report_type": "activity_snapshot",
        "generated_at": jst.iso(now), "data_updated_at": None,
        "data_updated_at_reason": "複数台帳の最終更新時刻をこのレポートでは検証していない",
        "period": {"start": jst.iso(start), "end": jst.iso(now),
                   "start_inclusive": True, "end_inclusive": True,
                   "timezone": "Asia/Tokyo", "basis": "posted_at",
                   "window_days": window_days, "mode": "rolling"},
        "filters": {"account": account_name, "project": project}, "min_n": min_n,
        "by_account": nodes,
        "measurement": comparison._measurement_contract(),
        "cannot_say": list(source.get("cannot_say", [])) if project is not None else [],
        "limitations": [
            "前期間との比較・因果推論・推奨行動は含まない",
            "媒体・account をまたぐ合計や順位は作らない",
            "投稿の母集団は所有が確認できた実測台帳の根投稿。SNS上の全投稿ではない",
            "marks=24 は正確な経過24時間を保証しない。by_post の age_hours / marks_collapsed を参照",
            "返信の replies_back_24h は返信台帳へのフォールバックを含み、24時間内の反応とは限らない",
            "反応あり件数は観測された反応。欠測を無反応と断定できない",
        ],
        "provenance": {"source": after_cli.SCHEMA_SOURCE,
                       "calculation": "after.answer", "advice": "excluded"},
    }


def _markdown_text(value):
    """Keep ledger strings inert in the human-readable summary."""
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in ("\\", "`", "*", "_", "[", "]", "|", "#"):
        text = text.replace(char, "\\" + char)
    return text.replace("\r", " ").replace("\n", " ")


def render_markdown(payload):
    """Human summary and complete evidence are rendered from one payload."""
    if payload.get("report_type") == "period_comparison":
        from . import analytics_comparison
        return analytics_comparison.render_markdown(payload)
    period = payload["period"]
    lines = ["# Activity snapshot", "",
             f"対象期間: {period['start']} ～ {period['end']}（両端を含む、投稿日時基準）",
             f"生成時刻: {payload['generated_at']}。データ更新時刻: 不明。",
             f"中央値の最小母数: {payload['min_n']}。", ""]
    for account_name, node in payload["by_account"].items():
        posts = node["posts"]
        metric = posts["views_24h"]
        median = "不明" if metric["median"] is None else metric["median"]
        lines += [f"## {_markdown_text(account_name)}", "",
                  f"自分の根投稿: {posts['n']} 件。24h views 中央値: {median}（有効 n={metric['n']}）。",
                  f"絡みに行った返信: {node['engagements']['n']} 件。観測された反応あり: {node['engagements']['reacted']} 件。",
                  "", "判断できないこと:", ""]
        lines += ["- " + _markdown_text(reason) for reason in node["cannot_say"]]
        if not node["cannot_say"]:
            lines.append("- 個別の不足理由なし。以下の共通制約は適用されます。")
        lines.append("")
    lines += ["## 全体の制約", ""]
    lines += ["- " + _markdown_text(reason) for reason in payload["limitations"]]
    lines += ["- " + _markdown_text(reason) for reason in payload["cannot_say"]]
    lines += ["", "## 根拠と構造化データ", "",
              "以下は同じ結果の全項目（schema_version=1）。", ""]
    # Indented JSON avoids fence termination by untrusted ledger string values.
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    lines += ["    " + line for line in data.splitlines()]
    return "\n".join(lines) + "\n"


def cmd_analytics_report(args):
    try:
        payload = answer(args.account, project=args.project,
                         window_days=args.window_days, min_n=args.min_n,
                         compare_previous=getattr(args, "compare_previous", False), by=getattr(args, "by", None))
        output = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
                  if args.json else render_markdown(payload))
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (after_cli.AfterError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(output)
    return 0
