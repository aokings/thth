"""CLI の遠くの道と MCP（設計 3.14.0 §1・§2・§3.4・段 3）。

- `thth login`（tty・--stdin・引数は断る・0600）／`thth logout`。
- 切り替え: 台帳があれば手元・無くて鍵があれば遠く・両方なら手元（`--remote` で遠く）・どちらも無ければ手元の断り。
- 各命令が `POST /api/v1/<operation>` に同じ形で届き、`--json` は VM の JSON をそのまま出す。
- 200・202→result・401・429・網の断・`remote_unsupported`。鍵は出力に出ない。
- MCP は `thth … --json` を呼ぶだけ。

偽の Worker は 127.0.0.1 の HTTP（conftest の `THTH_TEST_ALLOW_HTTP` が loopback だけ許す）。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from thth import cli, remote

KEY = "k3y_" + "A" * 39  # 43 字の URL 安全な鍵（Worker の opaque() と同じ形）
REQ = "R" * 43 + ".inv-demo-abc"
ACCOUNT = "inv-demo-abc"


class FakeWorker:
    """`/api/v1/*` の偽物。`replies` に (status, body, headers) を順に積む。無ければ `default`。"""

    def __init__(self):
        self.calls = []
        self.queue = []
        self.default = None
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                worker.calls.append({"method": self.command, "path": self.path,
                                     "auth": self.headers.get("authorization"),
                                     "type": self.headers.get("content-type"),
                                     "body": json.loads(raw) if raw else None})
                status, body, headers = worker.queue.pop(0) if worker.queue else worker.default(worker.calls[-1])
                data = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            do_GET = _answer
            do_POST = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def push(self, status, body, headers=None):
        self.queue.append((status, body, headers))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _echo(call):
    """既定の答え: どの operation にも「受け取った本文」を返す（形の確かめ用）。"""
    return 200, {"operation": call["path"].rsplit("/", 1)[-1], "received": call["body"]}, None


@pytest.fixture
def home(tmp_path, monkeypatch):
    """台帳の無い THTH_ROOT と、試験ごとの remote.json の置き場。"""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    config = tmp_path / "cfg" / "thth" / "remote.json"
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote, "_sleep", lambda seconds: None)
    return {"root": root, "config": config}


@pytest.fixture
def worker(home):
    fake = FakeWorker()
    fake.default = _echo
    home["config"].parent.mkdir(parents=True)
    home["config"].write_text(json.dumps({"url": fake.url, "key": KEY}))
    os.chmod(home["config"], 0o600)
    yield fake
    fake.close()


def run(capsys, *argv, stdin=None, monkeypatch=None):
    if stdin is not None:
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    assert KEY not in out and KEY not in err
    return rc, out, err


# --------------------------------------------------------------------------
# login / logout
# --------------------------------------------------------------------------

def test_login_stdin_は_0600_と親_0700_で保存し鍵を出さない(home, capsys, monkeypatch):
    rc, out, err = run(capsys, "login", "--stdin", stdin=KEY + "\n", monkeypatch=monkeypatch)
    assert rc == 0
    path = home["config"]
    assert json.loads(path.read_text()) == {"url": "https://thth.me", "key": KEY}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert "thth account status" in out


def test_login_は_tty_で読む(home, capsys, monkeypatch):
    import getpass
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": KEY)
    rc, out, err = run(capsys, "login", "--url", "https://thth.example")
    assert rc == 0
    assert json.loads(home["config"].read_text()) == {"url": "https://thth.example", "key": KEY}


def test_login_は_tty_でなければ_stdin_を案内する(home, capsys, monkeypatch):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(KEY))
    rc, out, err = run(capsys, "login")
    assert rc == 2 and err.startswith("tty_required")
    assert not home["config"].exists()


def test_login_は鍵を引数で受けない(home, capsys):
    rc, out, err = run(capsys, "login", KEY)
    assert rc == 2 and err.startswith("key_in_argument")
    assert "履歴" in err
    assert not home["config"].exists()


@pytest.mark.parametrize("bad", ["short", "has space in it here", "x" * 600, ""])
def test_login_は鍵の形を確かめる(home, capsys, monkeypatch, bad):
    rc, out, err = run(capsys, "login", "--stdin", stdin=bad + "\n", monkeypatch=monkeypatch)
    assert rc == 2 and err.startswith("invalid_key_format")
    assert not home["config"].exists()


def test_login_は_https_でない_url_を断る(home, capsys, monkeypatch):
    monkeypatch.delenv("THTH_TEST_ALLOW_HTTP")
    rc, out, err = run(capsys, "login", "--stdin", "--url", "http://thth.me", stdin=KEY, monkeypatch=monkeypatch)
    assert rc == 2 and err.startswith("invalid_url")


def test_logout_は消す_無くても_0(home, capsys, monkeypatch):
    run(capsys, "login", "--stdin", stdin=KEY, monkeypatch=monkeypatch)
    rc, out, err = run(capsys, "logout")
    assert rc == 0 and not home["config"].exists()
    rc, out, err = run(capsys, "logout")
    assert rc == 0 and "ログインしていません" in out


# --------------------------------------------------------------------------
# 切り替え
# --------------------------------------------------------------------------

def test_台帳も鍵も無ければ手元の断りに_thth_login_を添える(home, capsys):
    rc, out, err = run(capsys, "measured", ACCOUNT)
    assert rc != 0
    assert "台帳が無い" in err and "thth login" in err


def test_鍵だけなら遠くの道(worker, capsys):
    rc, out, err = run(capsys, "posts", ACCOUNT, "--json")
    assert rc == 0
    call = worker.calls[-1]
    assert call["method"] == "POST" and call["path"] == "/api/v1/posts"
    assert call["auth"] == "Bearer " + KEY
    assert call["type"] == "application/json"
    assert call["body"] == {"account": ACCOUNT, "limit": 25}
    assert json.loads(out) == {"operation": "posts", "received": call["body"]}


def test_台帳があれば手元が先_remote_で遠く(worker, isolated_account, capsys):
    name = isolated_account["name"]
    rc, out, err = run(capsys, "measured", name, "--json")
    assert rc == 0 and worker.calls == []
    assert "posts" in json.loads(out)
    rc, out, err = run(capsys, "measured", name, "--json", "--remote")
    assert rc == 0 and worker.calls[-1]["path"] == "/api/v1/measured"


def test_remote_を付けても鍵が無ければ_not_logged_in(home, capsys):
    rc, out, err = run(capsys, "posts", ACCOUNT, "--remote")
    assert rc == 2 and err.startswith("not_logged_in")
    assert "thth login" in err


@pytest.mark.parametrize("argv", [["throw", ACCOUNT], ["run", ACCOUNT], ["auth", ACCOUNT, "--by", "x"],
                                  ["account", ACCOUNT], ["account", "resume", ACCOUNT, "--by", "x"],
                                  ["account", "add", ACCOUNT, "--remote"], ["lint", "x.md", "--remote"],
                                  ["approve", "x.md", "--remote"]])
def test_遠くの道で使えない命令は_remote_unsupported(worker, capsys, argv):
    rc, out, err = run(capsys, *argv)
    assert rc == 2 and err.startswith("remote_unsupported")
    assert worker.calls == []


def test_鍵があっても口座でない語を取る命令は巻き込まない(worker, capsys):
    # `topics history <語>` は口座の位置に語が来る。遠くの道に送らない。
    rc, out, err = run(capsys, "topics", "history", "語")
    assert worker.calls == []


# --------------------------------------------------------------------------
# 各命令の届き方
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv, operation, body", [
    (["posts", ACCOUNT, "--limit", "5", "--refresh"], "posts", {"limit": 5, "refresh": True}),
    (["replies", ACCOUNT, "--limit", "3", "--refresh", "--post", "99"], "replies",
     {"limit": 3, "refresh": True, "post_id": "99"}),
    (["measured", ACCOUNT, "--post", "99"], "measured", {"post_id": "99"}),
    (["collect", ACCOUNT], "collect", {}),
    (["mentions", ACCOUNT, "--limit", "4"], "mentions", {"limit": 4}),
    (["topics", ACCOUNT, "--search", "お茶", "--limit", "7"], "topics_search", {"query": "お茶", "limit": 7}),
    (["profile", ACCOUNT, "meta"], "profile", {"username": "meta"}),
    (["location", "search", ACCOUNT, "渋谷駅"], "location_search", {"query": "渋谷駅"}),
    (["account", "status", ACCOUNT], "account_status", {}),
    (["account", "set", ACCOUNT, "daily_max_posts", "3"], "settings", {"key": "daily_max_posts", "value": "3"}),
    (["queue", ACCOUNT], "draft_list", {}),
    (["retract", ACCOUNT, "123", "--reason", "誤字"], "retract_request", {"post_id": "123", "reason": "誤字"}),
    (["send", ACCOUNT, "--text", "こんにちは", "--topic", "お茶", "--reply-to", "55"], "send_request",
     {"body": "こんにちは", "topic": "お茶", "reply_to": "55"}),
])
def test_命令は_operation_と引数で届き_json_はそのまま(worker, capsys, argv, operation, body):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 0, err
    call = worker.calls[-1]
    assert call["path"] == "/api/v1/" + operation
    assert call["body"] == {"account": ACCOUNT, **body}
    assert json.loads(out) == {"operation": operation, "received": call["body"]}


def test_send_は本文をファイルと標準入力からも読む(worker, capsys, monkeypatch, tmp_path):
    path = tmp_path / "body.txt"
    path.write_text("一行目\n二行目\n")
    rc, out, err = run(capsys, "send", ACCOUNT, "--text-file", str(path), "--json")
    assert rc == 0 and worker.calls[-1]["body"]["body"] == "一行目\n二行目\n"
    rc, out, err = run(capsys, "send", ACCOUNT, "--json", stdin="標準入力から", monkeypatch=monkeypatch)
    assert rc == 0 and worker.calls[-1]["body"]["body"] == "標準入力から"


def test_send_dry_run_は_draft_put_で_lint_だけ(worker, capsys):
    worker.push(200, {"draft_id": "d" * 64, "account": ACCOUNT, "status": "draft", "revision": "r" * 64})
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "試し", "--dry-run")
    assert rc == 0 and "出していません" in out
    assert [c["path"] for c in worker.calls] == ["/api/v1/draft_put"]
    assert worker.calls[0]["body"]["body"] == "試し" and worker.calls[0]["body"]["publish_at"]


def test_send_は_rehearsal_を挟まず_1_回で出す(worker, capsys):
    worker.push(200, {"account": ACCOUNT, "status": "published", "post_id": "777",
                      "permalink": "https://www.threads.net/@demo/post/777", "via": "api"})
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "出す")
    assert rc == 0 and "777" in out and "https://www.threads.net/@demo/post/777" in out
    assert [c["path"] for c in worker.calls] == ["/api/v1/send_request"]


def test_schedule_は_draft_put_のあと_schedule_request(worker, capsys):
    worker.push(200, {"draft_id": "d" * 64, "account": ACCOUNT, "status": "draft", "revision": "r" * 64})
    worker.push(200, {"account": ACCOUNT, "draft_id": "d" * 64, "status": "approved",
                      "publish_at": "2026-09-27T09:00:00+09:00", "via": "api"})
    rc, out, err = run(capsys, "schedule", ACCOUNT, "--text", "明日の朝", "--at", "2026-09-27T09:00+09:00")
    assert rc == 0 and "2026-09-27 09:00 JST" in out
    assert [c["path"] for c in worker.calls] == ["/api/v1/draft_put", "/api/v1/schedule_request"]
    assert worker.calls[0]["body"]["publish_at"] == "2026-09-27T09:00+09:00"
    assert worker.calls[1]["body"] == {"account": ACCOUNT, "draft_id": "d" * 64}


def test_schedule_の一覧と添付は遠くの道では断る(worker, capsys):
    rc, out, err = run(capsys, "schedule", ACCOUNT)
    assert rc == 2 and err.startswith("remote_unsupported")
    rc, out, err = run(capsys, "send", ACCOUNT, "--media", "a.png", "--alt", "a")
    assert rc == 2 and err.startswith("remote_unsupported: 添付")
    assert worker.calls == []


def test_schedule_の予約を刻む形は手元の道では断る(isolated_account, capsys):
    rc, out, err = run(capsys, "schedule", isolated_account["name"], "--text", "x", "--at", "2026-09-27T09:00+09:00")
    assert rc == 2 and err.startswith("remote_only")


def test_人向けの表示は手元の道と同じ関数(worker, capsys):
    worker.push(200, {"account": ACCOUNT, "posts": [{"id": "1", "timestamp": "2026-09-26T10:00:00+0000",
                                                    "permalink": "https://www.threads.net/@demo/post/1",
                                                    "text": "本文", "topic": None, "via_thth": True}],
                      "retracted": []})
    rc, out, err = run(capsys, "posts", ACCOUNT)
    assert rc == 0
    assert "THTH（同席の送信）" in out and "| 本文" in out and "—— 1 件" in out
    worker.push(200, {"account": ACCOUNT, "settings": {"daily_max_posts": 10, "daily_max_retracts": 5,
                      "burst_count": 5, "burst_minutes": 10, "hold_minutes": 0, "min_interval_hours": 0},
                      "scheduled": True, "quiet_hours": None, "stopped": None, "ignored": [],
                      "today": {"date": "2026-09-26", "posts": 1, "retracts": 0}})
    rc, out, err = run(capsys, "account", "status", ACCOUNT)
    assert rc == 0 and "daily_max_posts" in out and "→ 動いています" in out


# --------------------------------------------------------------------------
# 応答の種類
# --------------------------------------------------------------------------

def test_202_なら_result_を見に行く(worker, capsys):
    worker.push(202, {"status": "pending", "request_id": REQ})
    worker.push(202, {"status": "pending"})
    worker.push(200, {"account": ACCOUNT, "status": "published", "post_id": "9", "permalink": None})
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "遅い", "--json")
    assert rc == 0 and json.loads(out)["post_id"] == "9"
    assert [(c["method"], c["path"]) for c in worker.calls] == [
        ("POST", "/api/v1/send_request"), ("GET", "/api/v1/result/" + REQ), ("GET", "/api/v1/result/" + REQ)]
    assert all(c["auth"] == "Bearer " + KEY for c in worker.calls)


def test_result_を待ちきれなければ_remote_pending(worker, capsys, monkeypatch):
    ticks = iter(range(0, 10000, 30))
    monkeypatch.setattr(remote, "_clock", lambda: next(ticks))
    worker.default = lambda call: (202, {"status": "pending"}, None) if "result" in call["path"] \
        else (202, {"status": "pending", "request_id": REQ}, None)
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "遅い")
    assert rc == 1 and err.startswith("remote_pending")
    assert "thth posts" in err


@pytest.mark.parametrize("code", ["invalid_key", "key_expired"])
def test_401_は符丁と次の一手(worker, capsys, code):
    worker.push(401, {"error": code})
    rc, out, err = run(capsys, "posts", ACCOUNT, "--json")
    assert rc == 2 and err.startswith(code)
    assert "/activity" in err and "thth login" in err
    assert json.loads(out) == {"error": code}


def test_429_は_retry_after_を待って_1_回だけやり直す(worker, capsys, monkeypatch):
    slept = []
    monkeypatch.setattr(remote, "_sleep", slept.append)
    worker.push(429, {"error": "rate_limited"}, {"Retry-After": "3"})
    rc, out, err = run(capsys, "posts", ACCOUNT, "--json")
    assert rc == 0 and slept == [3] and len(worker.calls) == 2
    worker.push(429, {"error": "rate_limited"}, {"Retry-After": "3"})
    worker.push(429, {"error": "rate_limited"}, {"Retry-After": "3"})
    rc, out, err = run(capsys, "posts", ACCOUNT)
    assert rc == 1 and err.startswith("rate_limited") and len(worker.calls) == 4


def test_429_に_retry_after_が無ければそのまま断る(worker, capsys):
    worker.push(429, {"error": "too_many_requests"})
    rc, out, err = run(capsys, "posts", ACCOUNT)
    assert rc == 1 and err.startswith("too_many_requests") and len(worker.calls) == 1


def test_網が断たれていれば_remote_unavailable(home, capsys):
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    home["config"].parent.mkdir(parents=True)
    home["config"].write_text(json.dumps({"url": "http://127.0.0.1:%d" % port, "key": KEY}))
    rc, out, err = run(capsys, "posts", ACCOUNT)
    assert rc == 2 and err.startswith("remote_unavailable")


def test_壊れた_remote_json_は_remote_config_invalid(home, capsys):
    home["config"].parent.mkdir(parents=True)
    home["config"].write_text("{")
    rc, out, err = run(capsys, "posts", ACCOUNT)
    assert rc == 2 and err.startswith("remote_config_invalid")


def test_安全装置の断りは符丁と_JST_の時刻(worker, capsys):
    worker.push(200, {"error": "too_soon", "next_at": "2026-09-26T03:00:00+00:00"})
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "早すぎ", "--json")
    assert rc == 1
    assert err.splitlines()[0] == "too_soon（次は 2026-09-26 12:00 JST から）"
    assert json.loads(out) == {"error": "too_soon", "next_at": "2026-09-26T03:00:00+00:00"}
    worker.push(200, {"error": "account_stopped", "reason": "burst"})
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "止まった")
    assert rc == 1 and err.startswith("account_stopped: burst") and "/activity" in err


def test_緩める設定は符丁のまま(worker, capsys):
    worker.push(200, {"error": "settings_loosen_requires_owner"})
    rc, out, err = run(capsys, "account", "set", ACCOUNT, "daily_max_posts", "99")
    assert rc == 1 and err.startswith("settings_loosen_requires_owner")


def test_鍵がサーバの答えに反射しても出さない(worker, capsys):
    worker.push(200, {"error": "upstream_unavailable", "reason": "rejected " + KEY})
    rc, out, err = run(capsys, "posts", ACCOUNT, "--json")  # run() が KEY の不在を確かめる
    assert rc == 1 and err.startswith("upstream_unavailable")


# --------------------------------------------------------------------------
# MCP は CLI を呼ぶだけ
# --------------------------------------------------------------------------

def _mcp():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "mcp"))
    try:
        import server
    finally:
        sys.path.pop(0)
    return server


def test_mcp_は_login_済みなら_SERVER_TOOLS_と同じ名前を出す(worker, monkeypatch):
    server = _mcp()
    monkeypatch.delenv("THTH_REPORT_CREDENTIALS", raising=False)
    monkeypatch.delenv("THTH_REPORT_TOKEN", raising=False)
    names = [tool["name"] for tool in server.local_tools()]
    assert {"thth_send_request", "thth_posts", "thth_replies", "thth_measured", "thth_retract_request",
            "thth_account_status", "thth_settings", "thth_topics_search"} <= set(names)
    assert set(names) <= {tool["name"] for tool in server.SERVER_TOOLS}


def test_mcp_は_login_も台帳も無ければ今の一覧(home, monkeypatch):
    server = _mcp()
    monkeypatch.delenv("THTH_REPORT_CREDENTIALS", raising=False)
    monkeypatch.delenv("THTH_REPORT_TOKEN", raising=False)
    assert server.local_tools() == server.TOOLS


def test_mcp_の道具は_cli_を_json_で呼ぶだけ(worker, monkeypatch):
    server = _mcp()
    seen = []

    def fake_run(args, *, stdin_text=None):
        seen.append((args, stdin_text))
        return subprocess.CompletedProcess(args, 0, stdout='{"ok": true}\n', stderr="")
    monkeypatch.setattr(server, "run_cli", fake_run)
    result = server.call_tool("thth_send_request", {"account": ACCOUNT, "body": "本文\n2 行目", "topic": "お茶"})
    assert result == {"content": [{"type": "text", "text": '{"ok": true}'}], "isError": False}
    assert seen[-1] == (["send", ACCOUNT, "--json", "--topic=お茶"], "本文\n2 行目")
    server.call_tool("thth_replies", {"account": ACCOUNT, "limit": 3, "refresh": True, "post_id": "9"})
    assert seen[-1][0] == ["replies", ACCOUNT, "--json", "--limit=3", "--refresh", "--post=9"]
    server.call_tool("thth_retract_request", {"account": ACCOUNT, "post_id": "9", "reason": "誤字"})
    assert seen[-1][0] == ["retract", ACCOUNT, "9", "--reason=誤字", "--json"]
    server.call_tool("thth_settings", {"account": ACCOUNT})
    assert seen[-1][0] == ["account", "status", ACCOUNT, "--json"]
    assert server.call_tool("thth_profile", {"account": ACCOUNT, "username": "--help"})["isError"] is True
    assert server.call_tool("thth_posts", {"account": ACCOUNT, "limit": "3"})["isError"] is True


def test_mcp_から_cli_を通って偽の_worker_に届く(worker):
    server = _mcp()
    worker.push(200, {"account": ACCOUNT, "status": "published", "post_id": "5", "permalink": None, "via": "api"})
    result = server.call_tool("thth_send_request", {"account": ACCOUNT, "body": "MCP から"})
    assert result["isError"] is False, result
    assert json.loads(result["content"][0]["text"])["post_id"] == "5"
    assert worker.calls[-1]["body"] == {"account": ACCOUNT, "body": "MCP から"}
    assert KEY not in json.dumps(result)
    worker.push(401, {"error": "key_expired"})
    result = server.call_tool("thth_posts", {"account": ACCOUNT})
    assert result["isError"] is True and "key_expired" in result["content"][0]["text"]
    assert KEY not in json.dumps(result)
