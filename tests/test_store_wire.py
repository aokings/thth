"""店を読む口の網の柵（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §5・5.17.0）。

柵「決まった道に、決まった中身だけ」:
  1. 網の部品を使うのは wire.py だけ（`tests/test_store_base_audit.py` の今の試験・新しいファイルも自動で見る）。
  2. wire.py の Request は GET と POST の 2 か所・urlopen は 1 か所（同じく `test_store_base_audit.py`）。
  3. BASE と STORES のホストへの GET 以外は全部断る。表に無い道（STORES の注文・入金・商品 1 件など）は GET でも断る。
     STORES の引数は limit と offset だけ。
  4. Shopify: GET は全部断る。QUERIES に無い文（1 字足した・mutation を含む）・知らない variables の鍵は断る。
  5. QUERIES の文は `query ` で始まり、mutation・subscription・order・customer・draftOrder の語を含まない。
  6. 店のラベル: 点・斜線・@・大文字・空・61 字・ラベルなし・port つきは断る。
  7. 3xx は追わない（同じホストへのリダイレクトでも、次の網の呼び出しが起きない）。
本物の網には出ない（偽の HTTPS の handler）。
"""
from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request
import urllib.response
import email.message

import pytest

from thth.shop import wire
from thth.shop.wire import StoreRefused

SHOP = "nigami-test"
GQL = f"https://{SHOP}.myshopify.com/admin/api/{wire.SHOPIFY_API_VERSION}/graphql.json"
TOKEN_URL = f"https://{SHOP}.myshopify.com/admin/oauth/access_token"


def _gql(name="shop", variables=None, query=None):
    payload = {"query": query if query is not None else wire.QUERIES[name]}
    if variables is not None:
        payload["variables"] = variables
    return json.dumps(payload).encode()


def _token_body(**extra):
    fields = {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "sec", **extra}
    return urllib.parse.urlencode(fields).encode()


def _refused(method, url, body=None):
    with pytest.raises(StoreRefused) as exc:
        wire.check_request(method, url, body)
    assert exc.value.code == "store_request_rejected" and exc.value.detail == "blocked_request"


# ------------------------------------------------------------------ 3: BASE と STORES

def test_BASEはGETで表の道だけ():
    wire.check_request("GET", "https://apiv2.thebase.com/api/items?limit=100&offset=0")
    wire.check_request("GET", "https://apiv2.thebase.com/api/user")
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        _refused(method, "https://apiv2.thebase.com/api/items", b"{}")
        _refused(method, "https://apiv2.thebase.com/api/items")
    for path in ("/api/orders", "/api/items/1", "/api/users", "/api/items/"):
        _refused("GET", "https://apiv2.thebase.com" + path)
    _refused("GET", "https://apiv2.thebase.com/api/items?limit=1&status=x")       # 表に無い引数
    _refused("GET", "https://apiv2.thebase.com/api/items", b"")                    # GET に本文


# STORES（5.18.0）: クレデンシャルは権限を選べず注文も通る鍵なので、注文を読まない柵はこの表だけ。
STORES_DENIED_PATHS = (
    "/retail/202211/orders", "/retail/202211/orders/x", "/retail/202211/remittances",
    "/retail/202211/sales_channels", "/retail/202211/categories", "/retail/202211/items/x",
    "/retail/202211/items/x/variations", "/retail/202211/coupons", "/retail/202211/stock_adjustments",
    "/retail/202211/store/", "/retail/202211/items/", "/retail/202304/items", "/retail/items", "/api/items",
)


