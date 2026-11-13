"""店を読む口の網と断り（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §5）。

**網に出るのはここの `default_transport` の 1 か所**。柵は「**決まった道に、決まった中身だけ**」:

- 送り先のホストは表（`ROUTES`）のものだけ。BASE は定数 `BASE_HOST`、STORES は定数 `STORES_HOST`、Shopify は
  「店の myshopify のラベル＋定数の `SHOPIFY_SUFFIX`」（台帳に持つのはラベルだけ・ホストや URL は受けない）。
- (メソッド, 道) と問い合わせの引数の名前は表にあるものだけ。BASE と STORES は GET だけ。Shopify は POST だけ
  （鍵を取る口と Admin GraphQL。GraphQL の決まりで POST だが、店は変えない）。
- STORES（5.18.0）のクレデンシャルに何が許されているかを THTH は選べない（登録の項目に権限が無い【公式】・注文も
  入金も書く道も通ると見る【推測】）。
  だから注文を読まない柵は**この表だけ**: 道は `/retail/202211/store` と `/retail/202211/items` の 2 つ、引数は
  `limit` と `offset` だけ（`ids`・`keyword`・`status` も使わない）。
- POST の本文も見る: 鍵を取る口は決まった 3 つの欄の form、GraphQL は `QUERIES` の文のどれかと**1 字も違わず**、
  `variables` は許した鍵だけ。呼ぶ側は URL・問い合わせの文・ヘッダを渡せない（`get`・`shopify_token`・
  `shopify_graphql` がここで組む）。外れたら打たずに `store_request_rejected（blocked_request）`。
- **リダイレクトは同じホストでも追わない**（表に無い道へ運ばれうるので）。3xx は `(数, 本文)` で返り、口が
  `store_request_rejected（redirect）` に直す。
- 再試行しない。例外は Shopify だけで、短い鍵の取り直しの 1 回（`access.py`）と、点数の断りのあとに同じ文を
  打ち直すとき（`THROTTLED` は待ってから・`MAX_COST_EXCEEDED` と `INTERNAL_SERVER_ERROR` は `first` を小さくして・
  回数の上限は `shopify.py`）。打ち直しもこの柵を通る（文・変数の鍵・`first` の幅は同じ）。

試験は `transport` を差し替える（本物の網に出ない）。`transport(method, url, headers, body=None) -> (status, body_bytes)`。
GET のときは 3 つの引数で呼ぶ（BASE の今の偽の transport を直さずに済む）。4xx・5xx・3xx も (status, body) で返す。
届かないときは `StoreRefused("store_unavailable")`。

**秘密を出さない**: 断りの文は符丁と静的な語（形を検査したもの・HTTP の数）だけ。応答の本文・鍵は断りに入れない。
鍵（`Authorization`・`X-Shopify-Access-Token`・client secret）を載せるのはここの 3 つの口だけで、行く先は表の
ホストだけ。応答に入っている URL（商品頁・画像）と、台帳の `public_url`（STORES の店の頁・持ち主が入れた値）には
何も送らない。
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe

BASE_HOST = "apiv2.thebase.com"
BASE_ORIGIN = "https://" + BASE_HOST
# 呼んでよい道（注文 `/api/orders` などは入れない・設計 §2）。
BASE_PATHS = frozenset(("/api/user", "/api/items"))

# STORES（5.18.0・設計 Shopify と STORES §7）: 版 202211 の 2 つの道だけ。注文 `/retail/202211/orders`・入金・
# 販売チャネル・商品 1 件の道・書く道は入れない（クレデンシャルはそれらも通ると見る【推測】ので、柵はこの表）。
STORES_HOST = "api.stores.dev"
STORES_API_VERSION = "202211"
STORES_PATHS = frozenset((f"/retail/{STORES_API_VERSION}/store", f"/retail/{STORES_API_VERSION}/items"))

# Shopify（5.17.0）: ホストは「ラベル＋定数の後ろ」。ラベルは myshopify の形だけ（点・斜線・@・大文字を含まない）。
SHOPIFY_SUFFIX = ".myshopify.com"
SHOPIFY_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,58}[a-z0-9])?\Z")
SHOPIFY_API_VERSION = "2026-10"
SHOPIFY_TOKEN_PATH = "/admin/oauth/access_token"
SHOPIFY_GRAPHQL_PATH = f"/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
# 商品 1 つで読む種類（variant）の数（5.18.1・種類ごとの値段で上下を決める・監査 5.18.0 の 5）。種類の値段は
# `ProductVariant.price`（店の通貨の Money）と `legacyResourceId` だけを読む（在庫の品・品番・お客さんの欄は読まない）。
# 点数（1 回 `SHOPIFY_QUERY_COST_MAX` まで・公式）は商品の頁の大きさ（shopify.PAGE_FIRST）と掛け算になるので、
# 頁を 25 件にして見積り（728 点・数え方と出所は `shopify.py` の頭）を上限の 8 割の内に置く（試験が文から数え直して
# 縛る）。【推測】本物の店で点数を見たことはない。外れたときは `shopify.py` が `first` を小さくして読み続ける。
SHOPIFY_VARIANTS_FIRST = 20
SHOPIFY_QUERY_COST_MAX = 1000
# 問い合わせの文はこの 2 本だけ（呼ぶ側は名前で選ぶだけ・文を渡せない）。欄の名前は公式の `Shop`・`Product`・
# `products`・`currentAppInstallation` の頁（2026-11-12 に読んだ・版 2026-10）による。
QUERIES = {
    "shop": "query ThthShop { shop { id name myshopifyDomain url currencyCode } "
            "currentAppInstallation { accessScopes { handle } } }",
    "products": "query ThthProducts($first: Int!, $after: String) { "
                "products(first: $first, after: $after, sortKey: ID) { "
                "nodes { id legacyResourceId title status onlineStoreUrl totalInventory tracksInventory updatedAt "
                "priceRangeV2 { minVariantPrice { amount currencyCode } } "
                "featuredMedia { preview { image { url } } } "
                f"variants(first: {SHOPIFY_VARIANTS_FIRST}) {{ nodes {{ legacyResourceId price }} "
                "pageInfo { hasNextPage } } } "
                "pageInfo { hasNextPage endCursor } } }",
}
# 問い合わせごとに許した variables の鍵。
QUERY_VARIABLES = {"shop": frozenset(), "products": frozenset(("first", "after"))}
PRODUCTS_FIRST_MAX = 50
AFTER_MAX = 1024
_TOKEN_FIELDS = ("grant_type", "client_id", "client_secret")
_POST = "POST"

# Square（実店舗のレジ・設計 実店舗 §5.1・計画 実店舗 段 5）: **GET だけ**。道は店舗の一覧・品の一覧・在庫の数（1 品の
# 種類ずつ）の 3 つ。品の検索 `/v2/catalog/search` と在庫のまとめ読み `/v2/inventory/counts/batch-retrieve` は POST なので
# 開けない（計画 §8 の 5「GET で足りるなら POST は開けない」）。注文・支払い・返金・顧客・スタッフ・勤怠の道は表に入れない。
# 個人の鍵は scope の無い全部の権限の鍵【公式】なので、この表が「読むだけ」の柵のすべて。
SQUARE_HOST = "connect.squareup.com"
# API の版（`Square-Version`・2026-11-13 に変更履歴で「Latest」と読めた版）。版を上げるときは試験の材料も見直す。
SQUARE_VERSION = "2026-09-16"
SQUARE_LOCATIONS_PATH = "/v2/locations"
SQUARE_CATALOG_PATH = "/v2/catalog/list"
# 在庫の数の道 `/v2/inventory/{catalog_object_id}`。id は英大文字と数字だけ（`changes`・`physical-counts`・
# `adjustments`・`transfers` など英小文字の道は形で外れる）。
SQUARE_INVENTORY_PATH = re.compile(r"/v2/inventory/[A-Z0-9]{1,64}\Z")
# 品の一覧で読む種類（`types` の値はこの 2 つの形だけ・CATEGORY は持ち主が出す品を選ぶときだけ）。
SQUARE_TYPES = frozenset(("ITEM,IMAGE", "ITEM,IMAGE,CATEGORY"))
SQUARE_ID = re.compile(r"[A-Z0-9]{1,64}\Z")
SQUARE_CURSOR_MAX = 2048

# スマレジ（プラットフォーム API・計画 実店舗 段 5）: 鍵を取る口（`id.smaregi.jp` に POST 1 つ・本文は決まった 2 つの欄）と、
# 商品の一覧・店舗の 1 件・在庫の一覧の GET 3 つ。取引・会員・スタッフ・勤怠の道は表に入れない。scope も読む 3 つだけを頼む
# （鍵を取るときに頼んだ scope しか鍵に付かない【公式: scope は要求する API スコープ】）。本番だけ（サンドボックスの
# `.dev` のホストは入れない）。
SMAREGI_ID_HOST = "id.smaregi.jp"
SMAREGI_API_HOST = "api.smaregi.jp"
SMAREGI_CONTRACT = re.compile(r"[A-Za-z0-9_]{1,40}\Z")
SMAREGI_SCOPE = "pos.products:read pos.stores:read pos.stock:read"
SMAREGI_TOKEN_PATH = re.compile(r"/app/[A-Za-z0-9_]{1,40}/token\Z")
SMAREGI_PRODUCTS_PATH = re.compile(r"/[A-Za-z0-9_]{1,40}/pos/products\Z")
SMAREGI_STORE_PATH = re.compile(r"/[A-Za-z0-9_]{1,40}/pos/stores/[0-9]{1,9}\Z")
SMAREGI_STOCK_PATH = re.compile(r"/[A-Za-z0-9_]{1,40}/pos/stock\Z")
# 応答で受ける欄（`fields`）。この文字列のどれかと 1 字も違わないものだけ（店舗の住所・電話・商品の原価などを受けない）。
SMAREGI_FIELDS = {
    "products": "productId,categoryId,productName,price,stockControlDivision",
    "store": "storeId,storeName",
    "stock": "productId,storeId,stockAmount",
}
_SMAREGI_TOKEN_FIELDS = ("grant_type", "scope")

# 道の表（店の種類 → ホストの決め方・(メソッド, 道)・問い合わせの引数の名前）。
# `patterns` は id を含む道（(メソッド, 形, 引数の名前)）。`values` は引数の値を縛る（名前 → 受ける値か形）。
ROUTES = {
    "base": {"host": BASE_HOST, "paths": frozenset(("GET", p) for p in BASE_PATHS),
             "params": frozenset(("limit", "offset"))},
    "stores": {"host": STORES_HOST, "paths": frozenset(("GET", p) for p in STORES_PATHS),
               "params": frozenset(("limit", "offset"))},
    "shopify": {"suffix": SHOPIFY_SUFFIX,
                "paths": frozenset(((_POST, SHOPIFY_TOKEN_PATH), (_POST, SHOPIFY_GRAPHQL_PATH))),
                "params": frozenset()},
    "square": {"host": SQUARE_HOST,
               "paths": frozenset((("GET", SQUARE_LOCATIONS_PATH), ("GET", SQUARE_CATALOG_PATH))),
               "patterns": (("GET", SQUARE_INVENTORY_PATH, frozenset(("location_ids",))),),
               "path_params": {SQUARE_LOCATIONS_PATH: frozenset(), SQUARE_CATALOG_PATH: frozenset(("types", "cursor"))},
               "params": frozenset(("types", "cursor", "location_ids")),
               "values": {"types": SQUARE_TYPES, "location_ids": SQUARE_ID,
                          "cursor": re.compile(r"[!-~]{1,%d}\Z" % SQUARE_CURSOR_MAX)}},
    "smaregi": {"host": SMAREGI_API_HOST, "paths": frozenset(),
                "patterns": (("GET", SMAREGI_PRODUCTS_PATH, frozenset(("fields", "limit", "page"))),
                             ("GET", SMAREGI_STORE_PATH, frozenset(("fields",))),
                             ("GET", SMAREGI_STOCK_PATH, frozenset(("fields", "store_id", "product_id")))),
                "params": frozenset(("fields", "limit", "page", "store_id", "product_id")),
                "values": {"fields": frozenset(SMAREGI_FIELDS.values()), "store_id": re.compile(r"[0-9]{1,9}\Z"),
                           "product_id": re.compile(r"[0-9]{1,15}\Z"), "limit": re.compile(r"[0-9]{1,4}\Z"),
                           "page": re.compile(r"[0-9]{1,3}\Z")}},
    "smaregi_id": {"host": SMAREGI_ID_HOST, "paths": frozenset(),
                   "patterns": ((_POST, SMAREGI_TOKEN_PATH, frozenset()),),
                   "params": frozenset()},
}
# 道ごとに**要る**引数（道 か 道の形 → 名前 → 受ける値か形）。無ければ打たない（監査 5.19.0 直したほうがよい 5 の a）。
# スマレジは `fields` が無いと全部の欄（原価・店舗の住所と電話など）が返るので、3 つの道とも、その道の `fields` と
# 1 字も違わない値が要る。在庫は 1 つの店舗の 1 つの商品だけ。Square の品の一覧は `types`、在庫は `location_ids` が要る。
REQUIRED = {
    "square": {SQUARE_CATALOG_PATH: {"types": SQUARE_TYPES},
               SQUARE_INVENTORY_PATH: {"location_ids": SQUARE_ID}},
    "smaregi": {SMAREGI_PRODUCTS_PATH: {"fields": frozenset((SMAREGI_FIELDS["products"],))},
                SMAREGI_STORE_PATH: {"fields": frozenset((SMAREGI_FIELDS["store"],))},
                SMAREGI_STOCK_PATH: {"fields": frozenset((SMAREGI_FIELDS["stock"],)),
                                     "store_id": re.compile(r"[0-9]{1,9}\Z"), "product_id": re.compile(r"[0-9]{1,15}\Z")}},
}
TIMEOUT = 30
MAX_BODY = 5_000_000

# 断りの符丁（この口が出すもの全部・目録 `thth/refusal_catalog_part9.py` に 3 つがある）。
REASONS = frozenset((
    "store_args_invalid", "store_name_taken", "store_missing", "store_ledger_invalid",
    "store_token_missing", "store_token_input_unsafe", "store_token_empty", "store_token_expired",
    "store_token_rejected", "store_scope_missing", "store_shop_mismatch",
    "store_api_changed", "store_unavailable", "store_request_rejected",
    "store_read_incomplete", "store_draft_nothing", "store_draft_exists", "store_draft_account",
    "store_draft_rejected",
    # 監査 5.15.0 の直し（11/12）。
    "store_token_expires_past", "store_draft_no_baseline", "store_not_open", "store_item_changed",
    "store_remove_unconfirmed", "store_remove_unsafe", "store_log_unavailable",
    # Shopify（5.17.0・設計 Shopify と STORES §4.2・§4.3）。
    "store_org_mismatch", "store_refresh_rejected", "store_refresh_busy", "store_shop_unknown",
    "store_currency_unsupported",
    # 実店舗のレジ（Square・スマレジ・計画 実店舗 段 5・目録は `thth/refusal_catalog_part_pos.py`）。
    "pos_args_invalid", "pos_token_rejected", "pos_scope_missing", "pos_location_unknown", "pos_api_changed",
    "pos_unavailable", "pos_currency_unsupported", "pos_publish_empty", "pos_read_limit",
))
# POST を打ってよい種類（本文を表で縛る口がある種類だけ）。Square・BASE・STORES・スマレジの API のホストは入れない。
POST_KINDS = frozenset(("shopify", "smaregi_id"))
_DETAIL = re.compile(r"[A-Za-z0-9_./: -]{1,80}\Z")


class StoreRefused(Exception):
    """店を読む口の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None, **extra):
        if code not in REASONS:
            code = "store_request_rejected"
        self.code = code
        self.detail = detail if isinstance(detail, str) and _DETAIL.fullmatch(detail) else None
        self.extra = extra
        super().__init__(code)

    def __str__(self) -> str:
        return f"{self.code}（{self.detail}）" if self.detail else self.code


