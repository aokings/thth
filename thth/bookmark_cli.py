"""`thth bookmark add|delete|reconcile|list`（設計 4.0 S1 §7.3・S4 §6〜§8・§11）。

    thth bookmark add <口座> (--delivery <id> | --url <URL>) --comment "…" [--tags a,b]
                      [--confirm <digest> --by <名前> --production]
    thth bookmark delete <口座> (--delivery <id> | --url <URL>) [--reason …] [--confirm <digest> --by <名前> --production]
    thth bookmark edit <口座> (--delivery <id> | --url <URL>) [--comment "…"] [--tags a,b]
                       [--confirm <digest> --by <名前> --production]   （THTH が付けたものの直し・設計 S4 §21・4.2）
    thth bookmark reconcile <口座> [--url <URL>] [--by <名前>]      （CLI だけ・結果不明の行を GET で確かめる）
    thth bookmark list <口座> [--json]                             （読むだけ・`sent/bm:` の一覧）
    thth bookmark batch <口座> <ファイル|-> [--confirm <digest> --by <名前> --production] [--json]
                                                                 （まとめて承認・設計 S4 §20・4.1）
    thth bookmark batch-status <口座> [--batch <id>] [--json]       （読むだけ・順番待ちの札と理由）
    thth bookmark batch-cancel <口座> <id> [--item <番号>] --by <名前> （待ちの札を取り消す・1 段）
    thth bookmark batch-resume <口座> --by <名前>                    （止まったまとめを今すぐ見直させる・1 段・4.2.2）

自分のはてなブログの記事（配信の台帳に `published_url` がある配信）か、レーンが参照している他人の
ページに、主体のはてな ID でブックマークを付ける 2 段の承認。1 段目（`--confirm` 無し）は URL・対象・
はてな側の状態・コメント・タグ・主体・今日の残り枠と digest を見せて何もしない（rc 1）。2 段目は
ロックの中で安全装置と 3 つの柵を通し、1 回だけ打つ。予約はしない（queue に載せない）。

台帳が手元に無く `thth login` 済みなら、`add`・`delete` は遠くの道（`bookmark_request`）へ同じ引数で行く。
"""
from __future__ import annotations

import json
import os
import sys

from . import accounts as accounts_mod


def _target_args(parser) -> None:
    parser.add_argument("account")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--delivery", default=None, help="対象の配信 id（自分の記事・thth deliveries <口座> で見る）")
    which.add_argument("--url", default=None, help="参照先の URL（配信の台帳に無い他人のページ）")


