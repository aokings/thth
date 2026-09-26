"""ブラウザ式の `thth login`（設計 3.14.2 §2）と、運営者のラッパ `bin/thth-remote`（§3-2）。

- 既定はブラウザ式: code（XXXX-XXXX）と 43 字の poll_token を作り、hash だけを start に送り、
  ブラウザを開いて（開けなくても URL を出す）poll し、受け取った鍵を `remote.json` に `url`・`key`・`account` で置く。
- `--no-browser` は URL だけ。tty が無くても動く。`--stdin`・`--paste`（貼る道）は残る。
- 鍵も poll_token も出力に出ない。期限切れ・断り・網の断は符丁で。
- ラッパは `login`・`logout` だけ手元の `thth` に渡し、それ以外は ssh で VM へ。

偽の Worker は 127.0.0.1 の HTTP（conftest の `THTH_TEST_ALLOW_HTTP` が loopback だけ許す）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from thth import cli, remote

KEY = "k3y_" + "B" * 39  # Worker の opaque() と同じ 43 字
ACCOUNT = "inv-demo-abc"
CODE = re.compile(r"/login/([A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4})\b")
REPO = Path(__file__).resolve().parent.parent


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class FakeLoginWorker:
    """`/api/v1/login/start` と `/api/v1/login/poll/<code>` の偽物。

    `waits` 回 202 を返したあと `final`（既定は 200 {key, account}）。poll_token と code の hash を確かめる。
    """

    def __init__(self, *, waits=2, final=None, start=None):
        self.calls = []
        self.started = None
        self.waits = waits
        self.final = final or (200, {"key": KEY, "account": ACCOUNT})
        self.start_answer = start
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                worker.calls.append({"method": "POST", "path": self.path, "auth": self.headers.get("authorization"),
                                     "body": body})
                if worker.start_answer:
                    return self._send(*worker.start_answer)
                worker.started = body
                return self._send(202, {"url": worker.url + "/login/", "expires_at": "2026-09-26T12:10:00.000Z"})

            def do_GET(self):
                worker.calls.append({"method": "GET", "path": self.path, "auth": self.headers.get("authorization")})
                code = self.path.rsplit("/", 1)[-1]
                auth = self.headers.get("authorization") or ""
                if worker.started is None or sha(code) != worker.started["code_hash"]:
                    return self._send(404, {"error": "not_found"})
                if not auth.startswith("Bearer ") or sha(auth[7:]) != worker.started["poll_token_hash"]:
                    return self._send(401, {"error": "unauthorized"})
                polls = sum(1 for c in worker.calls if c["method"] == "GET")
                if polls <= worker.waits:
                    return self._send(202, {"status": "waiting"})
                return self._send(*worker.final)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    config = tmp_path / "cfg" / "thth" / "remote.json"
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote, "_sleep", lambda seconds: None)
    opened = []
    monkeypatch.setattr(remote, "_open_browser", lambda page: opened.append(page) or True)
    # tty の無い標準入力（ブラウザ式は入力を要らない）。
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    return {"config": config, "opened": opened}


@pytest.fixture
def fake():
    worker = FakeLoginWorker()
    yield worker
    worker.close()


def login(capsys, *argv):
    rc = cli.main(["login", *argv])
    out, err = capsys.readouterr()
    assert KEY not in out and KEY not in err
    return rc, out, err


def test_ブラウザ式_既定で_code_と_poll_token_の_hash_だけ送り鍵と口座を保存する(home, fake, capsys):
    rc, out, err = login(capsys, "--url", fake.url)
    assert rc == 0, err
    code = CODE.search(out).group(1)
    page = f"{fake.url}/login/{code}"
    assert page in out and "10 分" in out
    assert home["opened"] == [page]
    start = fake.calls[0]
    assert start["method"] == "POST" and start["path"] == "/api/v1/login/start" and start["auth"] is None
    assert set(start["body"]) == {"code_hash", "poll_token_hash"}
    assert start["body"]["code_hash"] == sha(code)
    polls = [c for c in fake.calls if c["method"] == "GET"]
    assert len(polls) == 3 and all(c["path"] == "/api/v1/login/poll/" + code for c in polls)
    token = polls[0]["auth"][len("Bearer "):]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token) and sha(token) == start["body"]["poll_token_hash"]
    assert token not in out and token not in err
    path = home["config"]
    assert json.loads(path.read_text()) == {"url": fake.url, "key": KEY, "account": ACCOUNT}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert "保存しました" in out and ACCOUNT in out and f"thth account status {ACCOUNT}" in out
    # 保存した鍵で遠くの道がそのまま使える（load は account の欄を気にしない）。
    assert remote.load() == {"url": fake.url, "key": KEY}


def test_no_browser_は_URL_だけ出す(home, fake, capsys):
    rc, out, err = login(capsys, "--url", fake.url, "--no-browser")
    assert rc == 0
    assert home["opened"] == []
    assert CODE.search(out) and "ブラウザで開いて" in out


def test_ブラウザが開けなくても_URL_を出して待つ(home, fake, capsys, monkeypatch):
    monkeypatch.setattr(remote, "_open_browser", lambda page: False)
    rc, out, err = login(capsys, "--url", fake.url)
    assert rc == 0 and CODE.search(out) and "ブラウザで開いて" in out


def test_本物の_open_browser_は例外を飲む(monkeypatch):
    import webbrowser

    def boom(page):
        raise webbrowser.Error("no browser")

    monkeypatch.setattr(webbrowser, "open", boom)
    assert remote._open_browser("https://thth.me/login/ABCD-EFGH") is False


def test_tty_が無くてもブラウザ式は動く(home, fake, capsys):
    assert not sys.stdin.isatty()
    rc, out, err = login(capsys, "--url", fake.url)
    assert rc == 0 and "tty_required" not in err


def test_期限切れは_login_expired_で何も置かない(home, capsys):
    worker = FakeLoginWorker(waits=1, final=(410, {"error": "expired"}))
    try:
        rc, out, err = login(capsys, "--url", worker.url)
    finally:
        worker.close()
    assert rc == 2 and err.startswith("login_expired")
    assert not home["config"].exists()


def test_10_分待っても許可されなければ_login_expired(home, fake, capsys, monkeypatch):
    fake.waits = 10 ** 6
    ticks = iter(range(0, 10 ** 6, 100))
    monkeypatch.setattr(remote, "_clock", lambda: next(ticks))
    rc, out, err = login(capsys, "--url", fake.url)
    assert rc == 2 and err.startswith("login_expired")
    assert 5 <= sum(1 for c in fake.calls if c["method"] == "GET") <= 7
    assert not home["config"].exists()


def test_poll_の断りは_login_failed(home, capsys):
    worker = FakeLoginWorker(waits=0, final=(401, {"error": "unauthorized"}))
    try:
        rc, out, err = login(capsys, "--url", worker.url)
    finally:
        worker.close()
    assert rc == 2 and err.startswith("login_failed")
    assert not home["config"].exists()


@pytest.mark.parametrize("answer", [{"key": "short", "account": ACCOUNT}, {"key": KEY, "account": "../x"},
                                    {"key": KEY}, {"account": ACCOUNT}])
def test_鍵の形でない答えは保存せず鍵も出さない(home, capsys, answer):
    worker = FakeLoginWorker(waits=0, final=(200, answer))
    try:
        rc, out, err = login(capsys, "--url", worker.url)
    finally:
        worker.close()
    assert rc == 2 and err.startswith("login_failed")
    assert not home["config"].exists()


def test_start_を受けない_Worker_なら_login_unavailable_で貼る道を案内(home, capsys):
    worker = FakeLoginWorker(start=(404, {"error": "not_found"}))
    try:
        rc, out, err = login(capsys, "--url", worker.url)
    finally:
        worker.close()
    assert rc == 2 and err.startswith("login_unavailable") and "--stdin" in err
    assert not home["config"].exists()


def test_網が断たれていれば_remote_unavailable(home, capsys):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    rc, out, err = login(capsys, "--url", f"http://127.0.0.1:{port}")
    assert rc == 2 and err.startswith("remote_unavailable")


def test_待っている間に_ctrl_c_なら_login_cancelled(home, fake, capsys, monkeypatch):
    fake.waits = 10 ** 6
    calls = []

    def interrupt(seconds):
        calls.append(seconds)
        if len(calls) > 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(remote, "_sleep", interrupt)
    rc, out, err = login(capsys, "--url", fake.url)
    assert rc == 2 and "login_cancelled" in err
    assert calls[0] == remote.LOGIN_POLL_SECONDS
    assert not home["config"].exists()


def test_stdin_の貼る道は残る(home, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(KEY + "\n"))
    rc, out, err = login(capsys, "--stdin", "--url", "https://thth.example")
    assert rc == 0
    assert json.loads(home["config"].read_text()) == {"url": "https://thth.example", "key": KEY}
    assert home["opened"] == []


def test_paste_は_tty_で読み_tty_が無ければ_tty_required(home, capsys, monkeypatch):
    rc, out, err = login(capsys, "--paste")
    assert rc == 2 and err.startswith("tty_required")
    import getpass
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": KEY)
    rc, out, err = login(capsys, "--paste", "--url", "https://thth.example")
    assert rc == 0
    assert json.loads(home["config"].read_text()) == {"url": "https://thth.example", "key": KEY}


def test_code_は紛らわしい字を含まない():
    assert not set("0O1IL") & set(remote.LOGIN_ALPHABET)
    assert len(set(remote.LOGIN_ALPHABET)) == len(remote.LOGIN_ALPHABET) == 31


# --------------------------------------------------------------------------
# bin/thth-remote（運営者の Mac のラッパ）: login/logout だけ手元、それ以外は VM
# --------------------------------------------------------------------------

WRAPPER = REPO / "bin" / "thth-remote"


@pytest.fixture
def shims(tmp_path):
    """偽の `ssh` と偽の手元の `thth`（呼ばれた引数を書く）。ラッパ自身も PATH の先頭に `thth` として置く。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"

    def shim(name, tag):
        path = bin_dir / name
        path.write_text(f'#!/bin/sh\nprintf "{tag}' + ' %s\\n" "$*" >> ' + f'"{log}"\n')
        path.chmod(0o755)
        return path

    wrapper_dir = tmp_path / "wrapper"
    wrapper_dir.mkdir()
    (wrapper_dir / "thth").write_text(WRAPPER.read_text())
    (wrapper_dir / "thth").chmod(0o755)
    shim("ssh", "ssh")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local = local_dir / "thth"
    local.write_text(f'#!/bin/sh\nprintf "local %s\\n" "$*" >> "{log}"\n')
    local.chmod(0o755)
    env = {"PATH": f"{wrapper_dir}:{bin_dir}:{local_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return {"wrapper": wrapper_dir / "thth", "env": env, "log": log, "bin": bin_dir}


def call_wrapper(shims, *argv, env=None):
    result = subprocess.run(["/bin/bash", str(shims["wrapper"]), *argv], env=env or shims["env"],
                            capture_output=True, text=True, timeout=30)
    return result.returncode, shims["log"].read_text() if shims["log"].exists() else ""


@pytest.mark.parametrize("argv", [["login"], ["login", "--no-browser"], ["logout"]])
def test_ラッパは_login_logout_だけ手元の_thth_に渡す(shims, argv):
    rc, log = call_wrapper(shims, *argv)
    assert rc == 0
    assert log == "local " + " ".join(argv) + "\n", "PATH 上のラッパ自身は飛ばし、手元の thth を使う"


@pytest.mark.parametrize("argv", [["posts", ACCOUNT], ["account", "status", ACCOUNT], ["loginx"], []])
def test_ラッパはそれ以外を今までどおり_VM_へ(shims, argv):
    rc, log = call_wrapper(shims, *argv)
    assert rc == 0
    assert log.startswith("ssh wt ") and log.count("\n") == 1, "手元の thth は呼ばれない"
    assert log.rstrip("\n").endswith("exec thth" + "".join(" " + a for a in argv))


def test_ラッパは手元に_thth_が無ければ_python_m_thth(shims, tmp_path):
    (tmp_path / "local" / "thth").unlink()
    python = shims["bin"] / "python3"
    python.write_text(f'#!/bin/sh\nprintf "python %s\\n" "$*" >> "{shims["log"]}"\n')
    python.chmod(0o755)
    rc, log = call_wrapper(shims, "logout")
    assert rc == 0 and log == "python -m thth logout\n"


def test_ラッパは_THTH_LOCAL_を優先する(shims, tmp_path):
    chosen = tmp_path / "chosen"
    chosen.write_text(f'#!/bin/sh\nprintf "chosen %s\\n" "$*" >> "{shims["log"]}"\n')
    chosen.chmod(0o755)
    rc, log = call_wrapper(shims, "login", env={**shims["env"], "THTH_LOCAL": str(chosen)})
    assert rc == 0 and log == "chosen login\n"