def test_STORESはGETで表の2つの道だけ():
    wire.check_request("GET", "https://api.stores.dev/retail/202211/store")
    wire.check_request("GET", "https://api.stores.dev/retail/202211/items?limit=100&offset=0")
    assert wire.ROUTES["stores"]["paths"] == {("GET", "/retail/202211/store"), ("GET", "/retail/202211/items")}
    assert {m for m, _p in wire.ROUTES["stores"]["paths"]} == {"GET"}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        _refused(method, "https://api.stores.dev/retail/202211/items", b"{}")
        _refused(method, "https://api.stores.dev/retail/202211/store")
    for path in STORES_DENIED_PATHS:
        _refused("GET", "https://api.stores.dev" + path)
    assert not any("order" in p for _m, p in wire.ROUTES["stores"]["paths"])
    # 引数は limit と offset だけ（ids・keyword・status・in_stock も使わない）。
    for query in ("ids=a", "keyword=x", "status=shown", "in_stock=true", "only_preorder_sales=true",
                  "limit=1&ids=a"):
        _refused("GET", "https://api.stores.dev/retail/202211/items?" + query)
    _refused("GET", "https://api.stores.dev/retail/202211/items", b"")                # GET に本文
    for url in ("http://api.stores.dev/retail/202211/items", "https://api.stores.dev:443/retail/202211/items",
                "https://x@api.stores.dev/retail/202211/items", "https://API.STORES.DEV/retail/202211/items",
                "https://api.stores.dev.evil.example/retail/202211/items", "https://stores.dev/retail/202211/items",
                "https://evil.api.stores.dev/retail/202211/items", "https://api.stores.jp/retail/202211/items",
                "https://api.stores.dev/retail/202211/items#x"):
        _refused("GET", url)


def test_STORESの口はURLを自分で組みBearerを載せる():
    seen = []

    def transport(method, url, headers, body=None):
        wire.check_request(method, url, body)
        seen.append((method, url, dict(headers), body))
        return 200, b"{}"

    wire.get("stores", "/retail/202211/items", {"limit": 100, "offset": 200}, "cred_x", transport=transport)
    [(method, url, headers, body)] = seen
    assert (method, body) == ("GET", None)
    assert url == "https://api.stores.dev/retail/202211/items?limit=100&offset=200"
    assert headers["Authorization"] == "Bearer cred_x"
    for path in ("/retail/202211/orders", "/retail/202211/items/x"):
        with pytest.raises(StoreRefused):
            wire.get("stores", path, None, "x", transport=lambda *a: pytest.fail("網に出た"))
    with pytest.raises(StoreRefused):
        wire.get("stores", "/retail/202211/items", {"ids": "a"}, "x", transport=lambda *a: pytest.fail("網に出た"))


# ------------------------------------------------------------------ 4・5: Shopify

def test_Shopifyは表の2つの道にPOSTで決まった中身だけ():
    wire.check_request("POST", GQL, _gql("shop"))
    wire.check_request("POST", GQL, _gql("products", {"first": 50, "after": None}))
    wire.check_request("POST", GQL, _gql("products", {"first": 1, "after": "eyJsYXN0X2lkIjoxfQ=="}))
    wire.check_request("POST", TOKEN_URL, _token_body())
    for url in (GQL, TOKEN_URL, f"https://{SHOP}.myshopify.com/admin/api/2026-10/orders.json"):
        _refused("GET", url)
    _refused("POST", f"https://{SHOP}.myshopify.com/admin/api/2026-07/graphql.json", _gql("shop"))   # 版が違う
    _refused("POST", f"https://{SHOP}.myshopify.com/admin/api/{wire.SHOPIFY_API_VERSION}/orders.json", _gql("shop"))
    _refused("POST", GQL + "?x=1", _gql("shop"))
    _refused("POST", GQL, None)


def test_QUERIESに無い文と知らないvariablesは断る():
    _refused("POST", GQL, _gql(query=wire.QUERIES["shop"] + " "))                 # 1 字足した
    _refused("POST", GQL, _gql(query=wire.QUERIES["shop"][:-1]))
    _refused("POST", GQL, _gql(query="mutation { productDelete(input: {id: \"x\"}) { deletedProductId } }"))
    _refused("POST", GQL, _gql(query="query { orders(first: 1) { nodes { id } } }"))
    _refused("POST", GQL, _gql("shop", {"first": 1}))                              # shop に variables は無い
    _refused("POST", GQL, _gql("products", {"first": 50, "query": "status:active"}))
    _refused("POST", GQL, _gql("products", {"first": 51}))
    _refused("POST", GQL, _gql("products", {"first": 0}))
    _refused("POST", GQL, _gql("products", {"first": True}))
    _refused("POST", GQL, _gql("products", {"first": 5, "after": "x" * 1025}))
    _refused("POST", GQL, json.dumps({"query": wire.QUERIES["shop"], "operationName": "x"}).encode())
    _refused("POST", GQL, b"not json")
    with pytest.raises(StoreRefused):
        wire.shopify_graphql(SHOP, "orders", None, "tok", transport=lambda *a: pytest.fail("網に出た"))


