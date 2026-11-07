"""`thth gads …`（Google 広告・読むだけ）。

    thth gads auth [--no-browser] [--timeout 秒]     利用者の Google アカウントで 1 度だけ認可する（端末から）
    thth gads accounts [--json]                     その鍵で見える広告の口座（listAccessibleCustomers）
    thth gads keywords --customer <id> --seed <語>... [--url <頁>] [--lang ja|en] [--geo JP|US] [--limit N] [--json]
                                                    キーワードの案と月の平均の検索数・競合・ページ上部の入札の幅
    thth gads report --customer <id> [--since YYYY-MM-DD [--until YYYY-MM-DD] | --days N] [--json]
                                                    キャンペーンごとの表示・クリック・費用・コンバージョン

<id> は `226-006-0755` でも `2260060755` でもよい。金額は口座の通貨（JPY の口座なら円・micros を 100 万で割る）。
client の JSON は `$THTH_GOOGLE_ADS_CLIENT`（無ければ `~/.config/thth/google-ads-client.json`）、
認可は `$THTH_GOOGLE_ADS_TOKEN`（無ければ `~/.config/thth/google-ads-token.json`・600）に置く。
**書く命令は無い**（作る・止める・予算は次の段）。MCP と遠くの道にはまだ出さない。
"""
from __future__ import annotations

import argparse
import datetime
import json

from .. import jst, redact, refusal
from . import api, auth
from .wire import GadsRefused

VERBS = ("auth", "accounts", "keywords", "report")
DEFAULT_DAYS = 30
MAX_DAYS = 3650
LIMIT_MAX = 1000


def _refuse(args, exc: GadsRefused) -> int:
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


def format_customer(cid: str) -> str:
    return f"{cid[:3]}-{cid[3:6]}-{cid[6:]}" if len(cid) == 10 else cid


def _num(value) -> str:
    return "—" if value is None else f"{value:,}"


# ------------------------------------------------------------------ auth

def cmd_auth(args) -> int:
    try:
        value = auth.run(open_browser=not args.no_browser, timeout=args.timeout,
                         transport=getattr(args, "transport", None))
    except GadsRefused as exc:
        return _refuse(args, exc)
    print(f"Google 広告の認可を残しました: {value['token_path']}（{value['mode']}・中身は表示しません）")
    print("次の一手: thth gads accounts で結べる口座を確かめてください")
    return 0


# ------------------------------------------------------------------ accounts

def cmd_accounts(args) -> int:
    try:
        ids = _client(args).accessible_customers()
    except GadsRefused as exc:
        return _refuse(args, exc)

    def render(v):
        if not v["customers"]:
            print("この Google アカウントから見える広告の口座は 0 件でした（Google 広告の画面で口座を作るか、"
                  "口座に招待してもらってから打ち直してください）")
            return
        print(f"この Google アカウントから見える広告の口座（{len(v['customers'])} 件）:")
        for cid in v["customers"]:
            print(f"  {format_customer(cid)}")
        print("次の一手: thth gads report --customer <id> か thth gads keywords --customer <id> --seed <語>")
    return _emit({"customers": ids}, args, render)


# ------------------------------------------------------------------ keywords

def cmd_keywords(args) -> int:
    try:
        api.customer_id(args.customer)
        api.keyword_ideas_body(args.seed, url=args.url, lang=args.lang, geo=args.geo, limit=args.limit)
        value = _client(args).keyword_ideas(args.customer, args.seed, url=args.url, lang=args.lang,
                                            geo=args.geo, limit=args.limit)
    except GadsRefused as exc:
        return _refuse(args, exc)

    def render(v):
        lang = api.LANGUAGES[v["lang"]][1]
        geo = api.GEO_TARGETS[v["geo"]][1]
        print(f"キーワードの案（口座 {format_customer(v['customer'])}・{lang}・{geo}・Google 検索・"
              f"種 {'、'.join(v['seeds'])}{'・頁 ' + v['url'] if v['url'] else ''}）")
        if not v["ideas"]:
            print("  案は 0 件でした（種の語を広げるか、--url に頁を足すと案が増えます）")
            return
        for row in v["ideas"]:
            low, high = row["low_top_of_page_bid_yen"], row["high_top_of_page_bid_yen"]
            bid = "—" if low is None and high is None else f"{_num(low)}〜{_num(high)} 円"
            print(f"  {row['text']}  月平均 {_num(row['avg_monthly_searches'])}  競合 {row['competition'] or '—'}"
                  f"  ページ上部の入札 {bid}")
        print(f"  （{len(v['ideas'])} 件・{v['currency_note']}）")
    return _emit(value, args, render)


# ------------------------------------------------------------------ report

