"""v2.1-B（承認を通る書き込みの口）用の偽 Threads API（`http.server`）。

本物の `graph.threads.net` には一切触れない。受けた要求を**種類ごとに数える**
——特に **DELETE を受けた回数**（`deletes`）が、取り下げのテストの証拠になる。

`behavior`:
  - "create"   : "ok" | "permission"（403・`error.message` に permission）| "4xx"
  - "publish"  : "ok" | "5xx"
  - "delete"   : "ok" | "permission" | "4xx" | "no_success"（200 だが success 無し）
  - "location" : "ok" | "permission" | "empty"
  - "recent"   : "ok" | "absent"（404。URL を引けない経路の試験用）
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading
import urllib.parse

PERMISSION_BODY = {"error": {"message": "(#10) Application does not have permission "
                                        "for this action", "type": "OAuthException",
                             "code": 10}}


class _Handler(http.server.BaseHTTPRequestHandler):
    behavior: dict = {}
    counter = [1]
    create_params: list
    deletes: list          # DELETE を受けたパス（access_token は落として記録）
    locations: list        # /location_search に届いた params
    permalink = "https://www.threads.net/@fake/post/ABC"

    def _respond_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.endswith("/threads_publish"):
            mode = self.behavior.get("publish", "ok")
            if mode == "ok":
                _id = str(self.counter[0])
                self.counter[0] += 1
                self._respond_json(200, {"id": _id})
            else:
                self._respond_json(500, {"error": {"message": "server error (fake)"}})
            return
        if parsed.path.endswith("/threads"):
            params = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8")).items()}
            params.pop("access_token", None)
            self.__class__.create_params.append(params)
            mode = self.behavior.get("create", "ok")
            if mode == "ok":
                _id = str(self.counter[0])
                self.counter[0] += 1
                self._respond_json(200, {"id": _id})
            elif mode == "permission":
                self._respond_json(403, PERMISSION_BODY)
            else:
                self._respond_json(400, {"error": {"message": "bad request (fake)"}})
            return
        self._respond_json(404, {"error": {"message": "not found"}})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        self.__class__.deletes.append(parsed.path)
        mode = self.behavior.get("delete", "ok")
        media_id = parsed.path.rsplit("/", 1)[-1]
        if mode == "ok":
            self._respond_json(200, {"success": True, "deleted_id": media_id})
        elif mode == "permission":
            self._respond_json(403, PERMISSION_BODY)
        elif mode == "no_success":
            self._respond_json(200, {"deleted_id": media_id})
        else:
            self._respond_json(400, {"error": {"message": "bad request (fake)"}})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        params.pop("access_token", None)
        if parsed.path == "/v1.0/location_search":
            self.__class__.locations.append(params)
            mode = self.behavior.get("location", "ok")
            if mode == "permission":
                self._respond_json(403, PERMISSION_BODY)
            elif mode == "empty":
                self._respond_json(200, {"data": []})
            else:
                self._respond_json(200, {"data": [
                    {"id": "101", "name": "渋谷駅", "city": "渋谷区", "country": "JP"},
                    {"id": "102", "name": "渋谷駅前", "city": "渋谷区", "country": "JP"},
                    {"id": "103", "name": "渋谷ヒカリエ", "city": "渋谷区", "country": "JP"},
                    {"id": "104", "name": "渋谷スクランブル", "city": "渋谷区", "country": "JP"},
                    {"id": "105", "name": "渋谷パルコ", "city": "渋谷区", "country": "JP"},
                    {"id": "106", "name": "渋谷 6 件目（切り詰め確認）", "city": "渋谷区",
                     "country": "JP"},
                ]})
            return
        if parsed.path.endswith("/threads"):
            if self.behavior.get("recent", "ok") == "absent":
                self._respond_json(404, {"error": {"message": "not found"}})
            else:
                self._respond_json(200, {"data": [
                    {"id": "777", "permalink": self.permalink,
                     "timestamp": "2026-09-14T10:00:00+0000", "text": "出した本文"}]})
            return
        self._respond_json(404, {"error": {"message": "not found"}})

    def log_message(self, format, *args):  # noqa: A002
        pass


class _ServerURL(str):
    handler_cls: type


@contextlib.contextmanager
def fake_threads_writes_server(behavior: dict | None = None):
    handler_cls = type("Handler", (_Handler,),
                       {"behavior": dict(behavior or {}), "counter": [1],
                        "create_params": [], "deletes": [], "locations": []})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = _ServerURL(f"http://127.0.0.1:{server.server_port}")
        url.handler_cls = handler_cls
        yield url
    finally:
        server.shutdown()
        thread.join(timeout=5)
