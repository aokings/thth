"""世間の検索だけ 40 秒（設計 3.1.0 §4・2026-09-23 実測）。

実機: `GET /keyword_search` は Meta 側の索引引きが 10 秒を超えることがあり、
**こちらが先に切っていた**——切られた側からは「0 件」と区別が付かないので、
`thth where` / `thth topics --search` / `thth morning` の第 3 段が黙って空に
なっていた。他の口（言及・実測・会話）は 10 秒のまま。

確かめるのは 3 つ:
  1. 秒数そのもの（検索 40・他 10）と、socket に渡る締切が口ごとに違うこと。
  2. **実際に遅い偽サーバ**で、検索は通って他の口は切れること。
  3. `THTH_THREADS_TIMEOUT_SECONDS` は**両方**を上書きすること。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading
import time

import pytest

from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod


def make_adapter(base_url, **kwargs):
    return threads_mod.ThreadsAdapter(base_url=base_url, access_token="FAKE",
                                      user_id="1", **kwargs)


@contextlib.contextmanager
def slow_server(*, slow_path, seconds):
    """`slow_path` を含む要求だけ `seconds` 秒待ってから答える偽 Graph。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):  # noqa: N802
            if slow_path in self.path:
                time.sleep(seconds)
            body = json.dumps({"data": [{"id": "1", "username": "someone",
                                         "text": "本文", "timestamp": "2026-09-23T06:00:00+09:00"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: A002
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def deadlines(monkeypatch):
    """各 HTTP 呼び出しに渡った socket の締切（パス・秒）を並べる。"""
    seen = []
    original = threads_mod.httpsafe.urlopen

    def urlopen(url, *args, timeout=None, **kwargs):
        target = url if isinstance(url, str) else url.full_url
        endpoint = target.split("?")[0].rsplit("/", 1)[-1]
        if endpoint in ("keyword_search", "mentions"):
            seen.append((endpoint, timeout))
        return original(url, *args, timeout=timeout, **kwargs)

    monkeypatch.setattr(threads_mod.httpsafe, "urlopen", urlopen)
    return seen


# ------------------------------------------------------------------ 秒数

def test_秒数そのもの():
    assert threads_mod.SEARCH_TIMEOUT_SECONDS == 40.0
    assert threads_mod.DEFAULT_TIMEOUT_SECONDS == 10.0
    adapter = make_adapter("https://graph.threads.net")
    assert adapter.search_timeout == 40.0 and adapter.timeout == 10.0


def test_検索だけが長い締切を受け取る(monkeypatch):
    seen = deadlines(monkeypatch)
    with slow_server(slow_path="/nothing", seconds=0) as base_url:
        adapter = make_adapter(base_url)
        adapter.keyword_search("コーヒー")
        adapter.mentions()
    assert dict(seen) == {"keyword_search": 40.0, "mentions": 10.0}


# ------------------------------------------------- 実際に遅い偽サーバ

@pytest.mark.parametrize("slow_path,expected", [
    ("keyword_search", "search_wins"), ("mentions", "other_times_out")])
def test_遅いサーバでは検索だけが待てる(slow_path, expected):
    # 15 秒を 1 テストにつき 1 回だけ待つ代わりに、同じ形を縮尺で通す
    # （検索 = 既定より長い・他 = 既定）。秒数そのものは上の 2 本が見ている。
    with slow_server(slow_path=slow_path, seconds=1.0) as base_url:
        adapter = make_adapter(base_url, timeout=0.3, search_timeout=3.0)
        if expected == "search_wins":
            assert adapter.keyword_search("コーヒー")[0]["message_id"] == "1"
        else:
            with pytest.raises(adapter_base.AdapterError):
                adapter.mentions()


def test_実寸_検索が15秒かかっても通る():
    """実機と同じ寸法（10 秒では切れていた・40 秒なら届く）。"""
    with slow_server(slow_path="keyword_search", seconds=15.0) as base_url:
        adapter = make_adapter(base_url)
        assert adapter.keyword_search("コーヒー")[0]["message_id"] == "1"


def test_実寸_検索以外は15秒で切れる():
    """他の口は 10 秒のまま——遅いのは索引だけ、という事実に合わせる。"""
    with slow_server(slow_path="mentions", seconds=15.0) as base_url:
        adapter = make_adapter(base_url)
        with pytest.raises(adapter_base.AdapterError):
            adapter.mentions()


# ------------------------------------------------------------ 上書き

@pytest.mark.parametrize("raw,seconds", [
    ("5", 5.0), ("90.5", 90.5), ("", None), ("abc", None), ("0", None),
    ("-1", None), ("inf", None), ("nan", None)])
def test_環境変数は両方を上書きする(monkeypatch, raw, seconds):
    monkeypatch.setenv("THTH_THREADS_TIMEOUT_SECONDS", raw)
    assert threads_mod.timeout_override() == seconds
    adapter = make_adapter("https://graph.threads.net")
    assert adapter.timeout == (seconds if seconds is not None else 10.0)
    assert adapter.search_timeout == (seconds if seconds is not None else 40.0)


def test_上書きはsocketまで届く(monkeypatch):
    monkeypatch.setenv("THTH_THREADS_TIMEOUT_SECONDS", "2.5")
    seen = deadlines(monkeypatch)
    with slow_server(slow_path="/nothing", seconds=0) as base_url:
        adapter = threads_mod.ThreadsAdapter.from_account(
            {"media": "threads"}, {"access_token": "FAKE", "user_id": "1"})
        adapter.base_url = base_url
        adapter.keyword_search("コーヒー")
        adapter.mentions()
    assert dict(seen) == {"keyword_search": 2.5, "mentions": 2.5}