def _blocked():
    return StoreRefused("store_request_rejected", "blocked_request")


def shopify_host(label) -> str:
    """店のラベルから Shopify のホスト。ラベルが形に合わなければ打たない。"""
    if not isinstance(label, str) or not SHOPIFY_LABEL.fullmatch(label):
        raise _blocked()
    return label + SHOPIFY_SUFFIX


def _kind_for(netloc: str) -> str | None:
    """ホスト（port・userinfo の無い netloc そのまま・大文字は直さない）から店の種類。"""
    if netloc == BASE_HOST:
        return "base"
    if netloc == STORES_HOST:
        return "stores"
    if netloc.endswith(SHOPIFY_SUFFIX) and SHOPIFY_LABEL.fullmatch(netloc[:-len(SHOPIFY_SUFFIX)]):
        return "shopify"
    if netloc == SQUARE_HOST:
        return "square"
    if netloc == SMAREGI_API_HOST:
        return "smaregi"
    if netloc == SMAREGI_ID_HOST:
        return "smaregi_id"
    return None


def _allowed_names(route: dict, method: str, path: str) -> frozenset | None:
    """(メソッド, 道) が表にあれば、その道で受ける引数の名前。無ければ None（打たない）。"""
    if (method, path) in route["paths"]:
        return route.get("path_params", {}).get(path, route["params"])
    for wanted, pattern, names in route.get("patterns", ()):
        if method == wanted and pattern.fullmatch(path):
            return names
    return None


