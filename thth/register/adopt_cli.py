"""台帳の器 段 2 の命令（設計 §13 の `register adopt`・`restore`・`diff`・`merge`・`row add|set` と、控えの
`keep`・止まった入れ替えの `resume`）。

    thth register adopt <元のフォルダ> --to <台帳のフォルダ> --by <名前> [--plan <範囲.json>] [--decl <宣言のフォルダ>] [--json]
        元から台帳を作る（元には 1 バイトも書かない）。2 回めは取り込み直し（変わった物と消えた物だけ・片道）
    thth register restore <台帳> --to <先のフォルダ> [--at <控え>] [--json]
        台帳の全部（か控えた前の版）を元の場所・名前・バイトで書く。目録の指紋で、数・名前・sha256 が同じと言える
    thth register diff <台帳> <前>..<後> [--json]
        足した・変わった・退役した・消えた記録とファイル。番号かファイルが消えたら赤（rc 1）。<前>・<後> は控えた
        目録の指紋の頭（8 字以上）・first・last・now
    thth register keep <台帳> --by <名前> [--list] [--json]
        今の姿を控える（元の形式の道具が直に書いたあと・git の外の台帳で前の版を取り出せるように）
    thth register merge <台帳> <束のフォルダ> --by <名前> [--base <目録の指紋>] [--json]
        束のファイルを、まとめての入れ替えで入れる（止まっても、打ち直すか resume で続け、resume --rollback で戻す）
    thth register resume <台帳> (--continue | --rollback) --by <名前> [--json]
        止まった入れ替えを続ける・戻す（別の者が打ってよい）
    thth register row set <台帳> <種類:番号> --set 欄=値 [...] --by <名前> [--json]
    thth register row add <台帳> <種類> --set 欄=値 [...] --by <名前> [--table <道>] [--json]
        表の 1 行を直す・足す。触っていない行のバイトが 1 つでも変わるなら書かずに断る

<台帳> は、控えた台帳の名前（`register add`）か、台帳のフォルダの道（`/` を含む）。網に出ない。
"""
from __future__ import annotations

import json
import os

from .. import admin_log, jst
from . import adopt as A
from . import keep as K
from . import ledger
from .errors import RegisterRefused


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def _at() -> str:
    return jst.now_jst().isoformat(timespec="seconds")


def _by(args) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None))
    except ValueError:
        raise RegisterRefused("register_args_invalid", "by") from None


def root_of(name: str) -> str:
    """控えた名前か、フォルダの道（`/` を含む・`~` か `.` で始まる）。"""
    if isinstance(name, str) and (os.sep in name or "/" in name or name.startswith(("~", "."))):
        return ledger.check_path(name)
    return ledger.root_of(ledger.load(name))


def _short(fp) -> str:
    return fp[:16] if isinstance(fp, str) else "-"


def cmd_adopt(args) -> int:
    by = _by(args)
    plan = A.read_plan(args.plan)
    decl = os.path.abspath(os.path.expanduser(args.decl)) if args.decl else None
    value = A.adopt(args.source, args.to, plan=plan, by=by, at=_at(), decl=decl)
    if args.json:
        _print(value)
        return 0
    print(f"取り込みました: {value['files']} ファイル・{value['bytes']} バイト → {value['register']}")
    print(f"  台帳の目録の指紋 {_short(value['fp'])}・元の目録の指紋 {_short(value['source_fp'])}"
          f"（{'同じ' if value['same'] else '違う'}）")
    if value["continued"]:
        print("  止まっていた入れ替えの続きから進めました")
    if not value["first"] and (value["changed"] or value["removed"]):
        print(f"  入れ替えた {value['changed']}・元から消えて台帳から除いた {value['removed']}（前の中身は控えに残ります）")
    if value["source_moved"]:
        print("  取り込みの間に元が動きました（元の目録の指紋が読み始めと読み終わりで違います）。もう一度取り込むと追いつきます")
    print("  元には書いていません（読んだだけ）")
    return 0


def cmd_restore(args) -> int:
    root = root_of(args.register)
    value = A.restore(root, args.to, at_snapshot=args.at)
    if args.json:
        _print(value)
        return 0
    print(f"戻しました: {value['files']} ファイル・{value['bytes']} バイト → {value['to']}")
    print(f"  目録の指紋 {_short(value['fp'])}（取り込んだ時の元の目録の指紋と同じなら、数・名前・sha256 が全部同じ）")
    if value["pending"]:
        print("  止まった入れ替えが残っているので、入れ替えの前の姿を書きました（thth register resume で続けるか戻す）")
    return 0


def cmd_diff(args) -> int:
    root = root_of(args.register)
    value = A.diff(root, args.range)
    if args.json:
        if not value["red"]:
            _print(value)
    else:
        r, f = value["records"], value["files"]
        print(f"{_short(value['from'])}..{_short(value['to'])}: 記録 足した {len(r['added'])}・変わった {len(r['changed'])}・"
              f"退役した {len(r['retired'])}・消えた {len(r['vanished'])}／ファイル 足した {len(f['added'])}・"
              f"変わった {len(f['changed'])}・消えた {len(f['removed'])}")
        for ref in r["vanished"][:20]:
            print(f"  消えた記録 {ref}")
        for path in f["removed"][:20]:
            print(f"  消えたファイル {path}")
        for item in r["changed"][:20]:
            units = item.get("units")
            extra = f"（単位 足した {units['added']}・消えた {units['vanished']}）" if units else ""
            print(f"  変わった {item['record']}{extra}")
    if value["red"]:
        raise RegisterRefused("register_record_vanished", f"{len(value['records']['vanished'])} records "
                              f"{len(value['files']['removed'])} files",
                              vanished=value["records"]["vanished"][:50], removed=value["files"]["removed"][:50],
                              diff=value)
    return 0


