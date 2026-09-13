"""リダイレクトでトークンが別ホストへ漏れるかを試す偽サーバ 2 本（127.0.0.1）。

セキュリティ監査 2026-09-14・P1-1 の再現（`redirect_leak.py`）と同じ型:

  - **1 本目**（`origin`）はどの口に来ても `302 Location: <2 本目>` を返す。
  - **2 本目**（`elsewhere`）は受け取った要求を `received` に積む
    （`Authorization` ヘッダ・クエリ文字列を含む）。

本物の媒体（graph.threads.net・mastodon の実インスタンス・bsky.social）には
一切触らない。トークンは**偽の値**（`FAKE-TOKEN-…`）。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading


def _make_recorder(received: list):
    class Recorder(http.server.BaseHTTPRequestHandler):
        def _record_and_reply(self):
            received.append({
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "headers": {k.lower(): v for k, v in self.headers.items()},
            })
            body = json.dumps({"id": "999999", "username": "leaked"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _record_and_reply
        do_POST = _record_and_reply

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Recorder


def _make_redirector(target: dict, status: int, location=None):
    class Redirector(http.server.BaseHTTPRequestHandler):
        def _redirect(self):
            where = location if location is not None else target["base"] + self.path
            self.send_response(status)
            self.send_header("Location", where)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _redirect
        do_POST = _redirect

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Redirector


@contextlib.contextmanager
def redirect_pair(*, status: int = 302, location=None):
    """`(origin_base, elsewhere_base, received)` を返す。

    `location` を渡すと 1 本目はその値をそのまま `Location` に入れる
    （相対 `//host/...` や `http://` への格下げを試すため）。
    """
    received: list = []
    target = {"base": ""}

    elsewhere = http.server.HTTPServer(("127.0.0.1", 0), _make_recorder(received))
    target["base"] = f"http://127.0.0.1:{elsewhere.server_port}"
    origin = http.server.HTTPServer(("127.0.0.1", 0),
                                     _make_redirector(target, status, location))

    threads = []
    for server in (elsewhere, origin):
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        threads.append(t)
    try:
        yield (f"http://127.0.0.1:{origin.server_port}", target["base"], received)
    finally:
        for server in (elsewhere, origin):
            server.shutdown()
        for t in threads:
            t.join(timeout=5)


@contextlib.contextmanager
def recording_server():
    """記録だけする 1 本（リダイレクトしない口の対照実験用）。"""
    received: list = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _make_recorder(received))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield (f"http://127.0.0.1:{server.server_port}", received)
    finally:
        server.shutdown()
        t.join(timeout=5)
