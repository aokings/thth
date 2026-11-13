"""実店舗のレジ（Square・スマレジ）の網の柵（`thth/shop/wire.py`・計画 実店舗 段 5・§6 の 6）。

柵「Square には GET 以外を打たない」:
  1. 道の表の Square の (メソッド, 道) は GET だけ（id を含む道の形も GET だけ）。
  2. Square のホストへの GET 以外（POST・PUT・PATCH・DELETE）は、どの道でも打たずに断る。品の検索と在庫のまとめ読み
     （本物では POST の道）も断る。
  3. `square.py` は網の口のうち GET の口（`wire.get`）しか呼ばない（POST の口の名前を書いていない）。
  4. 表に無い道は GET でも断る（注文・支払い・返金・顧客・スタッフ・勤怠・在庫の動き・品の 1 件など）。引数の名前と値も縛る。
柵「スマレジは鍵を取る POST 1 つと GET だけ」:
  5. POST は `id.smaregi.jp` の `/app/{契約}/token` だけで、本文は grant_type と読む 3 つの scope と 1 字も違わない。
  6. GET は商品・店舗・在庫の 3 つだけ・`fields` は決まった文字列だけ。取引・会員・スタッフ・勤怠の道は断る。
柵「売上・顧客・支払いの道の名前がコードに現れない」:
  7. Square とスマレジの道の表と形、`square.py`・`smaregi.py` の文字列の定数に、売上・顧客・支払いの道の語が無い。
本物の網には出ない。
"""
from __future__ import annotations

import ast
import pathlib
import urllib.parse

import pytest

from thth.shop import wire
from thth.shop.wire import StoreRefused

SQ = "https://connect.squareup.com"
SM = "https://api.smaregi.jp"
SMID = "https://id.smaregi.jp"
C = "nigamic01"
SHOP_DIR = pathlib.Path(wire.__file__).parent
# 売上・顧客・支払い・スタッフ・勤怠の道の語（Square とスマレジの API の道の名前・英語）。
FORBIDDEN_WORDS = ("order", "payment", "refund", "customer", "card", "invoice", "subscription", "team", "labor",
                   "shift", "transaction", "member", "staff", "loyalty", "gift", "dispute", "payout", "bank",
                   "cash", "booking", "timecard", "terminal", "checkout", "employee")


def _refused(method, url, body=None):
    with pytest.raises(StoreRefused) as exc:
        wire.check_request(method, url, body)
    assert exc.value.code == "store_request_rejected" and exc.value.detail == "blocked_request"


# ------------------------------------------------------------------ Square: GET だけ

def test_柵_Squareの道の表はGETだけ():
    route = wire.ROUTES["square"]
    assert {m for m, _p in route["paths"]} == {"GET"}
    assert {m for m, _p, _n in route.get("patterns", ())} == {"GET"}
    assert {p for _m, p in route["paths"]} == {"/v2/locations", "/v2/catalog/list"}


def test_柵_SquareにGET以外は打たない():
    good = (SQ + "/v2/locations", SQ + "/v2/catalog/list?types=ITEM%2CIMAGE", SQ + "/v2/inventory/VAR00010?location_ids=L1")
    for url in good:
        wire.check_request("GET", url)
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            _refused(method, url.split("?")[0], b"{}")
            _refused(method, url.split("?")[0])
    # 本物では POST の読む道（品の検索・在庫のまとめ読み・トークンの様子）も開けない。
    for path in ("/v2/catalog/search", "/v2/catalog/search-catalog-items", "/v2/catalog/batch-retrieve",
                 "/v2/inventory/counts/batch-retrieve", "/v2/inventory/changes/batch-retrieve",
                 "/oauth2/token/status", "/oauth2/token", "/v2/orders/search"):
        _refused("POST", SQ + path, b"{}")
        _refused("GET", SQ + path)


