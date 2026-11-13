"""`thth register …` の命令（台帳の機械・計画 `docs/計画_実店舗_2026-11-13.md` 段 1〜4）。

    thth register add <台帳> --path <フォルダ> --project <project> --kind media|store --by <名前>
                                       台帳のフォルダを THTH に控える（控えは 0600・台帳そのものは預からない）
    thth register check <台帳> [--json]
                                       全部の行の形を点検する（頭の 7 つの欄・印・見直し日・秘密らしい値・種類ごとの形）
    thth register show <台帳> [--json]
                                       行ごとに「中身の材料に使えるか」と、使えないならその符丁
    thth register use <台帳> <行> [--json]
                                       柵: その行を材料に使えるか。使えれば外に出してよい欄と本文、使えなければ断る
    thth register confirm <台帳> <行> --by <名前> [--confirm D] [--json]
                                       本人が認めた（営業の時間と臨時は二段確認）
    thth register deny <台帳> <行> --by <名前> [--json]
                                       本人が違うと言った（行は消さない・同じ推測を二度聞かない）
    thth register remove <台帳> --by <名前> [--json]
                                       THTH の控えと認めた記録を消す（台帳のフォルダは消さない）

記録（`admin_log`）の動きの名前は `register_` で始める。名前・時刻・行の番号だけで、台帳の本文は入れない。
**網に出ない。** 台帳には頭の決まった欄（`form.MANAGED`）しか書かない。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from .. import admin_log, jst, redact, refusal
from . import ACTION_PREFIX, COMMAND, confirm as confirm_mod, fence, form, kinds, ledger
from .errors import RegisterRefused

# 詳細の語のうち、目録の `by_detail` に通し方があるもの。
DETAIL_WORDS = frozenset(("register_name", "project", "by", "row", "confirm"))


def _refuse(args, exc: RegisterRefused) -> int:
    reason = {"reason": exc.detail} if exc.detail in DETAIL_WORDS else {}
    found = refusal.parts(exc.code, **reason) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（{exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail, **exc.extra}
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload, **reason)


def _run(handler):
    def call(args) -> int:
        try:
            return handler(args)
        except RegisterRefused as exc:
            return _refuse(args, exc)
    return call


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _by(args) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None))
    except ValueError:
        raise RegisterRefused("register_args_invalid", "by") from None


def _record(event: str, name: str, by: str, diff: dict) -> None:
    try:
        admin_log.append(ACTION_PREFIX + event, name, {"media": "register"}, by=by, diff=diff)
    except (OSError, ValueError, admin_log.AdminLogError):
        raise RegisterRefused("register_log_unavailable") from None


def _log_ready() -> None:
    try:
        with admin_log.transaction():
            pass
    except (OSError, ValueError, admin_log.AdminLogError):
        raise RegisterRefused("register_log_unavailable") from None


def _today():
    return jst.now_jst().date()


def open_register(name: str):
    """`(控え, 台帳のフォルダ, 読んだ結果)`。"""
    cfg = ledger.load(name)
    root = ledger.root_of(cfg)
    return cfg, root, form.scan(root)


def find_row(scanned: dict, row_id: str) -> form.Row:
    if not isinstance(row_id, str) or not form.ID.fullmatch(row_id):
        raise RegisterRefused("register_args_invalid", "row")
    row = form.find(scanned["rows"], row_id)
    if row is None:
        raise RegisterRefused("register_row_missing", row_id)
    return row


def _row_line(row: form.Row) -> list:
    return [f"  {row.path}: {word} — {form.problem_text(word)}" for word in row.problems]


# ------------------------------------------------------------------ 命令

def cmd_add(args) -> int:
    by = _by(args)
    name = ledger.check_name(args.register)
    if not isinstance(args.project, str) or not ledger.accounts.name_is_safe(args.project):
        raise RegisterRefused("register_args_invalid", "project")
    path = ledger.check_path(args.path)
    _log_ready()
    written = ledger.write_new(name, ledger.build(name, project=args.project, path=path, kind=args.kind))
    _record("added", name, by, {"project": [None, args.project], "kind": [None, args.kind]})
    scanned = form.scan(path)
    value = {"register": name, "ledger": written, "rows": len(scanned["rows"]), "problems": len(scanned["problems"])}
    if args.json:
        _print(value)
        return 0
    print(f"台帳 {name} を控えました（{written}）。行 {value['rows']} 枚・形の問題 {value['problems']} 件")
    print(f"  次: thth {COMMAND} check {name} で形を点検します")
    return 0


def cmd_check(args) -> int:
    cfg, _root, scanned = open_register(args.register)
    value = {"register": args.register, "kind": cfg["kind"], "rows": len(scanned["rows"]),
             "skipped": scanned["skipped"], "problems": scanned["problems"]}
    if scanned["problems"]:
        lines = []
        for row in scanned["rows"]:
            lines += _row_line(row)
        if not args.json:
            for line in lines:
                print(line, file=sys.stderr)
        raise RegisterRefused("register_row_invalid", f"{len(scanned['problems'])} rows",
                              problems=scanned["problems"], rows=len(scanned["rows"]))
    if args.json:
        _print(value)
        return 0
    print(f"台帳 {args.register}: 行 {value['rows']} 枚の形は合っています（頭の無い .md {value['skipped']} 枚は読み物として数えただけ）")
    return 0


def cmd_show(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    records = ledger.read_confirmed(args.register)
    today = _today()
    rows = [{"id": row.id, "path": row.path, "kind": row.kind, "confirmed": row.head.get("confirmed"),
             "usable": fence.state(row, records, today) is None, "code": fence.state(row, records, today)}
            for row in scanned["rows"]]
    if args.json:
        _print({"register": args.register, "rows": rows})
        return 0
    for row in rows:
        mark = "使える" if row["usable"] else row["code"]
        print(f"{row['id'] or '(番号なし)'}\t{row['kind']}\t{row['confirmed']}\t{mark}\t{row['path']}")
    return 0


def cmd_use(args) -> int:
    _cfg, _root, scanned = open_register(args.register)
    row = fence.usable(find_row(scanned, args.row), ledger.read_confirmed(args.register), _today())
    value = {"register": args.register, "row": row.id, "kind": row.kind, "fields": form.outward(row),
             "body": row.body.strip(), "digest": form.fingerprint(row.text)}
    if args.json:
        _print(value)
        return 0
    print(f"行 {row.id}（{row.kind}）は材料に使えます。外に出してよい欄:")
    for key, val in value["fields"].items():
        print(f"  {key}: {val}")
    if value["body"]:
        print("本文:")
        print(value["body"])
    return 0


def cmd_confirm(args) -> int:
    by = _by(args)
    _cfg, root, scanned = open_register(args.register)
    row = find_row(scanned, args.row)
    if row.problems:
        raise RegisterRefused("register_row_secret" if "secret" in row.problems else "register_row_invalid", row.id)
    if row.head["confirmed"] == form.YES and (ledger.read_confirmed(args.register).get(row.id) or {}).get(
            "digest") == form.fingerprint(row.text):
        value = {"register": args.register, "row": row.id, "confirmed": form.YES, "already": True}
        _print(value) if args.json else print(f"行 {row.id} は既に本人が認めた行です（中身も認めたときのままです）")
        return 0
    digest = confirm_mod.digest_for(args.register, row)
    if kinds.two_stage(row.kind) and args.confirm != digest:
        if args.confirm is not None:
            raise RegisterRefused("register_confirm_mismatch", "confirm", digest=digest)
        refusal.mark_first_stage()
        value = {"register": args.register, "row": row.id, "kind": row.kind, "stage": 1, "digest": digest,
                 "text": row.text}
        if args.json:
            _print(value)
            return 1
        print(f"行 {row.id}（{row.kind}）は二段で認める種類です。次の全文を持ち主に見せ、合っていると言われたら、")
        print(f"thth {COMMAND} confirm {args.register} {row.id} --by {by} --confirm {digest} で確定してください。")
        print("-----")
        print(row.text)
        print("-----")
        print(f"digest: {digest}")
        return 1
    _log_ready()
    done = confirm_mod.confirm(args.register, root, row, by=by)
    _record("confirmed", args.register, by, {"row": [None, row.id]})
    if args.json:
        _print({"register": args.register, **done})
        return 0
    print(f"行 {row.id} を「本人が認めた」にしました（{done['confirmed_at']}）。中身を変えると、認めたは外れます")
    return 0


def cmd_deny(args) -> int:
    by = _by(args)
    _cfg, root, scanned = open_register(args.register)
    row = find_row(scanned, args.row)
    if row.problems:
        raise RegisterRefused("register_row_secret" if "secret" in row.problems else "register_row_invalid", row.id)
    _log_ready()
    done = confirm_mod.deny(args.register, root, row, by=by)
    _record("denied", args.register, by, {"row": [None, row.id]})
    if args.json:
        _print({"register": args.register, **done})
        return 0
    print(f"行 {row.id} を「本人が違うと言った」にしました。行は残すので、同じことを二度聞きません")
    return 0


def remove_state(name: str) -> list:
    """控えと認めた記録を消す（台帳のフォルダは消さない）。消した物の並び。"""
    done = []
    state = ledger.state_dir_for(name)
    if os.path.isdir(state) and not os.path.islink(state):
        shutil.rmtree(state)
        done.append("state")
    try:
        os.unlink(ledger.path_for(name))
        done.append("ledger")
    except FileNotFoundError:
        pass
    return done


def cmd_remove(args) -> int:
    by = _by(args)
    ledger.load(args.register)
    _log_ready()
    done = remove_state(args.register)
    _record("removed", args.register, by, {"deleted": [[], done]})
    if args.json:
        _print({"register": args.register, "deleted": done})
        return 0
    print(f"台帳 {args.register} の控えと認めた記録を消しました（台帳のフォルダには触っていません）")
    return 0


# ------------------------------------------------------------------ 退出

def purge_for_leave(account: str, project, *, by: str) -> list:
    """その project の最後の口座が抜けるとき、その project の台帳の控えと記録を消す（台帳のフォルダは消さない）。"""
    from .. import accounts
    if not accounts.name_is_safe(project or ""):
        return []
    names = ledger.of_project(project)
    if not names:
        return []
    for other in accounts.list_account_names():
        if other == account:
            continue
        try:
            if accounts.load_account(other).get("project") == project:
                return []
        except accounts.AccountError:
            return []
    removed = []
    for name in names:
        done = remove_state(name)
        admin_log.append(ACTION_PREFIX + "removed", name, {"media": "register"}, by=by,
                         diff={"deleted": [[], done], "via": [None, "leave"]})
        removed.append(name)
    return removed


# ------------------------------------------------------------------ parser

PARENT_HELP = "台帳の機械（形の点検・推測を材料に使わせない柵・認める操作）"


def register(sub) -> None:
    import argparse
    parent = sub.add_parser(COMMAND, help=PARENT_HELP, description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = parent.add_subparsers(dest="register_command", required=True)

    p = verbs.add_parser("add", help="台帳のフォルダを控える")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--path", required=True, metavar="フォルダ")
    p.add_argument("--project", required=True)
    p.add_argument("--kind", required=True, choices=ledger.KINDS)
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_add))

    for verb, help_text, func in (("check", "全部の行の形を点検する", cmd_check),
                                  ("show", "行ごとに材料に使えるか", cmd_show)):
        p = verbs.add_parser(verb, help=help_text)
        p.add_argument("register", metavar="台帳")
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=_run(func))

    p = verbs.add_parser("use", help="柵: その行を材料に使えるか")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_use))

    p = verbs.add_parser("confirm", help="本人が認めた（営業の時間と臨時は二段確認）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--by", required=True)
    p.add_argument("--confirm", default=None, metavar="D")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_confirm))

    p = verbs.add_parser("deny", help="本人が違うと言った（行は消さない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("row", metavar="行")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_deny))

    p = verbs.add_parser("remove", help="THTH の控えと認めた記録を消す（台帳のフォルダは消さない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_run(cmd_remove))
