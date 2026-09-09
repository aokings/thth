"""round-trip（発注 §5 受け入れ 11）: 隔離 clone（bare origin）に umami-bile の写しを
置き、偽 API で本番モードを 1 巡して、front-matter 3 行だけが書き変わった commit が
origin に届くことを確認する（本文が 1 バイトも変わらないこと）。"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import subprocess
import threading

from tests.conftest import FIXTURES_DIR, init_git_pair, run_git
from thth import core


class _OkHandler(http.server.BaseHTTPRequestHandler):
    counter = [1]

    def do_POST(self):  # noqa: N802
        body = json.dumps({"id": str(self.counter[0])}).encode("utf-8")
        self.counter[0] += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def fake_ok_server():
    handler_cls = type("Handler", (_OkHandler,), {"counter": [1]})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_roundtrip_本番モード1巡で3行だけ変わる(isolated_account_factory, tmp_path, monkeypatch):
    with open(os.path.join(FIXTURES_DIR, "umami-bile.md"), encoding="utf-8") as f:
        seed_content = f.read()
    pair = init_git_pair(tmp_path, seed_content=seed_content, seed_name="2026-09-08-umami-bile.md")

    account = isolated_account_factory(repo_dir=pair["work"], production=True)

    monkeypatch.setenv("THTH_THREADS_WAIT_SECONDS", "0")

    with fake_ok_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        result = core.throw_once(account["name"], production_flag=True)

    assert result.exit_code == 0
    assert result.mode == "production"
    assert result.action == "post"
    assert result.post_id is not None

    # origin（bare）に push されたかを、独立した clone で検証する（work を直接見ない
    # ことで「push が本当に届いたか」を確かめる）。
    verify_dir = str(tmp_path / "verify")
    subprocess.run(["git", "clone", pair["bare"], verify_dir], check=True,
                    capture_output=True, text=True)
    verify_path = os.path.join(verify_dir, "docs", "sns", "queue", "2026-09-08-umami-bile.md")
    with open(verify_path, encoding="utf-8") as f:
        new_content = f.read()

    seed_lines = seed_content.split("\n")
    new_lines = new_content.split("\n")
    assert len(seed_lines) == len(new_lines)
    diff_lines = [i for i, (a, b) in enumerate(zip(seed_lines, new_lines)) if a != b]
    diff_keys = {new_lines[i].split(":", 1)[0].strip() for i in diff_lines}
    assert diff_keys == {"status", "post_id", "posted_at"}
    assert "status: posted" in new_content
    assert f"post_id: {result.post_id}" in new_content

    # 本文（front-matter 以降）は 1 バイトも変わっていない。
    def body_of(text: str) -> str:
        lines = text.split("\n")
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        return "\n".join(lines[end + 1:])

    assert body_of(seed_content) == body_of(new_content)

    log = run_git(pair["bare"], ["log", "--oneline", "-1"])
    assert "thth:" in log.stdout