SQUARE_DENIED = (
    "/v2/orders", "/v2/orders/ORD1", "/v2/payments", "/v2/refunds", "/v2/customers", "/v2/customers/C1",
    "/v2/team-members", "/v2/labor/shifts", "/v2/cards", "/v2/invoices", "/v2/subscriptions", "/v2/merchants",
    "/v2/merchants/me", "/v2/locations/L1", "/v2/catalog/object/ITEM1", "/v2/catalog/info",
    "/v2/inventory/VAR1/changes", "/v2/inventory/changes", "/v2/inventory/physical-counts/X1",
    "/v2/inventory/adjustments/X1", "/v2/inventory/transfers/X1", "/v2/inventory/var1", "/v2/inventory/",
    "/v2/locations/", "/v2/catalog/list/", "/v2/bank-accounts", "/v2/payouts", "/v2/gift-cards",
)


def test_Squareは表に無い道をGETでも打たない():
    for path in SQUARE_DENIED:
        _refused("GET", SQ + path)
    for url in ("http://connect.squareup.com/v2/locations", "https://connect.squareup.com:443/v2/locations",
                "https://x@connect.squareup.com/v2/locations", "https://connect.squareupsandbox.com/v2/locations",
                "https://connect.squareup.com.evil.example/v2/locations", "https://CONNECT.SQUAREUP.COM/v2/locations",
                "https://connect.squareup.com/v2/locations#x"):
        _refused("GET", url)


def test_Squareの引数は道ごとの名前と値だけ():
    for query in ("types=ITEM", "types=ITEM%2CIMAGE%2CCATEGORY%2CDISCOUNT", "types=ITEM_VARIATION",
                  "types=ITEM%2CIMAGE&types=ITEM%2CIMAGE", "catalog_version=1", "location_ids=L1",
                  "cursor=a%20b"):
        _refused("GET", SQ + "/v2/catalog/list?" + query)
    wire.check_request("GET", SQ + "/v2/catalog/list?types=ITEM%2CIMAGE%2CCATEGORY&cursor=abc")
    _refused("GET", SQ + "/v2/locations?types=ITEM%2CIMAGE")
    for query in ("location_ids=L1%2CL2", "location_ids=", "cursor=x", "types=ITEM%2CIMAGE"):
        _refused("GET", SQ + "/v2/inventory/VAR1?" + query)


def test_柵_square_pyはGETの口しか呼ばない():
    tree = ast.parse((SHOP_DIR / "square.py").read_text(encoding="utf-8"))
    used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "wire"}
    assert "get" in used
    assert not used & {"shopify_token", "shopify_graphql", "smaregi_token", "default_transport", "OPENER"}
    strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert not any(s.strip() in {"POST", "PUT", "PATCH", "DELETE"} for s in strings)


def test_Squareの口は版のヘッダを載せGETで打つ():
    seen = []
    wire.get("square", "/v2/locations", None, "tok", transport=lambda *a: seen.append(a) or (200, b"{}"))
    [(method, url, headers)] = seen
    assert method == "GET" and url == SQ + "/v2/locations"
    assert headers["Square-Version"] == wire.SQUARE_VERSION and headers["Authorization"] == "Bearer tok"


# ------------------------------------------------------------------ スマレジ: 鍵を取る POST 1 つと GET だけ

def _token_body(**fields):
    base = {"grant_type": "client_credentials", "scope": wire.SMAREGI_SCOPE}
    base.update(fields)
    return urllib.parse.urlencode({k: v for k, v in base.items() if v is not None}).encode()


def test_柵_スマレジのPOSTは鍵を取る口だけで本文は決まった2つの欄():
    wire.check_request("POST", SMID + f"/app/{C}/token", _token_body())
    for body in (_token_body(scope="pos.products:read pos.transactions:read"), _token_body(scope=None),
                 _token_body(grant_type="authorization_code"), _token_body(extra="1"),
                 _token_body(scope=wire.SMAREGI_SCOPE + " pos.customers:read"), b"not form", b""):
        _refused("POST", SMID + f"/app/{C}/token", body)
    _refused("GET", SMID + f"/app/{C}/token")
    _refused("POST", SMID + "/authorize/token", _token_body())
    _refused("POST", SMID + f"/app/{C}/token?x=1", _token_body())
    for path in (f"/{C}/pos/products", f"/{C}/pos/stock", f"/{C}/pos/stores/1", f"/{C}/pos/transactions"):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            _refused(method, SM + path, b"{}")
    assert {m for m, _p, _n in wire.ROUTES["smaregi"]["patterns"]} == {"GET"}
    assert {m for m, _p, _n in wire.ROUTES["smaregi_id"]["patterns"]} == {"POST"}


