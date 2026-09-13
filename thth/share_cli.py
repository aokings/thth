"""`thth share on|off|status|log`（設計 v2 §3・裁定 §7-3「既定 off」）。

**`build_parser()` には 1 行しか足さない**（`share_cli.register(sub)`）。
`thth/cli.py` は並行して別の Track が触っているので、口はこちら側に閉じる。

**この口は何も送らない。** 泉のサーバ（v2-5）は v2.0.0 の範囲に無い（§7）。
`on` にしても起きるのは `state/share/outbox/` に積み始めることだけで、
`log` で**積んだ全部が 1 行残らず読める**。
"""
from __future__ import annotations

import json

from . import share as share_mod

ACTIONS = ("status", "on", "off", "log", "sync")


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser(
        "share", help="泉に落とす水を手元に積むかどうか（既定 off・送り先はまだ無い）")
    p.add_argument("action", nargs="?", default="status", choices=list(ACTIONS),
                   help="status=いまの状態 / on=積み始める / off=止める / "
                        "log=積んだ全部を読む / sync=台帳から積み直す")
    p.add_argument("--by", default=None,
                   help="on / off を切り替えた人（記録に残る・泉には出ない）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_share)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_share(args) -> int:
    try:
        return _cmd_share(args)
    except share_mod.ShareError as e:
        # **黙って間違えない**（loud reject）。
        if getattr(args, "json", False):
            _print_json({"error": "share_broken", "detail": str(e)})
        else:
            print(f"share: {e}")
        return 2


def _cmd_share(args) -> int:
    action = getattr(args, "action", None) or "status"

    if action == "on":
        share_mod.set_enabled(True, by=args.by or "")
        結果 = share_mod.sync()
        st = share_mod.status()
        if args.json:
            _print_json({"enabled": True, "synced": 結果, "status": st})
            return 0
        print("share: on。**ここから積み始めます**（送ってはいません——"
              "泉のサーバはまだありません）。")
        print(f"  観測者の仮名: {st['observer']}（この 1 つだけが泉に出る名前です）")
        print(f"  置き場:       {st['outbox']}")
        print(f"  積んだ件数:   {st['queued']} 件"
              f"（今回 {結果['added']} 件足しました）")
        for s in 結果.get("skipped") or []:
            print(f"  積めなかったもの: {s['what']}（{s['why']}）")
        print("  積んだ全部を読む: thth share log")
        print("  止める:           thth share off")
        return 0

    if action == "off":
        share_mod.set_enabled(False, by=args.by or "")
        st = share_mod.status()
        if args.json:
            _print_json({"enabled": False, "status": st})
            return 0
        print("share: off。**ここから 1 バイトも積みません。**")
        print(f"  すでに積んだ {st['queued']} 件はそのまま手元に残ります"
              f"（{st['outbox']}）。消すのはあなたの手です。")
        return 0

    if action == "sync":
        結果 = share_mod.sync()
        if args.json:
            _print_json(結果)
            return 0
        if not 結果["enabled"]:
            print("share は off です（`thth share on` で始まります）。何も積みませんでした。")
            return 0
        print(f"share: {結果['added']} 件を足しました"
              f"（観測 {結果['observations']}・スレッドの形 {結果['shapes']}・"
              f"打ち消し {結果['retractions']}）。")
        for s in 結果.get("skipped") or []:
            print(f"  積めなかったもの: {s['what']}（{s['why']}）")
        return 0

    if action == "log":
        rows, broken = share_mod.log_rows()
        if args.json:
            _print_json({"rows": rows, "broken": broken})
            return 0
        if not rows and not broken:
            print("積んだものはまだありません（off のままならこれが正しい状態です）。")
            return 0
        # **全部出す。** 端折ると「読める」と言えなくなる。
        for r in rows:
            print(json.dumps(r, ensure_ascii=False, sort_keys=True))
        print(f"--- {len(rows)} 件（これで全部です）")
        for b in broken:
            print(f"--- 読めなかった行: {b}")
        return 0

    st = share_mod.status()
    if args.json:
        _print_json(st)
        return 0
    print(f"share: {'on' if st['enabled'] else 'off'}"
          f"{'（既定）' if not st['enabled'] and not st['changed_at'] else ''}")
    if st["changed_at"]:
        print(f"  切り替えた日時: {st['changed_at']}")
    print(f"  観測者の仮名: {st['observer'] or '（まだ作っていません）'}")
    print(f"  置き場:       {st['outbox']}")
    print(f"  積んだ件数:   {st['queued']} 件 / {st['bytes']} バイト")
    for schema, n in sorted((st.get("by_schema") or {}).items()):
        print(f"    {schema}: {n}")
    for b in st.get("broken") or []:
        print(f"  読めなかった行: {b}")
    print(f"  送り先:       {st['destination_note']}")
    if not st["enabled"]:
        print("  始めるなら: thth share on（何が積まれるかは `thth share log` で全部読めます）")
    return 0
