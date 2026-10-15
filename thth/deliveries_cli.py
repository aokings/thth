"""`thth deliveries <account|project> [--article …] [--json]`——配信の台帳を並べる（設計 4.0 S1 §3.4）。

**読むだけ。** `state/<account>/deliveries/*.json` を口座ごとに読み、1 行ずつ（か JSON で）出す。
媒体の API は呼ばない。主体の台帳も読まない。

1 本が読めなくても他は出し、読めなかったものは `cannot_say` に `delivery_unreadable` と言い、
`unreadable[]` にどの口座のどの配信か（と理由の符丁）を並べる（0 件と混ぜない）。
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import deliveries as deliveries_mod
from . import jst

DELIVERIES_SCHEMA = "thth.deliveries/1"


class DeliveriesError(ValueError):
    """問いが受け取れない（対象が無い）。"""


def _row(record: dict) -> dict:
    """1 行の要約（JSON の `deliveries[]` は台帳そのもの。表示はここから）。"""
    last = record["history"][-1]
    return {"delivery": record["delivery"], "account": record["account"],
            "principal": record.get("principal"), "blog_id": record.get("blog_id"),
            "article": record.get("article"), "form": record.get("form"),
            "state": record["state"], "seq": record["seq"], "at": last.get("at"),
            "published_url": record.get("published_url")}


def build(target, *, article=None, now=None, allowed_names=None) -> dict:
    """配信の一覧。`allowed_names` が渡されたら、その外の口座は読まない。"""
    from . import morning
    now = now if now is not None else jst.now_jst()
    names = morning.targets(target)
    kind = "account" if names == [target] else "project"
    if allowed_names is not None:
        names = [name for name in names if name in allowed_names]
    if not names:
        raise DeliveriesError("target_unknown")
    rows, unreadable, cannot_say = [], [], set()
    for name in names:
        try:
            records, broken = deliveries_mod.list_for(name)
        except (deliveries_mod.DeliveryError, OSError):
            cannot_say.add("deliveries_unreadable")
            unreadable.append({"account": name, "delivery": None, "reason": "deliveries_unreadable"})
            continue
        if broken:
            cannot_say.add("delivery_unreadable")
            unreadable += broken
        rows += [r for r in records if article is None or r.get("article") == article]
    rows.sort(key=lambda r: (r["history"][-1].get("at") or "", r["account"], r["delivery"]))
    return {"schema": DELIVERIES_SCHEMA, "report_type": "deliveries",
            "generated_at": jst.iso(now), "target": target, "target_kind": kind,
            "accounts": names, "article": article, "n": len(rows),
            "deliveries": rows, "unreadable": unreadable,
            "cannot_say": sorted(cannot_say),
            "limitations": [
                "読むだけ。媒体の API は呼ばず、THTH の配信の台帳だけを並べる",
                "THTH を通した配信だけ。手で出した記事は入らない"]}


def render(payload, out=print) -> None:
    head = f"  記事 {payload['article']}" if payload.get("article") else ""
    out(f"配信  {payload['target']}（{payload['target_kind']}）{head}  {payload['n']} 件")
    for record in payload["deliveries"]:
        row = _row(record)
        url = f"  {row['published_url']}" if row.get("published_url") else ""
        out(f"  {row['delivery']}  {row['account']}  {row['state']}（{row['seq']}）"
            f"  {row['article']}  {row['blog_id']}  {row['at']}{url}")
    if payload["unreadable"]:
        out("読めなかったもの: " + "・".join(
            f"{u['account']}/{u['delivery'] or '*'}（{u['reason']}）" for u in payload["unreadable"]))


def register(sub) -> None:
    p = sub.add_parser("deliveries", help="配信の台帳（はてなブログの記事の配信）を並べる（読むだけ）")
    p.add_argument("account", metavar="target", help="account 名か project 名")
    p.add_argument("--article", default=None, help="記事の id（例 kopicha/2026-10-arabica）で絞る")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    try:
        payload = build(args.account, article=args.article)
    except (DeliveriesError, accounts_mod.AccountError) as exc:
        print(str(exc), file=sys.stderr)
        if args.json:
            print(json.dumps({"schema": DELIVERIES_SCHEMA, "cannot_say": [str(exc)]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render(payload)
    return 0
