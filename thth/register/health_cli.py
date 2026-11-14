"""台帳の器 段 8 の命令（`health.py` の頭の注・指示 `docs/指示_台帳の器_段8_健康と複数のメディア_2026-11-14.md` §2・§3）。

    thth register health [<台帳>] [--thin] [--across] [--all] [--as-of <日時>] [--common <共通の台帳>] [--json]
        健康（読むだけ）: 網・判断・封・期限・決まり・共通の群ごとに、項目の数と多くて 20 件の例。
        数えられない項目は数を作らず、わけを出す。--thin は線の薄い点を全部。--across は持ち主の全部の台帳を
        またいで DOI か URL が同じ物を並べる。--all は控えた台帳ごとに 1 行（--json なら群ごとの数だけの JSON）
    thth register sisters <メディアの台帳> [--common <共通の台帳>] [--media <メディア>] [--as-of <日時>] [--json]
        姉妹リンクの候補（読むだけ）: 共通の台帳の対応の記録から、関係が same で、相手のメディアの記録が読者の
        出し口で使える対。判断は足さない（載せるかは別の「出してよいか」の判断）。相手の台帳が無ければ確かめられない

網に出ない。台帳にも THTH の側の控えにも書かない。
"""
from __future__ import annotations

import json

from . import health as H
from .adopt_cli import root_of
from .errors import RegisterRefused

GROUP_JA = {"net": "網", "judgment": "判断", "seal": "封", "deadline": "期限", "rules": "決まり", "common": "共通"}
ITEM_JA = {
    "thin": "線の薄い点（指す・指されるが 0 か 1）", "dangling": "先の無い指し", "open_questions": "開いた問い",
    "stale": "古くなった判断", "vanished": "消えた指し先への判断", "observed_only": "観測しか無い版（判断の無い状態の語）",
    "candidates": "結び直しの候補", "self_check": "書き手と同じ者の確かめ", "writer_only": "書き手だけの読み",
    "pending_reasons": "「まだ」の判断", "recheck_due": "調べ直す条件が満たされた問い",
    "baseline_only": "基準だけで通っている記録", "undeclared_values": "範囲か時点が未宣言の値",
    "provenance_review": "見直す来歴の控え", "accept_pending": "受け入れ待ちの判断",
    "foreign_stops": "よそから来て効いている止める", "erase_unfinished": "消し終わっていない消す求め",
    "review_passed": "見直す日を過ぎた記録", "until_near": "「いつまで」が近い判断（30 日以内）",
    "until_passed": "「いつまで」を過ぎた判断", "passed_in_last_generation": "過ぎたのに最後の世代に載っている記録",
    "unmatched_checks": "1 件も当たらない検査", "failed_checks": "回す途中で止まった検査",
    "unverifiable_pointers": "確かめられない指し（よその台帳）", "violations": "検査の違反",
    "take_pending": "採り入れ待ち", "common_unchecked": "共通の止めるを確かめられないまま封をした出し口",
}


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def _say(value: dict) -> None:
    print(f"台帳 {value['register']} の健康（{value['as_of']}・記録 {value['records']} 件・判断 {value['judgments']} 件"
          f"・検収の表から {value['inspections']} 件）")
    for group in H.GROUPS:
        print(f"  {GROUP_JA[group]}")
        for name, item in value["groups"][group].items():
            label = ITEM_JA.get(name, name)
            if item["count"] is None:
                print(f"    {label}: 数えられない（{item['uncountable']}）")
                continue
            line = f"    {label}: {item['count']}"
            for extra in ("by_kind", "by_reason", "by_level", "by_pointer"):
                if item.get(extra):
                    line += "（" + "・".join(f"{k} {n}" for k, n in item[extra].items()) + "）"
            print(line)
            for ex in item["examples"][:3]:
                print("      " + ", ".join(f"{k}={v}" for k, v in ex.items() if v is not None)[:160])


def _across_say(value: dict) -> None:
    print(f"  台帳をまたいで同じ出典らしい物 {len(value['same'])} 組（見る欄: {', '.join(value['fields'])}）")
    for group in value["same"][:20]:
        print(f"    {group['key']}: " + "・".join(f"{m['register']} {m['record']}" for m in group["members"]))
    for name, kinds in value["not_looked"].items():
        if kinds:
            print(f"    見ていない種類（{name}）: {', '.join(kinds)}")


def cmd_health(args) -> int:
    if args.all:
        lines = []
        for name, path in H.owner_ledgers():
            try:
                value = H.health(path, as_of=args.as_of, common_root=args.common, name=name)
                lines.append(H.summary_line(value))
            except RegisterRefused as exc:
                lines.append({"register": name, "error": exc.code})
        for line in lines:
            if args.json:
                _print(line)
            elif "error" in line:
                print(f"{line['register']}: 読めない（{line['error']}）")
            else:
                print(f"{line['register']}: " + "・".join(f"{GROUP_JA[g]} {n}" for g, n in line["groups"].items())
                      + (f"（数えられない {len(line['uncountable'])} 項目）" if line["uncountable"] else ""))
        return 0
    if not args.register:
        raise RegisterRefused("register_args_invalid", "register")
    root = root_of(args.register)
    value = H.health(root, as_of=args.as_of, common_root=args.common, thin=args.thin, name=args.register)
    if args.across:
        value["across"] = H.across_owner(root, args.register)
    if args.json:
        _print(value)
        return 0
    _say(value)
    if args.across:
        _across_say(value["across"])
    return 0


def cmd_sisters(args) -> int:
    root = root_of(args.register)
    value = H.sisters(root, name=args.register, media=args.media, common_root=args.common, as_of=args.as_of)
    if args.json:
        _print(value)
        return 0
    print(f"姉妹リンクの候補（{value['media']}・{value['as_of']}）: {len(value['candidates'])} 組。"
          "載せるかは別の「出してよいか」の判断です（器は足しません）")
    for c in value["candidates"]:
        print(f"  {c['mine']} ↔ {c['partner']['media']} {c['partner']['record']}（出し口 {', '.join(c['partner']['outlets'])}）")
    for u in value["unverifiable"]:
        print(f"  確かめられない: {u['mine']} ↔ {u['partner']['media']} {u['partner']['record']}（相手の台帳が手元に無い）")
    for n in value["not_usable"]:
        print(f"  出さない: {n['mine']} ↔ {n['partner']['media']} {n['partner']['record']}（{n['why']}）")
    return 0


def register_parsers(verbs, run) -> None:
    p = verbs.add_parser("health", help="健康（読むだけ）: 網・判断・封・期限・決まり・共通の項目の数と例")
    p.add_argument("register", nargs="?", default=None, metavar="台帳")
    p.add_argument("--thin", action="store_true", help="線の薄い点を全部並べる")
    p.add_argument("--across", action="store_true", help="持ち主の全部の台帳をまたいで同じ出典らしい物を並べる")
    p.add_argument("--all", action="store_true", help="控えた台帳ごとに 1 行")
    p.add_argument("--as-of", default=None, metavar="日時")
    p.add_argument("--common", default=None, metavar="共通の台帳")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_health))

    p = verbs.add_parser("sisters", help="姉妹リンクの候補（読むだけ・判断は足さない）")
    p.add_argument("register", metavar="メディアの台帳")
    p.add_argument("--common", default=None, metavar="共通の台帳")
    p.add_argument("--media", default=None, metavar="メディア")
    p.add_argument("--as-of", default=None, metavar="日時")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run(cmd_sisters))
