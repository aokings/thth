"""`thth conflict <account> [<dl>] [show|check|resolve …]`（設計 4.0 S5 §5.2）。中身は `hatena_conflict`。

- `thth conflict <口座> [--json]`: `conflict` と `no_follow` の配信の一覧（読むだけ・HTTP 無し）。
- `thth conflict <口座> <dl> show`: はてなの今の本文を GET 1 回で読み、最後に送った本文との差分（台帳は書かない）。
- `thth conflict <口座> check [<dl>] [--limit 20]`: `published` の配信を GET して台帳と比べる（違えば conflict）。
- `thth conflict <口座> <dl> resolve (--adopt|--gone|--no-follow) --by <名前> [--confirm <digest>]`: 2 段で解く。
"""
from __future__ import annotations

import json
import os
import sys

from . import accounts as accounts_mod
from . import hatena_entry
from . import lock as lock_mod

ACTIONS = ("show", "check", "resolve")


def register(sub) -> None:
    p = sub.add_parser("conflict", help="はてなの配信の衝突（外での手直し）を見る・確かめる・解く（4.0 S5）")
    p.add_argument("account")
    p.add_argument("rest", nargs="*", metavar="[<dl>] [show|check|resolve]",
                   help="配信 id と操作（省けば一覧）")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--adopt", action="store_true", help="はてなの側を正にする（今の本文を取り込む）")
    choice.add_argument("--gone", action="store_true", help="外で消えていた（台帳を deleted に）")
    choice.add_argument("--no-follow", action="store_true", help="この配信は記事の修正に追従しない")
    p.add_argument("--by", default=None, help="誰が決めたか（resolve で必須）")
    p.add_argument("--confirm", default=None, help="1 段目が表示した digest")
    p.add_argument("--limit", type=int, default=20, help="check で確かめる配信の数（既定 20）")
    p.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd)


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(args, rc, code, message) -> int:
    if args.json:
        _print({"ok": False, "error": code, "message": message})
    else:
        print(f"{code}: {message}", file=sys.stderr)
    return rc


def _parse_rest(rest):
    """`[<dl>] [show|check|resolve]` か `check [<dl>]`。`(delivery, action)`。"""
    words = list(rest)
    if not words:
        return None, None
    if words[0] in ACTIONS:
        action = words.pop(0)
        return (words[0] if words else None), action
    delivery = words.pop(0)
    action = words.pop(0) if words else "show"
    if words or action not in ACTIONS:
        raise ValueError("invalid_arguments")
    return delivery, action


def cmd(args) -> int:
    from . import hatena_conflict
    try:
        accounts_mod.validate_name(args.account)
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        return _fail(args, 2, "account_unreadable", str(exc))
    if not hatena_entry.is_hatena(cfg):
        return _fail(args, 2, "not_hatena", "はてなブログの口座だけです（配信の台帳を持つ口座）")
    try:
        delivery, action = _parse_rest(args.rest)
    except ValueError:
        return _fail(args, 2, "invalid_arguments", "thth conflict <口座> [<dl>] [show|check|resolve] の形です")
    try:
        if action is None:
            return _list(args, hatena_conflict.listing(args.account))
        if action == "show":
            if delivery is None:
                return _fail(args, 2, "delivery_required", "show には配信 id が要ります")
            return _show(args, hatena_conflict.show(args.account, cfg, delivery))
        if action == "check":
            return _check(args, hatena_conflict.check(args.account, cfg, delivery, limit=args.limit, wait=args.wait))
        return _resolve(args, cfg, delivery)
    except hatena_conflict.ConflictRefused as exc:
        return _fail(args, exc.rc, exc.code, exc.message)


def _detail(conflict) -> str:
    if not conflict:
        return ""
    labels = "/".join(hatena_entry.DETAIL_LABELS.get(d, str(d)) for d in (conflict.get("detail") or []))
    return f"{conflict.get('reason')}{'・' + labels if labels else ''}"


def _list(args, payload) -> int:
    if args.json:
        _print(payload)
        return 0
    print(f"衝突  {args.account}  conflict {len(payload['conflicts'])} 件・追従しない {len(payload['no_follow'])} 件")
    for row in payload["conflicts"]:
        detail = "/".join(hatena_entry.DETAIL_LABELS.get(d, str(d)) for d in (row.get("detail") or []))
        print(f"  {row['delivery']}  {row['article']}  conflict（{row['reason']}{'・' + detail if detail else ''}・"
              f"{hatena_entry.short_at(row['since'])} から）  {row.get('published_url') or '—'}")
        print(f"    見る: thth conflict {args.account} {row['delivery']} show")
    for row in payload["no_follow"]:
        print(f"  {row['delivery']}  {row['article']}  no_follow（{hatena_entry.short_at(row['since'])} から）")
    if payload["unreadable"]:
        print("読めなかった台帳: " + "・".join(u["delivery"] for u in payload["unreadable"]))
    return 0


