"""スマレジ（プラットフォーム API・実店舗のレジ）を読む口（計画 `docs/計画_実店舗_2026-11-13.md` 段 5・設計
`docs/設計_実店舗_2026-11-13.md` §9.1）。

鍵はマイページの「プラットフォーム API 設定」（プレミアムプラン以上・契約の管理者【公式】）で発行した client id と
secret（台帳の外の鍵のファイル・`auth` は `smaregi_client`）。打つのは `wire.py` の表のものだけ:

- 鍵を取る `POST https://id.smaregi.jp/app/{契約 ID}/token`（Basic・本文は `grant_type=client_credentials` と
  `scope=pos.products:read pos.stores:read pos.stock:read` だけ）→ `access_token`・`scope`。1 つの命令で 1 回だけ取る
  （有効期間は通常 3600 秒【公式の例】・命令は数分で終わるので手元に置かない）。返った `scope` に 3 つがそろわなければ
  `pos_scope_missing`。
- `GET https://api.smaregi.jp/{契約 ID}/pos/stores/{店舗 ID}?fields=storeId,storeName` → 店舗の名前だけ（住所・電話は
  `fields` で受けない）。404 は `pos_location_unknown`。**営業の時間の欄はスマレジに無い**【公式】。
- `GET …/pos/products?fields=productId,categoryId,productName,price,stockControlDivision&limit=1000&page=N`
  → 商品の配列（欄はどれも文字列【公式】）。
- `GET …/pos/stock?fields=productId,storeId,stockAmount&store_id=<店舗>&product_id=<商品>` → **出してよい商品の、
  在庫を数える商品だけ**を 1 つずつ読む（出さない商品の在庫の数は受けない）。記録の無い商品は 0（公式「在庫数が変動して
  初めて取得可能」）。

行への直し方: 持ち主が出してよいとした商品（か部門）だけが公開の行（`pos.is_published`）・id は文字列の数字・在庫は
`stockControlDivision` が `1`（在庫管理の対象外）なら None・値段は `price`（円）・種類は無い（1 つの SKU が 1 つの商品）・
画像と商品頁の URL は None・`sales_status` は `on_sale`。

**【未確認】**（`pos.UNVERIFIED`）: マイページで発行した認証情報がこの取り方で通るか・契約 ID の字の形・本物の応答。
偽の応答で作った。
"""
from __future__ import annotations

import json
import re
import time

from .. import redact
from . import pos, wire
from .rows import clean_text
from .wire import StoreRefused

PAGE_LIMIT = 1000
MAX_PAGES = 3
INVENTORY_MAX = 50
MIN_INTERVAL_SECONDS = 0.1
SLEEP = time.sleep
SCOPES = frozenset(wire.SMAREGI_SCOPE.split())
_DIGITS = re.compile(r"[0-9]{1,15}\Z")


