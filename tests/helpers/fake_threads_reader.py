"""`GET /v1.0/<user_id>/threads` だけを返す偽サーバ（`thth account` の遠隔確認用）。

本物の `graph.threads.net` には触れない。`rows` に渡した投稿一覧をそのまま返す。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading


def make_handler(rows, status=200):
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"data": rows} if status == 200
                               else {"error": {"message": "だめ"}}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            pass

    return _Handler


@contextlib.contextmanager
def fake_threads_reader(rows, status=200):
    server = http.server.HTTPServer(("127.0.0.1", 0), make_handler(rows, status))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
