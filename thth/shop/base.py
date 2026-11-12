"""BASE v2（β）を読む口。欄の名前は公式（2026-11-12 に読んだ）による:

- `GET https://apiv2.thebase.com/api/user`（scope `user.read`）→ `shop_id`・`shop_url`（独自ドメインがあればそれ）・
  `shop_name`・`able_to_business` ほか。**取り出すのは店の名前と URL と id と公開の有無だけ**。`mail_address`・
  `nickname`・`shop_introduction`・`external_service_account` は読み捨てる（出力・写し・ログに出さない）。
- `GET https://apiv2.thebase.com/api/items`（scope `items.read`）→ **配列**（包む鍵は無い）。`limit` 1〜100・`offset`。
  総件数は返らず、`limit` 未満なら終わり。欄は `id`（int）・`name`・`price`（int|null・税込の円）・`stock`（int）・
  `visible`（bool）・`sales_status`（`item_waiting_for_sale`・`item_now_on_sale`・`item_end_of_sale`）・
  `images[]{url, scaled_url, square_url}`・`modified_at`（ISO 8601・UTC）ほか。`detail`（説明の本文）は持たない。
- 断りは Problem Details（`type` が `/errors/{resource}/{reason}`）。401 `/errors/auth/unauthenticated`（期限切れも）・
  403 `/errors/auth/forbidden`（scope が足りない）・503 `/errors/internal/maintenance`。
- **商品頁の URL は応答に無い**。組み方（店の URL ＋ 商品の id）は公式の頁で確かめられなかったので組まない
  （`item_url` は None・下書きは店の URL を載せてその旨を書く）。

β のため欄が変わりうる: 要る欄が無い・型が違えば `store_api_changed` で断り、写しを進めない。知らない欄は無視する。
再試行しない。1 回の読みのページの上限は `MAX_PAGES`、ページの間は `MIN_INTERVAL_SECONDS` 空ける（控えめに）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

# 店に寄らない行の部品は `rows.py`（3 つの店で共通）。今の試験と呼ぶ側が使う名前はここにも残す。
from .rows import NAME_MAX, ROW_FIELDS, URL_MAX, _CONTROL, buyable, clean_text, snapshot_row  # noqa: F401
from .rows import https_url as _https_url
from .rows import is_int as _int
from .wire import BASE_ORIGIN, StoreRefused, default_transport

PAGE_LIMIT = 100
MAX_PAGES = 5
MIN_INTERVAL_SECONDS = 1.0
SLEEP = time.sleep
SALES_STATUSES = ("item_waiting_for_sale", "item_now_on_sale", "item_end_of_sale")
_TYPE = re.compile(r"/errors/[a-z_]+/[a-z_-]+\Z")


def refusal_for(status: int, raw: bytes) -> StoreRefused:
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        payload = None
    kind = payload.get("type") if isinstance(payload, dict) else None
    detail = kind if isinstance(kind, str) and _TYPE.fullmatch(kind) else f"HTTP {status}"
    if status == 401:
        return StoreRefused("store_token_rejected", detail)
    if status == 403:
        return StoreRefused("store_scope_missing", detail)
    if status >= 500 or status == 429:
        return StoreRefused("store_unavailable", detail)
    return StoreRefused("store_request_rejected", detail)


class Client:
    """1 つの命令で 1 つ。鍵は呼ぶ側が読んで渡す（ここは値を出さない）。"""

    def __init__(self, access_token: str, *, transport=None):
        self._token = access_token
        self.transport = transport or default_transport
        self.calls = 0

    def _get(self, path: str, params: dict | None = None):
        url = BASE_ORIGIN + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        self.calls += 1
        status, raw = self.transport("GET", url, headers)
        if status != 200:
            raise refusal_for(status, raw)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreRefused("store_api_changed", "not_json") from None

    def user(self) -> dict:
        """店の id・名前・URL・公開の有無だけ。ほかの欄（メール・紹介文・連携先）は捨てる。"""
        payload = self._get("/api/user")
        if (not isinstance(payload, dict) or not isinstance(payload.get("shop_id"), str) or not payload["shop_id"]
                or _https_url(payload.get("shop_url")) is None):
            raise StoreRefused("store_api_changed", "api_user")
        able = payload.get("able_to_business")
        return {"shop_id": clean_text(payload["shop_id"], 64), "shop_name": clean_text(payload.get("shop_name")),
                "shop_url": payload["shop_url"], "able_to_business": able if isinstance(able, bool) else None}

    def items(self) -> dict:
        """`{"items": [...], "complete": bool, "pages": n}`。上限のページまで読んで終わらなければ complete は偽。"""
        rows, complete, pages = [], False, 0
        for page in range(MAX_PAGES):
            if page:
                SLEEP(MIN_INTERVAL_SECONDS)
            payload = self._get("/api/items", {"limit": PAGE_LIMIT, "offset": page * PAGE_LIMIT})
            pages += 1
            if not isinstance(payload, list):
                raise StoreRefused("store_api_changed", "api_items")
            rows.extend(normalize(item) for item in payload)
            if len(payload) < PAGE_LIMIT:
                complete = True
                break
        seen, out = set(), []
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
        return {"items": out, "complete": complete, "pages": pages}


def normalize(item) -> dict:
    """1 商品を許した欄だけに。要る欄が無い・型が違えば `store_api_changed`。"""
    if not isinstance(item, dict) or not _int(item.get("id")) or not isinstance(item.get("visible"), bool):
        raise StoreRefused("store_api_changed", "item_fields")
    if not item["visible"]:
        return {"id": item["id"], "visible": False}
    price = item.get("price")
    if (not isinstance(item.get("name"), str) or not (price is None or _int(price)) or not _int(item.get("stock"))
            or item.get("sales_status") not in SALES_STATUSES or not isinstance(item.get("images"), list)
            or not isinstance(item.get("modified_at"), str)):
        raise StoreRefused("store_api_changed", "item_fields")
    first = item["images"][0] if item["images"] and isinstance(item["images"][0], dict) else {}
    return {"id": item["id"], "name": clean_text(item["name"]), "price": price, "stock": item["stock"],
            "visible": True, "sales_status": item["sales_status"], "image_url": _https_url(first.get("url")),
            "item_url": None, "modified_at": clean_text(item["modified_at"], 40)}