def _show(args, payload) -> int:
    if args.json:
        _print(payload)
        return 0
    head = f"{payload['delivery']}  {payload['state']}"
    if payload.get("conflict"):
        head += f"（{_detail(payload['conflict'])}・{hatena_entry.short_at(payload['conflict'].get('at'))}）"
    print(f"{args.account}  {head}  {payload.get('published_url') or '—'}")
    if payload["gone"]:
        print("  はてなに記事がありません（404・外で消えた）。"
              f"thth conflict {args.account} {payload['delivery']} resolve --gone --by <名前>")
        return 0
    r = payload["remote"]
    print(f"  はてなの今の本文: readback_sha {r['readback_sha'][:12]}…（content_type {r['content_type'] or '不明'}）")
    if r["title_differs"]:
        print(f"  題: 台帳 {payload['ledger_title']} → はてな {r['title']}")
    if r["categories_differs"]:
        print(f"  カテゴリ: 台帳 {'・'.join(payload['ledger_categories'] or [])} → はてな {'・'.join(r['categories'] or [])}")
    if r["diff_lines"] is None:
        print("  最後に送った本文の記録が無いので差分は出せません")
    else:
        print("  ---- 本文の差分（最後に送った本文 → はてなの今の本文） ----")
        for line in r["diff_lines"]:
            print("  " + line)
        print("  ---- ここまで ----")
    print(f"  はてなの側を正にする: thth conflict {args.account} {payload['delivery']} resolve --adopt --by <名前>"
          "（そのあと更新を承認し直すと、記事で上書きできます）")
    return 0


RESULT_WORDS = {"same": "同じ", "readback_changed": "外で手直しされています（conflict にしました）",
                "entry_missing": "はてなにありません（conflict・entry_missing にしました）",
                "readback_filled": "台帳に無かった欄を埋めました"}


def _check(args, payload) -> int:
    if args.json:
        _print(payload)
        return 0
    print(f"確かめた  {args.account}  {payload['checked']} 件")
    for row in payload["results"]:
        detail = "/".join(hatena_entry.DETAIL_LABELS.get(d, d) for d in row.get("detail") or [])
        word = RESULT_WORDS.get(row["result"], row["result"])
        print(f"  {row['delivery']}  {word}{'（' + detail + '）' if detail else ''}")
    return 0


def _resolve(args, cfg, delivery) -> int:
    from . import hatena_conflict
    if delivery is None:
        return _fail(args, 2, "delivery_required", "resolve には配信 id が要ります")
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _fail(args, 1, "by_required", "--by <名前> を付けてください（誰が決めたかを記録します）")
    resolution = "adopt" if args.adopt else "gone" if args.gone else "no_follow" if args.no_follow else None
    if resolution is None:
        return _fail(args, 1, "resolution_required", "--adopt・--gone・--no-follow のどれかを付けてください")
    payload = hatena_conflict.resolve(args.account, cfg, delivery, resolution, by=by, confirm=args.confirm,
                                      wait=args.wait)
    flag = {"adopt": "--adopt", "gone": "--gone", "no_follow": "--no-follow"}[resolution]
    command = (f"thth conflict {args.account} {delivery} resolve {flag} --by {json.dumps(by, ensure_ascii=False)} "
               f"--confirm {payload['digest']}")
    if args.json:
        _print(dict(payload, command=command) if not payload["resolved"] else payload)
        return 0 if payload["resolved"] else 1
    if not payload["resolved"]:
        print(f"解きません（確認の一段目です）: {args.account} {delivery}（{payload['state']}"
              f"{'・' + _detail(payload['conflict']) if payload.get('conflict') else ''}）")
        if resolution == "adopt":
            r = payload["remote"]
            print(f"  決めること: はてなの今の本文を正にする（readback_sha {r['readback_sha'][:12]}…・"
                  f"送った本文から +{r['diff_added']} 行 −{r['diff_removed']} 行）。記事（repo）は変えません")
            print("  承認済みの更新の原稿は approval_stale になります。承認し直すと 1 段目の差分が"
                  "「はてなの今の本文 → 記事」になります（上書きの承認）")
        elif resolution == "gone":
            print("  決めること: 外で消えていた。台帳を deleted にします（sent/ は触りません）")
        else:
            print("  決めること: この配信は記事の修正に追従しない（no_follow）。更新は断り、削除はできます")
        print(f"  by: {by}")
        print(f"digest: {payload['digest']}")
        print(f"解くなら: {command}")
        return 1
    print(f"解きました: {args.account} {delivery}（{payload['resolution_word']}・{by}）")
    if resolution == "adopt":
        print("  はてなの今の本文を取り込みました。上書きするなら更新の原稿を承認し直してください")
    elif resolution == "gone":
        print("  承認済みの更新・削除の原稿があれば thth revoke で draft に戻してください")
    if not payload.get("admin_log"):
        print("  ⚠ 変更ログに書けませんでした（解いてあります）")
    return 0
