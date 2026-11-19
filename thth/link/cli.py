"""`thth link …`（店の変化の連動・設計 `docs/設計_店の変化の連動_2026-11-19.md`）。

    thth link check <台帳> [--json]           宣言（.thth/linkage.json）の形と、出し先・同意の今（効いている・期限切れ・取り消し）
    thth link consent <台帳> --by 名前 [--confirm D] [--json]
                                              宣言の同意のうち、まだ控えていない物を二段確認で THTH に控える
                                              （控えた同意だけが自動に効く・ファイルだけの同意は承認待ち）
    thth link consent <台帳> --revoke [--to 出し先] --by 名前 [--confirm D]
                                              控えた同意を二段確認で取り消す（二度と自動に使わない）
    thth link consent <台帳> --reset --by 名前 [--confirm D]
                                              壊れた控えを別の名前に退けて空から始める（二段確認・消さない）
    thth link make <台帳> [--by 名前] [--json]  今出す頃のきっかけから束を作る（出し先ごとに 同意あり／承認待ち）
    thth link show <台帳> [--json]            束と、出し先ごとの状態（出た・待ち・止めた・断った）
    thth link send <台帳> --bundle <束> --by 名前 [--confirm D] [--json]
                                              束の まだ出ていない物を 二段確認でまとめて出す（一段目は何も変えない）
    thth link send <台帳> --auto --by 名前 [--json]
                                              今効いている同意のある物だけを、確認なしで出す
    thth link run <台帳> --by 名前 [--json]    make と send --auto を続けて（VM の timer が回す）

<台帳> は `thth register add` で控えた名前。宣言は持ち主が書き、THTH は書き換えない（同意の取り消しは、その同意を
消すか `revoked_at` を書く。次の回から効く）。Google マップの出し先は、台帳の店の行（店.md）の `gbp_location` と
一致する店だけ（認める操作は二段確認）。IG の出し先は、口座の project が台帳の project と同じものだけ。Google マップに書くのは `thth gbp` と同じ認可（`thth gbp auth`）。
IG に出すのは `thth send` と同じ道（口座の台帳の `production: true`・ロック・安全装置・`sent/` の記録）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import admin_log, jst
from ..register import bundle as bundle_mod, cli as register_cli, ledger, linkage
from ..register.errors import RegisterRefused
from . import send as send_mod

STATE_JA = {"ready": "同意あり（自動で出す）", "waiting": "承認待ち", "blocked": "直す所がある（承認では通らない）",
            "sending": "出している途中", "sent": "出た",
            "unchanged": "前に出た中身と同じ（出し直さない）", "refused": "断った",
            "stopped": "止めた", "unknown": "出たか分からない", "superseded": "出し直しに替えた", "expired": "期限切れ"}
KIND_JA = {"gbp_special": "Google マップの臨時の時間", "gbp_event": "Google マップのイベントの投稿",
           "ig": "IG のフィード", "ig_story": "IG のストーリー"}


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _by(args, default: str) -> str:
    try:
        return admin_log.actor(getattr(args, "by", None) or os.environ.get("THTH_ACTOR") or default)
    except ValueError:
        raise RegisterRefused("register_args_invalid", "by") from None


def _run(handler):
    def call(args) -> int:
        try:
            ledger.check_name(args.register)
            return handler(args)
        except RegisterRefused as exc:
            return register_cli._refuse(args, exc)
    return call


def cmd_check(args) -> int:
    cfg = ledger.load(args.register)
    root = ledger.root_of(cfg)
    decl, problems = linkage.read(root, args.register)
    if decl is None:
        raise RegisterRefused("register_linkage_invalid", "linkage_missing")
    if problems:
        if not args.json:
            for word in problems:
                print(f"  .thth/{linkage.FILE}: {word} — {linkage.problem_text(word)}", file=sys.stderr)
        raise RegisterRefused("register_linkage_invalid", problems[0], problems=problems)
    now = jst.now_jst()
    decl = linkage.with_kept(decl, args.register)
    consent = [dict({k: v for k, v in c.items()}, state=linkage.consent_state(c, now), kept=c["fingerprint"] in decl["kept"])
               for c in decl["consent"]]
    unkept = linkage.unkept(decl, now)
    value = {"register": args.register, "outlets": decl["outlets"], "consent": consent,
             "unkept": len(unkept), "note": "register_link_consent_unkept" if unkept else None}
    if args.json:
        _print(value)
        return 0
    print(f"台帳 {args.register} の連動の宣言は形が合っています（出し先 {len(decl['outlets'])}・同意 {len(consent)}）")
    for o in decl["outlets"]:
        print(f"  出し先 {o['to']}: {'・'.join(o['triggers'])}")
    for c in consent:
        word = {"valid": "効いている", "expired": "期限切れ", "revoked": "取り消した", "not_yet": "まだ始まらない"}[c["state"]]
        print(f"  同意 {c['to']}: {'・'.join(c['triggers'])}（{c['until']} まで・{c['by']}・{word}"
              f"・{'控えた' if c['kept'] else '控えていない（承認待ちに倒す）'}）")
    if unkept:
        print(f"注意（register_link_consent_unkept）: 控えていない同意が {len(unkept)} 件あります。"
              f"thth link consent {args.register} --by <名前> で全文を見て控えてください")
    if not consent:
        print("  同意はありません。束はどれも 1 回ずつの二段確認で出します")
    return 0


def cmd_consent(args) -> int:
    if not (getattr(args, "by", None) or "").strip():
        raise RegisterRefused("register_args_invalid", "by")   # 同意を控える・取り消すのは誰か（必須）
    by = _by(args, "thth link consent")
    if args.revoke and args.reset:
        raise RegisterRefused("register_args_invalid", "revoke_or_reset")
    if args.reset:
        value = send_mod.reset_consent(args.register, by=by, now=jst.now_jst(), confirm=args.confirm)
        if args.json:
            _print({"ok": value["stage"] == 2, **value})
            return 0 if value["stage"] == 2 else 1
        if value["stage"] == 1:
            print(f"退けません（確認の一段目です）: {value['note']}")
            print(f"digest: {value['digest']}")
            print(f"退けるなら: thth link consent {args.register} --reset --by <名前> --confirm {value['digest']}")
            return 1
        print(f"控えを {value['aside'] or '（無し）'} に退けました。thth link consent で控え直してください")
        return 0
    value = send_mod.keep_consent(args.register, by=by, now=jst.now_jst(), confirm=args.confirm, revoke=args.revoke,
                                  to=args.to)
    if value["stage"] == 1:
        if args.json:
            _print({"ok": False, **value})
            return 1
        verb = "取り消しません" if args.revoke else "控えません"
        print(f"{verb}（確認の一段目です）: 台帳 {args.register} の同意 {len(value['consent'])} 件")
        print(f"注意: {value['note']}")
        for c in value["consent"]:
            print(f"  同意 {c['to']}: {'・'.join(c['triggers'])}（{c['until']} まで・書いた人 {c['by']}・{c.get('at') or ''}"
                  + (f"・取り消し {c['revoked_at']}" if c.get("revoked_at") else "") + "）")
        print(f"digest: {value['digest']}")
        again = " --revoke" + (f" --to {args.to}" if args.to else "") if args.revoke else ""
        print(f"{'取り消す' if args.revoke else '控える'}なら: thth link consent {args.register}{again} --by <名前> "
              f"--confirm {value['digest']}")
        return 1
    if args.json:
        _print({"ok": True, **value})
        return 0
    for c in value["revoked"]:
        print(f"取り消した: 同意 {c['to']}: {'・'.join(c['triggers'])}（二度と自動に使いません）")
    if not value["kept"] and not value["revoked"]:
        print(f"台帳 {args.register}: 控える同意はありません（どれも控えてあるか、期限切れ・取り消し済みです）")
    for c in value["kept"]:
        print(f"控えた: 同意 {c['to']}: {'・'.join(c['triggers'])}（{c['until']} まで）")
    return 0


def _bundle_lines(b) -> list:
    lines = [f"束 {b['id']}（行 {b['row']}・{b['trigger']}・出さない期限 {b['not_after']}）"
             + ("（片づけの束: 前に書いた日を外すだけ）" if b.get("clear_only") else "")
             + (f" ← {b['replaces']} の出し直し" if b.get("replaces") else "")]
    for item in b["items"]:
        tail = f"（{item['code']}{'・' + item['detail'] if item.get('detail') else ''}）" if item.get("code") else ""
        lines.append(f"  {STATE_JA[item['state']]}\t{item['to']}（{KIND_JA[item['kind']]}）{tail}"
                     + (f"　外す日: {'・'.join(item['clear'])}" if item.get("clear") else "")
                     + ("　行が見つからないので、同意があっても承認待ち" if item.get("hold") else ""))
    return lines


def cmd_make(args) -> int:
    by = _by(args, "thth link make")
    made = send_mod.make(args.register, by=by, now=jst.now_jst())
    if args.json:
        _print({"register": args.register, "made": made})
        return 0
    if not made:
        print(f"台帳 {args.register}: 新しく作る束はありません（今出す頃のきっかけが無いか、同じ変化の束が既にあります）")
        return 0
    for b in made:
        for line in _bundle_lines(b):
            print(line)
    print("次の一手: 承認待ちは thth link send <台帳> --bundle <束> --by <名前> で中身を見てから出せます")
    return 0


def cmd_show(args) -> int:
    ledger.load(args.register)
    bundles = bundle_mod.read(args.register)
    if args.json:
        _print({"register": args.register, "bundles": bundles})
        return 0
    if not bundles:
        print(f"台帳 {args.register}: 束はまだありません（thth link make で作れます）")
    for b in bundles:
        for line in _bundle_lines(b):
            print(line)
    return 0


def _send_out(args, value) -> int:
    if value.get("stage") == 1:
        if args.json:
            _print({"ok": False, **value})
            return 1
        print(f"出しません（確認の一段目です）: 束 {value['bundle']}")
        for item in value["items"]:
            print(f"  出す: {item['to']}（{KIND_JA[item['kind']]}）")
            for text_line in (item.get("text") or "").splitlines():
                print(f"    {text_line}")
            if item.get("clear"):
                print("    外す日: " + "・".join(item["clear"]) + "（THTH が前に書いた臨時の時間を Google から外す）")
            if item.get("days"):
                print("    臨時の時間: " + "・".join(d["date"] + (" 休み" if d["closed"] else f" {d['open']}〜{d['close']}")
                                              for d in item["days"]))
            if item.get("event"):
                print(f"    イベント: {item['event']['title']}（{item['event']['start']}〜{item['event']['end']}）")
        for stop in value["stops"]:
            print(f"  出さない: {stop['to']}（{KIND_JA[stop['kind']]}・{stop['code']}）")
        for note in value["notes"]:
            print(f"  注意: {note}")
        print(f"digest: {value['digest']}")
        print(f"出すなら: thth link send {args.register} --bundle {value['bundle']} --by <名前> --confirm {value['digest']}")
        return 1
    if args.json:
        _print({"ok": True, **value})
        return 0
    if not value["items"]:
        print(f"台帳 {args.register}: 今出す物はありません（承認待ち {value['waiting']} 件）")
    for row in value["items"]:
        tail = f"（{row['code']}{'・' + row['detail'] if row.get('detail') else ''}）" if row.get("code") else ""
        print(f"{STATE_JA[row['state']]}\t{row['bundle']} {row['to']}（{KIND_JA[row['kind']]}）{tail}")
    return 0


def cmd_send(args) -> int:
    by = _by(args, "thth link send")
    value = send_mod.send(args.register, by=by, now=jst.now_jst(), bundle_id=args.bundle, auto=args.auto,
                          confirm=args.confirm, transport=getattr(args, "transport", None),
                          adapter_factory=getattr(args, "adapter_factory", None))
    return _send_out(args, value)


def cmd_run(args) -> int:
    by = _by(args, "thth link run")
    now = jst.now_jst()
    made = send_mod.make(args.register, by=by, now=now)
    value = send_mod.send(args.register, by=by, now=now, auto=True, transport=getattr(args, "transport", None),
                          adapter_factory=getattr(args, "adapter_factory", None))
    value["made"] = [b["id"] for b in made]
    return _send_out(args, value)


def register(sub) -> None:
    """`build_parser()` に `link` を足す（cli.py に足すのはこの呼び出しだけ）。"""
    p = sub.add_parser("link", help="店の変化の連動（台帳の 1 つの変化を束にして、マップ・IG のフィード・ストーリーへ）",
                       formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    verbs = p.add_subparsers(dest="link_verb", required=True)
    for verb, help_text, func in (("check", "宣言の形と、出し先・同意の今（読むだけ）", cmd_check),
                                  ("show", "束と出し先ごとの状態（読むだけ）", cmd_show)):
        q = verbs.add_parser(verb, help=help_text)
        q.add_argument("register", metavar="台帳")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=_run(func))
    q = verbs.add_parser("consent", help="宣言の同意を二段確認で控える・取り消す（控えた同意だけが自動に効く）")
    q.add_argument("register", metavar="台帳")
    q.add_argument("--revoke", action="store_true")
    q.add_argument("--to", default=None, metavar="出し先")
    q.add_argument("--reset", action="store_true")
    q.add_argument("--confirm", default=None, metavar="D")
    q.add_argument("--by", default=None)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=_run(cmd_consent))
    q = verbs.add_parser("make", help="今出す頃のきっかけから束を作る")
    q.add_argument("register", metavar="台帳")
    q.add_argument("--by", default=None)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=_run(cmd_make))
    q = verbs.add_parser("send", help="束を出す（--bundle は二段確認・--auto は同意のある物だけ確認なしで）")
    q.add_argument("register", metavar="台帳")
    q.add_argument("--bundle", default=None, metavar="束")
    q.add_argument("--auto", action="store_true")
    q.add_argument("--confirm", default=None, metavar="D")
    q.add_argument("--by", default=None)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=_run(cmd_send))
    q = verbs.add_parser("run", help="make と send --auto を続けて（VM の timer が回す）")
    q.add_argument("register", metavar="台帳")
    q.add_argument("--by", default=None)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=_run(cmd_run))
