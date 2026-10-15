"""`thth bookmark add <口座> --delivery <id> --comment "…" [--tags a,b] [--confirm <digest> --by <名前> --production]`
（設計 4.0 S1 §7.3）。

自分のはてなブログの記事（配信の台帳に `published_url` がある配信）に、主体のはてな ID で
ブックマークを付ける 2 段の承認。1 段目（`--confirm` 無し）は URL・記事・コメント・タグ・主体・
今日の残り枠と digest を見せて何もしない（rc 1）。2 段目はロックの中で安全装置を通し、送信の口
（adapter・S4）が無ければ `unsupported_medium` で止まる（rc 2）。予約はしない（queue に載せない）。

台帳が手元に無く `thth login` 済みなら、遠くの道（`bookmark_request`）へ同じ引数で行く（`thth/remote.py`）。
"""
from __future__ import annotations

import json
import os
import sys

from . import accounts as accounts_mod


def register(sub) -> None:
    p = sub.add_parser("bookmark", help="自分のはてなブログの記事にブックマークを付ける（二段確認・送信は 4.0 S4）")
    verbs = p.add_subparsers(dest="verb", required=True)
    add = verbs.add_parser("add", help="ブックマークを付ける（1 段目は表示と digest だけ）")
    add.add_argument("account")
    add.add_argument("--delivery", required=True, help="対象の配信 id（thth deliveries <口座> で見る）")
    add.add_argument("--comment", required=True, help="コメント（100 字まで・改行は空白に）")
    add.add_argument("--tags", default=None, help="タグ（a,b・10 個まで）")
    add.add_argument("--confirm", default=None, help="1 段目が出した digest")
    add.add_argument("--by", default=None, help="誰が承認したか（2 段目で必須）")
    add.add_argument("--production", action="store_true", help="台帳が production: true のときだけ打つ")
    add.add_argument("--remote", action="store_true",
                     help="台帳が手元にあっても thth.me の鍵で動かす（遠くの道・thth login のあと）")
    add.add_argument("--json", action="store_true")
    add.set_defaults(func=cmd)


def _out(args, payload, rc, text_lines=None, *, err=False) -> int:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in text_lines or []:
            print(line, file=sys.stderr if err else sys.stdout)
    return rc


def _refuse(args, code, message, rc=2) -> int:
    return _out(args, {"ok": False, "error": code, "message": message}, rc, [f"{code}: {message}"], err=True)


def cmd(args) -> int:
    from . import core, guard, hatena_bookmark, lock as lock_mod
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _refuse(args, "account_unreadable", str(exc))
    try:
        p = hatena_bookmark.prepare(args.account, cfg, delivery=args.delivery, comment=args.comment,
                                    tags=args.tags)
    except hatena_bookmark.BookmarkError as exc:
        return _refuse(args, exc.code, "ブックマークの対象・コメント・タグを確かめてください"
                       "（対象は thth deliveries <口座> で published の配信だけ）")
    if not args.confirm:
        from . import refusals
        refusals.mark_first_stage()  # 1 段目は断りではない（rc 1 は従前の二段確認と同じ）
        return _out(args, {"ok": False, "bookmarked": False, "stage": 1, "digest": p["digest"],
                           "preview": hatena_bookmark.preview(p)}, 1,
                    ["ブックマークしません（確認の一段目です）"] + hatena_bookmark.lines(p)
                    + [f"付けるなら: thth bookmark add {args.account} --delivery {args.delivery} "
                       f"--comment … --confirm {p['digest']} --by <名前> --production"])
    if args.confirm != p["digest"]:
        return _refuse(args, "confirm_mismatch", f"digest が一致しません（いまの digest は {p['digest']}）", rc=1)
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _refuse(args, "by_required", "--by <名前> を付けてください（誰が承認したかを記録します）", rc=1)
    if not (cfg.get("production") is True and args.production):
        return _refuse(args, "production_disabled",
                       "台帳が production: true で --production を付けたときだけ打ちます（何もしていません）", rc=1)
    state_dir = accounts_mod.state_dir_for(args.account)
    try:
        with core._account_locks(args.account, cfg, state_dir, wait=0):
            again = hatena_bookmark.prepare(args.account, cfg, delivery=args.delivery, comment=args.comment,
                                            tags=args.tags)
            if again["digest"] != args.confirm:
                return _refuse(args, "confirm_mismatch", "表示したあとに対象が変わりました。もう一度 1 段目から", rc=1)
            done = hatena_bookmark.execute_locked(args.account, cfg, again, actor=by, via="cli")
    except lock_mod.LockBusy:
        return _refuse(args, "account_busy", "いま別の実行がロックを持っています", rc=1)
    except guard.GuardRefused as exc:
        return _refuse(args, str(exc), core.guard_refusal_message(args.account, exc), rc=1)
    except hatena_bookmark.BookmarkError as exc:
        if exc.code == "unsupported_medium":
            return _refuse(args, "unsupported_medium",
                           "はてなブックマークの送信の口はまだありません（4.0 S4）。何もしていません")
        return _refuse(args, exc.code, "打てませんでした")
    return _out(args, {"ok": True, **done}, 0, [f"ブックマークしました: {done['url']}"])
