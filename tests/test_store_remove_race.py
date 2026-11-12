"""`thth shop remove` と短い鍵の取り直しの競り合い・逆向きの形（監査 5.17.0 残っていること 1）。

形: 読みが鎖を持って短い鍵を取り直している最中に、remove の二段目が走る。remove の一覧（`plan`）は鎖の前に作るので、
その時点で短い鍵のファイルが無い（401 で捨てた直後・まだ 1 度も取っていない）と一覧に入らない。読みが書き終えて
鎖を放したあと、remove が一覧の物だけを消すと、0600 の 24 時間の鍵が 1 つ残る。

柵:
  - remove は鎖の中で一覧を取り直し、機械が既定の置き場に書く物（短い鍵・状態の置き場）は一覧に無くても消す。
  - 人が確かめた物（鍵・台帳・残す物）が鎖の中で増えていたら、何も消さずに store_remove_unconfirmed。
  - remove の鎖は状態の置き場を残さない（無かった置き場を鎖のために作っても、消して数えない）。
本物の網には出ない（mint は試験の関数・鎖は試験が持つ）。
"""
from __future__ import annotations

import pathlib
import threading
import time

import pytest

from thth.shop import access, ledger, remove
from thth.shop.wire import StoreRefused

from tests.test_store_access import NOW, STORE, home  # noqa: F401  (fixture)


def _token(home):
    cfg = ledger.load(STORE)
    pathlib.Path(cfg["token"]).parent.mkdir(parents=True, exist_ok=True)
    ledger.write_token(cfg, {"auth": "shopify_client", "client_id": "c", "client_secret": "s", "shop_id": "x",
                             "obtained_at": "t", "expires_on": None, "no_expiry": True})


def _leftovers(home):
    return sorted(str(p) for p in pathlib.Path(home).rglob("*") if STORE in p.name)


class SlowMint:
    """読みが鎖を持ったまま網で待っている形（`go` が立つまで返さない）。"""

    def __init__(self):
        self.inside, self.go, self.calls = threading.Event(), threading.Event(), 0

    def __call__(self):
        self.calls += 1
        self.inside.set()
        assert self.go.wait(10)
        return {"access_token": "short-late", "expires_in": 86399}


def _race(home, monkeypatch, *, before):
    """`before` の形にしてから一覧を作り、読みに鎖を持たせたまま remove の二段目を走らせる。"""
    monkeypatch.setattr(access, "SLEEP", lambda seconds: time.sleep(0.01))
    _token(home)
    before()
    found = remove.plan(STORE)
    assert "access" not in [t["category"] for t in found["targets"]]    # 一覧に短い鍵は無い
    mint, result = SlowMint(), {}

    def reader():
        result["token"] = access.ensure(STORE, mint, now=NOW)

    def remover():
        try:
            result["done"] = remove.execute(found)
        except StoreRefused as exc:
            result["code"] = exc.code

    first = threading.Thread(target=reader)
    first.start()
    assert mint.inside.wait(10)                      # 読みが鎖を持って網で待っている
    second = threading.Thread(target=remover)
    second.start()
    time.sleep(0.3)                                  # remove が鎖を待つまで
    assert "done" not in result                      # 鎖を待っている（まだ何も消していない）
    mint.go.set()                                    # 読みが短い鍵を書いて鎖を放す
    first.join(10)
    second.join(10)
    assert result.get("token") == "short-late" and mint.calls == 1
    return found, result


def test_取り直しの最中に走ったremoveは一覧に無い短い鍵も消す_401で捨てた直後(home, monkeypatch):
    def dropped():
        access.ensure(STORE, lambda: {"access_token": "short-old", "expires_in": 86399}, now=NOW)
        access.drop(STORE, "short-old")              # 401 で捨てた直後（状態の置き場はある）
    found, result = _race(home, monkeypatch, before=dropped)
    assert [t["category"] for t in found["targets"]] == ["token", "state", "ledger"]
    assert result["done"] == ["token", "access", "state", "ledger"]
    assert _leftovers(home) == []


def test_取り直しの最中に走ったremoveは一覧に無い短い鍵も消す_まだ1度も取っていない(home, monkeypatch):
    found, result = _race(home, monkeypatch, before=lambda: None)
    assert [t["category"] for t in found["targets"]] == ["token", "ledger"]   # 状態の置き場もまだ無い
    assert result["done"] == ["token", "access", "state", "ledger"]
    assert _leftovers(home) == []


def test_鎖の中で人の確かめた物が増えていればなにも消さずにunconfirmed(home, monkeypatch):
    """一覧を作ったあとに鍵が置かれたら（`token set` も同じ鎖を取る）、確かめた digest と違うので消さない。"""
    found = remove.plan(STORE)
    assert [t["category"] for t in found["targets"]] == ["ledger"]
    _token(home)
    with pytest.raises(StoreRefused) as exc:
        remove.execute(found)
    assert exc.value.code == "store_remove_unconfirmed"
    assert exc.value.detail == remove.digest(remove.plan(STORE))           # 今の digest を返す
    assert pathlib.Path(ledger.path_for(STORE)).exists()
    assert pathlib.Path(ledger.load(STORE)["token"]).exists()
    assert not pathlib.Path(ledger.state_dir_for(STORE)).exists()           # 鎖のために作った置き場も残さない


def test_removeの鎖は無かった状態の置き場を残さず数えない(home):
    _token(home)
    found = remove.plan(STORE)
    assert remove.execute(found) == ["token", "ledger"]
    assert _leftovers(home) == []
