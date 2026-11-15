"""返信に付ける印（未回答から外す・5.23.0）。

報告 r20261021-c6f28e99（関東 Threads）: 同じ枝の中でまとめて答えた返信や、読者どうしの掛け合いが、
reply_to で 1 件ずつ返していないので未回答に残り続け、窓が動くたびに数が変わって起こされた。
報告 r20261104-366caff8（関東 Threads）: 消された・見えないコメント（返信を送ると code 24・
subcode 4279009）が、毎回未回答に数えられて返し忘れに見えた。

印は 3 つ:

- `answered_in_branch`: 同じ枝の中の自分の投稿で答えた（`answered_by` に自分の post_id）。
- `no_reply_needed`: 返さないのが正しい（読者どうしの会話など）。
- `unreplyable`: もう返せない（媒体が返信先を断った）。Threads の code 24・subcode 4279009 で
  返信が断られたときは、THTH が自分で付ける（`note_publish_refusal`）。

印は `state/<口座>/reply_marks/marks.json`（0600・0700 の置き場・原子的な書き込み）。返信の本文・
相手の名前は持たない（返信の id・印・誰が・いつ・理由の 1 行だけ）。`thth unanswered` と、それを読む
observe・board・summary・申し送りは、印の付いた返信を未回答に数えず、`marked` に別に数える。
印は `thth reply-mark <口座> <reply_id> --undo` で外せる（記録の意味を変えない・何度でも付け直せる）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import accounts, jst, server_files

MARKS_FILE = "marks.json"
LOCK_FILE = "marks.lock"
KINDS = ("answered_in_branch", "no_reply_needed", "unreplyable")
KIND_JA = {"answered_in_branch": "枝の中で答えた", "no_reply_needed": "返さない", "unreplyable": "もう返せない"}
SCHEMA = "thth.reply_marks/1"
MAX_REASON = 200
# 返信の id（媒体の数字・英数と `_-.:` だけ）。
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
# Threads が「返信先に送れない」と答えた形（関東 11/04・container_http_400・is_transient=false）。
THREADS_UNREPLYABLE = ((24, 4279009),)


def _directory(account) -> Path:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return Path(accounts.state_dir_for(account)) / "reply_marks"


def id_is_usable(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def load(account) -> tuple[dict, bool]:
    """`({reply_id: 印}, 読めたか)`。置き場が無ければ `({}, True)`（読むだけ・何も作らない）。"""
    folder = _directory(account)
    if not os.path.lexists(folder):
        return {}, True
    try:
        with server_files.directory(folder, private=True) as fd:
            raw = server_files.read_at(fd, MARKS_FILE, private=True, maximum=4 * 1024 * 1024)
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError):
        return {}, False
    try:
        value = json.loads(raw)
    except ValueError:
        return {}, False
    marks = value.get("marks") if isinstance(value, dict) else None
    if not isinstance(marks, dict):
        return {}, False
    out = {rid: row for rid, row in marks.items()
           if id_is_usable(rid) and isinstance(row, dict) and row.get("mark") in KINDS}
    return out, len(out) == len(marks)


def _write(account, change) -> dict:
    """ロックの中で読み直し、`change(marks)` を当てて書く。戻りは書いたあとの印。"""
    folder = _directory(account)
    with server_files.directory(folder, create=True, private=True) as fd:
        with server_files.lock_at(fd, LOCK_FILE):
            try:
                raw = server_files.read_at(fd, MARKS_FILE, private=True, maximum=4 * 1024 * 1024)
            except FileNotFoundError:
                raw = None
            marks = {}
            if raw is not None:
                value = json.loads(raw)
                marks = dict(value.get("marks") or {}) if isinstance(value, dict) else {}
            change(marks)
            server_files.replace_at(fd, MARKS_FILE, server_files.encode({"schema": SCHEMA, "marks": marks}),
                                    expected=raw, private=True)
    return marks


def mark(account, reply_id, kind, *, by, answered_by=None, reason=None, via="cli", now=None) -> dict:
    """印を付ける（同じ返信に付け直すと上書き）。戻りはその返信の印。"""
    if kind not in KINDS:
        raise ValueError("invalid_mark")
    if not id_is_usable(reply_id):
        raise ValueError("reply_id_invalid")
    row = {"mark": kind, "by": str(by)[:80], "at": jst.iso(now), "via": via}
    if answered_by:
        row["answered_by"] = answered_by
    if isinstance(reason, str) and reason.strip():
        row["reason"] = " ".join(reason.split())[:MAX_REASON]
    _write(account, lambda marks: marks.__setitem__(reply_id, row))
    return row


def unmark(account, reply_id) -> dict | None:
    """印を外す。外した印（無ければ None）。"""
    found = {}

    def change(marks):
        found["row"] = marks.pop(reply_id, None)
    _write(account, change)
    return found.get("row")


_CODES_IN_TEXT = re.compile(r"_code_([0-9]{1,9})_subcode_([0-9]{1,9})\b")


def refusal_pair(detail, error=None):
    """Graph の `(code, error_subcode)`。`api_diagnostic` が無ければ失敗の文の静的な尻尾
    （添付の道の `container_http_400_code_24_subcode_4279009`）から引く。"""
    if isinstance(detail, dict) and type(detail.get("code")) is int:
        return detail.get("code"), detail.get("error_subcode")
    found = _CODES_IN_TEXT.search(error) if isinstance(error, str) else None
    return (int(found.group(1)), int(found.group(2))) if found else (None, None)


def note_publish_refusal(account, cfg, reply_to, detail, *, error=None, log=None) -> bool:
    """返信の公開が断られたあとに呼ぶ。Threads が「返信先に送れない」と答えたら `unreplyable` を付ける。

    印を置けなくても公開の結果は変えない（言うだけ）。付けたら True。"""
    if (cfg or {}).get("media") != "threads" or not id_is_usable(reply_to):
        return False
    pair = refusal_pair(detail, error)
    if pair not in THREADS_UNREPLYABLE:
        return False
    try:
        mark(account, reply_to, "unreplyable", by="thth", via="publish",
             reason=f"Threads が返信先を断った（code {pair[0]}・subcode {pair[1]}）")
    except (OSError, ValueError) as exc:
        if log is not None:
            log(f"返信先に「もう返せない」の印を置けませんでした（{type(exc).__name__}）。"
                f"thth reply-mark {account} {reply_to} --unreplyable --by <名前> で付けられます")
        return False
    if log is not None:
        log(f"返信先 {reply_to} は Threads が受け付けません（消されたか見えない）。未回答から外しました"
            f"（戻すなら thth reply-mark {account} {reply_to} --undo --by <名前>）")
    return True


# --------------------------------------------------------------------------
# `thth reply-mark`
# --------------------------------------------------------------------------

def _known_reply(account, reply_id) -> bool | None:
    """返信の台帳にその返信があるか（読めなければ None）。"""
    from . import replies
    from .adapters import base
    try:
        cfg = accounts.load_account(account)
        data = replies.load(account)
    except (accounts.AccountError, OSError, ValueError, TypeError, AttributeError):
        return None
    for row in data.get("replies") or []:
        message = base.normalize_message(row, medium=cfg["media"])
        if message.get("message_id") == reply_id:
            return True
    return False


def cmd(args) -> int:
    from . import refusal
    kind = ("answered_in_branch" if args.answered_by else "no_reply_needed" if args.no_reply_needed
            else "unreplyable" if args.unreplyable else None)

    def refuse(code, message, rc=2):
        return refusal.emit(args, code, line=f"{code}: {message}",
                            payload={"ok": False, "error": code, "message": message}, rc=rc,
                            dump=lambda value: print(json.dumps(value, ensure_ascii=False, indent=2)))
    try:
        accounts.validate_name(args.account)
        accounts.load_account(args.account)
    except accounts.AccountError as exc:
        return refuse("account_unreadable", str(exc))
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return refuse("by_required", "--by <名前> を付けてください（誰が印を付けたかを記録します）", rc=1)
    if not id_is_usable(args.reply_id) or (args.answered_by and not id_is_usable(args.answered_by)):
        return refuse("post_id_invalid", "返信の id（thth unanswered の reply_id）の数字を確かめてください")
    if args.undo:
        if kind is not None:
            return refuse("reply_mark_kind_conflict",
                          "--undo は印の種類と一緒に渡せません。外すだけなら --undo だけを付けてください")
        try:
            removed = unmark(args.account, args.reply_id)
        except (OSError, ValueError):
            return refuse("reply_marks_unwritable", "印の置き場に書けませんでした。thth doctor で置き場を確かめてください")
        out = {"ok": True, "account": args.account, "reply_id": args.reply_id, "removed": removed}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif removed:
            print(f"{args.account}: {args.reply_id} の印（{KIND_JA[removed['mark']]}）を外しました。未回答にまた数えます")
        else:
            print(f"{args.account}: {args.reply_id} に印はありません（何も変えていません）")
        return 0
    if kind is None:
        return refuse("reply_mark_kind_required",
                      "印の種類を 1 つ付けてください: --answered-by <自分の post_id>・--no-reply-needed・--unreplyable")
    known = _known_reply(args.account, args.reply_id)
    if known is False:
        return refuse("reply_not_in_ledger",
                      f"{args.reply_id} は {args.account} の返信の台帳にありません。"
                      f"thth unanswered {args.account} の reply_id を写してください")
    try:
        row = mark(args.account, args.reply_id, kind, by=by, answered_by=args.answered_by, reason=args.reason)
    except (OSError, ValueError):
        return refuse("reply_marks_unwritable", "印の置き場に書けませんでした。thth doctor で置き場を確かめてください")
    out = {"ok": True, "account": args.account, "reply_id": args.reply_id, **row}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        tail = f"（{args.answered_by} で答えた）" if args.answered_by else ""
        print(f"{args.account}: {args.reply_id} に印を付けました: {KIND_JA[kind]}{tail}。未回答には数えません"
              f"（外すなら thth reply-mark {args.account} {args.reply_id} --undo --by <名前>）")
        if known is None:
            print("  （返信の台帳を読めなかったので、その返信があるかは確かめていません）", file=sys.stderr)
    return 0


def register(sub):
    parser = sub.add_parser(
        "reply-mark",
        help="返信に印を付けて未回答から外す（枝の中で答えた・返さない・もう返せない。--undo で外す）")
    parser.add_argument("account")
    parser.add_argument("reply_id", help="thth unanswered の reply_id")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--answered-by", default=None, metavar="POST_ID",
                       help="同じ枝の中で答えた自分の投稿の post_id")
    group.add_argument("--no-reply-needed", action="store_true", help="返さないのが正しい（読者どうしの会話など）")
    group.add_argument("--unreplyable", action="store_true", help="もう返せない（消された・見えない）")
    parser.add_argument("--reason", default=None, help="理由の 1 行（任意）")
    parser.add_argument("--by", default=None, help="誰が決めたか（環境変数 THTH_ACTOR でも）")
    parser.add_argument("--undo", action="store_true", help="印を外す")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd)
