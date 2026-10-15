"""`thth list create|add|remove|show`——X の**非公開リスト**（3.17.0・関東レーン依頼・masaru 10/12）。

**`build_parser()` には 1 行しか足さない**（`list_cli.register(sub)`）。MCP には今回は足さない。

決まり（一次資料 docs.x.com・X Developer Guidelines「No bulk or indiscriminate additions」）:
  - **公開のリストは作らない**——`private` は常に true（旗も設けない）。
  - 足す／外すは**人が選んだ少数だけ**: 1 回に最大 10 人（それ以上は rc=2・SNS に送らない）。
    検索結果から自動で足す経路は作らない（人の id か @handle を引数で受けるだけ）。
  - X 以外の account は rc=2 `unsupported_on_platform`（`cannot_say` に理由）。
  - 要る scope（`list.read`・`list.write`）がトークンに無いと分かっていれば、**叩く前に**
    rc=2 `scope_missing`（`thth auth <account> --by <名前>` を案内）。

書き込み（create・add・remove）の作法は `thth like`（`reaction_cli`）に合わせる: `--by` 必須・
台帳に `production: true` が無ければ SNS に送らない・account のロック・**SNS への要求は 1 回
ずつ・再試行しない**。費用は月の X 予算の台帳（`budget_x.list_write`・作る 0.010 USD・
足す／外す 0.005 USD）に予約→確定で入れる。@handle の id 引きは本人確認と同じ予約
（`GET /2/users/by/username`・1 回 0.010 USD）。

読み取り（show）は `thth where`・`thth thread` と同じ有償の読み取り（Posts: Read の台帳・
1 日の本数 `x_daily_reads`・月の X 予算）。返す形は where の行と同じ（本文は全文の
`text` と 1 行の `preview`・**返すだけで保存しない**・10/15 から全文）。runs には本文も名前も id も書かない（件数だけ）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import core
from . import jst
from . import lock as lock_mod
from . import redact as redact_mod
from . import runs as runs_mod
from .adapters import base as adapter_base

MAX_BY = 256
MAX_MEMBERS = 10
LIST_ID = re.compile(r"[0-9]{1,19}")
USER_ID = re.compile(r"[0-9]{1,19}")
HANDLE = re.compile(r"@([A-Za-z0-9_]{1,15})")
NOTE = ("非公開リストだけ・人が選んだ少数だけ（X Developer Guidelines: bulk や無差別な追加は不可）。"
        "足された相手に X が通知するかは未確認")
WORDS = {"create": "リストを作る", "add": "リストに足す", "remove": "リストから外す",
         "show": "リストの投稿を読む"}


class ListRefused(Exception):
    def __init__(self, rc, code, message, **extra):
        super().__init__(message)
        self.rc, self.code, self.extra = rc, code, extra


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser("list", help="X の非公開リスト（作る・足す・外す・読む。公開のリストは作らない）")
    ops = p.add_subparsers(dest="list_command", required=True)

    def common(q, *, write):
        q.add_argument("account")
        q.add_argument("--json", action="store_true")
        if write:
            q.add_argument("--by", default=None, help="誰が頼んだか（必須・THTH_ACTOR でも可）")
            q.add_argument("--wait", type=lock_mod.wait_seconds, default=0,
                           help="ロックを待つ秒数（既定 0）")

    c = ops.add_parser("create", help="非公開リストを作る（private は常に true）")
    common(c, write=True)
    c.add_argument("name", help="名前（1〜25 字）")
    c.add_argument("--description", default=None, help="説明（100 字まで）")
    c.set_defaults(func=cmd_list, list_op="create")

    for op, label in (("add", "非公開リストに人を足す（1 回 10 人まで）"),
                      ("remove", "非公開リストから人を外す（1 回 10 人まで）")):
        q = ops.add_parser(op, help=label)
        common(q, write=True)
        q.add_argument("list_id", help="リストの id（数字）")
        q.add_argument("users", nargs="+", help="人の id（数字）か @handle（1 回 10 人まで）")
        q.set_defaults(func=cmd_list, list_op=op)

    s = ops.add_parser("show", help="リストの投稿を読む（有償の読み取り・保存しない）")
    common(s, write=False)
    s.add_argument("list_id", help="リストの id（数字）")
    s.add_argument("--limit", type=int, default=None, help="本数（既定 10・最大 25）")
    s.set_defaults(func=cmd_list, list_op="show")


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(args, rc: int, code: str, message: str, **extra) -> int:
    if getattr(args, "json", False):
        _print_json({"ok": False, "error": code, "message": message, **extra})
    else:
        print(f"{code}: {message}" if not message.startswith(code) else message, file=sys.stderr)
    return rc


def _clean_text(value, *, what, maximum, minimum=0):
    text = (value or "").strip() if isinstance(value, str) else ""
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text):
        raise ListRefused(2, "invalid_request", f"{what}に制御文字は使えません")
    if not minimum <= len(text) <= maximum:
        raise ListRefused(2, "invalid_request", f"{what}は {minimum}〜{maximum} 字です（{len(text)} 字）")
    return text


def _parse_users(values):
    """人の指定（数字の id か @handle）。**10 人を超えたら SNS に送らずに断る。**"""
    if len(values) > MAX_MEMBERS:
        raise ListRefused(2, "too_many_members",
                          f"1 回に足す／外すのは {MAX_MEMBERS} 人までです（{len(values)} 人）。"
                          "人が選んだ少数だけにしてください（bulk は X の決まりで不可）")
    out = []
    for raw in values:
        text = (raw or "").strip()
        if USER_ID.fullmatch(text):
            item = ("id", text)
        elif HANDLE.fullmatch(text):
            item = ("handle", HANDLE.fullmatch(text).group(1))
        else:
            raise ListRefused(2, "invalid_user",
                              f"人は数字の id か @handle（英数と _ の 15 字まで）で指定してください: {text!r}")
        if item not in out:
            out.append(item)
    return out


def _prepare(args, op):
    """台帳・媒体・トークン・scope を確かめて `(account_cfg, adapter_cls, token)`。"""
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        raise ListRefused(2, "account_unavailable", str(e)) from None
    media = account_cfg.get("media")
    if media != "x":
        message = f"{args.account}: リストは X だけです（この account は {media}）"
        raise ListRefused(2, "unsupported_on_platform", message, cannot_say=[message])
    try:
        adapter_cls = adapters_mod.adapter_class(media)
    except adapter_base.AdapterError as e:
        raise ListRefused(2, "account_unavailable", str(e)) from None
    if op != "show" and not approval_mod.is_true(account_cfg.get("production")):
        raise ListRefused(1, "production_disabled",
                          f"{args.account}: 台帳に production: true が無いので{WORDS[op]}ことはしません"
                          "（SNS には送っていません）")
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        raise ListRefused(2, "no_token", f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    missing = adapter_cls.list_scope_missing(token, op)
    if missing:
        raise ListRefused(2, "scope_missing", str(adapter_base.ScopeMissing(missing, args.account)),
                          permission=missing)
    return account_cfg, adapter_cls, token


def _adapter_error(args, e, *, what, **extra):
    if isinstance(e, adapter_base.ScopeMissing):
        return _fail(args, 2, "scope_missing", str(e).replace("<account>", args.account),
                     permission=e.permission, **extra)
    if isinstance(e, adapter_base.PermissionMissing):
        return _fail(args, 2, "scope_missing", str(e).replace("<account>", args.account),
                     permission=e.permission, **extra)
    if isinstance(e, accounts_mod.AccountStopped):
        return _fail(args, 1, "account_stopped", str(e), **extra)
    text = redact_mod.redact(str(e))
    code = text.split(":", 1)[0].strip() if re.match(r"[a-z_0-9]+:", text) else "upstream_refused"
    return _fail(args, 1, code if code in ("budget_exhausted", "x_daily_read_cap") else "upstream_refused",
                 f"{what}ことができませんでした: {text}", **extra)


def _record(account, action, *, n, status, error):
    try:
        runs_mod.record_minimal(account, {"action": action, "account": account, "n": n,
                                          "status": status, "error": error})
    except (OSError, ValueError, RuntimeError):
        pass


def cmd_list(args) -> int:
    op = getattr(args, "list_op", None)
    if op not in WORDS:
        return _fail(args, 2, "invalid_request", "知らない操作です")
    try:
        if op == "show":
            return _show(args)
        by = (getattr(args, "by", None) or os.environ.get("THTH_ACTOR") or "").strip()
        if not by or len(by) > MAX_BY or any(ord(c) < 32 or ord(c) == 127 for c in by):
            raise ListRefused(1, "by_required", "--by を付けてください（誰が頼んだかを記録します）。"
                                                "環境変数 THTH_ACTOR でも指定できます")
        if op == "create":
            name = _clean_text(args.name, what="リストの名前", minimum=1, maximum=25)
            description = _clean_text(args.description, what="リストの説明", maximum=100) or None
            members = None
        else:
            if not LIST_ID.fullmatch((args.list_id or "").strip()):
                raise ListRefused(2, "invalid_request", "リストの id は 19 桁までの数字です")
            members = _parse_users(args.users)
            name = description = None
        account_cfg, adapter_cls, token = _prepare(args, op)
    except ListRefused as e:
        return _fail(args, e.rc, e.code, str(e), **e.extra)

    state_dir = accounts_mod.state_dir_for(args.account)
    try:
        with core._account_locks(args.account, account_cfg, state_dir, wait=getattr(args, "wait", 0)):
            try:
                adapter = adapters_mod.make_adapter(account_cfg, token)
            except accounts_mod.AccountStopped as e:
                return _fail(args, 1, "account_stopped", str(e))
            if op == "create":
                return _create(args, adapter, name, description, by)
            return _members(args, adapter, op, args.list_id.strip(), members, by)
    except lock_mod.LockBusy:
        return _fail(args, 1, "locked", f"{args.account} は既に実行中です（ロック取得失敗）。"
                                        "--wait <秒> で空くのを待てます")


def _cost(adapter_units):
    from . import budget_x
    usd = budget_x.LIST_UNIT_PRICE * adapter_units
    return {"usd": budget_x.decimal_text(usd), "basis": "estimate_not_actual_charge",
            "price_version": budget_x.LIST_PRICE_VERSION}


def _create(args, adapter, name, description, by) -> int:
    from . import budget_x
    try:
        result = adapter.list_create(name, description=description)   # ← SNS への要求はここ 1 回
    except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
        _record(args.account, "list_create", n=0, status="error", error="list_create_failed")
        return _adapter_error(args, e, what=WORDS["create"])
    _record(args.account, "list_create", n=1, status="ok", error=None)
    payload = {"ok": True, "account": args.account, "operation": "create", "by": by,
               "list_id": result["list_id"], "name": result["name"], "private": True,
               "x_cost_estimate": _cost(budget_x.LIST_CREATE_UNITS), "note": NOTE}
    if args.json:
        _print_json(payload)
        return 0
    print(f"非公開リストを作りました: {args.account} {result['list_id']}（{result['name']}・{by}）")
    print(f"  X の料金の目安: {payload['x_cost_estimate']['usd']} USD（List: Create・推定）")
    print(f"  注記: {NOTE}")
    return 0


def _members(args, adapter, op, list_id, members, by) -> int:
    from . import budget_x
    done, lookups = [], 0
    method = adapter.list_add_member if op == "add" else adapter.list_remove_member
    pending = list(members)
    while pending:
        kind, value = pending[0]
        try:
            if kind == "handle":
                lookups += 1
                user_id = adapter.user_by_username(value)["user_id"]   # Users: Read 1 回
            else:
                user_id = value
            method(list_id, user_id)                                      # ← SNS への要求は 1 人 1 回
        except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
            _record(args.account, "list_" + op, n=len(done), status="error", error="list_" + op + "_failed")
            rest = [("@" + v if k == "handle" else v) for k, v in pending]
            return _adapter_error(args, e, what=WORDS[op], done=done, not_done=rest,
                                  failed=rest[0])
        done.append({"user": "@" + value if kind == "handle" else value, "user_id": user_id})
        pending.pop(0)
    _record(args.account, "list_" + op, n=len(done), status="ok", error=None)
    usd = budget_x.LIST_UNIT_PRICE * budget_x.LIST_MANAGE_UNITS * len(done) + budget_x.PRICE * lookups
    payload = {"ok": True, "account": args.account, "operation": op, "by": by, "list_id": list_id,
               "done": done,
               "x_cost_estimate": {"usd": budget_x.decimal_text(usd), "basis": "estimate_not_actual_charge",
                                   "members": len(done), "handle_lookups": lookups},
               "note": NOTE}
    if args.json:
        _print_json(payload)
        return 0
    word = "足しました" if op == "add" else "外しました"
    print(f"リスト {list_id} に{word}（{by}）: " if op == "add" else f"リスト {list_id} から{word}（{by}）: ",
          end="")
    print("・".join(row["user"] for row in done))
    print(f"  X の料金の目安: {payload['x_cost_estimate']['usd']} USD"
          f"（List: Manage {len(done)} 回・handle の id 引き {lookups} 回・推定）")
    print(f"  注記: {NOTE}")
    return 0


def _show(args) -> int:
    from . import threads_read_cli as threads_read_cli_mod
    from . import where_cli as where_cli_mod
    list_id = (args.list_id or "").strip()
    if not LIST_ID.fullmatch(list_id):
        return _fail(args, 2, "invalid_request", "リストの id は 19 桁までの数字です")
    try:
        account_cfg, adapter_cls, token = _prepare(args, "show")
    except ListRefused as e:
        return _fail(args, e.rc, e.code, str(e), **e.extra)
    limit = args.limit
    if limit is not None and (type(limit) is not int or not 1 <= limit <= adapter_cls.LIST_TWEETS_MAX_LIMIT):
        return _fail(args, 2, "invalid_request",
                     f"--limit は 1〜{adapter_cls.LIST_TWEETS_MAX_LIMIT} です（有償の読み取りなので範囲外は断ります）")
    now = jst.now_jst()
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        rows = adapter.list_tweets(list_id, limit=limit)
    except (adapter_base.AdapterError, accounts_mod.AccountStopped) as e:
        _record(args.account, "list_show", n=None, status="error", error="list_show_failed")
        return _adapter_error(args, e, what=WORDS["show"])
    try:
        replied, replied_why = threads_read_cli_mod.replied_index(account_cfg, args.account)
    except (OSError, ValueError, accounts_mod.AccountError) as e:
        replied, replied_why = None, str(e)
    posts = where_cli_mod._posts_with_preview(rows, replied)
    cannot_say = [f"返信の印: {replied_why}"] if replied_why else []
    if not posts:
        cannot_say.append(where_cli_mod.ZERO_MESSAGE)
    if getattr(adapter, "list_more", False):
        cannot_say.append(f"続きがあります（{len(posts)} 本で止めました。続きは読んでいません）")
    _record(args.account, "list_show", n=len(posts), status="ok", error=None)
    result = {"account": args.account, "list_id": list_id, "medium": "x", "n": len(posts),
              "posts": posts, "x_read_cost": adapter.read_cost(), "cannot_say": cannot_say,
              "storage": "display_only", "note": NOTE,
              "provenance": {"fetched_at": jst.iso(now), "endpoint": "GET /2/lists/:id/tweets"}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"list  {args.account}  {list_id}  件数={len(posts)}")
    for post in posts:
        mark = threads_read_cli_mod._replied_cell(post["replied"])
        print(f"  {mark}{post['timestamp'] or '—'}  @{post['author'] or '—'}"
              f"（{post['author_key'] or '—'}）")
        threads_read_cli_mod.print_full_text(post.get("text"), indent="    ")
        if post["permalink"]:
            print(f"    {post['permalink']}")
        print(f"    post_id: {post['post_id']}")
    for line in cannot_say:
        print(f"  言えない: {line}")
    print("  " + where_cli_mod.read_cost_line(result["x_read_cost"]))
    print("注記: 表示だけで、道具は保存しません")
    return 0