def date_range(args, *, today=None) -> tuple[str, str]:
    """`--days N`（昨日までの N 日）か `--since`〜`--until`（既定は今日）。どちらも無ければ昨日までの 30 日。"""
    today = today or jst.now_jst().date()
    if args.days is not None and (args.since or args.until):
        raise GadsRefused("gads_date_invalid")
    if args.since or args.until:
        if not args.since:
            raise GadsRefused("gads_date_invalid")
        try:
            since = datetime.date.fromisoformat(args.since)
            until = datetime.date.fromisoformat(args.until) if args.until else today
        except ValueError:
            raise GadsRefused("gads_date_invalid") from None
        if until < since or (until - since).days >= MAX_DAYS:
            raise GadsRefused("gads_date_invalid")
        return since.isoformat(), until.isoformat()
    days = DEFAULT_DAYS if args.days is None else args.days
    if not 1 <= days <= MAX_DAYS:
        raise GadsRefused("gads_date_invalid")
    until = today - datetime.timedelta(days=1)
    return (until - datetime.timedelta(days=days - 1)).isoformat(), until.isoformat()


def cmd_report(args) -> int:
    try:
        api.customer_id(args.customer)
        since, until = date_range(args)
        value = _client(args).campaign_report(args.customer, since, until)
    except GadsRefused as exc:
        return _refuse(args, exc)

    def render(v):
        head = f"Google 広告の成績（口座 {format_customer(v['customer'])}・{v['since']}〜{v['until']}）"
        if not v["campaigns"]:
            print(head)
            print("  この期間に数のあるキャンペーンがありません（0 件）。期間を広げるか、"
                  "Google 広告の画面でキャンペーンの状態を確かめてください")
            return
        print(head)
        for row in v["campaigns"]:
            print(f"  {row['name']}（{row['status'] or '—'}）  表示 {_num(row['impressions'])}  "
                  f"クリック {_num(row['clicks'])}  費用 {_num(row['cost_yen'])} 円  "
                  f"コンバージョン {row['conversions'] if row['conversions'] is not None else '—'}")
        t = v["totals"]
        print(f"  合計  表示 {_num(t['impressions'])}  クリック {_num(t['clicks'])}  費用 {_num(t['cost_yen'])} 円  "
              f"コンバージョン {t['conversions']}（{v['currency_note']}）")
    return _emit(value, args, render)


# ------------------------------------------------------------------ 登録

def _limit(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("1〜1000 の整数です") from None
    if not 1 <= value <= LIMIT_MAX:
        raise argparse.ArgumentTypeError("1〜1000 の整数です")
    return value


def register(sub) -> None:
    """`build_parser()` に `gads` を足す（cli.py に足すのはこの呼び出しだけ）。"""
    p = sub.add_parser("gads", help="Google 広告（読むだけ: 認可・口座・キーワードの案・キャンペーンの成績）",
                       formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    verbs = p.add_subparsers(dest="gads_verb", required=True)
    p_auth = verbs.add_parser("auth", help="利用者の Google アカウントで 1 度だけ認可する（端末から・ブラウザで許可）")
    p_auth.add_argument("--no-browser", dest="no_browser", action="store_true",
                        help="ブラウザを開かない（出た URL を手で貼る）")
    p_auth.add_argument("--timeout", type=int, default=auth.AUTH_TIMEOUT, help="戻りを待つ秒（既定 300）")
    p_auth.set_defaults(func=cmd_auth)
    p_acc = verbs.add_parser("accounts", help="その認可で見える広告の口座（読むだけ）")
    p_acc.add_argument("--json", action="store_true")
    p_acc.set_defaults(func=cmd_accounts)
    p_kw = verbs.add_parser("keywords", help="キーワードの案と検索の数（キーワードプランナーと同じ中身・読むだけ）")
    p_kw.add_argument("--customer", required=True, metavar="ID", help="広告の口座の id（226-006-0755 の形でも可）")
    p_kw.add_argument("--seed", required=True, nargs="+", metavar="語", help="種の語（1〜20 語）")
    p_kw.add_argument("--url", default=None, metavar="頁", help="この頁の中身も種にする")
    p_kw.add_argument("--lang", default="ja", metavar="|".join(api.LANGUAGES), help="言語（既定 ja）")
    p_kw.add_argument("--geo", default="JP", metavar="|".join(api.GEO_TARGETS), help="地域（既定 JP）")
    p_kw.add_argument("--limit", type=_limit, default=50, metavar="1-1000", help="出す案の数（既定 50）")
    p_kw.add_argument("--json", action="store_true")
    p_kw.set_defaults(func=cmd_keywords)
    p_rep = verbs.add_parser("report", help="キャンペーンごとの表示・クリック・費用・コンバージョン（読むだけ）")
    p_rep.add_argument("--customer", required=True, metavar="ID", help="広告の口座の id（226-006-0755 の形でも可）")
    p_rep.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    p_rep.add_argument("--until", default=None, metavar="YYYY-MM-DD", help="既定は今日")
    p_rep.add_argument("--days", type=int, default=None, metavar="N", help="昨日までの N 日（既定 30）")
    p_rep.add_argument("--json", action="store_true")
    p_rep.set_defaults(func=cmd_report)

