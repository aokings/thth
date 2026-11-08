"""計画 Meta 全部盛り申請 M3: 読むだけの最小の口。

見るのは:
  - `thth embed`（Threads・FB・IG）・`thth where`（IG のハッシュタグ・FB のページの検索）・`thth who --profile`
    （IG の business_discovery・FB の公開の数）・`thth upcoming`・`thth ads catalog|leads|network|shop` が 1 本ずつ通る。
  - **本文・画像を頼まない**（要求の `fields` を縛る）。**どこにも残さない**（置き場の全部のファイルを読み、目印が無い）。
  - IG の 7 日 30 種: 31 種目は網に出る前に断る。同じタグは数えない。7 日で枠が戻る。timer の語（`keyword_search`）に乗らない。
  - 読む口は GET だけ。断りは目録の 3 つを出す。審査員の道（`thth login`）でも `embed`・`upcoming` が通る。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。ここで通るのは「読んだ文書のとおりに投げ、返った形を写す」まで。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import secrets

import pytest

from thth import adapters, cli, jst, meta_public, refusal_catalog
from thth.adapters import facebook as fb
from thth.adapters import instagram as ig
from thth.ads import graphread, ledger
from thth.report_service import ReportServiceError, execute_report
from tests.test_b17_remote_reads import call as server_call
from tests.test_facebook_adapter import fake_graph
from tests.test_v212_server_writes import env  # noqa: F401
from tests.test_v3140_remote_cli import ACCOUNT, KEY, _echo, home, run, worker  # noqa: F401
from tests.test_v3140_server_reads import media  # noqa: F401

V = "/" + fb.GRAPH_VERSION
PAGE, IG_ID, OTHER_PAGE, HASHTAG = "61596075596464", "17841437318552220", "20531316728", "17843826142012701"
ACT, BUSINESS, CATALOG, FORM = "1234567890", "4242", "555", "777"
FB_ACCOUNT, IG_ACCOUNT, TH_ACCOUNT, ADS = "m3-fb", "m3-ig", "m3-th", "m3-ads"
# 残ってはいけない目印（偽の応答に入れる）。頼んでいない欄でも Meta が返したら写さないことまで見る。
CAPTION, LEAD_EMAIL, BIO = "本文の目印かもしれない", "lead-marker@example.test", "自己紹介の目印"
FOUND = {"id": "1", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/AAA/",
         "timestamp": "2026-11-01T01:00:00+0000", "like_count": 12, "comments_count": 3, "caption": CAPTION}
HTML = "<blockquote>埋め込み</blockquote>"
OEMBED = (200, {"html": HTML, "type": "rich", "provider_name": "Meta", "secret_extra": CAPTION})


def routes():
    return {
        ("GET", V + "/oembed_post"): OEMBED, ("GET", V + "/oembed_video"): OEMBED, ("GET", V + "/oembed_page"): OEMBED,
        ("GET", V + "/instagram_oembed"): OEMBED, ("GET", "/v1.0/oembed"): OEMBED,
        ("GET", V + "/ig_hashtag_search"): (200, {"data": [{"id": HASHTAG}]}),
        ("GET", V + f"/{HASHTAG}/top_media"): (200, {"data": [FOUND]}),
        ("GET", V + f"/{HASHTAG}/recent_media"): (200, {"data": [dict(FOUND, id="2")]}),
        ("GET", V + "/pages/search"): (200, {"data": [{"id": OTHER_PAGE, "name": "別のページ",
                                                       "link": "https://www.facebook.com/other"}]}),
        ("GET", V + "/" + IG_ID): (200, {"business_discovery": {
            "username": "someshop", "followers_count": 900, "media_count": 40, "biography": BIO,
            "media": {"data": [FOUND]}}}),
        ("GET", V + "/otherpage"): (200, {"id": OTHER_PAGE, "name": "別のページ", "fan_count": 70, "followers_count": 80,
                                          "about": BIO}),
        ("GET", V + f"/{OTHER_PAGE}/posts"): (200, {"data": [{
            "id": OTHER_PAGE + "_1", "created_time": "2026-11-01T01:00:00+0000", "message": CAPTION,
            "shares": {"count": 2}, "comments": {"summary": {"total_count": 5}},
            "reactions": {"summary": {"total_count": 9}}}]}),
        ("GET", V + f"/{IG_ID}/upcoming_events"): (200, {"data": [{"id": "9", "title": "新作の発売",
                                                                   "start_time": "2026-12-01T01:00:00+0000"}]}),
        ("GET", V + f"/{BUSINESS}/owned_product_catalogs"): (200, {"data": [{"id": CATALOG, "name": "こぴちゃ",
                                                                             "product_count": 3}]}),
        ("GET", V + f"/{CATALOG}/products"): (200, {"data": [{"id": "1", "name": "豆", "retailer_id": "B-1",
                                                              "price": "1000"}]}),
        ("GET", V + f"/{PAGE}/leadgen_forms"): (200, {"data": [{"id": FORM, "name": "資料請求", "status": "ACTIVE",
                                                                "leads_count": 1}]}),
        ("GET", V + f"/{FORM}/leads"): (200, {"data": [{"id": "88", "created_time": "2026-11-01T01:00:00+0000",
                                                        "field_data": [{"name": "email", "values": [LEAD_EMAIL]}]}]}),
        ("GET", V + f"/{BUSINESS}/adnetworkanalytics"): (200, {"data": [{"query_id": "q", "omitted_results": [{}], "results": [
            {"time": "2026-11-01", "metric": "fb_ad_network_imp", "value": "100"}]}]}),
        ("GET", V + f"/{BUSINESS}/commerce_merchant_settings"): (200, {"data": [{"id": "6", "cta": "OFFSITE_IOS",
                                                                                 "shop_setup": "SETUP"}]}),
    }


@pytest.fixture
def m3(isolated_account_factory, tmp_path, monkeypatch):
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=4242\nMETA_APP_SECRET=" + secrets.token_urlsafe(32) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    monkeypatch.setenv("THTH_ADACCOUNTS_DIR", str(tmp_path / "adaccounts"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    tokens = {}
    for name, extra in ((FB_ACCOUNT, {"media": "facebook", "page_id": PAGE, "handle": PAGE, "user_id": PAGE}),
                        (IG_ACCOUNT, {"media": "instagram", "ig_user_id": IG_ID, "page_id": PAGE, "handle": IG_ID,
                                      "user_id": IG_ID}),
                        (TH_ACCOUNT, {"media": "threads", "user_id": "1"})):
        token = tmp_path / f"{name}.token"
        tokens[name] = "tok-" + secrets.token_hex(12)
        token.write_text(json.dumps({"access_token": tokens[name], "user_id": extra["user_id"]}))
        token.chmod(0o600)
        isolated_account_factory(name, token=str(token), **extra)
    data = ledger.build(ADS, act_id=ACT, project="kopicha", pages=[FB_ACCOUNT], business_id=BUSINESS)
    ledger.write_new(ADS, data)
    ledger.write_token(ledger.load(ADS), {"access_token": "ads-" + secrets.token_hex(12), "scopes": []})
    table = routes()
    with fake_graph(table) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        monkeypatch.setenv("THTH_THREADS_BASE_URL", url)
        yield {"tmp": tmp_path, "routes": table, "calls": calls, "tokens": tokens}


def main(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def fields_of(m3, suffix) -> str:
    return next(c["query"]["fields"][0] for c in m3["calls"] if c["path"].endswith(suffix))


def nothing_kept(m3, *markers):
    """置き場の全部のファイルに目印が無い（本文・リード・自己紹介は画面に出すだけ）。"""
    for path in pathlib.Path(m3["tmp"]).rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            for marker in markers:
                assert marker.encode() not in raw, f"{path} に {marker} が残っている"


# ================================================================== embed

@pytest.mark.parametrize("account,url,suffix", [
    (FB_ACCOUNT, "https://www.facebook.com/page/posts/123", "/oembed_post"),
    (FB_ACCOUNT, "https://www.facebook.com/page/videos/123/", "/oembed_video"),
    (FB_ACCOUNT, "https://www.facebook.com/page", "/oembed_page"),
    (IG_ACCOUNT, "https://www.instagram.com/p/AAA/", "/instagram_oembed"),
    (TH_ACCOUNT, "https://www.threads.net/@a/post/AAA", "/v1.0/oembed"),
])
def test_embedは媒体ごとの口を呼びhtmlを出して残さない(m3, capsys, account, url, suffix):
    rc, out, err = main(capsys, "embed", account, url, "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["embed"]["html"] == HTML and value["storage"] == "display_only"
    assert "secret_extra" not in value["embed"]
    assert [c["path"] for c in m3["calls"]][-1].endswith(suffix)
    assert m3["calls"][-1]["query"]["url"] == [url]
    nothing_kept(m3, HTML, url)


@pytest.mark.parametrize("account,url", [
    (IG_ACCOUNT, "https://www.facebook.com/page/posts/1"), (FB_ACCOUNT, "http://www.facebook.com/page"),
    (FB_ACCOUNT, "https://user:pw@www.facebook.com/page"), (FB_ACCOUNT, "https://evil.example/www.facebook.com"),
    (TH_ACCOUNT, "not a url"),
])
def test_embedは媒体の違うURLを網に出す前に断り3つを出す(m3, capsys, account, url):
    rc, _out, err = main(capsys, "embed", account, url)
    assert rc == 2 and "embed_url_invalid" in err and "通すには:" in err and "もっと良く:" in err
    assert m3["calls"] == []


def test_embedはhtmlの無い答えを取れたことにしない(m3, capsys):
    m3["routes"][("GET", V + "/instagram_oembed")] = (200, {"type": "rich"})
    rc, _out, err = main(capsys, "embed", IG_ACCOUNT, "https://www.instagram.com/p/AAA/")
    assert rc == 1 and "html がありません" in err


# ================================================================== where の Instagram

def test_ハッシュタグの検索は本文と画像を頼まず数とidだけを出して残さない(m3, capsys):
    rc, out, err = main(capsys, "where", IG_ACCOUNT, "#Coffee", "--json")
    assert rc == 0, err
    assert CAPTION not in out
    node = json.loads(out)["by_account"][IG_ACCOUNT]
    post = node["by_word"]["#Coffee"]["posts"][0]
    assert (post["post_id"], post["likes"], post["replies"], post["text"]) == ("1", 12, 3, None)
    assert node["storage"] == "display_only" and node["hashtag_window"] == {"used": 1, "limit": 30}
    fields = fields_of(m3, "/top_media").split(",")
    assert not {"caption", "media_url", "children", "username"} & set(fields)
    search = next(c for c in m3["calls"] if c["path"].endswith("/ig_hashtag_search"))
    assert search["query"]["q"] == ["coffee"] and search["query"]["user_id"] == [IG_ID]
    nothing_kept(m3, CAPTION, FOUND["permalink"])
    # 残るのは調べたタグと時刻だけ。
    saved = json.loads((pathlib.Path(meta_public._hashtag_path(IG_ACCOUNT))).read_text())
    assert list(saved) == ["tags"] and list(saved["tags"]) == ["coffee"]
    assert jst.parse(saved["tags"]["coffee"]) is not None


def test_recentはrecent_mediaを呼ぶ(m3, capsys):
    rc, out, _err = main(capsys, "where", IG_ACCOUNT, "coffee", "--recent", "--json")
    assert rc == 0 and json.loads(out)["by_account"][IG_ACCOUNT]["by_word"]["coffee"]["posts"][0]["post_id"] == "2"


def test_人向けの画面に数が出て本文の行は無い(m3, capsys):
    rc, out, _err = main(capsys, "where", IG_ACCOUNT, "coffee")
    assert rc == 0 and "いいね=12  コメント=3" in out and "本文なし" not in out


def fill(tags=30, now=None):
    now = now or jst.now_jst()
    meta_public.reserve_hashtags(IG_ACCOUNT, [f"t{i}" for i in range(tags)], now=now)


def test_31種目は網に出る前に断り3つと次の時刻を出す(m3, capsys):
    fill()
    rc, _out, err = main(capsys, "where", IG_ACCOUNT, "thirtyfirst")
    assert rc == 2 and err.startswith("ig_hashtag_limit") and "通すには:" in err and "もっと良く:" in err
    assert m3["calls"] == []
    rc, out, _err = main(capsys, "where", IG_ACCOUNT, "thirtyfirst", "--json")
    assert rc == 2
    body = json.loads(out)
    assert body["error"] == "ig_hashtag_limit" and body["why"] and body["fix"] and body["better"] and body["next_at"]
    assert "thirtyfirst" not in json.loads(pathlib.Path(meta_public._hashtag_path(IG_ACCOUNT)).read_text())["tags"]


def test_越える語が1つでもあれば1語も呼ばない(m3, capsys):
    fill(29)
    rc, _out, _err = main(capsys, "where", IG_ACCOUNT, "new1", "new2")
    assert rc == 2 and m3["calls"] == []


def test_projectは1口座の枠の断りで全体を落とさない(m3, capsys):
    # 確かめ役（Fable 11/05）の指摘: --project の中の IG が 30 種を越えると、FB の答えまで消えていた。
    fill()
    rc, out, err = main(capsys, "where", "--project", "nigamilab", "thirtyfirst", "--json")
    assert rc == 0, err
    body = json.loads(out)
    assert IG_ACCOUNT not in body["by_account"] and FB_ACCOUNT in body["by_account"]
    assert any(line.startswith(f"{IG_ACCOUNT}: ig_hashtag_limit") for line in body["cannot_say"])
    assert "thirtyfirst" not in json.loads(pathlib.Path(meta_public._hashtag_path(IG_ACCOUNT)).read_text())["tags"]


def test_同じタグは30種のあとも数えない(m3, capsys):
    fill()
    rc, _out, err = main(capsys, "where", IG_ACCOUNT, "#T3")
    assert rc == 0, err


def test_7日で枠が戻る(m3, capsys):
    fill(now=jst.now_jst() - datetime.timedelta(days=7, minutes=1))
    rc, out, err = main(capsys, "where", IG_ACCOUNT, "fresh", "--json")
    assert rc == 0, err
    assert json.loads(out)["by_account"][IG_ACCOUNT]["hashtag_window"]["used"] == 1
    assert len(json.loads(pathlib.Path(meta_public._hashtag_path(IG_ACCOUNT)).read_text())["tags"]) == 1


def test_控えが壊れていれば数えられないので呼ばない(m3, capsys):
    path = pathlib.Path(meta_public._hashtag_path(IG_ACCOUNT))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")
    rc, _out, err = main(capsys, "where", IG_ACCOUNT, "coffee")
    assert rc == 2 and err.startswith("ig_hashtag_ledger_unreadable") and m3["calls"] == []


def test_タグにならない語は断る(m3, capsys):
    rc, _out, err = main(capsys, "where", IG_ACCOUNT, "two words")
    assert rc == 2 and err.startswith("ig_hashtag_invalid") and m3["calls"] == []


@pytest.mark.parametrize("flag", (["--also", "豆"], ["--aggregate"], ["--tag"], ["--ja"], ["--since", "7d"]))
def test_本文を読まない口は本文の絞り込みを黙って無視しない(m3, capsys, flag):
    rc, out, _err = main(capsys, "where", IG_ACCOUNT, "coffee", *flag, "--json")
    assert rc == 0 and m3["calls"] == []
    body = json.loads(out)
    assert body["by_account"] == {} and "では使えません" in body["cannot_say"][0]


def test_timerの語の口に乗らない():
    # 朝の報告と観測の地図は `keyword_search` を見る。乗ると timer が 30 種を食う。
    assert "keyword_search" not in ig.InstagramAdapter.CAPABILITIES
    assert "keyword_search" not in fb.FacebookAdapter.CAPABILITIES


# ================================================================== where の Facebook

def test_ページの検索は名前とリンクとidだけ(m3, capsys):
    rc, out, err = main(capsys, "where", FB_ACCOUNT, "コーヒー", "--json")
    assert rc == 0, err
    post = json.loads(out)["by_account"][FB_ACCOUNT]["by_word"]["コーヒー"]["posts"][0]
    assert (post["post_id"], post["author"], post["permalink"]) == (OTHER_PAGE, "別のページ", "https://www.facebook.com/other")
    assert fields_of(m3, "/pages/search") == "id,name,link"
    nothing_kept(m3, "別のページ")


def test_機能の審査が無い断りは再認可を勧めない(m3, capsys):
    m3["routes"][("GET", V + "/pages/search")] = (403, {"error": {"message": "needs feature", "code": 10}})
    rc, out, _err = main(capsys, "where", FB_ACCOUNT, "コーヒー", "--json")
    assert rc == 0
    line = json.loads(out)["by_account"][FB_ACCOUNT]["cannot_say"][0]
    assert "meta_feature_required" in line and "Page Public Content Access" in line
    assert "通すには:" in line and "もっと良く:" in line and "thth auth" not in line


# ================================================================== who

def test_whoのプロフィールはIGの数だけで残さない(m3, capsys):
    rc, out, err = main(capsys, "who", IG_ACCOUNT, "@someshop", "--profile", "--json")
    assert rc == 0, err
    profile = json.loads(out)["profile"]
    assert (profile["follower_count"], profile["media_count"]) == (900, 40)
    assert profile["recent_media"][0]["likes"] == 12
    assert BIO not in out and CAPTION not in out
    field = fields_of(m3, "/" + IG_ID)
    assert "biography" not in field and "caption" not in field and "media_url" not in field
    nothing_kept(m3, "someshop", BIO, CAPTION)


def test_whoのプロフィールはFBの数だけで投稿が読めなくてもページの数は返す(m3, capsys):
    rc, out, err = main(capsys, "who", FB_ACCOUNT, "@otherpage", "--profile", "--json")
    assert rc == 0, err
    profile = json.loads(out)["profile"]
    assert (profile["follower_count"], profile["fan_count"]) == (80, 70)
    assert profile["recent_media"][0] == {"id": OTHER_PAGE + "_1", "timestamp": "2026-11-01T01:00:00+0000",
                                          "likes": 9, "comments": 5, "shares": 2}
    assert "message" not in fields_of(m3, "/posts") and CAPTION not in out and BIO not in out
    nothing_kept(m3, "otherpage", CAPTION)
    m3["routes"][("GET", V + f"/{OTHER_PAGE}/posts")] = (403, {"error": {"message": "x", "code": 10}})
    rc, out, _err = main(capsys, "who", FB_ACCOUNT, "@otherpage", "--profile", "--json")
    profile = json.loads(out)["profile"]
    assert rc == 0 and profile["fan_count"] == 70 and "meta_feature_required" in profile["recent_media_cannot_say"]


@pytest.mark.parametrize("account,name", [(IG_ACCOUNT, "@a){id}"), (FB_ACCOUNT, "@a/feed")])
def test_欄の式やpathに入る名前は字を絞る(m3, capsys, account, name):
    rc, out, _err = main(capsys, "who", account, name, "--profile", "--json")
    assert rc == 0 and json.loads(out)["profile"] is None and m3["calls"] == []


# ================================================================== upcoming

def test_upcomingは自分の予定を読み残さない(m3, capsys):
    rc, out, err = main(capsys, "upcoming", IG_ACCOUNT, "--json")
    assert rc == 0, err
    assert json.loads(out)["events"][0]["title"] == "新作の発売"
    nothing_kept(m3, "新作の発売")


def test_upcomingはInstagramだけ(m3, capsys):
    rc, _out, err = main(capsys, "upcoming", FB_ACCOUNT)
    assert rc == 1 and "予定の読み取り" in err and m3["calls"] == []


# ================================================================== 広告の読む口

def test_カタログの一覧と商品(m3, capsys):
    rc, out, err = main(capsys, "ads", "catalog", "list", ADS, "--json")
    assert rc == 0, err
    assert json.loads(out)["catalogs"] == [{"id": CATALOG, "name": "こぴちゃ", "product_count": 3}]
    rc, out, err = main(capsys, "ads", "catalog", "products", ADS, "--catalog", CATALOG, "--json")
    assert rc == 0, err
    assert json.loads(out)["products"] == [{"id": "1", "name": "豆", "retailer_id": "B-1"}]


def test_ビジネスのカタログでないidは商品を読まない(m3, capsys):
    rc, _out, err = main(capsys, "ads", "catalog", "products", ADS, "--catalog", "999")
    assert rc == 2 and "ads_catalog_other_business" in err and "通すには:" in err
    assert not any(c["path"].endswith("/999/products") for c in m3["calls"])


def test_台帳にビジネスが無ければ広告アカウントに訊き台帳には書かない(m3, capsys):
    path = pathlib.Path(ledger.path_for(ADS))
    data = json.loads(path.read_text())
    data["business_id"] = None
    path.write_text(json.dumps(data))
    m3["routes"][("GET", V + "/act_" + ACT)] = (200, {"id": "act_" + ACT, "business": {"id": BUSINESS, "name": "吾徳"}})
    rc, _out, err = main(capsys, "ads", "catalog", "list", ADS)
    assert rc == 0, err
    assert json.loads(path.read_text())["business_id"] is None
    m3["routes"][("GET", V + "/act_" + ACT)] = (200, {"id": "act_" + ACT})
    rc, _out, err = main(capsys, "ads", "catalog", "list", ADS)
    assert rc == 2 and "ads_business_unknown" in err


def test_リードは端末に出すだけでどこにも残さない(m3, capsys):
    rc, out, err = main(capsys, "ads", "leads", ADS, "--json")
    assert rc == 0, err
    assert json.loads(out)["forms"][0]["leads_count"] == 1 and LEAD_EMAIL not in out
    rc, out, err = main(capsys, "ads", "leads", ADS, "--form", FORM, "--json")
    assert rc == 0, err
    assert json.loads(out)["leads"][0]["fields"]["email"] == [LEAD_EMAIL]
    rc, out, _err = main(capsys, "ads", "leads", ADS, "--form", FORM)
    assert rc == 0 and LEAD_EMAIL in out
    nothing_kept(m3, LEAD_EMAIL)


def test_ページのフォームでないidはリードを読まない(m3, capsys):
    rc, _out, err = main(capsys, "ads", "leads", ADS, "--form", "999")
    assert rc == 2 and "ads_form_other_page" in err
    assert not any(c["path"].endswith("/999/leads") for c in m3["calls"])


def test_ページが1つに決まらなければ断る(m3, capsys):
    path = pathlib.Path(ledger.path_for(ADS))
    data = json.loads(path.read_text())
    data["pages"] = []
    path.write_text(json.dumps(data))
    rc, _out, err = main(capsys, "ads", "leads", ADS)
    assert rc == 2 and "ads_page_unknown" in err and m3["calls"] == []
    rc, _out, err = main(capsys, "ads", "leads", ADS, "--page", PAGE)
    assert rc == 0, err


def test_AudienceNetworkの数とショップの設定(m3, capsys):
    rc, out, err = main(capsys, "ads", "network", ADS, "--since", "2026-11-01", "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["rows"][0]["value"] == "100" and value["omitted"] == 1
    call = next(c for c in m3["calls"] if c["path"].endswith("/adnetworkanalytics"))
    assert json.loads(call["query"]["metrics"][0]) == graphread.AUDIENCE_METRICS
    rc, out, err = main(capsys, "ads", "shop", ADS, "--json")
    assert rc == 0, err
    assert json.loads(out)["shops"] == [{"id": "6", "cta": "OFFSITE_IOS", "shop_setup": "SETUP"}]


def test_M3の読む口は全部GETだけで鍵を出さない(m3, capsys):
    outputs = []
    for argv in (["embed", IG_ACCOUNT, "https://www.instagram.com/p/AAA/"], ["where", IG_ACCOUNT, "coffee"],
                 ["where", FB_ACCOUNT, "語"], ["who", IG_ACCOUNT, "@someshop", "--profile"],
                 ["who", FB_ACCOUNT, "@otherpage", "--profile"], ["upcoming", IG_ACCOUNT],
                 ["ads", "catalog", "list", ADS], ["ads", "catalog", "products", ADS, "--catalog", CATALOG],
                 ["ads", "leads", ADS], ["ads", "leads", ADS, "--form", FORM], ["ads", "network", ADS],
                 ["ads", "shop", ADS]):
        rc, out, err = main(capsys, *argv)
        assert rc == 0, (argv, err)
        outputs.append(out + err)
    assert m3["calls"] and {c["method"] for c in m3["calls"]} == {"GET"}
    for token in m3["tokens"].values():
        assert token not in "".join(outputs)


# ================================================================== 審査員の道（thth login）

class Fake:
    def __init__(self):
        self.failed = None

    def oembed(self, url):
        return {"html": HTML, "provider_name": "Threads"}


@pytest.fixture
def served(env, media, monkeypatch):  # noqa: F811
    monkeypatch.setattr(adapters, "make_adapter", lambda *a, **k: Fake())
    return env


def test_VMの読む口はCLIと同じ形でembedを返す(served):
    assert server_call(served, "embed", url="https://www.threads.net/@a/post/AAA") == {
        "account": "alpha", "url": "https://www.threads.net/@a/post/AAA",
        "embed": {"html": HTML, "provider_name": "Threads"}, "storage": "display_only"}


def test_VMの読む口のembedもrunsに1行を残す(served):
    # 遠くの道でも手元と同じ記録（審査の 30 日の呼び出しに数える・11/06）。命令の名前と結果だけ。
    from thth import accounts
    server_call(served, "embed", url="https://www.threads.net/@a/post/AAA")
    rows = [json.loads(line) for path in pathlib.Path(accounts.state_dir_for("alpha")).glob("runs-*.ndjson")
            for line in path.read_text().splitlines() if line.strip()]
    mine = [r for r in rows if r.get("action") == "embed"]
    assert mine and mine[-1]["status"] == "ok" and HTML not in json.dumps(mine)


def test_VMの読む口も他の媒体のURLを断る(served):
    with pytest.raises(ReportServiceError) as caught:
        execute_report(served["context"], {"operation": "embed", "account": "alpha", "url": "https://evil.example/"})
    assert str(caught.value) == "embed_url_invalid"


def test_遠くの道のCLIがembedとupcomingを通す(worker, capsys):  # noqa: F811
    rc, _out, _err = run(capsys, "embed", ACCOUNT, "https://www.threads.net/@a/post/AAA", "--json")
    assert rc == 0
    assert worker.calls[-1]["path"] == "/api/v1/embed"
    rc, _out, _err = run(capsys, "upcoming", ACCOUNT, "--json")
    assert rc == 0
    assert worker.calls[-1]["path"] == "/api/v1/upcoming_events"


# ================================================================== 目録

def test_M3の断りは3つがそろい版はsinceの形():
    for code in ("meta_feature_required", "ig_hashtag_limit", "ig_hashtag_invalid", "ig_hashtag_ledger_unreadable",
                 "embed_url_invalid", "ads_business_unknown", "ads_page_unknown", "ads_form_other_page",
                 "ads_catalog_other_business"):
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"] and entry["since"] == "5.13.0", code
