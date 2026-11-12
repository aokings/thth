"""Shopify を読む口（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §4・5.17.0）。

打つのは `wire.py` の決まった 2 つの問い合わせ（`QUERIES` の shop と products）と、鍵を取る口（client credentials）
だけ。GraphQL の決まりで POST だが、店は変えない。欄の名前は公式（2026-11-12 に読んだ・版 2026-10）による:

- `shop { id name myshopifyDomain url currencyCode }`・`currentAppInstallation { accessScopes { handle } }`。
  取り出すのは myshopify のドメイン・名前・URL・通貨・鍵に許された scope の名前の並びだけ。
- `products(first, after, sortKey: ID)` の `nodes`: `legacyResourceId`・`title`・`status`・`onlineStoreUrl`・
  `totalInventory`・`tracksInventory`・`updatedAt`・`priceRangeV2.minVariantPrice`・`featuredMedia.preview.image.url`・
  `variants(first: 20).nodes` の `legacyResourceId`・`price`（5.18.1）と `variants.pageInfo.hasNextPage`。
  `pageInfo.hasNextPage`・`endCursor`。注文・お客さんの欄は問い合わせに無い。

行への直し方（`rows.ROW_FIELDS` は変えない）: id は `int(legacyResourceId)`・公開は `status == ACTIVE` かつ
`onlineStoreUrl` が https（偽なら id と visible だけ）・値段は種類の中で**いちばん安い**値段（円の店だけ・整数の円）・
在庫は `tracksInventory` が偽なら None（数えない）・`sales_status` は `active`・商品頁の URL は `onlineStoreUrl`・
種類ごとの値段（`variant_prices`・5.18.1）は `{str(legacyResourceId): 円}`。種類が 20 を超えて読み切れない商品は
None（比べられない・上下を言わない・`diff.py`）。値段の上下はこちらで決める（`price` はいちばん安い値なので、
いちばん安い種類が無くなっただけでも上がって見える・監査 5.18.0 の 5）。

**【未確認】の所は偽の応答で作った**（本物の店では 1 度も動かしていない・設計 §11）: 詰まったときの形
（200 で `errors[].extensions.code` が `THROTTLED`）・scope が足りないときの形（`ACCESS_DENIED`）・鍵が違うときの
401 の本文・`shop_not_permitted` の応答の HTTP の数と本文の形・402 と 423（凍った店）の数。形に寄りかからず、
HTTP の数と、検査した符丁の語だけで決める。`errors` の文・応答の本文は断りに入れない。

**点数（query cost）の見積り**（5.18.1・2026-11-13 に公式を読んで数え直した）。出所:
`https://shopify.dev/docs/apps/build/apis/graphql-admin/rate-limits`（数え方の表・1 回 1000 点まで・頼んだ点と
使った点・`extensions.cost` の形）、`https://shopify.dev/docs/api/admin-graphql/latest`（`MAX_COST_EXCEEDED` の例の
`extensions.cost`・`maxCost`・`THROTTLED`・`INTERNAL_SERVER_ERROR`）、`https://shopify.dev/docs/api/usage/limits`
（待つのは 1 秒が目安）、`https://shopify.engineering/rate-limiting-graphql-apis-calculating-query-complexity`
（connection は「2＋返すと見込む物の数」・cursor と pageInfo は無料、2021 年の記事）。決まり: scalar と enum は 0 点、
物は 1 点、interface は選んだ物の最大、connection は `first` で大きさが決まる。頼んだ点（requestedQueryCost）は
実行の前に文と `first` だけで決まり（店の商品の数・種類の数には寄らない）、1000 点を超えると打つ前に
`MAX_COST_EXCEEDED`。

数え方（`QUERIES["products"]`・頁の大きさ f・種類の数 v＝`VARIANTS_FIRST`）。pageInfo は記事では無料、今の表では
物なので 1 点として**多めに**数える:

- 商品 1 つ＝商品 1 ＋ `priceRangeV2` 1 ＋ `minVariantPrice` 1 ＋ `featuredMedia`（interface）1 ＋ `preview` 1 ＋
  `image` 1 ＋ 種類の connection（2 ＋ v × 種類 1 ＋ その pageInfo 1）＝ 6 ＋ (3 ＋ v)。v＝20 で 29 点。
- 頁＝ 2 ＋ f × 29 ＋ 頁の pageInfo 1。f＝25 で **728 点**（pageInfo を無料と見れば 702 点）。上限 1000 の
  8 割（`COST_BUDGET`＝800）の内。5.18.1 の前の見積り 727 は頁の pageInfo を数えていなかった（1 点違い）。
- 公式は「Shopify は欄に手で点を付けることがある」とも書くので、ここの数は**下限の見込み**で、本物の店の
  `requestedQueryCost` で確かめるまでは【推測】。試験（`tests/test_store_shopify_cost.py`）が文から数え直して縛る。

見込みが外れても読みを止めない作り（`Client.items`・`Client._query`）:

- 応答の `requestedQueryCost` が `COST_BUDGET` を超えていれば、次の頁から頁を比で小さくする（打ち直しは無い）。
- `MAX_COST_EXCEEDED` なら、`extensions.cost`（数だけ読む）との比で頁を小さくして**同じ文で**打ち直す（読めなければ
  半分）。`INTERNAL_SERVER_ERROR`（公式: 頁が大きく入れ子の connection があると起きうる）なら半分にして打ち直す。
  小さくするのは 1 回の読みで `MAX_SHRINKS` 回まで・`MIN_PAGE_FIRST` 件まで。読める商品の数（`MAX_ITEMS`＝500）は保つ。
- `THROTTLED` なら、`throttleStatus` から足りない点が戻るまで（読めなければ公式の目安の 1 秒）待って、同じ問い合わせを
  `THROTTLE_RETRIES` 回まで打ち直す。1 回の待ちは `MAX_THROTTLED_WAIT_SECONDS` まで。
- **頁の間の待ち**（`throttle_wait`）: 応答の残りの点が次に頼む点より少なければ、足りない点が戻るまで（＋
  `MIN_INTERVAL_SECONDS`・1 回は `MAX_THROTTLE_WAIT_SECONDS` まで）待つ。足りていれば `MIN_INTERVAL_SECONDS`。
  種類の多い 500 件の店は頼む点の和がバケツより大きいので、この待ちは混んでいなくても 2 分ほどになる（直す前の
  「残りが頼む点の 2 倍より少なければ頼む点 ÷ 戻る速さで長くて 5 秒」は、5 秒では足りず THROTTLED を招いた）。
- **1 回の読み全体にも上限**（監査 5.18.1 直したほうがよい 2）: 上の回数は問い合わせごとなので、頁ごとに混んでいると
  合わせて数十回・数分になりうる。1 回の読み（`Client` 1 つ）で、打ち直しは合わせて `READ_RESENDS_MAX` 回まで
  （`THROTTLED` の待ちと、頁を小さくしての打ち直しの和）・**混んだせいで**（`THROTTLED` のあと）眠るのは合わせて
  `READ_WAIT_SECONDS_MAX` 秒まで。頁の間の調子合わせの待ちはこれに数えない（監査 5.18.1 の確かめの危ない 1:
  数えると、混んでいない店でも種類の多い 300〜500 件で毎回止まった）。頁の間の待ちは別に、合わせて
  `READ_PACE_SECONDS_MAX` 秒まで（混んでいない標準の店の種類 20 の 500 件は 2 分ほどで、その 1.5 倍）。どちらも
  超えそうなら眠らず・打たずに `store_unavailable（read_budget）` で断る。承認の読み直し（`recheck.check`）も同じ
  `items()` を通るので、確定がその間より長く止まらない。
- 網に出るのは今までどおり `wire.py` だけ・文は 2 本・変数は `first` と `after` だけ（`first` を変えるだけ）。
"""
from __future__ import annotations

