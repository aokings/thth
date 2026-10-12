"""`thth community search <account> <語…>`——X の**コミュニティを探す**（3.19.0・関東レーン依頼 #1933・
masaru 10/13「X のコミュニティを試してみて」）。

**`build_parser()` には 1 行しか足さない**（`community_cli.register(sub)`）。MCP には今回は足さない。

一次資料（**L1** docs.x.com search-communities・pricing）: `GET /2/communities/search`（`query`・
`max_results` 10〜100・`community.fields`）・tweet.read＋users.read・Community: Read 0.005 USD／resource。
**参加の API は資料に無い**（create-post の `community_id` と、検索・id 引きの 2 つだけ）。
**「自分が参加しているか」を示す欄も無い**（`community.fields` は access・created_at・description・
id・join_policy・member_count・name）。なので送る前には分からず、参加していなければ X の断りが
`publish_refusal` の流儀で残る（`x_unknown_refusal（…）`）。

決まり:
  - X 以外の account は rc=2 `unsupported_on_platform`（`cannot_say` に理由）。
  - 語は空白でつないで **1 回だけ**検索する（語ごとに叩かない・費用を 1 回分にする）。
  - 既定 10 件・最大 25 件（`--limit`）。X の下限が 10 なので、10 未満でも 10 件分を押さえる。
  - 費用は月の X 予算の台帳（`budget_x.community_read`・別のファイル）に予約→確定。
    **1 日の本数（`x_daily_reads`・他人の投稿の読み取り）には数えない**。
  - **返すだけで保存しない**。runs には件数だけ（語も名前も id も書かない）。
  - 各コミュニティの規則（宣伝の禁止など）は人が読んで守る。X の自動化の決まり（bulk・
    無差別の投稿の禁止）に従い、検索の結果へ機械的に出す経路は作らない。
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

MAX_WORDS = 10
NOTE = ("参加は X の画面で先に（API に参加の口は無い・参加しているかを示す欄も無い）。"
        "各コミュニティの規則（宣伝の禁止など）は人が読んで守る。X の自動化の決まりで"
        "bulk・無差別の投稿は不可")


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser("community", help="X のコミュニティを探す（有償の読み取り・保存しない）")
    ops = p.add_subparsers(dest="community_command", required=True)
    s = ops.add_parser("search", help="語で X のコミュニティを探す（1 回・既定 10 件・最大 25 件）")
    s.add_argument("account")
    s.add_argument("words", nargs="+", help="探す語（空白でつないで 1 回だけ検索します）")
    s.add_argument("--limit", type=int, default=None, help="件数（既定 10・最大 25）")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_community_search)


def _fail(args, rc: int, code: str, message: str, **extra) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "error": code, "message": message, **extra},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(message if message.startswith(code) else f"{code}: {message}", file=sys.stderr)
    return rc


def _record(account, *, n, status, error):
    try:
        runs_mod.record_minimal(account, {"action": "community_search", "account": account, "n": n,
                                          "status": status, "error": error})
    except (OSError, ValueError, RuntimeError):
        pass


def cmd_community_search(args) -> int:
    words = [w.strip() for w in (args.words or []) if isinstance(w, str) and w.strip()]
    if not words:
        return _fail(args, 2, "invalid_request", "探す語を 1 つ以上ください")
    if len(words) > MAX_WORDS:
        return _fail(args, 2, "invalid_request", f"語は {MAX_WORDS} 個までです（{len(words)} 個）")
    if any(ord(c) < 32 or ord(c) == 127 for w in words for c in w):
        return _fail(args, 2, "invalid_request", "語に制御文字は使えません")
    query = " ".join(words)
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return _fail(args, 2, "account_unavailable", str(e))
    media = account_cfg.get("media")
    if media != "x":
        message = f"{args.account}: コミュニティは X だけです（この account は {media}）"
        return _fail(args, 2, "unsupported_on_platform", message, cannot_say=[message])
    try:
        adapter_cls = adapters_mod.adapter_class(media)
    except adapter_base.AdapterError as e:
        return _fail(args, 2, "account_unavailable", str(e))
    limit = args.limit
    maximum = adapter_cls.COMMUNITY_SEARCH_MAX_LIMIT
    if limit is not None and (type(limit) is not int or not 1 <= limit <= maximum):
        return _fail(args, 2, "invalid_request",
                     f"--limit は 1〜{maximum} です（有償の読み取りなので範囲外は断ります）")
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        return _fail(args, 2, "no_token", f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    now = jst.now_jst()
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        rows = adapter.community_search(query, limit=limit)    # ← X への要求はここ 1 回
    except accounts_mod.AccountStopped as e:
        _record(args.account, n=None, status="error", error="community_search_failed")
        return _fail(args, 1, "account_stopped", str(e))
    except adapter_base.AdapterError as e:
        _record(args.account, n=None, status="error", error="community_search_failed")
        text = redact_mod.redact(str(e))
        head = text.split(":", 1)[0].strip()
        code = head if head in ("budget_exhausted", "provider_rate_limited") else "upstream_refused"
        return _fail(args, 1, code, f"コミュニティを探せませんでした: {text}")
    cannot_say = []
    if not rows:
        cannot_say.append("0 件でした。本当に無いか、X が語を受け付けなかったかは区別できません")
    if getattr(adapter, "community_more", False):
        cannot_say.append(f"続きがあります（{len(rows)} 件で止めました。続きは読んでいません）")
    cannot_say.append("参加しているかは分かりません（X の資料にその欄がありません）")
    _record(args.account, n=len(rows), status="ok", error=None)
    result = {"account": args.account, "medium": "x", "query": query, "n": len(rows),
              "communities": rows, "x_read_cost": adapter.community_read_cost(),
              "cannot_say": cannot_say, "storage": "display_only", "note": NOTE,
              "provenance": {"fetched_at": jst.iso(now), "endpoint": "GET /2/communities/search"}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"community  {args.account}  「{query}」  件数={len(rows)}")
    for row in rows:
        members = row["member_count"] if row["member_count"] is not None else "—"
        print(f"  {row['community_id']}  {row['name'] or '—'}  人数={members}"
              f"  join_policy={row['join_policy'] or '—'}  access={row['access'] or '—'}"
              f"  作成={row['created_at'] or '—'}")
        if row["description"]:
            print(f"    {row['description']}")
        print(f"    {row['url']}")
    for line in cannot_say:
        print(f"  言えない: {line}")
    cost = result["x_read_cost"]
    print(f"  X の料金の目安: {cost['communities']} 件・{cost['usd_estimate']} USD（Community: Read・推定・"
          f"1 日の本数には数えない）")
    print(f"  出すとき: thth send {args.account} --community <id> …／queue の front-matter に community_id: <id>")
    print(f"注記: {NOTE}。表示だけで、道具は保存しません")
    return 0
