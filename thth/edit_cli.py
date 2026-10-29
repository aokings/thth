"""`thth edit` と `thth reply hide|unhide|delete`（Meta 計画 段 1・10/29）。

**`build_parser()` には 1 行しか足さない**（`edit_cli.register(sub)`）。口はこちら側に閉じる
（`retract_cli`・`reaction_cli` と同じ作り）。媒体名では分けず、adapter の capability で分ける:

- `edit_post` — `thth edit <account> <post_id> --text …`。**THTH で出した投稿だけ**
  （`sent/<post_id>.json` が要る・取り下げ済みは断る）。二段確認: 一段目は直す前と直したあとの
  本文と digest を見せて何もしない（rc=1）、二段目 `--confirm <digest>` で 1 回だけ直す。
  digest は直したあとの本文（＋直す前の本文）から作る（`approval.compute_edit_digest`）。成功したら
  `sent/` の `text`・`body_hash` を直し、前の本文を `edits[]` に残す（`sent.mark_post_edit`）。
- `hide_reply` — `thth reply hide|unhide <account> <comment_id> --by …` は 1 段（隠すは戻せる）。
  `thth reply delete … --reason … --by …` は**取り下げと同じ二段確認**（`compute_retract_digest`
  に印を足した digest・投稿の取り下げの digest を流用できない）。記録は
  `state/<account>/moderation-YYYY-MM.ndjson` に 1 行（コメントの本文は残さない）。

fail-closed（`retract` と同じ）:
  - 台帳に `production: true` が無ければ媒体を呼ばない（読むだけの一段目は通す）。
  - 権限が `.token` に乗っていなければ rc=2（`thth auth` のやり直し）。
  - 安全装置の止めの印（`guard/stopped.json`）があれば呼ばない。口座の停止（`leave_gate`）は
    adapter の口そのものが `leave_gate.bind` で包まれていて断る。
  - 結果が分からない（時間切れ・5xx）ときは**済んだと言わない**。記録に `result: unknown` を残し、
    媒体の画面で確かめるよう言う（自動で投げ直さない）。
  - 手元の MCP には出さない（`retract` と同じ線）。

**媒体の直す・隠す・消すの口を呼ぶのはこのモジュールだけ**（`tests/test_facebook_stage1.py` が
source を見て縛る）。
"""
from __future__ import annotations

import json
import os
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import core
from . import jst
from . import lock as lock_mod
from . import queuefile
from . import redact as redact_mod
from . import sent as sent_mod
from .adapters import base as adapter_base

REPLY_ACTIONS = ("hide", "unhide", "delete")


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser(
        "edit", help="THTH で出した投稿の本文を直す（二段確認・Facebook）")
    p.add_argument("account")
    p.add_argument("post_id")
    p.add_argument("--text", default=None, help="直したあとの本文")
    p.add_argument("--text-file", dest="text_file", default=None,
                   help="直したあとの本文のファイル（- なら標準入力）")
    p.add_argument("--reason", default=None, help="なぜ直すか（記録に残す・任意）")
    p.add_argument("--by", default=None, help="誰が直したか（記録に残す・必須）")
    p.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何もしない")
    p.add_argument("--json", action="store_true")
    p.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
    p.set_defaults(func=cmd_edit)

    r = sub.add_parser(
        "reply", help="コメントを隠す・戻す・消す（hide|unhide は 1 段・delete は二段確認・Facebook）")
    r.add_argument("action", choices=REPLY_ACTIONS)
    r.add_argument("account")
    r.add_argument("comment_id")
    r.add_argument("--reason", default=None, help="なぜか（記録に残す・delete は必須）")
    r.add_argument("--by", default=None, help="誰がしたか（記録に残す・必須）")
    r.add_argument("--confirm", default=None, help="delete の一段目が表示した digest")
    r.add_argument("--json", action="store_true")
    r.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
    r.set_defaults(func=cmd_reply)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(args, code: int, message: str, **extra) -> int:
    if getattr(args, "json", False):
        _print_json({"ok": False, "error": message, **extra})
    else:
        print(message, file=sys.stderr)
    return code


