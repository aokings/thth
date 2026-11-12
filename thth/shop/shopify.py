"""Shopify を読む口（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §4・5.17.0）。

打つのは `wire.py` の決まった 2 つの問い合わせ（`QUERIES` の shop と products）と、鍵を取る口（client credentials）
だけ。GraphQL の決まりで POST だが、店は変えない。欄の名前は公式（2026-11-12 に読んだ・版 2026-10）による:

- `shop { id name myshopifyDomain url currencyCode }`・`currentAppInstallation { accessScopes { handle } }`。
  取り出すのは myshopify のドメイン・名前・URL・通貨・鍵に許された scope の名前の並びだけ。
- `products(first, after, sortKey: ID)` の `nodes`: `legacyResourceId`・`title`・`status`・`onlineStoreUrl`・
  `totalInventory`・`tracksInventory`・`updatedAt`・`priceRangeV2.minVariantPrice`・`featuredMedia.preview.image.url`。
  `pageInfo.hasNextPage`・`endCursor`。注文・お客さんの欄は問い合わせに無い。

行への直し方（`rows.ROW_FIELDS` は変えない）: id は `int(legacyResourceId)`・公開は `status == ACTIVE` かつ
`onlineStoreUrl` が https（偽なら id と visible だけ）・値段は種類の中で**いちばん安い**値段（円の店だけ・整数の円）・
在庫は `tracksInventory` が偽なら None（数えない）・`sales_status` は `active`・商品頁の URL は `onlineStoreUrl`。

**【未確認】の所は偽の応答で作った**（本物の店では 1 度も動かしていない・設計 §11）: 詰まったときの形
（200 で `errors[].extensions.code` が `THROTTLED`）・scope が足りないときの形（`ACCESS_DENIED`）・鍵が違うときの
401 の本文・`shop_not_permitted` の応答の HTTP の数と本文の形・402 と 423（凍った店）の数。形に寄りかからず、
HTTP の数と、検査した符丁の語だけで決める。`errors` の文・応答の本文は断りに入れない。
"""
from __future__ import annotations

import decimal
import json
import re
import time

from .. import redact
from . import access, wire
from .rows import clean_text, https_url
from .wire import StoreRefused

PAGE_FIRST = 50
MAX_PAGES = 10
MIN_INTERVAL_SECONDS = 0.5
UNKNOWN_COST_WAIT_SECONDS = 1.0
MAX_THROTTLE_WAIT_SECONDS = 5.0
SLEEP = time.sleep
REQUIRED_SCOPE = "read_products"
CURRENCY = "JPY"
SCOPES_MAX = 300
MINT_BODY_PEEK = 4096
_CODE = re.compile(r"[A-Z_]{1,40}\Z")
_HANDLE = re.compile(r"[a-z_]{1,64}\Z")
_DIGITS = re.compile(r"[0-9]{1,18}\Z")


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise StoreRefused("store_api_changed", "not_json") from None


def mint_refusal(status: int, raw: bytes) -> StoreRefused:
    """鍵を取る口の断り（設計 §4.2 の表）。本文は `shop_not_permitted` の語が**あるか無いか**だけ見る。"""
    if b"shop_not_permitted" in (raw or b"")[:MINT_BODY_PEEK]:
        return StoreRefused("store_org_mismatch")
    if 300 <= status < 400:
        return StoreRefused("store_request_rejected", "redirect")
    if status == 404:
        return StoreRefused("store_shop_unknown")
    if status in (400, 401, 403):
        return StoreRefused("store_refresh_rejected", f"HTTP {status}")
    if status == 429 or status >= 500:
        return StoreRefused("store_unavailable", f"HTTP {status}")
    return StoreRefused("store_refresh_rejected", f"HTTP {status}")


def query_refusal(status: int) -> StoreRefused:
    """GraphQL の HTTP の断り（401 は呼ぶ側が 1 回だけ取り直してから）。"""
    if 300 <= status < 400:
        return StoreRefused("store_request_rejected", "redirect")
    if status == 401:
        return StoreRefused("store_token_rejected", "HTTP 401")
    if status in (402, 423):
        return StoreRefused("store_unavailable", "shop_frozen")    # 【推測の数・形に寄らない】
    if status == 403:
        return StoreRefused("store_scope_missing", "HTTP 403")
    if status == 404:
        return StoreRefused("store_shop_unknown")
    if status == 429 or status >= 500:
        return StoreRefused("store_unavailable", f"HTTP {status}")
    return StoreRefused("store_request_rejected", f"HTTP {status}")


