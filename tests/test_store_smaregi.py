"""スマレジを読む口（`thth/shop/smaregi.py`・`thth shop …` の kind smaregi・計画 実店舗 段 5）。

柵:
  - 打つのは、鍵を取る POST 1 つ（`id.smaregi.jp`・本文は grant_type と読む 3 つの scope だけ・Basic）と、商品・店舗・在庫の GET
    だけ。`fields` は決まった文字列だけ（店舗の住所・電話・商品の原価などを受けない）。偽の transport が本物の柵を通す。
  - 返った scope に読む 3 つがそろわなければ `pos_scope_missing`。ほかの scope が付いて返れば知らせる。
  - **レジの品は既定で材料にしない**（Square と同じ・出さない商品の在庫は読まない）。
  - client id と secret と取った鍵は、出力にもファイル（鍵のファイルのほか）にも出ない。取った鍵は手元に置かない。
  - token set と doctor は「まだ確かめていない所」（マイページの認証情報がこの取り方で通るか）を正直に言う。
**【未確認】**: マイページで発行した認証情報がこの取り方で通るか・契約 ID の字の形・本物の応答（`pos.UNVERIFIED`）。偽で作った。
"""
from __future__ import annotations

import base64
import json
import pathlib
import secrets
import urllib.parse

import pytest

from tests.kept_bytes import kept_bytes
from thth import admin_log, cli, queuefile
from thth import refusal_catalog as rc
from thth.shop import ACTION_PREFIX, ledger, smaregi, wire
from thth.shop import cli as store_cli

STORE = "nigami-smaregi"
URL = "https://nigami.example.invalid"
CONTRACT = "nigamic01"
STORE_ID = "1"


def product(i, *, category="10", stock_control="0", price="1200", **extra):
    node = {"productId": str(i), "categoryId": category, "productName": f"豆 {i}", "price": price,
            "stockControlDivision": stock_control, "cost": "cost-marker", "description": f"説明の本文 {i}"}
    node.update(extra)
    return node


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    state = {"products": [product(1), product(2), product(3, category="20")],
             "stock": {"1": "5", "2": "0"}, "calls": [], "tmp": tmp_path,
             "client_id": "cid" + secrets.token_hex(8), "client_secret": "csec" + secrets.token_hex(16),
             "access": "acc" + secrets.token_hex(20), "granted": wire.SMAREGI_SCOPE, "status": [],
             "store": {"storeId": STORE_ID, "storeName": "にがみ 本店"}, "page_size": None}

    def transport(method, url, headers, body=None):
        wire.check_request(method, url, body)          # 本物の口と同じ柵を通す
        parts = urllib.parse.urlsplit(url)
        state["calls"].append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if state["status"]:
            status = state["status"].pop(0)
            if status != 200:
                return status, json.dumps({"type": "about:blank", "title": "error-message-marker"}).encode()
        if parts.netloc == wire.SMAREGI_ID_HOST:
            assert parts.path == f"/app/{CONTRACT}/token"
            want = base64.b64encode(f"{state['client_id']}:{state['client_secret']}".encode()).decode()
            if headers.get("Authorization") != f"Basic {want}":
                return 401, json.dumps({"error": "invalid_client", "error_description": "error-message-marker"}).encode()
            return 200, json.dumps({"access_token": state["access"], "token_type": "Bearer", "expires_in": 3600,
                                    "scope": state["granted"]}).encode()
        if headers.get("Authorization") != f"Bearer {state['access']}":
            return 401, b""
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == f"/{CONTRACT}/pos/stores/{STORE_ID}":
            return 200, json.dumps(dict(state["store"], address="address-marker", phoneNumber="phone-marker")).encode()
        if parts.path.startswith(f"/{CONTRACT}/pos/stores/"):
            return 404, b""
        if parts.path == f"/{CONTRACT}/pos/products":
            size = state["page_size"] or int(query["limit"])
            start = (int(query["page"]) - 1) * size
            return 200, json.dumps(state["products"][start:start + size]).encode()
        assert parts.path == f"/{CONTRACT}/pos/stock" and query["store_id"] == STORE_ID
        amount = state["stock"].get(query["product_id"])
        rows = [] if amount is None else [{"productId": query["product_id"], "storeId": STORE_ID, "stockAmount": amount}]
        return 200, json.dumps(rows).encode()

    lines = []
    state["lines"] = lines
    monkeypatch.setattr(store_cli, "TRANSPORT", transport)
    monkeypatch.setattr(store_cli, "READ_SECRET", lambda: lines.pop(0) + "\n")
    monkeypatch.setattr(smaregi, "SLEEP", lambda seconds: None)
    return state


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def add(capsys, *extra):
    return run(capsys, "shop", "add", STORE, "--kind", "smaregi", "--project", "nigamilab", "--url", URL,
               "--contract", CONTRACT, "--location", STORE_ID, *extra, "--by", "m")


def setup_store(capsys, env):
    rc_, out, err = add(capsys)
    assert rc_ == 0, err
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc2, out2, err2 = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc2 == 0, err2
    return out + err + out2 + err2


