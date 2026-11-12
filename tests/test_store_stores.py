"""STORES を読む口（`thth/shop/stores.py`・`thth shop …` の kind stores・5.18.0）。

柵（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §5・§7・§8）:
  - 鍵はプライベートアプリのクレデンシャル（固定の文字列）を `Authorization: Bearer` に載せるだけ。引数では受けない。
    店が見えたとき（`GET /retail/202211/store` が通ったとき）だけ保存する。`--expires` は任意。
  - 打つのは GET だけ・道は `/retail/202211/store` と `/retail/202211/items` の 2 つ・引数は limit と offset だけ。
    クレデンシャルに何が許されているかを THTH は選べないので、注文を読まない柵は wire.py の表（偽の transport が本物の口と同じ柵を通す）。
  - 店の URL は応答に無いので、台帳の `public_url`（持ち主が入れる）。https だけ・userinfo と query と fragment なし。
    THTH はこの URL に何も送らない（下書きに載せるだけ）。
  - 行への直し方: id は文字列・公開は shown だけ・在庫は種類のどれかが null なら None（数えない）・予約販売と
    販売の期間の前と後は「売っている」に数えない。
  - クレデンシャルは出力にもファイル（鍵のファイルのほか）にも出ない。
**【未確認】の応答（鍵が違うときの数・401 か 403 か・種類の無い商品・販売の期間の時刻の形）は偽で作った**
（設計 §2.2 の【未確認】・§11。本物の店では 1 度も動かしていない）。材料の形は公式の OpenAPI の例による。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import secrets
import stat
import urllib.parse

import pytest

from tests.kept_bytes import kept_bytes
from thth import admin_log, cli, jst, queuefile
from thth import refusal_catalog as rc
from thth.shop import ACTION_PREFIX, ledger, stores, wire
from thth.shop import cli as store_cli

STORE = "nigami-stores"
URL = "https://nigami.stores.jp"
HOST = wire.STORES_HOST
STORE_PATH = "/retail/202211/store"
ITEMS_PATH = "/retail/202211/items"
NOW = datetime.datetime(2026, 11, 12, 12, 0, tzinfo=jst.JST)


def item(i, *, status="shown", quantities=(None, 20, 10), price=3000, name=None, preorder=None, period=None,
         images=True, **extra):
    """`items` の 1 件（公式の OpenAPI の例の形・T シャツの S は在庫が無制限・知らない欄も混ぜる）。"""
    node = {"id": f"616950cd768f7263b4da{i:04d}", "name": name or f"T シャツ {i}", "created_at": "2026-11-01T00:00:00+09:00",
            "status": status, "image_urls": [f"https://cdn.example.invalid/{i}.jpg"] if images else [],
            "price": price, "description": f"説明の本文 {i}", "regular_price": price, "type": "regular",
            "preorder_sales": preorder, "sales_period": period,
            "variations": [{"id": f"v{i}{n}", "name": size, "quantity": q, "sales_price": price,
                            "sku": f"sku-marker-{i}{n}", "barcode": f"barcode-marker-{i}{n}"}
                           for n, (size, q) in enumerate(zip(("S", "M", "L"), quantities))],
            "category_ids": ["c1"], "futureField": {"x": 1}}
    node.update(extra)
    return node


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    state = {"items": [item(1), item(2, quantities=(5, 0, 1)), item(3, status="hidden")], "calls": [],
             "tmp": tmp_path, "credential": "stcred_" + secrets.token_hex(16), "status": [], "body": [],
             "store": {"id": "66a0ba9ed7785100a418d4f9", "name": "STORES SHOP"}, "page_size": None}

    def transport(method, url, headers, body=None):
        wire.check_request(method, url, body)          # 本物の口と同じ柵を通す
        parts = urllib.parse.urlsplit(url)
        state["calls"].append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if state["status"]:
            status = state["status"].pop(0)
            if status != 200:
                return status, state["body"].pop(0) if state["body"] else b""
        if headers.get("Authorization") != f"Bearer {state['credential']}":
            # 【未確認】鍵が違うときの数（公式の定義に 401 は無い）。偽では 403 にしておく。
            return 403, json.dumps({"errors": [{"message": "error-message-marker forbidden"}]}).encode()
        if parts.path == STORE_PATH:
            return 200, json.dumps(state["store"]).encode()
        assert parts.path == ITEMS_PATH
        query = dict(urllib.parse.parse_qsl(parts.query))
        size = state["page_size"] or int(query["limit"])
        start = int(query.get("offset", 0))
        return 200, json.dumps({"items": state["items"][start:start + size]}).encode()

    lines = []

    def read_secret():
        return lines.pop(0) + "\n"

    state["lines"] = lines
    monkeypatch.setattr(store_cli, "TRANSPORT", transport)
    monkeypatch.setattr(store_cli, "READ_SECRET", read_secret)
    monkeypatch.setattr(stores, "SLEEP", lambda seconds: state.setdefault("slept", []).append(seconds))
    monkeypatch.setattr(stores, "NOW", lambda: NOW)
    return state


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def erun(capsys, *argv):
    rc_, out, err = run(capsys, *argv)
    return rc_, out + err


def add(capsys, url=URL, store=STORE):
    return run(capsys, "shop", "add", store, "--kind", "stores", "--project", "nigamilab", "--url", url, "--by", "m")


def setup_store(capsys, env, *extra):
    rc_, out, err = add(capsys)
    assert rc_ == 0, err
    text = out + err
    env["lines"][:] = [env["credential"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m", *extra)
    assert rc_ == 0, err
    return text + out + err


# ------------------------------------------------------------------ 台帳（店の頁の URL は持ち主が入れる）

def test_台帳は店の頁のURLを持ち注文を読まないことを案内する(capsys, env):
    rc_, out, err = add(capsys)
    assert rc_ == 0, err
    data = json.loads(pathlib.Path(ledger.path_for(STORE)).read_text())
    assert data == {"store": STORE, "kind": "stores", "project": "nigamilab", "token": ledger.default_token(STORE),
                    "public_url": URL}
    assert "クレデンシャル" in out and "注文" in out and "何も送りません" in out
    assert "STORES が提供するものではありません" in out
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "added")
    assert rows[-1]["medium"] == "stores"


@pytest.mark.parametrize("url", ["http://nigami.stores.jp", "https://u:p@nigami.stores.jp", "https://u@nigami.stores.jp",
                                 "https://:p@nigami.stores.jp", "https://@nigami.stores.jp",
                                 "https://nigami.stores.jp/?a=1", "https://nigami.stores.jp/#x",
                                 "https://nigami.stores.jp/?", "nigami.stores.jp", "", "https://",
                                 "https://nigami.stores.jp/a b", "https://nigami.stores.jp/\x00",
                                 "https://nigami.stores.jp:99999/", "javascript:alert(1)",
                                 "https://nigami.stores.jp/" + "a" * 2048])
def test_店の頁のURLが形に合わなければ前向きに断る(capsys, env, url):
    rc_, err = erun(capsys, "shop", "add", STORE, "--kind", "stores", "--project", "nigamilab", "--url", url,
                    "--by", "m")
    assert rc_ == 2 and "store_args_invalid" in err and "（url）" in err
    assert "--url" in rc.CATALOG["store_args_invalid"]["why"]
    assert not pathlib.Path(ledger.path_for(STORE)).exists()


def test_店の頁のURLの端():
    assert ledger.check_public_url("https://nigami.stores.jp") == "https://nigami.stores.jp"
    assert ledger.check_public_url("https://example.com/shop/nigami") == "https://example.com/shop/nigami"
    long = "https://n.example/" + "a" * (ledger.PUBLIC_URL_MAX - len("https://n.example/"))
    assert ledger.check_public_url(long) == long and ledger.check_public_url(long + "a") is None


def test_urlの無いSTORESとurlやshopのついたほかの店は断る(capsys, env):
    rc_, err = erun(capsys, "shop", "add", STORE, "--kind", "stores", "--project", "nigamilab", "--by", "m")
    assert rc_ == 2 and "（url）" in err
    rc_, err = erun(capsys, "shop", "add", "x-base", "--kind", "base", "--project", "nigamilab", "--url", URL,
                    "--by", "m")
    assert rc_ == 2 and "（url）" in err
    rc_, err = erun(capsys, "shop", "add", STORE, "--kind", "stores", "--project", "nigamilab", "--url", URL,
                    "--shop", "x", "--by", "m")
    assert rc_ == 2 and "（shop）" in err


def test_台帳でホストを変えられず店の頁のURLも検査する(capsys, env):
    setup_store(capsys, env)
    path = pathlib.Path(ledger.path_for(STORE))
    for bad in ({"public_url": "http://evil.example"}, {"public_url": "https://evil.example/?k=v"},
                {"host": "evil.example"}, {"shop": "evil"}, {"public_url": 1}):
        data = json.loads(path.read_text())
        good = dict(data)
        data.update(bad)
        path.write_text(json.dumps(data))
        rc_, value, _ = jrun(capsys, "shop", "items", STORE)
        assert rc_ == 2 and value["code"] == "store_ledger_invalid", bad
        path.write_text(json.dumps(good))
    data = json.loads(path.read_text())
    del data["public_url"]
    path.write_text(json.dumps(data))
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_ledger_invalid"


# ------------------------------------------------------------------ 鍵（クレデンシャル）

def test_クレデンシャルは店が見えたときだけ保存し期限の日は任意(capsys, env):
    text = setup_store(capsys, env)
    cfg = ledger.load(STORE)
    saved = json.loads(pathlib.Path(cfg["token"]).read_text())
    assert saved["auth"] == "bearer" and saved["access_token"] == env["credential"]
    assert saved["no_expiry"] is True and saved["expires_on"] is None and saved["shop_id"] == env["store"]["id"]
    assert set(saved) == {"auth", "access_token", "shop_id", "obtained_at", "expires_on", "no_expiry"}
    assert stat.S_IMODE(pathlib.Path(cfg["token"]).stat().st_mode) == 0o600
    assert "鍵の期限の日はありません" in text and env["credential"] not in text
    assert [urllib.parse.urlsplit(c["url"]).path for c in env["calls"]] == [STORE_PATH]
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "token_set")
    assert rows[-1]["medium"] == "stores"


def test_期限の日を付ければ14日前から知らせる(capsys, env):
    soon = (jst.now_jst().date() + datetime.timedelta(days=5)).isoformat()
    text = setup_store(capsys, env, "--expires", soon)
    saved = json.loads(pathlib.Path(ledger.load(STORE)["token"]).read_text())
    assert saved["expires_on"] == soon and saved["no_expiry"] is False
    assert f"鍵の期限 {soon}" in text and "STORES から新しいクレデンシャル" in text
    assert "同じものを新しい日を付けて" in text                          # 監査 5.18.0 直したほうがよい 4
    assert "置き直せば通ります" in rc.CATALOG["store_token_expired"]["fix"]["text"]
    _, out, err = run(capsys, "shop", "whoami", STORE)
    assert "STORES から新しいクレデンシャル" in err and "BASE Developers" not in out + err
    from thth import morning
    now = datetime.datetime.combine(jst.now_jst().date(), datetime.time(10), tzinfo=jst.JST)
    [line] = morning._store_key_lines({"nigamilab"}, now)
    assert "STORES のクレデンシャル" in line and "あと 5 日" in line


def test_morningは期限の日の無いクレデンシャルに何も言わない(capsys, env):
    from thth import morning
    setup_store(capsys, env)
    assert morning._store_key_lines({"nigamilab"}, NOW) == []


def test_鍵の引数は店の種類に合わせる(capsys, env):
    assert add(capsys)[0] == 0
    env["lines"][:] = [env["credential"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--auth", "token", "--by", "m")
    assert rc_ == 2 and "（auth）" in err
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--expires", "2020-01-01", "--by", "m")
    assert rc_ == 2 and "store_token_expires_past" in err
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--expires", "2027-13-01", "--by", "m")
    assert rc_ == 2 and "（expires）" in err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()


@pytest.mark.parametrize("status", [401, 403])
def test_違うクレデンシャルは保存しない(capsys, env, status):
    assert add(capsys)[0] == 0
    env["lines"][:] = ["stcred_wrong"]
    env["status"][:] = [status]
    env["body"][:] = [json.dumps({"errors": [{"message": "error-message-marker"}]}).encode()]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "store_token_rejected" in err and f"HTTP {status}" in err
    assert "error-message-marker" not in err and "stcred_wrong" not in err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()
    assert "STORES" in rc.CATALOG["store_token_rejected"]["why"]
    assert "スタンダードプラン" in rc.CATALOG["store_token_rejected"]["why"]


def test_別の店のクレデンシャルは前向きに断りforceで置き換えて写しを捨てる(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["store"] = {"id": "77b0ba9ed7785100a418d4f9", "name": "OTHER"}
    env["lines"][:] = [env["credential"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "store_shop_mismatch" in err
    env["lines"][:] = [env["credential"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m", "--force")
    assert rc_ == 0 and "前の店の写しを捨てました" in out
    assert ledger.read_snapshot(STORE) is None


def test_店の種類と鍵の形が合わなければ読まない(capsys, env):
    setup_store(capsys, env)
    path = pathlib.Path(ledger.load(STORE)["token"])
    good = path.read_text()
    for bad in ({"access_token": env["credential"]},                                   # BASE の形（auth が無い）
                {"auth": "shopify_token", "access_token": env["credential"]},
                {"auth": "bearer"}, {"auth": "bearer", "access_token": ""}):
        path.write_text(json.dumps(bad))
        rc_, value, _ = jrun(capsys, "shop", "items", STORE)
        assert rc_ == 2 and value["code"] == "store_token_missing", bad
    path.write_text(good)
    assert run(capsys, "shop", "items", STORE)[0] == 0


# ------------------------------------------------------------------ 読む・行への直し方

def test_whoamiは台帳に入れた店のURLを出す(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "whoami", STORE)
    assert rc_ == 0
    assert value["shop_id"] == env["store"]["id"] and value["shop_name"] == "STORES SHOP"
    assert value["shop_url"] == URL and value["able_to_business"] is None and value["token"]["state"] == "none"
    assert value["notice"] == "この道具（THTH）は STORES が提供するものではありません。"
    _, out, _ = run(capsys, "shop", "whoami", STORE)
    assert f"{URL}（台帳に入れた値）" in out and "（公開 不明）" in out


def test_商品は許した欄だけで公開でない商品はidだけ(capsys, env):
    env["items"] = [item(1), item(2, quantities=(5, 0, 1)), item(3, status="hidden"), item(4, status="unlisted"),
                    item(5, quantities=(0, 0, 0)), item(6, price=None, images=False, quantities=(7,)),
                    item(7, image_urls=["http://insecure.example/7.jpg"]), item(8, image_urls=None)]
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True
    rows = {row["id"]: row for row in value["items"]}
    one = rows[item(1)["id"]]
    assert one == {"id": item(1)["id"], "name": "T シャツ 1", "price": 3000, "stock": None, "visible": True,
                   "sales_status": "on_sale", "image_url": "https://cdn.example.invalid/1.jpg", "item_url": None,
                   "modified_at": None}
    assert rows[item(2)["id"]]["stock"] == 6
    assert rows[item(3)["id"]] == {"id": item(3)["id"], "visible": False}
    assert rows[item(4)["id"]] == {"id": item(4)["id"], "visible": False}          # 限定公開は非公開の扱い
    assert rows[item(5)["id"]]["stock"] == 0
    assert rows[item(6)["id"]]["price"] is None and rows[item(6)["id"]]["image_url"] is None
    assert rows[item(7)["id"]]["image_url"] is None and rows[item(8)["id"]]["image_url"] is None
    _, out, _ = run(capsys, "shop", "items", STORE)
    assert "在庫 —（数えない）" in out and "（非公開）" in out
    joined = json.dumps(value, ensure_ascii=False) + out
    for marker in ("説明の本文", "sku-marker", "barcode-marker", "futureField", "regular_price", "T シャツ 3"):
        assert marker not in joined, marker


def test_販売の状態は予約と期間の前と後を分ける():
    day = datetime.timedelta(days=1)
    iso = lambda t: t.isoformat()                                                    # noqa: E731
    cases = [
        ({}, "on_sale"),
        ({"preorder": {"shipping_start_at": "2027-01-01"}}, "preorder"),
        ({"period": {"period_start_at": iso(NOW + day), "period_end_at": None}}, "before_sale"),
        ({"period": {"period_start_at": None, "period_end_at": iso(NOW - day)}}, "after_sale"),
        ({"period": {"period_start_at": iso(NOW - day), "period_end_at": iso(NOW + day)}}, "on_sale"),
        ({"period": {"period_start_at": "2026-11-13T00:00:00Z", "period_end_at": None}}, "before_sale"),
        ({"period": {"period_start_at": "2026-11-12T13:00:00", "period_end_at": None}}, "before_sale"),  # 時差なしは日本の時刻
        ({"period": {"period_start_at": None, "period_end_at": None}}, "on_sale"),
    ]
    for kwargs, want in cases:
        assert stores.normalize(item(1, **kwargs), NOW)["sales_status"] == want, kwargs


@pytest.mark.parametrize("node, detail", [
    (item(1, variations=[]), "item_variations"),
    (item(1, variations=None), "item_variations"),
    (item(1, variations=[{"name": "S"}]), "item_variations"),
    (item(1, variations=[{"quantity": "3"}]), "item_variations"),
    (item(1, variations=[{"quantity": True}]), "item_variations"),
    (item(1, status="draft"), "item_status"),
    (item(1, id=12345), "item_fields"),
    (item(1, id="abc-def"), "item_fields"),
    (item(1, id="a" * 65), "item_fields"),
    (item(1, name=None) | {"name": None}, "item_fields"),
    (item(1, price="3000"), "item_fields"),
    (item(1, price=True), "item_fields"),
    (item(1, image_urls="https://x"), "item_fields"),
    (item(1, period="2026-01-01"), "item_sales_period"),
    (item(1, period={"period_start_at": "昨日", "period_end_at": None}), "item_sales_period"),
    (item(1, period={"period_start_at": 1, "period_end_at": None}), "item_sales_period"),
])
def test_形の違う商品は断り写しを進めない(capsys, env, node, detail):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    before = pathlib.Path(ledger.snapshot_path(STORE)).read_bytes()
    env["items"] = [item(9), node]
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE, "--accept")
    assert rc_ == 2 and value["code"] == "store_api_changed" and value["detail"] == detail
    assert pathlib.Path(ledger.snapshot_path(STORE)).read_bytes() == before


@pytest.mark.parametrize("payload", [[], {"data": []}, {"items": {}}, "x", None])
def test_応答の包みが違えば断る(capsys, env, payload):
    setup_store(capsys, env)
    env["status"][:] = [418]
    env["body"][:] = [json.dumps(payload).encode()]
    # 418 は数の断りになる（形を見る前）。包みの違いは 200 で返す偽の口で見る。
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_request_rejected" and value["detail"] == "HTTP 418"
    client = stores.Client({"public_url": URL}, "c", transport=lambda m, u, h: (200, json.dumps(payload).encode()))
    with pytest.raises(wire.StoreRefused) as caught:
        client.items()
    assert caught.value.code == "store_api_changed" and caught.value.detail == "api_items"


def test_店の応答の形が違えば断る():
    for payload in ({"id": 1, "name": "x"}, {"id": "a-b", "name": "x"}, {"id": "abc"}, [], {"name": "x"}):
        client = stores.Client({"public_url": URL}, "c", transport=lambda m, u, h, p=payload: (200, json.dumps(p).encode()))
        with pytest.raises(wire.StoreRefused) as caught:
            client.user()
        assert caught.value.code == "store_api_changed" and caught.value.detail == "store", payload
    client = stores.Client({"public_url": URL}, "c", transport=lambda m, u, h: (200, b"<html>"))
    with pytest.raises(wire.StoreRefused) as caught:
        client.user()
    assert caught.value.detail == "not_json"


def test_ちょうど100件なら次の頁を読み上限の頁で終わらなければ読み切れない(capsys, env):
    env["items"] = [item(i) for i in range(100)]
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True and value["pages"] == 2 and len(value["items"]) == 100
    assert env["slept"] == [stores.MIN_INTERVAL_SECONDS]
    offsets = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(c["url"]).query)) for c in env["calls"]
               if c["url"].split("?")[0].endswith(ITEMS_PATH)]
    assert offsets == [{"limit": "100", "offset": "0"}, {"limit": "100", "offset": "100"}]
    env["items"] = [item(i) for i in range(stores.PAGE_LIMIT * stores.MAX_PAGES + 1)]
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is False and value["pages"] == stores.MAX_PAGES
    assert "500 件の上限" in value["note"]
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 2 and value["code"] == "store_read_incomplete"
    assert "STORES" in rc.CATALOG["store_read_incomplete"]["why"]


# ------------------------------------------------------------------ 断り（【未確認】の数は偽で作った）

@pytest.mark.parametrize("status, body, code, detail", [
    (401, b"", "store_token_rejected", "HTTP 401"),
    (403, json.dumps({"errors": [{"message": "error-message-marker"}]}).encode(), "store_token_rejected", "HTTP 403"),
    (400, json.dumps({"errors": [{"message": "error-message-marker"}]}).encode(), "store_request_rejected", "HTTP 400"),
    (404, b"", "store_request_rejected", "HTTP 404"),
    (429, b"", "store_unavailable", "HTTP 429"),
    (503, json.dumps({"message": "error-message-marker"}).encode(), "store_unavailable", "HTTP 503"),
    (500, b"<html>", "store_unavailable", "HTTP 500"),
    (302, b"", "store_request_rejected", "redirect"),
])
def test_断りはHTTPの数だけで決め応答の文は入れない(capsys, env, status, body, code, detail):
    setup_store(capsys, env)
    env["status"][:] = [status]
    env["body"][:] = [body]
    rc_, out, err = run(capsys, "shop", "items", STORE, "--json")
    value = json.loads(out)
    assert rc_ == 2 and value["code"] == code and value["detail"] == detail
    assert "error-message-marker" not in out + err
    assert code != "store_scope_missing"


# ------------------------------------------------------------------ 違い・下書き・読み直し・外す

def test_下書きは店の頁のURLを載せ文字列のidで選べる(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["items"].append(item(40, quantities=(None,)))
    env["items"].append(item(41, quantities=(3,)))
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", item(40)["id"])
    assert rc_ == 0, err
    [path] = sorted(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    q = queuefile.parse(str(path))
    assert q.front_matter["status"] == "draft" and q.front_matter["from_store_url"] == "shop"
    assert q.front_matter["from_store"] == f"store/{STORE}/item/{item(40)['id']}"
    text = path.read_text()
    assert URL in text and "説明の本文" not in text and "cdn.example.invalid" not in text
    assert any("店の URL を載せました" in note for note in value["notes"])
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "drafted")
    assert rows[-1]["medium"] == "stores"


@pytest.mark.parametrize("kwargs", [
    {"preorder": {"shipping_start_at": "2027-01-01"}},
    {"period": {"period_start_at": (NOW + datetime.timedelta(days=1)).isoformat(), "period_end_at": None}},
    {"period": {"period_start_at": None, "period_end_at": (NOW - datetime.timedelta(days=1)).isoformat()}},
    {"quantities": (0, 0)},
])
def test_予約販売と販売の期間の外と売り切れの新着は下書きにしない(capsys, env, isolated_account_factory, kwargs):
    account = isolated_account_factory()
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["items"].append(item(50, **kwargs))
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_draft_nothing"
    assert "予約販売" in rc.CATALOG["store_draft_nothing"]["why"]


def test_承認の読み直しはSTORESの店でも文字列のidで商品を読む(capsys, env):
    from thth.shop import recheck
    setup_store(capsys, env)
    target = item(1)["id"]
    front = {"from_store": f"store/{STORE}/item/{target}", "from_store_price": "3000"}
    assert recheck.check("x.md", front) == (None, None)
    env["items"][0] = item(1, price=2500)
    reason, _ = recheck.check("x.md", front)
    assert reason and "store_item_changed" in reason and "price 3000 -> 2500" in reason
    env["items"][0] = item(1, status="unlisted")
    reason, _ = recheck.check("x.md", front)
    assert reason and "unpublished" in reason
    reason, _ = recheck.check("x.md", {"from_store": f"store/{STORE}/item/nothere0"})
    assert reason and "item_gone" in reason


def test_removeは鍵と写しと台帳を消しSTORESの側のクレデンシャルを案内する(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 1 and [t["category"] for t in value["targets"]] == ["token", "state", "ledger"]
    _, out, _ = run(capsys, "shop", "remove", STORE, "--by", "m")
    assert "STORES に頼んで" in out
    rc_, done, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0 and done["deleted"] == ["token", "state", "ledger"]
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "removed")
    assert rows[-1]["medium"] == "stores"
    assert not pathlib.Path(ledger.state_dir_for(STORE)).exists()


# ------------------------------------------------------------------ 鍵を出さない・打つのは表のものだけ

def test_柵_クレデンシャルは出力にもファイルにも出ない(capsys, env, isolated_account_factory):
    isolated_account_factory()
    texts = [setup_store(capsys, env)]
    for argv in (("whoami",), ("whoami", "--json"), ("items",), ("items", "--json"), ("diff",), ("diff", "--json")):
        _, out, err = run(capsys, "shop", argv[0], STORE, *argv[1:])
        texts.append(out + err)
    env["items"].append(item(7))
    _, out, err = run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")
    texts.append(out + err)
    env["status"][:] = [403]
    _, out, err = run(capsys, "shop", "whoami", STORE, "--json")
    texts.append(out + err)
    joined = "\n".join(texts)
    markers = [env["credential"], "説明の本文", "sku-marker", "barcode-marker", "error-message-marker"]
    for marker in markers:
        assert marker not in joined, marker
    token_file = pathlib.Path(ledger.load(STORE)["token"])
    found_token = False
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if not path.is_file():
            continue
        raw = kept_bytes(path)
        if path == token_file:
            found_token = env["credential"].encode() in raw
            continue
        for marker in markers:
            assert marker.encode() not in raw, f"{path} に {marker}"
    assert found_token                                   # 鍵のファイルにだけある（見る場所を取り違えていない）


def test_柵_命令を全部打っても打つのは表のSTORESの道だけ(capsys, env, isolated_account_factory):
    isolated_account_factory()
    env["items"] = [item(i) for i in range(120)]
    setup_store(capsys, env)
    for argv in (("whoami",), ("items",), ("diff",), ("diff", "--accept")):
        assert run(capsys, "shop", argv[0], STORE, *argv[1:])[0] == 0
    env["items"].append(item(900))
    assert run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")[0] == 0
    from thth.shop import recheck
    assert recheck.check("x.md", {"from_store": f"store/{STORE}/item/{item(1)['id']}"}) == (None, None)
    assert {c["method"] for c in env["calls"]} == {"GET"}
    assert {c["body"] for c in env["calls"]} == {None}
    assert {urllib.parse.urlsplit(c["url"]).netloc for c in env["calls"]} == {HOST}
    assert {urllib.parse.urlsplit(c["url"]).path for c in env["calls"]} == {STORE_PATH, ITEMS_PATH}
    names = {k for c in env["calls"] for k, _v in urllib.parse.parse_qsl(urllib.parse.urlsplit(c["url"]).query)}
    assert names == {"limit", "offset"}
    assert {c["headers"]["Authorization"] for c in env["calls"]} == {f"Bearer {env['credential']}"}
    # 台帳の店の頁の URL には何も送らない。
    assert not any(urllib.parse.urlsplit(URL).netloc in c["url"] for c in env["calls"])


def test_目録の文はSTORESでも読める():
    catalog = rc.CATALOG
    for code in ("store_token_missing", "store_token_rejected", "store_ledger_invalid", "store_api_changed",
                 "store_unavailable", "store_read_incomplete", "store_args_invalid"):
        entry = catalog[code]
        assert "STORES" in entry["why"] + entry["fix"]["text"] + (entry["better"] or ""), code
    assert "public_url" in catalog["store_ledger_invalid"]["fix"]["text"]
