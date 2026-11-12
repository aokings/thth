"""写しとの違い（読むだけ）。

違いの種類（`KINDS`）:

- `new`: 写しに無かった公開の商品。
- `published`: 非公開から公開になった。`unpublished`: 公開から非公開になった。
- `price_down`・`price_up`: 値段の上下（前と今の数は材料として持つ。下書きの本文には書かない）。
- `sold_out`・`restock`: 在庫の節目だけ（ある → 無い、無い → ある。在庫を数えない商品は「ある」）。ほかの在庫の増減は
  違いに数えない。
- `removed`: 写しにあって今の読みに無い。

**全ページを読み切れなかったとき**（`complete` が偽）は、`removed` と `sold_out` を言わない（読めなかった頁に
あるだけかもしれない）。写しも進めない（呼ぶ側）。
"""
from __future__ import annotations

from .rows import in_stock

KINDS = ("new", "published", "unpublished", "price_down", "price_up", "sold_out", "restock", "removed")
# 下書きを作れる違い → 下書きの選び方（`--kind`）。
DRAFT_KINDS = {"new": "new", "published": "new", "restock": "restock", "price_down": "price_down"}


def compare(before_items: dict, rows: list, *, complete: bool) -> list:
    changes = []
    now = {str(row["id"]): row for row in rows}
    for key, row in now.items():
        old = before_items.get(key)
        if old is None:
            if row.get("visible"):
                changes.append(_change("new", row))
            continue
        if not row.get("visible"):
            if old.get("visible"):
                changes.append(_change("unpublished", row))
            continue
        if not old.get("visible"):
            changes.append(_change("published", row))
            continue
        price_a, price_b = old.get("price"), row.get("price")
        if isinstance(price_a, int) and isinstance(price_b, int) and price_a != price_b:
            changes.append(_change("price_down" if price_b < price_a else "price_up", row,
                                   before=price_a, after=price_b))
        # 在庫の節目は「在庫があるか」の前と今で決める（None は数えない＝ある・`rows.in_stock`）。
        had, has = in_stock(old), in_stock(row)
        if had and not has and complete:
            changes.append(_change("sold_out", row))
        elif not had and has:
            changes.append(_change("restock", row))
    if complete:
        for key, old in before_items.items():
            if key not in now:
                changes.append({"id": old.get("id"), "kind": "removed", "name": old.get("name")})
    order = {kind: i for i, kind in enumerate(KINDS)}
    return sorted(changes, key=lambda c: (order[c["kind"]], str(c["id"])))


def _change(kind, row, **extra):
    out = {"id": row["id"], "kind": kind}
    if row.get("visible"):
        out["name"] = row.get("name")
    out.update(extra)
    return out
