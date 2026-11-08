"""`thth gsc …`（Google Search Console・読むだけ）。

    thth gsc auth [--no-browser] [--timeout 秒]     利用者の Google アカウントで 1 度だけ認可する（端末から）
    thth gsc sites [--json]                         その鍵で見えるプロパティと権限
    thth gsc query --site <プロパティ> [--days N | --since YYYY-MM-DD [--until YYYY-MM-DD]]
                   [--dim query|page|country|device|date ...] [--page-prefix <URL>] [--query-contains <語>]
                   [--limit N] [--json]             クリック・表示回数・CTR・平均掲載順位
    thth gsc compare --site <プロパティ> [--days N] [--dim query|page] [--limit N] [--json]
                                                    直近 N 日とその前の N 日の差（伸びた・落ちた語か頁を上から）
    thth gsc inspect --site <プロパティ> --url <頁> [--json]
                                                    URL の検査（Google の索引にある版の様子・読むだけ）

<プロパティ> は `sc-domain:example.com`（ドメイン）か `https://example.com/`（URL の頭・終わりは /）。
日付は Search Console と同じ太平洋時間。既定は直近 28 日で、終わりの日は 3 日前（数は 2〜3 日遅れて入る・
確定した数だけを読む）。client の JSON は `thth gads` と同じ（`$THTH_GOOGLE_ADS_CLIENT`、無ければ
`~/.config/thth/google-ads-client.json`）、認可は `$THTH_GOOGLE_GSC_TOKEN`（無ければ
`~/.config/thth/google-gsc-token.json`・600）に置く。**書く命令は無い**。MCP と遠くの道にはまだ出さない。
"""
from __future__ import annotations

import argparse
import datetime
import json

from .. import redact, refusal
from . import api, auth
from .wire import GscRefused

VERBS = ("auth", "sites", "query", "compare", "inspect")
DEFAULT_DAYS = 28
# 数が入るまでの遅れ（公式: 「Data is typically available after 2-3 days」・how-tos/all-your-data）。
DATA_LAG_DAYS = 3
# Search Console が持つのは約 16 か月。期間の長さはそれに収まる 480 日まで。
MAX_DAYS = 480
COMPARE_MAX_DAYS = MAX_DAYS // 2
COMPARE_FETCH = 5000
# 太平洋時間（夏時間の 1 時間の差は、3 日の遅れの余白に収まるので標準時で数える）。
PACIFIC = datetime.timezone(datetime.timedelta(hours=-8))


def _refuse(args, exc: GscRefused) -> int:
    found = refusal.parts(exc.code) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（Google: {exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail}
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload)


def _emit(value, args, render) -> int:
    if getattr(args, "json", False):
        print(json.dumps(value, ensure_ascii=False))
    else:
        render(value)
    return 0


def _client(args):
    return api.Client(transport=getattr(args, "transport", None))


def _num(value) -> str:
    return "—" if value is None else f"{value:,}"


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _pos(value) -> str:
    return "—" if value is None else f"{value:.1f}"


def today_pacific() -> datetime.date:
    return datetime.datetime.now(tz=PACIFIC).date()


def latest_day(today=None) -> datetime.date:
    return (today or today_pacific()) - datetime.timedelta(days=DATA_LAG_DAYS)


# ------------------------------------------------------------------ auth

def cmd_auth(args) -> int:
    try:
        value = auth.run(open_browser=not args.no_browser, timeout=args.timeout,
                         transport=getattr(args, "transport", None))
    except GscRefused as exc:
        return _refuse(args, exc)
    print(f"Search Console の認可を残しました: {value['token_path']}（{value['mode']}・中身は表示しません）")
    print("次の一手: thth gsc sites で見えるプロパティを確かめてください")
    return 0


# ------------------------------------------------------------------ sites

PERMISSION_JA = {"siteOwner": "所有者", "siteFullUser": "フル", "siteRestrictedUser": "制限付き",
                 "siteUnverifiedUser": "未確認"}


def cmd_sites(args) -> int:
    try:
        sites = _client(args).sites()
    except GscRefused as exc:
        return _refuse(args, exc)

    def render(v):
        if not v["sites"]:
            print("この Google アカウントから見える Search Console のプロパティは 0 件でした（Search Console の画面で"
                  "サイトを確認するか、持ち主にユーザーとして足してもらってから打ち直してください）")
            return
        print(f"この Google アカウントから見えるプロパティ（{len(v['sites'])} 件）:")
        for entry in v["sites"]:
            print(f"  {entry['site']}  権限 {PERMISSION_JA.get(entry['permission'], '—')}")
        print("次の一手: thth gsc query --site <プロパティ> で直近 28 日の検索の成績を読めます")
    return _emit({"sites": sites}, args, render)


# ------------------------------------------------------------------ query

