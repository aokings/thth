"""STORES（retail API 202211）を読む口（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §7・5.18.0）。

鍵はプライベートアプリの**クレデンシャル**（STORES が発行する固定の文字列）を `Authorization: Bearer` に載せるだけ。
OAuth も取り直しも作らない（公開アプリの話・設計 §0 の 1）。打つのは `wire.py` の表の GET 2 つだけ:

- `GET https://api.stores.dev/retail/202211/store` → `{"id": "66a0…", "name": "STORES SHOP"}` の 2 つだけ。
  **店の URL は返らない**ので、台帳の `public_url`（持ち主が入れた値）を `shop_url` にする。
- `GET https://api.stores.dev/retail/202211/items?limit=100&offset=…` → `{"items": [...]}`。欄の名前は公式の
  OpenAPI の定義（2026-11-12 に読んだ）による。1 件は `id`（**文字列**）・`name`・`status`（`shown` 公開・`hidden`
  非公開・`unlisted` 限定公開）・`price`（整数か null・種類の中でいちばん高い `sales_price`）・`image_urls`・
  `preorder_sales`・`sales_period`・`variations[].quantity`（整数か null・null は在庫数が無制限）ほか。
  **更新の時刻・商品頁の URL は無い。** `description`・品番・バーコード・`category_ids` などは読み捨てる。
- 注文・入金・販売チャネル・書く道は呼ばない。**クレデンシャルはそれらも通る鍵**（権限を選べない）なので、
  柵は `wire.ROUTES` の表（道 2 つ・引数は `limit` と `offset` だけ）。

行への直し方（`rows.ROW_FIELDS` は変えない・設計 §7.3）: id は文字列のまま・公開は `status == "shown"`（ほかは
id と visible だけ）・在庫は種類のどれかが null なら None（数えない）、全部が整数なら合計・`sales_status` は
`preorder`（予約販売）・`on_sale`・`before_sale`・`after_sale`・商品頁の URL と更新の時刻は None。

**【未確認】の所は偽の応答で作った**（本物の店では 1 度も動かしていない・設計 §11）:

- 鍵が違う・取り消された・スタンダードプランでなくなったときの HTTP の数（公式の定義に 401 は無く、400・403・
  429・503 だけ）。**401 と 403 のどちらでも** `store_token_rejected`（HTTP の数を詳細に）にする。STORES には
  scope が無いので `store_scope_missing` にしない。
- クレデンシャルの期限（公式に書かれていない）。`thth shop token set --expires` は任意で、無ければ期限の日の無い鍵
  （`no_expiry`）として持つ。
- 種類（variations）の無い商品の形。空・配列でなければ `store_api_changed（item_variations）`（最初の本物の読みで
  ここに当たったら直す）。
- `sales_period` の時刻の形（ISO 8601 と見る【推測】）。時差の無い時刻は日本の時刻と見る【推測】。片方の端が null
  なら、その側は開いていると見る【推測】。読めない形は `store_api_changed（item_sales_period）`。

断りは HTTP の数だけで決める。`errors[].message` も 503 の `message` も、応答の本文は断りに入れない。
再試行しない。1 回の読みは `PAGE_LIMIT` 件ずつ `MAX_PAGES` 頁まで、頁の間は `MIN_INTERVAL_SECONDS` 空ける。
"""
from __future__ import annotations

import datetime
import json
import re
import time

from .. import jst
from . import wire
from .rows import clean_text, https_url, is_int
from .wire import StoreRefused

STORE_PATH = f"/retail/{wire.STORES_API_VERSION}/store"
ITEMS_PATH = f"/retail/{wire.STORES_API_VERSION}/items"
PAGE_LIMIT = 100
MAX_PAGES = 5
MIN_INTERVAL_SECONDS = 1.0
SLEEP = time.sleep
# 今の時刻（販売の期間の前と後を決める・試験の差し込み口）。
NOW = jst.now_jst
STATUSES = ("shown", "hidden", "unlisted")
_ID = re.compile(r"[A-Za-z0-9]{1,64}\Z")


def refusal_for(status: int) -> StoreRefused:
    """HTTP の数だけで決める（本文は見ない）。401 と 403 はどちらも鍵の断り【未確認: どちらが返るか】。"""
    if 300 <= status < 400:
        # リダイレクトは同じホストでも追わない（設計 §5・3 つの店とも）。
        return StoreRefused("store_request_rejected", "redirect")
    if status in (401, 403):
        return StoreRefused("store_token_rejected", f"HTTP {status}")
    if status == 429 or status >= 500:
        return StoreRefused("store_unavailable", f"HTTP {status}")
    return StoreRefused("store_request_rejected", f"HTTP {status}")


