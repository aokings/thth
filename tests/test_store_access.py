"""短い鍵の置き場と取り直し（`thth/shop/access.py`・設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §4.2）。

柵:
  - 残りが 300 秒より長ければ網に出ない（置いた鍵を使い回す）。切れていれば 1 回だけ取る。
  - 鎖の中でもう一度置き場を読む（待つ間に別の thth が取り直していれば、取らずにそれを使う）。
  - 鎖が取れなければ store_refresh_busy（打たない）。
  - 応答の形が違えば store_api_changed（token_response）・置く期限は 24 時間まで・ファイルは 0600。
  - drop は置き場の鍵が断られた鍵と同じときだけ消す。
本物の網には出ない（mint は試験の関数）。
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import pathlib
import stat

import pytest

from thth import jst
from thth.shop import access, ledger
from thth.shop.wire import StoreRefused

STORE = "nigami-shopify"
NOW = datetime.datetime(2026, 11, 12, 10, 0, tzinfo=jst.JST)


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    ledger.write_new(STORE, ledger.build(STORE, kind="shopify", project="nigamilab", shop="nigami-test"))
    return tmp_path


class Mint:
    def __init__(self, *responses):
        self.responses = list(responses) or [{"access_token": "short-1", "scope": "read_products", "expires_in": 86399}]
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.responses[min(self.calls, len(self.responses)) - 1]


def test_置いた鍵は残りが長ければ網に出ずに使い回す(home):
    mint = Mint()
    assert access.ensure(STORE, mint, now=NOW) == "short-1" and mint.calls == 1
    assert access.ensure(STORE, mint, now=NOW + datetime.timedelta(hours=23)) == "short-1" and mint.calls == 1
    path = pathlib.Path(access.path_for(STORE))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path == pathlib.Path(os.path.expanduser(ledger.default_access(STORE)))
    value = json.loads(path.read_text())
    # 権限の名前は置かない（privacy「一覧は残さない」・監査 5.17.0 危ない 1）。
    assert set(value) == {"schema", "access_token", "expires_at", "obtained_at"} and value["schema"] == access.SCHEMA


def test_切れる5分前を過ぎれば1回だけ取り直す(home):
    mint = Mint({"access_token": "short-1", "scope": "read_products", "expires_in": 86399},
                {"access_token": "short-2", "scope": "read_products", "expires_in": 86399})
    access.ensure(STORE, mint, now=NOW)
    later = NOW + datetime.timedelta(seconds=86399 - access.REFRESH_MARGIN_SECONDS + 1)
    assert access.ensure(STORE, mint, now=later) == "short-2" and mint.calls == 2
    assert access.ensure(STORE, mint, now=later) == "short-2" and mint.calls == 2


def test_置く期限は24時間まで(home):
    access.ensure(STORE, Mint({"access_token": "long", "expires_in": 10 ** 9}), now=NOW)
    value = json.loads(pathlib.Path(access.path_for(STORE)).read_text())
    assert datetime.datetime.fromisoformat(value["expires_at"]) == NOW + datetime.timedelta(seconds=86400)


@pytest.mark.parametrize("response", [{}, {"access_token": "", "expires_in": 1}, {"access_token": "x", "expires_in": 0},
                                      {"access_token": "x", "expires_in": "86399"}, {"access_token": "a b", "expires_in": 5},
                                      {"access_token": "x", "expires_in": True}, None, []])
def test_形の違う応答は置かずに断る(home, response):
    with pytest.raises(StoreRefused) as exc:
        access.ensure(STORE, Mint(response), now=NOW)
    assert exc.value.code == "store_api_changed" and exc.value.detail == "token_response"
    assert not pathlib.Path(access.path_for(STORE)).exists()


def test_鎖の中で読み直し別のththが取り直していればそれを使う(home, monkeypatch):
    """1 度目の読み（鎖の外）は古く、鎖の中の読み直しで新しい鍵が見えれば、mint は呼ばない。"""
    access.ensure(STORE, Mint(), now=NOW)
    fresh = access.read(STORE)
    reads = iter([None, fresh])
    monkeypatch.setattr(access, "read", lambda name: next(reads))
    mint = Mint()
    assert access.ensure(STORE, mint, now=NOW) == "short-1" and mint.calls == 0


def test_鎖が取れなければ打たずにbusy(home, monkeypatch):
    directory = pathlib.Path(ledger.state_dir_for(STORE))
    directory.mkdir(parents=True)
    fd = os.open(directory / access.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    clock = iter(range(0, 1000))
    monkeypatch.setattr(access, "CLOCK", lambda: next(clock))
    monkeypatch.setattr(access, "SLEEP", lambda seconds: None)
    mint = Mint()
    try:
        with pytest.raises(StoreRefused) as exc:
            access.ensure(STORE, mint, now=NOW)
    finally:
        os.close(fd)
    assert exc.value.code == "store_refresh_busy" and mint.calls == 0
    assert access.ensure(STORE, mint, now=NOW) == "short-1"      # 鎖が空けば取れる


def test_dropは断られた鍵と同じときだけ消す(home):
    access.ensure(STORE, Mint(), now=NOW)
    access.drop(STORE, "another")
    assert pathlib.Path(access.path_for(STORE)).exists()
    access.drop(STORE, "short-1")
    assert not pathlib.Path(access.path_for(STORE)).exists()


def test_removeの鎖は状態の置き場を作らない(home):
    with access.locked(STORE, create=False):
        pass
    assert not pathlib.Path(ledger.state_dir_for(STORE)).exists()


def test_壊れた置き場は無いものとして取り直す(home):
    path = pathlib.Path(access.path_for(STORE))
    path.parent.mkdir(parents=True, exist_ok=True)
    for broken in ("not json", json.dumps({"schema": "x"}), json.dumps({"schema": access.SCHEMA, "access_token": "t",
                                                                        "expires_at": "2026-11-12T10:00:00"})):
        path.write_text(broken)
        mint = Mint()
        assert access.ensure(STORE, mint, now=NOW) == "short-1" and mint.calls == 1


def test_台帳が無ければ取らずに止まり状態の置き場も作らない(home):
    pathlib.Path(ledger.path_for(STORE)).unlink()
    mint = Mint()
    with pytest.raises(StoreRefused) as exc:
        access.ensure(STORE, mint, now=NOW)
    assert exc.value.code == "store_missing" and mint.calls == 0
    assert not pathlib.Path(access.path_for(STORE)).exists()
    assert not pathlib.Path(ledger.state_dir_for(STORE)).exists()


def test_removeの間に鎖を待っていた読みは短い鍵を書き戻さない(home, monkeypatch):
    """監査 5.17.0 直したほうがよい 1: remove が鎖を持って消し終えたあと、待っていた読みが鎖を取っても網に出ず書かない。"""
    import threading
    import time as _time
    from thth.shop import remove
    cfg = ledger.load(STORE)
    pathlib.Path(cfg["token"]).parent.mkdir(parents=True, exist_ok=True)
    ledger.write_token(cfg, {"auth": "shopify_client", "client_id": "c", "client_secret": "s", "shop_id": "x",
                             "obtained_at": "t", "expires_on": None, "no_expiry": True})
    monkeypatch.setattr(access, "SLEEP", lambda seconds: _time.sleep(0.01))
    mint, result = Mint(), {}
    with access.locked(STORE):                       # remove が鎖を持っている間
        found = remove.plan(STORE)
        def reader():
            try:
                result["token"] = access.ensure(STORE, mint, now=NOW)
            except StoreRefused as exc:
                result["code"] = exc.code
        thread = threading.Thread(target=reader)
        thread.start()
        _time.sleep(0.3)                             # 読みが鎖を待つまで
        remove._execute(found)                       # remove の中身（鍵 → 短い鍵 → 写し → 台帳）
    thread.join(10)
    assert result == {"code": "store_missing"} and mint.calls == 0
    leftovers = [p for p in pathlib.Path(home).rglob("*") if STORE in p.name]
    assert leftovers == []