def _stage_args(parser) -> None:
    parser.add_argument("--confirm", default=None, help="1 段目が出した digest")
    parser.add_argument("--by", default=None, help="誰が承認したか（2 段目で必須）")
    parser.add_argument("--production", action="store_true", help="台帳が production: true のときだけ打つ")
    parser.add_argument("--remote", action="store_true",
                        help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    parser.add_argument("--json", action="store_true")


def register(sub) -> None:
    p = sub.add_parser("bookmark", help="自分の記事か参照先のページにはてなブックマークを付ける（二段確認）")
    verbs = p.add_subparsers(dest="verb", required=True)
    add = verbs.add_parser("add", help="ブックマークを付ける（1 段目は表示と digest だけ）")
    _target_args(add)
    add.add_argument("--comment", required=True, help="コメント（100 字まで・改行は空白に）")
    add.add_argument("--tags", default=None, help="タグ（a,b・10 個まで）")
    _stage_args(add)
    add.set_defaults(func=cmd)
    delete = verbs.add_parser("delete", help="THTH が付けたブックマークを消す（1 段目は表示と digest だけ）")
    _target_args(delete)
    delete.add_argument("--reason", default=None, help="消す理由（記録に残す・省けば bookmark_delete）")
    _stage_args(delete)
    delete.set_defaults(func=cmd, comment="", tags=None)
    edit = verbs.add_parser("edit", help="THTH が付けたブックマークのコメントとタグを直す（1 段目は今の中身と差分と digest だけ）")
    _target_args(edit)
    edit.add_argument("--comment", default=None, help="直したあとのコメント（省けば今のまま・\"\" で空に）")
    edit.add_argument("--tags", default=None, help="直したあとのタグ（a,b・省けば今のまま・\"\" で外す）")
    _stage_args(edit)
    edit.set_defaults(func=cmd_edit)
    rec = verbs.add_parser("reconcile", help="結果不明（unknown）のブックマークをはてなに訊いて決める（CLI だけ）")
    rec.add_argument("account")
    rec.add_argument("--url", default=None, help="この URL の行だけ")
    rec.add_argument("--by", default=None)
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_reconcile)
    lst = verbs.add_parser("list", help="THTH が付けたブックマークの一覧（読むだけ）")
    lst.add_argument("account")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=cmd_list)
    batch = verbs.add_parser("batch", help="1 日分の札（JSON Lines）をまとめて承認する（1 段目は一覧と digest だけ）")
    batch.add_argument("account")
    batch.add_argument("file", help='札のファイル（1 行に {"url"|"delivery", "comment", "tags"}・- なら標準入力）')
    _stage_args(batch)
    batch.set_defaults(func=cmd_batch)
    bstatus = verbs.add_parser("batch-status", help="まとめて承認した札の状態（読むだけ）")
    bstatus.add_argument("account")
    bstatus.add_argument("--batch", default=None, help="このまとめだけ（bb_…）")
    bstatus.add_argument("--remote", action="store_true",
                         help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    bstatus.add_argument("--json", action="store_true")
    bstatus.set_defaults(func=cmd_batch_status)
    bcancel = verbs.add_parser("batch-cancel", help="まとめて承認した札のうち待ちのものを取り消す")
    bcancel.add_argument("account")
    bcancel.add_argument("batch", help="まとめの id（bb_…・batch-status で見る）")
    bcancel.add_argument("--item", type=int, default=None, help="この番号の札だけ")
    bcancel.add_argument("--by", default=None, help="誰が取り消したか（必須）")
    bcancel.add_argument("--remote", action="store_true",
                         help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    bcancel.add_argument("--json", action="store_true")
    bcancel.set_defaults(func=cmd_batch_cancel)
    bresume = verbs.add_parser("batch-resume", help="止まったまとめを今すぐ見直させる（はてなに打って止まったときの"
                                                    " 60 分の待ちを外す・札は変えない）")
    bresume.add_argument("account")
    bresume.add_argument("--by", default=None, help="誰が解いたか（必須）")
    bresume.add_argument("--json", action="store_true")
    bresume.set_defaults(func=cmd_batch_resume)


def _out(args, payload, rc, text_lines=None, *, err=False) -> int:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in text_lines or []:
            print(line, file=sys.stderr if err else sys.stdout)
    return rc


def _bookmark_message(exc, fallback) -> str:
    text = _MESSAGES.get(exc.code, fallback)
    if exc.code == "bookmark_tag_too_long" and getattr(exc, "tags", None):
        from .hatena_bookmark import tag_too_long_text
        return tag_too_long_text(exc.tags)
    if exc.code == "hatena_rejected_item":
        from .hatena_bookmark import rejected_item_text
        return rejected_item_text(getattr(exc, "status", None))
    if exc.code == "bookmark_already_deleted" and getattr(exc, "at", None):
        text += f"（{exc.at} に削除）"
    return text


def _foresight(p) -> dict:
    """1 段目の `--json` に「見立て」（段 4・§7.4）: `assessment`（当たる見込みの行）と `advisories[]`。"""
    from . import core
    found = p.get("foresight")
    if not isinstance(found, dict):
        return {}
    advisories = [core.public_assessment(a) for a in found.get("advisories") or []]
    if found.get("outcome") in ("pass", "advise"):
        return {"assessment": None, "advisories": advisories}
    return {"assessment": core.public_assessment(found), "advisories": advisories}


def _guard_refuse(args, exc, *, suffix="") -> int:
    """安全装置の断り（段 4・§7.2 の型: 今の 1 行・根拠・通すには）。`--json` に `assessment`。"""
    from . import core
    parts = core.refusal_parts(args.account, exc)
    message = parts["line"] + suffix
    payload = {"ok": False, "error": str(exc), "message": message, "assessment": parts["assessment"]}
    lines = [f"{exc}: {message}"] + core.refusal_lines(args.account, exc)[1:]
    return _out(args, payload, 1, lines, err=True)


def _refuse(args, code, message, rc=2, **extra) -> int:
    return _out(args, {"ok": False, "error": code, "message": message, **extra}, rc, [f"{code}: {message}"],
                err=True)


_MESSAGES = {
    "use_delivery": "この URL は自分の記事です。--delivery <配信 id> で指してください（thth deliveries <口座>）",
    "bookmark_other_lane_article": "この URL は同じ owner の別のレーン（別の主体）の記事です。付けません",
    "invalid_url": "URL は http／https で、host があり、空白・制御文字の無いものです",
    "bookmark_not_found": "THTH が付けたブックマークの記録がありません（手で付けたものは THTH から消しません）",
    "bookmark_already_deleted": "このブックマークはもう消しています",
    "bookmark_edit_missing_remote": "はてなにこの主体のブックマークがありません。直しの口では新しく付けません"
                                    "（付けるなら thth bookmark add）。何もしていません",
    "bookmark_edit_no_change": "直したあとの中身が今と同じです（何もしていません）",
    "bookmark_result_unknown": "付けた（か前の直しの）結果がまだ分かりません。thth bookmark reconcile <口座> のあとに直してください",
    "approval_stale": "表示したあとに台帳が変わりました。もう一度 1 段目から",
    "invalid_op": "op は add か delete です（コメント・タグの直しははてなの画面から）",
    "bookmark_precheck_failed": "打つ前の確かめ（この主体のブックマークの GET）が通りませんでした。何もしていません",
    "bookmark_record_failed": "はてなには打ちましたが、記録（sent/bm:）を書けませんでした。置き場を直し、"
                              "はてなの画面で付いているかを確かめてください（打ち直しは柵が止めます）",
    "hatena_auth_rejected": "はてなが認可を断りました。thth principal auth <主体> --force --by <名前> をやり直してください",
    # 4.2.2（S4 §24）: 文は `_bookmark_message` が HTTP の番号と札の直し方を入れて作る。
    "hatena_rejected_item": "この札だけはてなに断られました（token は生きています）",
}


def cmd(args) -> int:
    from . import core, guard, hatena_bookmark, lock as lock_mod
    op = "delete" if args.verb == "delete" else "add"
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    target = {"delivery": args.delivery, "url": args.url}
    try:
        p = hatena_bookmark.prepare(args.account, cfg, comment=args.comment, tags=args.tags, op=op, **target)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, _bookmark_message(exc, "ブックマークの対象・コメント・タグを確かめてください"
                                                         "（自分の記事は thth deliveries <口座> で published の配信）"))
    flag = f"--delivery {args.delivery}" if args.delivery else f"--url {args.url}"
    if not args.confirm:
        from . import refusals
        refusals.mark_first_stage()  # 1 段目は断りではない（rc 1 は従前の二段確認と同じ）
        verb = "消しません" if op == "delete" else "ブックマークしません"
        rest = " --comment …" if op == "add" else ""
        return _out(args, {"ok": False, "bookmarked": False, "stage": 1, "digest": p["digest"],
                           "preview": hatena_bookmark.preview(p), **_foresight(p)}, 1,
                    [f"{verb}（確認の一段目です）"] + hatena_bookmark.lines(p)
                    + [f"{'消す' if op == 'delete' else '付ける'}なら: thth bookmark {args.verb} {args.account} {flag}"
                       f"{rest} --confirm {p['digest']} --by <名前> --production"])
    if args.confirm != p["digest"]:
        return _refuse(args, "confirm_mismatch", f"digest が一致しません（いまの digest は {p['digest']}）", rc=1)
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _refuse(args, "by_required", "--by <名前> を付けてください（誰が承認したかを記録します）", rc=1)
    if not (cfg.get("production") is True and args.production):
        return _refuse(args, "production_disabled",
                       "台帳が production: true で --production を付けたときだけ打ちます（何もしていません）", rc=1)
    state_dir = accounts_mod.state_dir_for(args.account)
    logs = []
    try:
        with core._account_locks(args.account, cfg, state_dir, wait=0):
            again = hatena_bookmark.prepare(args.account, cfg, comment=args.comment, tags=args.tags, op=op,
                                            check_remote=False, **target)
            if again["digest"] != args.confirm:
                return _refuse(args, "confirm_mismatch", "表示したあとに対象が変わりました。もう一度 1 段目から", rc=1)
            if op == "delete":
                done = hatena_bookmark.delete_locked(args.account, cfg, again, actor=by, via="cli",
                                                     reason=getattr(args, "reason", None))
            else:
                done = hatena_bookmark.execute_locked(args.account, cfg, again, actor=by, via="cli",
                                                      log=logs.append)
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except guard.GuardRefused as exc:
        detail = f"（{exc.reason}）" if getattr(exc, "reason", None) else ""
        return _guard_refuse(args, exc, suffix=detail)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, _bookmark_message(exc, "打てませんでした（何も記録していません）"))
    finally:
        for line in logs:
            print(line, file=sys.stderr)
    if done.get("status") == "unknown":
        return _out(args, {"ok": False, **done}, 1,
                    [f"結果が分かりません（{done.get('error')}）: {done['url']}",
                     f"打ち直しません。thth bookmark reconcile {args.account} で確かめてください"], err=True)
    if op == "delete":
        note = "（はてなに既にありませんでした）" if done.get("missing") else ""
        return _out(args, {"ok": True, **done}, 0, [f"ブックマークを消しました{note}: {done['url']}"])
    return _out(args, {"ok": True, **done}, 0, [f"ブックマークしました: {done['url']}"]
                + ([f"  {done['permalink']}"] if done.get("permalink") else []))


