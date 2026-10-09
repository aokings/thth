"""`thth repost|unrepost|like|unlike <account> <post> --by <名前>`（設計 3.15.0）。

**`build_parser()` には 1 行しか足さない**（`reaction_cli.register(sub)`）。

反応は本文を作らず取り消せるので、承認の二段は掛けない（設計 §2.1・§6-1。Threads の
再投稿も段 0 で取り消せると分かったので二段にしない）。代わりに:

fail-closed:
  - 台帳に `production: true` が無ければ SNS に送らない（`thth retract` と同じ門）。
  - SNS の API に口が無い反応は rc=2 `unsupported_on_platform`（Threads のいいね）。
  - 要る scope がトークンに無いと分かっていれば叩かずに rc=2 `scope_missing`
    （Mastodon の `write:favourites`・X の `like.write`。`thth auth` を案内する）。
  - ガード（`guard.check_reaction`）: 止まった口座・`daily_max_reactions`・再投稿の夜間。
    公開の枠（`daily_max_posts`・burst）には数えない。

冪等: 取り消していない同じ反応が記録にあれば SNS に送らずに返す（`already_done`）。
取り消しは記録の `platform_id` を使い、記録に `undone_at` を足す。記録が無ければ
`not_found`（THTH を通していない反応には触らない）。

**SNS への要求は 1 回だけ・再試行しない**（応答を失ったら結果不明として言い、記録は
書かない）。X は従量なので、料金の目安（作る 0.015 USD・消す 0.010 USD）を出力に添える。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import core
from . import guard as guard_mod
from . import lock as lock_mod
from . import postid as postid_mod
from . import reactions as reactions_mod
from . import redact as redact_mod
from .adapters import base as adapter_base

# 命令 → (反応の種類, 取り消しか)
COMMANDS = {
    "repost": ("repost", False),
    "unrepost": ("repost", True),
    "like": ("like", False),
    "unlike": ("like", True),
}
WORDS = {("repost", False): "再投稿", ("repost", True): "再投稿の取り消し",
         ("like", False): "いいね", ("like", True): "いいねの取り消し"}
HELP = {
    "repost": "投稿を再投稿する（Threads／Bluesky／Mastodon／X・production: true の口座だけ）",
    "unrepost": "THTH で再投稿したものを取り消す",
    "like": "投稿にいいねする（Bluesky／Mastodon／X。Threads の API にいいねはありません）",
    "unlike": "THTH でいいねしたものを取り消す",
}
MAX_BY = 256


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    for command in COMMANDS:
        p = sub.add_parser(command, help=HELP[command])
        p.add_argument("account")
        p.add_argument("post", help="投稿の id か URL")
        p.add_argument("--by", default=None, help="誰が頼んだか（記録に残す・必須）")
        p.add_argument("--json", action="store_true")
        p.add_argument("--wait", type=lock_mod.wait_seconds, default=0,
                       help="ロックを待つ秒数（既定 0）")
        # どの口から来たか（MCP の道具は `--via mcp` を付けて呼ぶ）。
        p.add_argument("--via", choices=reactions_mod.VIAS, default="cli",
                       help=argparse.SUPPRESS)
        p.set_defaults(func=cmd_reaction, reaction_command=command)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(args, rc: int, code: str, message: str, **extra) -> int:
    if getattr(args, "json", False):
        _print_json({"ok": False, "error": code, "message": message, **extra})
    else:
        print(f"{code}: {message}" if not message.startswith(code) else message, file=sys.stderr)
    return rc


# --------------------------------------------------------------------------
# 投稿の指定（id か URL）
# --------------------------------------------------------------------------

_X_HOSTS = ("x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com",
            "mobile.twitter.com")


def _x_url_to_id(text: str) -> str:
    parsed = urllib.parse.urlsplit(text)
    parts = parsed.path.rstrip("/").split("/")
    if (parsed.scheme != "https" or parsed.netloc.lower() not in _X_HOSTS or len(parts) < 4
            or parts[2] != "status" or not re.fullmatch(r"[0-9]{1,19}", parts[3])):
        raise postid_mod.PostIdError(
            "invalid_post_url: https://x.com/<user>/status/<数字> を指定してください")
    return parts[3]


def _mastodon_url_to_id(account_cfg: dict, text: str) -> str:
    parsed = urllib.parse.urlsplit(text)
    instance = urllib.parse.urlsplit(os.environ.get("THTH_MASTODON_INSTANCE")
                                     or account_cfg.get("instance") or "")
    parts = parsed.path.rstrip("/").split("/")
    if parsed.netloc.lower() != (instance.netloc or "").lower() or not parsed.netloc:
        raise postid_mod.PostIdError(
            "invalid_post_url: 自分のインスタンス以外の URL は id に直せません"
            "（自分のインスタンスで開いた投稿の URL か、id を渡してください）")
    if len(parts) == 3 and parts[1].startswith("@") and re.fullmatch(r"[0-9]{1,32}", parts[2]):
        return parts[2]
    if (len(parts) == 5 and parts[1] == "users" and parts[3] == "statuses"
            and re.fullmatch(r"[0-9]{1,32}", parts[4])):
        return parts[4]
    raise postid_mod.PostIdError(
        "invalid_post_url: https://<インスタンス>/@<user>/<数字> を指定してください")


def resolve_post(account_cfg: dict, account_name: str, value: str) -> str:
    """`<post>` を id にする。Threads・Bluesky は `postid.for_account()`、X と Mastodon はここ。"""
    text = (value or "").strip()
    if not text:
        raise postid_mod.PostIdError("invalid_post: 投稿の id か URL が空です")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text):
        raise postid_mod.PostIdError("invalid_post: 制御文字は使えません")
    media = account_cfg.get("media")
    if text.startswith(("https://", "http://")):
        if media == "x":
            return _x_url_to_id(text)
        if media == "mastodon":
            return _mastodon_url_to_id(account_cfg, text)
    out = postid_mod.for_account(account_cfg, text, account_name)
    return out.strip() if isinstance(out, str) else out


# --------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------

def _x_cost(media: str, adapter_cls, undo: bool):
    prices = getattr(adapter_cls, "REACTION_PRICE_USD", None)
    if media != "x" or not prices:
        return None
    return {"usd": prices["delete" if undo else "create"],
            "item": "User Interaction: Delete" if undo else "User Interaction: Create",
            "basis": "estimate_not_actual_charge"}


def cmd_reaction(args) -> int:
    command = getattr(args, "reaction_command", None)
    if command not in COMMANDS:
        return _fail(args, 2, "invalid_request", "知らない反応です")
    kind, undo = COMMANDS[command]
    word = WORDS[(kind, undo)]
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by or len(by) > MAX_BY or any(ord(c) < 32 or ord(c) == 127 for c in by):
        return _fail(args, 1, "by_required", "--by を付けてください（誰が頼んだかを記録します）。"
                                            "環境変数 THTH_ACTOR でも指定できます")
    via = getattr(args, "via", "cli") or "cli"

    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return _fail(args, 2, "account_unavailable", str(e))
    media = account_cfg.get("media")
    try:
        adapter_cls = adapters_mod.adapter_class(media)
    except adapter_base.AdapterError as e:
        return _fail(args, 2, "account_unavailable", str(e))
    if not adapter_cls.reaction_supported(kind):
        return _fail(args, 2, "unsupported_on_platform",
                     adapter_cls.reaction_unsupported_message(kind))

    try:
        post_id = resolve_post(account_cfg, args.account, args.post)
    except postid_mod.PostIdError as e:
        return _fail(args, 2, "invalid_post", str(e))
    # Threads・Mastodon・X の id は数字だけ（Bluesky は AT URI・`is_post_id`）。
    # 形違いは**ネットワークの前に**断る。
    if (not isinstance(post_id, str) or not adapter_cls.is_post_id(post_id)
            or media != "bluesky" and not re.fullmatch(r"[0-9]{1,32}", post_id)):
        return _fail(args, 2, "invalid_post", adapter_cls.POST_ID_FORM_HINT)

    # **production: true が無ければ SNS に送らない**（retract と同じ fail-closed）。
    if not approval_mod.is_true(account_cfg.get("production")):
        return _fail(args, 1, "production_disabled",
                     f"{args.account}: 台帳に production: true が無いので{word}しません"
                     "（SNS には送っていません）")
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        return _fail(args, 2, "no_token",
                     f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    missing = adapter_cls.reaction_scope_missing(token, kind, undo=undo)
    if missing:
        return _fail(args, 2, "scope_missing", str(adapter_base.ScopeMissing(missing, args.account)),
                     permission=missing)

    state_dir = accounts_mod.state_dir_for(args.account)
    try:
        with core._account_locks(args.account, account_cfg, state_dir, wait=getattr(args, "wait", 0)):
            return _locked(args, account_cfg, adapter_cls, token, state_dir, kind=kind, undo=undo,
                           post_id=post_id, by=by, via=via, word=word, media=media)
    except lock_mod.LockBusy:
        return _fail(args, 1, "locked", f"{args.account} は既に実行中です（ロック取得失敗）。"
                                        "--wait <秒> で空くのを待てます")


def _locked(args, account_cfg, adapter_cls, token, state_dir, *, kind, undo, post_id, by, via,
            word, media) -> int:
    account = args.account
    existing = reactions_mod.active(state_dir, kind, post_id)
    if not undo and existing is not None:
        # 冪等: SNS に送らない（ガードにも数えない）。
        return _done(args, existing, kind=kind, undo=undo, word=word, already=True, cost=None)
    if undo and existing is None:
        return _fail(args, 1, "not_found",
                     f"{account}: {post_id} への{WORDS[(kind, False)]}の記録がありません"
                     "（THTH を通していない反応には触りません。媒体の画面から手で）")
    try:
        guard_mod.check_reaction(account, account_cfg, kind, undo=undo)
    except guard_mod.GuardRefused as e:
        extra = {k: v for k, v in (("next_at", e.next_at), ("reason", e.reason)) if v}
        tail = f"（次は {e.next_at} から）" if e.next_at else ""
        return _fail(args, 1, str(e), f"安全装置が{word}を断りました{tail}", **extra)

    adapter = adapters_mod.make_adapter(account_cfg, token)
    try:
        if undo:
            method = getattr(adapter, "un" + kind)
            method(existing["platform_id"], post_id=post_id)   # ← SNS への要求はここ 1 回
        else:
            result = getattr(adapter, kind)(post_id)            # ← SNS への要求はここ 1 回
    except adapter_base.UnsupportedOnPlatform as e:
        return _fail(args, 2, "unsupported_on_platform", str(e))
    except adapter_base.ScopeMissing as e:
        return _fail(args, 2, "scope_missing", str(e).replace("<account>", account),
                     permission=e.permission)
    except adapter_base.PermissionMissing as e:
        return _fail(args, 2, "scope_missing", str(e).replace("<account>", account),
                     permission=e.permission)
    except accounts_mod.AccountStopped as e:
        return _fail(args, 1, "account_stopped", str(e))
    except Exception as e:  # noqa: BLE001 — 媒体の失敗は 1 行にして記録は変えない
        return _fail(args, 1, "upstream_refused",
                     f"{word}できませんでした（記録は変えていません）: " + redact_mod.redact(str(e)))

    cost = _x_cost(media, adapter_cls, undo)
    if undo:
        row = reactions_mod.mark_undone(existing, by=by, via=via)
    else:
        reaction_id = (result or {}).get("reaction_id") if isinstance(result, dict) else None
        if not isinstance(reaction_id, str) or not reaction_id:
            return _fail(args, 1, "upstream_refused",
                         f"{word}の応答に id がありません（記録していません・媒体の画面で確かめてください）")
        row = reactions_mod.write(state_dir, kind=kind, post_id=post_id, platform_id=reaction_id,
                                  by=by, via=via, media=media)
    return _done(args, row, kind=kind, undo=undo, word=word, already=False, cost=cost)


def _done(args, row, *, kind, undo, word, already, cost) -> int:
    record = reactions_mod.public(row)
    payload = {"ok": True, "account": args.account, "kind": kind, "undo": undo,
               "post_id": record["post_id"], "platform_id": record.get("platform_id"),
               "already_done": already, "record": record}
    if cost is not None:
        payload["x_cost_estimate"] = cost
    if args.json:
        _print_json(payload)
        return 0
    if already:
        print(f"既に{word}しています（SNS には送っていません）: {args.account} {record['post_id']}"
              f"（{record.get('at')}・{record.get('by')}）")
    else:
        when = record.get("undone_at") if undo else record.get("at")
        print(f"{word}しました: {args.account} {record['post_id']}（{when}・"
              f"{record.get('undone_by') if undo else record.get('by')}）")
    if cost is not None:
        print(f"  X の料金の目安: {cost['usd']} USD（{cost['item']}・推定）")
    return 0
