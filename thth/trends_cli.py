"""`thth trends <account>`・`thth counts <account> <語>`——X のトレンドと語ごとの投稿数の推移
（3.19.0・関東レーン・masaru 10/13「両方やって」）。

**`build_parser()` には 1 行しか足さない**（`trends_cli.register(sub)`）。MCP には今回は足さない。

一次資料（**L1** docs.x.com）:
  - `GET /2/trends/by/woeid/:woeid`（`max_trends` 1〜50・既定 20・`trend.fields` trend_name・tweet_count）。
    料金表 Trends 0.010 USD／request。rate limit の表: app ごと 75／15 分・利用者ごとは「—」。
  - `GET /2/tweets/counts/recent`（直近 7 日・`granularity` minute／hour／day・`query` は rate limit の
    表で 512 字）。料金表 Counts: Recent 0.005 USD／request。rate limit の表: app ごと 300／15 分・
    利用者ごとは「—」。
  - どちらも security は OAuth2UserToken か BearerToken。**利用者のトークンで通るかは実機で未確認**
    （rate limit の表に利用者ごとの欄が無い）。tier の条件は資料に書かれていない。

決まり:
  - X 以外の account は rc=2 `unsupported_on_platform`（`cannot_say` に理由）。
  - 1 回の命令で要求は 1 回（続きは読まない）。費用は月の X 予算の台帳（`budget_x.request_read`・
    種類ごとに別のファイル）に予約→確定。**1 日の本数（`x_daily_reads`）には数えない**。
  - **返すだけで保存しない**。runs には件数だけ（語・トレンドの名前は書かない）。
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import jst
from . import redact as redact_mod
from . import runs as runs_mod
from .adapters import base as adapter_base

JAPAN_WOEID = 23424856
NOTE = "表示だけで、道具は保存しません。推定であって請求額ではありません"


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    t = sub.add_parser("trends", help="X のトレンド（地域ごと・有償の読み取り 1 回・保存しない）")
    t.add_argument("account")
    t.add_argument("--woeid", type=int, default=JAPAN_WOEID,
                   help=f"地域の WOEID（既定 {JAPAN_WOEID}＝日本）")
    t.add_argument("--max", dest="max_trends", type=int, default=None, help="件数（1〜50・既定 20）")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_trends)

    c = sub.add_parser("counts", help="X で語を含む投稿の数の推移（直近 7 日・有償の読み取り 1 回・保存しない）")
    c.add_argument("account")
    c.add_argument("word", help="語（X の検索の演算子も書ける・512 字まで）")
    c.add_argument("--granularity", choices=("hour", "day"), default="day", help="区間（既定 day）")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_counts)


def _fail(args, rc: int, code: str, message: str, **extra) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "error": code, "message": message, **extra},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(message if message.startswith(code) else f"{code}: {message}", file=sys.stderr)
    return rc


def _record(account, action, *, n, status, error):
    try:
        runs_mod.record_minimal(account, {"action": action, "account": account, "n": n,
                                          "status": status, "error": error})
    except (OSError, ValueError, RuntimeError):
        pass


def _adapter(args, what):
    """`(adapter, None)` か `(None, rc)`。X 以外・token 無しは要求の前に断る。"""
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return None, _fail(args, 2, "account_unavailable", str(e))
    media = account_cfg.get("media")
    if media != "x":
        message = f"{args.account}: {what}は X だけです（この account は {media}）"
        return None, _fail(args, 2, "unsupported_on_platform", message, cannot_say=[message])
    adapter_cls = adapters_mod.adapter_class(media)
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        return None, _fail(args, 2, "no_token", f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    try:
        return adapters_mod.make_adapter(account_cfg, token), None
    except accounts_mod.AccountStopped as e:
        return None, _fail(args, 1, "account_stopped", str(e))


def _upstream(args, action, e, what) -> int:
    _record(args.account, action, n=None, status="error", error=action + "_failed")
    if isinstance(e, accounts_mod.AccountStopped):
        return _fail(args, 1, "account_stopped", str(e))
    text = redact_mod.redact(str(e))
    head = text.split(":", 1)[0].strip()
    code = head if head in ("budget_exhausted", "provider_rate_limited") else "upstream_refused"
    return _fail(args, 1, code, f"{what}を読めませんでした: {text}")


def _cost_line(cost, api) -> str:
    return (f"X の料金の目安: {cost['requests']} 回・{cost['usd_estimate']} USD（{api}・推定・"
            "1 日の本数には数えない）")


def cmd_trends(args) -> int:
    from . import budget_x
    woeid, cap = args.woeid, args.max_trends
    if type(woeid) is not int or not 1 <= woeid <= 2 ** 31 - 1:
        return _fail(args, 2, "invalid_request", "--woeid は正の整数です")
    if cap is not None and (type(cap) is not int or not 1 <= cap <= 50):
        return _fail(args, 2, "invalid_request", "--max は 1〜50 です")
    adapter, rc = _adapter(args, "トレンド")
    if adapter is None:
        return rc
    now = jst.now_jst()
    try:
        rows = adapter.trends(woeid, max_trends=cap)          # ← X への要求はここ 1 回
    except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
        return _upstream(args, "trends", e, "トレンド")
    _record(args.account, "trends", n=len(rows), status="ok", error=None)
    cannot_say = [] if rows else ["0 件でした。本当に無いか、X がこの地域に返さなかったかは区別できません"]
    if any(row["tweet_count"] is None for row in rows):
        cannot_say.append("投稿数が無いトレンドがあります（X が数を返さなかったもの・0 ではありません）")
    result = {"account": args.account, "medium": "x", "woeid": woeid, "n": len(rows), "trends": rows,
              "x_read_cost": adapter.request_read_cost(budget_x.TRENDS), "cannot_say": cannot_say,
              "storage": "display_only", "note": NOTE,
              "provenance": {"fetched_at": jst.iso(now), "endpoint": "GET /2/trends/by/woeid/:woeid"}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"trends  {args.account}  woeid={woeid}  件数={len(rows)}")
    for index, row in enumerate(rows, start=1):
        count = row["tweet_count"] if row["tweet_count"] is not None else "—"
        print(f"  {index:>2}. {row['trend_name']}  投稿数={count}")
    for line in cannot_say:
        print(f"  言えない: {line}")
    print("  " + _cost_line(result["x_read_cost"], "Trends"))
    print(f"注記: {NOTE}")
    return 0


def cmd_counts(args) -> int:
    from . import budget_x
    word = args.word.strip() if isinstance(args.word, str) else ""
    if not word:
        return _fail(args, 2, "invalid_request", "語は空でない文字列です")
    if any(ord(c) < 32 or ord(c) == 127 for c in word):
        return _fail(args, 2, "invalid_request", "語に制御文字は使えません")
    adapter, rc = _adapter(args, "投稿数の推移")
    if adapter is None:
        return rc
    maximum = adapter.COUNTS_QUERY_MAX
    if len(word) > maximum:
        return _fail(args, 2, "invalid_request", f"語は {maximum} 字までです（{len(word)} 字）")
    now = jst.now_jst()
    try:
        counted = adapter.post_counts(word, granularity=args.granularity)   # ← X への要求はここ 1 回
    except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
        return _upstream(args, "counts", e, "投稿数")
    buckets = counted["buckets"]
    _record(args.account, "counts", n=len(buckets), status="ok", error=None)
    cannot_say = []
    if not buckets:
        cannot_say.append("区間が返りませんでした。本当に無いか、X が語を受け付けなかったかは区別できません")
    if counted["more"]:
        cannot_say.append(f"続きがあります（{len(buckets)} 区間で止めました。続きは読んでいません）")
    result = {"account": args.account, "medium": "x", "query": word, "granularity": args.granularity,
              "window_days": 7, "n": len(buckets), "buckets": buckets,
              "total_post_count": counted["total_post_count"],
              "x_read_cost": adapter.request_read_cost(budget_x.COUNTS), "cannot_say": cannot_say,
              "storage": "display_only", "note": NOTE,
              "provenance": {"fetched_at": jst.iso(now), "endpoint": "GET /2/tweets/counts/recent"}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    total = result["total_post_count"] if result["total_post_count"] is not None else "—"
    print(f"counts  {args.account}  「{word}」  区間={args.granularity}  直近 7 日  合計={total}")
    for row in buckets:
        print(f"  {row['start']}  {row['post_count']}")
    for line in cannot_say:
        print(f"  言えない: {line}")
    print("  " + _cost_line(result["x_read_cost"], "Counts: Recent"))
    print(f"注記: {NOTE}")
    return 0
