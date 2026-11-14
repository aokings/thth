"""台帳の器 段 5 の消す求めの命令（`erase.py` の頭の注・指示 `docs/指示_台帳の器_段5_跡と消す求め_2026-11-14.md` §3）。

    thth register erase request <台帳> <種類:番号> [--field <欄>] [--unit <単位>] --by <名前>
                                --why personal|rights|mistake|other [--json]
        求めを置く（何も消さない・出口にも効かない。理由は種類の語だけで、文を取らない）
    thth register erase scope <台帳> <求め> --versions all|current|<控えの名>... [--field <欄>] --by <名前>
                              [--confirm D] [--json]
        範囲の判断（二段: 1 回めは消える範囲と再現できなくなる世代の見積りと印・印を付けて打ち直すと置く）。
        範囲は求めより広げられない
    thth register erase run <台帳> <求め> --by <名前> [--json]
        実行（台帳のフォルダの中の、今の本文と器の記録だけ。止まっても打ち直せば続きから）。最後に台帳の全部の
        ファイルを読んで確かめる。残っていれば rc は 0 にならない
    thth register erase status <台帳> [<求め>] [--json]
        場所ごとの結果と残り・再現できない世代（求めを省くと、求めの一覧と消し終わっていない数）
    thth register erase done <台帳> <求め> --place <場所> --result erased|kept --by <名前> [--json]
        持ち主が確かめた場所に結果を 1 つ付ける。全部に結果が付いて初めて「消し終わり」

網に出ない。
"""
from __future__ import annotations

import json

from .. import admin_log, jst
from . import erase as E
from .adopt_cli import root_of
from .errors import RegisterRefused

STATE_JA = {"erased": "消した", "nothing": "無かった", "not_erased": "消していない", "not_checked": "確かめていない",
            "kept": "消さないと決めた", "running": "途中", "not_started": "まだ"}


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def _at() -> str:
    return jst.now_jst().isoformat(timespec="seconds")


def _by(args) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None))
    except ValueError:
        raise RegisterRefused("register_args_invalid", "by") from None


def _show(value: dict) -> None:
    state = "消し終わり" if value.get("state") == "finished" else "消し終わっていない"
    print(f"消す求め {value['request']}（{value.get('record')}・欄 {value.get('field') or '記録ごと'}）: {state}")
    for place in E.PLACES:
        entry = (value.get("places") or {}).get(place) or {"state": "not_started"}
        print(f"  {E.PLACE_JA[place]}: {STATE_JA.get(entry.get('state'), entry.get('state'))}"
              + (f"（{entry['note']}）" if entry.get("note") and entry.get("state") == "not_checked" else ""))
    for u in value.get("unreproducible") or []:
        print(f"  再現できない世代: {u['outlet']} の世代 {u['generations'][0]}〜{u['generations'][1]}（多めに言う）")
    if value.get("rewritten_lines"):
        print(f"  印の形に書き換えた判断の行: {len(value['rewritten_lines'])} 件")
    if value.get("state") != "finished":
        print("  過去の判定を全部は再現できません。確かめていない場所は、持ち主が確かめて "
              "thth register erase done で 1 つずつ結果を付けてください")


def cmd_request(args) -> int:
    value = E.request(root_of(args.register), args.record, field=args.field, unit=args.unit, by=_by(args), at=_at(),
                      why=args.why)
    if args.json:
        _print(value)
        return 0
    print(f"消す求めを置きました: {value['request']}（{value['record']}・欄 {value['field'] or '記録ごと'}）。"
          "まだ何も消していません。出口にも効きません（出すのを止めたいなら thth register judge --stop を別に足す）")
    print(f"次に範囲を決めます: {value['next']}")
    return 0


