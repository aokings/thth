"""Instagram の商品タグの最小の口（`thth/meta_shop.py`）。

見るのは:
  - `thth shop eligibility|catalogs|products|tags|tag` が偽の Graph で通る（path・欄の名は公式の頁 2026-11-09 の原文のとおり）。
  - 書く口（tag）は二段確認・production・digest。一段目は書かない。
  - 答えは数と id と商品名まで。runs には商品・投稿の id を残さない。
  - 資格が false のときの断りは、なぜ・通す道・より良くする一手を出す。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets

import pytest

from thth import appreview, meta_shop
from thth import permission_watch as pw
from thth import refusal_catalog
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import fake_graph
from tests.test_m4_meta_writes import found, main, nothing_kept, two_steps, writes

V = "/" + fb.GRAPH_VERSION
PAGE, IG_ID = "61596075596464", "17841437318552220"
FB_ACCOUNT, IG_ACCOUNT = "m6s-fb", "m6s-ig"
MEDIA, CATALOG, PRODUCT, OTHER = "17895695668004550", "960179311066902", "3231775643511089", "5209223099160494"


@pytest.fixture
def shop(isolated_account_factory, tmp_path, monkeypatch):
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=4242\nMETA_APP_SECRET=" + secrets.token_urlsafe(32) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    for name, extra in ((FB_ACCOUNT, {"media": "facebook", "page_id": PAGE, "handle": PAGE, "user_id": PAGE}),
                        (IG_ACCOUNT, {"media": "instagram", "ig_user_id": IG_ID, "page_id": PAGE, "handle": IG_ID,
                                      "user_id": IG_ID})):
        token = tmp_path / f"{name}.token"
        token.write_text(json.dumps({"access_token": "tok-" + secrets.token_hex(12), "user_id": extra["user_id"]}))
        token.chmod(0o600)
        isolated_account_factory(name, token=str(token), production=True, **extra)
    state = {"eligible": True, "write": (200, {"success": True})}
    table = {
        ("GET", V + f"/{IG_ID}"): lambda q, f, r: (200, {"shopping_product_tag_eligibility": state["eligible"],
                                                         "id": IG_ID}),
        ("GET", V + f"/{IG_ID}/available_catalogs"): (200, {"available_catalogs": {"data": [
            {"catalog_id": CATALOG, "catalog_name": "名前の目印", "shop_name": "店の目印", "product_count": 11}]},
            "id": IG_ID}),
        ("GET", V + f"/{IG_ID}/catalog_product_search"): (200, {"data": [
            {"product_id": int(PRODUCT), "product_name": "商品の目印", "review_status": "approved", "merchant_id": 1,
             "image_url": "https://example.invalid/x.jpg"},
            {"product_id": int(OTHER), "product_name": "商品2", "review_status": "pending"}]}),
        ("GET", V + f"/{MEDIA}/product_tags"): (200, {"data": [
            {"product_id": int(PRODUCT), "name": "商品の目印", "review_status": "approved", "x": 0.5, "y": 0.8,
             "price_string": "¥3,500"}]}),
        ("POST", V + f"/{MEDIA}/product_tags"): lambda q, f, r: state["write"],
    }
    with fake_graph(table) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        yield {"tmp": tmp_path, "routes": table, "calls": calls, "state": state}


def get(shop, suffix):
    return [c for c in shop["calls"] if c["method"] == "GET" and c["path"].endswith(suffix)]


def test_資格はtrueとfalseを読む(shop, capsys):
    rc, out, err = main(capsys, "shop", "eligibility", IG_ACCOUNT, "--json")
    assert rc == 0, err
    assert json.loads(out)["eligible"] is True
    assert get(shop, f"/{IG_ID}")[0]["query"]["fields"] == ["shopping_product_tag_eligibility"]
    assert "shop_eligibility" in found(IG_ACCOUNT)


def test_資格がfalseなら前向きな案内を出す(shop, capsys):
    shop["state"]["eligible"] = False
    rc, out, err = main(capsys, "shop", "eligibility", IG_ACCOUNT)
    assert rc == 0, err
    assert "付けられません" in out and "なぜ" in out and "通すには" in out and "より良くするには" in out
    assert "自社のドメイン" in out


def test_カタログと商品は名前までで残さない(shop, capsys):
    rc, out, err = main(capsys, "shop", "catalogs", IG_ACCOUNT, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["catalogs"][0]["catalog_id"] == CATALOG and value["catalogs"][0]["product_count"] == 11
    rc, out, err = main(capsys, "shop", "products", IG_ACCOUNT, "--catalog", CATALOG, "--q", "豆", "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert [r["product_id"] for r in value["products"]] == [PRODUCT, OTHER] and "merchant_id" not in out
    query = get(shop, "/catalog_product_search")[0]["query"]
    assert query["catalog_id"] == [CATALOG] and query["q"] == ["豆"]
    nothing_kept(shop, "商品の目印", "店の目印", PRODUCT, CATALOG)
    assert {"shop_catalogs", "shop_products"} <= set(found(IG_ACCOUNT))


def test_投稿のタグは位置と審査の状態を出す(shop, capsys):
    rc, out, err = main(capsys, "shop", "tags", IG_ACCOUNT, MEDIA, "--json")
    assert rc == 0, err
    tag = json.loads(out)["tags"][0]
    assert tag["product_id"] == PRODUCT and tag["x"] == 0.5 and "price_string" not in tag and "¥" not in out
    assert "shop_tags" in found(IG_ACCOUNT)


def test_タグは二段で付ける_updated_tagsはJSONの配列(shop, capsys):
    (rc, out, err), first = two_steps(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, "--product", OTHER,
                                      "--x", "0.25", "--y", "0.75", "--by", "m")
    assert rc == 0, err
    assert first["already"] is False and first["tags_now"] == 1
    post = writes(shop)[0]
    assert (post["method"], post["path"]) == ("POST", V + f"/{MEDIA}/product_tags")
    assert json.loads(post["form"]["updated_tags"][0]) == [{"product_id": OTHER, "x": 0.25, "y": 0.75}]
    assert "shop_tag" in found(IG_ACCOUNT)


def test_動画は位置なしで付ける(shop, capsys):
    (rc, out, err), _ = two_steps(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, "--product", PRODUCT, "--by", "m")
    assert rc == 0, err
    assert json.loads(writes(shop)[0]["form"]["updated_tags"][0]) == [{"product_id": PRODUCT}]


def test_一段目とdigestの違いでは書かない(shop, capsys):
    rc, out, err = main(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, "--product", OTHER, "--by", "m", "--json")
    assert rc == 1 and json.loads(out)["stage"] == 1 and writes(shop) == []
    rc, out, err = main(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, "--product", OTHER, "--by", "m",
                        "--confirm", "000000000000", "--json")
    assert rc == 1 and "meta_write_digest_mismatch" in out and writes(shop) == []


def test_台帳にproductionが無ければ二段目でも書かない(shop, capsys, isolated_account_factory):
    isolated_account_factory("m6s-noprod", token=str(shop["tmp"] / f"{IG_ACCOUNT}.token"), production=False,
                             media="instagram", ig_user_id=IG_ID, page_id=PAGE, handle=IG_ID, user_id=IG_ID)
    (rc, out, err), _ = two_steps(capsys, "shop", "tag", "m6s-noprod", MEDIA, "--product", OTHER, "--by", "m")
    assert rc == 1 and "production_disabled" in out and writes(shop) == []


def test_書く口はby無しでは断る(shop, capsys):
    rc, out, err = main(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, "--product", OTHER)
    assert rc == 1 and "by_required" in err and writes(shop) == []


def test_結果の分からない書き込みは済んだと言わない(shop, capsys):
    shop["state"]["write"] = (503, {"error": {"message": "temporarily unavailable", "code": 2}})
    (rc, out, err), _ = two_steps(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, "--product", OTHER, "--by", "m")
    assert rc == 1 and "meta_write_unknown" in out and len(writes(shop)) == 1


@pytest.mark.parametrize("argv", [("--product", "abc"), ("--product", PRODUCT, "--x", "0.5"),
                                  ("--product", PRODUCT, "--x", "1.5", "--y", "0.5")])
def test_引数が違えば網に出る前に断る(shop, capsys, argv):
    rc, out, err = main(capsys, "shop", "tag", IG_ACCOUNT, MEDIA, *argv, "--by", "m")
    assert rc == 2 and "shop_args_invalid" in err and shop["calls"] == []


def test_商品の検索はカタログが数字でなければ断る(shop, capsys):
    rc, out, err = main(capsys, "shop", "products", IG_ACCOUNT, "--catalog", "x1")
    assert rc == 2 and "shop_args_invalid" in err and shop["calls"] == []


def test_商品タグはIG口座だけ(shop, capsys):
    rc, out, err = main(capsys, "shop", "eligibility", FB_ACCOUNT)
    assert rc == 2 and "meta_write_unsupported" in err and shop["calls"] == []


# ================================================================== 柵

def test_権限は台本の区間と記録の種類を持ち_catalog_managementは重ねない():
    rows = {row["name"]: row for row in appreview.items()}
    name = "instagram_shopping_tag_products"
    assert name in pw.PERMISSIONS and name not in appreview.PLANNED
    assert rows[name]["segments"] == ["ig-shop"] and rows[name]["doors"]
    assert pw.PERMISSIONS[name]["kind"] == pw.WRITE and pw.PERMISSIONS[name]["evidence"]
    assert "ig-shop" not in rows["catalog_management"]["segments"]
    assert "shop_catalogs" in pw.PERMISSIONS["catalog_management"]["also"]["instagram"]
    assert set(meta_shop.RUN_ACTIONS) <= set(pw.RUN_ACTIONS)


def test_書く口を呼ぶのはmeta_shopだけ():
    root = pathlib.Path(__file__).resolve().parent.parent / "thth"
    doors = re.compile(r"\.shop_tag\(")
    offenders = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                       if doors.search(p.read_text(encoding="utf-8")) and p.name != "meta_shop.py")
    assert offenders == []


def test_符丁は目録にあり3つがそろう():
    entry = refusal_catalog.CATALOG["shop_args_invalid"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]