def cmd_edit(args) -> int:
    """`thth bookmark edit`（設計 S4 §21）。新規の追加とは別の柵: 自分の主体の confirmed の行だけ・はてなに無ければ打たない。"""
    from . import core, guard, hatena_bookmark, lock as lock_mod, refusals
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    target = {"delivery": args.delivery, "url": args.url}
    fallback = "直せる行を確かめてください（thth bookmark list <口座>・THTH が付けて confirmed の行だけ直せます）"
    try:
        p = hatena_bookmark.prepare_edit(args.account, cfg, comment=args.comment, tags=args.tags, **target)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, _bookmark_message(exc, fallback))
    flag = f"--delivery {args.delivery}" if args.delivery else f"--url {args.url}"
    if not args.confirm:
        refusals.mark_first_stage()
        rest = "".join(f" --{key} {json.dumps(value, ensure_ascii=False)}"
                       for key, value in (("comment", args.comment), ("tags", args.tags)) if value is not None)
        return _out(args, {"ok": False, "edited": False, "stage": 1, "digest": p["digest"],
                           "preview": hatena_bookmark.preview_edit(p), **_foresight(p)}, 1,
                    ["直しません（確認の一段目です）"] + hatena_bookmark.edit_lines(p)
                    + [f"直すなら: thth bookmark edit {args.account} {flag}{rest} --confirm {p['digest']}"
                       " --by <名前> --production"])
    if args.confirm != p["digest"]:
        return _refuse(args, "confirm_mismatch", f"digest が一致しません（いまの digest は {p['digest']}）", rc=1)
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _refuse(args, "by_required", "--by <名前> を付けてください（誰が承認したかを記録します）", rc=1)
    if not (cfg.get("production") is True and args.production):
        return _refuse(args, "production_disabled",
                       "台帳が production: true で --production を付けたときだけ打ちます（何もしていません）", rc=1)
    logs = []
    try:
        with core._account_locks(args.account, cfg, accounts_mod.state_dir_for(args.account), wait=0):
            again = hatena_bookmark.prepare_edit(args.account, cfg, comment=args.comment, tags=args.tags,
                                                 check_remote=False, **target)
            if again["digest"] != args.confirm:
                return _refuse(args, "confirm_mismatch", "表示したあとに台帳が変わりました。もう一度 1 段目から", rc=1)
            done = hatena_bookmark.edit_locked(args.account, cfg, again, actor=by, via="cli", log=logs.append)
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except guard.GuardRefused as exc:
        detail = f"（{exc.reason}）" if getattr(exc, "reason", None) else ""
        return _guard_refuse(args, exc, suffix=detail)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, _bookmark_message(exc, "直せませんでした（何も記録していません）"))
    finally:
        for line in logs:
            print(line, file=sys.stderr)
    if done.get("status") == "unknown":
        return _out(args, {"ok": False, **done}, 1,
                    [f"結果が分かりません（{done.get('error')}）: {done['url']}",
                     f"打ち直しません。thth bookmark reconcile {done.get('holder') or args.account} で確かめてください"],
                    err=True)
    return _out(args, {"ok": True, **done}, 0, [f"ブックマークを直しました: {done['url']}"]
                + ([f"  {done['permalink']}"] if done.get("permalink") else []))


