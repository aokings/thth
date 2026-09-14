"""リダイレクトでトークンが別ホストへ持ち越されない（監査 2026-09-14・P1-1）。

**見つかり方**: アダプタは `urllib.request.urlopen()` を素で呼んでいた。既定の
opener は 3xx を黙って追い、`Authorization` ヘッダを**別ホストへもそのまま**
持って行く。乗っ取られたインスタンス・間に入った proxy・DNS を握られた経路の
どれでも、`302 Location: https://attacker/` の 1 行でトークンが外へ出る。

ここで確かめるのは 2 つだけ:
  1. 追わずに **loud に失敗する**（黙って 0 件・黙って成功にしない）。
  2. 2 本目のサーバに `Authorization` も `access_token` も**届いていない**。
"""
from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from tests.helpers.fake_redirect_server import recording_server, redirect_pair
from thth import httpsafe
from thth.adapters import bluesky as bluesky_mod
from thth.adapters import mastodon as mastodon_mod
from thth.adapters import threads as threads_mod

FAKE_TOKEN = "FAKE-TOKEN-never-a-real-secret"


def _no_secret_reached(received: list) -> None:
    for req in received:
        assert req["authorization"] is None, f"Authorization が届いた: {req}"
        assert FAKE_TOKEN not in req["path"], f"トークンが URL で届いた: {req}"


# ------------------------------------------------------------------ Mastodon

def test_mastodonは別ホストへのリダイレクトを追わずAuthorizationを渡さない():
    with redirect_pair() as (origin, elsewhere, received):
        adapter = mastodon_mod.MastodonAdapter(instance=origin, access_token=FAKE_TOKEN)
        with pytest.raises(mastodon_mod.AdapterError) as e:
            adapter.whoami()
    assert "***" not in str(e.value) or FAKE_TOKEN not in str(e.value)
    assert FAKE_TOKEN not in str(e.value)
    # **1 本目で止まっている**（2 本目には 1 件も届いていない）。
    assert received == [], received


# ------------------------------------------------------------------ Bluesky

def test_blueskyは別ホストへのリダイレクトを追わずbearerを渡さない():
    with redirect_pair() as (origin, elsewhere, received):
        with pytest.raises((urllib.error.HTTPError, urllib.error.URLError)):
            bluesky_mod._xrpc(origin, "GET", "com.atproto.server.getSession",
                              bearer=FAKE_TOKEN, timeout=5)
    assert received == [], received


# ------------------------------------------------------------------ Threads

def test_threadsは別ホストへのリダイレクトを追わずaccess_tokenを渡さない():
    with redirect_pair() as (origin, elsewhere, received):
        adapter = threads_mod.ThreadsAdapter(base_url=origin, access_token=FAKE_TOKEN,
                                             user_id="1", timeout=5)
        with pytest.raises((urllib.error.HTTPError, urllib.error.URLError)):
            adapter._get("/v1.0/me", {"fields": "id,username"})
    assert received == [], received
    _no_secret_reached(received)


# ------------------------------------------------------- 追い方の細かいところ

def test_相対のLocationでも別ホストなら追わない():
    """`Location: //host/…`（scheme 相対）は文字列で見ると別ホストに見えない。"""
    with recording_server() as (elsewhere, received):
        host = elsewhere.split("//", 1)[1]
        with redirect_pair(location=f"//{host}/leaked") as (origin, _, _received):
            req = urllib.request.Request(origin + "/x",
                                          headers={"Authorization": f"Bearer {FAKE_TOKEN}"})
            with pytest.raises(urllib.error.HTTPError):
                httpsafe.urlopen(req, timeout=5)
    assert received == [], received


def test_同じホストの中のリダイレクトはこれまでどおり追う():
    """**既存の経路の意味を変えない**——同一 origin の 302 は今までどおり追う。"""
    import contextlib
    import http.server
    import json
    import threading

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/first":
                self.send_response(302)
                self.send_header("Location", "/second")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        req = urllib.request.Request(base + "/first",
                                      headers={"Authorization": f"Bearer {FAKE_TOKEN}"})
        with contextlib.closing(httpsafe.urlopen(req, timeout=5)) as resp:
            assert json.loads(resp.read())["ok"] is True
    finally:
        server.shutdown()
        t.join(timeout=5)
    assert [p for p, _ in seen] == ["/first", "/second"]
    # 同じホストなので `Authorization` は持ち越してよい（そこは変えない）。
    assert seen[1][1] == f"Bearer {FAKE_TOKEN}"


def test_httpsからhttpへの格下げは同じホストでも追わない():
    """https → http の格下げは同一 host でも拒む（平文に載せ替えさせない）。"""
    handler = httpsafe.SameOriginRedirectHandler()

    class FakeReq:
        full_url = "https://example.test/a"

    with pytest.raises(httpsafe.RedirectBlocked):
        handler.redirect_request(FakeReq(), None, 302, "Found", {},
                                 "http://example.test/a")
    # 同一 origin・同じ scheme なら通す（`None` ではなく `Request` が返る）。
    real = urllib.request.Request("https://example.test/a")
    got = handler.redirect_request(real, None, 302, "Found", {},
                                   "https://example.test/b")
    assert got is not None and got.full_url == "https://example.test/b"