def publish(capsys, *args):
    rc_, out, err = run(capsys, "shop", "publish", STORE, *args, "--by", "m")
    assert rc_ == 0, err
    return out


# ------------------------------------------------------------------ 台帳と鍵

def test_台帳は契約と店舗と空の出す品の表を持ちマイページの道を案内する(capsys, env):
    rc_, out, err = add(capsys)
    assert rc_ == 0, err
    data = json.loads(pathlib.Path(ledger.path_for(STORE)).read_text())
    assert data == {"store": STORE, "kind": "smaregi", "project": "nigamilab", "token": ledger.default_token(STORE),
                    "public_url": URL, "contract": CONTRACT, "location": STORE_ID,
                    "publish": {"items": [], "categories": []}}
    assert "プレミアムプラン以上" in out and "pos.products:read" in out and "スマレジが提供するものではありません" in out


@pytest.mark.parametrize("argv, detail", [
    (("--contract", "a/b", "--location", "1"), "contract"), (("--location", "1"), "contract"),
    (("--contract", CONTRACT, "--location", "A1"), "location"), (("--contract", CONTRACT), "location"),
    (("--contract", CONTRACT, "--location", "1234567890"), "location"),
])
def test_契約と店舗の形が合わなければ前向きに断る(capsys, env, argv, detail):
    rc_, out, err = run(capsys, "shop", "add", STORE, "--kind", "smaregi", "--project", "nigamilab", "--url", URL,
                        *argv, "--by", "m")
    assert rc_ == 2 and "pos_args_invalid" in out + err and f"（{detail}）" in out + err
    assert rc.CATALOG["pos_args_invalid"]["by_detail"][detail]["fix"]


def test_鍵は取れて店舗が見えたときだけ保存しまだ確かめていない所を言う(capsys, env):
    text = setup_store(capsys, env)
    cfg = ledger.load(STORE)
    saved = json.loads(pathlib.Path(cfg["token"]).read_text())
    assert saved["auth"] == "smaregi_client" and saved["client_id"] == env["client_id"]
    assert saved["client_secret"] == env["client_secret"] and saved["no_expiry"] is True
    assert saved["shop_id"] == CONTRACT and "access_token" not in saved          # 取った鍵は置かない
    assert "まだ確かめていない所" in text and "マイページで発行した認証情報" in text
    for value in (env["client_id"], env["client_secret"], env["access"]):
        assert value not in text
    [token_call, store_call] = env["calls"]
    assert token_call["method"] == "POST" and token_call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert dict(urllib.parse.parse_qsl(token_call["body"].decode())) == {"grant_type": "client_credentials",
                                                                         "scope": wire.SMAREGI_SCOPE}
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(store_call["url"]).query)) == {"fields": "storeId,storeName"}
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "token_set")
    assert rows[-1]["medium"] == "smaregi"


def test_違うclientは保存せず応答の文を入れない(capsys, env):
    assert add(capsys)[0] == 0
    env["lines"][:] = ["cidwrong", "csecwrong"]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "pos_token_rejected" in out + err and "HTTP 401" in out + err
    assert "error-message-marker" not in out + err and "csecwrong" not in out + err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()
    assert "まだ本物で確かめていません" in rc.CATALOG["pos_token_rejected"]["why"]


def test_返ったscopeが足りなければ断りほかのscopeは知らせる(capsys, env):
    assert add(capsys)[0] == 0
    env["granted"] = "pos.products:read pos.stores:read"
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "pos_scope_missing" in out + err and "（scope）" in out + err
    env["granted"] = wire.SMAREGI_SCOPE + " pos.transactions:read"
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 0 and "pos.transactions:read" in out and "外すと" in out


def test_台帳の店舗が無ければ前向きに断る(capsys, env):
    rc_, _o, err = run(capsys, "shop", "add", STORE, "--kind", "smaregi", "--project", "nigamilab", "--url", URL,
                       "--contract", CONTRACT, "--location", "9", "--by", "m")
    assert rc_ == 0, err
    env["lines"][:] = [env["client_id"], env["client_secret"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "pos_location_unknown" in out + err


# ------------------------------------------------------------------ 読む

def test_柵_出す品が無ければ名前を持たず在庫も読まない(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and all(r == {"id": r["id"], "visible": False} for r in value["items"])
    assert not any("/pos/stock" in c["url"] for c in env["calls"])


def test_出す品と部門だけ公開の行になり在庫は店舗の数(capsys, env):
    env["products"] += [product(4, stock_control="1"), product(5), product(6, price="980.0")]
    setup_store(capsys, env)
    publish(capsys, "--category", "10")
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    rows = {r["id"]: r for r in value["items"]}
    assert rows["1"] == {"id": "1", "name": "豆 1", "price": 1200, "stock": 5, "visible": True,
                         "sales_status": "on_sale", "image_url": None, "item_url": None, "modified_at": None}
    assert rows["2"]["stock"] == 0 and rows["3"] == {"id": "3", "visible": False}
    assert rows["4"]["stock"] is None and rows["5"]["stock"] == 0 and rows["6"]["price"] == 980
    stock_calls = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(c["url"]).query))["product_id"]
                   for c in env["calls"] if "/pos/stock" in c["url"]]
    assert stock_calls == ["1", "2", "5", "6"]                  # 出す品の、在庫を数える品だけ
    joined = json.dumps(value, ensure_ascii=False)
    for marker in ("cost-marker", "説明の本文", "address-marker", "phone-marker"):
        assert marker not in joined


