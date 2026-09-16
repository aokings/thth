"""`thth thread <account> <post_id>` / MCP `thread_read`——枝をその場で読む
（保存しない・設計「自分の泉」§2.1・T1-2）。

芯（設計 §0・§2.1）: **枝はその場で読む。残すのは自分の行為と反応。判断は
LLM。** この口は「読んで見せる」だけ——**台帳に 1 バイトも書かない。runs に
も本文を出さない。**

規約（設計「自分の泉」§2.1・T1-2 発注書）:

  (a) **`data/` の下に何も書かない**（実行前後で `accounts.data_dirs()` の
      全 dir のファイル一覧とサイズが同じ）。
  (b) runs には 1 行だけ: `{"action": "thread_read", "account", "medium",
      "post_id", "messages", "truncated", "status", "error"}`。**`text`・
      `username` を含めない**。
  (c) `PermissionMissing`・`AdapterError` は `threads_read_cli.py` の
      `mentions` と同じ出し方（`--json` なら `{"error", "permission",
      "account", "granted"}`・rc=1）。**「読めない」を空の枝にしない。**
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import after_cli as after_cli_mod
from . import engagements as engagements_mod
from . import jst
from . import redact as redact_mod
from . import replies as replies_mod
from . import runs as runs_mod
from . import threads_read_cli as threads_read_cli_mod
from .adapters import base as adapter_base

CAPABILITY = "thread_read"

DEFAULT_MAX_MESSAGES = 200
MAX_MESSAGES_LIMIT = 1000

# 人向け画面の本文プレビューの長さ（`threads_read_cli.TEXT_PREVIEW_CHARS` と
# 同じ考え方だが、こちらは枝を読むための口なので少し長め・120 字）。
TEXT_PREVIEW_CHARS = 120

# `provenance.source`（媒体ごと・T1-2 発注書のとおり）。
SOURCE_BY_MEDIUM = {
    "bluesky": "bluesky:getPostThread",
    "mastodon": "mastodon:/context",
    "threads": "threads:/conversation",
}
# `provenance.permission`（Threads だけ・他は None）。
PERMISSION_BY_MEDIUM = {
    "threads": "threads_read_replies",
}

# post_id の形が違うときに見せる、媒体ごとの手本（T6-2）。**形の検査そのものは
# adapter の classmethod（`Adapter.is_post_id()`）に置く**——ここは断り文の
# 言い回しだけを持つ（media を見て分岐する core の規約は壊さない・
# `thread_read` は判定を「呼ぶだけ」）。
POST_ID_FORM_HINT = {
    "bluesky": "Bluesky の post_id は `at://did:…/app.bsky.feed.post/…` の形です。",
    "mastodon": "Mastodon の post_id は数字の id です。",
    "threads": "Threads の post_id は数字の id です。",
}


class ThreadReadError(Exception):
    """問いが受け取れない（post_id が空・max_messages が範囲外 等）。

    **黙って空の答えを返さない**（loud reject。`after_cli.AfterError` と同じ筋）。
    """


def _reject(message: str) -> None:
    raise ThreadReadError(message)


def _depth_map(root_id: str, messages: list) -> dict:
    """`message_id` → 根からの段数（直下が 1）。

    `replied_to` を根まで辿って数える。`thth/threadshape.py::_tree()` と同じ
    考え方（**辿れなければ `None`。推測しない**）——ただしこちらはディスクの
    行ではなく、`conversation()` が返した生の `Message` 辞書を直に見る。
    """
    nodes: dict = {}
    for m in messages:
        mid = m.get("message_id")
        if mid is None:
            continue
        mid = str(mid)
        if mid in nodes:
            continue
        replied = m.get("replied_to")
        nodes[mid] = {"parent": str(replied) if replied is not None else None,
                      "depth": None}

    for rid in nodes:
        if nodes[rid]["depth"] is not None:
            continue
        seen, cur, chain = set(), rid, []
        while True:
            if cur is None or cur in seen:
                break                              # 親が無い・循環 → 辿れない
            if cur == root_id:
                for depth, node_id in enumerate(reversed(chain), start=1):
                    if nodes[node_id]["depth"] is None:
                        nodes[node_id]["depth"] = depth
                break
            if cur not in nodes:
                break                              # 台帳に無い親 → 辿れない
            seen.add(cur)
            chain.append(cur)
            cur = nodes[cur]["parent"]

    return {mid: info["depth"] for mid, info in nodes.items()}


def _already_replied_index(account_cfg: dict, account_name: str):
    """`message_id` → 「もう返した」印、の材料を 2 つ用意する。

    戻り値 `(ledger_by_reply_to, queue_index, unreadable_reasons)`。
    `ledger_by_reply_to` は絡みの台帳（`engagements.load()`）の行を
    `reply_to` で束ねたもの（同じ先に複数あれば `posted_at` が最も古い行を
    残す——**最初に絡みに行った記録**）。`queue_index` は
    `threads_read_cli.replied_index()` の索引（`reply_to → {"status", "file"}`）。
    **台帳が読めなかった理由は捨てない**（`unreadable_reasons`）——
    T1-2 発注書「台帳が読めないときは `null` を『返していない』にしない」。
    """
    unreadable: list = []

    eng = engagements_mod.load(account_cfg, account_name)
    if eng["broken"]:
        unreadable.append(f"絡みの台帳の一部が読めません（{eng['broken']} 本）")
    ledger_by_reply_to: dict = {}
    for row in eng["rows"]:
        target = row.get("reply_to")
        if not target:
            continue
        target = str(target)
        current = ledger_by_reply_to.get(target)
        if current is None or (row.get("posted_at") or "") < (current.get("posted_at") or ""):
            ledger_by_reply_to[target] = row

    queue_index, queue_reason = threads_read_cli_mod.replied_index(account_cfg, account_name)
    if queue_index is None:
        unreadable.append(queue_reason or "queue の「もう返した」印が読めません")

    return ledger_by_reply_to, queue_index, unreadable


def _already_replied_for(message_id: str, *, ledger_by_reply_to: dict,
                         queue_index: dict | None):
    """規約 (c) の 3 段: (a) 絡みの台帳 → (b) queue の下書き → (c) `None`。"""
    ledger_row = ledger_by_reply_to.get(str(message_id))
    if ledger_row is not None:
        return {"post_id": ledger_row.get("post_id"), "at": ledger_row.get("posted_at")}
    if queue_index:
        entry = queue_index.get(str(message_id))
        if entry is not None:
            return {"status": entry["status"]}
    return None


def _sort_key(message: dict) -> tuple:
    """根からの時刻順。**時刻が読めない行は末尾**（落とさない・規約 5）。"""
    ts = message.get("timestamp")
    if isinstance(ts, str) and ts:
        return (0, ts)
    return (1, "")


def answer(account_name: str, post_id: str, *, since: str | None = None,
           max_messages: int = DEFAULT_MAX_MESSAGES, now=None) -> dict:
    """`thread_read` の答え（設計「自分の泉」§2.1）。**読むだけ・何も書かない。**"""
    if not isinstance(post_id, str) or not post_id.strip():
        _reject("post_id が空です")
    if not isinstance(max_messages, int) or isinstance(max_messages, bool):
        _reject(f"max_messages は整数です: {max_messages!r}")
    if max_messages < 1 or max_messages > MAX_MESSAGES_LIMIT:
        _reject(f"max_messages は 1〜{MAX_MESSAGES_LIMIT} です: {max_messages!r}")
    post_id = post_id.strip()

    now = now if now is not None else jst.now_jst()
    account_cfg = accounts_mod.load_account(account_name)
    media = account_cfg.get("media")
    if CAPABILITY not in adapters_mod.capabilities_for(media):
        raise adapter_base.AdapterError(
            f"{account_name}: この媒体（{media}）では枝を読む口は未対応です")
    adapter_cls = adapters_mod.adapter_class(media)
    # **形違いは adapter を叩く前に断る**（T6-2）。短い id（rkey だけ）を渡す
    # 取り違いが、adapter の生のエラーとして被験者に届いていた（試験の摩擦）。
    # 検査そのもの（`is_post_id()`）は媒体ごとの adapter に置く——ここは
    # それを呼んで、断り文に手本と `--json` の使い方を添えるだけ。
    if not adapter_cls.is_post_id(post_id):
        hint = POST_ID_FORM_HINT.get(media, "post_id の形が違います。")
        _reject(f"{hint}`thth where {account_name} --json` の `post_id` を"
                "そのまま渡してください")
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        raise adapter_base.AdapterError(f"{account_name}: token が無いので読めません")
    adapter = adapters_mod.make_adapter(account_cfg, token)

    root_row = adapter.fetch_post(post_id)
    raw_messages = adapter.conversation(post_id, since=since)

    own_handles, unreadable_accounts = replies_mod._own_handles()
    incomplete = bool(unreadable_accounts)

    def _is_own(username):
        return replies_mod._classify_own(username, own_handles, incomplete=incomplete)

    depths = _depth_map(str(root_row.get("root_post") or post_id), raw_messages)
    ledger_by_reply_to, queue_index, ledgers_unreadable = _already_replied_index(
        account_cfg, account_name)
    if incomplete:
        ledgers_unreadable = list(ledgers_unreadable) + [
            f"account の handle が読めません: {', '.join(unreadable_accounts)}"]

    ordered = sorted(raw_messages, key=_sort_key)
    truncated = len(ordered) > max_messages
    continue_from = None
    if truncated:
        kept = ordered[:max_messages]
        continue_from = kept[-1].get("timestamp") if kept else None
    else:
        kept = ordered

    messages = []
    author_keys: set = set()
    own_count = 0
    for m in kept:
        author_key = m.get("author_key")
        if author_key:
            author_keys.add(author_key)
        is_own = _is_own(m.get("username"))
        if is_own is True:
            own_count += 1
        messages.append({
            "message_id": m.get("message_id"),
            "replied_to": m.get("replied_to"),
            "depth": depths.get(str(m.get("message_id"))) if m.get("message_id") is not None
                    else None,
            "author_key": author_key,
            "username": m.get("username"),
            "is_own": is_own,
            "timestamp": m.get("timestamp"),
            "text": m.get("text"),
            "already_replied": _already_replied_for(
                m.get("message_id"), ledger_by_reply_to=ledger_by_reply_to,
                queue_index=queue_index),
        })

    root_is_own = _is_own(root_row.get("username"))
    root_author_key = root_row.get("author_key")
    if root_author_key:
        author_keys.add(root_author_key)

    eng_rows = engagements_mod.records(account_cfg, account_name)

    return {
        "root": {
            "post_id": root_row.get("message_id"),
            "author_key": root_author_key,
            "is_own": root_is_own,
            "timestamp": root_row.get("timestamp"),
            "text": root_row.get("text"),
            "username": root_row.get("username"),
        },
        "messages": messages,
        "counts": {
            "messages": len(messages),
            "participants": len(author_keys),
            "own": own_count,
            "truncated": truncated,
        },
        # `last_reaction`（T3-2・設計「自分の泉」§2.1）: 計算は
        # `after_cli.reaction_lookup()` の 1 か所だけ（`who_is_this`・
        # `where_cli` と同じ）。
        "you_and_them": engagements_mod.author_summary(
            author_keys, eng_rows, reaction_for=after_cli_mod.reaction_lookup(account_name)),
        "provenance": {
            "fetched_at": jst.iso(now),
            "source": SOURCE_BY_MEDIUM.get(media, media),
            "permission": PERMISSION_BY_MEDIUM.get(media),
            "continue_from": continue_from,
            "ledgers_unreadable": ledgers_unreadable,
        },
    }


# ---------------------------------------------------------------- runs（規約 b）

def _record_run(account_name: str, *, media: str | None, post_id: str,
                messages, truncated: bool, status: str, error: str | None,
                now=None) -> None:
    """runs に 1 行だけ足す。**本文・username は絶対に入れない**（規約 (b)）。

    既存の `runs.append_run()` は投稿の実行専用の 11 項目を要求するので使わない
    ——`thread_read` は読むだけの別種の行なので、`runs.record_minimal()`
    （T2-2 で `where_cli` と共通化・元はここにあった）に最小の形（発注書の
    とおり）で渡すだけ。
    """
    line = {
        "action": "thread_read", "account": account_name, "medium": media,
        "post_id": post_id, "messages": messages, "truncated": truncated,
        "status": status, "error": error,
    }
    runs_mod.record_minimal(account_name, line, now=now)


# ---------------------------------------------------------------------- CLI

def _replied_prefix(already_replied) -> str:
    if not already_replied:
        return ""
    if "status" in already_replied:
        mark = threads_read_cli_mod.REPLIED_MARKS.get(
            already_replied["status"], already_replied["status"])
        return f"[{mark}] "
    return "[返信済] "


def _render_human(result: dict) -> None:
    """人向けの画面: 根 1 行 → 返信を `depth` の字下げ（設計「自分の泉」T1-2）。

    **画面は要約しない**（LLM は `--json` を読む）。
    """
    root = result["root"]
    who = "自分" if root["is_own"] is True else ("他者" if root["is_own"] is False else "不明")
    root_text = (root.get("text") or "")[:TEXT_PREVIEW_CHARS]
    print(f"根 @{root.get('username') or '?'}（{who}）: {root_text}")
    for m in result["messages"]:
        depth = m.get("depth")
        indent = "  " * depth if isinstance(depth, int) and depth > 0 else "  "
        who = "自分" if m["is_own"] is True else ("他者" if m["is_own"] is False else "不明")
        ts = m.get("timestamp") or ""
        hhmm = ts[11:16] if isinstance(ts, str) and len(ts) >= 16 else (ts or "?")
        text = (m.get("text") or "")[:TEXT_PREVIEW_CHARS]
        prefix = _replied_prefix(m.get("already_replied"))
        print(f"{indent}{prefix}{hhmm} @{m.get('username') or '?'}"
              f"（{m.get('author_key') or '?'}・{who}）: {text}")
    counts = result["counts"]
    print(f"  n={counts['messages']}  参加者={counts['participants']}  "
         f"自分={counts['own']}  truncated={counts['truncated']}")


def register(sub) -> None:
    """`thth thread <account> <post_id>` を親の subparsers にぶら下げる
    （`threads_read_cli.register()` と同じ型）。"""
    p = sub.add_parser(
        "thread",
        help="投稿の枝をその場で読む。保存しない（設計「自分の泉」§2.1・T1-2）")
    p.add_argument("account")
    p.add_argument("post_id")
    p.add_argument("--since", default=None, help="この時刻以降（ISO）だけ")
    p.add_argument("--max-messages", dest="max_messages", type=int,
                   default=DEFAULT_MAX_MESSAGES,
                   help=f"読む上限（既定 {DEFAULT_MAX_MESSAGES}・上限 {MAX_MESSAGES_LIMIT}）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_thread)


def cmd_thread(args) -> int:
    """`thth thread <account> <post_id> [--since ISO] [--max-messages N] [--json]`。"""
    as_json = bool(getattr(args, "json", False))
    account_cfg = None
    media = None
    try:
        account_cfg = accounts_mod.load_account(args.account)
        media = account_cfg.get("media")
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        result = answer(args.account, args.post_id, since=args.since,
                        max_messages=args.max_messages)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ThreadReadError as e:
        print(str(e), file=sys.stderr)
        return 2
    except adapter_base.PermissionMissing as e:
        message = (f"{e.permission} がトークンに乗っていません。"
                  f"`thth auth {args.account}` をやり直してください"
                  + (f"（{e.detail}）" if e.detail else ""))
        _record_run(args.account, media=media, post_id=args.post_id, messages=None,
                   truncated=False, status="error", error=message)
        if as_json:
            print(json.dumps({"error": message, "permission": e.permission,
                              "account": args.account, "granted": False},
                             ensure_ascii=False, indent=2))
        else:
            print(message, file=sys.stderr)
        return 1
    except adapter_base.AdapterError as e:
        message = f"{args.account}: {redact_mod.redact(str(e))}"
        _record_run(args.account, media=media, post_id=args.post_id, messages=None,
                   truncated=False, status="error", error=message)
        if as_json:
            print(json.dumps({"error": message, "account": args.account},
                             ensure_ascii=False, indent=2))
        else:
            print(message, file=sys.stderr)
        return 1

    _record_run(args.account, media=media, post_id=args.post_id,
               messages=result["counts"]["messages"],
               truncated=result["counts"]["truncated"], status="ok", error=None)

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _render_human(result)
    return 0
