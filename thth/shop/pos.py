"""実店舗のレジ（Square・スマレジ）に共通の部品（計画 `docs/計画_実店舗_2026-11-13.md` 段 5・設計 `docs/設計_実店舗_2026-11-13.md` §5）。

網には出ない（網は `wire.py` だけ）。ここにあるのは:

- **出してよい品の表**（台帳の `publish`）: レジの品は店の中の事情の品（「その他 500 円」・袋・取り置き）も混ざるので、
  **既定では 1 つも材料にしない**（設計 §5.2）。持ち主が `thth shop publish` で「出してよい」とした品（か部門）だけを
  公開の行にし、ほかは `{"id", "visible": False}`（名前も持たない）。
- 断りの写し方（HTTP の数だけで決める・応答の本文は見ない）。
- 数の読み方（レジの値段と在庫は文字列で返ることがある）。

台帳の `publish` の形: `{"items": [品の id], "categories": [部門の id]}`（どちらも英数字 64 字まで・それぞれ
`PUBLISH_MAX` まで・重なりなし）。
"""
from __future__ import annotations

import decimal
import re

from .wire import StoreRefused

POS_KINDS = frozenset(("square", "smaregi"))
PUBLISH_MAX = 200
PUBLISH_KEYS = ("items", "categories")
_ID = re.compile(r"[A-Za-z0-9]{1,64}\Z")
_NUMBER = re.compile(r"-?[0-9]{1,12}(\.[0-9]{1,6})?\Z")


def empty_publish() -> dict:
    return {"items": [], "categories": []}


def publish_ok(value) -> bool:
    """台帳の `publish` の形（知らない鍵・重なり・形の違う id を受けない）。"""
    if not isinstance(value, dict) or set(value) != set(PUBLISH_KEYS):
        return False
    for key in PUBLISH_KEYS:
        ids = value[key]
        if (not isinstance(ids, list) or len(ids) > PUBLISH_MAX or len(set(ids)) != len(ids)
                or not all(isinstance(i, str) and _ID.fullmatch(i) for i in ids)):
            return False
    return True


def is_published(publish: dict, item_id: str, category_ids) -> bool:
    """持ち主が出してよいとした品か（品の id か、品の部門のどれかが表にある）。"""
    if item_id in publish["items"]:
        return True
    return any(c in publish["categories"] for c in category_ids)


def nothing_published(publish: dict) -> bool:
    return not publish["items"] and not publish["categories"]


def number(value, *, field: str) -> decimal.Decimal:
    """`"1200"`・`1200`・`"3.0"` を数に。読めなければ `pos_api_changed（<欄>）`。"""
    if isinstance(value, bool):
        raise StoreRefused("pos_api_changed", field)
    if isinstance(value, int):
        return decimal.Decimal(value)
    if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        return decimal.Decimal(value.strip())
    raise StoreRefused("pos_api_changed", field)


def whole(value, *, field: str) -> int:
    """在庫の数（小数は切り捨て・量り売りの品など）。"""
    return int(number(value, field=field).to_integral_value(rounding=decimal.ROUND_FLOOR))


def yen(value, *, field: str) -> int:
    """値段（円・小数のある値段は形が違うと見る）。"""
    found = number(value, field=field)
    if found != found.to_integral_value():
        raise StoreRefused("pos_api_changed", field)
    return int(found)


def refusal_for(status: int) -> StoreRefused:
    """HTTP の数だけで決める（本文は見ない・Square とスマレジで同じ）。"""
    if 300 <= status < 400:
        return StoreRefused("store_request_rejected", "redirect")
    if status == 401:
        return StoreRefused("pos_token_rejected", f"HTTP {status}")
    if status == 403:
        return StoreRefused("pos_scope_missing", f"HTTP {status}")
    if status == 429 or status >= 500:
        return StoreRefused("pos_unavailable", f"HTTP {status}")
    return StoreRefused("store_request_rejected", f"HTTP {status}")


# 鍵の広さと、THTH が打つものの知らせ（`thth shop doctor`・`whoami`・`token set` が出す・断りの目録にも同じ筋）。
KEY_NOTES = {
    "square": (
        "Square の個人のアクセストークンは scope の無い全部の権限の鍵です（支払い・返金・顧客にも届きます）。"
        "THTH が Square に打つのは GET だけで、道は店舗の一覧・品の一覧・在庫の数の 3 つです（試験 "
        "tests/test_store_pos_wire.py が柵）。",
        "鍵の広さを小さくするなら、Square の OAuth で自分の店に自分のアプリを許可し、scope を ITEMS_READ・"
        "INVENTORY_READ・MERCHANT_PROFILE_READ の 3 つだけにした鍵を作って渡せます（OAuth の鍵は 30 日で切れるので "
        "--expires に切れる日を付けると、thth morning が 14 日前から知らせます）。THTH はその鍵もそのまま受けます。",
    ),
    "smaregi": (
        "スマレジの鍵は、マイページのプラットフォーム API 設定（プレミアムプラン以上・契約の管理者）で発行した "
        "client id と secret です。THTH は鍵を取るときに pos.products:read・pos.stores:read・pos.stock:read の "
        "3 つだけを頼み、打つのはその鍵を取る POST 1 つと、商品・店舗・在庫を読む GET だけです。",
        "マイページのスコープの設定でも、この 3 つだけをオンにしておくと、secret が漏れたときに取引や会員を"
        "読む鍵を作られません。",
    ),
}
# まだ原文や本物で確かめていない所（`thth shop doctor` と `token set` が正直に言う・設計 実店舗 §9.6）。
UNVERIFIED = {
    "square": (
        "まだ確かめていない所: 本物の Square の店では 1 度も読んでいません（応答の形は公式の頁の欄の名前から作った偽で"
        "試しています）。在庫の数の記録が無い品を 0 と見ています。",
    ),
    "smaregi": (
        "まだ確かめていない所: マイページで発行した認証情報が、この取り方（id.smaregi.jp への client credentials）で"
        "通るかは、公式の頁で読み切れていません（アプリとして登録したときの取り方は公式に書かれています）。"
        "本物のスマレジの契約では 1 度も読んでいません。契約 ID の字の形も確かめていません。",
        "在庫の記録が無い商品（在庫が 1 度も動いていない商品）は 0 と見ています（公式「在庫数が変動して初めて取得可能」）。"
        "画像は読んでいません。",
    ),
}