def test_鍵を取る口の本文は3つの欄だけ():
    _refused("POST", TOKEN_URL, _token_body(scope="read_orders"))
    _refused("POST", TOKEN_URL, _token_body(grant_type="authorization_code"))
    _refused("POST", TOKEN_URL, urllib.parse.urlencode({"grant_type": "client_credentials"}).encode())
    _refused("POST", TOKEN_URL, _gql("shop"))
    _refused("POST", TOKEN_URL, _token_body() + b"&client_id=again")


def test_QUERIESの文はqueryで始まり注文と書く語を含まない():
    assert set(wire.QUERIES) == {"shop", "products"}
    for name, text in wire.QUERIES.items():
        assert text.startswith("query "), name
        lowered = text.lower()
        for word in ("mutation", "subscription", "order", "customer", "draftorder"):
            assert word not in lowered, (name, word)


# ------------------------------------------------------------------ 6: 店のラベル

@pytest.mark.parametrize("label", ["a.b", "a/b", "a@b", "A", "Nigami", "", "x" * 61, "-a", "a-", "a_b", None,
                                   "a.myshopify.com.evil.example"])
def test_ラベルが形に合わなければ打たない(label):
    with pytest.raises(StoreRefused):
        wire.shopify_host(label)
    with pytest.raises(StoreRefused):
        wire.shopify_graphql(label, "shop", None, "tok", transport=lambda *a: pytest.fail("網に出た"))


@pytest.mark.parametrize("url", [
    "https://evil.example/admin/oauth/access_token",
    "https://myshopify.com/admin/oauth/access_token",
    "https://.myshopify.com/admin/oauth/access_token",
    f"https://{SHOP}.myshopify.com:8443/admin/oauth/access_token",
    f"https://{SHOP}.myshopify.com.evil.example/admin/oauth/access_token",
    f"https://u:p@{SHOP}.myshopify.com/admin/oauth/access_token",
    f"https://{SHOP.upper()}.myshopify.com/admin/oauth/access_token",
    f"https://a.b.myshopify.com/admin/oauth/access_token",
    f"http://{SHOP}.myshopify.com/admin/oauth/access_token",
    f"https://{SHOP}.myshopify.com/admin/oauth/access_token#x",
])
def test_ホストは店のラベルと定数の後ろだけ(url):
    _refused("POST", url, _token_body())


def test_61字までのラベルの端():
    assert wire.shopify_host("a" * 60) == "a" * 60 + ".myshopify.com"
    assert wire.shopify_host("a1-b2") == "a1-b2.myshopify.com"


def test_口はURLと本文を自分で組み同じ柵を通す():
    seen = []

    def transport(method, url, headers, body=None):
        wire.check_request(method, url, body)
        seen.append((method, url, dict(headers), body))
        return 200, b"{}"

    wire.shopify_graphql(SHOP, "products", {"first": 50, "after": None}, "shpat_x", transport=transport)
    wire.shopify_token(SHOP, "cid", "csecret", transport=transport)
    wire.get("base", "/api/items", {"limit": 1, "offset": 0}, "pat", transport=lambda m, u, h: (200, b"[]"))
    (m1, u1, h1, b1), (m2, u2, h2, b2) = seen
    assert (m1, u1) == ("POST", GQL) and h1["X-Shopify-Access-Token"] == "shpat_x" and "Authorization" not in h1
    assert (m2, u2) == ("POST", TOKEN_URL) and b"csecret" in b2 and "csecret" not in json.dumps(h2)
    with pytest.raises(StoreRefused):
        wire.get("shopify", "/api/items", None, "x", transport=lambda *a: pytest.fail("網に出た"))
    with pytest.raises(StoreRefused):
        wire.get("base", "/api/orders", None, "x", transport=lambda *a: pytest.fail("網に出た"))


# ------------------------------------------------------------------ 7: リダイレクトを追わない

