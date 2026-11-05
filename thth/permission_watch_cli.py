"""`thth admin meta`（権限の不使用の見張り・口の中身は `thth/permission_watch.py`）。

- `watch`: 権限ごとの最後に成功した日・期限の台帳・広告の段の数（読むだけ）。
- `keepalive`: 読むだけの権限に最小の読み（timer が 1 日 1 回打つ・`thth systemd --meta-keepalive`）。
- `ledger`・`set`: 人が読んで入れる値（Data Use Checkup の期日・広告の段・Advanced の権限）。

**CLI だけ・MCP には出さない**（管理者の口）。
"""
from __future__ import annotations

import json

from . import permission_watch as watch
from . import refusal


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _render_watch(view) -> None:
    for row in view["permissions"]:
        if not row["watched"] and not row["holders"]:
            continue
        last = row["last_success_at"] or ("記録なし・鍵の取得から数える" if row["counted_from"] else "記録なし")
        idle = "—" if row["days_idle"] is None else f"{row['days_idle']} 日"
        mark = "見張る" if row["watched"] else "Standard"
        note = f"  最小の読みの失敗: {row['keepalive_error']}" if row["keepalive_error"] else ""
        print(f"{row['permission']}（{row['media']}・{row['kind']}・{mark}）: {idle}・{row['stage']}・{last}{note}")
    for row in view["accounts"]:
        print(f"{row['account']}: {row['permission']} {row['days_idle']} 日（利用者ごとの 90 日）")
    _render_ledger(view)
    for row in view["notices"]:
        print(f"知らせ {row['code']}: {row['what']}")
        print(f"  {refusal.FIX}{row['fix']}")


def _render_ledger(view) -> None:
    duc = view["duc"]
    print("Data Use Checkup: " + (f"{duc['due']}（残り {duc['days_left']} 日）" if duc["due"]
                                  else "期日が台帳にありません（thth admin meta set duc_due <日付> --by <名前>）"))
    tier = view["ads_tier"]
    rate = "—" if tier["error_rate"] is None else f"{tier['error_rate']:.1%}"
    print(f"広告の段: {tier['tier']}・15 日で {tier['calls_15d']} 回（誤り {tier['errors_15d']}）・"
          f"直近 {tier['last_n']} 回のエラー率 {rate}・数え始め {tier['counting_since'] or '—'}")
    for row in view["data_access"]:
        print(f"データに触れる期限 {row['account']}: {row['expires_at']}（残り {row['remaining_days']:.1f} 日）")


def cmd_watch(args) -> int:
    view = watch.build()
    if args.json:
        _print_json(view)
    else:
        _render_watch(view)
    return 0


def cmd_ledger(args) -> int:
    view = watch.build()
    if args.json:
        _print_json({key: view[key] for key in ("duc", "ads_tier", "data_access", "cannot_say")})
    else:
        _render_ledger(view)
    return 0


def cmd_set(args) -> int:
    try:
        row = watch.set_ledger(args.key, args.value, by=args.by)
    except watch.LedgerRefused as e:
        return refusal.emit(args, e.code)
    if args.json:
        _print_json({"ok": True, "key": args.key, "value": row[args.key]})
    else:
        print(f"台帳に書きました: {args.key}（{args.by}）")
    return 0


def cmd_keepalive(args) -> int:
    result = watch.keepalive(dry_run=args.dry_run)
    if args.json:
        _print_json(result)
    else:
        for row in result["results"]:
            detail = row.get("error") or row.get("reason") or ""
            print(f"{row['account']}: {row['permission']}: {row['result']}" + (f"（{detail}）" if detail else ""))
    return 1 if any(row["result"] == "error" for row in result["results"]) else 0


def register_admin(commands) -> None:
    parser = commands.add_parser("meta", help="Meta の権限の不使用の見張り（最後に成功した日・期限の台帳・広告の段）")
    operations = parser.add_subparsers(dest="meta_operation", required=True)
    p = operations.add_parser("watch", help="権限ごとの最後に成功した日と知らせを見る（読むだけ）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_watch)
    p = operations.add_parser("keepalive", help="使っていない読むだけの権限に最小の読みを打つ（書く権限は打たない）")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help="打つ先を出すだけ（網に出ない）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_keepalive)
    p = operations.add_parser("ledger", help="期限の台帳と広告の段の数を見る（読むだけ）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ledger)
    p = operations.add_parser("set", help="台帳の値を書く（duc_due・ads_tier・advanced）")
    p.add_argument("key", help="duc_due（YYYY-MM-DD）・ads_tier（limited か full）・advanced（権限をカンマで全部）")
    p.add_argument("value")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_set)
