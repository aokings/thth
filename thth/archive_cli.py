"""`thth archive <account> …`——X の他人の公開投稿を全期間（2006 年から）から読む（4.5.0）。

設計: `docs/設計_4.5.0_Xの全期間の検索_2026-10-21.md`。**`build_parser()` には 1 行しか足さない**
（`archive_cli.register(sub)`）。MCP には出さない（設計 §7）。

形:
  thth archive <account> --from <handle> --title <曲名> [--max-total 20] [--per-call 10] [--json]
  thth archive <account> --query '<語>' [--since YYYY-MM-DD] [--until YYYY-MM-DD] …
  thth archive <account> --from <handle> --title <曲名> --count-only [--max-pages 1] [--granularity day]

一次資料（**L1** docs.x.com）:
  - `GET /2/tweets/search/all`（従量の契約・2006-03 から・`max_results` 10〜500・語は 1,024 字・
    `start_time`／`end_time`・`next_token`）。料金表 Posts: Read 0.005 USD／返った 1 本。
  - `GET /2/tweets/counts/all`（31 日ごとに 1 ページ・`granularity` minute／hour／day）。
    料金表 Counts: All 0.010 USD／request。

決まり:
  - X 以外の account は rc=2 `unsupported_on_platform`。範囲外の指定は**要求も予約もせずに** rc=2。
  - 予約は要求の前・確定は応答の後・課金は返った本数。結果不明は投げ直さない。
  - 1 日の本数は `x_archive_daily_reads`（既定 0＝読まない）。今の `x_daily_reads` には数えない。
  - **返すだけで保存しない**。runs には件数だけ（語・本文は書かない）。
"""
from __future__ import annotations

import json
import sys

from . import jst
from . import x_archive
from .adapters import base as adapter_base
from .trends_cli import _adapter, _fail, _record

NOTE = "表示だけで、道具は保存しません。費用は推定であって請求額ではありません"


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    a = sub.add_parser("archive", help="X の全期間の検索（2006 年から・有償の読み取り・材料集め・保存しない）")
    a.add_argument("account")
    a.add_argument("--from", dest="handle", default=None, help="投稿者の handle（@ は有っても無くても）")
    a.add_argument("--title", default=None, help="曲名（引用符で囲んだ句にする・引用符は空白に）")
    a.add_argument("--include-retweets", action="store_true", help="再投稿も含める（既定は -is:retweet）")
    a.add_argument("--query", default=None, help="語をそのまま（--from／--title と同時には使わない）")
    a.add_argument("--since", default=None, help="この時刻から（YYYY-MM-DD は UTC の 0 時・既定は 2006-03-21）")
    a.add_argument("--until", default=None, help="この時刻まで（既定は今）")
    a.add_argument("--per-call", type=int, default=None, help="1 回の本数（10〜100・既定 10）")
    a.add_argument("--max-total", type=int, default=None, help="総本数の上限（10〜500・既定 20）")
    a.add_argument("--max-pages", type=int, default=None,
                   help="ページの上限（検索は 1〜50・既定 5／--count-only は 1〜300・既定 1）")
    a.add_argument("--sort", choices=("recency", "relevancy"), default="recency")
    a.add_argument("--count-only", action="store_true", help="件数だけ（Counts: All・1 回 0.010 USD・31 日ごと）")
    a.add_argument("--granularity", choices=("day", "hour"), default="day", help="--count-only の区間（既定 day）")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_archive)


def _query(args) -> str:
    if args.query is not None:
        if args.handle is not None or args.title is not None:
            raise x_archive.QueryError("--query と --from／--title は同時に使えません")
        return x_archive.check_query(args.query)
    if args.handle is None or args.title is None:
        raise x_archive.QueryError("--from と --title の両方か、--query を渡してください")
    return x_archive.build_query(args.handle, args.title, retweets=args.include_retweets)


def _upstream(args, action, e, what) -> int:
    from . import accounts as accounts_mod, redact as redact_mod, runs as runs_mod
    if isinstance(e, accounts_mod.AccountStopped):
        _record(args.account, action, n=None, status="error", error=action + "_failed")
        return _fail(args, 1, "account_stopped", str(e))
    text = redact_mod.redact(str(e))
    head = text.split(":", 1)[0].strip()
    _record(args.account, action, n=None, status="error", error=runs_mod.failure_error(action + "_failed", text))
    known = ("budget_exhausted", "provider_rate_limited", "x_archive_daily_read_cap", "x_archive_unattended",
             "x_archive_limit")
    code = head if head in known else "upstream_refused"
    rc = 2 if code in ("x_archive_limit", "x_archive_unattended") else 1
    return _fail(args, rc, code, f"{what}を読めませんでした: {text}")


def cost_line(cost: dict) -> str:
    def dash(value):
        return "—" if value is None else value
    line = (f"X の全期間の読み取り: 今回 {cost['posts']} 本・約 ${cost['usd_estimate']}"
            f"（{cost['requests']} 回）")
    if cost.get("counts_requests"):
        line += f"・件数 {cost['counts_requests']} 回・約 ${cost['counts_usd_estimate']}"
    line += (f"・今日 {dash(cost.get('today'))}/{dash(cost.get('daily_cap'))} 本（x_archive_daily_reads）"
             f"・今月 ${dash(cost.get('month_used_usd'))}/${dash(cost.get('cap_usd'))}")
    return line


