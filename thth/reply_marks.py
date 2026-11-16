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

ほかに、未回答の数え方とは別の印を 1 つ持つ（段 12 の 3）:

- `fact_challenge`（`--fact-challenge`）: 読者が事実を指摘した返信。印の付いた返信が付いた自分の投稿が
  配信の台帳（`sent/`）に控えた `seeds`（台帳の記録の番号）を、`thth register health`・`verify`・`fix` が
  「確かめ直しの候補」として名指しする（`thth/register/posts.py`）。器は返信の本文を読まない（印は窓口が付ける）。
  **未回答から外さない**: ほかの 3 つは「返さなくてよい」印だが、事実の指摘は答えるべき返信なので、数え方を今と変えない。
  置き場は同じ `marks.json` の `facts`（`marks` とは別の欄・同じ返信に両方の印を持てる）。

印は `state/<口座>/reply_marks/marks.json`（0600・0700 の置き場・原子的な書き込み）。返信の本文・
相手の名前は持たない（返信の id・印・誰が・いつ・理由の 1 行だけ）。`thth unanswered` と、それを読む
observe・board・summary・申し送りは、印の付いた返信を未回答に数えない。印の数（`n_marked`・`marked`）を
別に出すのは `thth unanswered` だけ。
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
# 段 12 の 3: 事実の指摘の印。未回答の数え方には入れない（`marks` でなく `facts` に置く・頭の注）。
FACT_CHALLENGE = "fact_challenge"
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


def load_facts(account) -> tuple[dict, bool]:
    """事実の指摘の印 `({reply_id: {"post_id", "by", "at", "via", ...}}, 読めたか)`（段 12 の 3・読むだけ）。"""
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
    facts = value.get("facts", {}) if isinstance(value, dict) else None
    if not isinstance(facts, dict):
        return {}, False
    out = {rid: row for rid, row in facts.items()
           if id_is_usable(rid) and isinstance(row, dict) and row.get("mark") == FACT_CHALLENGE}
    return out, len(out) == len(facts)


class MarksUnreadable(ValueError):
    """印の置き場（`marks.json`）が読めない形（JSON でない・頭が dict でない・`marks`／`facts` が dict でない）。

    黙って読み替えて書き直さない（監査 5.24.0 の軽 5: `facts` が list のとき dict に読み替えていた）。置き場は
    書き換えず、`reply_marks_unreadable` で断る。"""


def _existing_fields(raw) -> dict:
    """書く前に読み直した置き場を `{"marks": …, "facts": …}` に。読めない形なら `MarksUnreadable`（書き換えない）。"""
    if raw is None:
        return {"marks": {}, "facts": {}}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise MarksUnreadable("not_json") from exc
    if not isinstance(value, dict):
        raise MarksUnreadable("not_object")
    fields = {}
    for key in ("marks", "facts"):
        part = value.get(key)
        if part is None:
            part = {}
        if not isinstance(part, dict):
            raise MarksUnreadable(f"{key}_not_object")
        fields[key] = dict(part)
    return fields


def _write(account, change, *, field="marks") -> dict:
    """ロックの中で読み直し、`change(印)` を当てて書く。戻りは書いたあとの印。

    `field` は `marks`（未回答から外す印）か `facts`（事実の指摘・段 12 の 3）。もう片方の欄はそのまま残す。
    `field=None` なら `change({"marks": …, "facts": …})` で両方を 1 回の書き込みで変える（2 つの印を一緒に付けるとき）。"""
    folder = _directory(account)
    with server_files.directory(folder, create=True, private=True) as fd:
        with server_files.lock_at(fd, LOCK_FILE):
            try:
                raw = server_files.read_at(fd, MARKS_FILE, private=True, maximum=4 * 1024 * 1024)
            except FileNotFoundError:
                raw = None
            fields = _existing_fields(raw)
            change(fields if field is None else fields[field])
            body = {"schema": SCHEMA, "marks": fields["marks"]}
            if fields["facts"]:
                body["facts"] = fields["facts"]
            server_files.replace_at(fd, MARKS_FILE, server_files.encode(body), expected=raw, private=True)
    return fields if field is None else fields[field]


def mark(account, reply_id, kind, *, by, answered_by=None, reason=None, via="cli", now=None) -> dict:
    """印を付ける（同じ返信に付け直すと上書き）。戻りはその返信の印。"""
    row = _mark_row(reply_id, kind, by=by, answered_by=answered_by, reason=reason, via=via, now=now)
    _write(account, lambda marks: marks.__setitem__(reply_id, row))
    return row