import decimal
import json
import re
import time

from .. import redact
from . import access, wire
from .rows import VARIANT_ID, VARIANT_PRICES, clean_text, https_url
from .wire import StoreRefused

# 頁は 25 件（種類を 20 まで読むので点数が掛け算になる・`wire.SHOPIFY_VARIANTS_FIRST`・頭の説明の数え方で 728 点）。
# 1 回で読める商品の数は 500（5.17.0 と同じ）。頁を小さくしたときも 500 まで読めるよう、頁の数の上限は
# `MAX_ITEMS // MIN_PAGE_FIRST`。
PAGE_FIRST = 25
MAX_ITEMS = 500
MIN_PAGE_FIRST = 5
MAX_PAGES = MAX_ITEMS // MIN_PAGE_FIRST
MAX_SHRINKS = 2
VARIANTS_FIRST = wire.SHOPIFY_VARIANTS_FIRST
# 頼む点の目安（上限の 8 割）。見込みの数（728）も、応答の requestedQueryCost も、これを超えれば頁を小さくする。
COST_BUDGET = wire.SHOPIFY_QUERY_COST_MAX * 8 // 10
THROTTLE_RETRIES = 2
# 1 回の読み全体の上限。打ち直しの和と、混んだせい（`THROTTLED`）で眠る秒の和（頭の説明）。
READ_RESENDS_MAX = 4
READ_WAIT_SECONDS_MAX = 60.0
# 頁の間の待ちの和の上限（別に持つ）。混んでいない標準の店（2,000 点・毎秒 100 点）の種類 20 の 500 件は約 120 秒、
# 頁を 5 件まで小さくしても点に余りがあれば 0.5 秒 × 99。どちらも収まる大きさ（`tests/test_store_shopify_cost.py`）。
READ_PACE_SECONDS_MAX = 180.0
MAX_THROTTLED_WAIT_SECONDS = 10.0
MIN_INTERVAL_SECONDS = 0.5
UNKNOWN_COST_WAIT_SECONDS = 1.0
MAX_THROTTLE_WAIT_SECONDS = 10.0
PACE_FACTOR_STEP = 1.5
PACE_FACTOR_MAX = 3.0
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