def _by(args) -> str:
    return (args.by or os.environ.get("THTH_ACTOR") or "").strip()


def _load(args, capability: str, word: str):
    """台帳・adapter のクラス・token。断りは `(None, rc, message)`。"""
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return None, 2, str(e)
    media = account_cfg.get("media")
    if capability not in adapters_mod.capabilities_for(media):
        return None, 2, f"{media}: この媒体の{word}は未対応です（capability {capability} がありません）"
    adapter_cls = adapters_mod.adapter_class(media)
    token = accounts_mod.load_token(account_cfg)
    return (account_cfg, adapter_cls, token), 0, ""


def _ready_to_write(args, account_cfg, adapter_cls, token, permissions):
    """媒体を呼ぶ前の fail-closed。断るなら `(rc, 人向けの 1 行)`、通すなら None。"""
    if not approval_mod.is_true(account_cfg.get("production")):
        return 1, (f"{args.account}: 台帳に production: true が無いので媒体を呼びません"
                   "（dry-run と同じ fail-closed）")
    if not adapter_cls.has_token(token):
        return 2, f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）"
    missing = adapter_cls.missing_permissions(token, list(permissions))
    if missing:
        return 2, adapter_base.not_granted_message(missing[0]).replace("<account>", args.account)
    return None


def _guard_stopped(account_name) -> str | None:
    """安全装置の止めの印。止まっていれば人向けの 1 行。"""
    from . import guard as guard_mod
    if not guard_mod.stop_place_usable(account_name):
        return None
    halted = guard_mod.stopped(account_name)
    if halted is None:
        return None
    return (f"{account_name} は安全装置で止まっています（{halted.get('reason') or '理由不明'}）。"
            "止めたまま媒体は呼びません（thth admin で止めた理由を確かめてから再開してください）")


