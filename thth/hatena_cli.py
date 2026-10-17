"""`thth hatena collect <口座> [--delivery <dl>] [--by <名前>]`（設計 4.0 S6 §4.4・CLI だけ）。

`thth run` の末尾と同じ反応の採取を今 1 回。刻みを跨いでいない配信は読まずに「次は 24h・10/21 08:00
ごろ」と言う。`--force` は作らない（刻みの外の値は比べられないので台帳に入れない。今の値を見るだけなら
`thth article reactions --fetch`）。MCP と thth.me には出さない。
"""
from __future__ import annotations

import sys

from . import accounts as accounts_mod
from . import jst


def _when(at) -> str:
    return f"{at.month}/{at.day} {at:%H:%M}"


def cmd_collect(args) -> int:
    from . import collect, deliveries, hatena_reactions, principals
    try:
        cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not principals.uses_principal(cfg):
        print("はてなの口座ではありません（反応の採取ははてなブログの口座だけ）: not_hatena", file=sys.stderr)
        return 2
    if args.delivery is not None and not deliveries.is_delivery_id(args.delivery):
        print("配信 id の形が違います（dl_ と 16 進 12 桁）: invalid_delivery", file=sys.stderr)
        return 2
    now = jst.now_jst()
    info: dict = {}
    rc = hatena_reactions.guarded_entry(args.account, now=now, log=print, info=info, delivery=args.delivery)
    if info.get("skipped") != "account_leaving":
        hatena_reactions._record_run(args.account, rc=rc, trigger=collect.TRIGGER_MANUAL, now=now, info=info)
    result = info.get("result") or {}
    for row in result.get("waiting") or []:
        nxt = row.get("next")
        if nxt is None:
            print(f"  {row['delivery']}: 刻みは全部済んでいます（読みません）")
        else:
            print(f"  {row['delivery']}: 跨いでいない（次は {nxt[0]}h・{_when(nxt[1])} ごろ）。読みません")
    if "result" in info and not result.get("looked") and not result.get("waiting") and not result.get("errors"):
        print("採る配信がありません（published・conflict・no_follow で公開から 38 日以内の配信だけ）")
    return rc


def register(sub) -> None:
    p = sub.add_parser("hatena", help="はてなの口座の反応の採取（4.0 S6・CLI だけ）")
    inner = p.add_subparsers(dest="hatena_action", required=True)
    c = inner.add_parser("collect", help="run の末尾と同じ反応の採取を今 1 回（刻みを跨いだ配信だけ読む）")
    c.add_argument("account", help="はてなブログの account 名")
    c.add_argument("--delivery", default=None, help="配信 id で 1 本だけ")
    c.add_argument("--by", default=None, help="誰が打ったか（表示だけ）")
    c.set_defaults(func=cmd_collect)