def errors_refusal(errors) -> StoreRefused:
    """200 で `errors` があるとき。`extensions.code` を形で検査した語だけを見る（文は入れない）。"""
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    extensions = first.get("extensions") if isinstance(first.get("extensions"), dict) else {}
    code = extensions.get("code")
    code = code if isinstance(code, str) and _CODE.fullmatch(code) else None
    if code == "THROTTLED":
        return StoreRefused("store_unavailable", "throttled")
    if code == "ACCESS_DENIED":
        return StoreRefused("store_scope_missing", "ACCESS_DENIED")
    return StoreRefused("store_api_changed", "graphql_errors")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def throttle_wait(payload) -> float:
    """次の頁までに空ける秒（点数が読めて残りが少なければ回復を待つ・読めなければ 1 秒）。"""
    try:
        cost = payload["extensions"]["cost"]
        requested = _number(cost["requestedQueryCost"])
        status = cost["throttleStatus"]
        available, restore = _number(status["currentlyAvailable"]), _number(status["restoreRate"])
    except (KeyError, TypeError):
        return max(MIN_INTERVAL_SECONDS, UNKNOWN_COST_WAIT_SECONDS)
    if requested is None or available is None or not restore or restore <= 0:
        return max(MIN_INTERVAL_SECONDS, UNKNOWN_COST_WAIT_SECONDS)
    if available < 2 * requested:
        return max(MIN_INTERVAL_SECONDS, min(requested / restore, MAX_THROTTLE_WAIT_SECONDS))
    return MIN_INTERVAL_SECONDS


def yen(money) -> int:
    """`{"amount": "1200.0", "currencyCode": "JPY"}` → 1200。円でない・小数がある → `store_currency_unsupported`。"""
    if not isinstance(money, dict) or not isinstance(money.get("amount"), str):
        raise StoreRefused("store_api_changed", "item_price")
    if money.get("currencyCode") != CURRENCY:
        raise StoreRefused("store_currency_unsupported")
    try:
        amount = decimal.Decimal(money["amount"])
    except decimal.InvalidOperation:
        raise StoreRefused("store_api_changed", "item_price") from None
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise StoreRefused("store_currency_unsupported", "fraction")
    return int(amount)


def normalize(node) -> dict:
    """1 商品を許した欄だけに。要る欄が無い・型が違えば `store_api_changed`。知らない欄は無視する。"""
    if not isinstance(node, dict):
        raise StoreRefused("store_api_changed", "item_fields")
    legacy = node.get("legacyResourceId")
    legacy = str(legacy) if isinstance(legacy, (str, int)) and not isinstance(legacy, bool) else ""
    if not _DIGITS.fullmatch(legacy) or not isinstance(node.get("status"), str):
        raise StoreRefused("store_api_changed", "item_fields")
    item_id = int(legacy)
    url = https_url(node.get("onlineStoreUrl"))
    if node["status"] != "ACTIVE" or url is None:
        # 下書き・アーカイブ・限定公開・オンラインストアに出していない商品は名前も持たない。
        return {"id": item_id, "visible": False}
    tracks, total = node.get("tracksInventory"), node.get("totalInventory")
    if (not isinstance(node.get("title"), str) or not isinstance(tracks, bool)
            or not isinstance(total, int) or isinstance(total, bool) or not isinstance(node.get("updatedAt"), str)):
        raise StoreRefused("store_api_changed", "item_fields")
    price_range = node.get("priceRangeV2")
    if not isinstance(price_range, dict):
        raise StoreRefused("store_api_changed", "item_price")
    media = node.get("featuredMedia")
    image = None
    if isinstance(media, dict) and isinstance(media.get("preview"), dict) and isinstance(media["preview"].get("image"), dict):
        image = https_url(media["preview"]["image"].get("url"))
    return {"id": item_id, "name": clean_text(node["title"]), "price": yen(price_range.get("minVariantPrice")),
            "stock": total if tracks else None, "visible": True, "sales_status": "active", "image_url": image,
            "item_url": url, "modified_at": clean_text(node["updatedAt"], 40)}


