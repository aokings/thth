"""`thth bookmark add|delete|reconcile|list`（設計 4.0 S1 §7.3・S4 §6〜§8・§11）。

    thth bookmark add <口座> (--delivery <id> | --url <URL>) --comment "…" [--tags a,b]
                      [--confirm <digest> --by <名前> --production]
    thth bookmark delete <口座> (--delivery <id> | --url <URL>) [--reason …] [--confirm <digest> --by <名前> --production]
    thth bookmark reconcile <口座> [--url <URL>] [--by <名前>]      （CLI だけ・結果不明の行を GET で確かめる）
    thth bookmark list <口座> [--json]                             （読むだけ・`sent/bm:` の一覧）

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


def _out(args, payload, rc, text_lines=None, *, err=False) -> int:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in text_lines or []:
            print(line, file=sys.stderr if err else sys.stdout)
    return rc


def _refuse(args, code, message, rc=2) -> int:
    return _out(args, {"ok": False, "error": code, "message": message}, rc, [f"{code}: {message}"], err=True)


_MESSAGES = {
    "use_delivery": "この URL は自分の記事です。--delivery <配信 id> で指してください（thth deliveries <口座>）",
    "bookmark_other_lane_article": "この URL は同じ owner の別のレーン（別の主体）の記事です。付けません",
    "invalid_url": "URL は http／https で、host があり、空白・制御文字の無いものです",
    "bookmark_not_found": "THTH が付けたブックマークの記録がありません（手で付けたものは THTH から消しません）",
    "invalid_op": "op は add か delete です（コメント・タグの直しははてなの画面から）",
    "bookmark_precheck_failed": "打つ前の確かめ（この主体のブックマークの GET）が通りませんでした。何もしていません",
    "bookmark_record_failed": "はてなには打ちましたが、記録（sent/bm:）を書けませんでした。置き場を直し、"
                              "はてなの画面で付いているかを確かめてください（打ち直しは柵が止めます）",
    "hatena_auth_rejected": "はてなが認可を断りました。thth principal auth <主体> --force --by <名前> をやり直してください",
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
        return _refuse(args, exc.code, _MESSAGES.get(exc.code, "ブックマークの対象・コメント・タグを確かめてください"
                                                    "（自分の記事は thth deliveries <口座> で published の配信）"))
    flag = f"--delivery {args.delivery}" if args.delivery else f"--url {args.url}"
    if not args.confirm:
        from . import refusals
        refusals.mark_first_stage()  # 1 段目は断りではない（rc 1 は従前の二段確認と同じ）
        verb = "消しません" if op == "delete" else "ブックマークしません"
        rest = " --comment …" if op == "add" else ""
        return _out(args, {"ok": False, "bookmarked": False, "stage": 1, "digest": p["digest"],
                           "preview": hatena_bookmark.preview(p)}, 1,
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
        return _refuse(args, str(exc), core.guard_refusal_message(args.account, exc) + detail, rc=1)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, _MESSAGES.get(exc.code, "打てませんでした（何も記録していません）"))
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
