"""写しとの違い（読むだけ）。

違いの種類（`KINDS`）:

- `new`: 写しに無かった公開の商品。
- `published`: 非公開から公開になった。`unpublished`: 公開から非公開になった。
- `sale_started`: 公開のまま、売り方（`sales_status`）が「売っている」（`rows.ON_SALE`）の外から内になった
  （5.18.1・監査 5.18.0 の 6）。STORES の販売の期間の前・予約販売・期間の後 → 販売中、BASE の販売の前・販売の
  終わり → 販売中。下書きは新着と同じ選び方（`new`）で、本文は「販売を始めました」。同じ商品の在庫の節目
  （無い → ある）が同時なら `restock` は言わない。前の写しの行に `sales_status` が無い・文字列でないときは言わない。
  Shopify は公開の行がいつも `active` なので出ない（下書き・アーカイブ → 公開は `published`）。
- `price_down`・`price_up`: 値段の上下（前と今の数は材料として持つ。下書きの本文には書かない）。
  種類のある店（STORES・Shopify・行に `variant_prices` がある）は**同じ種類の値段**で決める（5.18.1・監査 5.18.0 の 5）:
  前と今の両方にある種類のどれかが下がり、どれも上がっていなければ `price_down`（上がりだけなら `price_up`）。
  `before`・`after` は下がった（上がった）種類のうち今いちばん安い種類の前と今、`variants` はその種類の数。
  種類の無い店（BASE）は今までどおり行の `price` で決める。
- `price_changed`: 値段が動いたが上下を言わない（**下書きを作らない**）。`why` は `mixed`（上がった種類と下がった
  種類がある）・`snapshot_without_variants`（前の版の写しで種類ごとの値段が無い）・`variants_unread`（種類を
  読み切れない商品）。後の 2 つは行の `price` が動いたときだけ言う。
- `variants_changed`: 種類が増えた・減った（`added`・`removed` は数・**下書きを作らない**）。値段の上下ではない。
- `sold_out`・`restock`: 在庫の節目だけ（ある → 無い、無い → ある。在庫を数えない商品は「ある」）。ほかの在庫の増減は
  違いに数えない。
- `removed`: 写しにあって今の読みに無い。

**全ページを読み切れなかったとき**（`complete` が偽）は、`removed` と `sold_out` を言わない（読めなかった頁に
あるだけかもしれない）。写しも進めない（呼ぶ側）。
"""
from __future__ import annotations

from .rows import ON_SALE, VARIANT_PRICES, in_stock, is_int, variant_prices

KINDS = ("new", "published", "sale_started", "unpublished", "price_down", "price_up", "price_changed", "variants_changed",
         "sold_out", "restock", "removed")
# 下書きを作れる違い → 下書きの選び方（`--kind`）。
DRAFT_KINDS = {"new": "new", "published": "new", "sale_started": "new", "restock": "restock", "price_down": "price_down"}


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
        changes.extend(_price_changes(old, row))
        started = _sale_started(old, row)
        if started:
            changes.append(_change("sale_started", row))
        # 在庫の節目は「在庫があるか」の前と今で決める（None は数えない＝ある・`rows.in_stock`）。
        had, has = in_stock(old), in_stock(row)
        if had and not has and complete:
            changes.append(_change("sold_out", row))
        elif not had and has and not started:
            changes.append(_change("restock", row))
    if complete:
        for key, old in before_items.items():
            if key not in now:
                changes.append({"id": old.get("id"), "kind": "removed", "name": old.get("name")})
    order = {kind: i for i, kind in enumerate(KINDS)}
    return sorted(changes, key=lambda c: (order[c["kind"]], str(c["id"])))


def _sale_started(old: dict, row: dict) -> bool:
    """売っているの外 → 内。前の売り方が文字列でなければ（古い・形の違う写し）言わない。"""
    before = old.get("sales_status")
    return isinstance(before, str) and before not in ON_SALE and row.get("sales_status") in ON_SALE


def _price_changes(old: dict, row: dict) -> list:
    price_a, price_b = old.get("price"), row.get("price")
    moved = is_int(price_a) and is_int(price_b) and price_a != price_b
    if VARIANT_PRICES not in row:
        # 種類の無い店（BASE・商品 1 つに値段 1 つ）: 行の値段で決める（今までどおり）。
        if moved:
            return [_change("price_down" if price_b < price_a else "price_up", row, before=price_a, after=price_b)]
        return []
    before, now = variant_prices(old), variant_prices(row)
    if before is None or now is None:
        # 種類ごとに比べられない。行の値段が動いても、種類が減っただけかもしれないので上下を言わない。
        if not moved:
            return []
        why = "snapshot_without_variants" if VARIANT_PRICES not in old else "variants_unread"
        return [_change("price_changed", row, before=price_a, after=price_b, why=why)]
    common = sorted(set(before) & set(now))
    pairs = [(k, before[k], now[k]) for k in common if is_int(before[k]) and is_int(now[k])]
    downs = [p for p in pairs if p[2] < p[1]]
    ups = [p for p in pairs if p[2] > p[1]]
    out = []
    if downs and ups:
        out.append(_change("price_changed", row, before=price_a, after=price_b, why="mixed",
                           down=len(downs), up=len(ups)))
    elif downs or ups:
        moved_variants = downs or ups
        _key, was, is_now = min(moved_variants, key=lambda p: (p[2], p[0]))
        out.append(_change("price_down" if downs else "price_up", row, before=was, after=is_now,
                           variants=len(moved_variants)))
    added, removed = len(set(now) - set(before)), len(set(before) - set(now))
    if added or removed:
        out.append(_change("variants_changed", row, added=added, removed=removed))
    return out


def _change(kind, row, **extra):
    out = {"id": row["id"], "kind": kind}
    if row.get("visible"):
        out["name"] = row.get("name")
    out.update(extra)
    return out