def cmd_reconcile(args) -> int:
    from . import core, hatena_bookmark, lock as lock_mod
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip() or None
    try:
        with core._account_locks(args.account, cfg, accounts_mod.state_dir_for(args.account), wait=0):
            out = hatena_bookmark.reconcile(args.account, cfg, url=args.url, by=by)
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, _MESSAGES.get(exc.code, "照合できませんでした"))
    lines = [f"{args.account}: 結果不明 {out['checked']} 件を確かめました（付いていた {out['confirmed']}・"
             f"無かった {out['not_found']}・分からないまま {out['unchanged']}）"]
    lines += [f"  {row['result']}  {row['url']}" for row in out["rows"]]
    lines += [f"  直し: {row['result']}  {row['url']}" for row in out.get("edits") or []]
    return _out(args, {"ok": True, **out}, 0, lines)


def cmd_list(args) -> int:
    from . import hatena_bookmark
    try:
        accounts_mod.validate_name(args.account)
        accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    rows = hatena_bookmark.list_rows(args.account)
    lines = [f"{args.account}: ブックマーク {len(rows)} 件"]
    for row in rows:
        gone = f"  削除 {row['retracted_at']}" if row.get("retracted_at") else ""
        lines.append(f"  {row['sent_at']}  {row['target'] or '?'}  {row['result'] or '?'}  {row['url']}{gone}")
    return _out(args, {"account": args.account, "n": len(rows), "bookmarks": rows}, 0, lines)


