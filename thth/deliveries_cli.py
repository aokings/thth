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
            "published_url": record.get("published_url"),
            # S2 §6: はてなが返した本文が送った本文と違った（最後に出した行の印）。
            "readback_differs": _readback_differs(record),
            # S3 §7.1: 画像の数と、主体の画像の台帳での状態の内訳。
            "images": image_summary(record),
            # S4 §3.3: ブックマークは `sent/bm:` を配信 id で引いて読むときに添える（台帳には書かない）。
            "bookmark": bookmark_of(record)}


def bookmark_of(record: dict) -> dict | None:
    """配信のブックマーク（口座の `sent/bm:` を配信 id で引く・読むだけ）。無ければ None。"""
    from . import hatena_bookmark
    try:
        row = hatena_bookmark.by_delivery(record["account"]).get(record["delivery"])
    except (OSError, ValueError, accounts_mod.AccountError):
        return None
    if row is None:
        return None
    return {"post_id": row["post_id"], "result": row.get("result"), "sent_at": row.get("sent_at"),
            "retracted_at": row.get("retracted_at"), "permalink": row.get("permalink")}


def bookmark_text(mark) -> str:
    if not mark:
        return "  ブクマ: 未"
    when = jst.parse(mark.get("sent_at"))
    day = f"{when.month}/{when.day}" if when else "?"
    if mark.get("retracted_at"):
        return f"  ブクマ: 消した（{day} に付けた）"
    if mark.get("result") == "unknown":
        return f"  ブクマ: 結果不明（{day}・thth bookmark reconcile）"
    if mark.get("result") == "not_found":
        return f"  ブクマ: 付いていなかった（{day}）"
    return f"  ブクマ: 付けた（{day}）"


def _project_of(target, names):
    """対象の project（口座名なら台帳の project）。読めなければ None。"""
    for name in names:
        try:
            return accounts_mod.load_account(name).get("project")
        except (accounts_mod.AccountError, ValueError, TypeError):
            continue
    return None


def announcements(project, delivery_ids, *, allowed_names=None):
    """同じ project の SNS の口座の `sent/` から、`announces` が配信 id に一致する行（読むときに集める）。

    `(行の list, 読めなかった口座の list)`。行は `{account, post_id, sent_at, announces, article, article_url}`。
    """
    from . import announce, sent as sent_mod
    rows, broken = [], []
    if not delivery_ids:
        return rows, broken
    for name, data in announce._raw_accounts():
        if data.get("project") != project or data.get("media") == "hatenablog":
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        errors = []
        try:
            records = sent_mod.records(accounts_mod.state_dir_for(name), errors=errors)
        except (OSError, ValueError):
            broken.append(name)
            continue
        if errors:
            broken.append(name)
        for row in records:
            if row.get("announces") in delivery_ids:
                rows.append({"account": name, "post_id": row["post_id"], "sent_at": row.get("sent_at"),
                             "announces": row["announces"], "article": row.get("article"),
                             "article_url": row.get("article_url"), "retracted_at": row.get("retracted_at")})
    rows.sort(key=lambda r: (r["sent_at"] or "", r["account"]))
    return rows, broken


def image_summary(record: dict) -> dict | None:
    """配信の `images[]` を主体の画像の台帳（`principal_state/<p>/fotolife/`）で引いて数える（読むだけ）。"""
    from . import hatena_fotolife
    images = record.get("images") or []
    if not images:
        return None
    counts = {"total": len(images), "uploaded": 0, "unknown": 0, "none": 0, "unreadable": 0}
    principal = record.get("principal")
    for row in images:
        sha = row.get("source_sha256") if isinstance(row, dict) else None
        try:
            state, _rec = hatena_fotolife.status_of(principal, sha)
        except (ValueError, OSError, accounts_mod.AccountError):
            state = "unreadable"
        counts[state if state in counts else "unreadable"] += 1
    return counts


def image_text(summary) -> str:
    if not summary:
        return ""
    parts = [f"上がっている {summary['uploaded']}"]
    if summary["unknown"]:
        parts.append(f"結果不明 {summary['unknown']}")
    if summary["unreadable"]:
        parts.append(f"台帳が読めない {summary['unreadable']}")
    return f"  画像 {summary['total']} 件（{'・'.join(parts)}）"


def _readback_differs(record: dict) -> bool:
    for row in reversed(record["history"]):
        if row.get("state") in ("published", "conflict", "deleted") and row.get("entry_id"):
            return row.get("readback_differs") is True
    return False


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
    # S4 §9.4 の 5・§3.3: 記事で絞ったときは告知（SNS の `sent/`）とブックマーク（`sent/bm:`）を添える。
    told, marks = None, None
    if article is not None:
        told, broken_sns = announcements(_project_of(target, names), {r["delivery"] for r in rows},
                                         allowed_names=allowed_names)
        if broken_sns:
            cannot_say.add("sent_unreadable")
            unreadable += [{"account": name, "delivery": None, "reason": "sent_unreadable"} for name in broken_sns]
        marks = []
        for r in rows:
            mark = bookmark_of(r)
            if mark is not None:
                marks.append(dict(mark, account=r["account"], delivery=r["delivery"]))
    return {"schema": DELIVERIES_SCHEMA, "report_type": "deliveries",
            "generated_at": jst.iso(now), "target": target, "target_kind": kind,
            "accounts": names, "article": article, "n": len(rows),
            "deliveries": rows, "unreadable": unreadable,
            **({"announcements": told, "bookmarks": marks} if article is not None else {}),
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
        differs = "  読み戻し差あり" if row.get("readback_differs") else ""
        out(f"  {row['delivery']}  {row['account']}  {row['state']}（{row['seq']}）"
            f"  {row['article']}  {row['blog_id']}  {row['at']}{url}{differs}{image_text(row['images'])}"
            f"{bookmark_text(row['bookmark']) if row['state'] in ('published', 'deleted') else ''}")
    for told in payload.get("announcements") or []:
        when = jst.parse(told.get("sent_at"))
        stamp = f"{when.month}/{when.day} {when:%H:%M}" if when else "?"
        out(f"  告知: {told['account']} {stamp}（{told['post_id']}）→ {told['announces']}")
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