def _mark_row(reply_id, kind, *, by, answered_by=None, reason=None, via="cli", now=None) -> dict:
    if kind not in KINDS:
        raise ValueError("invalid_mark")
    if not id_is_usable(reply_id):
        raise ValueError("reply_id_invalid")
    row = {"mark": kind, "by": str(by)[:80], "at": jst.iso(now), "via": via}
    if answered_by:
        row["answered_by"] = answered_by
    if isinstance(reason, str) and reason.strip():
        row["reason"] = " ".join(reason.split())[:MAX_REASON]
    return row


def unmark(account, reply_id, *, field="marks") -> dict | None:
    """印を外す。外した印（無ければ None）。`field="facts"` は事実の指摘の印だけを外す。"""
    found = {}

    def change(marks):
        found["row"] = marks.pop(reply_id, None)
    _write(account, change, field=field)
    return found.get("row")


def mark_fact(account, reply_id, *, by, post_id=None, reason=None, via="cli", now=None) -> dict:
    """事実の指摘の印を付ける（段 12 の 3）。`post_id` は返信が付いた自分の投稿（返信の台帳から引いた物）。
    返信の本文は持たない。未回答の数え方は変えない（`marks` に入れない）。"""
    row = _fact_row(reply_id, by=by, post_id=post_id, reason=reason, via=via, now=now)
    _write(account, lambda facts: facts.__setitem__(reply_id, row), field="facts")
    return row


def _fact_row(reply_id, *, by, post_id=None, reason=None, via="cli", now=None) -> dict:
    if not id_is_usable(reply_id) or (post_id is not None and not isinstance(post_id, str)):
        raise ValueError("reply_id_invalid")
    row = {"mark": FACT_CHALLENGE, "by": str(by)[:80], "at": jst.iso(now), "via": via}
    if post_id:
        row["post_id"] = post_id[:512]
    if isinstance(reason, str) and reason.strip():
        row["reason"] = " ".join(reason.split())[:MAX_REASON]
    return row


def mark_with_fact(account, reply_id, kind, *, by, answered_by=None, post_id=None, reason=None, via="cli",
                   now=None) -> tuple:
    """未回答から外す印と事実の指摘の印を **1 回の書き込み**で付ける（片方だけ残る半端を作らない・検収 段 12 の軽 4）。"""
    row = _mark_row(reply_id, kind, by=by, answered_by=answered_by, reason=reason, via=via, now=now)
    fact = _fact_row(reply_id, by=by, post_id=post_id, reason=reason, via=via, now=now)

    def change(fields):
        fields["marks"][reply_id] = row
        fields["facts"][reply_id] = fact
    _write(account, change, field=None)
    return row, fact


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
    """この口座の投稿への返信の台帳にその返信があるか（読めなければ None）。"""
    known, _post = _reply_post(account, reply_id)
    return known


def _reply_post(account, reply_id) -> tuple:
    """`(台帳にあるか, 返信が付いた自分の投稿の post_id)`。読めなければ `(None, None)`。

    返信の置き場は同じ repo のほかの口座と共有なので、この口座の投稿の返信だけを読む
    （`owned_only`・検収 5.23.0 軽3: 前は口座 two の返信に口座 one で印が付いた）。"""
    from . import replies
    from .adapters import base
    try:
        cfg = accounts.load_account(account)
        data = replies.load(account, owned_only=True)
    except (accounts.AccountError, OSError, ValueError, TypeError, AttributeError):
        return None, None
    for row in data.get("replies") or []:
        if row.get("account") not in (None, account):
            continue
        message = base.normalize_message(row, medium=cfg["media"])
        if message.get("message_id") == reply_id:
            post = row.get("post_id")
            return True, post if isinstance(post, str) and post else None
    return False, None