def _required_for(kind: str, path: str) -> dict:
    """その道に要る引数（名前 → 受ける値か形）。要る物が無い道は空。"""
    for key, wanted in REQUIRED.get(kind, {}).items():
        if (key == path) if isinstance(key, str) else bool(key.fullmatch(path)):
            return wanted
    return {}


def _value_ok(rule, value: str) -> bool:
    if isinstance(rule, frozenset):
        return value in rule
    return bool(rule.fullmatch(value))


def _check_token_body(body: bytes) -> None:
    try:
        pairs = urllib.parse.parse_qsl(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        raise _blocked() from None
    keys = [k for k, _v in pairs]
    if sorted(keys) != sorted(_TOKEN_FIELDS) or dict(pairs).get("grant_type") != "client_credentials":
        raise _blocked()


def _check_smaregi_token_body(body: bytes) -> None:
    """スマレジの鍵を取る本文: `grant_type=client_credentials` と、読む 3 つの scope（`SMAREGI_SCOPE`）だけ。"""
    try:
        pairs = urllib.parse.parse_qsl(body.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        raise _blocked() from None
    if sorted(k for k, _v in pairs) != sorted(_SMAREGI_TOKEN_FIELDS) or dict(pairs) != {
            "grant_type": "client_credentials", "scope": SMAREGI_SCOPE}:
        raise _blocked()


def _check_graphql_body(body: bytes) -> None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _blocked() from None
    if not isinstance(payload, dict) or not set(payload) <= {"query", "variables"}:
        raise _blocked()
    name = next((n for n, text in QUERIES.items() if payload.get("query") == text), None)
    variables = payload.get("variables", {})
    if name is None or not isinstance(variables, dict) or not set(variables) <= QUERY_VARIABLES[name]:
        raise _blocked()
    if name == "products":
        first, after = variables.get("first"), variables.get("after")
        if (not isinstance(first, int) or isinstance(first, bool) or not 1 <= first <= PRODUCTS_FIRST_MAX
                or not (after is None or (isinstance(after, str) and 0 < len(after) <= AFTER_MAX))):
            raise _blocked()


def check_request(method: str, url: str, body: bytes | None = None) -> None:
    """表にある (ホスト, メソッド, 道, 引数の名前, 本文) だけ。外れたら打たない（柵）。"""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except (ValueError, TypeError, AttributeError):
        raise _blocked() from None
    if (parts.scheme != "https" or port is not None or parts.username or parts.password or parts.fragment
            or "@" in parts.netloc):
        raise _blocked()
    kind = _kind_for(parts.netloc)
    route = ROUTES.get(kind) if kind else None
    allowed = _allowed_names(route, method, parts.path) if route is not None else None
    if allowed is None:
        raise _blocked()
    try:
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        raise _blocked() from None
    names = [k for k, _v in pairs]
    if not set(names) <= allowed or len(names) != len(set(names)):
        raise _blocked()
    values = route.get("values", {})
    if any(name in values and not _value_ok(values[name], value) for name, value in pairs):
        raise _blocked()
    given = dict(pairs)
    if any(name not in given or not _value_ok(rule, given[name]) for name, rule in _required_for(kind, parts.path).items()):
        raise _blocked()
    if method == "GET":
        if body is not None:
            raise _blocked()
        return
    if not isinstance(body, bytes) or parts.query or kind not in POST_KINDS:
        raise _blocked()
    if kind == "smaregi_id":
        _check_smaregi_token_body(body)
    elif parts.path == SHOPIFY_TOKEN_PATH:
        _check_token_body(body)
    else:
        _check_graphql_body(body)


def check_url(method: str, url: str) -> None:
    """本文の無い問い合わせの柵（BASE と STORES の GET・今の試験と偽の transport が使う名前）。"""
    check_request(method, url, None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """どのリダイレクトも追わない（同じホストの中でも）。3xx は HTTPError として口に返る。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise httpsafe.RedirectBlocked(req.full_url, code, "redirect", headers, fp)


OPENER = httpsafe.build_opener(redirect_handler=_NoRedirect())


def default_transport(method: str, url: str, headers: dict, body: bytes | None = None):
    check_request(method, url, body)   # 呼ぶ側が正しく組んでいても、打つ直前にもう 1 度
    if body is None:
        request = urllib.request.Request(url, method="GET", headers=headers)
    else:
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with httpsafe.urlopen(request, timeout=TIMEOUT, via=OPENER) as response:
            return response.status, response.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(100_000)
        except Exception:  # noqa: BLE001 — 本文が読めなくても数で決める
            raw = b""
        finally:
            exc.close()
        return exc.code, raw
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        # `http.client.IncompleteRead`（応答の途中で切れた）も `HTTPException` の仲間（監査 5.15.0 気づき 6）。
        raise StoreRefused("store_unavailable") from None


# ------------------------------------------------------------------ 呼ぶ側に見せる 3 つの口（URL と本文はここで組む）

# GET で読む店（`Authorization: Bearer`）。Shopify は POST の 2 つの口だけ。スマレジは鍵を取る POST 1 つ（`smaregi_token`）と GET。
GET_KINDS = frozenset(("base", "stores", "square", "smaregi"))
# 店の種類ごとに足すヘッダ（Square は API の版を決め打ちする）。
_EXTRA_HEADERS = {"square": {"Square-Version": SQUARE_VERSION}}


def get(kind: str, path: str, params: dict | None, token: str, *, transport=None):
    """BASE・STORES・Square・スマレジの GET（`Authorization: Bearer`）。`(status, body)`。"""
    route = ROUTES.get(kind)
    if kind not in GET_KINDS or route is None:
        raise _blocked()
    url = "https://" + route["host"] + path + ("?" + urllib.parse.urlencode(params) if params else "")
    check_request("GET", url)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", **_EXTRA_HEADERS.get(kind, {})}
    return (transport or default_transport)("GET", url, headers)


def smaregi_token(contract: str, client_id: str, client_secret: str, *, transport=None):
    """スマレジの client credentials で鍵を取る（本文は grant_type と読む 3 つの scope だけ・Basic）。`(status, body)`。"""
    if not isinstance(contract, str) or not SMAREGI_CONTRACT.fullmatch(contract):
        raise _blocked()
    url = "https://" + SMAREGI_ID_HOST + f"/app/{contract}/token"
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": SMAREGI_SCOPE}).encode("ascii")
    check_request(_POST, url, body)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
               "Accept": "application/json"}
    return (transport or default_transport)(_POST, url, headers, body)


def shopify_token(shop: str, client_id: str, client_secret: str, *, transport=None):
    """Shopify の client credentials で短い鍵を取る（本文は決まった 3 つの欄の form）。`(status, body)`。"""
    url = "https://" + shopify_host(shop) + SHOPIFY_TOKEN_PATH
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                   "client_secret": client_secret}).encode("ascii")
    check_request(_POST, url, body)
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    return (transport or default_transport)(_POST, url, headers, body)


def shopify_graphql(shop: str, name: str, variables: dict | None, token: str, *, transport=None):
    """Shopify の Admin GraphQL を `QUERIES` の名前で打つ（文は渡せない）。`(status, body)`。"""
    if name not in QUERIES:
        raise _blocked()
    url = "https://" + shopify_host(shop) + SHOPIFY_GRAPHQL_PATH
    payload = {"query": QUERIES[name]}
    if variables:
        payload["variables"] = variables
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    check_request(_POST, url, body)
    headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Shopify-Access-Token": token}
    return (transport or default_transport)(_POST, url, headers, body)
