"""`thth doctor` が HTTP 200 の `error` を成功と表示しない（外部レビュー再々判定 N7・2026-09-12）。

**本物の Threads API には触らない。** 偽の HTTP サーバを立てて確かめる。

外部レビューの記述（そのまま）: 全 GET へ `{"error":{"message":"permission denied",
"code":10}}` を HTTP 200 で返す fixture を使うと、doctor は `returncode=0`、
**5 probe を ○**、返信 probe を「未投稿のため検査できない」扱いにした。

`thth/adapters/threads.py` の `_get()` には同じ直しが既に入っている
（HTTP 200 でも body に `error` があれば失敗として上げる）。doctor は自前の
`_get()` を持っていて、そちらには入っていなかった——同じ考え方を入れる。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading

from thth import doctor as doctor_mod


class _AllErrorHandler(http.server.BaseHTTPRequestHandler):
    """全 GET へ、HTTP 200 のまま `error` を含む本文を返す（外部レビューの fixture 通り）。"""

    def do_GET(self) -> None:  # noqa: N802（http.server の命名規則）
        payload = {"error": {"message": "permission denied", "code": 10}}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _AllErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _write_token(path, token="DOCTOR-N7-TOKEN"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999",
                   "username": "nigamilab", "scopes": None}, f)


def test_200のerrorを成功と表示しない(tmp_path, monkeypatch, isolated_account_factory):
    """**これが本題。** 全 probe が失敗として出ること（○が 1 つも無いこと）を確かめる。"""
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    with _server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        report = doctor_mod.diagnose(account["name"])

    assert report["probes"], "probe が 1 つも無い"
    assert all(p["ok"] is not True for p in report["probes"]), (
        f"HTTP 200 の error を成功（○）扱いにした probe がある: {report['probes']}"
    )


def test_投稿一覧が失敗したときに未投稿理由を出さない(tmp_path, monkeypatch,
                                          isolated_account_factory):
    """**理由が事実と違う表示を作らない。** 「自分の投稿一覧」probe が失敗したなら、
    返信 probe の理由は「投稿一覧の取得に失敗した」であって、
    「投稿がまだ無いので試せない」ではない（投稿が無いかどうかは分かっていない）。
    """
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    with _server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        report = doctor_mod.diagnose(account["name"])

    reply_probe = next(p for p in report["probes"]
                        if p["permission"] == "threads_read_replies")
    assert reply_probe["detail"] != "投稿がまだ無いので試せない", (
        "投稿一覧の取得が失敗しているのに「未投稿だから試せない」と表示している"
    )
    assert reply_probe["ok"] is False, (
        "理由が分かっている失敗なのに、判定不能（－）にしてしまっている"
    )


def test_run_doctorの終了コードも失敗になる(tmp_path, monkeypatch, isolated_account_factory):
    """人向け出力の入口（`run_doctor`）でも、○表示・rc=0 にならないこと。"""
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    with _server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = doctor_mod.run_doctor(account["name"], log=lines.append)

    assert rc != 0, "HTTP 200 の error があるのに成功終了している"
    out = "\n".join(lines)
    assert "○" not in out, f"○ 表示が残っている: {out}"
    assert "投稿がまだ無いので試せない" not in out
    assert "DOCTOR-N7-TOKEN" not in out, "トークンの値が出力に出ている"
