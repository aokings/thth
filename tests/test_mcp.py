"""薄い MCP（発注 §5 受け入れ 12）。CLI を subprocess で呼んで JSON を返すだけで、
業務論理を持たない。CLI を差し替えたら出力が変わることで確認する。"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import importlib.util

from tests.conftest import FIXTURES_DIR

MCP_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp", "server.py")


def _load_server_module():
    spec = importlib.util.spec_from_file_location("thth_mcp_server", MCP_SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_thth_lintはcliのjson出力をそのまま返す(isolated_account):
    server = _load_server_module()
    path = os.path.join(FIXTURES_DIR, "umami-bile.md")
    result = server.call_tool("thth_lint", {"file": path})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["errors"] == []


def test_thth_boardはcliのjson出力をそのまま返す(isolated_account):
    server = _load_server_module()
    result = server.call_tool("thth_board", {})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert "accounts" in payload


def test_mcpに業務論理が無い_cliを差し替えると出力が変わる(tmp_path, monkeypatch):
    """`call_tool` は subprocess で CLI を呼ぶだけであることを、CLI 差し替えで確認する。
    MCP 自身が結果を作っている（判断を持っている）なら、CLI を差し替えても出力は
    変わらないはず。"""
    server = _load_server_module()

    fake_cli = tmp_path / "fake-thth"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "print(json.dumps({'fake': True, 'args': sys.argv[1:]}))\n"
    )
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(server, "THTH_BIN", str(fake_cli))

    result = server.call_tool("thth_board", {})
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"fake": True, "args": ["board", "--json"]}


def test_mcp_stdioでtools_listとtools_callが通る(isolated_account):
    proc = subprocess.Popen(
        [sys.executable, MCP_SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=dict(os.environ),
    )
    try:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "thth_board", "arguments": {}}},
        ]
        for req in requests:
            proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        responses = []
        for _ in range(3):  # initialize・tools/list・tools/call の 3 件（notify は無応答）
            line = proc.stdout.readline()
            if not line:
                break
            responses.append(json.loads(line))
    finally:
        proc.wait(timeout=10)

    by_id = {r["id"]: r for r in responses}
    assert by_id[1]["result"]["serverInfo"]["name"] == "thth"
    tool_names = {t["name"] for t in by_id[2]["result"]["tools"]}
    # **MCP に出す道具は明示的に固定する。** 増えたら必ずここが落ちる——
    # `approve` や `throw` や `token` が黙って混ざらないための見張り。
    # 2026-09-11 にトピック提案の**読み取り 3 本**を足した（設計 §7）。
    assert tool_names == {"thth_lint", "thth_queue", "thth_preview", "thth_board",
                          "thth_topic_context", "thth_topic_evaluate",
                          "thth_topic_decision"}
    # 副作用のあるものは 1 つも出ていない。
    assert not (tool_names & {"thth_approve", "thth_throw", "thth_token",
                               "thth_auth", "thth_refresh", "thth_revoke",
                               "thth_topic_observe", "thth_topic_record_decision"})
    call_result = by_id[3]["result"]
    payload = json.loads(call_result["content"][0]["text"])
    assert "accounts" in payload