def _moment(value) -> datetime.datetime | None:
    """販売の期間の端。null は None（開いている）。読めない形は `store_api_changed`。"""
    if value is None:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= 40:
        raise StoreRefused("store_api_changed", "item_sales_period")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value   # 3.10 の fromisoformat は Z を読まない
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise StoreRefused("store_api_changed", "item_sales_period") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=jst.JST)   # 【推測】時差の無い時刻は日本の時刻


def sales_status(item: dict, now: datetime.datetime) -> str:
    if item.get("preorder_sales") is not None:
        return "preorder"
    period = item.get("sales_period")
    if period is None:
        return "on_sale"
    if not isinstance(period, dict):
        raise StoreRefused("store_api_changed", "item_sales_period")
    start, end = _moment(period.get("period_start_at")), _moment(period.get("period_end_at"))
    if start is not None and now < start:
        return "before_sale"
    if end is not None and now > end:
        return "after_sale"
    return "on_sale"


def stock_of(variations) -> int | None:
    """種類のどれかの `quantity` が null なら None（数えない・無制限）。全部が整数ならその合計。"""
    if not isinstance(variations, list) or not variations:
        raise StoreRefused("store_api_changed", "item_variations")
    total = 0
    for variation in variations:
        if not isinstance(variation, dict) or "quantity" not in variation:
            raise StoreRefused("store_api_changed", "item_variations")
        quantity = variation["quantity"]
        if quantity is None:
            return None
        if not is_int(quantity):
            raise StoreRefused("store_api_changed", "item_variations")
        total += quantity
    return total


def normalize(item, now: datetime.datetime) -> dict:
    """1 商品を許した欄だけに。要る欄が無い・型が違えば `store_api_changed`。知らない欄は無視する。"""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _ID.fullmatch(item["id"]):
        raise StoreRefused("store_api_changed", "item_fields")
    if item.get("status") not in STATUSES:
        raise StoreRefused("store_api_changed", "item_status")
    if item["status"] != "shown":
        # 非公開と限定公開の商品は名前も持たない（限定公開は URL を知る人だけが見られる商品・設計 §7.3）。
        return {"id": item["id"], "visible": False}
    price = item.get("price")
    if not isinstance(item.get("name"), str) or not (price is None or is_int(price)):
        raise StoreRefused("store_api_changed", "item_fields")
    images = item.get("image_urls")
    if images is None:
        images = []
    if not isinstance(images, list):
        raise StoreRefused("store_api_changed", "item_fields")
    return {"id": item["id"], "name": clean_text(item["name"]), "price": price,
            "stock": stock_of(item.get("variations")), "visible": True, "sales_status": sales_status(item, now),
            "image_url": https_url(images[0]) if images else None, "item_url": None, "modified_at": None}


class Client:
    """1 つの命令で 1 つ。鍵（クレデンシャル）は呼ぶ側が読んで渡す（ここは値を出さない）。"""

    def __init__(self, cfg: dict, credential: str, *, transport=None):
        self.cfg = cfg
        self._token = credential
        self.transport = transport
        self.calls = 0

    def _get(self, path: str, params: dict | None = None):
        # URL とヘッダは網の口（`wire.get`）が組む（呼ぶ側は道と引数を名前で渡すだけ）。
        self.calls += 1
        status, raw = wire.get("stores", path, params, self._token, transport=self.transport)
        if status != 200:
            raise refusal_for(status)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreRefused("store_api_changed", "not_json") from None

    def user(self) -> dict:
        """店の id と名前だけ（応答はこの 2 つだけ）。URL は台帳の `public_url`（持ち主が入れた値）。"""
        payload = self._get(STORE_PATH)
        if (not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not _ID.fullmatch(payload["id"])
                or not isinstance(payload.get("name"), str)):
            raise StoreRefused("store_api_changed", "store")
        return {"shop_id": payload["id"], "shop_name": clean_text(payload["name"]),
                "shop_url": self.cfg["public_url"], "able_to_business": None}

    def items(self) -> dict:
        """`{"items": [...], "complete": bool, "pages": n}`。上限の頁まで読んで終わらなければ complete は偽。"""
        rows, complete, pages = [], False, 0
        now = NOW()
        for page in range(MAX_PAGES):
            if page:
                SLEEP(MIN_INTERVAL_SECONDS)
            payload = self._get(ITEMS_PATH, {"limit": PAGE_LIMIT, "offset": page * PAGE_LIMIT})
            pages += 1
            found = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(found, list):
                raise StoreRefused("store_api_changed", "api_items")
            rows.extend(normalize(item, now) for item in found)
            if len(found) < PAGE_LIMIT:
                complete = True
                break
        seen, out = set(), []
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
        return {"items": out, "complete": complete, "pages": pages}
