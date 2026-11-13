"""Square（実店舗のレジ）を読む口（計画 `docs/計画_実店舗_2026-11-13.md` 段 5・設計 `docs/設計_実店舗_2026-11-13.md` §5.1・§9.2）。

**打つのは GET だけ**（`wire.ROUTES["square"]`・柵は `tests/test_store_pos_wire.py`）。道は 3 つ:

- `GET https://connect.squareup.com/v2/locations` → `{"locations": [...]}`。台帳の `location`（読む店舗の id・1 つ）の
  1 件から、`merchant_id`（店の持ち主の id・鍵の取り違えを見分ける）・`name`・`status`・`business_hours.periods`
  （曜日 `day_of_week`・`start_local_time`・`end_local_time`）だけを取り出す。住所・電話・メール・座標・SNS の欄は読み捨てる。
  権限は `MERCHANT_PROFILE_READ`【公式】。
- `GET /v2/catalog/list?types=ITEM,IMAGE&cursor=…` → `{"objects": [...], "cursor": …}`（1 頁 100 件【公式】）。
  品（ITEM）の `item_data.name`・`variations[]`（種類・`item_variation_data.price_money`・`track_inventory`・
  `location_overrides[]` の `location_id`・`price_money`・`track_inventory`・`sold_out`）・`categories[].id`・
  `image_ids`・`is_archived` と、店舗に置いているか（`present_at_all_locations`・`present_at_location_ids`・
  `absent_at_location_ids`）、`updated_at`。画像（IMAGE）は `image_data.url` だけ。説明・品番・税・SKU は読み捨てる。
  権限は `ITEMS_READ`【公式】。
- `GET /v2/inventory/{種類の id}?location_ids=<店舗>` → `{"counts": [{"catalog_object_id", "state", "location_id",
  "quantity"}]}`。**出してよい品の、在庫を数える種類だけ**を 1 つずつ読む（まとめて読む道は POST なので開けない）。
  `sold_out` が真の種類は打たずに 0 と見る（公式「真なら在庫の数は 0 と見る」）。権限は `INVENTORY_READ`【公式】。

行への直し方（`rows.ROW_FIELDS` は変えない）:

- **持ち主が出してよいとした品（`pos.is_published`）だけが公開の行**。ほかは `{"id", "visible": False}`（名前も持たない）。
  出してよい品でも、この店舗に置いていない・アーカイブした品は公開の行にしない。
- 値段は種類の値段（この店舗の上書きがあればそれ・可変の値段は None）。行の `price` はいちばん安い種類の値段。
  円（JPY）でなければ `pos_currency_unsupported`。種類ごとの値段は `variant_prices`。
- 在庫は、在庫を数えない種類が 1 つでもあれば None（数えない）、全部数えるならその店舗の `IN_STOCK` の合計。
  在庫の記録の無い種類は 0【推測】。
- `sales_status` は `on_sale`（Square の品に販売の期間の欄は無い【推測】）。商品頁の URL は None（店の頁は台帳の
  `public_url`）。

**本物の Square の店では 1 度も動かしていない**（応答は公式の頁の欄の名前から作った偽・`pos.UNVERIFIED`）。
"""
from __future__ import annotations

import json
import re
import time

from . import pos, wire
from .rows import VARIANT_PRICES, clean_text, https_url
from .wire import StoreRefused

PAGE_LIMIT = 100           # 品の一覧の 1 頁（Square が決める・公式）
MAX_PAGES = 10
INVENTORY_MAX = 50         # 1 回の読みで在庫の数を読む種類の上限（超えたら `pos_read_limit`）
MIN_INTERVAL_SECONDS = 0.2
SLEEP = time.sleep
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_TIME = re.compile(r"([0-9]{1,2}):([0-9]{2})(:[0-9]{2})?\Z")
_ID = re.compile(r"[A-Za-z0-9]{1,64}\Z")


def present_at(obj: dict, location: str) -> bool:
    """その店舗に置いているか（`present_at_all_locations` が無ければ真・公式の既定）。"""
    if obj.get("present_at_all_locations") is False:
        return location in (obj.get("present_at_location_ids") or [])
    return location not in (obj.get("absent_at_location_ids") or [])


def _hhmm(value) -> str:
    matched = _TIME.fullmatch(value) if isinstance(value, str) else None
    if not matched or int(matched.group(1)) > 24 or int(matched.group(2)) > 59:
        raise StoreRefused("pos_api_changed", "business_hours")
    return f"{int(matched.group(1)):02d}:{matched.group(2)}"


