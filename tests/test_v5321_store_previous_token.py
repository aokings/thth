"""監査 5.32.0 の軽い 5 (a)・6（5.32.1）: 店の鍵の入口の「前の鍵が照合の元」。

- 5 (a): 4 つの種類（BASE・STORES・スマレジ・Shopify）とも、前の鍵と店が違えば `--force` でも置き換えない
  （前はスマレジと Shopify に柵が無く、`--force` を効く形に戻しても緑のままだった）。
- 6: 前の鍵のファイルがあるのに読めない（壊れた JSON・店の番号の無い形）ときは、「前の鍵は無い」と同じに扱わず、
  写しの店の番号を照合の元にする（違えば `store_shop_mismatch`）。写しも無いとき、人が見ていない口
  （`thth secret accept`）は `store_token_unreadable`（identity_unknown 14）で断り、人の手の `token set` は
  朝の知らせが勧める置き直し（5.18.2）のとおり受ける。

偽の店だけに送る（各店の試験の偽の transport をそのまま使う）。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tests import test_store_base as base_t
from tests import test_store_shopify as shopify_t
from tests import test_store_smaregi as smaregi_t
from tests import test_store_stores as stores_t
from thth.shop import ledger

KINDS = ("base", "stores", "smaregi", "shopify")


def _world(kind, capsys, tmp_path, monkeypatch):
    """種類ごとの偽の店を立てて鍵を 1 本置く。`(店, 鍵を入れ直す argv, 入れる値を積む関数)`。"""
    if kind == "base":
        env = base_t.env.__wrapped__(tmp_path, monkeypatch)
        base_t.setup_store(capsys, env)
        return base_t.STORE, ["--expires", "2027-01-31"], lambda: None
    if kind == "stores":
        env = stores_t.env.__wrapped__(tmp_path, monkeypatch)
        stores_t.setup_store(capsys, env)
        return stores_t.STORE, [], lambda: env["lines"].__setitem__(slice(None), [env["credential"]])
    if kind == "smaregi":
        env = smaregi_t.env.__wrapped__(tmp_path, monkeypatch)
        smaregi_t.setup_store(capsys, env)
        return smaregi_t.STORE, [], lambda: env["lines"].__setitem__(
            slice(None), [env["client_id"], env["client_secret"]])
    env = shopify_t.env.__wrapped__(tmp_path, monkeypatch)
    shopify_t.setup_store(capsys, env)
    return shopify_t.STORE, ["--auth", "client"], lambda: env["lines"].__setitem__(
        slice(None), [env["client_id"], env["client_secret"]])


def _token_set(capsys, store, extra, *flags):
    from thth import cli
    rc = cli.main(["shop", "token", "set", store, *extra, "--by", "m", *flags])
    out, err = capsys.readouterr()
    return rc, out + err


@pytest.mark.parametrize("kind", KINDS)
def test_前の鍵と店が違えばforceでも置き換えない(capsys, tmp_path, monkeypatch, kind):
    store, extra, feed = _world(kind, capsys, tmp_path, monkeypatch)
    path = pathlib.Path(ledger.load(store)["token"])
    token = json.loads(path.read_text())
    token["shop_id"] = "another-shop-v5321"          # 前の鍵は別の店のもの
    path.write_text(json.dumps(token))
    before = path.read_bytes()
    for flags in ((), ("--force",)):
        feed()
        rc, said = _token_set(capsys, store, extra, *flags)
        assert rc == 2 and "store_shop_mismatch" in said, (kind, flags, said)
        assert path.read_bytes() == before, (kind, flags)


def _break(path, broken):
    if broken == "shop_id_missing":
        token = json.loads(path.read_text())
        token.pop("shop_id")
        path.write_text(json.dumps(token))
    else:
        path.write_text(broken)


def _snapshot(store, shop_id):
    ledger.write_snapshot(store, shop_id=shop_id, items=[], read_at="2026-11-28T00:00:00+09:00")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("broken", ["{", "shop_id_missing"])
def test_前の鍵が読めなければ写しの店と照合し違えば置かない(capsys, tmp_path, monkeypatch, kind, broken):
    store, extra, feed = _world(kind, capsys, tmp_path, monkeypatch)
    path = pathlib.Path(ledger.load(store)["token"])
    _snapshot(store, "another-shop-v5321")
    _break(path, broken)
    before = path.read_bytes()
    feed()
    rc, said = _token_set(capsys, store, extra, "--force")
    assert rc == 2 and "store_shop_mismatch" in said, (kind, said)
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_前の鍵が読めず写しが同じ店なら置き直せる(capsys, tmp_path, monkeypatch, kind):
    store, extra, feed = _world(kind, capsys, tmp_path, monkeypatch)
    path = pathlib.Path(ledger.load(store)["token"])
    _snapshot(store, json.loads(path.read_text())["shop_id"])
    _break(path, "{")
    feed()
    rc, said = _token_set(capsys, store, extra)
    assert rc == 0, (kind, said)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("broken", ["{", "shop_id_missing"])
def test_照合の元が無ければ人の見ていない口は断り今のファイルはそのまま(capsys, tmp_path, monkeypatch, kind, broken):
    from tests.test_v5321_secret_accept_codes import accept, answer
    store, extra, feed = _world(kind, capsys, tmp_path, monkeypatch)
    path = pathlib.Path(ledger.load(store)["token"])
    _break(path, broken)
    before = path.read_bytes()
    feed()   # 偽の店の試験は READ_SECRET を差し替えているので、店に通る値はそちらから入る
    two = kind in ("smaregi", "shopify")
    data = b"value-one-v5321\nvalue-two-v5321\n" if two else b"value-one-v5321\n"
    rc, out, err = accept(capsys, "shop-client" if two else "shop-token", store, data,
                          *(["--expires", "2027-01-31"] if kind == "base" else []))
    assert (rc, answer(out)["code"]) == (14, "identity_unknown"), (kind, out, err)
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", KINDS)
def test_照合の元が無くても人の手のtoken_setは朝の知らせのとおり置き直せる(capsys, tmp_path, monkeypatch, kind):
    store, extra, feed = _world(kind, capsys, tmp_path, monkeypatch)
    _break(pathlib.Path(ledger.load(store)["token"]), "{")
    feed()
    rc, said = _token_set(capsys, store, extra)
    assert rc == 0, (kind, said)


@pytest.mark.parametrize("kind", KINDS)
def test_同じ店の鍵の入れ替えは今までどおり通る(capsys, tmp_path, monkeypatch, kind):
    store, extra, feed = _world(kind, capsys, tmp_path, monkeypatch)
    feed()
    rc, said = _token_set(capsys, store, extra, "--force")
    assert rc == 0, (kind, said)