def _first_error_extensions(errors) -> dict:
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    return first.get("extensions") if isinstance(first.get("extensions"), dict) else {}


def error_code(errors) -> str | None:
    """`errors[0].extensions.code` の形を検査した語（大文字と _ だけ）。無ければ None。"""
    code = _first_error_extensions(errors).get("code")
    return code if isinstance(code, str) and _CODE.fullmatch(code) else None


def exceeded_cost(errors) -> int | None:
    """`MAX_COST_EXCEEDED` の `extensions.cost`（頼んだ点・公式の例にある数）。数でなければ None（文は読まない）。"""
    value = _number(_first_error_extensions(errors).get("cost"))
    return int(value) if value is not None and 0 < value < 10_000_000 else None


def errors_refusal(errors) -> StoreRefused:
    """200 で `errors` があるとき。`extensions.code` を形で検査した語だけを見る（文は入れない）。"""
    code = error_code(errors)
    if code == "THROTTLED":
        return StoreRefused("store_unavailable", "throttled")
    if code == "ACCESS_DENIED":
        return StoreRefused("store_scope_missing", "ACCESS_DENIED")
    if code == "MAX_COST_EXCEEDED":
        # 点数の見積り（`wire.SHOPIFY_VARIANTS_FIRST`・`PAGE_FIRST`）が外れたとき。詳細で分かるようにする（5.18.1）。
        return StoreRefused("store_api_changed", "max_cost_exceeded")
    if code == "INTERNAL_SERVER_ERROR":
        # 公式: 500 の代わりに 200 で返す Shopify の中の誤り。頁を小さくしても続けば断る（5.18.1）。
        return StoreRefused("store_unavailable", "internal_error")
    return StoreRefused("store_api_changed", "graphql_errors")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def throttle_wait(payload, factor: float = 1.0) -> float:
    """次の頁までに空ける秒。残りの点が頼む点（この頁と同じと見る）より少なければ、足りない点が戻るまで
    （× `factor` ＋ `MIN_INTERVAL_SECONDS`・長くて `MAX_THROTTLE_WAIT_SECONDS`）。足りていれば `MIN_INTERVAL_SECONDS`。
    読めなければ 1 秒。`factor` は、ほかのアプリが点を使っていて戻る速さが見かけより遅い店のための余裕
    （`Client` が `THROTTLED` のたびに上げる）。"""
    try:
        cost = payload["extensions"]["cost"]
        requested = _number(cost["requestedQueryCost"])
        status = cost["throttleStatus"]
        available, restore = _number(status["currentlyAvailable"]), _number(status["restoreRate"])
    except (KeyError, TypeError):
        return max(MIN_INTERVAL_SECONDS, UNKNOWN_COST_WAIT_SECONDS)
    if requested is None or available is None or not restore or restore <= 0:
        return max(MIN_INTERVAL_SECONDS, UNKNOWN_COST_WAIT_SECONDS)
    if available < requested:
        return min((requested - available) / restore * factor + MIN_INTERVAL_SECONDS, MAX_THROTTLE_WAIT_SECONDS)
    return MIN_INTERVAL_SECONDS


