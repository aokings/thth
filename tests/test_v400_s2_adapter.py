"""4.0 S2 §3: はてなブログの adapter（`thth/adapters/hatenablog.py`）と timer の枝（`hatena_publish`）。

T-S2-3a〜3g と doctor（§3.7）。HTTP は fake（`httpsafe.urlopen` の差し替え）。はてなには出ない。
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from tests.hatena_s1_flow_helpers import ACCOUNT, NOW, approve, call, fm_of, lane  # noqa: F401
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import (BLOG, COLLECTION, FAKE_CONSUMER, FAKE_TOKEN, HATENA_ID, blog_handler,  # noqa: F401
                                  entry_id, entry_xml, fake_http, feed_xml, put_secrets, sent_content)
from thth import core, deliveries, hatena_publish, httpsafe, inflight, oauth1, sent
from thth.adapters import hatenablog

ARTICLE = "# アラビカ\n\n値動きのまとめ。\n\n終わり。\n"
ATOM = "http://www.w3.org/2005/Atom"


def throw():
    return core.throw_once(ACCOUNT, production_flag=True, adapter_factory=None, now=NOW)


def states(delivery):
    return [row["state"] for row in deliveries.read(ACCOUNT, delivery)["history"]]


@pytest.fixture
def blog(lane, fake_http):  # noqa: F811
    """画像の無い記事・偽物の token と consumer を置いた lane-a。"""
    lane.write("articles/a.md", ARTICLE)
    put_secrets(lane.world)
    lane.install = fake_http
    return lane


def approved(lane, name="a.md", **kwargs):
    path = lane.queue(name, **kwargs)
    approve(path)
    return path, fm_of(path)["delivery"]


class Blog:
    """記事を覚えている fake のブログ（POST で作り・GET で返し・PUT で直し・DELETE で消す）。"""

    def __init__(self, *, content_type="text/x-markdown"):
        self.entries = {}
        self.content_type = content_type
        self.next_num = 99

    def __call__(self, method, url, headers, body):
        if method == "GET" and url == COLLECTION:
            return 200, {}, feed_xml([entry_xml(num=n, content=c, content_type=self.content_type)
                                      for n, c in reversed(list(self.entries.items()))]
                                     or [entry_xml(num="1", content="前の記事", content_type=self.content_type)])
        if method == "POST" and url == COLLECTION:
            num = str(self.next_num)
            self.entries[num] = sent_content(body)
            return 201, {}, entry_xml(num=num, content=self.entries[num], content_type=self.content_type)
        num = url.rsplit("/", 1)[-1]
        if num not in self.entries:
            return 404, {}, b"Not Found"
        if method == "GET":
            return 200, {}, entry_xml(num=num, content=self.entries[num], content_type=self.content_type)
        if method == "PUT":
            self.entries[num] = sent_content(body)
            return 200, {}, entry_xml(num=num, content=self.entries[num], content_type=self.content_type)
        if method == "DELETE":
            del self.entries[num]
            return 200, {}, b""
        raise AssertionError((method, url))


def adapter_for(world, **principal_extra):
    principal = {"principal": "hatena-main", "hatena_id": HATENA_ID, "consumer": str(world.main_token) + ".consumer",
                 "token": str(world.main_token), **principal_extra}
    account = {"account": ACCOUNT, "blog_id": BLOG, "token": str(world.main_token), "timeout": 300,
               "media": "hatenablog"}
    return hatenablog.factory(account, principal)


# ---------------------------------------------------------------- T-S2-3a

@pytest.mark.parametrize("setup, code", [
    (dict(token=False), "token_missing"),
    (dict(consumer=False), "consumer_missing"),
    (dict(token_data={**FAKE_TOKEN, "schema": "thth.hatena-oauth/0"}), "token_invalid"),
    (dict(token_data={**FAKE_TOKEN, "hatena_id": "someone-else"}), "token_invalid"),
    (dict(token_data={k: v for k, v in FAKE_TOKEN.items() if k != "oauth_token_secret"}), "token_invalid"),
    (dict(consumer_data={**FAKE_CONSUMER, "schema": "x"}), "consumer_invalid"),
])
def test_T_S2_3a_factoryが作れなければ何も書かずexit2(blog, setup, code):
    import os
    for p in (str(blog.world.main_token), str(blog.world.main_token) + ".consumer"):
        os.unlink(p)
    put_secrets(blog.world, **setup)
    http = blog.install(Blog())
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("refused", code, 2)
    assert http.calls == [] and inflight.read(blog.state) is None
    assert states(delivery) == ["wanted"] and sent.records(blog.state) == []


def test_T_S2_3a_auth_wsseはauth_unsupported(blog):
    blog.world.edit_principal("hatena-main", auth="wsse")
    http = blog.install(Blog())
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("refused", "auth_unsupported", 2)
    assert http.calls == [] and states(delivery) == ["wanted"]


# ---------------------------------------------------------------- 公開・更新・削除の通し

def test_作成_更新_削除をAtomPubで1回ずつ(blog):
    fake = Blog()
    http = blog.install(fake)
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    assert http.methods() == [("GET", COLLECTION), ("POST", COLLECTION)]
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published" and record["entry_id"] == entry_id("99")
    assert record["edit_uri"] == f"{COLLECTION}/99"
    assert record["published_url"] == "https://a.hatenablog.com/entry/2026/09/09/100000"
    assert "readback_differs" not in record["history"][-1]
    # 更新: GET（読み戻し）→ GET（記法）→ PUT
    blog.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    approved(blog, "u.md", op="update", delivery=delivery)
    http.calls.clear()
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    assert http.methods() == [("GET", f"{COLLECTION}/99"), ("GET", COLLECTION), ("PUT", f"{COLLECTION}/99")]
    assert "直した本文。" in fake.entries["99"]
    # 削除: DELETE だけ
    approved(blog, "d.md", op="delete", delivery=delivery)
    http.calls.clear()
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    assert http.methods() == [("DELETE", f"{COLLECTION}/99")]
    assert deliveries.read(ACCOUNT, delivery)["state"] == "deleted" and fake.entries == {}


# ---------------------------------------------------------------- T-S2-3b

def test_T_S2_3b_URLはhatena_idとblog_idだけから_台帳のedit_uriが違えば送らない(blog):
    """柵: `_member` が台帳の edit_uri を使う（か比べない）と赤。"""
    fake = Blog()
    http = blog.install(fake)
    path, delivery = approved(blog)
    assert throw().action == "post"
    # 台帳の edit_uri を別のブログへ書き換える（台帳を信じて別のブログへ打たない）
    raw = deliveries.load(ACCOUNT, delivery)
    deliveries.advance(ACCOUNT, delivery, state="published", at="2026-09-09T10:05:00+09:00", base=raw,
                       fields={"edit_uri": "https://blog.hatena.ne.jp/evil/evil.hatenablog.com/atom/entry/99"})
    approved(blog, "d.md", op="delete", delivery=delivery)
    http.calls.clear()
    result = throw()
    assert result.error == "edit_uri_mismatch" and http.calls == []
    assert deliveries.read(ACCOUNT, delivery)["state"] == "published"
    assert inflight.read(blog.state) is None


def test_T_S2_3b_member_urlはentry_idの末尾の数字から(world):
    put_secrets(world)
    a = adapter_for(world)
    assert a.collection_url == COLLECTION
    assert a.member_url(entry_id("12345")) == f"{COLLECTION}/12345"
    for bad in ("12345", "tag:x-abc", "tag:x-", "tag:x-１２", None):
        with pytest.raises(ValueError):
            a.member_url(bad)


# ---------------------------------------------------------------- T-S2-3c

def test_T_S2_3c_送るXML(world, fake_http):
    """柵: `hatenablog:scheduled`（か updated・preview・custom-url）を足すと赤。"""
    put_secrets(world)
    http = fake_http(blog_handler())
    a = adapter_for(world)
    result = a.create_entry(title="題 & <x>", body="本文\n\n- a", categories=["コーヒー", "価格"], images=[])
    assert result.ok, result
    body = http.calls[0]["body"]
    assert body.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
    import xml.etree.ElementTree as ET
    root = ET.fromstring(body)
    assert root.tag == "{%s}entry" % ATOM
    tags = [child.tag for child in root.iter()]
    for absent in ("updated", "scheduled", "preview", "custom-url", "id"):
        assert not [t for t in tags if t.endswith("}" + absent)], absent
    content = root.find("{%s}content" % ATOM)
    assert content.get("type") == "text/plain" and content.text == "本文\n\n- a"
    assert root.find("{%s}title" % ATOM).text == "題 & <x>"
    assert root.find("{%s}author/{%s}name" % (ATOM, ATOM)).text == HATENA_ID
    assert [c.get("term") for c in root.findall("{%s}category" % ATOM)] == ["コーヒー", "価格"]
    assert root.find("{http://www.w3.org/2007/app}control/{http://www.w3.org/2007/app}draft").text == "no"


def test_T_S2_3c_手元の画像は送らずimages_unsupported(world, fake_http):
    put_secrets(world)
    http = fake_http(blog_handler())
    result = adapter_for(world).create_entry(title="t", body="b", categories=[], images=[{"path": "x.png"}])
    assert (result.ok, result.ambiguous, result.error) == (False, False, "images_unsupported")
    assert http.calls == []


# ---------------------------------------------------------------- T-S2-3d

def test_T_S2_3d_応答の読み():
    raw = entry_xml(num="7", content="本文\n", title="題", categories=["a", "b"])
    got = hatenablog.parse_entry(raw)
    assert got["entry_id"] == entry_id("7")
    assert got["edit_uri"] == f"{COLLECTION}/7"
    assert got["published_url"] == "https://a.hatenablog.com/entry/2026/09/09/100000"
    assert (got["content"], got["content_type"], got["draft"]) == ("本文\n", "text/x-markdown", "no")
    assert (got["title"], got["categories"]) == ("題", ["a", "b"])
    assert got["published"] and got["edited"]


@pytest.mark.parametrize("raw", [
    entry_xml(doctype=True),
    feed_xml([entry_xml()]),            # 根が entry でない
    b"<entry><broken",
    b"",
])
def test_T_S2_3d_読めない応答はresponse_invalid(raw):
    with pytest.raises(hatenablog.ResponseInvalid):
        hatenablog.parse_entry(raw)


def test_T_S2_3d_alternateにtext_htmlが無ければ最初のalternate():
    raw = entry_xml(with_alternate=False).replace(
        b'<author>', b'<link rel="alternate" href="https://a.hatenablog.com/x"/><author>')
    assert hatenablog.parse_entry(raw)["published_url"] == "https://a.hatenablog.com/x"


# ---------------------------------------------------------------- T-S2-3e

def _boom(exc):
    def f(_body):
        raise exc
    return f


CASES = [
    # (POST の答え, 台帳の最後, inflight が残るか, error)
    ((400, b"bad"), "wanted", False, "hatena_http_400"),
    ((401, b"no"), "wanted", False, "hatena_auth_rejected"),
    ((403, b"no"), "wanted", False, "hatena_auth_rejected"),
    ((404, b"no"), "wanted", False, "hatena_http_404"),
    ((405, b"no"), "wanted", False, "hatena_http_405"),
    ((429, b"slow"), "wanted", False, "provider_rate_limited"),
    ((500, b"oops"), "unknown", True, "hatena_http_500"),
    ((503, b"oops"), "unknown", True, "hatena_http_503"),
    ((201, entry_xml(draft="yes")), "unknown", True, "hatena_draft_unexpected"),
    ((201, b"<not xml"), "unknown", True, "response_invalid"),
    ((201, entry_xml(with_alternate=False)), "unknown", True, "response_invalid"),
    ((200, entry_xml()), "unknown", True, "hatena_http_200"),
    (_boom(TimeoutError("slow")), "unknown", True, "hatena_transport_failed"),
    (_boom(urllib.error.URLError("down")), "unknown", True, "hatena_transport_failed"),
    (_boom(ConnectionResetError("reset")), "unknown", True, "hatena_transport_failed"),
    (lambda _b: (201, b"<" * (hatenablog.MAX_RESPONSE_BYTES + 10)), "unknown", True, "response_too_large"),
    # 3xx を返したあと（要求は届いている）＝出たか分からない（監査 中-1）。接続の前の断りとは分ける。
    (_boom(httpsafe.RedirectBlocked(COLLECTION, 302, "redirect", {}, None)), "unknown", True, "redirect_blocked"),
    (_boom(httpsafe.EndpointRejected("endpoint_invalid")), "wanted", False, "endpoint_rejected"),
]


@pytest.mark.parametrize("answer, last, held, error", CASES)
def test_T_S2_3e_断りと結果不明の分け方(blog, answer, last, held, error):
    """柵: 5xx を「出ていない」（ambiguous=False）にすると 500・503 の行が赤。"""
    blog.install(blog_handler(post=answer))
    path, delivery = approved(blog)
    result = throw()
    assert result.error == ("publish_ambiguous" if held else error), result
    assert states(delivery)[-1] == last
    assert deliveries.read(ACCOUNT, delivery)["history"][-1].get("error") == error
    assert (inflight.read(blog.state) is not None) is held
    assert sent.records(blog.state) == []
    assert fm_of(path)["status"] == "approved"


def test_T_S2_3e_401はprincipal_auth_forceを案内(blog):
    blog.install(blog_handler(post=(401, b"")))
    approved(blog)
    lines = []
    result = core.throw_once(ACCOUNT, production_flag=True, now=NOW, log=lines.append)
    assert result.error == "hatena_auth_rejected"
    assert any("thth principal auth hatena-main --force" in line for line in lines), lines


def _published(blog, fake):
    blog.install(fake)
    path, delivery = approved(blog)
    assert throw().action == "post"
    return delivery


def test_T_S2_3e_更新の前のGETが404ならconflict_entry_missing(blog):
    fake = Blog()
    delivery = _published(blog, fake)
    fake.entries.clear()           # はてなの画面で消された
    blog.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    approved(blog, "u.md", op="update", delivery=delivery)
    http = blog.install(fake)
    result = throw()
    assert result.action == "conflict" and result.message == "entry_missing"
    assert [m for m, _u in http.methods()] == ["GET"]
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "conflict" and record["history"][-1]["reason"] == "entry_missing"
    assert inflight.read(blog.state) is None


def test_T_S2_3e_削除が404ならconflict_entry_missingで打ち直さない(blog):
    """柵: 404 を S1 の BEFORE（published に戻す）のままにすると赤。"""
    fake = Blog()
    delivery = _published(blog, fake)
    fake.entries.clear()
    approved(blog, "d.md", op="delete", delivery=delivery)
    http = blog.install(fake)
    result = throw()
    assert result.action == "conflict" and result.message == "entry_missing"
    assert http.methods() == [("DELETE", f"{COLLECTION}/99")]
    record = deliveries.read(ACCOUNT, delivery)
    assert states(delivery)[-2:] == ["inflight", "conflict"]
    assert record["history"][-1]["reason"] == "entry_missing"
    assert inflight.read(blog.state) is None
    http.calls.clear()
    assert throw().action == "none" and http.calls == []     # 次の run は打たない


def test_T_S2_3e_更新の5xxはunknown(blog):
    fake = Blog()
    delivery = _published(blog, fake)
    blog.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    approved(blog, "u.md", op="update", delivery=delivery)

    def handler(method, url, headers, body):
        if method == "PUT":
            return 502, {}, b""
        return fake(method, url, headers, body)
    blog.install(handler)
    result = throw()
    assert result.error == "publish_ambiguous"
    assert states(delivery)[-1] == "unknown" and inflight.read(blog.state) is not None


# ---------------------------------------------------------------- T-S2-3f

def test_T_S2_3f_ヘッダとエラーの文に秘密が無い(world, fake_http):
    put_secrets(world)
    world.edit_principal("hatena-main", user_agent_contact="https://example.invalid/contact")
    http = fake_http(blog_handler(post=_boom(OSError("reset by " + FAKE_TOKEN["oauth_token_secret"]))))
    from thth import __version__, principals
    p = principals.load_principal("hatena-main")
    a = hatenablog.factory({"account": ACCOUNT, "blog_id": BLOG, "token": p["token"], "media": "hatenablog"}, p)
    result = a.create_entry(title="t", body="b", categories=[], images=[])
    assert result.ambiguous and result.error == "hatena_transport_failed"
    h = http.calls[0]["headers"]
    assert h["user-agent"] == f"thth/{__version__} (+https://example.invalid/contact)"
    assert h["accept"] == "application/atom+xml"
    assert h["content-type"] == "application/atom+xml; charset=utf-8"
    fields = oauth1.parse_header(h["authorization"])
    assert fields["oauth_token"] == FAKE_TOKEN["oauth_token"]
    assert fields["oauth_consumer_key"] == FAKE_CONSUMER["consumer_key"]
    assert fields["realm"] == "" and fields["oauth_version"] == "1.0"
    # 署名は URL だけ（XML の body は入れない）で検証できる
    params = [(k, v) for k, v in fields.items() if k not in ("realm", "oauth_signature")]
    assert fields["oauth_signature"] == oauth1.sign_hmac_sha1(
        oauth1.base_string("POST", COLLECTION, params), FAKE_CONSUMER["consumer_secret"],
        FAKE_TOKEN["oauth_token_secret"])
    for secret in (FAKE_TOKEN["oauth_token_secret"], FAKE_CONSUMER["consumer_secret"]):
        assert secret not in a._scrub("x " + secret + " y")
        assert secret not in json.dumps(h["authorization"])
    assert http.calls[0]["timeout"] == 60


def test_T_S2_3f_時間切れは読む30秒_書く60秒_口座のtimeoutが短ければそちら(world, fake_http):
    put_secrets(world)
    http = fake_http(blog_handler())
    a = adapter_for(world)
    a.blog_syntax()
    a.create_entry(title="t", body="b", categories=[], images=[])
    assert [c["timeout"] for c in http.calls] == [30, 60]
    a.timeout = 10
    a.blog_syntax()
    assert http.calls[-1]["timeout"] == 10


# ---------------------------------------------------------------- T-S2-3g

def test_T_S2_3g_5xxのあと同じrunで2回目のPOSTが無い(blog):
    http = blog.install(blog_handler(post=(500, b"")))
    approved(blog)
    approved(blog, "b.md")
    throw()
    assert [m for m, _u in http.methods()].count("POST") == 1


# ---------------------------------------------------------------- doctor（§3.7）

def test_doctorはfactoryとprobeを見せる(blog):
    blog.install(Blog())
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    report = json.loads(out)
    (probe,) = report["probes"]
    assert probe["key"] == "atom_entries" and probe["ok"] is True
    assert probe["entries"] == 1 and probe["syntax"] == "text/x-markdown"
    assert FAKE_TOKEN["oauth_token_secret"] not in out


@pytest.mark.parametrize("status, needle", [(401, "--force"), (404, "blog_id")])
def test_doctorの401と404は次の一手(blog, status, needle):
    blog.install(lambda m, u, h, b: (status, {}, b""))
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    (probe,) = json.loads(out)["probes"]
    assert probe["ok"] is False and needle in probe["detail"] and rc == 1


def test_doctorはtokenが無ければprincipal_authを案内(blog):
    import os
    os.unlink(str(blog.world.main_token))
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    report = json.loads(out)
    assert "token_missing" in report["error"] and "thth principal auth hatena-main" in report["error"]
    assert rc != 0 and report["probes"] == []


def test_doctorは記法がMarkdownでなければ失敗の印(blog):
    blog.install(Blog(content_type="text/x-hatena-syntax"))
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    (probe,) = json.loads(out)["probes"]
    assert probe["ok"] is False and "blog_syntax_mismatch" in probe["detail"]
