"""4.0 S2 の試験（`tests/test_v400_s2_*.py`）が共有する、はてなの fake の HTTP と応答の XML。

`thth.httpsafe.urlopen` を差し替える（**本物のはてなには 1 回も出ない**。`THTH_TEST_ALLOW_HTTP` も
使わない）。呼ばれた順・method・URL・ヘッダ（小文字の鍵）・body を `calls` に残す。

秘密の値は**明らかな偽物**（`fake-…`）だけを置く。
"""
from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

import pytest

ATOM = "http://www.w3.org/2005/Atom"
APP = "http://www.w3.org/2007/app"
HATENA_ID = "lane-owner"
BLOG = "a.hatenablog.com"
FAKE_TOKEN = {"schema": "thth.hatena-oauth/1", "hatena_id": HATENA_ID,
              "oauth_token": "fake-oauth-token-0001", "oauth_token_secret": "fake-oauth-token-secret-0002",
              "scopes": ["read_public", "write_public", "read_private", "write_private"],
              "obtained_at": "2026-09-01T10:00:00+09:00"}
FAKE_CONSUMER = {"schema": "thth.hatena-consumer/1", "consumer_key": "fake-consumer-key-0003",
                 "consumer_secret": "fake-consumer-secret-0004"}
COLLECTION = f"https://blog.hatena.ne.jp/{HATENA_ID}/{BLOG}/atom/entry"


def entry_id(num="99", hatena_id=HATENA_ID):
    return f"tag:blog.hatena.ne.jp,2013:blog-{hatena_id}-17680117127000000000-{num}"


def entry_xml(*, num="99", content="本文", content_type="text/x-markdown", draft="no", title="題",
              blog=BLOG, hatena_id=HATENA_ID, alternate=None, with_alternate=True, eid=None,
              doctype=False, categories=()) -> bytes:
    """公式の例の形の 1 本（`id`・`link rel=edit`・`link rel=alternate type=text/html`・`content`・`app:control`）。"""
    edit = f"https://blog.hatena.ne.jp/{hatena_id}/{blog}/atom/entry/{num}"
    alt = alternate or f"https://{blog}/entry/2026/09/09/100000"
    cats = "".join(f"<category term={quoteattr(c)} />" for c in categories)
    head = '<?xml version="1.0" encoding="utf-8"?>\n'
    if doctype:
        head += '<!DOCTYPE entry [<!ENTITY x "y">]>\n'
    return (head + f'<entry xmlns="{ATOM}" xmlns:app="{APP}">'
            f"<id>{escape(eid or entry_id(num, hatena_id))}</id>"
            f'<link rel="edit" href="{edit}"/>'
            + (f'<link rel="alternate" type="text/html" href="{alt}"/>' if with_alternate else "")
            + f"<author><name>{hatena_id}</name></author>"
            f"<title>{escape(title)}</title><updated>2026-09-09T10:00:00+09:00</updated>"
            f"<published>2026-09-09T10:00:00+09:00</published><app:edited>2026-09-09T10:00:00+09:00</app:edited>"
            f"<summary type=\"text\">s</summary>"
            f"<content type={quoteattr(content_type)}>{escape(content)}</content>"
            f'<hatena:formatted-content type="text/html" xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#">'
            f"&lt;p&gt;x&lt;/p&gt;</hatena:formatted-content>"
            f"{cats}<app:control><app:draft>{draft}</app:draft></app:control></entry>").encode("utf-8")


def feed_xml(entries=(), *, next_url=None) -> bytes:
    """一覧（`feed`）。`entries` は `entry_xml` の bytes の並び（先頭が最新）。"""
    body = "".join(re.sub(r"^<\?xml[^>]*\?>\s*", "", e.decode("utf-8")) for e in entries)
    nxt = f'<link rel="next" href="{next_url}"/>' if next_url else ""
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="{ATOM}" xmlns:app="{APP}">'
            f"<title>blog</title>{nxt}{body}</feed>").encode("utf-8")


def markdown_feed():
    return feed_xml([entry_xml(num="1", content="前の記事")])


def sent_content(body: bytes) -> str:
    """送った XML の `content` の文字列。"""
    root = ET.fromstring(body)
    return root.find("{%s}content" % ATOM).text or ""


class _Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = _Headers(headers or {})
        self._body = io.BytesIO(body or b"")

    def read(self, n=-1):
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Headers(dict):
    def items(self):  # noqa: D401 — email.message.Message と同じ口
        return list(super().items())


class FakeHTTP:
    """`handler(method, url, headers, body) -> (status, headers, bytes)`（か例外を上げる）。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, req, *, timeout):
        headers = {k.lower(): v for k, v in req.header_items()}
        call = {"method": req.get_method(), "url": req.full_url, "headers": headers, "body": req.data,
                "timeout": timeout}
        self.calls.append(call)
        status, out_headers, body = self.handler(call["method"], call["url"], headers, req.data)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "fake", out_headers or {}, io.BytesIO(body or b""))
        return _Response(status, out_headers, body)

    def methods(self):
        return [(c["method"], c["url"]) for c in self.calls]


@pytest.fixture
def fake_http(monkeypatch):
    """`install(handler) -> FakeHTTP`。差し替えるのは `httpsafe.urlopen` だけ。"""
    from thth import httpsafe
    from tests.conftest import restore_real_hatena_adapters

    def install(handler):
        fake = FakeHTTP(handler)
        monkeypatch.setattr(httpsafe, "urlopen", fake)
        # 偽物の HTTP を置いたので、本物の adapter を作ってよい（既定の柵を外す・4.0 S4 監査 低-3）。
        restore_real_hatena_adapters(monkeypatch)
        # 公開の読む口（4.0 S6）の柵を外すのは偽物の HTTP を置いたときだけ（S6 監査 中-4）。
        from thth import hatena_reactions
        monkeypatch.setattr(hatena_reactions, "FETCH", None)
        return fake
    return install


def put_secrets(world, *, token=True, consumer=True, token_data=None, consumer_data=None):
    """主体 `hatena-main` の token と consumer を（偽物の値で）0600 で置く。"""
    path = str(world.main_token)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if token:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(token_data or FAKE_TOKEN, f)
        os.chmod(path, 0o600)
    if consumer:
        with open(path + ".consumer", "w", encoding="utf-8") as f:
            json.dump(consumer_data or FAKE_CONSUMER, f)
        os.chmod(path + ".consumer", 0o600)
    return path


def blog_handler(*, post=None, put=None, delete=None, get=None, feed=None):
    """AtomPub の口ごとの答え。各引数は `(status, bytes)` か `callable(body) -> (status, bytes)`。

    既定: 一覧は Markdown の記事 1 本・POST は送った本文をそのまま返す 201。
    """
    def pick(value, body, default):
        value = default if value is None else value
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(body)
        if isinstance(value, BaseException):
            raise value
        status, raw = value
        return status, {"Content-Type": "application/atom+xml"}, raw

    def echo_created(body):
        return 201, entry_xml(content=sent_content(body))

    def echo_updated(body):
        return 200, entry_xml(content=sent_content(body))

    def handler(method, url, headers, body):
        if method == "GET" and url == COLLECTION:
            return pick(feed, body, (200, markdown_feed()))
        if method == "POST" and url == COLLECTION:
            return pick(post, body, echo_created)
        if method == "PUT":
            return pick(put, body, echo_updated)
        if method == "DELETE":
            return pick(delete, body, (200, b""))
        if method == "GET":
            return pick(get, body, (200, entry_xml(content="今の本文")))
        raise AssertionError(f"unexpected {method} {url}")
    return handler