def cmd_scope(args) -> int:
    value = E.scope(root_of(args.register), args.request, args.versions, by=_by(args), at=_at(), confirm=args.confirm,
                    field=args.field, record=args.record)
    if args.json:
        _print(value)
        return 1 if value["stage"] == 1 else 0
    est = value["estimate"]
    if value["stage"] == 1:
        print(f"範囲の判断の 1 段めです（{value['request']}・版 {' '.join(value['scope']['versions'])}）。消える範囲:")
    else:
        print(f"範囲の判断を置きました（{value['request']}）。thth register erase run で実行します。消える範囲:")
    places = est["places"]
    print(f"  今の本文: {'範囲に入る' if places['current_text']['in_range'] else '範囲に入らない'}")
    print(f"  前の版: {places['kept_objects']['objects']} 個（{places['kept_objects']['note']}）")
    print(f"  判断の行: {places['judgments']['lines']} 行を印の形に書き換える")
    for u in est["unreproducible"]:
        print(f"  再現できなくなる世代: {u['outlet']} の世代 {u['generations'][0]}〜{u['generations'][1]}")
    if value["stage"] == 1:
        print(f"持ち主が見て、よいと言ったら --confirm {value['digest']} を付けて打ち直してください")
        return 1
    return 0


def cmd_run(args) -> int:
    value = E.run(root_of(args.register), args.request, by=_by(args), at=_at())
    check = value["places"].get("self_check") or {}
    if check.get("state") == "not_erased":
        raise RegisterRefused("register_erase_left", "self_check", request=value["request"],
                              found_in=sorted(check.get("found_in") or {}), places={
                                  p: (v or {}).get("state") for p, v in value["places"].items()})
    if args.json:
        _print(value)
        return 0
    _show(value)
    return 0


def cmd_status(args) -> int:
    root = root_of(args.register)
    value = E.status(root, args.request)
    if args.json:
        _print(value)
        return 0
    if args.request is None:
        print(f"消す求め {len(value['requests'])} 件・消し終わっていない {value['unfinished']} 件")
        for x in value["requests"]:
            print(f"  {x['request']}\t{x['record']}\t{x['field'] or '記録ごと'}\t"
                  f"{'消し終わり' if x['state'] == 'finished' else '消し終わっていない'}")
        return 0
    _show(value)
    return 0


def cmd_done(args) -> int:
    value = E.done(root_of(args.register), args.request, args.place, by=_by(args), at=_at(), outcome=args.result)
    if args.json:
        _print(value)
        return 0
    _show(value)
    return 0


def register_parsers(verbs, run) -> None:
    parent = verbs.add_parser("erase", help="消す求め（求め・範囲の判断・実行・結果と残り）")
    sub = parent.add_subparsers(dest="erase_command", required=True)

    p = sub.add_parser("request", help="求めを置く（何も消さない）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("record", metavar="種類:番号")
    p.add_argument("--field", default=None, metavar="欄")
    p.add_argument("--unit", default=None, metavar="単位")
    p.add_argument("--why", required=True, metavar="理由の種類")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_request))

    p = sub.add_parser("scope", help="範囲の判断（二段）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("request", metavar="求め")
    p.add_argument("--versions", nargs="+", required=True, metavar="版")
    p.add_argument("--field", default=None, metavar="欄")
    p.add_argument("--record", default=None, metavar="種類:番号")
    p.add_argument("--confirm", default=None, metavar="D")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_scope))

    p = sub.add_parser("run", help="実行（続きからも）")
    p.add_argument("register", metavar="台帳")
    p.add_argument("request", metavar="求め")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_run))

    p = sub.add_parser("status", help="結果と残り")
    p.add_argument("register", metavar="台帳")
    p.add_argument("request", nargs="?", default=None, metavar="求め")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_status))

    p = sub.add_parser("done", help="持ち主が確かめた場所に結果を付ける")
    p.add_argument("register", metavar="台帳")
    p.add_argument("request", metavar="求め")
    p.add_argument("--place", required=True, choices=E.PLACES)
    p.add_argument("--result", required=True, choices=E.DONE_RESULTS)
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_done))
