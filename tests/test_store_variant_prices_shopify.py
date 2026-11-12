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
