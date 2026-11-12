"""店に寄らない行の部品（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §3・§6）。

どの店の口（`base.py`・`shopify.py`・`stores.py`）も、応答を許した欄だけの行（`ROW_FIELDS`）に直してから渡す。写し・違い・
下書き・読み直しはこの行だけを見る（店ごとの差は「応答を行に直す所」に閉じる）。

店ごとに違う 3 つの受け口:

- 商品の id: 整数の店（BASE・Shopify）と文字列の店（STORES・5.18.0）がある。写しの鍵は `str(id)`。
- 在庫: 整数か、**None（数えない）**。None は「在庫を数えない商品」で、買える側に数える（`in_stock`）。
- 商品頁の URL（`item_url`）: 取れる店（Shopify）と取れない店（BASE・STORES）がある。
"""
from __future__ import annotations

import re
import urllib.parse

NAME_MAX = 200
URL_MAX = 2048
# 行を分ける字 U+2028・U+2029 は字そのものを貼らずエスケープで書く（5.17.0 の寄せで空白に化けていた・監査 5.18.0 の 1）。
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")
# 写しと出力に持つ欄（公開の商品）。非公開の商品は `id` と `visible` だけ。
ROW_FIELDS = ("id", "name", "price", "stock", "visible", "sales_status", "image_url", "item_url", "modified_at")
# 「売っている」の語（店の間で語がぶつからないので 1 つの集合・BASE は item_now_on_sale・Shopify は active・
# STORES は on_sale。STORES の preorder（予約販売）・before_sale・after_sale は入れない）。
ON_SALE = frozenset(("item_now_on_sale", "active", "on_sale"))


def clean_text(value, limit=NAME_MAX) -> str:
    """利用者の入力（商品名など）: 改行・制御文字を空白に、`---` を落とし、空白を詰めて切る。"""
    text = _CONTROL.sub(" ", value if isinstance(value, str) else "")
    while "---" in text:
        text = text.replace("---", "-")
    return " ".join(text.split())[:limit]


def https_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > URL_MAX or _CONTROL.search(value) or " " in value:
        return None
    parts = urllib.parse.urlsplit(value)
    # userinfo は形を問わず断る（`https://:p@host` は username が空の文字列になるので `@` で見る・監査 5.18.0 の 2）。
    return value if parts.scheme == "https" and parts.hostname and "@" not in parts.netloc else None


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def snapshot_row(row: dict) -> dict:
    if not row.get("visible"):
        return {"id": row["id"], "visible": False}
    return {key: row.get(key) for key in ROW_FIELDS}


def in_stock(row: dict) -> bool:
    """在庫があるか。None（数えない）は真、整数は 0 より大きいとき真。公開の行にだけ使う（非公開の行に stock は無い）。"""
    stock = row.get("stock")
    if stock is None:
        return True
    return is_int(stock) and stock > 0


def buyable(row: dict) -> bool:
    """公開で買える（下書きを作ってよい）商品か。"""
    return bool(row.get("visible")) and row.get("sales_status") in ON_SALE and in_stock(row)
