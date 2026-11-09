"""ブランドコンテンツとクリエイターマーケットプレイスの最小の口（`thth/meta_brand.py`）。

見るのは:
  - `thth brand creators|promote|ads-permissions|content`・`thth creators search` が偽の Graph で 1 本ずつ通る
    （path・欄の名は公式の頁 2026-11-09 の原文のとおり）。
  - 書く口（approve・remove・--allow/--deny）は二段確認・production・digest。一段目は書かない。
  - 答えは数と id まで。相手の id は runs に残さない。
  - 6 権限は PERMISSIONS と録画の区間にあり、記録の種類を持つ。断りの符丁は目録に 3 つそろう。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import json
import pathlib
import re
import secrets

import pytest

from thth import appreview, cli, meta_brand
from thth import permission_watch as pw
from thth import refusal_catalog
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import fake_graph
from tests.test_m4_meta_writes import found, main, nothing_kept, two_steps, writes

V = "/" + fb.GRAPH_VERSION
PAGE, IG_ID = "61596075596464", "17841437318552220"
FB_ACCOUNT, IG_ACCOUNT = "m6-fb", "m6-ig"
CREATOR, OTHER, BRAND, MEDIA, BUSINESS = "17841400000000001", "17841400000000002", "17841400000000009", \
    "17895695668004550", "650157780891832"
SIX = ("instagram_branded_content_brand", "instagram_branded_content_creator", "instagram_branded_content_ads_brand",
       "facebook_branded_content_ads_brand", "instagram_creator_marketplace_discovery",
       "facebook_creator_marketplace_discovery")


@pytest.fixture
def m6(isolated_account_factory, tmp_path, monkeypatch):
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
    state = {"approved": [CREATOR], "write": (200, {"success": True})}

    def tag_get(query, form, raw):
        asked = query["user_ids"][0].split(",")
        return 200, {"data": [{"id": v} for v in asked if v in state["approved"]]}

    table = {
        ("GET", V + f"/{IG_ID}/branded_content_tag_approval"): tag_get,
        ("POST", V + f"/{IG_ID}/branded_content_tag_approval"): lambda q, f, r: state["write"],
        ("DELETE", V + f"/{IG_ID}/branded_content_tag_approval"): lambda q, f, r: state["write"],
        ("GET", V + f"/{MEDIA}/branded_content_partner_promote"):
            lambda q, f, r: (200, {"approved": ",".join(state["approved"])}),
        ("POST", V + f"/{MEDIA}/branded_content_partner_promote"): lambda q, f, r: state["write"],
        ("GET", V + f"/{IG_ID}/branded_content_ad_permissions"): (200, {"data": [
            {"creator_ig_id": int(CREATOR), "creator_username": "名前の目印", "permission_status": "APPROVED"},
            {"creator_ig_id": int(OTHER), "creator_username": "名前の目印2", "permission_status": "PENDING"}]}),
        ("GET", V + f"/{BUSINESS}/partnership-ads-advertisable-content"): (200, {"data": [
            {"content_id": "111", "platform": "INSTAGRAM", "media_type": "IMAGE", "post_type": "FEED",
             "ad_usage": "NEVER_USED", "caption": "本文の目印"},
            {"content_id": "222", "platform": "FACEBOOK"}], "paging": {"cursors": {"after": "x"}}}),
        ("GET", V + f"/{IG_ID}/creator_marketplace_creators"):
            (200, {"data": [{"id": "9001", "username": "名前の目印"}]}),
        ("GET", V + "/creator_marketplace/creators"):
            (200, {"data": [{"creator_id": "7001", "creator_display_name": "名前の目印"}]}),
    }
    with fake_graph(table) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        yield {"tmp": tmp_path, "routes": table, "calls": calls, "state": state}


def get(m6, suffix):
    return [c for c in m6["calls"] if c["method"] == "GET" and c["path"].endswith(suffix)]


def test_承認クリエイターは尋ねて載っているかを読む(m6, capsys):
    rc, out, err = main(capsys, "brand", "creators", IG_ACCOUNT, "list", CREATOR, OTHER, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["approved"] == [CREATOR] and value["checked"] == 2 and value["storage"] == "display_only"
    assert get(m6, "/branded_content_tag_approval")[0]["query"]["user_ids"] == [f"{CREATOR},{OTHER}"]
    assert "brand_creators" in found(IG_ACCOUNT)


def test_承認クリエイターは二段で足し二段で外す(m6, capsys):
    (rc, out, err), first = two_steps(capsys, "brand", "creators", IG_ACCOUNT, "approve", OTHER, "--by", "m")
    assert rc == 0, err
    assert first["ids"] == [OTHER] and first["already"] == []
    post = writes(m6)[0]
    assert (post["method"], post["path"]) == ("POST", V + f"/{IG_ID}/branded_content_tag_approval")
    assert post["form"]["user_ids"] == [OTHER]
    (rc, out, err), _ = two_steps(capsys, "brand", "creators", IG_ACCOUNT, "remove", CREATOR, "--by", "m")
    assert rc == 0, err
    delete = writes(m6)[-1]
    assert delete["method"] == "DELETE" and delete["query"]["user_ids"] == [CREATOR]
    assert {"brand_creators"} <= set(found(IG_ACCOUNT))


def test_一段目とdigestの違いでは書かない(m6, capsys):
    rc, out, err = main(capsys, "brand", "creators", IG_ACCOUNT, "approve", OTHER, "--by", "m", "--json")
    assert rc == 1 and json.loads(out)["stage"] == 1 and writes(m6) == []
    rc, out, err = main(capsys, "brand", "creators", IG_ACCOUNT, "approve", OTHER, "--by", "m",
                        "--confirm", "000000000000", "--json")
    assert rc == 1 and "meta_write_digest_mismatch" in out and writes(m6) == []


def test_台帳にproductionが無ければ二段目でも書かない(m6, capsys, isolated_account_factory):
    isolated_account_factory("m6-noprod", token=str(m6["tmp"] / f"{IG_ACCOUNT}.token"), production=False,
                             media="instagram", ig_user_id=IG_ID, page_id=PAGE, handle=IG_ID, user_id=IG_ID)
    (rc, out, err), _ = two_steps(capsys, "brand", "creators", "m6-noprod", "approve", OTHER, "--by", "m")
    assert rc == 1 and "production_disabled" in out and writes(m6) == []


def test_書く口はby無しでは断る(m6, capsys):
    rc, out, err = main(capsys, "brand", "creators", IG_ACCOUNT, "approve", OTHER)
    assert rc == 1 and "by_required" in err and writes(m6) == []


def test_結果の分からない書き込みは済んだと言わない(m6, capsys):
    m6["state"]["write"] = (503, {"error": {"message": "temporarily unavailable", "code": 2}})
    (rc, out, err), _ = two_steps(capsys, "brand", "creators", IG_ACCOUNT, "approve", OTHER, "--by", "m")
    assert rc == 1 and "meta_write_unknown" in out and len(writes(m6)) == 1


@pytest.mark.parametrize("argv", [("list",), ("list", "abc"), ("approve", ",".join(str(i) for i in range(1, 102)))])
def test_idの形が違えば網に出る前に断る(m6, capsys, argv):
    rc, out, err = main(capsys, "brand", "creators", IG_ACCOUNT, *argv, "--by", "m")
    assert rc == 2 and "brand_args_invalid" in err and m6["calls"] == []


def test_ブランドコンテンツの書く口はIG口座だけ(m6, capsys):
    rc, out, err = main(capsys, "brand", "creators", FB_ACCOUNT, "list", CREATOR)
    assert rc == 2 and "meta_write_unsupported" in err and m6["calls"] == []


def test_広告に使う許可は読んでから二段で許す(m6, capsys):
    rc, out, err = main(capsys, "brand", "promote", IG_ACCOUNT, MEDIA, "--json")
    assert rc == 0, err
    assert json.loads(out)["approved"] == [CREATOR]
    (rc, out, err), first = two_steps(capsys, "brand", "promote", IG_ACCOUNT, MEDIA, "--allow", "--sponsor", BRAND,
                                      "--by", "m")
    assert rc == 0, err
    post = writes(m6)[0]
    assert post["path"] == V + f"/{MEDIA}/branded_content_partner_promote"
    assert post["form"]["sponsor_id"] == [BRAND] and post["form"]["permission"] == ["true"]
    (rc, out, err), _ = two_steps(capsys, "brand", "promote", IG_ACCOUNT, MEDIA, "--deny", "--sponsor", BRAND,
                                  "--by", "m")
    assert writes(m6)[-1]["form"]["permission"] == ["false"]
    assert {"brand_promote"} <= set(found(IG_ACCOUNT))


def test_許すのにsponsorが無ければ断る(m6, capsys):
    rc, out, err = main(capsys, "brand", "promote", IG_ACCOUNT, MEDIA, "--allow", "--by", "m")
    assert rc == 2 and "brand_args_invalid" in err and writes(m6) == []


def test_口座の段の広告の許可は状態と数だけ出す(m6, capsys):
    rc, out, err = main(capsys, "brand", "ads-permissions", IG_ACCOUNT, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["count"] == 2 and value["by_status"] == {"APPROVED": 1, "PENDING": 1}
    assert "名前の目印" not in out
    assert "brand_ads_permissions" in found(IG_ACCOUNT)


@pytest.mark.parametrize("account,scope", [(FB_ACCOUNT, "fb_page_id"), (IG_ACCOUNT, "ig_user_id")])
def test_提携広告の投稿はbusinessの口でFBもIGも読む(m6, capsys, account, scope):
    rc, out, err = main(capsys, "brand", "content", account, "--business", BUSINESS, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert [row["content_id"] for row in value["content"]] == ["111", "222"] and "本文の目印" not in out
    call = get(m6, "/partnership-ads-advertisable-content")[-1]
    assert call["query"][scope] == [PAGE if scope == "fb_page_id" else IG_ID]
    assert call["path"] == V + f"/{BUSINESS}/partnership-ads-advertisable-content"
    assert "brand_content" in found(account)


def test_businessが無いか数字でなければ断る(m6, capsys):
    with pytest.raises(SystemExit):
        cli.main(["brand", "content", IG_ACCOUNT])
    capsys.readouterr()
    rc, out, err = main(capsys, "brand", "content", IG_ACCOUNT, "--business", "x1")
    assert rc == 2 and "brand_args_invalid" in err


def test_クリエイターの検索はIGとFBで別の口_idだけ出して残さない(m6, capsys):
    rc, out, err = main(capsys, "creators", "search", IG_ACCOUNT, "--country", "jp", "--q", "料理", "--json")
    assert rc == 0, err
    assert json.loads(out)["creators"] == [{"creator_id": "9001"}] and "名前の目印" not in out
    query = get(m6, "/creator_marketplace_creators")[0]["query"]
    assert query["creator_countries"] == ['["JP"]'] and query["query"] == ["料理"] and query["fields"] == ["id"]
    rc, out, err = main(capsys, "creators", "search", FB_ACCOUNT, "--country", "JP", "--json")
    assert rc == 0, err
    assert json.loads(out)["creators"] == [{"creator_id": "7001"}]
    query = get(m6, "/creator_marketplace/creators")[0]["query"]
    assert query["creator_countries"] == ["JP"] and query["fields"] == ["creator_id"]
    nothing_kept(m6, "名前の目印", "9001", "7001", CREATOR)
    assert "creators_search" in found(IG_ACCOUNT) and "creators_search" in found(FB_ACCOUNT)


def test_acting_businessはIGだけ送りFBでは網に出ず断る(m6, capsys):
    rc, out, err = main(capsys, "creators", "search", IG_ACCOUNT, "--acting-business", BUSINESS)
    assert rc == 0, err
    assert get(m6, "/creator_marketplace_creators")[0]["query"]["acting_business_id"] == [BUSINESS]
    before = len(m6["calls"])
    rc, out, err = main(capsys, "creators", "search", FB_ACCOUNT, "--acting-business", BUSINESS)
    assert rc == 1 and len(m6["calls"]) == before


def test_マーケットプレイスの権限系の断りは資格の案内にする(m6, capsys):
    m6["routes"][("GET", V + f"/{IG_ID}/creator_marketplace_creators")] = (403, {"error": {
        "code": 10, "message": "Permission Denied"}})
    rc, out, err = main(capsys, "creators", "search", IG_ACCOUNT)
    assert rc == 1 and "creator_marketplace_not_ready" in err and "onboard" in err


@pytest.mark.parametrize("argv", [("--country", "JPN"), ("--limit", "0"), ("--q", "a\x07")])
def test_検索の引数が違えば網に出る前に断る(m6, capsys, argv):
    rc, out, err = main(capsys, "creators", "search", IG_ACCOUNT, *argv)
    assert rc == 2 and "brand_args_invalid" in err and m6["calls"] == []


# ================================================================== 柵

def test_6権限は台本の区間と記録の種類を持つ():
    rows = {row["name"]: row for row in appreview.items()}
    for name in SIX:
        assert name in pw.PERMISSIONS and name not in appreview.PLANNED, name
        assert rows[name]["segments"] and rows[name]["doors"], name
        assert pw.PERMISSIONS[name]["evidence"], name
    assert set(meta_brand.RUN_ACTIONS) <= set(pw.RUN_ACTIONS)
    for name in ("instagram_branded_content_brand", "instagram_branded_content_creator"):
        assert pw.PERMISSIONS[name]["kind"] == pw.WRITE


def test_書く口を呼ぶのはmeta_brandだけ():
    root = pathlib.Path(__file__).resolve().parent.parent / "thth"
    doors = re.compile(r"\.(brand_creators_change|promote_set)\(")
    offenders = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                       if doors.search(p.read_text(encoding="utf-8")) and p.name != "meta_brand.py")
    assert offenders == []


def test_符丁は目録にあり3つがそろう():
    for code in ("brand_args_invalid", "creator_marketplace_not_ready"):
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_sponsorだけで許す取り消すが無ければ断る(m6, capsys):
    rc, out, err = main(capsys, "brand", "promote", IG_ACCOUNT, MEDIA, "--sponsor", BRAND)
    assert rc == 2 and "brand_args_invalid" in err and "--allow" in err and "--deny" in err