def hours_of(location: dict) -> list:
    """曜日ごとの営業の時間 `[{"day": "MON", "start": "09:00", "end": "18:00"}]`（曜日と始まりの順）。無ければ空。"""
    found = location.get("business_hours")
    if found is None:
        return []
    periods = found.get("periods") if isinstance(found, dict) else None
    if periods is None:
        return []
    if not isinstance(periods, list):
        raise StoreRefused("pos_api_changed", "business_hours")
    out = []
    for period in periods:
        if not isinstance(period, dict) or period.get("day_of_week") not in DAYS:
            raise StoreRefused("pos_api_changed", "business_hours")
        out.append({"day": period["day_of_week"], "start": _hhmm(period.get("start_local_time")),
                    "end": _hhmm(period.get("end_local_time"))})
    return sorted(out, key=lambda p: (DAYS.index(p["day"]), p["start"], p["end"]))


def _money(money) -> int | None:
    if money is None:
        return None
    if not isinstance(money, dict):
        raise StoreRefused("pos_api_changed", "price_money")
    if money.get("currency") != "JPY":
        raise StoreRefused("pos_currency_unsupported", str(money.get("currency"))[:8] if money.get("currency") else None)
    return pos.yen(money.get("amount"), field="price_money")


class Client:
    """1 つの命令で 1 つ。鍵は呼ぶ側が読んで渡す（ここは値を出さない）。"""

    def __init__(self, cfg: dict, token: str, *, transport=None):
        self.cfg = cfg
        self.location = cfg["location"]
        self._token = token
        self.transport = transport
        self.calls = 0

    def _get(self, path: str, params: dict | None = None):
        if self.calls:
            SLEEP(MIN_INTERVAL_SECONDS)
        self.calls += 1
        status, raw = wire.get("square", path, params, self._token, transport=self.transport)
        if status != 200:
            raise pos.refusal_for(status)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreRefused("pos_api_changed", "not_json") from None
        if not isinstance(payload, dict):
            raise StoreRefused("pos_api_changed", "not_object")
        return payload

    # ------------------------------------------------------------------ 店舗

    def _location(self) -> dict:
        payload = self._get(wire.SQUARE_LOCATIONS_PATH)
        found = payload.get("locations")
        if not isinstance(found, list):
            raise StoreRefused("pos_api_changed", "locations")
        for location in found:
            if isinstance(location, dict) and location.get("id") == self.location:
                return location
        # 選べる店舗の id と名前（持ち主の店の店舗の名前だけ・住所は出さない）を断りに添える。
        choices = [{"id": loc["id"], "name": clean_text(loc.get("name"), 80)} for loc in found
                   if isinstance(loc, dict) and isinstance(loc.get("id"), str) and wire.SQUARE_ID.fullmatch(loc["id"])]
        raise StoreRefused("pos_location_unknown", locations=choices[:20])

    def user(self) -> dict:
        location = self._location()
        merchant = location.get("merchant_id")
        if not isinstance(merchant, str) or not _ID.fullmatch(merchant) or not isinstance(location.get("name"), str):
            raise StoreRefused("pos_api_changed", "location")
        return {"shop_id": merchant, "shop_name": clean_text(location["name"]), "shop_url": self.cfg["public_url"],
                "able_to_business": location.get("status") == "ACTIVE", "location": self.location,
                "hours": hours_of(location)}

    # ------------------------------------------------------------------ 品

    def _objects(self, types: str):
        objects, cursor, pages, complete = [], None, 0, False
        for _page in range(MAX_PAGES):
            params = {"types": types}
            if cursor:
                params["cursor"] = cursor
            payload = self._get(wire.SQUARE_CATALOG_PATH, params)
            pages += 1
            found = payload.get("objects", [])
            if not isinstance(found, list):
                raise StoreRefused("pos_api_changed", "objects")
            objects.extend(found)
            cursor = payload.get("cursor")
            if not cursor:
                complete = True
                break
            if not isinstance(cursor, str) or len(cursor) > wire.SQUARE_CURSOR_MAX:
                raise StoreRefused("pos_api_changed", "cursor")
        return objects, complete, pages

    @staticmethod
    def _category_ids(item_data: dict) -> list:
        ids = [c.get("id") for c in (item_data.get("categories") or []) if isinstance(c, dict)]
        if isinstance(item_data.get("category_id"), str):
            ids.append(item_data["category_id"])
        return [i for i in ids if isinstance(i, str)]

    def _variations(self, item_data: dict) -> list:
        """この店舗に置いている種類の `(id, 値段, 在庫を数えるか, 売り切れの印)`。"""
        out = []
        found = item_data.get("variations")
        if not isinstance(found, list):
            raise StoreRefused("pos_api_changed", "variations")
        for variation in found:
            if not isinstance(variation, dict) or not isinstance(variation.get("id"), str) \
                    or not wire.SQUARE_ID.fullmatch(variation["id"]):
                raise StoreRefused("pos_api_changed", "variation_id")
            if not present_at(variation, self.location):
                continue
            data = variation.get("item_variation_data") or {}
            if not isinstance(data, dict):
                raise StoreRefused("pos_api_changed", "variation")
            override = next((o for o in (data.get("location_overrides") or [])
                             if isinstance(o, dict) and o.get("location_id") == self.location), {})
            if data.get("pricing_type") == "VARIABLE_PRICING" or override.get("pricing_type") == "VARIABLE_PRICING":
                price = None
            else:
                price = _money(override["price_money"]) if "price_money" in override else _money(data.get("price_money"))
            track = override["track_inventory"] if "track_inventory" in override else data.get("track_inventory")
            out.append((variation["id"], price, track is True, override.get("sold_out") is True))
        return out

    def _count(self, variation_id: str) -> int:
        payload = self._get(f"/v2/inventory/{variation_id}", {"location_ids": self.location})
        counts = payload.get("counts", [])
        if not isinstance(counts, list):
            raise StoreRefused("pos_api_changed", "counts")
        total = 0
        for count in counts:
            if (isinstance(count, dict) and count.get("catalog_object_id") == variation_id
                    and count.get("location_id") == self.location and count.get("state") == "IN_STOCK"):
                total += pos.whole(count.get("quantity"), field="quantity")
        return total   # 【推測】記録の無い種類は 0

    def items(self) -> dict:
        """`{"items", "complete", "pages", "hours", "inventory_calls"}`。出してよい品だけ在庫を読む。"""
        location = self._location()
        objects, complete, pages = self._objects("ITEM,IMAGE")
        images = {o.get("id"): https_url((o.get("image_data") or {}).get("url")) for o in objects
                  if isinstance(o, dict) and o.get("type") == "IMAGE"}
        publish = self.cfg["publish"]
        rows, seen, inventory_calls = [], set(), 0
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "ITEM" or obj.get("is_deleted") is True:
                continue
            item_id = obj.get("id")
            if not isinstance(item_id, str) or not _ID.fullmatch(item_id):
                raise StoreRefused("pos_api_changed", "item_id")
            if item_id in seen:
                continue
            seen.add(item_id)
            data = obj.get("item_data")
            if not isinstance(data, dict):
                raise StoreRefused("pos_api_changed", "item_data")
            if (not pos.is_published(publish, item_id, self._category_ids(data)) or data.get("is_archived") is True
                    or not present_at(obj, self.location)):
                rows.append({"id": item_id, "visible": False})
                continue
            if not isinstance(data.get("name"), str):
                raise StoreRefused("pos_api_changed", "item_name")
            variations = self._variations(data)
            if not variations:
                rows.append({"id": item_id, "visible": False})
                continue
            stock = 0
            for variation_id, _price, track, sold_out in variations:
                if not track:
                    stock = None
                    break
                if sold_out:
                    continue
                inventory_calls += 1
                if inventory_calls > INVENTORY_MAX:
                    raise StoreRefused("pos_read_limit", "inventory")
                stock += self._count(variation_id)
            prices = [p for _v, p, _t, _s in variations if p is not None]
            image_ids = data.get("image_ids") or []
            rows.append({"id": item_id, "name": clean_text(data["name"]), "price": min(prices) if prices else None,
                         "stock": stock, "visible": True, "sales_status": "on_sale",
                         "image_url": images.get(image_ids[0]) if isinstance(image_ids, list) and image_ids else None,
                         "item_url": None,
                         "modified_at": clean_text(obj.get("updated_at"), 40) or None,
                         VARIANT_PRICES: {v: p for v, p, _t, _s in variations}})
        return {"items": rows, "complete": complete, "pages": pages, "hours": hours_of(location),
                "inventory_calls": inventory_calls}

    def candidates(self) -> dict:
        """持ち主が出す品を選ぶための一覧（名前と部門の名前・**写しにもファイルにも残さない**）。在庫は読まない。"""
        self._location()
        objects, complete, _pages = self._objects("ITEM,IMAGE,CATEGORY")
        names = {o.get("id"): clean_text((o.get("category_data") or {}).get("name"), 80) for o in objects
                 if isinstance(o, dict) and o.get("type") == "CATEGORY"}
        out = []
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "ITEM" or obj.get("is_deleted") is True:
                continue
            data = obj.get("item_data") or {}
            item_id = obj.get("id")
            if not isinstance(item_id, str) or not _ID.fullmatch(item_id) or not present_at(obj, self.location):
                continue
            cats = self._category_ids(data)
            out.append({"id": item_id, "name": clean_text(data.get("name")),
                        "categories": [{"id": c, "name": names.get(c) or ""} for c in cats if _ID.fullmatch(c)],
                        "published": pos.is_published(self.cfg["publish"], item_id, cats),
                        "archived": data.get("is_archived") is True})
        return {"candidates": out, "complete": complete}