def _read_text(args) -> str | None:
    if args.text is not None and args.text_file is not None:
        raise ValueError("--text と --text-file はどちらか 1 つです")
    if args.text is not None:
        return args.text
    if args.text_file is None:
        return None
    if args.text_file == "-":
        return sys.stdin.read()
    with open(args.text_file, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# thth edit
# --------------------------------------------------------------------------

def _edit_record(state_dir: str, post_id: str):
    """直せる記録か。`(row, None)` か `(None, 断りの 1 行)`。"""
    row = sent_mod.read(state_dir, post_id)
    if row is None or row.get("post_id") != post_id:
        return None, (f"post_id {post_id} の記録が sent/ にありません。**THTH で出していない投稿は"
                      "直しません**（Meta もそのアプリで作った投稿しか直せません）")
    if row.get("kind") is not None:
        return None, f"post_id {post_id} は SNS の投稿の記録ではありません（{row.get('kind')}）"
    if row.get("retracted_at"):
        return None, f"post_id {post_id} は取り下げ済みです（{row.get('retracted_at')}）"
    return row, None


def cmd_edit(args) -> int:
    try:
        return _cmd_edit(args)
    except adapter_base.PermissionMissing as e:
        return _fail(args, 2, str(e).replace("<account>", args.account))


def _cmd_edit(args) -> int:
    post_id = (args.post_id or "").strip()
    by = _by(args)
    reason = (args.reason or "").strip() or None
    if not by:
        return _fail(args, 1, "--by を付けてください（誰が直したかを記録します）。"
                              "環境変数 THTH_ACTOR でも指定できます")
    try:
        text = _read_text(args)
    except (ValueError, OSError) as e:
        return _fail(args, 1, str(e))
    if text is None or not text.strip():
        return _fail(args, 1, "直したあとの本文を --text か --text-file で渡してください")
    text = text.strip("\n")
    bad = queuefile.find_control_char(text)
    if bad:
        return _fail(args, 1, queuefile.control_char_message(*bad))
    loaded, rc, message = _load(args, "edit_post", "投稿の編集")
    if loaded is None:
        return _fail(args, rc, message)
    account_cfg, adapter_cls, token = loaded
    media = account_cfg.get("media")
    limit = queuefile.limit_for(media, account_cfg)
    n = queuefile.count_for(media, text)
    if n > limit:
        return _fail(args, 1, f"本文が上限を超えています（{n} > {limit}）。"
                              + queuefile.limit_note(media, account_cfg))
    state_dir = accounts_mod.state_dir_for(args.account)
    row, refusal = _edit_record(state_dir, post_id)
    if refusal:
        return _fail(args, 1, refusal)
    previous = row.get("text") or ""
    if previous.strip() == text.strip():
        return _fail(args, 1, "直したあとの本文が今の記録と同じです（直しません）")
    digest = approval_mod.compute_edit_digest(post_id=post_id, account=args.account,
                                              text=text, previous_text=previous)
    unknown = [e for e in row.get("edits") or [] if isinstance(e, dict) and e.get("result") == "unknown"]

    if not args.confirm:
        # ---- 一段目: 見せるだけ。媒体は呼ばない ----
        if args.json:
            _print_json({"ok": False, "edited": False, "stage": 1, "account": args.account,
                         "post_id": post_id, "url": row.get("url"), "permalink": row.get("url"),
                         "previous_text": previous, "text": text, "by": by, "reason": reason,
                         "digest": digest, "unknown_edits": len(unknown)})
            return 1
        print(f"直しません（確認の一段目です）: {args.account} post_id {post_id}")
        print(f"  URL : {row.get('url') or '（記録にありません）'}")
        print(f"  by  : {by}" + (f"・理由: {reason}" if reason else ""))
        if unknown:
            print(f"  ⚠ 前の直しで結果が分からないものが {len(unknown)} 件あります（媒体の画面で確かめてください）")
        print("--- いまの本文（記録） ---")
        print(previous)
        print("--- 直したあとの本文 ---")
        print(text)
        print("--- ここまで ---")
        if not approval_mod.is_true(account_cfg.get("production")):
            print("  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません")
        print(f"digest: {digest}")
        print(f"直すなら: thth edit {args.account} {post_id} --text-file <同じ本文のファイル> "
              f"--by {json.dumps(by, ensure_ascii=False)} --confirm {digest}")
        return 1

    # ---- 二段目 ----
    if args.confirm != digest:
        return _fail(args, 1, f"digest が一致しないので直しません（表示したものと中身が違います）。"
                              f"いまの digest は {digest} です。もう一度 thth edit からやり直してください")
    refusal = _ready_to_write(args, account_cfg, adapter_cls, token, [adapter_cls.EDIT_PERMISSION])
    if refusal:
        return _fail(args, *refusal)
    try:
        with core._account_locks(args.account, account_cfg, state_dir, wait=args.wait):
            # ロックの中で読み直す（一段目のあとに別の直し・取り下げが入っていないか）。
            row, refusal = _edit_record(state_dir, post_id)
            if refusal:
                return _fail(args, 1, refusal)
            again = approval_mod.compute_edit_digest(post_id=post_id, account=args.account,
                                                     text=text, previous_text=row.get("text") or "")
            if again != digest:
                return _fail(args, 1, "一段目のあとに記録が変わりました（別の直しが入った）。"
                                      "もう一度 thth edit からやり直してください")
            stopped = _guard_stopped(args.account)
            if stopped:
                return _fail(args, 1, stopped, guard="stopped")
            adapter = adapters_mod.make_adapter(account_cfg, token)
            edited_at = jst.iso()
            try:
                adapter.edit_post(post_id, text)          # ← 媒体を呼ぶのはここ 1 回だけ
            except adapter_base.PermissionMissing:
                raise
            except accounts_mod.AccountStopped as e:
                return _fail(args, 1, f"{args.account} は止まっています（{e}）。直していません")
            except adapter_base.AdapterError as e:
                message = redact_mod.redact(str(e))
                if getattr(e, "failure", None) == "ambiguous":
                    path = sent_mod.mark_post_edit(state_dir, post_id, result="unknown",
                                                   edited_at=edited_at, by=by, text=text,
                                                   reason=reason, digest=digest)
                    return _fail(args, 1, "直したかどうか分かりません（記録に result: unknown を足しました・"
                                          f"{path}）。媒体の画面で確かめてください: {message}",
                                 result="unknown")
                return _fail(args, 1, "直せませんでした（記録は変えていません）: " + message)
            path = sent_mod.mark_post_edit(state_dir, post_id, result="confirmed", edited_at=edited_at,
                                           by=by, text=text, reason=reason, digest=digest)
    except lock_mod.LockBusy:
        return _fail(args, 1, f"{args.account} は既に実行中です（ロック取得失敗）。--wait <秒> で空くのを待てます")

    payload = {"ok": True, "edited": True, "account": args.account, "post_id": post_id,
               "url": row.get("url"), "permalink": row.get("url"), "edited_at": edited_at,
               "by": by, "record": path}
    if args.json:
        _print_json(payload)
    else:
        print(f"直しました: {args.account} post_id {post_id}（{edited_at}・{by}）")
        print(f"  記録の本文を直し、前の本文を edits[] に残しました: {path}")
    return 0


# --------------------------------------------------------------------------
# thth reply hide|unhide|delete
# --------------------------------------------------------------------------

def moderation_path(state_dir: str, when=None) -> str:
    when = when or jst.now_jst()
    return os.path.join(state_dir, f"moderation-{when.strftime('%Y-%m')}.ndjson")


def _append_moderation(state_dir: str, row: dict) -> str:
    path = moderation_path(state_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _reply_delete_digest(comment_id: str, account: str, reason: str) -> str:
    # 取り下げの digest に印を足す（投稿の取り下げの digest をコメントの削除に流用できない）。
    return approval_mod.compute_retract_digest(post_id=comment_id, account=account,
                                               reason=reason + "\x1freply_delete")


def cmd_reply(args) -> int:
    try:
        return _cmd_reply(args)
    except adapter_base.PermissionMissing as e:
        return _fail(args, 2, str(e).replace("<account>", args.account))


def _cmd_reply(args) -> int:
    comment_id = (args.comment_id or "").strip()
    by = _by(args)
    reason = (args.reason or "").strip()
    action = args.action
    if not by:
        return _fail(args, 1, "--by を付けてください（誰がしたかを記録します）。"
                              "環境変数 THTH_ACTOR でも指定できます")
    if action == "delete" and not reason:
        return _fail(args, 1, "--reason を付けてください（なぜ消すかを記録します）")
    if action != "delete" and args.confirm:
        return _fail(args, 1, "--confirm は delete だけです（hide・unhide は 1 段・戻せます）")
    loaded, rc, message = _load(args, "hide_reply", "コメントの非表示・削除")
    if loaded is None:
        return _fail(args, rc, message)
    account_cfg, adapter_cls, token = loaded
    permissions = list(getattr(adapter_cls, "MODERATE_PERMISSIONS", ()))
    if not adapter_cls.has_token(token):
        return _fail(args, 2, f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    missing = adapter_cls.missing_permissions(token, permissions)
    if missing:
        return _fail(args, 2, adapter_base.not_granted_message(missing[0]).replace("<account>", args.account))
    state_dir = accounts_mod.state_dir_for(args.account)
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        comment = adapter.fetch_reply(comment_id)        # 読むだけ（人に見せる・できるかを訊く）
    except adapter_base.PermissionMissing:
        raise
    except accounts_mod.AccountStopped as e:
        return _fail(args, 1, f"{args.account} は止まっています（{e}）")
    except Exception as e:   # noqa: BLE001
        return _fail(args, 1, "コメントを読めませんでした（何もしていません）: " + redact_mod.redact(str(e)))

    can = comment.get("can_remove") if action == "delete" else comment.get("can_hide")
    if can is False:
        word = "消せない" if action == "delete" else "隠せない"
        return _fail(args, 1, f"Meta がこのコメントは{word}と答えています（ページ自身のコメントなど）。何もしていません")
    if action in ("hide", "unhide") and comment.get("is_hidden") is (action == "hide"):
        state = "隠れています" if action == "hide" else "見えています"
        if args.json:
            _print_json({"ok": True, "changed": False, "action": action, "account": args.account,
                         "comment_id": comment_id})
        else:
            print(f"既に{state}（何もしていません）: {args.account} comment {comment_id}")
        return 0

    digest = _reply_delete_digest(comment_id, args.account, reason) if action == "delete" else None
    if action == "delete" and not args.confirm:
        if args.json:
            _print_json({"ok": False, "deleted": False, "stage": 1, "account": args.account,
                         "comment_id": comment_id, "text": comment.get("text"),
                         "by_page": comment.get("by_page"), "reason": reason, "by": by, "digest": digest})
            return 1
        print(f"消しません（確認の一段目です）: {args.account} comment {comment_id}")
        print(f"  書いた人: {'このページ' if comment.get('by_page') else (comment.get('username') or '（名前は返りません）')}")
        print(f"  理由: {reason}")
        print(f"  by  : {by}")
        print("--- 消すコメント ---")
        print(comment.get("text") or "")
        print("--- ここまで ---")
        print("  隠すだけなら戻せます: thth reply hide " + f"{args.account} {comment_id} --by {json.dumps(by, ensure_ascii=False)}")
        if not approval_mod.is_true(account_cfg.get("production")):
            print("  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません")
        print(f"digest: {digest}")
        print(f"消すなら: thth reply delete {args.account} {comment_id} --reason "
              f"{json.dumps(reason, ensure_ascii=False)} --by {json.dumps(by, ensure_ascii=False)} "
              f"--confirm {digest}")
        return 1
    if action == "delete" and args.confirm != digest:
        return _fail(args, 1, f"digest が一致しないので消しません（表示したものと中身が違います）。"
                              f"いまの digest は {digest} です。もう一度一段目からやり直してください")

    refusal = _ready_to_write(args, account_cfg, adapter_cls, token, permissions)
    if refusal:
        return _fail(args, *refusal)
    try:
        with core._account_locks(args.account, account_cfg, state_dir, wait=args.wait):
            if action == "delete":
                # 消すのは取り下げと同じ安全装置（止めの印・削除の数）を通す。
                from . import retract_cli
                refused = retract_cli._guard_refusal(args.account, account_cfg)
                if refused is not None:
                    return _fail(args, 1, refused[1], guard=refused[0])
            else:
                stopped = _guard_stopped(args.account)
                if stopped:
                    return _fail(args, 1, stopped, guard="stopped")
            at = jst.iso()
            record = {"at": at, "action": action, "comment_id": comment_id, "by": by,
                      "reason": reason or None, "by_page": comment.get("by_page"),
                      "author_key": comment.get("author_key"), "medium": account_cfg.get("media")}
            try:
                if action == "delete":
                    adapter.delete_reply(comment_id)    # ← 消すのはここ 1 回だけ
                else:
                    adapter.hide_reply(comment_id, hidden=(action == "hide"))
            except adapter_base.PermissionMissing:
                raise
            except accounts_mod.AccountStopped as e:
                return _fail(args, 1, f"{args.account} は止まっています（{e}）。何もしていません")
            except adapter_base.AdapterError as e:
                message = redact_mod.redact(str(e))
                if getattr(e, "failure", None) == "ambiguous":
                    path = _append_moderation(state_dir, {**record, "result": "unknown"})
                    return _fail(args, 1, f"済んだかどうか分かりません（{path} に result: unknown を残しました）。"
                                          f"媒体の画面で確かめてください: {message}", result="unknown")
                return _fail(args, 1, "できませんでした（何も変えていません）: " + message)
            path = _append_moderation(state_dir, {**record, "result": "confirmed"})
    except lock_mod.LockBusy:
        return _fail(args, 1, f"{args.account} は既に実行中です（ロック取得失敗）。--wait <秒> で空くのを待てます")

    word = {"hide": "隠しました", "unhide": "戻しました", "delete": "消しました"}[action]
    if args.json:
        _print_json({"ok": True, "changed": True, "action": action, "account": args.account,
                     "comment_id": comment_id, "at": at, "by": by, "record": path})
    else:
        print(f"{word}: {args.account} comment {comment_id}（{at}・{by}）")
        print(f"  記録: {path}")
    return 0
