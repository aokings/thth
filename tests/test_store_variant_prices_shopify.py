"""値段の上下は同じ種類の値段で決める・Shopify の側（監査 5.18.0 の 5・5.18.1）。

Shopify の行の `price` は種類の中でいちばん安い値（`priceRangeV2.minVariantPrice`）。いちばん安い種類が無くなった
だけで `price_up` に見えていた。商品の問い合わせで種類の値段（`variants(first: …) { legacyResourceId price }`）も読み、
種類ごとに比べる。種類を読み切れない商品（`hasNextPage`）は上下を言わない。本物の店は呼ばない。
"""
from __future__ import annotations

import pytest

from thth.shop import shopify, wire
from tests import test_store_shopify as sh
from tests.test_store_shopify import env  # noqa: F401 — Shopify の偽の店（同じ fixture を使う）


def _kinds(value):
    return [(c["kind"], str(c["id"])) for c in value["changes"]]


# ------------------------------------------------------------------ Shopify（price は種類の中でいちばん安い値）

def _variants(prices, start=10, more=False):
    return {"nodes": [{"legacyResourceId": str(start + n), "price": f"{p}.0"} for n, p in enumerate(prices)],
            "pageInfo": {"hasNextPage": more}}


def _product(i, prices, **extra):
    return sh.product(i, amount=f"{min(prices)}.0", variants=_variants(prices, start=i * 100), **extra)


def test_Shopifyでいちばん安い種類が無くなっただけでは値上げにしない(capsys, env):
    env["products"] = [_product(1, [1000, 1200])]
    sh.setup_store(capsys, env)
    assert sh.run(capsys, "shop", "diff", sh.STORE)[0] == 0
    env["products"] = [sh.product(1, amount="1200.0", variants={"nodes": [{"legacyResourceId": "101",
                                                                             "price": "1200.0"}],
                                                                  "pageInfo": {"hasNextPage": False}})]
    rc_, value, _ = sh.jrun(capsys, "shop", "diff", sh.STORE)
    assert rc_ == 0 and _kinds(value) == [("variants_changed", "1")]


def test_Shopifyで同じ種類の値段が下がれば値下げ(capsys, env):
    env["products"] = [_product(1, [1000, 1200])]
    sh.setup_store(capsys, env)
    assert sh.run(capsys, "shop", "diff", sh.STORE)[0] == 0
    env["products"] = [_product(1, [1000, 900])]
    rc_, value, _ = sh.jrun(capsys, "shop", "diff", sh.STORE)
    assert rc_ == 0 and _kinds(value) == [("price_down", "1")]
    assert (value["changes"][0]["before"], value["changes"][0]["after"]) == (1200, 900)


def test_Shopifyで種類を読み切れない商品は上下を言わない(capsys, env):
    env["products"] = [sh.product(1, amount="1000.0", variants=_variants([1000, 1200], start=100, more=True))]
    sh.setup_store(capsys, env)
    assert sh.run(capsys, "shop", "diff", sh.STORE)[0] == 0
    env["products"] = [sh.product(1, amount="900.0", variants=_variants([900, 1200], start=100, more=True))]
    rc_, value, _ = sh.jrun(capsys, "shop", "diff", sh.STORE)
    assert rc_ == 0 and _kinds(value) == [("price_changed", "1")]
    assert value["changes"][0]["why"] == "variants_unread"


def test_Shopifyの商品の問い合わせは種類の値段を読み注文の語を含まず点数は上限の内():
    text = wire.QUERIES["products"]
    assert f"variants(first: {shopify.VARIANTS_FIRST})" in text and "legacyResourceId price" in text
    for word in ("mutation", "order", "customer", "cost", "inventoryitem"):
        assert word not in text.lower()
    # 点数は文から公式の決まりで数え直す（728 点・`tests/test_store_shopify_cost.py`）。読める商品の数は 500 のまま。
    assert shopify.MAX_ITEMS >= 500


# 問い合わせで読む欄の集まり（監査 5.18.1 直したほうがよい 6）。語の拒否の並びだけでは、`sku`（品番）のような
# scalar を足しても緑のまま（点数も 0 で増えない）。privacy が言う「読む物」（店の名前・myshopify のドメイン・URL・
# 通貨・鍵の scope の名前／商品の ID・名前・公開・URL・在庫の数・値段・画像・種類ごとの ID と値段）と、文の欄の
# 集まりを 1 つも違わずに結ぶ。欄を足す・外すときは、ここと privacy を一緒に直す。
_FIELDS_SHOP = {
    "shop": {"id": None, "name": None, "myshopifyDomain": None, "url": None, "currencyCode": None},
    "currentAppInstallation": {"accessScopes": {"handle": None}},
}
_FIELDS_PRODUCTS = {
    "products(after=$after,first=$first,sortKey=ID)": {
        "nodes": {
            "id": None, "legacyResourceId": None, "title": None, "status": None, "onlineStoreUrl": None,
            "totalInventory": None, "tracksInventory": None, "updatedAt": None,
            "priceRangeV2": {"minVariantPrice": {"amount": None, "currencyCode": None}},
            "featuredMedia": {"preview": {"image": {"url": None}}},
            f"variants(first={wire.SHOPIFY_VARIANTS_FIRST})": {
                "nodes": {"legacyResourceId": None, "price": None},
                "pageInfo": {"hasNextPage": None},
            },
        },
        "pageInfo": {"hasNextPage": None, "endCursor": None},
    },
}


def _field_tree(text):
    from tests.test_store_shopify_cost import _TOKEN, _parse
    tokens = _TOKEN.findall(text)
    fields, end = _parse(tokens, tokens.index("{"))
    assert end == len(tokens), "文の終わりのあとに字が残っています"

    def tree(rows):
        out = {}
        for name, args, children in rows:
            key = name + ("(" + ",".join(f"{k}={v}" for k, v in sorted(args.items())) + ")" if args else "")
            assert key not in out, key
            out[key] = tree(children) if children is not None else None
        return out
    return tree(fields)


@pytest.mark.parametrize("name,want", [("shop", _FIELDS_SHOP), ("products", _FIELDS_PRODUCTS)])
def test_柵_Shopifyの問い合わせの欄は決めた集まりと1つも違わない(name, want):
    assert _field_tree(wire.QUERIES[name]) == want
    assert set(wire.QUERIES) == {"shop", "products"}


@pytest.mark.parametrize("variants", [None, {"nodes": "x", "pageInfo": {"hasNextPage": False}},
                                      {"nodes": [{"legacyResourceId": "1", "price": "12.5"}],
                                       "pageInfo": {"hasNextPage": False}},
                                      {"nodes": [{"legacyResourceId": "a/b", "price": "12.0"}],
                                       "pageInfo": {"hasNextPage": False}},
                                      {"nodes": [], "pageInfo": {}}])
def test_Shopifyの種類の形が違えば断る(variants):
    node = sh.product(1)
    node["variants"] = variants
    with pytest.raises(wire.StoreRefused) as exc:
        shopify.normalize(node)
    assert exc.value.code in ("store_api_changed", "store_currency_unsupported")