# --------------------------------------------------------------------------
# まとめて承認（設計 S4 §20・4.1）
# --------------------------------------------------------------------------

_BATCH_MESSAGES = {
    "batch_empty": "付けられる札が 1 件もありません（除いた理由は 1 段目の一覧に）",
    "batch_too_large": "札が多すぎます（1 ファイル 100 件・256 KB まで）。分けてください",
    "batch_exists": "同じ札の並びはもう承認しています（thth bookmark batch-status <口座>）",
    "batch_not_found": "そのまとめ（か札の番号）がありません（thth bookmark batch-status <口座>）",
    "batch_unreadable": "まとめのファイルが読めません",
    "batch_text_invalid": "札のファイルを読めません（1 行に 1 件の JSON か、JSON の配列）",
    "batch_not_cancellable": "その札は待ちではありません（付けた・断った・取り消した札は取り消せません）",
    "schedule_unavailable": "timer に載っていない口座（scheduled: false）では、並べても付きません",
    "unsupported_operation": "はてなの口座でだけ使えます",
}


def _batch_refuse(args, exc) -> int:
    return _refuse(args, exc.code, _BATCH_MESSAGES.get(exc.code, "まとめて承認できませんでした"))


def cmd_batch(args) -> int:
    from . import bookmark_batch, core, guard, lock as lock_mod, refusals
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    try:
        text = bookmark_batch.read_source(args.file)
        p = bookmark_batch.prepare(args.account, cfg, text)
    except bookmark_batch.BatchError as exc:
        return _batch_refuse(args, exc)
    if not args.confirm:
        refusals.mark_first_stage()  # 1 段目は断りではない（rc 1 は従前の二段確認と同じ）
        return _out(args, {"ok": False, "stage": 1, "digest": p["digest"], "preview": p}, 1,
                    ["ブックマークしません（まとめて承認の一段目です）"] + bookmark_batch.lines(p)
                    + [f"並べるなら: thth bookmark batch {args.account} {args.file} --confirm {p['digest']}"
                       " --by <名前> --production"])
    if args.confirm != p["digest"]:
        return _refuse(args, "confirm_mismatch", f"digest が一致しません（いまの digest は {p['digest']}）", rc=1)
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _refuse(args, "by_required", "--by <名前> を付けてください（誰が承認したかを記録します）", rc=1)
    if not (cfg.get("production") is True and args.production):
        return _refuse(args, "production_disabled",
                       "台帳が production: true で --production を付けたときだけ並べます（何もしていません）", rc=1)
    try:
        with core._account_locks(args.account, cfg, accounts_mod.state_dir_for(args.account), wait=0):
            locked = accounts_mod.load_account(args.account)
            again = bookmark_batch.prepare(args.account, locked, text)
            if again["digest"] != args.confirm:
                return _refuse(args, "confirm_mismatch", "表示したあとに札か台帳が変わりました。もう一度 1 段目から",
                               rc=1)
            done = bookmark_batch.approve_locked(args.account, locked, again, actor=by, via="cli")
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except guard.GuardRefused as exc:
        return _guard_refuse(args, exc)
    except bookmark_batch.BatchError as exc:
        return _batch_refuse(args, exc)
    start, end = bookmark_batch.ACTIVE_HOURS
    return _out(args, {"ok": True, **done}, 0,
                [f"{args.account}: {done['queued']} 件を順番待ちに置きました（{done['batch']}・除いた {done['excluded']} 件）",
                 f"  timer が {start}〜{end} 時に間を散らして付けます（期限 {bookmark_batch._hm(done['expires_at'])}）",
                 f"  状態: thth bookmark batch-status {args.account}"])


