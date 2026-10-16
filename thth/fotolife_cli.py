"""`thth fotolife <主体> [list|show <sha256>] [--json]`——主体の画像の台帳を見る（設計 4.0 S3 §12.4）。

**読むだけ・運営者の CLI だけ**（MCP の道具にも thth.me にも出さない）。フォトライフの API は呼ばない。
書く口（消す・直す）は作らない——消すのは人がフォトライフの画面で消し、次の run の確かめ（GET が 404）が
台帳を片づける（§4.3）。
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import hatena_fotolife
from . import principals

SCHEMA = "thth.fotolife/1"


def build(name) -> dict:
    principals.load_principal(name)   # 無い主体は断る（台帳の置き場を推測で作らない）
    rows, broken = hatena_fotolife.list_records(name)
    rows.sort(key=lambda r: (r.get("uploaded_at") or r.get("sent_at") or "", r["sha256"]))
    return {"schema": SCHEMA, "principal": name, "n": len(rows), "images": rows,
            "unreadable": broken,
            "cannot_say": ["fotolife_ledger_unreadable"] if broken else [],
            "limitations": ["読むだけ。フォトライフの API は呼ばず、THTH の画像の台帳だけを並べる",
                            "THTH が上げた画像だけ。人が画面から上げた画像は入らない"]}


def _line(row) -> str:
    first = row.get("first_seen") or {}
    when = row.get("uploaded_at") or row.get("sent_at") or "時刻不明"
    return (f"  {row['sha256'][:12]}  {row['state']}  {row.get('fotolife_id') or '-'}  {when}"
            f"  {first.get('account') or '-'}/{first.get('delivery') or '-'}  {first.get('path') or ''}")


def cmd(args) -> int:
    verb = args.verb or "list"
    try:
        if verb == "show":
            if not args.sha256:
                print("show には sha256（64 桁）が要ります", file=sys.stderr)
                return 2
            principals.load_principal(args.principal)
            try:
                record = hatena_fotolife.read(args.principal, args.sha256)
            except hatena_fotolife.LedgerUnreadable as exc:
                print(f"台帳が読めません（{exc}）", file=sys.stderr)
                return 2
            if record is None:
                print("台帳にありません", file=sys.stderr)
                return 1
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        payload = build(args.principal)
    except accounts_mod.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"フォトライフの画像の台帳  主体 {payload['principal']}  {payload['n']} 件")
    for row in payload["images"]:
        print(_line(row))
    if payload["unreadable"]:
        print("読めなかったもの: " + "・".join(sha[:12] for sha in payload["unreadable"]))
    return 0


def register(sub) -> None:
    p = sub.add_parser("fotolife", help="主体のフォトライフの画像の台帳を見る（読むだけ・CLI だけ）")
    p.add_argument("principal", help="主体の名前")
    p.add_argument("verb", nargs="?", choices=("list", "show"), default="list")
    p.add_argument("sha256", nargs="?", default=None, help="show のときの画像の sha256（64 桁）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd)
