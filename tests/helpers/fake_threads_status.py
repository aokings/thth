"""container の status を答える偽 Threads（設計 3.3.1 §6・**tests の中だけ**）。

本物の Threads API には触らない。`http.server` で次の 4 つの口だけを真似る:

- `POST /<user>/threads`         → container を作る（id は `CONTAINER_ID`）
- `POST /<user>/threads_publish` → `script.publish` の先頭から 1 つずつ答える
- `GET  /<container>`            → `script.status` の先頭から 1 つずつ（最後の 1 つは繰り返す）
- `GET  /v1.0/<user>/threads`    → `script.posts`（自分の最近の投稿）

何回呼ばれたかを数える（`publish_calls`・`status_calls`・`list_calls`）——
「2 度出していない」を回数で確かめるため。
"""
from __future__ import annotations

import contextlib
import dataclasses
import http.server
import json
import threading
import urllib.parse

from thth.adapters import threads as threads_mod

USER_ID = "12345"
CONTAINER_ID = "900"


@dataclasses.dataclass
class Script:
    # 公開の答え: ("ok", id) / (500, None) / (400, None) / ("drop", None)（接続断）
    publish: list = dataclasses.field(default_factory=lambda: [("ok", "777")])
    # container の status の答え: "FINISHED" などの語 / 数字（HTTP の失敗）
    status: list = dataclasses.field(default_factory=lambda: ["FINISHED"])
    # 自分の最近の投稿（`GET /v1.0/<user>/threads` の data）
    posts: list = dataclasses.field(default_factory=list)
    list_status: int = 200
    publish_calls: int = 0
    status_calls: int = 0
    list_calls: int = 0
    create_calls: int = 0
    # 公開の要求に載っていた creation_id（どの container を公開したか）。
    publish_ids: list = dataclasses.field(default_factory=list)


def _handler(script: Script):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if self.path.endswith("/threads_publish"):
                script.publish_calls += 1
                params = urllib.parse.parse_qs(raw.decode("utf-8"))
                script.publish_ids.append((params.get("creation_id") or [None])[0])
                kind, value = (script.publish.pop(0) if script.publish else (500, None))
                if kind == "ok":
                    self._json(200, {"id": value})
                elif kind == "drop":
                    # 応答を返さずに切る（接続断）。
                    self.close_connection = True
                    self.connection.close()
                else:
                    self._json(kind, {"error": {"message": "fake", "code": 1,
                                                "is_transient": True}})
                return
            script.create_calls += 1
            self._json(200, {"id": CONTAINER_ID})

        def do_GET(self):  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path == f"/v1.0/{USER_ID}/threads":
                script.list_calls += 1
                if script.list_status != 200:
                    self._json(script.list_status, {"error": {"message": "fake"}})
                    return
                self._json(200, {"data": list(script.posts)})
                return
            if path == f"/{CONTAINER_ID}":
                script.status_calls += 1
                answer = script.status.pop(0) if len(script.status) > 1 else script.status[0]
                if isinstance(answer, int):
                    self._json(answer, {"error": {"message": "fake"}})
                else:
                    self._json(200, {"status": answer, "id": CONTAINER_ID})
                return
            self._json(404, {"error": {"message": "not found"}})

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Handler


@contextlib.contextmanager
def serve(script: Script):
    server = http.server.HTTPServer(("127.0.0.1", 0), _handler(script))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def adapter(base_url: str, **kwargs) -> threads_mod.ThreadsAdapter:
    kwargs.setdefault("wait_seconds", 0)
    kwargs.setdefault("timeout", 2.0)
    kwargs.setdefault("status_poll_seconds", 0)
    return threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake-token",
                                       user_id=USER_ID, **kwargs)


def post_row(post_id: str, text: str, timestamp: str) -> dict:
    return {"id": post_id, "text": text, "timestamp": timestamp,
            "permalink": f"https://www.threads.net/@x/post/{post_id}"}
