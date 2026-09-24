"""投稿ごとのクリック（設計 3.9.0 §A・`thth analytics-report <account> --per-post-clicks [--since]`）。

**目的（goal）を問わない表**。unique_url_72h を満たす投稿（そのリンク先を前後 72 時間で
1 本の投稿しか使っていない・`click_attribution`）を、goal が click かどうかに関わらず
並べる。**goal の層（`--by goal`）とは混ぜない**——層ごとに分けず、行に goal も持たない。
goal を書く口が無かった頃の投稿（unrecorded）でも、リンク先が記録にあれば出る。

各行: 72 時間（投稿日を含む 3 暦日）のクリック・クリック率（/24h views）・窓の後の
クリック（参考）・窓の前のクリック（`clicks_before_post`）。**窓の前と後は窓の和に
足さない**。`clicks_before_post` が 1 以上なら「THTH を通していない投稿か Threads の外の
クリックが混ざっている可能性」の静的な 1 行を添える（原因は言わない）。

読むだけ。台帳を書かない。数には分母、欠測は null と理由。
"""
from __future__ import annotations

import datetime
import json
import sys

from . import accounts, jst, read_window

DEFAULT_WINDOW_DAYS = 7
POPULATION = "all_goals"
POPULATION_NOTE = ("goal を問わない（--by goal の層とは別の表・unrecorded の投稿も"
                   "リンク先が記録にあれば並ぶ）")
BEFORE_PRESENT = "clicks_before_post_present"
BEFORE_PRESENT_NOTE = ("投稿日より前にそのリンク先のクリックがあります。THTH を通していない投稿か"
                       "Threads の外のクリックが混ざっている可能性があります（原因は言えません）")


def _median(values):
    from . import analytics_comparison as comparison
    return comparison._median(values) if values else None


def _row(index, post_id, posted, post, now):
    """1 本ぶん。unique_url_72h を満たさなければ `(None, 理由)`。"""
    from . import analytics_comparison as comparison
    observation, _rejected = comparison._observation(post, posted, now, 24)
    views = ((observation or {}).get("metrics") or {}).get("views")
    attributed = index.attribute(post_id, posted, views)
    if attributed["basis"] is None:
        return None, attributed["cannot_say"]
    urls = attributed["urls"]
    before = index.before_post(post_id, posted, urls)
    after = index.after_window(post_id, posted, urls)
    clicks = attributed["clicks_72h"]
    share = None
    if clicks is not None and before["clicks"] is not None and after["clicks"] is not None:
        denominator = clicks + before["clicks"] + after["clicks"]
        share = {"value": round(clicks / denominator, 4) if denominator else None,
                 "numerator": clicks, "denominator": denominator,
                 "basis": "clicks_72h / (clicks_before_post + clicks_72h + clicks_after_window)",
                 "reason": None if denominator else "no_clicks"}
    return {"post_id": post_id, "posted_at": jst.iso(posted), "urls": urls,
            "window": attributed["window"], "window_days": attributed["window_days"],
            # **窓の和だけ**。窓の前（before）と後（after）はここに足さない。
            "clicks_72h": clicks,
            "clicks_missing": attributed["clicks_missing"],
            "views_24h": attributed["views_24h"], "click_rate": attributed["click_rate"],
            "rate_basis": attributed["rate_basis"], "rate_missing": attributed["rate_missing"],
            "clicks_after_window": after["clicks"], "after_window": after,
            "clicks_before_post": before["clicks"], "before_post": before,
            "window_share": share}, None


def _summary(rows, n_posts):
    clicks = [row["clicks_72h"] for row in rows if row["clicks_72h"] is not None]
    rates = [row["click_rate"] for row in rows if row["click_rate"] is not None]
    before = [row["clicks_before_post"] for row in rows if row["clicks_before_post"] is not None]
    after = [row["clicks_after_window"] for row in rows if row["clicks_after_window"] is not None]
    shares = [row["window_share"] for row in rows
              if row["window_share"] is not None and row["window_share"]["value"] is not None]
    numerator = sum(share["numerator"] for share in shares)
    denominator = sum(share["denominator"] for share in shares)
    values = [share["value"] for share in shares]
    return {"n_posts": n_posts, "n_attributable": len(rows),
            "clicks_72h": {"sum": sum(clicks), "median": _median(clicks), "n": len(clicks),
                           "denominator": len(rows)},
            "click_rate": {"median": _median(rates), "n": len(rates), "denominator": len(rows),
                           "rate_basis": "clicks_72h / views_24h"},
            "clicks_after_window": {"sum": sum(after), "n": len(after), "denominator": len(rows),
                                    "note": "参考（窓の和には足さない）"},
            "clicks_before_post": {"sum": sum(before), "n": len(before), "denominator": len(rows),
                                   "note": "窓の和には足さない"},
            "window_share": {"value": round(numerator / denominator, 4) if denominator else None,
                             "numerator": numerator, "denominator": denominator,
                             "per_post_median": _median(values),
                             "per_post_min": min(values) if values else None,
                             "n": len(values),
                             "reason": None if denominator else "no_countable_post"}}