def cmd_archive(args) -> int:
    from .adapters.x import XAdapter
    # ----- 要求の前の検査（範囲外は叩かない・予約もしない） -----------------------
    try:
        query = _query(args)
        start, end = x_archive.window(args.since, args.until)
    except x_archive.QueryError as e:
        return _fail(args, 2, "invalid_request", str(e))
    if args.count_only:
        pages = XAdapter.COUNTS_ALL_PAGES_DEFAULT if args.max_pages is None else args.max_pages
        if type(pages) is not int or not 1 <= pages <= XAdapter.COUNTS_ALL_PAGES_MAX:
            return _fail(args, 2, "x_archive_limit", f"--max-pages は 1〜{XAdapter.COUNTS_ALL_PAGES_MAX} です")
        needed = x_archive.counts_pages_needed(start, end)
        if needed > pages:
            from . import budget_x
            usd = budget_x.decimal_text(budget_x.COUNTS_ALL_PRICE * needed)
            return _fail(args, 2, "x_archive_limit",
                         f"この窓を数えきるには {needed} ページ（31 日ごと・約 ${usd}）要ります。"
                         f"--max-pages {needed} にするか、--since／--until で窓を狭めてください",
                         pages_needed=needed, usd_estimate_if_run=usd)
    else:
        per_call = XAdapter.ARCHIVE_PER_CALL_DEFAULT if args.per_call is None else args.per_call
        total = XAdapter.ARCHIVE_TOTAL_DEFAULT if args.max_total is None else args.max_total
        pages = XAdapter.ARCHIVE_PAGES_DEFAULT if args.max_pages is None else args.max_pages
        try:
            XAdapter.check_archive_limits(per_call, total)
        except adapter_base.AdapterError as e:
            return _fail(args, 2, "x_archive_limit", str(e))
        if type(pages) is not int or not 1 <= pages <= XAdapter.ARCHIVE_PAGES_MAX:
            return _fail(args, 2, "x_archive_limit", f"--max-pages は 1〜{XAdapter.ARCHIVE_PAGES_MAX} です")
    if x_archive.is_unattended():
        return _fail(args, 2, "x_archive_unattended", "全期間の読み取りは人が打った命令でだけ動きます")
    adapter, rc = _adapter(args, "全期間の検索")
    if adapter is None:
        return rc
    from . import accounts as accounts_mod
    now = jst.now_jst()
    window = {"start_time": x_archive.stamp(start), "end_time": x_archive.stamp(end) if end else None}
    if args.count_only:
        try:
            counted = adapter.archive_counts(query, start_time=window["start_time"], end_time=window["end_time"],
                                             granularity=args.granularity, max_pages=pages)
        except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
            return _upstream(args, "archive_counts", e, "全期間の投稿数")
        _record(args.account, "archive_counts", n=counted["pages"], status="ok", error=None)
        cannot_say = []
        if counted["more"]:
            cannot_say.append(f"続きがあります（{counted['pages']} ページで止めました）。合計は窓の一部です")
        if counted["stopped"]:
            cannot_say.append(f"途中で止まりました（{counted['stopped']}）。投げ直していません。合計は窓の一部です")
        result = {"account": args.account, "medium": "x", "mode": "count_only", "query": query, "window": window,
                  "granularity": args.granularity, "pages": counted["pages"], "buckets": counted["buckets"],
                  "total_post_count": counted["total_post_count"],
                  "complete": not counted["more"] and not counted["stopped"],
                  "x_read_cost": adapter.archive_read_cost(), "cannot_say": cannot_say,
                  "storage": "display_only", "note": NOTE, "generated_at": jst.iso(now),
                  "provenance": {"fetched_at": jst.iso(now), "endpoint": "GET /2/tweets/counts/all"}}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        total = result["total_post_count"] if result["total_post_count"] is not None else "—"
        print(f"archive --count-only  {args.account}  {query}  合計={total}  ページ={counted['pages']}")
        for line in cannot_say:
            print(f"  言えない: {line}")
        print("  " + cost_line(result["x_read_cost"]))
        print(f"注記: {NOTE}")
        return 0
    try:
        found = adapter.archive_search(query, per_call=per_call, max_total=total,
                                       start_time=window["start_time"], end_time=window["end_time"],
                                       sort_order=args.sort, max_pages=pages)
    except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
        return _upstream(args, "archive", e, "全期間の検索")
    rows = found["posts"]
    _record(args.account, "archive", n=len(rows), status="ok", error=None)
    cannot_say = []
    if not rows:
        cannot_say.append("0 本でした。本当に無いか、X が返さなかったかは区別できません")
    if found["more"]:
        cannot_say.append(f"続きがあります（総本数 {total}・ページ {pages} の上限で止めました。続きは読んでいません）")
    if found["stopped"]:
        cannot_say.append(f"途中で止まりました（{found['stopped']}）。投げ直していません")
    result = {"account": args.account, "medium": "x", "mode": "search", "query": query, "window": window,
              "sort": args.sort, "per_call": per_call, "max_total": total, "max_pages": pages,
              "n": len(rows), "pages": found["pages"], "more": found["more"], "stopped": found["stopped"],
              "posts": rows, "x_read_cost": adapter.archive_read_cost(), "cannot_say": cannot_say,
              "storage": "display_only", "note": NOTE, "generated_at": jst.iso(now),
              "provenance": {"fetched_at": jst.iso(now), "endpoint": "GET /2/tweets/search/all"}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"archive  {args.account}  {query}  {len(rows)} 本  ページ={found['pages']}")
    for row in rows:
        text = " ".join((row["text"] or "").split())
        print(f"  {row['created_at'] or '—'}  @{row['author_username'] or '—'}  {row['url'] or '—'}")
        print(f"    {text[:200]}")
    for line in cannot_say:
        print(f"  言えない: {line}")
    print("  " + cost_line(result["x_read_cost"]))
    print(f"注記: {NOTE}")
    return 0