class _FakeHTTPS(urllib.request.BaseHandler):
    """本物の網に出ない HTTPS の handler（呼ばれた URL をためて、決まった数と Location を返す）。"""

    handler_order = 100   # 本物の HTTPSHandler より先

    def __init__(self, code, location):
        self.code, self.location, self.seen = code, location, []

    def https_open(self, req):
        self.seen.append(req.full_url)
        headers = email.message.Message()
        if self.location:
            headers["Location"] = self.location
        response = urllib.response.addinfourl(io.BytesIO(b"moved"), headers, req.full_url, code=self.code)
        response.msg = "Found"
        return response


@pytest.mark.parametrize("location", [
    "https://apiv2.thebase.com/api/orders",                    # 同じホストの表に無い道
    "/api/items?limit=1&offset=0",                             # 同じホストの同じ道でも
    "https://evil.example/api/items",
])
def test_3xxは同じホストでも追わない(monkeypatch, location):
    fake = _FakeHTTPS(302, location)
    # 本物の口の opener と同じリダイレクトの handler を使う（wire.OPENER の作りを替えたら、ここが赤になる）。
    [redirect] = [h for h in wire.OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    opener = wire.httpsafe.build_opener(redirect_handler=type(redirect)())
    opener.add_handler(fake)
    monkeypatch.setattr(wire, "OPENER", opener)
    status, _raw = wire.default_transport("GET", "https://apiv2.thebase.com/api/items?limit=1&offset=0", {})
    assert status == 302 and fake.seen == ["https://apiv2.thebase.com/api/items?limit=1&offset=0"]


def test_3xxはBASEの口でredirectの断りになる(monkeypatch):
    from thth.shop import base
    exc = base.refusal_for(301, b"")
    assert exc.code == "store_request_rejected" and exc.detail == "redirect"
    client = base.Client("pat", transport=lambda m, u, h: (302, b""))
    with pytest.raises(StoreRefused) as caught:
        client.user()
    assert caught.value.detail == "redirect"


@pytest.mark.parametrize("location", [
    "https://api.stores.dev/retail/202211/orders",             # 同じホストの注文の道
    "/retail/202211/items?limit=100&offset=0",                 # 同じホストの同じ道でも
    "https://evil.example/retail/202211/items",
])
def test_STORESの3xxも同じホストでも追わず口でredirectの断りになる(monkeypatch, location):
    from thth.shop import stores
    fake = _FakeHTTPS(302, location)
    [redirect] = [h for h in wire.OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    opener = wire.httpsafe.build_opener(redirect_handler=type(redirect)())
    opener.add_handler(fake)
    monkeypatch.setattr(wire, "OPENER", opener)
    url = "https://api.stores.dev/retail/202211/items?limit=100&offset=0"
    status, _raw = wire.default_transport("GET", url, {})
    assert status == 302 and fake.seen == [url]
    client = stores.Client({"public_url": "https://x.example.invalid"}, "cred", transport=None)
    with pytest.raises(StoreRefused) as caught:
        client.items()
    assert caught.value.code == "store_request_rejected" and caught.value.detail == "redirect"
    assert fake.seen == [url, url]                             # 2 回目の命令でも、追った先は打っていない


# ------------------------------------------------------------------ 共通の網の守り（監査 5.17.0 直したほうがよい 2）

def test_httpsafeのviaはbuild_openerの作ったものだけ受ける():
    from thth import httpsafe

    class Bare:
        def open(self, *a, **k):
            pytest.fail("守りを通らずに打った")
    request = urllib.request.Request("file:///etc/hosts")
    for bad in (Bare(), urllib.request.build_opener()):
        with pytest.raises(httpsafe.EndpointRejected):
            httpsafe.urlopen(request, timeout=1, via=bad)
    assert type(wire.OPENER) is type(httpsafe.build_opener())


def test_viaを渡すのはshopのwire_pyだけ():
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "thth"
    found = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) == "urlopen"
                    and any(k.arg == "via" for k in node.keywords)):
                found.append(str(path.relative_to(root.parent)))
    assert found == ["thth/shop/wire.py"]


def test_httpsafeは危ない領域に入っている():
    import fnmatch
    import importlib.util
    import pathlib
    import sys
    path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "release_gate.py"
    spec = importlib.util.spec_from_file_location("release_gate_wire", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    patterns = [p for ps in module.DANGER_AREAS.values() for p in ps]
    assert any(fnmatch.fnmatchcase("thth/httpsafe.py", p) for p in patterns)