def answer(account_name, *, since=None, window_days=DEFAULT_WINDOW_DAYS, now=None) -> dict:
    """投稿ごとのクリックの payload（CLI の Markdown と JSON が同じものから作る）。"""
    from . import after_cli, analytics_comparison as comparison, click_attribution, measured
    if not isinstance(account_name, str) or not account_name.strip():
        raise after_cli.AfterError("account は空でない文字列です")
    if type(window_days) is not int or window_days < 1:
        raise after_cli.AfterError("window_days は 1 以上の整数です")
    now = jst.to_jst(now if now is not None else jst.now_jst()).replace(microsecond=0)
    try:
        floor = read_window.cutoff(since, now=now)
    except ValueError as exc:
        raise after_cli.AfterError(str(exc)) from None
    if floor is None:
        floor = now - datetime.timedelta(days=window_days)
    cfg = accounts.load_account(account_name)       # 無い・読めない台帳は AccountError
    loaded = measured.load(account_name, observation_metadata=True)
    items = []
    for post in loaded["posts"]:
        posted = comparison._timestamp(post.get("posted_at"))
        if posted is not None:
            items.append((str(post["post_id"]), posted, post))
    # 共有かどうかは account の投稿の全部と比べる（窓の外の投稿も）。
    index = click_attribution.Index.for_account(
        account_name, cfg, posts=[(pid, posted) for pid, posted, _post in items],
        account_daily=loaded.get("account_daily"), now=now)
    # **goal を問わない**。層に分けない（`--by goal` とは混ぜない）。
    members = [item for item in items if floor <= item[1] <= now]
    rows, reasons = [], {}
    for post_id, posted, post in sorted(members, key=lambda item: item[1]):
        row, reason = _row(index, post_id, posted, post, now)
        if row is None:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            rows.append(row)
    summary = _summary(rows, len(members))
    notes = []
    if summary["clicks_before_post"]["sum"] >= 1:
        notes.append({"code": BEFORE_PRESENT, "message": BEFORE_PRESENT_NOTE})
    return {"schema_version": 1, "report_type": "per_post_clicks", "account": account_name,
            "generated_at": jst.iso(now),
            "period": {"start": jst.iso(floor), "end": jst.iso(now), "basis": "posted_at",
                       "since": since, "window_days": None if since else window_days},
            "population": POPULATION, "population_note": POPULATION_NOTE,
            "basis": click_attribution.BASIS, "window": click_attribution.WINDOW,
            "window_note": click_attribution.WINDOW_NOTE,
            "before_basis": click_attribution.BEFORE_BASIS,
            "after_basis": click_attribution.AFTER_BASIS,
            "posts": rows, "not_attributable": dict(sorted(reasons.items())),
            "summary": summary, "notes": notes,
            "limitations": ["日次の粒度なので 72 時間は投稿日を含む 3 暦日の和",
                            "共有のリンク先・リンク無し・プロフィールのリンク・記録に無いリンク先は"
                            "並べない（not_attributable に理由ごとの本数）",
                            "窓の前と後は参考。窓の和には足さない",
                            "本数は実測の台帳にある投稿（根と返信を含む）。SNS 上の全投稿ではない"]}


def _fmt(value):
    return "null" if value is None else value


def render_markdown(payload) -> str:
    from .analytics_report import _markdown_text
    s = payload["summary"]
    period = payload["period"]
    lines = [f"# 投稿ごとのクリック {_markdown_text(payload['account'])}", "",
             f"対象: {period['start']} ～ {period['end']}（投稿日時基準）。{payload['population_note']}。",
             f"{payload['basis']}（{payload['window_note']}）。", "",
             f"並べた投稿 {s['n_attributable']}/{s['n_posts']} 本。"
             f"72h のクリック 合計 {s['clicks_72h']['sum']}・中央値 {_fmt(s['clicks_72h']['median'])}"
             f"（値あり n={s['clicks_72h']['n']}/{s['clicks_72h']['denominator']}）・"
             f"クリック率中央値 {_fmt(s['click_rate']['median'])}（n={s['click_rate']['n']}）。",
             f"窓の中の割合 {_fmt(s['window_share']['value'])}"
             f"（{s['window_share']['numerator']}/{s['window_share']['denominator']}・"
             f"投稿ごとの中央値 {_fmt(s['window_share']['per_post_median'])}・"
             f"最小 {_fmt(s['window_share']['per_post_min'])}）。"
             f"窓の後 {s['clicks_after_window']['sum']}（参考）・"
             f"窓の前 {s['clicks_before_post']['sum']}（窓には足さない）。", ""]
    lines.append("| 投稿 | 投稿日時 | 72h クリック | クリック率 | 窓の後（参考） | 窓の前 |")
    lines.append("|---|---|---|---|---|---|")
    for row in payload["posts"]:
        clicks = row["clicks_72h"] if row["clicks_72h"] is not None else f"null（{row['clicks_missing']}）"
        rate = row["click_rate"] if row["click_rate"] is not None else f"null（{row['rate_missing'] or row['clicks_missing']}）"
        after = _fmt(row["clicks_after_window"])
        before = _fmt(row["clicks_before_post"])
        lines.append(f"| {_markdown_text(row['post_id'])} | {row['posted_at']} | {clicks} | {rate}"
                     f" | {after} | {before} |")
    if payload["not_attributable"]:
        lines += ["", "並べない投稿: " + "・".join(f"{k} {v}" for k, v in payload["not_attributable"].items())]
    for note in payload["notes"]:
        lines += ["", f"{note['message']}（{note['code']}）"]
    lines += ["", "## 制約", ""] + ["- " + item for item in payload["limitations"]]
    return "\n".join(lines) + "\n"


def cmd(args) -> int:
    from . import after_cli
    if args.compare_previous or args.by or args.project or getattr(args, "weekly_goals", False):
        print("--per-post-clicks は --compare-previous・--by・--project・--weekly-goals と一緒には"
              "使えません（1 account の投稿ごとの表です）", file=sys.stderr)
        return 2
    if not args.account:
        print("--per-post-clicks には account が要ります", file=sys.stderr)
        return 2
    try:
        payload = answer(args.account, since=getattr(args, "since", None),
                         window_days=args.window_days)
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (after_cli.AfterError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        sys.stdout.write(render_markdown(payload))
    return 0