class Client:
    """1 つの命令で 1 つ。鍵は呼ぶ側が読んで渡す（ここは値を出さない）。

    `token` は鍵のファイルの中身（`auth` が `shopify_client` か `shopify_token`）。`cache` が偽なら短い鍵を置き場に
    書かず、取った鍵を `minted` に持つ（`token set` が「店が見えたときだけ保存する」ため）。
    """

    def __init__(self, cfg: dict, token: dict, *, transport=None, cache: bool = True):
        self.cfg = cfg
        self.shop = cfg["shop"]
        self._token = token
        self.transport = transport
        self.cache = cache
        self.minted = None
        self.calls = 0

    @property
    def auth(self) -> str:
        return "shopify_client" if self._token.get("auth") == "shopify_client" else "shopify_token"

    def _mint(self) -> dict:
        self.calls += 1
        status, raw = wire.shopify_token(self.shop, self._token["client_id"], self._token["client_secret"],
                                         transport=self.transport)
        if status != 200:
            raise mint_refusal(status, raw)
        payload = _json(raw)
        if not isinstance(payload, dict):
            raise StoreRefused("store_api_changed", "token_response")
        return payload

    def _access(self) -> str:
        if self.auth == "shopify_token":
            return self._token["access_token"]
        if not self.cache:
            if self.minted is None:
                minted = self._mint()
                token = minted.get("access_token")
                seconds = minted.get("expires_in")
                if (not isinstance(token, str) or not token or not isinstance(seconds, int)
                        or isinstance(seconds, bool) or seconds <= 0):
                    raise StoreRefused("store_api_changed", "token_response")
                redact.register_secret(token)
                self.minted = minted
            return self.minted["access_token"]
        return access.ensure(self.cfg["store"], self._mint)

    def _query(self, name: str, variables: dict | None = None) -> dict:
        token = self._access()
        self.calls += 1
        status, raw = wire.shopify_graphql(self.shop, name, variables, token, transport=self.transport)
        if status == 401 and self.auth == "shopify_client":
            # 「再試行しない」のただ 1 つの例外: 置き場の鍵が断られたら、取り直して同じ問い合わせを 1 回だけ打ち直す。
            if self.cache:
                access.drop(self.cfg["store"], token)
            else:
                self.minted = None
            token = self._access()
            self.calls += 1
            status, raw = wire.shopify_graphql(self.shop, name, variables, token, transport=self.transport)
        if status != 200:
            raise query_refusal(status)
        payload = _json(raw)
        if not isinstance(payload, dict):
            raise StoreRefused("store_api_changed", "graphql_shape")
        if payload.get("errors"):
            raise errors_refusal(payload["errors"])
        if not isinstance(payload.get("data"), dict):
            raise StoreRefused("store_api_changed", "graphql_shape")
        return payload

    def user(self) -> dict:
        """店の myshopify のドメイン・名前・URL・鍵の scope の並びだけ。"""
        data = self._query("shop")["data"]
        shop, install = data.get("shop"), data.get("currentAppInstallation")
        if not isinstance(shop, dict) or not isinstance(shop.get("myshopifyDomain"), str):
            raise StoreRefused("store_api_changed", "shop")
        if shop["myshopifyDomain"] != wire.shopify_host(self.shop):
            raise StoreRefused("store_shop_mismatch", "myshopify_domain")
        url = https_url(shop.get("url"))
        if url is None:
            raise StoreRefused("store_api_changed", "shop_url")
        if shop.get("currencyCode") != CURRENCY:
            raise StoreRefused("store_currency_unsupported")
        found = install.get("accessScopes") if isinstance(install, dict) else None
        if not isinstance(found, list):
            raise StoreRefused("store_api_changed", "access_scopes")
        scopes = sorted({row["handle"] for row in found[:SCOPES_MAX] if isinstance(row, dict)
                         and isinstance(row.get("handle"), str) and _HANDLE.fullmatch(row["handle"])})
        if REQUIRED_SCOPE not in scopes:
            raise StoreRefused("store_scope_missing", REQUIRED_SCOPE)
        return {"shop_id": shop["myshopifyDomain"], "shop_name": clean_text(shop.get("name")), "shop_url": url,
                "able_to_business": None, "scopes": scopes,
                "scopes_extra": [s for s in scopes if s != REQUIRED_SCOPE]}

    def items(self) -> dict:
        """`{"items": [...], "complete": bool, "pages": n}`。上限の頁まで読んで終わらなければ complete は偽。"""
        rows, complete, pages, after, wait = [], False, 0, None, 0.0
        for page in range(MAX_PAGES):
            if page:
                SLEEP(wait)
            payload = self._query("products", {"first": PAGE_FIRST, "after": after})
            pages += 1
            products = payload["data"].get("products")
            nodes = products.get("nodes") if isinstance(products, dict) else None
            info = products.get("pageInfo") if isinstance(products, dict) else None
            if (not isinstance(nodes, list) or not isinstance(info, dict)
                    or not isinstance(info.get("hasNextPage"), bool)):
                raise StoreRefused("store_api_changed", "products")
            rows.extend(normalize(node) for node in nodes)
            if not info["hasNextPage"]:
                complete = True
                break
            after = info.get("endCursor")
            if not isinstance(after, str) or not 0 < len(after) <= wire.AFTER_MAX:
                raise StoreRefused("store_api_changed", "products")
            wait = throttle_wait(payload)
        seen, out = set(), []
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
        return {"items": out, "complete": complete, "pages": pages}
