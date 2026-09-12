"""`data` 配列の要素が `null` でも、後続の投稿の採取を止めない（外部レビュー再々判定 N5・2026-09-12）。

**本物の Threads API には触らない。** 偽の HTTP サーバを立てて確かめる。

外部レビューの記述（そのまま）: HTTP 200 で `{"data":[null]}` を返す fixture を
使うと、adapter は `[None]` を返信一覧として返し、collector の `row.get()` で
`AttributeError` になった。**当該投稿の失敗として継続できず、後続投稿の出力も
作られなかった。**

閉じる条件は「配列という外形だけでなく、要素が読み手の扱える object であることを
adapter 境界で検査する」こと。弾けば `collect.py` 側の「例外は `errors` に積んで
次の投稿へ進む」がそのまま効くはず——**それを実際に確かめる。**
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading

import pytest

from tests.test_collect import NOW, _rows, _setup
from tests.conftest import write_queue_file
from thth import collect as collect_mod
from thth.adapters import threads as threads_mod


class _Handler(http.server.BaseHTTPRequestHandler):
    """`/POST1/conversation` だけ `{"data": [null]}` を返し、他は健全な応答を返す。"""

    def do_GET(self) -> None:  # noqa: N802（http.server の命名規則）
        path = self.path.split("?")[0]
        if path == "/v1.0/POST1/conversation":
            payload = {"data": [None]}
        elif path.endswith("/conversation"):
            payload = {"data": []}
        elif "insights" in path:
            payload = {"data": [{"name": "views", "total_value": {"value": 10}}]}
        else:
            payload = {"data": []}
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
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_dataの要素がnullなら件数として数えず失敗として上げる():
    """adapter 境界（`_rows()`）で、配列の**要素**が object かどうかを見る。"""
    with _server() as base_url:
        adapter = threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake",
                                              user_id="12345", timeout=2.0)
        with pytest.raises(RuntimeError) as e:
            adapter.conversation("POST1")
    assert "object ではありません" in str(e.value)


def test_null要素の投稿は失敗として残り後続の投稿は採れる(tmp_path, isolated_account_factory):
    """**これが本題。** POST1 の返信が `{"data": [null]}` で失敗しても、
    POST2 の採取は続くこと（`AttributeError` で丸ごと落ちていないか）を確かめる。
    """
    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-10T10:00:00+09:00")  # 2 時間前（1h の刻みを跨ぐ）
    write_queue_file(pair["queue_dir"], "post2.md",
                      fm_overrides={"status": "posted", "post_id": "POST2",
                                    "posted_at": "2026-09-10T10:00:00+09:00"})

    with _server() as base_url:
        adapter = threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake",
                                              user_id=account["name"], timeout=2.0)
        result = collect_mod.collect_once(account["name"], adapter=adapter, now=NOW,
                                           log=lambda _l: None)

    assert result["posts"] == 2, "束ではないので 2 投稿とも数えるはず"
    assert any("POST1" in e and "conversation" in e for e in result["errors"]), (
        f"POST1 の返信の失敗が errors に出ていない: {result['errors']}")
    # POST1 は返信こそ失敗したが、数（insights）は別に採れているはず——
    # 「当該投稿の失敗」が返信だけに留まり、投稿全体を巻き込んでいないこと。
    assert _rows(pair["work"], "data/sns/insights/posts/POST1.ndjson"), (
        "POST1 の返信が失敗したせいで、POST1 自身の数の採取まで止まっている"
    )
    # **後続の投稿（POST2）の採取が続いていること**——これが N5 の本題。
    assert _rows(pair["work"], "data/sns/replies/POST2.ndjson"), (
        "POST1 の要素 null で丸ごと落ちて、後続の POST2 の採取まで止まっている"
    )
    assert _rows(pair["work"], "data/sns/insights/posts/POST2.ndjson")
