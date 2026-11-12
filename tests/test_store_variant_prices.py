"""値段の上下は「同じ種類の値段」で決める（監査 5.18.0 の 5・5.18.1）。

壊れ方（直す前）: 行の `price` は STORES では種類の中でいちばん高い値、Shopify ではいちばん安い値。だから
いちばん高い種類（XL）を売るのをやめただけで STORES は `price_down`（「お値下げしました」の下書きができる）、
いちばん安い種類が無くなっただけで Shopify は `price_up` に見えた。

柵:
  - 種類が増えた・減っただけでは `price_down`・`price_up` にしない（`variants_changed` という下書きを作らない違い）。
  - `price_down` は「両方の写しにある種類のどれかが下がり、どれも上がっていない」とき。上がりと下がりが混ざれば
    `price_changed`（下書きを作らない）。
  - 前の版の写し（種類ごとの値段が無い）と比べるときは、上下を言わない（`price_changed`・下書きを作らない）。
    1 度 `--accept` で写しを進めれば、次から種類ごとに比べる。
  - 承認の読み直しも、下書きの頭に種類ごとの値段があれば種類ごとに比べる（種類が減っただけでは値段の断りにしない）。
  - BASE（商品 1 つに値段 1 つ）は今のまま（行に種類ごとの値段の鍵が無い）。
本物の店は呼ばない。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth import queuefile
from thth.shop import diff, draft, recheck, rows
from tests import test_store_stores as st
from tests.test_store_stores import env  # noqa: F401 — STORES の偽の店（同じ fixture を使う）


# ------------------------------------------------------------------ STORES（price は種類の中でいちばん高い値）

def _stores_item(i, prices, *, ids=None, **extra):
    ids = ids or [f"v{i}{n}" for n in range(len(prices))]
    variations = [{"id": vid, "name": f"size{n}", "quantity": 5, "sales_price": p}
                  for n, (vid, p) in enumerate(zip(ids, prices))]
    return st.item(i, price=max(p for p in prices if p is not None), variations=variations, **extra)


def _stores_baseline(capsys, env, items):
    env["items"] = items
    st.setup_store(capsys, env)
    assert st.run(capsys, "shop", "diff", st.STORE)[0] == 0


def _kinds(value):
    return [(c["kind"], str(c["id"])) for c in value["changes"]]


def test_STORESでいちばん高い種類をやめただけでは値下げにしない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    one = _stores_item(1, [3000, 3000, 4000])          # S・M・XL（XL がいちばん高い）
    _stores_baseline(capsys, env, [one])
    env["items"] = [_stores_item(1, [3000, 3000])]       # XL をやめた（どの値段も変わっていない）
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert rc_ == 0
    assert ("price_down", one["id"]) not in _kinds(value)
    assert ("variants_changed", one["id"]) in _kinds(value)
    [change] = [c for c in value["changes"] if c["kind"] == "variants_changed"]
    assert change["added"] == 0 and change["removed"] == 1
    rc_, value, _ = st.jrun(capsys, "shop", "draft", st.STORE, "--to", account["name"], "--kind", "price_down")
    assert rc_ == 2 and value["code"] == "store_draft_nothing"
    assert not list(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))


def test_STORESで同じ種類の値段が下がれば値下げで下書きの頭に種類ごとの値段(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    one = _stores_item(1, [3000, 3000, 4000])
    _stores_baseline(capsys, env, [one])
    env["items"] = [_stores_item(1, [2500, 3000, 4000])]  # S だけ下げた（いちばん高い値は 4000 のまま）
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert rc_ == 0
    [change] = [c for c in value["changes"] if c["kind"] == "price_down"]
    assert (change["before"], change["after"], change["variants"]) == (3000, 2500, 1)
    rc_, value, err = st.jrun(capsys, "shop", "draft", st.STORE, "--to", account["name"], "--kind", "price_down")
    assert rc_ == 0, err
    [path] = sorted(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    q = queuefile.parse(str(path))
    assert "お値下げしました" in path.read_text()
    assert q.front_matter["from_store_variant_prices"] == "v10=2500,v11=3000,v12=4000"
    # 読み直し: 種類も値段も同じなら通る。
    assert recheck.check(str(path), q.front_matter) == (None, None)


def test_STORESで上がりと下がりが混ざれば上下を言わず下書きも作らない(capsys, env):
    one = _stores_item(1, [3000, 3000])
    _stores_baseline(capsys, env, [one])
    env["items"] = [_stores_item(1, [2500, 3500])]
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert rc_ == 0 and _kinds(value) == [("price_changed", one["id"])]
    assert value["changes"][0]["why"] == "mixed"
    _, out, _ = st.run(capsys, "shop", "diff", st.STORE)
    assert "上がった種類と下がった種類があります" in out and "下書きは作りません" in out


def test_前の版の写しと比べる最初の1回は上下を言わず写しを進めれば種類ごとに比べる(capsys, env):
    one = _stores_item(1, [3000, 3000, 4000])
    _stores_baseline(capsys, env, [one])
    from thth.shop import ledger
    path = pathlib.Path(ledger.snapshot_path(st.STORE))
    snap = json.loads(path.read_text())
    for row in snap["items"].values():
        row.pop("variant_prices", None)                  # 5.18.0 の写し（種類ごとの値段が無い）
    path.write_text(json.dumps(snap))
    env["items"] = [_stores_item(1, [3000, 3000])]       # XL をやめた
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert rc_ == 0 and _kinds(value) == [("price_changed", one["id"])]
    assert value["changes"][0]["why"] == "snapshot_without_variants"
    assert value["variant_note"] and "--accept" in value["variant_note"]
    _, out, _ = st.run(capsys, "shop", "diff", st.STORE, "--accept")
    assert "種類ごとの値段" in out
    env["items"] = [_stores_item(1, [2800, 3000])]
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert _kinds(value) == [("price_down", one["id"])] and value["variant_note"] is None


def test_STORESの種類のidと値段の形が違えば断る(capsys, env):
    bad = st.item(1, variations=[{"id": "v/1", "quantity": 1, "sales_price": 100}])
    env["items"] = [bad]
    st.setup_store(capsys, env)
    rc_, value, _ = st.jrun(capsys, "shop", "items", st.STORE)
    assert rc_ != 0 and value["code"] == "store_api_changed" and value.get("detail") == "item_variations"
    env["items"] = [st.item(1, variations=[{"id": "v1", "quantity": 1, "sales_price": "100"}])]
    rc_, value, _ = st.jrun(capsys, "shop", "items", st.STORE)
    assert rc_ != 0 and value["code"] == "store_api_changed"


# ------------------------------------------------------------------ 読み直し（種類ごと）

def test_読み直しは種類が減っただけなら値段の断りにせず知らせ値下げの下書きなら断る(capsys, env):
    st.setup_store(capsys, env)
    target = _stores_item(1, [3000, 3000, 4000])
    env["items"] = [_stores_item(1, [3000, 3000])]
    front = {"from_store": f"store/{st.STORE}/item/{target['id']}", "from_store_price": "4000",
             "from_store_change": "new", "from_store_variant_prices": "v10=3000,v11=3000,v12=4000"}
    reason, note = recheck.check("x.md", front)
    assert reason is None and note and "種類" in note and "減った 1" in note
    reason, _ = recheck.check("x.md", dict(front, from_store_change="price_down"))
    assert reason and "store_item_changed" in reason and "variants_changed" in reason
    env["items"] = [_stores_item(1, [2900, 3000, 4000])]
    reason, _ = recheck.check("x.md", front)
    assert reason and "variant v10 price 3000 -> 2900" in reason
    # 頭に種類ごとの値段が無い下書き（5.18.0 までの下書き）は今までどおり行の値段で比べる。
    env["items"] = [_stores_item(1, [3000, 3000])]
    reason, _ = recheck.check("x.md", {k: v for k, v in front.items() if k != "from_store_variant_prices"})
    assert reason and "price 4000 -> 3000" in reason


# ------------------------------------------------------------------ 違いの決め方（行だけで）

def _row(i, price, variants=None, **extra):
    row = {"id": i, "name": f"豆 {i}", "price": price, "stock": 3, "visible": True, "sales_status": "on_sale",
           "image_url": None, "item_url": None, "modified_at": None}
    if variants is not None or "variant_prices" in extra:
        row["variant_prices"] = variants
    row.update(extra)
    return row


def test_BASEの行は種類ごとの値段を持たず今までどおり行の値段で比べる():
    before = {"1": rows.snapshot_row(_row(1, 1200))}
    assert "variant_prices" not in before["1"]
    changes = diff.compare(before, [_row(1, 900)], complete=True)
    assert [(c["kind"], c["before"], c["after"]) for c in changes] == [("price_down", 1200, 900)]


@pytest.mark.parametrize("before,after,kinds", [
    ({"a": 100, "b": 200}, {"a": 100}, ["variants_changed"]),                    # 減っただけ
    ({"a": 100}, {"a": 100, "b": 50}, ["variants_changed"]),                     # 安い種類を足しただけ
    ({"a": 100, "b": 200}, {"a": 90, "b": 200}, ["price_down"]),                 # 1 つ下がった
    ({"a": 100, "b": 200}, {"a": 110, "b": 200}, ["price_up"]),
    ({"a": 100, "b": 200}, {"a": 90, "b": 210}, ["price_changed"]),              # 混ざった
    ({"a": 100, "b": 200}, {"a": 90}, ["price_down", "variants_changed"]),       # 下がり、1 つ減った
    ({"a": 100}, {"b": 50}, ["variants_changed"]),                               # 全部入れ替わった
    ({"a": None, "b": 200}, {"a": 100, "b": 200}, []),                           # 値段の無い種類は比べない
])
def test_種類ごとの値段の数え方(before, after, kinds):
    old = rows.snapshot_row(_row(1, max(v for v in before.values() if v is not None), before))
    new = _row(1, max(v for v in after.values() if v is not None), after)
    found = [c["kind"] for c in diff.compare({"1": old}, [new], complete=True)]
    assert found == kinds


def test_下書きは値下げの違いに混ざりも種類の増減も選ばない():
    changes = [{"id": 1, "kind": "price_changed", "name": "x"}, {"id": 1, "kind": "variants_changed", "name": "x"}]
    assert draft.choose(changes, [_row(1, 100, {"a": 100})]) == []
    assert "price_changed" not in diff.DRAFT_KINDS and "variants_changed" not in diff.DRAFT_KINDS