def date_range(args, *, today=None) -> tuple[str, str]:
    """`--days N`（終わりは 3 日前）か `--since`〜`--until`（既定は 3 日前）。どちらも無ければ直近 28 日。"""
    last = latest_day(today)
    if args.days is not None and (args.since or args.until):
        raise GscRefused("gsc_date_invalid")
    if args.since or args.until:
        if not args.since:
            raise GscRefused("gsc_date_invalid")
        try:
            since = datetime.date.fromisoformat(args.since)
            until = datetime.date.fromisoformat(args.until) if args.until else last
        except ValueError:
            raise GscRefused("gsc_date_invalid") from None
        if until < since or (until - since).days >= MAX_DAYS:
            raise GscRefused("gsc_date_invalid")
        return since.isoformat(), until.isoformat()
    days = DEFAULT_DAYS if args.days is None else args.days
    if not 1 <= days <= MAX_DAYS:
        raise GscRefused("gsc_date_invalid")
    return (last - datetime.timedelta(days=days - 1)).isoformat(), last.isoformat()


def _key(row, dims) -> str:
    return " / ".join(row.get(d) or "—" for d in dims) if dims else "（全体）"


def cmd_query(args) -> int:
    dims = args.dim or ["query"]
    try:
        api.site_url(args.site)
        since, until = date_range(args)
        api.query_body(since, until, dims, page_prefix=args.page_prefix, query_contains=args.query_contains,
                       limit=args.limit)
        value = _client(args).query(args.site, since, until, dims, page_prefix=args.page_prefix,
                                    query_contains=args.query_contains, limit=args.limit)
    except GscRefused as exc:
        return _refuse(args, exc)

    def render(v):
        cond = "".join((f"・頁 {v['page_prefix']} から" if v["page_prefix"] else "",
                        f"・語に「{v['query_contains']}」" if v["query_contains"] else ""))
        print(f"Search Console の検索の成績（{v['site']}・{v['since']}〜{v['until']}・太平洋時間・"
              f"{' × '.join(v['dimensions']) or '全体'}{cond}）")
        if not v["rows"]:
            print("  この条件の行は 0 件でした（期間を広げるか、--page-prefix・--query-contains を外すと行が出ます）")
            return
        for row in v["rows"]:
            print(f"  {_key(row, v['dimensions'])}  クリック {_num(row['clicks'])}  表示 {_num(row['impressions'])}"
                  f"  CTR {_pct(row['ctr'])}  平均掲載順位 {_pos(row['position'])}")
        print(f"  （{len(v['rows'])} 行・クリックの多い順・Google が返す上の行だけ）")
    return _emit(value, args, render)


# ------------------------------------------------------------------ compare

def compare_ranges(days: int, *, today=None) -> tuple[tuple[str, str], tuple[str, str]]:
    if not 1 <= days <= COMPARE_MAX_DAYS:
        raise GscRefused("gsc_date_invalid")
    last = latest_day(today)
    start = last - datetime.timedelta(days=days - 1)
    prev_last = start - datetime.timedelta(days=1)
    prev_start = prev_last - datetime.timedelta(days=days - 1)
    return (start.isoformat(), last.isoformat()), (prev_start.isoformat(), prev_last.isoformat())


def cmd_compare(args) -> int:
    dim = args.dim
    try:
        api.site_url(args.site)
        if dim not in ("query", "page"):
            raise GscRefused("gsc_dim_invalid")
        now, before = compare_ranges(DEFAULT_DAYS if args.days is None else args.days)
        client = _client(args)
        a = client.query(args.site, *now, [dim], limit=COMPARE_FETCH)
        b = client.query(args.site, *before, [dim], limit=COMPARE_FETCH)
    except GscRefused as exc:
        return _refuse(args, exc)
    rows = api.compare_rows(a["rows"], b["rows"], dim)
    rising, falling = api.split_moves(rows, dim, args.limit)
    value = {"site": a["site"], "dimension": dim, "current": {"since": now[0], "until": now[1]},
             "previous": {"since": before[0], "until": before[1]},
             "totals": {"clicks": sum(r["clicks"] for r in a["rows"]),
                        "clicks_before": sum(r["clicks"] for r in b["rows"]),
                        "impressions": sum(r["impressions"] for r in a["rows"]),
                        "impressions_before": sum(r["impressions"] for r in b["rows"])},
             "rising": rising, "falling": falling}

    def render(v):
        word = "語" if dim == "query" else "頁"
        t = v["totals"]
        print(f"Search Console の前の期間との差（{v['site']}・{word}ごと・{v['current']['since']}〜{v['current']['until']}"
              f" と {v['previous']['since']}〜{v['previous']['until']}・太平洋時間）")
        print(f"  合計  クリック {_num(t['clicks_before'])} → {_num(t['clicks'])}  "
              f"表示 {_num(t['impressions_before'])} → {_num(t['impressions'])}（各期間の上の {COMPARE_FETCH:,} 行まで）")
        for title, items in (("伸びた", v["rising"]), ("落ちた", v["falling"])):
            print(f"  {title}{word}（{len(items)} 件）:")
            if not items:
                print("    なし")
            for r in items:
                print(f"    {r[dim]}  クリック {_num(r['clicks_before'])} → {_num(r['clicks'])}（{r['clicks_delta']:+,}）"
                      f"  表示 {_num(r['impressions_before'])} → {_num(r['impressions'])}（{r['impressions_delta']:+,}）"
                      f"  順位 {_pos(r['position_before'])} → {_pos(r['position'])}")
    return _emit(value, args, render)


