"""4.0 S3 §6: フォトライフの adapter（`thth/adapters/fotolife.py`）と共有部分（`hatena_common`）。

T-S3-1a・2a・2b・2d と、127.0.0.1 に立てた本物の HTTP サーバに本物の urllib の経路で通す試験。
**本物のはてなには 1 回も出ない。** 秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import base64

import pytest

from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import FAKE_CONSUMER, FAKE_TOKEN, HATENA_ID, fake_http, put_secrets  # noqa: F401
from tests.hatena_s3_helpers import ATOM03, FOTOLIFE, JPEG, photo_feed, photo_xml, posted
from tests.test_v400_s2_audit_fixes import _Server
from thth import httpsafe
from thth.adapters import fotolife, hatena_common, hatenablog


def adapter_for(world, **account_extra):  # noqa: F811
    principal = {"principal": "hatena-main", "hatena_id": HATENA_ID, "consumer": str(world.main_token) + ".consumer",
                 "token": str(world.main_token)}
    account = {"account": "lane-a-hatenablog", "blog_id": "a.hatenablog.com", "token": str(world.main_token),
               "timeout": 300, "media": "hatenablog", **account_extra}
    return fotolife.factory(account, principal)


def _answer(status, raw, headers=None):
    def handler(method, url, h, body):
        return status, headers or {}, raw
    return handler


# ---------------------------------------------------------------- T-S3-1a

def test_T_S3_1a_hatenablogは共有部分を使う():
    """既存の hatenablog の試験は 1 行も変えずに緑（全件で見る）。ここでは共有の形だけを確かめる。"""
    assert hatenablog.read_consumer is hatena_common.read_consumer
    assert hatenablog.check_token is hatena_common.check_token
    assert hatenablog.ResponseTooLarge is hatena_common.ResponseTooLarge
    assert hatenablog.MAX_RESPONSE_BYTES == hatena_common.MAX_RESPONSE_BYTES == 4 * 1024 * 1024


@pytest.mark.parametrize("setup, code", [
    (dict(token=False), "token_missing"), (dict(consumer=False), "consumer_missing"),
    (dict(token_data={**FAKE_TOKEN, "hatena_id": "someone-else"}), "token_invalid")])
def test_T_S3_1a_fotolifeのfactoryもブログと同じ符丁で断る(world, setup, code):  # noqa: F811
    from thth.hatena_publish import AdapterUnavailable
    put_secrets(world, **setup)
    with pytest.raises(AdapterUnavailable) as caught:
        adapter_for(world)
    assert caught.value.code == code


# ---------------------------------------------------------------- T-S3-2a 送る XML

def test_T_S3_2a_送るXMLはAtom03_base64に改行なし_フォルダ_generator_題(world, fake_http):  # noqa: F811
    """柵: 名前空間を Atom 1.0 にすると赤。"""
    put_secrets(world)
    data = JPEG * 200   # base64 が 76 字を超える長さ
    http = fake_http(_answer(201, photo_xml("20261020075901", title="bean.jpg thth:7e3f0a1b2c3d")))
    result = adapter_for(world).upload(data=data, content_type="image/jpeg", title="bean.jpg thth:7e3f0a1b2c3d",
                                       folder="thth")
    assert result.ok and result.fotolife_id == "20261020075901"
    (call,) = http.calls
    sent = posted(call["body"])
    assert sent["root"] == "{%s}entry" % ATOM03
    assert (sent["mode"], sent["type"]) == ("base64", "image/jpeg")
    assert "\n" not in sent["content"] and base64.b64decode(sent["content"]) == data
    assert sent["subject"] == "thth" and sent["generator"] == "thth"
    assert sent["title"] == "bean.jpg thth:7e3f0a1b2c3d"
    assert b"http://www.w3.org/2005/Atom" not in call["body"]


def test_T_S3_2a_題とフォルダはエスケープし_制御文字は送る前に断る(world, fake_http):  # noqa: F811
    put_secrets(world)
    http = fake_http(_answer(201, photo_xml("1", title="x")))
    a = adapter_for(world)
    assert a.upload(data=JPEG, content_type="image/jpeg", title="a<b>&c.jpg thth:0", folder="x&y").ok
    assert posted(http.calls[0]["body"])["title"] == "a<b>&c.jpg thth:0"
    for title, folder in (("bad\x01", "thth"), ("ok", "bad\x1e"), ("ok", "")):
        result = a.upload(data=JPEG, content_type="image/jpeg", title=title, folder=folder)
        assert (result.ok, result.ambiguous, result.error) == (False, False, "fotolife_title_invalid")
    assert len(http.calls) == 1


# ---------------------------------------------------------------- T-S3-2b 応答の読み

def test_T_S3_2b_公式の例の形から欄を取る():
    """柵: local name で探すのを名前空間の決め打ち（Atom 1.0）にすると赤。"""
    fields = fotolife.parse_photo(photo_xml("20261020075901", title="t", ext="j"))
    assert fields["fotolife_id"] == "20261020075901"
    assert fields["syntax"] == f"f:id:{HATENA_ID}:20261020075901j:image"
    assert fields["image_url"].endswith("/20261020075901.jpg")
    assert fields["id"] == f"tag:hatena.ne.jp,2005:fotolife-{HATENA_ID}-20261020075901"
    # Atom 1.0 で返っても・綴りの誤った要素名（dc:subjetct）があっても読む
    assert fotolife.parse_photo(photo_xml("5", namespace="http://www.w3.org/2005/Atom"))["fotolife_id"] == "5"
    assert fotolife.parse_photo(photo_xml("6", misspelled_subject=True))["fotolife_id"] == "6"
    # service.edit が無ければ Location の末尾
    assert fotolife.parse_photo(photo_xml("7", with_edit=False),
                                location=f"{FOTOLIFE}/atom/edit/7")["fotolife_id"] == "7"


@pytest.mark.parametrize("raw", [
    photo_xml("1", with_syntax=False), photo_xml("1", doctype=True), photo_xml("1", with_edit=False),
    photo_xml("1", misspelled_subject=True, broken=True), b"not xml", photo_feed([])])
def test_T_S3_2b_syntaxが無い_DOCTYPE_idが無い_閉じタグが合わないはresponse_invalid(raw):
    """公式の例の閉じタグの綴りの誤り（開きと閉じが違う）は XML として読めない＝response_invalid。"""
    from thth.adapters.hatena_common import ResponseInvalid
    with pytest.raises(ResponseInvalid):
        fotolife.parse_photo(raw)


def test_T_S3_2b_一覧は写真の並びと次の頁():
    feed = fotolife.parse_feed(photo_feed([photo_xml("2", title="b"), photo_xml("1", title="a")]))
    assert [p["title"] for p in feed["entries"]] == ["b", "a"] and feed["next"] is None


# ---------------------------------------------------------------- T-S3-2d URL・ヘッダ・秘密

def test_T_S3_2d_URLはhttpsのf_hatena固定_idが数字でなければ送らない(world, fake_http):  # noqa: F811
    put_secrets(world)
    http = fake_http(_answer(200, photo_xml("123")))
    a = adapter_for(world)
    assert a.post_url == "https://f.hatena.ne.jp/atom/post" and a.feed_url == "https://f.hatena.ne.jp/atom/feed"
    assert a.get_image(fotolife_id="123")["fotolife_id"] == "123"
    assert http.calls[-1]["url"] == "https://f.hatena.ne.jp/atom/edit/123"
    for bad in ("12a", "../1", "", "1" * 21):
        with pytest.raises(fotolife.CheckFailed) as caught:
            a.get_image(fotolife_id=bad)
        assert str(caught.value) == "fotolife_id_invalid"
    with pytest.raises(fotolife.CheckFailed) as caught:
        a.get_image(fotolife_id="123", edit_uri="https://elsewhere.example/atom/edit/123")
    assert str(caught.value) == "edit_uri_mismatch"
    assert len(http.calls) == 1


def test_T_S3_2d_AuthorizationとUser_AgentとContent_Type_エラーの文に秘密が無い(world, fake_http):  # noqa: F811
    put_secrets(world)
    http = fake_http(_answer(201, photo_xml("1")))
    adapter_for(world).upload(data=JPEG, content_type="image/jpeg", title="t", folder="thth")
    headers = http.calls[0]["headers"]
    assert headers["authorization"].startswith("OAuth ") and "oauth_signature=" in headers["authorization"]
    assert headers["user-agent"].startswith("thth/")
    assert headers["content-type"] == "application/atom+xml; charset=utf-8"
    for secret in (FAKE_TOKEN["oauth_token_secret"], FAKE_CONSUMER["consumer_secret"]):
        assert secret not in headers["authorization"]

    def boom(method, url, h, body):
        raise OSError("connect failed " + FAKE_TOKEN["oauth_token_secret"])
    fake_http(boom)
    a = adapter_for(world)
    result = a.upload(data=JPEG, content_type="image/jpeg", title="t", folder="thth")
    assert result.error == "fotolife_transport_failed"
    probe = a.probe()
    assert FAKE_TOKEN["oauth_token_secret"] not in repr(probe) + repr(result)


def test_T_S3_2d_DELETEとPUTの口は無い():
    assert not hasattr(fotolife.FotolifeAdapter, "delete_image")
    assert not hasattr(fotolife.FotolifeAdapter, "update_image")
    assert {name for name in vars(fotolife.FotolifeAdapter) if not name.startswith("_")} >= {
        "upload", "get_image", "recent", "probe"}


def test_get_imageの404はImageMissing_5xxはCheckFailed(world, fake_http):  # noqa: F811
    put_secrets(world)
    fake_http(_answer(404, b""))
    with pytest.raises(fotolife.ImageMissing):
        adapter_for(world).get_image(fotolife_id="1")
    fake_http(_answer(503, b""))
    with pytest.raises(fotolife.CheckFailed) as caught:
        adapter_for(world).get_image(fotolife_id="1")
    assert str(caught.value) == "fotolife_http_503"


def test_接続の前の断りは出ていない(world, fake_http):  # noqa: F811
    put_secrets(world)

    def reject(method, url, h, body):
        raise httpsafe.EndpointRejected("endpoint_invalid")
    fake_http(reject)
    result = adapter_for(world).upload(data=JPEG, content_type="image/jpeg", title="t", folder="thth")
    assert (result.ok, result.ambiguous, result.error) == (False, False, "endpoint_rejected")


# ---------------------------------------------------------------- 127.0.0.1 の本物の HTTP

def test_本物のurllibの経路で_POST201_POST500_POST302別ホスト_GET404_一覧(world, monkeypatch):  # noqa: F811
    """`httpsafe.urlopen` は差し替えない。柵: 302 を definite にすると赤。"""
    monkeypatch.setenv("THTH_TEST_ALLOW_HTTP", "1")
    put_secrets(world)
    with _Server() as srv:
        monkeypatch.setattr(fotolife, "BASE", f"http://127.0.0.1:{srv.port}")
        a = adapter_for(world)
        xml = {"Content-Type": "application/xml"}
        # POST 201
        srv.answers[("POST", "/atom/post")] = (201, {**xml, "Location": f"http://127.0.0.1:{srv.port}/atom/edit/42"},
                                               photo_xml("42", title="bean.jpg thth:0"))
        up = a.upload(data=JPEG, content_type="image/jpeg", title="bean.jpg thth:0", folder="thth")
        assert up.ok and up.fotolife_id == "42" and up.syntax == f"f:id:{HATENA_ID}:42j:image"
        assert up.edit_uri == f"http://127.0.0.1:{srv.port}/atom/edit/42"
        assert posted(srv.requests[-1]["body"])["type"] == "image/jpeg"
        assert srv.requests[-1]["authorization"].startswith("OAuth ")
        # POST 500 → 分からない
        srv.answers[("POST", "/atom/post")] = (500, {}, b"oops")
        failed = a.upload(data=JPEG, content_type="image/jpeg", title="t", folder="thth")
        assert (failed.ok, failed.ambiguous, failed.error) == (False, True, "fotolife_http_500")
        # POST 302（別ホスト）→ 追わない・分からない
        srv.answers[("POST", "/atom/post")] = (302, {"Location": f"http://localhost:{srv.port}/elsewhere"}, b"")
        moved = a.upload(data=JPEG, content_type="image/jpeg", title="t", folder="thth")
        assert (moved.ok, moved.ambiguous, moved.error) == (False, True, "redirect_blocked")
        assert not [r for r in srv.requests if r["path"] == "/elsewhere"]
        # GET 404
        with pytest.raises(fotolife.ImageMissing):
            a.get_image(fotolife_id="99")
        # GET 200
        srv.answers[("GET", "/atom/edit/42")] = (200, xml, photo_xml("42"))
        assert a.get_image(fotolife_id="42")["fotolife_id"] == "42"
        # 一覧
        srv.answers[("GET", "/atom/feed")] = (200, xml, photo_feed([photo_xml("42", title="bean.jpg thth:0")]))
        assert [p["title"] for p in a.recent()] == ["bean.jpg thth:0"]
        assert a.probe() == {"ok": True, "status": 200, "entries": 1, "error": None}
        for row in srv.requests:
            assert FAKE_TOKEN["oauth_token_secret"] not in (row["authorization"] or "")