def cmd(args) -> int:
    from . import refusal
    kind = ("answered_in_branch" if args.answered_by else "no_reply_needed" if args.no_reply_needed
            else "unreplyable" if args.unreplyable else None)

    def refuse(code, message, rc=2):
        return refusal.emit(args, code, line=f"{code}: {message}",
                            payload={"ok": False, "error": code, "message": message}, rc=rc,
                            dump=lambda value: print(json.dumps(value, ensure_ascii=False, indent=2)))

    def refuse_unreadable():
        # 前向きな断り（docs/原則_前向きな断り.md）: なぜ・通る直し方・より良くする一手。置き場は書き換えていない。
        where = os.path.join(accounts.state_dir_for(args.account), "reply_marks", MARKS_FILE)
        return refuse("reply_marks_unreadable",
                      f"印の置き場 {where} が読めない形です（JSON の頭と marks・facts は "
                      "{返信の id: 印} の形の dict）。黙って直すと前の印が消えるので、何も書き換えていません。"
                      "置き場を開いて形を直すか、別の名前に移してから付け直してください"
                      "（印は THTH に書かせると形がそろいます）")
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
    fact = bool(getattr(args, "fact_challenge", False))
    if args.undo and fact and kind is None:
        try:
            removed = unmark(args.account, args.reply_id, field="facts")
        except MarksUnreadable:
            return refuse_unreadable()
        except (OSError, ValueError):
            return refuse("reply_marks_unwritable", "印の置き場に書けませんでした。thth doctor で置き場を確かめてください")
        out = {"ok": True, "account": args.account, "reply_id": args.reply_id, "removed": removed}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif removed:
            print(f"{args.account}: {args.reply_id} の事実の指摘の印を外しました（台帳の確かめ直しの候補に出しません）")
        else:
            print(f"{args.account}: {args.reply_id} に事実の指摘の印はありません（何も変えていません）")
        return 0
    if args.undo:
        if kind is not None:
            return refuse("reply_mark_kind_conflict",
                          "--undo は印の種類と一緒に渡せません。外すだけなら --undo だけを付けてください")
        try:
            removed = unmark(args.account, args.reply_id)
        except MarksUnreadable:
            return refuse_unreadable()
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
    if kind is None and not fact:
        return refuse("reply_mark_kind_required",
                      "印の種類を 1 つ付けてください: --answered-by <自分の post_id>・--no-reply-needed・--unreplyable"
                      "（事実の指摘なら --fact-challenge）")
    known, replied_post = _reply_post(args.account, args.reply_id)
    if known is False:
        return refuse("reply_not_in_ledger",
                      f"{args.reply_id} は {args.account} の返信の台帳にありません。"
                      f"thth unanswered {args.account} の reply_id を写してください")
    row, fact_row = {}, None
    try:
        if kind is not None and fact:
            row, fact_row = mark_with_fact(args.account, args.reply_id, kind, by=by, answered_by=args.answered_by,
                                           post_id=replied_post, reason=args.reason)
        elif kind is not None:
            row = mark(args.account, args.reply_id, kind, by=by, answered_by=args.answered_by, reason=args.reason)
        else:
            fact_row = mark_fact(args.account, args.reply_id, by=by, post_id=replied_post, reason=args.reason)
    except MarksUnreadable:
        return refuse_unreadable()
    except (OSError, ValueError):
        return refuse("reply_marks_unwritable", "印の置き場に書けませんでした。thth doctor で置き場を確かめてください")
    out = {"ok": True, "account": args.account, "reply_id": args.reply_id, **row}
    if fact_row is not None:
        out["fact_challenge"] = fact_row
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if kind is not None:
            tail = f"（{args.answered_by} で答えた）" if args.answered_by else ""
            print(f"{args.account}: {args.reply_id} に印を付けました: {KIND_JA[kind]}{tail}。未回答には数えません"
                  f"（外すなら thth reply-mark {args.account} {args.reply_id} --undo --by <名前>）")
        if fact_row is not None:
            where = f"投稿 {replied_post}" if replied_post else "返信が付いた投稿（台帳から引けませんでした）"
            print(f"{args.account}: {args.reply_id} に事実の指摘の印を付けました（{where}）。未回答の数え方は変えません。"
                  f"この投稿が控えた台帳の記録を thth register health が確かめ直しの候補に出します"
                  f"（外すなら thth reply-mark {args.account} {args.reply_id} --undo --fact-challenge --by <名前>）")
        if known is None:
            print("  （返信の台帳を読めなかったので、その返信があるかは確かめていません）", file=sys.stderr)
    return 0


def register(sub):
    parser = sub.add_parser(
        "reply-mark",
        help="返信に印を付けて未回答から外す（枝の中で答えた・返さない・もう返せない。--undo で外す）。"
             "--fact-challenge は事実の指摘の印（未回答から外さない・台帳の確かめ直しの候補にする）")
    parser.add_argument("account")
    parser.add_argument("reply_id", help="thth unanswered の reply_id")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--answered-by", default=None, metavar="POST_ID",
                       help="同じ枝の中で答えた自分の投稿の post_id")
    group.add_argument("--no-reply-needed", action="store_true", help="返さないのが正しい（読者どうしの会話など）")
    group.add_argument("--unreplyable", action="store_true", help="もう返せない（消された・見えない）")
    parser.add_argument("--fact-challenge", action="store_true",
                        help="読者が事実を指摘した返信（未回答から外さない。投稿が控えた台帳の記録を確かめ直しの候補にする）")
    parser.add_argument("--reason", default=None, help="理由の 1 行（任意）")
    parser.add_argument("--by", default=None, help="誰が決めたか（環境変数 THTH_ACTOR でも）")
    parser.add_argument("--undo", action="store_true", help="印を外す")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd)