def throttled_wait(payload) -> float:
    """`THROTTLED` のあと打ち直すまでの秒。足りない点（頼んだ点 − 今ある点）が戻るまで。読めなければ公式の目安の 1 秒。"""
    try:
        cost = payload["extensions"]["cost"]
        requested = _number(cost["requestedQueryCost"])
        status = cost["throttleStatus"]
        available, restore = _number(status["currentlyAvailable"]), _number(status["restoreRate"])
    except (KeyError, TypeError):
        return UNKNOWN_COST_WAIT_SECONDS
    if requested is None or available is None or not restore or restore <= 0:
        return UNKNOWN_COST_WAIT_SECONDS
    return max(UNKNOWN_COST_WAIT_SECONDS, min((requested - available) / restore, MAX_THROTTLED_WAIT_SECONDS))


def requested_cost(payload) -> int | None:
    """応答の `extensions.cost.requestedQueryCost`（数だけ）。無ければ None。"""
    try:
        value = _number(payload["extensions"]["cost"]["requestedQueryCost"])
    except (KeyError, TypeError):
        return None
    return int(value) if value is not None and 0 < value < 10_000_000 else None


def smaller_first(asked: int, cost: int | None) -> int:
    """頁を小さくした大きさ。頼んだ点が分かれば `COST_BUDGET` に収まる比で、分からなければ半分（必ず asked より小さい）。"""
    size = asked * COST_BUDGET // cost if cost and cost > 0 else asked // 2
    return min(size, asked - 1)


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


def _amount_yen(text) -> int:
    """種類の値段（`ProductVariant.price`・店の通貨の Money の文字列）→ 円の整数。店の通貨は `user()` と
    `minVariantPrice.currencyCode` で円と確かめてある。小数があれば `store_currency_unsupported（fraction）`。"""
    if not isinstance(text, str) or not 0 < len(text) <= 40:
        raise StoreRefused("store_api_changed", "item_variants")
    return yen({"amount": text, "currencyCode": CURRENCY})