# ------------------------------------------------------------------ inspect

def cmd_inspect(args) -> int:
    try:
        api.site_url(args.site)
        if api._http_url(args.url) is None:
            raise GscRefused("gsc_url_invalid")
        if not api.url_in_site(args.site, args.url):
            raise GscRefused("gsc_inspect_url_outside")
        value = _client(args).inspect(args.site, args.url)
    except GscRefused as exc:
        return _refuse(args, exc)

    def render(v):
        print(f"URL の検査（{v['url']}・Google の索引にある版）")
        print(f"  判定 {v['verdict'] or '—'}  {v['coverage_state'] or ''}")
        print(f"  取得 {v['page_fetch_state'] or '—'}  robots.txt {v['robots_txt_state'] or '—'}  "
              f"索引 {v['indexing_state'] or '—'}  巡回 {v['crawled_as'] or '—'}  最後の巡回 {v['last_crawl_time'] or '—'}")
        print(f"  Google の正規 {v['google_canonical'] or '—'}  頁の指定 {v['user_canonical'] or '—'}")
        if v["link"]:
            print(f"  Search Console で見る: {v['link']}")
    return _emit(value, args, render)


# ------------------------------------------------------------------ 登録

def _bounded(low, high):
    def parse(text):
        try:
            value = int(text)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(f"{low}〜{high} の整数です") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{low}〜{high} の整数です")
        return value
    return parse


_SITE_HELP = "プロパティ（sc-domain:example.com か https://example.com/）"


def register(sub) -> None:
    """`build_parser()` に `gsc` を足す（cli.py に足すのはこの呼び出しだけ）。"""
    p = sub.add_parser("gsc", help="Google Search Console（読むだけ: 認可・プロパティ・検索の成績・前の期間との差・URL の検査）",
                       formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    verbs = p.add_subparsers(dest="gsc_verb", required=True)
    p_auth = verbs.add_parser("auth", help="利用者の Google アカウントで 1 度だけ認可する（端末から・ブラウザで許可）")
    p_auth.add_argument("--no-browser", dest="no_browser", action="store_true",
                        help="ブラウザを開かない（出た URL を手で貼る）")
    p_auth.add_argument("--timeout", type=int, default=auth.AUTH_TIMEOUT, help="戻りを待つ秒（既定 300）")
    p_auth.set_defaults(func=cmd_auth)
    p_sites = verbs.add_parser("sites", help="その認可で見えるプロパティと権限（読むだけ）")
    p_sites.add_argument("--json", action="store_true")
    p_sites.set_defaults(func=cmd_sites)
    p_q = verbs.add_parser("query", help="クリック・表示回数・CTR・平均掲載順位（searchAnalytics.query・読むだけ）")
    p_q.add_argument("--site", required=True, metavar="プロパティ", help=_SITE_HELP)
    p_q.add_argument("--days", type=int, default=None, metavar="N", help="3 日前までの N 日（既定 28）")
    p_q.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    p_q.add_argument("--until", default=None, metavar="YYYY-MM-DD", help="既定は 3 日前")
    p_q.add_argument("--dim", nargs="+", action="extend", default=None, metavar="|".join(api.DIMENSIONS),
                     help="分ける軸（既定 query・並べても重ねてもよい）")
    p_q.add_argument("--page-prefix", dest="page_prefix", default=None, metavar="URL", help="この URL で始まる頁だけ")
    p_q.add_argument("--query-contains", dest="query_contains", default=None, metavar="語", help="この語を含む検索だけ")
    p_q.add_argument("--limit", type=_bounded(1, api.ROW_LIMIT_MAX), default=100, metavar="1-25000",
                     help="出す行の数（既定 100）")
    p_q.add_argument("--json", action="store_true")
    p_q.set_defaults(func=cmd_query)
    p_c = verbs.add_parser("compare", help="直近 N 日とその前の N 日の差（伸びた・落ちた語か頁・読むだけ）")
    p_c.add_argument("--site", required=True, metavar="プロパティ", help=_SITE_HELP)
    p_c.add_argument("--days", type=int, default=None, metavar="N", help=f"比べる日数（既定 28・1〜{COMPARE_MAX_DAYS}）")
    p_c.add_argument("--dim", default="query", metavar="query|page", help="語ごとか頁ごとか（既定 query）")
    p_c.add_argument("--limit", type=_bounded(1, 1000), default=20, metavar="1-1000", help="伸びた・落ちたの各件数（既定 20）")
    p_c.add_argument("--json", action="store_true")
    p_c.set_defaults(func=cmd_compare)
    p_i = verbs.add_parser("inspect", help="URL の検査（Google の索引にある版の様子・読むだけ・1 プロパティ 1 日 2,000 回まで）")
    p_i.add_argument("--site", required=True, metavar="プロパティ", help=_SITE_HELP)
    p_i.add_argument("--url", required=True, metavar="頁", help="プロパティの中の頁の URL")
    p_i.add_argument("--json", action="store_true")
    p_i.set_defaults(func=cmd_inspect)
