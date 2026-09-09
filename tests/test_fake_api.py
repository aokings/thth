"""偽 API（`http.server`）で 6 種（発注 §5 受け入れ 10）: 正常／コンテナ作成が 4xx／
公開が 5xx／429／timeout／公開に成功した直後にプロセスを落とす（次回 inflight で停止）。
T1 検収 2026-09-09 の差し戻しで、失敗の三分類（設計 §3.5）を確かめる 2 本を追加した:
「公開が timeout → inflight が残り exit 1 → 次の実行も inflight で止まる」
「公開が 4xx → inflight が消えて exit 1 → 次の実行は普通に選び直せる」。

本物の Threads API には触らない。`ThreadsAdapter.base_url` を差し替えて、自前の
偽サーバにだけ向ける。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import threading
import time
import urllib.parse

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_thth, write_queue_file
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import runs as runs_mod
from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod


class _Handler(http.server.BaseHTTPRequestHandler):
    behavior = {"create": "ok", "publish": "ok", "create_delay": 0, "publish_delay": 0}
    counter = [1]
    # コンテナ作成（`/threads`）に実際に届いた params（`topic_tag` の検査用・T2c）。
    create_params: list

    def _respond_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server の命名規則)
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        if not self.path.endswith("/threads_publish"):
            # コンテナ作成の params を記録する（`topic_tag` が入っているか／
            # 入っていないかを偽サーバ側で受け取って確かめる・T2c）。
            parsed = urllib.parse.parse_qs(raw_body.decode("utf-8"))
            self.__class__.create_params.append({k: v[0] for k, v in parsed.items()})
        if self.path.endswith("/threads_publish"):
            mode = self.behavior["publish"]
            delay = self.behavior.get("publish_delay", 0)
        else:
            mode = self.behavior["create"]
            delay = self.behavior.get("create_delay", 0)
        if delay:
            time.sleep(delay)
        if mode == "ok":
            _id = str(self.counter[0])
            self.counter[0] += 1
            self._respond_json(200, {"id": _id})
        elif mode == "4xx":
            self._respond_json(400, {"error": "bad request"})
        elif mode == "5xx":
            self._respond_json(500, {"error": "server error"})
        elif mode == "429":
            self._respond_json(429, {"error": "rate limited"})
        elif mode == "no_id":
            self._respond_json(200, {})

    def log_message(self, format, *args):  # noqa: A002 - テスト出力を汚さない
        pass


class _ServerURL(str):
    """`base_url` として素朴に使える文字列に、コンテナ作成の params を覗ける
    `handler_cls` を添えたもの（既存の呼び出し側は文字列としてそのまま使える）。"""


@contextlib.contextmanager
def fake_threads_server(behavior: dict):
    handler_cls = type("Handler", (_Handler,),
                        {"behavior": dict(behavior), "counter": [1], "create_params": []})
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


def _adapter(base_url: str, **kwargs) -> threads_mod.ThreadsAdapter:
    kwargs.setdefault("wait_seconds", 0)
    kwargs.setdefault("timeout", 1.0)
    return threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake-token",
                                       user_id="12345", **kwargs)


def test_1b_topicを渡すとtopic_tagとして送られる():
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        adapter = _adapter(base_url)
        result = adapter.publish(
            adapter_base.Post(text="こんにちは", topic="苦味"), dry_run=False)
    assert result.post_id is not None
    assert base_url.handler_cls.create_params[0]["topic_tag"] == "苦味"


def test_1c_topicが空またはNoneならparamsに入らない():
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        adapter = _adapter(base_url)
        adapter.publish(adapter_base.Post(text="こんにちは", topic=None), dry_run=False)
        adapter.publish(adapter_base.Post(text="こんにちは", topic=""), dry_run=False)
    assert "topic_tag" not in base_url.handler_cls.create_params[0]
    assert "topic_tag" not in base_url.handler_cls.create_params[1]


def test_1_正常系は投稿idを返す():
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        adapter = _adapter(base_url)
        result = adapter.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert result.post_id is not None
    assert result.error is None


def test_2_コンテナ作成が4xxなら公開しない():
    with fake_threads_server({"create": "4xx", "publish": "ok"}) as base_url:
        adapter = _adapter(base_url)
        result = adapter.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert result.post_id is None
    assert result.error is not None
    assert "container作成失敗" in result.error
    assert "400" in result.error


def test_3_公開が5xxならpost_idが付かない():
    with fake_threads_server({"create": "ok", "publish": "5xx"}) as base_url:
        adapter = _adapter(base_url)
        result = adapter.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert result.post_id is None
    assert result.error is not None
    assert "公開失敗" in result.error
    assert "500" in result.error


def test_4_429は再試行せずエラーを返す():
    with fake_threads_server({"create": "429", "publish": "ok"}) as base_url:
        adapter = _adapter(base_url)
        result = adapter.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert result.post_id is None
    assert "429" in result.error


def test_5_timeoutはエラーを返す():
    with fake_threads_server({"create": "ok", "publish": "ok", "publish_delay": 3}) as base_url:
        adapter = _adapter(base_url, timeout=0.5)
        result = adapter.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert result.post_id is None
    assert result.error is not None
    assert "公開失敗" in result.error


def test_6_公開成功直後の中断は次回inflightで停止する(isolated_account_factory, tmp_path):
    """post_id 書き戻し前にプロセスが落ちても、次回の throw は inflight を見て
    exit 1 になる（二重投稿しない・設計 §3.5）。"""
    account = isolated_account_factory(production=True, quiet_hours=None)
    write_queue_file(account["queue_dir"], "a.md")

    token_path = os.path.join(account["repo_dir"], "..", "fake.token")
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "fake-token"}, f)
    env_path = os.path.join(account["repo_dir"], "..", "fake.env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("X=1\n")
    # 台帳の env/token パスをこのテストの実ファイルに向け直す。
    import thth.accounts as accounts_mod_local
    cfg_path = os.path.join(account["accounts_dir"], f"{account['name']}.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["env"] = env_path
    cfg["token"] = token_path
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        env = {
            "THTH_THREADS_BASE_URL": base_url,
            "THTH_THREADS_WAIT_SECONDS": "0",
            "THTH_TEST_CRASH_AFTER_PUBLISH": "1",
        }
        result = run_thth(["throw", account["name"], "--production"], env=env)
        # os._exit(1) で強制終了するので returncode は 1（正常な「公開に成功して
        # 書き戻し前に落ちた」を模している）。
        assert result.returncode == 1

    state_dir = accounts_mod.state_dir_for(account["name"])
    left = inflight_mod.read(state_dir)
    assert left is not None
    assert left.get("post_id") is not None  # 公開には成功していた証拠が残っている

    # 次回の throw は inflight を見て何もせず exit 1 になる（二重投稿しない）。
    result2 = run_thth(["throw", account["name"], "--production"])
    assert result2.returncode == 1

    # front-matter は書き換わっていない（push まで到達していない）。
    path = os.path.join(account["queue_dir"], "a.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "status: approved" in text


def test_7_公開がtimeoutならinflightが残り次回も止まる(isolated_account_factory):
    """出たか分からない失敗（timeout）は inflight を消さない（設計 §3.5・差し戻し 1
    件目）。消すと次の毎時実行が同じファイルをもう一度選び直して二重投稿になる。"""
    account = isolated_account_factory(production=True, quiet_hours=None)
    write_queue_file(account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "ok", "publish_delay": 3}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url, timeout=0.5)

        result = core.throw_once(account["name"], production_flag=True, adapter_factory=factory)
        assert result.exit_code == 1
        assert result.action == "inflight"

        left = inflight_mod.read(state_dir)
        assert left is not None
        assert left.get("file") == os.path.join(account["queue_dir"], "a.md")

        # 次の実行は inflight を見て、select をやり直さずに何もしないで exit 1。
        result2 = core.throw_once(account["name"], production_flag=True, adapter_factory=factory)
    assert result2.exit_code == 1
    assert result2.action == "inflight"
    assert inflight_mod.read(state_dir) is not None  # 消えていない

    # front-matter も書き換わっていない（二重投稿していない証拠）。
    path = os.path.join(account["queue_dir"], "a.md")
    with open(path, encoding="utf-8") as f:
        assert "status: approved" in f.read()


def test_8_公開が4xxならinflightが消えて次回は普通に選び直せる(isolated_account_factory):
    """出ていないと分かる失敗（HTTP 4xx）は inflight を消してよい（設計 §3.5）。"""
    account = isolated_account_factory(production=True, quiet_hours=None)
    write_queue_file(account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "4xx"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True, adapter_factory=factory)

    assert result.exit_code == 1
    assert result.action == "post"
    assert inflight_mod.read(state_dir) is None  # 消えている

    # 次の実行は inflight に阻まれず、select が同じファイルを普通に選び直せる
    # （dry-run で確認。承認 gate 等の他条件はそのまま生きている）。
    result2 = core.throw_once(account["name"], production_flag=False)
    assert result2.action != "inflight"
    assert result2.file == os.path.join(account["queue_dir"], "a.md")


def test_9_topicを付けて投稿するとrunsにtopicが残る(isolated_account_factory, tmp_path):
    """付けたトピックを読み返す field が Threads 側に無い（設計 §2.2）ので、
    付けたことは THTH 側の runs で記録する（T2c・masaru 裁定 2026-09-09）。
    push まで通す必要があるので、round-trip テストと同じ隔離 git pair を使う。"""
    seed_content = make_queue_text(fm_overrides={"topic": "苦味"})
    pair = init_git_pair(tmp_path, seed_content=seed_content, seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True, quiet_hours=None)
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True, adapter_factory=factory)

    assert result.exit_code == 0
    assert result.action == "post"
    assert base_url.handler_cls.create_params[0]["topic_tag"] == "苦味"

    runs = runs_mod.read_runs(state_dir)
    posted = [r for r in runs if r["action"] == "post" and r["status"] == "ok"]
    assert len(posted) == 1
    assert posted[0]["topic"] == "苦味"