def test_商品の頁を進め上限で終わらなければ読み切れない(capsys, env, monkeypatch):
    env["page_size"] = 2
    monkeypatch.setattr(smaregi, "PAGE_LIMIT", 2)
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True and value["pages"] == 2
    env["products"] += [product(i) for i in range(10, 20)]
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 2 and value["code"] == "store_read_incomplete"


@pytest.mark.parametrize("node, detail", [
    ({"stockControlDivision": "9"}, "stock_control_division"), ({"price": "abc"}, "price"),
    ({"productId": "x1"}, "product_id"), ({"productName": None}, "product_name"),
])
def test_形の違う商品は断る(capsys, env, node, detail):
    env["products"] = [product(1, **node)]
    setup_store(capsys, env)
    publish(capsys, "--category", "10")
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "pos_api_changed" and value["detail"] == detail


@pytest.mark.parametrize("status, code", [(401, "pos_token_rejected"), (403, "pos_scope_missing"),
                                          (429, "pos_unavailable"), (500, "pos_unavailable")])
def test_読みの断りはHTTPの数だけで決める(capsys, env, status, code):
    setup_store(capsys, env)
    env["status"][:] = [200, status]           # 鍵は取れて、商品の読みで断られる
    rc_, out, err = run(capsys, "shop", "items", STORE, "--json")
    assert rc_ == 2 and json.loads(out)["code"] == code and "error-message-marker" not in out + err


# ------------------------------------------------------------------ 下書き・読み直し・外す

def test_出す品の新着から下書きを作り承認で読み直す(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "pos_publish_empty"
    publish(capsys, "--category", "10")
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["products"].append(product(7))
    env["stock"]["7"] = "3"
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    [path] = sorted(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    q = queuefile.parse(str(path))
    assert q.front_matter["from_store"] == f"store/{STORE}/item/7" and str(q.front_matter["from_store_price"]) == "1200"
    assert URL in path.read_text()
    from thth.shop import recheck
    assert recheck.check("x.md", dict(q.front_matter)) == (None, None)
    env["products"][-1]["price"] = "1500"
    reason, _ = recheck.check("x.md", dict(q.front_matter))
    assert reason and "store_item_changed" in reason and "price 1200 -> 1500" in reason


def test_removeは鍵と写しと台帳を消しスマレジの側の鍵を案内する(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert [t["category"] for t in value["targets"]] == ["token", "state", "ledger"]
    _, out, _ = run(capsys, "shop", "remove", STORE, "--by", "m")
    assert "マイページ" in out
    rc_, done, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0 and done["deleted"] == ["token", "state", "ledger"]


def test_柵_鍵は出力にもファイルにも出ず打つのは表の道だけ(capsys, env, isolated_account_factory):
    isolated_account_factory()
    texts = [setup_store(capsys, env)]
    texts.append(publish(capsys, "--category", "10"))
    for argv in (("whoami",), ("whoami", "--json"), ("doctor",), ("doctor", "--json"), ("items",),
                 ("items", "--candidates"), ("diff",), ("diff", "--accept")):
        rc_, out, err = run(capsys, "shop", argv[0], STORE, *argv[1:])
        assert rc_ == 0, err
        texts.append(out + err)
    env["products"].append(product(8))
    env["stock"]["8"] = "1"
    rc_, out, err = run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")
    assert rc_ == 0, err
    texts.append(out + err)
    joined = "\n".join(texts)
    secrets_ = [env["client_id"], env["client_secret"], env["access"]]
    for marker in secrets_ + ["cost-marker", "説明の本文", "address-marker"]:
        assert marker not in joined, marker
    token_file = pathlib.Path(ledger.load(STORE)["token"])
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if path.is_file() and path != token_file:
            raw = kept_bytes(path)
            for marker in secrets_ + ["cost-marker", "address-marker"]:
                assert marker.encode() not in raw, f"{path} に {marker}"
    posts = [c for c in env["calls"] if c["method"] == "POST"]
    assert posts and {urllib.parse.urlsplit(c["url"]).netloc for c in posts} == {wire.SMAREGI_ID_HOST}
    gets = [c for c in env["calls"] if c["method"] == "GET"]
    assert {urllib.parse.urlsplit(c["url"]).netloc for c in gets} == {wire.SMAREGI_API_HOST}
    assert {c["method"] for c in env["calls"]} == {"GET", "POST"}
    assert not any(urllib.parse.urlsplit(URL).netloc in c["url"] for c in env["calls"])