def cmd_batch_status(args) -> int:
    from . import bookmark_batch
    try:
        accounts_mod.validate_name(args.account)
        accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    try:
        s = bookmark_batch.status(args.account, batch=args.batch)
    except bookmark_batch.BatchError as exc:
        return _batch_refuse(args, exc)
    return _out(args, s, 0, bookmark_batch.status_lines(s))


def cmd_batch_resume(args) -> int:
    """`thth bookmark batch-resume`（4.2.2・S4 §24）。止まりを外すだけ（札・指紋・sent/ は触らない・1 段）。"""
    from . import bookmark_batch, core, lock as lock_mod
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _refuse(args, "by_required", "--by <名前> を付けてください（誰が解いたかを記録します）", rc=1)
    try:
        with core._account_locks(args.account, cfg, accounts_mod.state_dir_for(args.account), wait=0):
            done = bookmark_batch.resume_locked(args.account, by=by)
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except bookmark_batch.BatchError as exc:
        return _batch_refuse(args, exc)
    was = done.get("was_blocked")
    head = (f"{args.account}: 止まり（{was.get('code')}）を外しました" if was
            else f"{args.account}: 止まっていませんでした（待ちの時刻だけ外しました）")
    return _out(args, {"ok": True, **done}, 0,
                [head, f"  次の thth run が待ち {done['pending']} 件を今の柵で見直します（まだ直っていなければ、また止まります）"])


def cmd_batch_cancel(args) -> int:
    from . import bookmark_batch, core, lock as lock_mod
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _refuse(args, "by_required", "--by <名前> を付けてください（誰が取り消したかを記録します）", rc=1)
    try:
        with core._account_locks(args.account, cfg, accounts_mod.state_dir_for(args.account), wait=0):
            done = bookmark_batch.cancel_locked(args.account, args.batch, by=by, item=args.item)
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except bookmark_batch.BatchError as exc:
        return _batch_refuse(args, exc)
    return _out(args, {"ok": True, **done}, 0,
                [f"{args.account}: {done['batch']} の待ちの札を {done['cancelled']} 件取り消しました"
                 "（付けたものは消していません。消すなら thth bookmark delete）"])
