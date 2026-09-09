"""`thth auth` / `thth refresh` 用の偽 Threads OAuth API（`http.server`）。

本物の `graph.threads.net` には一切触れない。`behavior` 辞書でエンドポイントごとの
応答を差し替える:
  - "short"（POST /oauth/access_token）
  - "long"（GET /access_token）
  - "me"（GET /v1.0/me）
  - "refresh"（GET /refresh_access_token）
値は "ok" | "4xx" | "5xx" | "no_token"。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading
import urllib.parse


class _Handler(http.server.BaseHTTPRequestHandler):
    behavior = {"short": "ok", "long": "ok", "me": "ok", "refresh": "ok"}
    short_token = "SHORT-SECRET-TOKEN"
    long_token = "LONG-SECRET-TOKEN"
    refreshed_token = "REFRESHED-SECRET-TOKEN"
    me_payload = {"id": "999999", "username": "nigamilab"}
    expires_in = 5184000

    def _respond_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_for(self, mode: str, ok_payload: dict) -> None:
        if mode == "ok":
            self._respond_json(200, ok_payload)
        elif mode == "no_token":
            self._respond_json(200, {})
        elif mode == "4xx":
            self._respond_json(400, {"error": {"message": "bad request (fake)"}})
        elif mode == "5xx":
            self._respond_json(500, {"error": {"message": "server error (fake)"}})
        else:
            self._respond_json(500, {"error": {"message": f"unknown mode {mode}"}})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/oauth/access_token":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            params = dict(urllib.parse.parse_qsl(body))
            self._last_params = params
            self._respond_for(self.behavior.get("short", "ok"),
                               {"access_token": self.short_token, "user_id": "999999"})
        else:
            self._respond_json(404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path == "/access_token":
            self._respond_for(self.behavior.get("long", "ok"),
                               {"access_token": self.long_token, "token_type": "bearer",
                                "expires_in": self.expires_in})
        elif parsed.path == "/v1.0/me":
            self._respond_for(self.behavior.get("me", "ok"), dict(self.me_payload))
        elif parsed.path == "/refresh_access_token":
            self._respond_for(self.behavior.get("refresh", "ok"),
                               {"access_token": self.refreshed_token, "token_type": "bearer",
                                "expires_in": self.expires_in})
        else:
            self._respond_json(404, {"error": "not found"})

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def fake_oauth_server(behavior: dict | None = None):
    handler_cls = type("Handler", (_Handler,), {"behavior": dict(behavior or {})})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
