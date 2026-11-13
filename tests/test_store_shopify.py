"""Shopify を読む口（`thth/shop/shopify.py`・`thth shop …` の kind shopify・5.17.0）。

柵（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §4・§8）:
  - 台帳に持つのは myshopify のラベルだけ（`xxx.myshopify.com` は後ろを落とす・独自ドメインや URL は前向きに断る）。
  - 鍵は 2 つの形（client id と secret・固定の鍵）。引数では受けない。店が見え、read_products があるときだけ保存する。
  - 余分な scope は断らずに知らせる（token set と whoami・--json の scopes_extra・記録には数だけ）。
  - 行への直し方: 公開は ACTIVE かつ onlineStoreUrl・値段はいちばん安い円・在庫を数えない商品は None。
  - 断り: 取り直しの断り（組織が違う・secret が違う）・401 は 1 回だけ取り直して打ち直す・THROTTLED・ACCESS_DENIED。
  - 鍵（client secret・短い鍵・固定の鍵）は出力にもファイル（鍵のファイルのほか）にも出ない。
  - 打つのは wire.py の表のものだけ（偽の transport が本物の口と同じ柵を通す）。
**【未確認】の応答の形（THROTTLED・ACCESS_DENIED・401 の本文・shop_not_permitted の数と本文・402 と 423）は偽で作った**
（設計 §2.1 の【未確認】・§11。本物の店では 1 度も動かしていない）。偽の材料の形は公式の頁の例による。
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
from thth.shop import ACTION_PREFIX, access, ledger, shopify, wire
from thth.shop import cli as store_cli

STORE = "nigami-shopify"
LABEL = "nigami-test"
DOMAIN = f"{LABEL}.myshopify.com"
GQL_PATH = f"/admin/api/{wire.SHOPIFY_API_VERSION}/graphql.json"


def product(i, *, title=None, status="ACTIVE", url=True, total=3, tracks=True, amount="1200.0", currency="JPY",
            media=True, **extra):
    """`products.nodes` の 1 件（公式の Product の欄の形・知らない欄も混ぜる）。"""
    node = {"id": f"gid://shopify/Product/{i}", "legacyResourceId": str(i), "title": title or f"豆 {i}",
            "status": status, "onlineStoreUrl": f"https://{LABEL}.example.invalid/products/p{i}" if url else None,
            "totalInventory": total, "tracksInventory": tracks, "updatedAt": "2026-11-10T00:00:00Z",
            "priceRangeV2": {"minVariantPrice": {"amount": amount, "currencyCode": currency}},
            "featuredMedia": {"preview": {"image": {"url": f"https://cdn.example.invalid/{i}.jpg"}}} if media else None,
            "descriptionHtml": f"説明の本文 {i}", "futureField": {"x": 1},
            # 種類の値段（5.18.1）: 既定は種類 1 つで、値段は商品のいちばん安い値と同じ。
            "variants": {"nodes": [{"legacyResourceId": str(i * 100), "price": amount}],
                         "pageInfo": {"hasNextPage": False}}}
    node.update(extra)
    return node


def cost(requested=312, available=1942, restore=100.0):
    return {"cost": {"requestedQueryCost": requested, "actualQueryCost": 58,
                     "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": available,
                                        "restoreRate": restore}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    state = {"products": [product(1), product(2), product(3, status="DRAFT")], "calls": [], "tmp": tmp_path,
             "client_id": "cid_" + secrets.token_hex(8), "client_secret": "csec_" + secrets.token_hex(16),
             "fixed": "shpat_" + secrets.token_hex(16), "minted": [], "mint_status": 200, "mint_body": None,
             "scopes": ["read_products"], "currency": "JPY", "domain": DOMAIN, "gql_status": [], "gql_body": [],
             "page_size": None, "extensions": cost(), "secrets_in": None, "rejected": set()}

    def mint_body():
        token = secrets.token_hex(16)
        state["minted"].append(token)
        return json.dumps({"access_token": token, "scope": ",".join(state["scopes"]), "expires_in": 86399}).encode()

    def transport(method, url, headers, body=None):
        wire.check_request(method, url, body)          # 本物の口と同じ柵を通す
        parts = urllib.parse.urlsplit(url)
        state["calls"].append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if parts.path == wire.SHOPIFY_TOKEN_PATH:
            form = dict(urllib.parse.parse_qsl(body.decode()))
            if state["mint_status"] != 200:
                return state["mint_status"], state["mint_body"] or b""
            if form["client_id"] != state["client_id"] or form["client_secret"] != state["client_secret"]:
                return 401, b'{"error":"invalid_client"}'
            return 200, mint_body()
        assert parts.path == GQL_PATH
        token = headers.get("X-Shopify-Access-Token")
        if state["gql_status"]:
            status = state["gql_status"].pop(0)
            if status != 200:
                return status, state["gql_body"].pop(0) if state["gql_body"] else b""
        if token in state["rejected"] or token not in state["minted"] + [state["fixed"]]:
            return 401, b'{"errors":"[API] Invalid API key or access token (unrecognized login or wrong password)"}'
        payload = json.loads(body.decode())
        name = next(n for n, text in wire.QUERIES.items() if text == payload["query"])
        if name == "shop":
            data = {"shop": {"id": "gid://shopify/Shop/1", "name": "にがみ屋", "myshopifyDomain": state["domain"],
                             "url": f"https://{LABEL}.example.invalid", "currencyCode": state["currency"],
                             "contactEmail": "owner-mail-marker@example.com"},
                    "currentAppInstallation": {"accessScopes": [{"handle": h} for h in state["scopes"]]}}
            return 200, json.dumps({"data": data, "extensions": state["extensions"]}).encode()
        variables = payload.get("variables") or {}
        size = state["page_size"] or variables["first"]
        start = int(variables["after"]) if variables.get("after") else 0
        nodes = state["products"][start:start + size]
        more = start + size < len(state["products"])
        data = {"products": {"nodes": nodes, "pageInfo": {"hasNextPage": more,
                                                          "endCursor": str(start + size) if more else None}}}
        out = {"data": data}
        if state["extensions"] is not None:
            out["extensions"] = state["extensions"]
        return 200, json.dumps(out).encode()

    lines = []

    def read_secret():
        return lines.pop(0) + "\n"

    state["lines"] = lines
    monkeypatch.setattr(store_cli, "TRANSPORT", transport)
    monkeypatch.setattr(store_cli, "READ_SECRET", read_secret)
    monkeypatch.setattr(shopify, "SLEEP", lambda seconds: state.setdefault("slept", []).append(seconds))
    return state


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def erun(capsys, *argv):
    """--json の無い命令（add・token set）の断り: `(rc, stderr)`。1 行目は `符丁: なぜ（詳細）`。"""
    rc_, out, err = run(capsys, *argv)
    return rc_, out + err


def add(capsys, shop=LABEL, store=STORE):
    return run(capsys, "shop", "add", store, "--kind", "shopify", "--project", "nigamilab", "--shop", shop, "--by", "m")


def setup_store(capsys, env, auth="client"):
    assert add(capsys)[0] == 0
    if auth == "client":
        env["lines"][:] = [env["client_id"], env["client_secret"]]
    else:
        env["lines"][:] = [env["fixed"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--auth", auth, "--by", "m")
    assert rc_ == 0, err
    return out + err


def _mints(env):
    return [c for c in env["calls"] if c["url"].endswith(wire.SHOPIFY_TOKEN_PATH)]


# ------------------------------------------------------------------ 台帳（ラベルだけ）

def test_台帳はmyshopifyのラベルだけで後ろは落とす(capsys, env):
    rc_, out, err = add(capsys, shop="Nigami-Test.myshopify.com")
    assert rc_ == 0, err
    data = json.loads(pathlib.Path(ledger.path_for(STORE)).read_text())
    assert data == {"store": STORE, "kind": "shopify", "project": "nigamilab", "token": ledger.default_token(STORE),
                    "shop": LABEL}
    assert "read_products だけ" in out and "Shopify が提供するものではありません" in out


@pytest.mark.parametrize("shop", ["https://nigami.example.com", "nigami.example.com", "a/b", "", "x" * 61,
                                  "nigami.myshopify.com.evil.example"])
def test_独自ドメインやURLは前向きに断る(capsys, env, shop):
    rc_, err = erun(capsys, "shop", "add", STORE, "--kind", "shopify", "--project", "nigamilab", "--shop", shop,
                    "--by", "m")
    assert rc_ == 2 and "store_args_invalid" in err and "（shop）" in err
    assert "myshopify のラベル" in rc.CATALOG["store_args_invalid"]["why"]
    assert not pathlib.Path(ledger.path_for(STORE)).exists()


def test_shopの無いShopifyとshopのついたBASEは断る(capsys, env):
    rc_, err = erun(capsys, "shop", "add", STORE, "--kind", "shopify", "--project", "nigamilab", "--by", "m")
    assert rc_ == 2 and "store_args_invalid" in err and "（shop）" in err
    rc_, err = erun(capsys, "shop", "add", "x-base", "--kind", "base", "--project", "nigamilab", "--shop", LABEL,
                    "--by", "m")
    assert rc_ == 2 and "（shop）" in err


def test_台帳でホストを変えられない(capsys, env):
    setup_store(capsys, env)
    path = pathlib.Path(ledger.path_for(STORE))
    for bad in ({"shop": "evil.example"}, {"shop": "x.myshopify.com"}, {"host": "evil.example"}):
        data = json.loads(path.read_text())
        good = dict(data)
        data.update(bad)
        path.write_text(json.dumps(data))
        rc_, value, _ = jrun(capsys, "shop", "items", STORE)
        assert rc_ == 2 and value["code"] == "store_ledger_invalid", bad
        path.write_text(json.dumps(good))


# ------------------------------------------------------------------ 鍵

def test_clientの鍵は店が見えたときだけ保存し短い鍵も別のファイルに置く(capsys, env):
    text = setup_store(capsys, env)
    cfg = ledger.load(STORE)
    saved = json.loads(pathlib.Path(cfg["token"]).read_text())
    assert saved["auth"] == "shopify_client" and saved["client_secret"] == env["client_secret"]
    assert saved["no_expiry"] is True and saved["expires_on"] is None and saved["shop_id"] == DOMAIN
    cached = json.loads(pathlib.Path(access.path_for(STORE)).read_text())
    assert cached["schema"] == access.SCHEMA and cached["access_token"] == env["minted"][-1]
    assert "scope" not in cached                       # 権限の名前は置かない（監査 5.17.0 危ない 1）
    for path in (cfg["token"], access.path_for(STORE)):
        assert stat.S_IMODE(pathlib.Path(path).stat().st_mode) == 0o600
    assert "鍵の期限の日はありません" in text and env["client_secret"] not in text
    # 置いた短い鍵を使い回す（whoami・items で鍵を取り直さない）。
    calls = len(_mints(env))
    assert run(capsys, "shop", "whoami", STORE)[0] == 0
    assert run(capsys, "shop", "items", STORE)[0] == 0
    assert len(_mints(env)) == calls == 1


def test_固定の鍵は取り直さない(capsys, env):
    setup_store(capsys, env, auth="token")
    saved = json.loads(pathlib.Path(ledger.load(STORE)["token"]).read_text())
    assert saved["auth"] == "shopify_token" and saved["access_token"] == env["fixed"]
    assert not pathlib.Path(access.path_for(STORE)).exists()
    assert run(capsys, "shop", "items", STORE)[0] == 0
    assert _mints(env) == []
    env["rejected"].add(env["fixed"])
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_token_rejected" and _mints(env) == []


def test_期限の日と鍵の形の引数は店の種類に合わせる(capsys, env):
    assert add(capsys)[0] == 0
    env["lines"][:] = [env["fixed"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--auth", "token", "--expires", "2027-01-31", "--by", "m")
    assert rc_ == 2 and "store_args_invalid" in err and "（expires）" in err
    run(capsys, "shop", "add", "x-base", "--kind", "base", "--project", "nigamilab", "--by", "m")
    rc_, err = erun(capsys, "shop", "token", "set", "x-base", "--auth", "client", "--expires", "2027-01-31", "--by", "m")
    assert rc_ == 2 and "（auth）" in err
    rc_, err = erun(capsys, "shop", "token", "set", "x-base", "--by", "m")
    assert rc_ == 2 and "（expires）" in err


def test_read_productsが無ければ保存しない(capsys, env):
    assert add(capsys)[0] == 0
    env["scopes"] = ["read_inventory"]
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "store_scope_missing" in err and "（read_products）" in err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()
    assert not pathlib.Path(access.path_for(STORE)).exists()


def test_余分なscopeは保存して前向きに知らせ記録には数だけ(capsys, env):
    env["scopes"] = ["read_products", "write_products", "read_orders", "read_customers"]
    text = setup_store(capsys, env)
    assert "read_products のほかに 3 つの権限" in text and "read_orders" in text and "入れ直すと安心" in text
    rc_, value, _ = jrun(capsys, "shop", "whoami", STORE)
    assert rc_ == 0 and value["scopes_extra"] == ["read_customers", "read_orders", "write_products"]
    assert value["token"]["state"] == "none" and value["notice"].startswith("この道具（THTH）は Shopify")
    _, out, _ = run(capsys, "shop", "whoami", STORE)
    assert "3 つの権限" in out and "鍵の期限: なし" in out
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "token_set")
    assert rows[-1]["diff"]["scopes_extra"] == [None, 3]
    assert "read_orders" not in json.dumps(rows)
    # 権限の名前の一覧は、鍵のファイル 2 つを含むどのファイルにも残さない（privacy・監査 5.17.0 危ない 1）。
    assert run(capsys, "shop", "items", STORE)[0] == 0
    files = [p for p in pathlib.Path(env["tmp"]).rglob("*") if p.is_file()]
    assert pathlib.Path(access.path_for(STORE)) in files and pathlib.Path(ledger.load(STORE)["token"]) in files
    for path in files:
        raw = kept_bytes(path)
        for name in ("read_orders", "write_products", "read_customers"):
            assert name.encode() not in raw, f"{path} に {name}"


def test_通貨とmyshopifyのドメインが違えば断る(capsys, env):
    assert add(capsys)[0] == 0
    env["currency"] = "USD"
    env["lines"][:] = [env["fixed"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--auth", "token", "--by", "m")
    assert rc_ == 2 and "store_currency_unsupported" in err
    env["currency"], env["domain"] = "JPY", "other-shop.myshopify.com"
    env["lines"][:] = [env["fixed"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--auth", "token", "--by", "m")
    assert rc_ == 2 and "store_shop_mismatch" in err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()


@pytest.mark.parametrize("status,body", [
    (400, b'{"error":"shop_not_permitted","error_description":"Client credentials cannot be performed on this shop."}'),
    (400, b"Oauth error shop_not_permitted: Client credentials cannot be performed on this shop."),
    (403, b"x" * 100 + b"shop_not_permitted"),
])
def test_組織の違う店は前向きに断る(capsys, env, status, body):
    """【未確認】shop_not_permitted の HTTP の数と本文の形（語があるか無いかだけで決める）。"""
    assert add(capsys)[0] == 0
    env["mint_status"], env["mint_body"] = status, body
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "store_org_mismatch" in err and "--auth token" in err
    assert "Client credentials cannot" not in err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()


@pytest.mark.parametrize("status,code,detail", [(401, "store_refresh_rejected", "HTTP 401"),
                                                (400, "store_refresh_rejected", "HTTP 400"),
                                                (404, "store_shop_unknown", None),
                                                (302, "store_request_rejected", "redirect"),
                                                (503, "store_unavailable", "HTTP 503")])
def test_鍵を取る口の断り(capsys, env, status, code, detail):
    assert add(capsys)[0] == 0
    env["mint_status"], env["mint_body"] = status, b'{"error":"invalid_client","marker":"mint-body-marker"}'
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc_, err = erun(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and code in err and (detail is None or f"（{detail}）" in err)
    assert "mint-body-marker" not in err


# ------------------------------------------------------------------ 読む・行への直し方

def test_商品は許した欄だけで公開でない商品はidだけ(capsys, env):
    env["products"] = [product(1), product(2, url=False), product(3, status="DRAFT"), product(4, status="ARCHIVED"),
                       product(5, status="UNLISTED"), product(6, tracks=False, total=0), product(7, total=0),
                       product(8, total=-2), product(9, media=False), product(10, amount="1500.00")]
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True
    rows = {row["id"]: row for row in value["items"]}
    assert rows[1] == {"id": 1, "name": "豆 1", "price": 1200, "stock": 3, "visible": True, "sales_status": "active",
                       "image_url": "https://cdn.example.invalid/1.jpg",
                       "item_url": f"https://{LABEL}.example.invalid/products/p1", "modified_at": "2026-11-10T00:00:00Z",
                       "variant_prices": {"100": 1200}}
    for hidden in (2, 3, 4, 5):
        assert rows[hidden] == {"id": hidden, "visible": False}
    assert rows[6]["stock"] is None and rows[7]["stock"] == 0 and rows[8]["stock"] == -2
    assert rows[9]["image_url"] is None and rows[10]["price"] == 1500
    _, out, _ = run(capsys, "shop", "items", STORE)
    assert "在庫 —（数えない）" in out
    assert "説明の本文" not in json.dumps(value)


@pytest.mark.parametrize("node,code", [(product(99, amount="1200.5"), "store_currency_unsupported"),
                                       (product(99, currency="USD"), "store_currency_unsupported"),
                                       (product(99, legacyResourceId="abc"), "store_api_changed"),
                                       (product(99, tracksInventory=None), "store_api_changed")])
def test_円の整数でない値段と形の違う商品は断り写しを進めない(capsys, env, node, code):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    before = pathlib.Path(ledger.snapshot_path(STORE)).read_bytes()
    env["products"].append(node)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE, "--accept")
    assert rc_ == 2 and value["code"] == code
    assert pathlib.Path(ledger.snapshot_path(STORE)).read_bytes() == before


def test_頁を送って読み上限の頁で終わらなければ読み切れない(capsys, env, monkeypatch):
    env["products"] = [product(i) for i in range(1, 8)]
    env["page_size"] = 3
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True and value["pages"] == 3 and len(value["items"]) == 7
    assert env["slept"] == [shopify.MIN_INTERVAL_SECONDS] * 2      # 点数に余りがあれば 0.5 秒
    monkeypatch.setattr(shopify, "MAX_PAGES", 2)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 2 and value["code"] == "store_read_incomplete"


def test_点数が少なければ回復を待ち読めなければ1秒():
    # 足りない点（頼む点 − 残り）が戻るまで ＋ 0.5 秒・標準の店（毎秒 100 点）では長くて 10 秒。足りていれば 0.5 秒
    # （監査 5.18.1 の確かめの危ない 1）。戻りの遅い店（毎秒 50 点）は 5.18.2 から頼む点 ÷ 戻る速さまで待つ
    # （直したほうがよい 10。前は 10 秒で頭打ちになり、毎頁 THROTTLED を招いた）。
    assert shopify.throttle_wait({"extensions": cost(requested=312, available=500, restore=100.0)}) == 0.5
    assert shopify.throttle_wait({"extensions": cost(requested=728, available=128, restore=100.0)}) == 6.5
    assert shopify.throttle_wait({"extensions": cost(requested=728, available=128, restore=100.0)}, 1.5) == 9.5
    assert shopify.throttle_wait({"extensions": cost(requested=1000, available=10, restore=50.0)}) == pytest.approx(20.3)
    assert shopify.throttle_wait({"extensions": cost(requested=728, available=10, restore=100.0)}, 3.0) == 10.0
    assert shopify.throttle_wait({"extensions": cost()}) == shopify.MIN_INTERVAL_SECONDS
    assert shopify.throttle_wait({"data": {}}) == 1.0
    assert shopify.throttle_wait({"extensions": {"cost": {"requestedQueryCost": "x"}}}) == 1.0


def test_extensionsの無い応答でも読める(capsys, env):
    env["extensions"] = None
    env["products"] = [product(i) for i in range(1, 5)]
    env["page_size"] = 2
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and len(value["items"]) == 4 and env["slept"] == [1.0]


# ------------------------------------------------------------------ 断り（【未確認】の形は偽で作った）

def _graphql_errors(code, message="secret-message-marker"):
    return json.dumps({"errors": [{"message": message, "extensions": {"code": code}}]}).encode()


@pytest.mark.parametrize("status,body,code,detail", [
    (200, _graphql_errors("THROTTLED"), "store_unavailable", "throttled"),
    (200, _graphql_errors("ACCESS_DENIED"), "store_scope_missing", "ACCESS_DENIED"),
    (200, _graphql_errors("SOMETHING_NEW"), "store_api_changed", "graphql_errors"),
    (200, _graphql_errors("lower case"), "store_api_changed", "graphql_errors"),
    (403, b'{"errors":"secret-message-marker"}', "store_scope_missing", "HTTP 403"),
    (404, b"", "store_shop_unknown", None),
    (302, b"", "store_request_rejected", "redirect"),
    (402, b"", "store_unavailable", "shop_frozen"),
    (423, b"", "store_unavailable", "shop_frozen"),
    (429, b"", "store_unavailable", "HTTP 429"),
    (200, b"not json", "store_api_changed", "not_json"),
    (200, b'{"data": null}', "store_api_changed", "graphql_shape"),
])
def test_問い合わせの断りは数と検査した語だけで決め文は入れない(capsys, env, status, body, code, detail):
    setup_store(capsys, env, auth="token")
    env["gql_status"], env["gql_body"] = [status], [body]
    if status == 200:
        # 200 のときは偽の transport が本文を返さないので、差し替えた transport で返す。
        real = store_cli.TRANSPORT

        def transport(method, url, headers, body_=None):
            wire.check_request(method, url, body_)
            env["gql_status"].clear()
            return 200, body
        store_cli.TRANSPORT = transport
        try:
            rc_, value, err = jrun(capsys, "shop", "items", STORE)
        finally:
            store_cli.TRANSPORT = real
    else:
        rc_, value, err = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == code and value["detail"] == detail
    assert value["why"] and value["fix"] and value["better"]
    assert "secret-message-marker" not in json.dumps(value) + err


@pytest.mark.parametrize("body", [b'{"errors":"[API] Invalid API key or access token"}', b""])
def test_401は置いた鍵を捨てて1回だけ取り直して打ち直す(capsys, env, body):
    """【未確認】鍵が違うときの 401 の本文（文字列の形と空の 2 通り・数だけで決める）。"""
    setup_store(capsys, env)
    old = env["minted"][-1]
    env["rejected"].add(old)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and len(_mints(env)) == 2
    cached = json.loads(pathlib.Path(access.path_for(STORE)).read_text())
    assert cached["access_token"] == env["minted"][-1] != old
    # 取り直した鍵も断られれば、それ以上は打たずに断る（打ち直しは 1 回だけ）。
    env["gql_status"], env["gql_body"] = [401, 401], [body, body]
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_token_rejected" and len(_mints(env)) == 3
    assert not env["gql_status"]


def test_取り直しが断られればその命令で知らせる(capsys, env):
    setup_store(capsys, env)
    pathlib.Path(access.path_for(STORE)).unlink()
    env["mint_status"], env["mint_body"] = 401, b'{"error":"invalid_client"}'
    rc_, value, _ = jrun(capsys, "shop", "whoami", STORE)
    assert rc_ == 2 and value["code"] == "store_refresh_rejected" and "--auth client" in value["fix"]


# ------------------------------------------------------------------ 違い・下書き・外す

def test_下書きは商品頁のURLを載せる(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["products"].append(product(40, tracks=False, total=0))
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    [path] = sorted(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    q = queuefile.parse(str(path))
    assert q.front_matter["status"] == "draft" and q.front_matter["from_store_url"] == "item"
    assert q.front_matter["from_store"] == f"store/{STORE}/item/40"
    assert f"https://{LABEL}.example.invalid/products/p40" in path.read_text()
    assert not any("店の URL を載せました" in note for note in value["notes"])
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "drafted")
    assert rows[-1]["medium"] == "shopify"


def test_承認の読み直しはShopifyの店でも商品を読む(capsys, env, isolated_account_factory):
    from thth.shop import recheck
    setup_store(capsys, env)
    front = {"from_store": f"store/{STORE}/item/1", "from_store_price": "1200"}
    assert recheck.check("x.md", front) == (None, None)
    env["products"][0] = product(1, amount="900.0")
    reason, _ = recheck.check("x.md", front)
    assert reason and "store_item_changed" in reason
    reason, _ = recheck.check("x.md", {"from_store": f"store/{STORE}/item/abc"})
    assert reason and "from_store_unreadable" in reason


def test_removeは鍵と短い鍵と写しと台帳を消しShopifyの側の鍵を案内する(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 1 and [t["category"] for t in value["targets"]] == ["token", "access", "state", "ledger"]
    _, out, _ = run(capsys, "shop", "remove", STORE, "--by", "m")
    assert "Dev Dashboard" in out and "短い鍵" in out
    rc_, done, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0 and done["deleted"] == ["token", "access", "state", "ledger"]
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "removed")
    assert rows[-1]["medium"] == "shopify"                 # 監査 5.17.0 直したほうがよい 4
    assert not pathlib.Path(access.path_for(STORE)).exists()
    assert not pathlib.Path(ledger.state_dir_for(STORE)).exists()


def test_morningは期限の日の無い鍵に何も言わない(capsys, env):
    from thth import morning
    setup_store(capsys, env)
    now = datetime.datetime(2026, 11, 12, 10, 0, tzinfo=jst.JST)
    assert morning._store_key_lines({"nigamilab"}, now) == []


# ------------------------------------------------------------------ 鍵を出さない・打つのは表のものだけ

def test_柵_鍵は出力にもファイルにも出ない(capsys, env, isolated_account_factory):
    isolated_account_factory()
    env["scopes"] = ["read_products", "read_orders"]
    texts = [setup_store(capsys, env)]
    for argv in (("whoami",), ("whoami", "--json"), ("items",), ("items", "--json"), ("diff",), ("diff", "--json")):
        _, out, err = run(capsys, "shop", argv[0], STORE, *argv[1:])
        texts.append(out + err)
    env["products"].append(product(7))
    _, out, err = run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")
    texts.append(out + err)
    env["rejected"].update(env["minted"])
    env["mint_status"] = 401
    _, out, err = run(capsys, "shop", "whoami", STORE, "--json")
    texts.append(out + err)
    joined = "\n".join(texts)
    markers = [env["client_secret"], env["client_id"], *env["minted"], "owner-mail-marker", "説明の本文"]
    for marker in markers:
        assert marker not in joined, marker
    token_file = pathlib.Path(ledger.load(STORE)["token"])
    access_file = pathlib.Path(access.path_for(STORE))
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if not path.is_file() or path in (token_file, access_file):
            continue
        raw = kept_bytes(path)
        for marker in markers:
            assert marker.encode() not in raw, f"{path} に {marker}"


def test_柵_命令を全部打っても打つのは表のShopifyの道だけ(capsys, env, isolated_account_factory):
    isolated_account_factory()
    setup_store(capsys, env)
    for argv in (("whoami",), ("items",), ("diff",), ("diff", "--accept")):
        assert run(capsys, "shop", argv[0], STORE, *argv[1:])[0] == 0
    env["products"].append(product(9))
    assert run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")[0] == 0
    assert {c["method"] for c in env["calls"]} == {"POST"}
    assert {urllib.parse.urlsplit(c["url"]).netloc for c in env["calls"]} == {DOMAIN}
    assert {urllib.parse.urlsplit(c["url"]).path for c in env["calls"]} == {wire.SHOPIFY_TOKEN_PATH, GQL_PATH}
    queries = {json.loads(c["body"])["query"] for c in env["calls"] if c["url"].endswith(GQL_PATH)}
    assert queries == set(wire.QUERIES.values())
    # client secret を載せるのは鍵を取る口だけ。GraphQL には短い鍵だけを載せる。
    for call in env["calls"]:
        if call["url"].endswith(GQL_PATH):
            assert env["client_secret"].encode() not in call["body"]
            assert call["headers"]["X-Shopify-Access-Token"] in env["minted"]


def test_新しい符丁は目録に3つがそろう():
    for code in ("store_org_mismatch", "store_refresh_rejected", "store_refresh_busy", "store_shop_unknown",
                 "store_currency_unsupported"):
        assert code in wire.REASONS
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code