class Client:
    """1 つの命令で 1 つ。鍵（client id と secret）は呼ぶ側が読んで渡す（ここは値を出さない）。"""

    def __init__(self, cfg: dict, token: dict, *, transport=None):
        self.cfg = cfg
        self.contract = cfg["contract"]
        self.location = cfg["location"]
        self._client = token
        self._access = None
        self.transport = transport
        self.calls = 0
        self.scopes = None

    def _mint(self) -> str:
        if self._access is not None:
            return self._access
        self.calls += 1
        status, raw = wire.smaregi_token(self.contract, self._client["client_id"], self._client["client_secret"],
                                         transport=self.transport)
        if status in (400, 401):
            # 【未確認】client id と secret の取り違え（invalid_client）は 401【公式: 取り違えると 401】。400 も鍵の断りと見る。
            raise StoreRefused("pos_token_rejected", f"HTTP {status}")
        if status != 200:
            raise pos.refusal_for(status)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreRefused("pos_api_changed", "token") from None
        access = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access, str) or not access or len(access) > 4096 or any(c.isspace() for c in access):
            raise StoreRefused("pos_api_changed", "token")
        redact.register_secret(access)
        granted = payload.get("scope")
        granted = set(granted.split()) if isinstance(granted, str) else set()
        if not SCOPES <= granted:
            raise StoreRefused("pos_scope_missing", "scope")
        self.scopes = sorted(granted)
        self._access = access
        return access

    def _get(self, path: str, params: dict):
        token = self._mint()
        if self.calls:
            SLEEP(MIN_INTERVAL_SECONDS)
        self.calls += 1
        status, raw = wire.get("smaregi", path, params, token, transport=self.transport)
        if status != 200:
            return status, None
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreRefused("pos_api_changed", "not_json") from None

    def user(self) -> dict:
        status, payload = self._get(f"/{self.contract}/pos/stores/{self.location}",
                                    {"fields": wire.SMAREGI_FIELDS["store"]})
        if status == 404:
            raise StoreRefused("pos_location_unknown")
        if status != 200:
            raise pos.refusal_for(status)
        if (not isinstance(payload, dict) or str(payload.get("storeId")) != self.location
                or not isinstance(payload.get("storeName"), str)):
            raise StoreRefused("pos_api_changed", "store")
        return {"shop_id": self.contract, "shop_name": clean_text(payload["storeName"]),
                "shop_url": self.cfg["public_url"], "able_to_business": None, "location": self.location,
                "hours": None, "scopes_extra": sorted(set(self.scopes or ()) - SCOPES)}

    def _products(self):
        found, complete, pages = [], False, 0
        for page in range(1, MAX_PAGES + 1):
            status, payload = self._get(f"/{self.contract}/pos/products",
                                        {"fields": wire.SMAREGI_FIELDS["products"], "limit": PAGE_LIMIT, "page": page})
            if status != 200:
                raise pos.refusal_for(status)
            if not isinstance(payload, list):
                raise StoreRefused("pos_api_changed", "products")
            pages += 1
            found.extend(payload)
            if len(payload) < PAGE_LIMIT:
                complete = True
                break
        out, seen = [], set()
        for product in found:
            if not isinstance(product, dict):
                raise StoreRefused("pos_api_changed", "product")
            pid = str(product.get("productId"))
            if not _DIGITS.fullmatch(pid):
                raise StoreRefused("pos_api_changed", "product_id")
            if pid not in seen:
                seen.add(pid)
                out.append((pid, product))
        return out, complete, pages

    def _stock(self, pid: str) -> int:
        status, payload = self._get(f"/{self.contract}/pos/stock", {"fields": wire.SMAREGI_FIELDS["stock"],
                                                                     "store_id": self.location, "product_id": pid})
        if status != 200:
            raise pos.refusal_for(status)
        if not isinstance(payload, list):
            raise StoreRefused("pos_api_changed", "stock")
        total = 0
        for row in payload:
            if isinstance(row, dict) and str(row.get("productId")) == pid and str(row.get("storeId")) == self.location:
                total += pos.whole(row.get("stockAmount"), field="stock_amount")
        return total   # 記録の無い商品は 0（在庫が 1 度も動いていない）

    @staticmethod
    def _category(product: dict) -> list:
        value = product.get("categoryId")
        return [str(value)] if value not in (None, "") else []

    def items(self) -> dict:
        products, complete, pages = self._products()
        publish = self.cfg["publish"]
        rows, inventory_calls = [], 0
        for pid, product in products:
            if not pos.is_published(publish, pid, self._category(product)):
                rows.append({"id": pid, "visible": False})
                continue
            if not isinstance(product.get("productName"), str):
                raise StoreRefused("pos_api_changed", "product_name")
            division = product.get("stockControlDivision")
            if division == "1":
                stock = None
            elif division == "0":
                inventory_calls += 1
                if inventory_calls > INVENTORY_MAX:
                    raise StoreRefused("pos_read_limit", "inventory")
                stock = self._stock(pid)
            else:
                raise StoreRefused("pos_api_changed", "stock_control_division")
            price = product.get("price")
            rows.append({"id": pid, "name": clean_text(product["productName"]),
                         "price": None if price in (None, "") else pos.yen(price, field="price"),
                         "stock": stock, "visible": True, "sales_status": "on_sale", "image_url": None,
                         "item_url": None, "modified_at": None})
        return {"items": rows, "complete": complete, "pages": pages, "hours": None, "inventory_calls": inventory_calls}

    def candidates(self) -> dict:
        """持ち主が出す商品を選ぶための一覧（名前と部門の id・**写しにもファイルにも残さない**）。在庫は読まない。"""
        products, complete, _pages = self._products()
        out = [{"id": pid, "name": clean_text(p.get("productName")),
                "categories": [{"id": c, "name": ""} for c in self._category(p) if _DIGITS.fullmatch(c)],
                "published": pos.is_published(self.cfg["publish"], pid, self._category(p)), "archived": False}
               for pid, p in products]
        return {"candidates": out, "complete": complete}