def cmd_keep(args) -> int:
    root = root_of(args.register)
    if args.list:
        value = {"history": K.history(root), "snapshots": K.snapshots(root)}
        if args.json:
            _print(value)
        else:
            for h in value["history"]:
                print(f"{h.get('at')}\t{h.get('what')}\t{_short(h.get('fp'))}\t{h.get('by')}")
        return 0
    by = _by(args)
    K.refuse_if_pending(root)
    entries = K.view(root)
    fp = K.save_snapshot(root, entries, what="keep", by=by, at=_at())
    value = {"fp": fp, "files": len(entries)}
    if args.json:
        _print(value)
    else:
        print(f"控えました: {len(entries)} ファイル・目録の指紋 {_short(fp)}（thth register diff <台帳> {fp[:12]}..now）")
    return 0


def _batch_line(value: dict) -> str:
    if not value.get("batch"):
        return "変わる物はありませんでした"
    head = "止まっていた入れ替えを続けて済ませました" if value.get("continued") else "入れ替えました"
    return f"{head}: {value['files']} ファイル（段取り {value['batch']}）・目録の指紋 {_short(value.get('fp'))}"


def cmd_merge(args) -> int:
    root = root_of(args.register)
    value = A.merge(root, args.bundle, by=_by(args), at=_at(), base=args.base)
    if args.json:
        _print(value)
    else:
        print(_batch_line(value))
    return 0


def cmd_resume(args) -> int:
    root = root_of(args.register)
    by = _by(args)
    if K.pending(root) is None:
        value = {"pending": False}
        if args.json:
            _print(value)
        else:
            print("止まった入れ替えはありません")
        return 0
    if args.rollback:
        value = {"pending": True, "action": "rollback", **K.rollback(root, by=by, at=_at())}
        line = f"入れ替えを戻しました: {value['files']} ファイル（段取り {value['batch']}）・目録の指紋 {_short(value['fp'])}"
    else:
        value = {"pending": True, "action": "continue", **K.apply(root, by=by, at=_at()), "continued": True}
        line = _batch_line(value)
    if args.json:
        _print(value)
    else:
        print(line)
    return 0


def _pairs(items) -> dict:
    out = {}
    for item in items or []:
        name, sep, val = item.partition("=")
        if not sep or not name:
            raise RegisterRefused("register_args_invalid", "set")
        out[name] = val
    if not out:
        raise RegisterRefused("register_args_invalid", "set")
    return out


def cmd_row(args) -> int:
    root = root_of(args.register)
    by = _by(args)
    values = _pairs(args.set)
    if args.row_command == "set":
        value = A.row_set(root, args.target, values, by=by, at=_at())
    else:
        value = A.row_add(root, args.target, values, by=by, at=_at(), path=args.table)
    if args.json:
        _print(value)
    else:
        print(f"{'直しました' if args.row_command == 'set' else '足しました'}: {value['record']}（{value['path']}）。"
              f"ほかの行のバイトは変わっていません・目録の指紋 {_short(value.get('fp'))}")
    return 0


def register_parsers(verbs, run) -> None:
    p = verbs.add_parser("adopt", help="元のフォルダから台帳を作る（元には書かない・2 回めは取り込み直し）")
    p.add_argument("source", metavar="元のフォルダ")
    p.add_argument("--to", required=True, metavar="台帳のフォルダ")
    p.add_argument("--by", required=True)
    p.add_argument("--plan", default=None, metavar="範囲.json", help="取り込む物の範囲（thth.adopt_plan/1）")
    p.add_argument("--decl", default=None, metavar="宣言のフォルダ", help="種類の宣言（kinds/）と憲法を台帳の .thth/ に置く")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_adopt))

    p = verbs.add_parser("restore", help="台帳を元の場所・名前・バイトで書き出す（手元に書くだけ）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--to", required=True, metavar="先のフォルダ")
    p.add_argument("--at", default=None, metavar="控え", help="控えた前の版（目録の指紋の頭・first・last）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_restore))

    p = verbs.add_parser("diff", help="足した・変わった・退役した・消えた記録（番号が消えたら赤）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("range", metavar="前..後")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_diff))

    p = verbs.add_parser("keep", help="今の姿を控える（前の版を取り出せるように）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("--by", default=None)
    p.add_argument("--list", action="store_true", help="控えの跡を並べる")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_keep))

    p = verbs.add_parser("merge", help="束をまとめて入れる（途中で止まっても前か後のどちらかに）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("bundle", metavar="束のフォルダ")
    p.add_argument("--by", required=True)
    p.add_argument("--base", default=None, metavar="目録の指紋")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_merge))

    p = verbs.add_parser("resume", help="止まった入れ替えを続ける・戻す")
    p.add_argument("register", metavar="台帳")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--continue", dest="cont", action="store_true")
    which.add_argument("--rollback", action="store_true")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_resume))

    p = verbs.add_parser("row", help="表の 1 行を直す・足す（触っていない行は 1 バイトも変えない）")
    rows = p.add_subparsers(dest="row_command", required=True)
    for verb, metavar, help_text in (("set", "種類:番号", "1 行の欄を直す"), ("add", "種類", "1 行を足す")):
        q = rows.add_parser(verb, help=help_text)
        q.add_argument("register", metavar="台帳")
        q.add_argument("target", metavar=metavar)
        q.add_argument("--set", action="append", default=None, metavar="欄=値")
        q.add_argument("--by", required=True)
        if verb == "add":
            q.add_argument("--table", default=None, metavar="道", help="種類の表が 2 枚以上のとき、足す表")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=run(cmd_row))
