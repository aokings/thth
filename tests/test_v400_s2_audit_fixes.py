"""4.0 S2 の監査（10/16）で見つかった穴の柵。番号は監査のまま。

  - 中-1: 書き込み（POST／PUT／DELETE）の `RedirectBlocked` は 3xx を返したあと＝要求は届いている
    → `unknown`（inflight を残す）。`EndpointRejected`（接続の前）は「出ていない」のまま。
  - 中-2: `thth inflight … resolve --published` で解いた create は GET 1 回で `readback_sha`・
    `edit_uri`・`published_url` を台帳に入れる（次の update が通る）。読めなければ今のまま＋1 行。
  - 中-3: `thth principal auth` の HTTP がつながらない（DNS・拒否・時間切れ・接続の前の断り）は
    traceback にせず `initiate_failed`／`token_failed` に「つながりません」。秘密は出ず token.lock は外れる。
  - 低-4: token のファイルが読めない（`OSError`）も `token_invalid`（timer の run を落とさない）。
  - 低-6: インライン HTML の `<a href="相対">` と `<相対>` は `link_base` があっても
    `relative_link_unresolvable`（変えずに断る）。
  - 低-8: `throw_chosen` の 0 段目で S2 の断りは action `refused`（`approval_stale` は承認が古いときだけ）。
  - 試験の穴 (a): 127.0.0.1 に立てた本物の HTTP サーバに、本物の urllib の経路で adapter を通す。

はてなには出ない（fake の HTTP か 127.0.0.1 のサーバ）。秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import urllib.error

import pytest

from tests.hatena_s1_flow_helpers import ACCOUNT, NOW, FakeAdapter, approve, call, factory_for, fm_of, lane  # noqa: F401
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import (BLOG, COLLECTION, FAKE_CONSUMER, FAKE_TOKEN, HATENA_ID, blog_handler,  # noqa: F401
                                  entry_id, entry_xml, fake_http, feed_xml, put_secrets, sent_content)
from tests.test_v400_s2_adapter import ARTICLE, Blog, adapter_for, approved, states, throw
from thth import accounts, deliveries, hatena_convert, hatena_publish, httpsafe, inflight, principal_cli, principals
from thth.adapters import hatenablog


@pytest.fixture
def blog(lane, fake_http):  # noqa: F811
    lane.write("articles/a.md", ARTICLE)
    put_secrets(lane.world)
    lane.install = fake_http
    return lane


def _raise(exc):
    def handler(method, url, headers, body):
        raise exc
    return handler


# ---------------------------------------------------------------- 中-1

@pytest.mark.parametrize("op", ["create", "update", "delete"])
def test_中1_書き込みのRedirectBlockedは出たか分からない(world, fake_http, op):
    """柵: `_write` の `RedirectBlocked` を ambiguous=False に戻すと赤。"""
    put_secrets(world)
    fake_http(_raise(httpsafe.RedirectBlocked(COLLECTION, 302, "redirect", {}, None)))
    a = adapter_for(world)
    if op == "create":
        result = a.create_entry(title="t", body="b", categories=[], images=[])
    elif op == "update":
        result = a.update_entry(entry_id=entry_id("5"), edit_uri=None, title="t", body="b", categories=[],
                                images=[])
    else:
        result = a.delete_entry(entry_id=entry_id("5"), edit_uri=None)
    assert (result.ok, result.ambiguous, result.error) == (False, True, "redirect_blocked")


@pytest.mark.parametrize("op", ["create", "update", "delete"])
def test_中1_接続の前の断りは出ていない(world, fake_http, op):
    put_secrets(world)
    fake_http(_raise(httpsafe.EndpointRejected("endpoint_invalid")))
    a = adapter_for(world)
    if op == "create":
        result = a.create_entry(title="t", body="b", categories=[], images=[])
    elif op == "update":
        result = a.update_entry(entry_id=entry_id("5"), edit_uri=None, title="t", body="b", categories=[],
                                images=[])
    else:
        result = a.delete_entry(entry_id=entry_id("5"), edit_uri=None)
    assert (result.ok, result.ambiguous, result.error) == (False, False, "endpoint_rejected")


# ---------------------------------------------------------------- 中-2

def _resolve(*flags):
    rc, out, err = call(["inflight", ACCOUNT, "resolve", *flags, "--by", "tester"])
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    return call(["inflight", ACCOUNT, "resolve", *flags, "--by", "tester", "--confirm", digest])


def _created_but_unknown(blog, fake):
    """POST は記事を作ったが 500 を返した（結果不明・inflight が残る）。"""
    def handler(method, url, headers, body):
        if method == "POST":
            fake(method, url, headers, body)
            return 500, {}, b""
        return fake(method, url, headers, body)
    blog.install(handler)
    path, delivery = approved(blog)
    assert throw().error == "publish_ambiguous"
    assert states(delivery)[-1] == "unknown" and inflight.read(blog.state) is not None
    return path, delivery


def test_中2_publishedで解いたcreateは読み戻しを台帳に入れ_次のupdateが通る(blog):
    """柵: resolve の GET（`_readback_for_resolve`）を外すと、最後の update が conflict で赤。"""
    fake = Blog()
    path, delivery = _created_but_unknown(blog, fake)
    http = blog.install(fake)
    rc, out, err = _resolve("--published", entry_id("99"))
    assert rc == 0, (out, err)
    assert http.methods() == [("GET", f"{COLLECTION}/99")]
    assert "readback_sha" not in err
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published" and record["entry_id"] == entry_id("99")
    assert record["edit_uri"] == f"{COLLECTION}/99"
    assert record["published_url"] == "https://a.hatenablog.com/entry/2026/09/09/100000"
    assert record["readback_sha"]
    assert fm_of(path)["status"] == "posted"
    # 次の update: 読み戻し（GET）→ 記法 → PUT。conflict にならない。
    blog.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    approved(blog, "u.md", op="update", delivery=delivery)
    http.calls.clear()
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    assert [m for m, _u in http.methods()] == ["GET", "GET", "PUT"]
    assert "直した本文。" in fake.entries["99"]


def test_中2_GETが失敗したら今のまま書き1行で知らせる(blog):
    fake = Blog()
    path, delivery = _created_but_unknown(blog, fake)

    def handler(method, url, headers, body):
        if method == "GET" and url != COLLECTION:
            return 503, {}, b""
        return fake(method, url, headers, body)
    blog.install(handler)
    rc, out, err = _resolve("--published", entry_id("99"))
    assert rc == 0, (out, err)
    assert "読み戻しが無いので止まります" in err and f"thth deliveries {ACCOUNT}" in err
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published" and record["entry_id"] == entry_id("99")
    assert "readback_sha" not in record and "edit_uri" not in record
    assert FAKE_TOKEN["oauth_token_secret"] not in out + err


def test_中2_adapterを作れなくても今のまま解ける(blog):
    fake = Blog()
    path, delivery = _created_but_unknown(blog, fake)
    os.unlink(str(blog.world.main_token))
    http = blog.install(fake)
    rc, out, err = _resolve("--published", entry_id("99"))
    assert rc == 0, (out, err)
    assert http.calls == [] and "thth deliveries" in err
    assert deliveries.read(ACCOUNT, delivery)["state"] == "published"


# ---------------------------------------------------------------- 中-3

NAME = "hatena-main"


class _Prompts:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, label):
        return self.answers.pop(0)


def _run_auth(**kw):
    out, err = [], []
    rc = principal_cli.run_auth(NAME, by="tester", out=out.append, err=err.append, isatty=lambda: True, **kw)
    return rc, "\n".join(out), "\n".join(err)


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(OSError("nodename nor servname provided: " + FAKE_CONSUMER["consumer_secret"])),
    ConnectionRefusedError("refused"),
    TimeoutError("timed out"),
    httpsafe.EndpointRejected("endpoint_proxy_forbidden"),
])
def test_中3_initiateがつながらなければinitiate_failedでtracebackにしない(world, fake_http, exc):
    """柵: `_http` の except を HTTPError だけに戻すと、例外が上がって赤。"""
    fake_http(_raise(exc))
    rc, out, err = _run_auth(prompts=_Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"]))
    assert rc == 1 and "initiate_failed" in err and "つながりません" in err
    for secret in (FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"]):
        assert secret not in out + err
    assert not os.path.exists(world.main_token)
    with principals.token_lock(NAME):   # token.lock は外れている
        pass


def test_中3_tokenの段でつながらなければtoken_failed(world, fake_http):
    def handler(method, url, headers, body):
        if url == principal_cli.INITIATE_URL:
            return 200, {}, (b"oauth_token=fake-request-token-0005&oauth_token_secret=fake-request-secret-0006"
                             b"&oauth_callback_confirmed=true")
        raise ConnectionResetError("reset fake-request-secret-0006")
    fake_http(handler)
    rc, out, err = _run_auth(prompts=_Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"],
                                              "fake-verifier-0009"))
    assert rc == 1 and "token_failed" in err and "つながりません" in err
    for secret in ("fake-request-secret-0006", "fake-verifier-0009", FAKE_CONSUMER["consumer_secret"]):
        assert secret not in out + err
    assert not os.path.exists(world.main_token)
    with principals.token_lock(NAME):
        pass


# ---------------------------------------------------------------- 低-4

def test_低4_tokenのファイルが読めなければtoken_invalidで何も書かない(blog):
    """柵: factory の except を ValueError だけに戻すと、OSError が上がって赤。"""
    os.unlink(str(blog.world.main_token))
    os.makedirs(str(blog.world.main_token))          # 読めない（ディレクトリ）
    http = blog.install(Blog())
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("refused", "token_invalid", 2)
    assert http.calls == [] and inflight.read(blog.state) is None
    assert states(delivery) == ["wanted"]


# ---------------------------------------------------------------- 低-6

BASE = "https://kopicha.example/"


@pytest.mark.parametrize("text", [
    '<a href="rel.md">前の記事</a>\n',
    "<A HREF='../x.md'>x</A>\n",
    "<a class=\"c\" href=/abs/page>x</a>\n",
    "詳しくは <../2026-09/old.md> を。\n",
    "<sub/page>\n",
])
def test_低6_HTMLのaと山括弧の相対リンクはlink_baseがあっても断る(text):
    """柵: `_refuse_unconverted_relatives` を外すと、変えないまま通って赤。"""
    with pytest.raises(hatena_convert.ConvertError) as exc:
        hatena_convert.render(text, article_source="articles/a.md", base_url=BASE)
    assert str(exc.value) == "relative_link_unresolvable"


def test_低6_相対でないHTMLと山括弧はそのまま通る():
    text = ('<a href="https://e.example/">e</a> <a href="#z">z</a> <https://e.example/q> <a@b.example> '
            "<br> </p> <hr/> <注意> [u](<x.md>) `<a href=\"rel.md\">` <!-- c -->\n")
    body, _images = hatena_convert.render(text, article_source="articles/a.md", base_url=BASE)
    assert body == ('<a href="https://e.example/">e</a> <a href="#z">z</a> <https://e.example/q> <a@b.example> '
                    "<br> </p> <hr/> <注意> [u](<https://kopicha.example/articles/x.md>) "
                    "`<a href=\"rel.md\">` <!-- c -->")
    assert hatena_convert.VERSION == "s2-rules-1"   # 断るだけ・通る本文の出力は変わらない


def test_低6_コードの囲みの中は触らない():
    text = '```html\n<a href="rel.md">x</a>\n<rel.md>\n```\n'
    assert hatena_convert.render(text, article_source="a.md", base_url=None)[0] == text.rstrip("\n")


# ---------------------------------------------------------------- 低-8

def test_低8_0段目のS2の断りはrefused_承認が古いときだけapproval_stale(lane):
    """柵: 0 段目の action を "approval_stale" 固定に戻すと赤。"""
    from tests.conftest import parse_verified
    lane.write("articles/a.md", ARTICLE)
    path = lane.queue()
    approve(path)
    lane.write("articles/a.md", "# アラビカ\n\n未 commit。\n", commit=False)
    cfg = accounts.load_account(ACCOUNT)
    adapter = FakeAdapter()
    logs = []
    result = hatena_publish.throw_chosen(ACCOUNT, cfg, lane.state, "run1", "production", parse_verified(path),
                                         NOW, logs.append, adapter_factory=factory_for(adapter))
    assert (result.action, result.error, result.exit_code) == ("refused", "article_not_committed", 2)
    assert adapter.calls == []
    # 直した記事を commit すると承認が古い＝approval_stale のまま
    lane.commit("articles/a.md")
    result = hatena_publish.throw_chosen(ACCOUNT, cfg, lane.state, "run1", "production", parse_verified(path),
                                         NOW, logs.append, adapter_factory=factory_for(adapter))
    assert (result.action, result.error) == ("approval_stale", "approval_stale")


# ---------------------------------------------------------------- 試験の穴 (a)

class _Server:
    """127.0.0.1 の本物の HTTP サーバ。`answers[(method, path)] = (status, headers, bytes)`。"""

    def __init__(self):
        self.answers = {}
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                server.requests.append({"method": self.command, "path": self.path, "body": body,
                                        "authorization": self.headers.get("Authorization")})
                status, headers, raw = server.answers.get((self.command, self.path), (404, {}, b"no"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = do_DELETE = _answer

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def test_穴a_本物のurllibの経路で一覧GET_POST201_POST500_POST302別ホスト_PUT200(world, monkeypatch):
    """柵: 中-1 の直しを戻すと 302 の段（unknown）が赤。`httpsafe.urlopen` は差し替えない。"""
    monkeypatch.setenv("THTH_TEST_ALLOW_HTTP", "1")
    put_secrets(world)
    with _Server() as srv:
        monkeypatch.setattr(hatenablog, "BASE", f"http://127.0.0.1:{srv.port}")
        a = adapter_for(world)
        coll = f"/{HATENA_ID}/{BLOG}/atom/entry"
        assert a.collection_url == f"http://127.0.0.1:{srv.port}{coll}"
        atom = {"Content-Type": "application/atom+xml"}
        # 一覧の GET
        srv.answers[("GET", coll)] = (200, atom, feed_xml([entry_xml(num="1", content="前")]))
        assert a.blog_syntax() == "text/x-markdown"
        # POST 201
        srv.answers[("POST", coll)] = (201, atom, entry_xml(num="7", content="本文"))
        created = a.create_entry(title="t", body="本文", categories=["c"], images=[])
        assert created.ok and created.entry_id == entry_id("7") and created.readback_body == "本文"
        assert sent_content(srv.requests[-1]["body"]) == "本文"
        assert srv.requests[-1]["authorization"].startswith("OAuth ")
        # POST 500 → 分からない
        srv.answers[("POST", coll)] = (500, {}, b"oops")
        failed = a.create_entry(title="t", body="b", categories=[], images=[])
        assert (failed.ok, failed.ambiguous, failed.error) == (False, True, "hatena_http_500")
        # POST 302（別ホスト）→ 追わない・要求は届いている＝分からない
        srv.answers[("POST", coll)] = (302, {"Location": f"http://localhost:{srv.port}/elsewhere"}, b"")
        before = len(srv.requests)
        moved = a.create_entry(title="t", body="b", categories=[], images=[])
        assert (moved.ok, moved.ambiguous, moved.error) == (False, True, "redirect_blocked")
        assert len(srv.requests) == before + 1 and srv.requests[-1]["method"] == "POST"
        assert not [r for r in srv.requests if r["path"] == "/elsewhere"]   # 追っていない
        # PUT 200
        srv.answers[("PUT", f"{coll}/7")] = (200, atom, entry_xml(num="7", content="直した"))
        updated = a.update_entry(entry_id=entry_id("7"), edit_uri=None, title="t", body="直した",
                                 categories=[], images=[])
        assert updated.ok and updated.readback_body == "直した"
        assert srv.requests[-1]["method"] == "PUT" and srv.requests[-1]["path"] == f"{coll}/7"
        for row in srv.requests:
            assert FAKE_TOKEN["oauth_token_secret"] not in (row["authorization"] or "")
            assert FAKE_CONSUMER["consumer_secret"] not in (row["authorization"] or "")
