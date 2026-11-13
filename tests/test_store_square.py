"""Square を読む口（`thth/shop/square.py`・`thth shop …` の kind square・計画 実店舗 段 5）。

柵:
  - 打つのは GET だけ・道は店舗の一覧・品の一覧・在庫の数の 3 つ（偽の transport が本物の口と同じ柵 `wire.check_request` を通す。
    表そのものの柵は `tests/test_store_pos_wire.py`）。
  - **レジの品は既定で材料にしない**: 出してよい品の表（台帳の `publish`）が空なら、どの品も `{"id", "visible": False}`。
    draft は網に出る前に `pos_publish_empty` で断る。出さない品は名前も在庫も写しに残らず、在庫の数も読まない。
  - 出してよい品の表を変えたら写しを捨てる（前からある品を「新しく入った」と取り違えない）。
  - 営業の時間はレジの値を見せ、写しと比べて変わったら知らせるだけ（下書きは作らない・台帳が正本）。
  - 鍵（アクセストークン）は出力にもファイル（鍵のファイルのほか）にも出ない。鍵の広さと GET だけのことを token set・
    whoami・doctor が言う。
  - remove と leave で鍵・写し・台帳が消える。
**応答は公式の頁の欄の名前から作った偽**（本物の Square の店では 1 度も動かしていない・`pos.UNVERIFIED`）。
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
from thth.shop import ACTION_PREFIX, ledger, pos, square, wire
from thth.shop import cli as store_cli

STORE = "nigami-square"
URL = "https://nigami.example.invalid"
LOC = "L8ZQK0X5Y2W1"
OTHER_LOC = "LOTHER000001"
MERCHANT = "MLR6Y5ZKT6N8E"


def variation(i, n=0, *, price=1200, track=True, sold_out=None, override_price=None, currency="JPY", **extra):
    overrides = []
    if sold_out is not None or override_price is not None:
        o = {"location_id": LOC}
        if sold_out is not None:
            o["sold_out"] = sold_out
        if override_price is not None:
            o["price_money"] = {"amount": override_price, "currency": currency}
        overrides.append(o)
    data = {"item_id": f"ITEM{i:04d}", "name": f"種類 {n}", "sku": f"sku-marker-{i}{n}", "pricing_type": "FIXED_PRICING",
            "price_money": {"amount": price, "currency": currency}, "track_inventory": track,
            "location_overrides": overrides, "upc": f"upc-marker-{i}{n}"}
    data.update(extra)
    return {"type": "ITEM_VARIATION", "id": f"VAR{i:04d}{n}", "present_at_all_locations": True,
            "item_variation_data": data}


def item_obj(i, *, name=None, variations=None, categories=("CATBEANS",), archived=False, image=True, **extra):
    node = {"type": "ITEM", "id": f"ITEM{i:04d}", "updated_at": "2026-11-01T00:00:00Z", "version": 1,
            "is_deleted": False, "present_at_all_locations": True,
            "item_data": {"name": name or f"豆 {i}", "description": f"説明の本文 {i}",
                          "variations": variations if variations is not None else [variation(i)],
                          "categories": [{"id": c} for c in categories], "is_archived": archived,
                          "image_ids": [f"IMG{i:04d}"] if image else [], "tax_ids": ["TAXMARKER"]}}
    node.update(extra)
    return node


def image_obj(i):
    return {"type": "IMAGE", "id": f"IMG{i:04d}", "image_data": {"url": f"https://items.example.invalid/{i}.jpg",
                                                                  "caption": "caption-marker"}}


def location(**extra):
    node = {"id": LOC, "name": "にがみ 本店", "status": "ACTIVE", "merchant_id": MERCHANT, "timezone": "Asia/Tokyo",
            "currency": "JPY", "address": {"address_line_1": "address-marker 1-2-3"}, "phone_number": "phone-marker",
            "business_email": "email-marker@example.invalid", "coordinates": {"latitude": 35.0, "longitude": 139.0},
            "business_hours": {"periods": [{"day_of_week": "TUE", "start_local_time": "9:00:00",
                                            "end_local_time": "18:00:00"},
                                           {"day_of_week": "MON", "start_local_time": "10:00:00",
                                            "end_local_time": "17:30:00"}]}}
    node.update(extra)
    return node


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    state = {"objects": [item_obj(1), item_obj(2), item_obj(3, categories=("CATBAGS",)), image_obj(1), image_obj(2),
                         {"type": "CATEGORY", "id": "CATBEANS", "category_data": {"name": "豆"}},
                         {"type": "CATEGORY", "id": "CATBAGS", "category_data": {"name": "袋"}}],
             "locations": [location(), {"id": OTHER_LOC, "name": "にがみ 駅前", "status": "ACTIVE",
                                        "merchant_id": MERCHANT}],
             "counts": {"VAR00010": "5", "VAR00020": "0"}, "calls": [], "tmp": tmp_path,
             "token": "EAAA" + secrets.token_hex(24), "status": [], "page_size": 100}

    def transport(method, url, headers, body=None):
        wire.check_request(method, url, body)          # 本物の口と同じ柵を通す
        parts = urllib.parse.urlsplit(url)
        state["calls"].append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        if state["status"]:
            status = state["status"].pop(0)
            if status != 200:
                return status, json.dumps({"errors": [{"detail": "error-message-marker"}]}).encode()
        if headers.get("Authorization") != f"Bearer {state['token']}":
            return 401, json.dumps({"errors": [{"category": "AUTHENTICATION_ERROR", "detail": "error-message-marker"}]}).encode()
        assert headers.get("Square-Version") == wire.SQUARE_VERSION
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/v2/locations":
            return 200, json.dumps({"locations": state["locations"]}).encode()
        if parts.path == "/v2/catalog/list":
            wanted = set(query["types"].split(","))
            objs = [o for o in state["objects"] if o["type"] in wanted]
            start = int(query.get("cursor", "0").replace("C", "") or 0)
            page = objs[start:start + state["page_size"]]
            out = {"objects": page}
            if start + state["page_size"] < len(objs):
                out["cursor"] = f"C{start + state['page_size']}"
            return 200, json.dumps(out).encode()
        var = parts.path.rsplit("/", 1)[1]
        assert query == {"location_ids": LOC}
        found = state["counts"].get(var)
        counts = [] if found is None else [
            {"catalog_object_id": var, "catalog_object_type": "ITEM_VARIATION", "state": "IN_STOCK",
             "location_id": LOC, "quantity": found, "calculated_at": "2026-11-13T00:00:00Z"},
            {"catalog_object_id": var, "state": "IN_STOCK", "location_id": OTHER_LOC, "quantity": "99"},
            {"catalog_object_id": var, "state": "SOLD", "location_id": LOC, "quantity": "40"}]
        return 200, json.dumps({"counts": counts}).encode()

    lines = []
    state["lines"] = lines
    monkeypatch.setattr(store_cli, "TRANSPORT", transport)
    monkeypatch.setattr(store_cli, "READ_SECRET", lambda: lines.pop(0) + "\n")
    monkeypatch.setattr(square, "SLEEP", lambda seconds: None)
    return state


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def add(capsys, *, location_id=LOC):
    return run(capsys, "shop", "add", STORE, "--kind", "square", "--project", "nigamilab", "--url", URL,
               "--location", location_id, "--by", "m")


def setup_store(capsys, env, *extra):
    rc_, out, err = add(capsys)
    assert rc_ == 0, err
    env["lines"][:] = [env["token"]]
    rc2, out2, err2 = run(capsys, "shop", "token", "set", STORE, "--by", "m", *extra)
    assert rc2 == 0, err2
    return out + err + out2 + err2


def publish(capsys, *args):
    rc_, out, err = run(capsys, "shop", "publish", STORE, *args, "--by", "m")
    assert rc_ == 0, err
    return out


# ------------------------------------------------------------------ 台帳と鍵

def test_台帳は店舗と空の出す品の表を持ち鍵の広さとGETだけを案内する(capsys, env):
    rc_, out, err = add(capsys)
    assert rc_ == 0, err
    data = json.loads(pathlib.Path(ledger.path_for(STORE)).read_text())
    assert data == {"store": STORE, "kind": "square", "project": "nigamilab", "token": ledger.default_token(STORE),
                    "public_url": URL, "location": LOC, "publish": {"items": [], "categories": []}}
    assert "全部の権限" in out and "GET だけ" in out and "ITEMS_READ" in out and "1 つもありません" in out
    assert "Square が提供するものではありません" in out


@pytest.mark.parametrize("argv, detail", [
    (("--location", "l-bad"), "location"), (("--location", ""), "location"), ((), "location"),
    (("--location", LOC, "--contract", "abc"), "contract"),
])
def test_店舗のidが形に合わなければ前向きに断る(capsys, env, argv, detail):
    rc_, out, err = run(capsys, "shop", "add", STORE, "--kind", "square", "--project", "nigamilab", "--url", URL,
                        *argv, "--by", "m")
    assert rc_ == 2 and "pos_args_invalid" in out + err and f"（{detail}）" in out + err
    assert not pathlib.Path(ledger.path_for(STORE)).exists()


def test_ほかの店の種類にlocationとcontractは付けられない(capsys, env):
    for extra in (("--location", LOC), ("--contract", "c1")):
        rc_, out, err = run(capsys, "shop", "add", "x-base", "--kind", "base", "--project", "nigamilab", *extra,
                            "--by", "m")
        assert rc_ == 2 and "pos_args_invalid" in out + err


def test_鍵は店舗が見えたときだけ保存し鍵の広さとまだ確かめていない所を言う(capsys, env):
    text = setup_store(capsys, env)
    cfg = ledger.load(STORE)
    saved = json.loads(pathlib.Path(cfg["token"]).read_text())
    assert saved["auth"] == "bearer" and saved["access_token"] == env["token"] and saved["shop_id"] == MERCHANT
    assert saved["no_expiry"] is True
    assert stat.S_IMODE(pathlib.Path(cfg["token"]).stat().st_mode) == 0o600
    assert env["token"] not in text
    assert "全部の権限" in text and "OAuth" in text and "まだ確かめていない所" in text
    assert [urllib.parse.urlsplit(c["url"]).path for c in env["calls"]] == ["/v2/locations"]


def test_鍵で見えない店舗なら見える店舗を並べて断る(capsys, env):
    rc_, _out, err = add(capsys, location_id="LNOTHERE0001")
    assert rc_ == 0, err
    env["lines"][:] = [env["token"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "pos_location_unknown" in out + err
    assert LOC in err and "にがみ 駅前" in err and "address-marker" not in out + err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()
    from thth.shop import kinds
    with pytest.raises(wire.StoreRefused) as exc:
        kinds.client(ledger.load(STORE), {"access_token": env["token"]}, store_cli.TRANSPORT).user()
    assert exc.value.code == "pos_location_unknown"
    assert {x["id"] for x in exc.value.extra["locations"]} == {LOC, OTHER_LOC}


def test_違う鍵は保存せず応答の文を入れない(capsys, env):
    assert add(capsys)[0] == 0
    env["lines"][:] = ["EAAAwrong"]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")
    assert rc_ == 2 and "pos_token_rejected" in out + err and "HTTP 401" in out + err
    assert "error-message-marker" not in out + err and "EAAAwrong" not in out + err
    assert not pathlib.Path(ledger.load(STORE)["token"]).exists()


def test_OAuthの鍵は期限の日を付けられmorningが14日前から知らせる(capsys, env):
    soon = (jst.now_jst().date() + datetime.timedelta(days=5)).isoformat()
    text = setup_store(capsys, env, "--expires", soon)
    assert "OAuth の鍵は 30 日で切れます" in text
    from thth import morning
    now = datetime.datetime.combine(jst.now_jst().date(), datetime.time(10), tzinfo=jst.JST)
    [line] = morning._store_key_lines({"nigamilab"}, now)
    assert "Square のアクセストークン" in line and "あと 5 日" in line


def test_morningは期限の日の無い個人の鍵に何も言わない(capsys, env):
    from thth import morning
    setup_store(capsys, env)
    now = datetime.datetime.combine(jst.now_jst().date(), datetime.time(10), tzinfo=jst.JST)
    assert morning._store_key_lines({"nigamilab"}, now) == []


# ------------------------------------------------------------------ 読む（既定は材料にしない）

def test_柵_出す品の表が空ならどの品も名前を持たず在庫も読まない(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True
    assert value["items"] == [{"id": "ITEM0001", "visible": False}, {"id": "ITEM0002", "visible": False},
                              {"id": "ITEM0003", "visible": False}]
    assert not any("/v2/inventory/" in c["url"] for c in env["calls"])
    _, out, _ = run(capsys, "shop", "items", STORE)
    assert "（出さない品）" in out and "豆 1" not in out


def test_出す品を選ぶ一覧は名前と部門を見せるが残さない(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE, "--candidates")
    assert rc_ == 0
    rows = {r["id"]: r for r in value["candidates"]}
    assert rows["ITEM0001"]["name"] == "豆 1" and rows["ITEM0001"]["categories"] == [{"id": "CATBEANS", "name": "豆"}]
    assert rows["ITEM0001"]["published"] is False
    assert not any("/v2/inventory/" in c["url"] for c in env["calls"])
    assert ledger.read_snapshot(STORE) is None
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if path.is_file():
            assert "豆 1".encode() not in kept_bytes(path), path


def test_出す品と部門だけ公開の行になり在庫と値段を店舗で読む(capsys, env):
    env["objects"] += [item_obj(4, variations=[variation(4, 0, price=900, track=False), variation(4, 1, price=1500)]),
                       item_obj(5, variations=[variation(5, 0, override_price=700, sold_out=True)]),
                       item_obj(6, archived=True), item_obj(7, present_at_all_locations=False,
                                                            present_at_location_ids=[OTHER_LOC])]
    setup_store(capsys, env)
    publish(capsys, "--item", "ITEM0001")
    publish(capsys, "--category", "CATBEANS")
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0
    rows = {r["id"]: r for r in value["items"]}
    assert rows["ITEM0001"] == {"id": "ITEM0001", "name": "豆 1", "price": 1200, "stock": 5, "visible": True,
                                "sales_status": "on_sale", "image_url": "https://items.example.invalid/1.jpg",
                                "item_url": None, "modified_at": "2026-11-01T00:00:00Z",
                                "variant_prices": {"VAR00010": 1200}}
    assert rows["ITEM0002"]["stock"] == 0
    assert rows["ITEM0003"] == {"id": "ITEM0003", "visible": False}              # 袋の部門は出さない
    assert rows["ITEM0004"]["stock"] is None and rows["ITEM0004"]["price"] == 900   # 数えない種類がある
    assert rows["ITEM0005"]["stock"] == 0 and rows["ITEM0005"]["price"] == 700      # 売り切れの印・店舗の値段
    assert rows["ITEM0006"] == {"id": "ITEM0006", "visible": False}               # アーカイブ
    assert rows["ITEM0007"] == {"id": "ITEM0007", "visible": False}               # この店舗に置いていない
    inventory = sorted(urllib.parse.urlsplit(c["url"]).path for c in env["calls"] if "/v2/inventory/" in c["url"])
    # 在庫を読むのは出す品の、数える種類だけ（売り切れの印の種類と数えない品は打たない）。
    assert inventory == ["/v2/inventory/VAR00010", "/v2/inventory/VAR00020"]
    joined = json.dumps(value, ensure_ascii=False)
    for marker in ("説明の本文", "sku-marker", "upc-marker", "TAXMARKER", "caption-marker", "address-marker",
                   "phone-marker", "email-marker"):
        assert marker not in joined, marker


def test_円でない値段は読まない(capsys, env):
    env["objects"] = [item_obj(1, variations=[variation(1, currency="USD")])]
    setup_store(capsys, env)
    publish(capsys, "--item", "ITEM0001")
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "pos_currency_unsupported" and value["detail"] == "USD"


def test_在庫を読む種類が上限を超えたら読み切れないと断る(capsys, env, monkeypatch):
    monkeypatch.setattr(square, "INVENTORY_MAX", 1)
    setup_store(capsys, env)
    publish(capsys, "--category", "CATBEANS")
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 2 and value["code"] == "pos_read_limit"
    assert ledger.read_snapshot(STORE) is None


def test_品の一覧の頁を進め上限の頁で終わらなければ読み切れない(capsys, env, monkeypatch):
    env["page_size"] = 2
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True and value["pages"] == 3
    monkeypatch.setattr(square, "MAX_PAGES", 2)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 2 and value["code"] == "store_read_incomplete"


@pytest.mark.parametrize("status, code", [(401, "pos_token_rejected"), (403, "pos_scope_missing"),
                                          (429, "pos_unavailable"), (503, "pos_unavailable"),
                                          (400, "store_request_rejected"), (302, "store_request_rejected")])
def test_断りはHTTPの数だけで決め応答の文は入れない(capsys, env, status, code):
    setup_store(capsys, env)
    env["status"][:] = [status]
    rc_, out, err = run(capsys, "shop", "items", STORE, "--json")
    assert rc_ == 2 and json.loads(out)["code"] == code
    assert "error-message-marker" not in out + err


# ------------------------------------------------------------------ 出す品の表

def test_出す品の表を変えたら写しを捨て記録に残す(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    assert ledger.read_snapshot(STORE) is not None
    out = publish(capsys, "--item", "ITEM0001")
    assert "写しを捨てました" in out and ledger.read_snapshot(STORE) is None
    assert ledger.load(STORE)["publish"] == {"items": ["ITEM0001"], "categories": []}
    assert "もう表にあります" in publish(capsys, "--item", "ITEM0001")
    publish(capsys, "--item", "ITEM0001", "--off")
    assert ledger.load(STORE)["publish"] == {"items": [], "categories": []}
    rows, _ = admin_log.read(account=STORE, event=ACTION_PREFIX + "publish_set")
    assert len(rows) == 2 and rows[0]["diff"]["items"] == [[], ["ITEM0001"]]
    assert json.loads(pathlib.Path(ledger.path_for(STORE)).read_text())["token"] == ledger.default_token(STORE)


def test_出す品の表の引数とネットショップでは前向きに断る(capsys, env):
    setup_store(capsys, env)
    rc_, out, err = run(capsys, "shop", "publish", STORE, "--by", "m")
    assert rc_ == 2 and "（item_or_category）" in out + err
    rc_, out, err = run(capsys, "shop", "publish", STORE, "--item", "A", "--category", "B", "--by", "m")
    assert rc_ == 2 and "（item_or_category）" in out + err
    ledger.write_new("x-base", ledger.build("x-base", kind="base", project="nigamilab"))
    rc_, out, err = run(capsys, "shop", "publish", "x-base", "--item", "1", "--by", "m")
    assert rc_ == 2 and "（publish_kind）" in out + err


def test_台帳の出す品の表は形を検査する(capsys, env):
    setup_store(capsys, env)
    path = pathlib.Path(ledger.path_for(STORE))
    good = path.read_text()
    for bad in ({"publish": {"items": ["a-b"], "categories": []}}, {"publish": {"items": []}},
                {"publish": {"items": ["A", "A"], "categories": []}}, {"publish": []},
                {"location": "lower"}, {"contract": "x"}):
        data = json.loads(good)
        data.update(bad)
        path.write_text(json.dumps(data))
        rc_, value, _ = jrun(capsys, "shop", "items", STORE)
        assert rc_ == 2 and value["code"] == "store_ledger_invalid", bad
    path.write_text(good)


# ------------------------------------------------------------------ 違い・営業の時間・下書き・読み直し

def test_whoamiとdoctorは営業の時間と鍵の広さを言う(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "whoami", STORE)
    assert rc_ == 0 and value["shop_id"] == MERCHANT and value["shop_url"] == URL and value["able_to_business"] is True
    assert value["hours"] == [{"day": "MON", "start": "10:00", "end": "17:30"},
                              {"day": "TUE", "start": "09:00", "end": "18:00"}]
    assert "address-marker" not in json.dumps(value, ensure_ascii=False)
    _, out, _ = run(capsys, "shop", "whoami", STORE)
    assert "月 10:00〜17:30・火 09:00〜18:00" in out and "台帳の営業の時間が正本" in out
    rc_, value, _ = jrun(capsys, "shop", "doctor", STORE)
    assert rc_ == 0 and all(c["ok"] for c in value["checks"]) and value["publish"] == {"items": 0, "categories": 0}
    assert any("GET だけ" in n for n in value["notes"]) and any("まだ確かめていない" in n for n in value["notes"])
    _, out, _ = run(capsys, "shop", "doctor", STORE)
    assert "全部の権限" in out and "ITEMS_READ" in out and env["token"] not in out


def test_営業の時間が変わったら知らせるだけで下書きは作らない(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    assert ledger.read_snapshot(STORE)["hours"][0] == {"day": "MON", "start": "10:00", "end": "17:30"}
    env["locations"][0]["business_hours"]["periods"][1]["end_local_time"] = "16:00:00"
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 0 and value["changes"] == []
    [change] = value["store_changes"]
    assert change["kind"] == "hours_changed" and change["after"][0]["end"] == "16:00"
    _, out, _ = run(capsys, "shop", "diff", STORE)
    assert "営業の時間がレジで変わりました" in out and "月 10:00〜17:30・火 09:00〜18:00 → 月 10:00〜16:00・火 09:00〜18:00" in out


def test_柵_出す品が無ければdraftは網に出る前に断る(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    before = len(env["calls"])
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "pos_publish_empty"
    assert len(env["calls"]) == before
    entry = rc.CATALOG["pos_publish_empty"]
    assert "既定" in entry["why"] and "--candidates" in entry["fix"]["text"] and entry["better"]


def test_店舗が無効ならdraftは下書きを作らずレジの店にも合う言い方で断る(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    publish(capsys, "--category", "CATBEANS")
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["objects"].append(item_obj(8))
    env["counts"]["VAR00080"] = "2"
    env["locations"][0]["status"] = "INACTIVE"
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_not_open"
    assert not list(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    entry = rc.CATALOG["store_not_open"]
    assert "Square" in entry["why"] and "店舗" in entry["why"] and "Square" in entry["fix"]["text"]
    assert "able_to_business" not in entry["why"]


def test_出す品の新着と再入荷と値下げから下書きを作り承認で読み直す(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    publish(capsys, "--category", "CATBEANS")
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["counts"]["VAR00020"] = "3"                                        # 再入荷
    env["objects"].append(item_obj(8))                                      # 新着（豆の部門）
    env["counts"]["VAR00080"] = "2"
    env["objects"][0]["item_data"]["variations"][0]["item_variation_data"]["price_money"]["amount"] = 1000  # 値下げ
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert {(c["kind"], c["id"]) for c in value["changes"]} == {("new", "ITEM0008"), ("restock", "ITEM0002"),
                                                               ("price_down", "ITEM0001")}
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    [path] = sorted(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    q = queuefile.parse(str(path))
    assert q.front_matter["status"] == "draft" and q.front_matter["from_store"] == f"store/{STORE}/item/ITEM0008"
    text = path.read_text()
    assert URL in text and "豆 8 新しく入りました" in text and "説明の本文" not in text
    assert any("レジの品には商品頁が無い" in n for n in value["notes"])
    from thth.shop import recheck
    assert recheck.check("x.md", dict(q.front_matter)) == (None, None)
    env["counts"]["VAR00080"] = "0"
    reason, _ = recheck.check("x.md", dict(q.front_matter))
    assert reason and "store_item_changed" in reason and "not_buyable" in reason
    # 出す品の表から外したら、承認の読み直しも通らない（非公開の扱い）。
    env["counts"]["VAR00080"] = "2"
    publish(capsys, "--category", "CATBEANS", "--off")
    reason, _ = recheck.check("x.md", dict(q.front_matter))
    assert reason and "unpublished" in reason


# ------------------------------------------------------------------ 外す・退出

def test_removeは鍵と写しと台帳を消しSquareの側の鍵を案内する(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 1 and [t["category"] for t in value["targets"]] == ["token", "state", "ledger"]
    _, out, _ = run(capsys, "shop", "remove", STORE, "--by", "m")
    assert "Developer Console" in out
    rc_, done, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0 and done["deleted"] == ["token", "state", "ledger"]
    assert not pathlib.Path(ledger.state_dir_for(STORE)).exists()


# ------------------------------------------------------------------ 鍵を出さない・打つのは GET だけ

def test_柵_鍵は出力にもファイルにも出ない(capsys, env, isolated_account_factory):
    isolated_account_factory()
    texts = [setup_store(capsys, env)]
    texts.append(publish(capsys, "--category", "CATBEANS"))
    for argv in (("whoami",), ("whoami", "--json"), ("doctor",), ("items",), ("items", "--json"),
                 ("items", "--candidates"), ("diff",), ("diff", "--json")):
        _, out, err = run(capsys, "shop", argv[0], STORE, *argv[1:])
        texts.append(out + err)
    env["objects"].append(item_obj(9))
    env["counts"]["VAR00090"] = "4"
    _, out, err = run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")
    texts.append(out + err)
    joined = "\n".join(texts)
    markers = [env["token"], "説明の本文", "sku-marker", "upc-marker", "address-marker", "phone-marker",
               "error-message-marker"]
    for marker in markers:
        assert marker not in joined, marker
    token_file = pathlib.Path(ledger.load(STORE)["token"])
    found_token = False
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if not path.is_file():
            continue
        raw = kept_bytes(path)
        if path == token_file:
            found_token = env["token"].encode() in raw
            continue
        for marker in markers:
            assert marker.encode() not in raw, f"{path} に {marker}"
    assert found_token


def test_柵_命令を全部打っても打つのはGETで表の3つの道だけ(capsys, env, isolated_account_factory):
    isolated_account_factory()
    setup_store(capsys, env)
    publish(capsys, "--category", "CATBEANS")
    for argv in (("whoami",), ("doctor",), ("items",), ("items", "--candidates"), ("diff",), ("diff", "--accept")):
        assert run(capsys, "shop", argv[0], STORE, *argv[1:])[0] == 0
    env["objects"].append(item_obj(9))
    env["counts"]["VAR00090"] = "4"
    assert run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")[0] == 0
    from thth.shop import recheck
    assert recheck.check("x.md", {"from_store": f"store/{STORE}/item/ITEM0001"}) == (None, None)
    assert {c["method"] for c in env["calls"]} == {"GET"}
    assert {c["body"] for c in env["calls"]} == {None}
    assert {urllib.parse.urlsplit(c["url"]).netloc for c in env["calls"]} == {wire.SQUARE_HOST}
    paths = {urllib.parse.urlsplit(c["url"]).path for c in env["calls"]}
    assert {p for p in paths if not p.startswith("/v2/inventory/")} == {"/v2/locations", "/v2/catalog/list"}
    assert not any(urllib.parse.urlsplit(URL).netloc in c["url"] for c in env["calls"])


def test_目録のレジの符丁は3つがそろい前向き():
    for code in sorted(c for c in wire.REASONS if c.startswith("pos_")):
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code
    assert "まだ本物で確かめていません" in rc.CATALOG["pos_token_rejected"]["why"]
    assert "OAuth" in rc.CATALOG["pos_scope_missing"]["why"]
    assert pos.KEY_NOTES["square"][0].count("GET だけ") == 1