def test_スマレジのGETは3つの道と決まったfieldsだけ():
    f = urllib.parse.quote(wire.SMAREGI_FIELDS["products"], safe="")
    wire.check_request("GET", SM + f"/{C}/pos/products?fields={f}&limit=1000&page=1")
    wire.check_request("GET", SM + f"/{C}/pos/stores/1?fields=storeId%2CstoreName")
    wire.check_request("GET", SM + f"/{C}/pos/stock?fields=productId%2CstoreId%2CstockAmount&store_id=1&product_id=2")
    for path in (f"/{C}/pos/transactions", f"/{C}/pos/customers", f"/{C}/pos/staffs", f"/{C}/pos/stores",
                 f"/{C}/pos/products/1", f"/{C}/pos/products/1/images", f"/{C}/pos/stock/changes",
                 f"/{C}/timecard/attendances", f"/{C}/pos/members", "/pos/products", f"/{C}/pos/stores/a"):
        _refused("GET", SM + path)
    for query in ("fields=productId%2Ccost", "fields=productId%2CcategoryId%2CproductName%2Cprice%2CstockControlDivision%2Ccost",
                  "sort=productId", "limit=10&limit=20", "upd_date_time-from=x"):
        _refused("GET", SM + f"/{C}/pos/products?" + query)
    _refused("GET", SM + f"/{C}/pos/stores/1?fields=storeId%2CstoreName%2Caddress%2CphoneNumber")
    _refused("GET", SM + f"/{C}/pos/stock?store_id=a")
    for url in ("https://api.smaregi.dev/" + C + "/pos/products", "http://api.smaregi.jp/" + C + "/pos/products",
                "https://api.smaregi.jp.evil.example/" + C + "/pos/products"):
        _refused("GET", url)


def test_スマレジの鍵を取る口はBasicで本文を自分で組む():
    seen = []
    wire.smaregi_token(C, "cid", "sec", transport=lambda *a: seen.append(a) or (200, b"{}"))
    [(method, url, headers, body)] = seen
    assert method == "POST" and url == SMID + f"/app/{C}/token"
    assert headers["Authorization"].startswith("Basic ") and "sec" not in headers["Authorization"]
    assert dict(urllib.parse.parse_qsl(body.decode())) == {"grant_type": "client_credentials",
                                                           "scope": wire.SMAREGI_SCOPE}
    with pytest.raises(StoreRefused):
        wire.smaregi_token("a/b", "cid", "sec", transport=lambda *a: (200, b"{}"))


# ------------------------------------------------------------------ 売上・顧客・支払いの道の語

def _route_texts(kind):
    route = wire.ROUTES[kind]
    texts = [p for _m, p in route["paths"]] + [pat.pattern for _m, pat, _n in route.get("patterns", ())]
    texts += [p for p in route.get("path_params", {})]
    return texts


def test_柵_レジの道の表と口の文字列に売上と顧客と支払いの語が無い():
    for kind in ("square", "smaregi", "smaregi_id"):
        for text in _route_texts(kind):
            assert not any(w in text.lower() for w in FORBIDDEN_WORDS), (kind, text)
    assert not any(w in wire.SMAREGI_SCOPE for w in FORBIDDEN_WORDS)
    for name in ("square.py", "smaregi.py"):
        tree = ast.parse((SHOP_DIR / name).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                # 道の形をした文字列（/ で始まる）に禁じた語が入っていない。
                if node.value.startswith("/"):
                    assert not any(w in node.value.lower() for w in FORBIDDEN_WORDS), (name, node.value)