def variant_prices(node: dict) -> dict | None:
    """`{str(legacyResourceId): 円}`。読み切れない（`hasNextPage`）なら None。形が違えば `store_api_changed`。"""
    variants = node.get("variants")
    nodes = variants.get("nodes") if isinstance(variants, dict) else None
    info = variants.get("pageInfo") if isinstance(variants, dict) else None
    if not isinstance(nodes, list) or not nodes or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
        raise StoreRefused("store_api_changed", "item_variants")
    out = {}
    for variant in nodes:
        legacy = variant.get("legacyResourceId") if isinstance(variant, dict) else None
        legacy = str(legacy) if isinstance(legacy, (str, int)) and not isinstance(legacy, bool) else ""
        if not _DIGITS.fullmatch(legacy) or not VARIANT_ID.fullmatch(legacy) or legacy in out:
            raise StoreRefused("store_api_changed", "item_variants")
        out[legacy] = _amount_yen(variant.get("price"))
    return None if info["hasNextPage"] else out


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
            "item_url": url, "modified_at": clean_text(node["updatedAt"], 40), VARIANT_PRICES: variant_prices(node)}


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
        self.throttled = 0
        self.resends = 0
        self.slept = 0.0
        self.paced = 0.0
        self.pace_factor = 1.0

    def _sleep(self, seconds: float) -> None:
        """混んだせい（`THROTTLED`）で眠る（1 回の読みで合計 `READ_WAIT_SECONDS_MAX` まで。超えるなら眠らずに断る）。"""
        if self.slept + seconds > READ_WAIT_SECONDS_MAX:
            raise StoreRefused("store_unavailable", "read_budget")
        self.slept += seconds
        SLEEP(seconds)

    def _pace(self, seconds: float) -> None:
        """頁の間の調子合わせで眠る（`READ_WAIT_SECONDS_MAX` には数えない・別に合計 `READ_PACE_SECONDS_MAX` まで）。"""
        if self.paced + seconds > READ_PACE_SECONDS_MAX:
            raise StoreRefused("store_unavailable", "read_budget")
        self.paced += seconds
        SLEEP(seconds)

    def _resend(self) -> None:
        """同じ問い合わせを打ち直す前に数える（1 回の読みで合わせて `READ_RESENDS_MAX` 回まで）。"""
        if self.resends >= READ_RESENDS_MAX:
            raise StoreRefused("store_unavailable", "read_budget")
        self.resends += 1

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

    def _send(self, name: str, variables: dict | None) -> dict:
        token = self._access()
        self.calls += 1
        status, raw = wire.shopify_graphql(self.shop, name, variables, token, transport=self.transport)
        if status == 401 and self.auth == "shopify_client":
            # 置き場の鍵が断られたら、取り直して同じ問い合わせを 1 回だけ打ち直す。
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
        return payload

    def _query(self, name: str, variables: dict | None = None) -> dict:
        """打ち直すのは 3 つの時だけ: 鍵の取り直し（401・1 回）・`THROTTLED`（待って `THROTTLE_RETRIES` 回まで）・
        頁を小さくして（`items` が `MAX_COST_EXCEEDED` と `INTERNAL_SERVER_ERROR` のときに・同じ文で）。"""
        for attempt in range(THROTTLE_RETRIES + 1):
            payload = self._send(name, variables)
            errors = payload.get("errors")
            if not errors:
                break
            exc = errors_refusal(errors)
            code = error_code(errors)
            if code == "THROTTLED" and attempt < THROTTLE_RETRIES:
                self._resend()
                self._sleep(throttled_wait(payload))
                self.throttled += 1
                # 頁の間の待ちが足りなかった（ほかのアプリが点を使っている）。次の頁からは多めに待つ。
                self.pace_factor = min(self.pace_factor * PACE_FACTOR_STEP, PACE_FACTOR_MAX)
                continue
            exc.shrink = code in ("MAX_COST_EXCEEDED", "INTERNAL_SERVER_ERROR")
            exc.cost = exceeded_cost(errors) if code == "MAX_COST_EXCEEDED" else None
            raise exc
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
        """`{"items": [...], "complete": bool, "pages": n, "adjusted": {...} か None}`。

        `MAX_ITEMS` 件か `MAX_PAGES` 頁まで読んで終わらなければ complete は偽。点数の見込みが外れたら頁を小さくして
        読み続け（頭の説明）、何をしたかを `adjusted` に返す（頁の大きさ・小さくした訳・待って打ち直した回数・
        見えた頼んだ点の最大）。
        """
        rows, complete, pages, after, wait = [], False, 0, None, None
        first, shrinks, seen_cost = PAGE_FIRST, [], None
        while pages < MAX_PAGES and len(rows) < MAX_ITEMS:
            if wait is not None:
                self._pace(wait)
            asked = min(first, MAX_ITEMS - len(rows))
            try:
                payload = self._query("products", {"first": asked, "after": after})
            except StoreRefused as exc:
                if not getattr(exc, "shrink", False) or len(shrinks) >= MAX_SHRINKS:
                    raise
                smaller = smaller_first(asked, exc.cost)
                if smaller < MIN_PAGE_FIRST:
                    raise
                self._resend()
                shrinks.append({"from": asked, "to": smaller, "why": exc.detail})
                first, wait = smaller, MIN_INTERVAL_SECONDS
                continue
            pages += 1
            products = payload["data"].get("products")
            nodes = products.get("nodes") if isinstance(products, dict) else None
            info = products.get("pageInfo") if isinstance(products, dict) else None
            if (not isinstance(nodes, list) or not isinstance(info, dict)
                    or not isinstance(info.get("hasNextPage"), bool)):
                raise StoreRefused("store_api_changed", "products")
            rows.extend(normalize(node) for node in nodes)
            cost = requested_cost(payload)
            if cost is not None:
                seen_cost = max(cost, seen_cost or 0)
                if cost > COST_BUDGET:
                    # 打つ前の見込み（728）より Shopify の数が大きい。次の頁から比で小さくする（打ち直しは無い）。
                    smaller = max(MIN_PAGE_FIRST, smaller_first(asked, cost))
                    if smaller < first:
                        shrinks.append({"from": asked, "to": smaller, "why": "requested_cost"})
                        first = smaller
            if not info["hasNextPage"]:
                complete = True
                break
            after = info.get("endCursor")
            if not isinstance(after, str) or not 0 < len(after) <= wire.AFTER_MAX:
                raise StoreRefused("store_api_changed", "products")
            wait = throttle_wait(payload, self.pace_factor)
        seen, out = set(), []
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
        adjusted = None
        if shrinks or self.throttled:
            adjusted = {"page_first": first, "shrinks": shrinks, "throttled_retries": self.throttled,
                        "requested_cost_max": seen_cost}
        return {"items": out, "complete": complete, "pages": pages, "adjusted": adjusted}
