"""THTH の画面（MCP Apps）の柵: 画面に対応しない client には、今と 1 字も変わらない。

設計: docs/設計_THTH画面_MCPApps_2026-11-27.md §2 の 3・§6 の 3。
画面を足すのは、client が `initialize` の `capabilities.extensions` に
`io.modelcontextprotocol/ui` と `text/html;profile=mcp-app` を言ったときだけ。
言わない client（Claude Code・古い client・能力を何も言わない client）には、
次の 4 つを今のまま保つ:

1. `initialize` の能力は `{"tools": {}}` だけ（`resources` を言わない）。
2. どの道具にも `_meta` が付かず、画面だけの道具（`thth_ui_*`）が一覧に出ない。
3. `resources/list`・`resources/read` は「method not found」。
4. 道具の結果に `structuredContent` が付かない（AI に見える物を増やさない）。

さらに、能力の言い方が違う 4 通りの client で、道具の一覧が 1 字違わないことを見る。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

MCP_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp", "server.py")

UI_EXT = "io.modelcontextprotocol/ui"

# 画面に対応しない client の initialize の言い方（4 通り）。
NO_UI_CLIENTS = {
    "params が空": {},
    "能力が空": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "claude-code", "version": "0"}},
    "別の拡張だけ": {"protocolVersion": "2025-06-18",
                "capabilities": {"extensions": {"io.example/other": {}}},
                "clientInfo": {"name": "other", "version": "0"}},
    "ui の拡張でも html を言わない": {"protocolVersion": "2025-06-18",
                             "capabilities": {"extensions": {UI_EXT: {"mimeTypes": ["text/plain"]}}},
                             "clientInfo": {"name": "half", "version": "0"}},
}


def _session(init_params: dict) -> dict:
    """1 本のサーバに initialize から順に流し、id ごとの応答を返す。"""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init_params},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "ui://thth/queue"}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "thth_board", "arguments": {}}},
    ]
    proc = subprocess.run(
        [sys.executable, MCP_SERVER],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, env=dict(os.environ), timeout=60,
    )
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    by_id = {r.get("id"): r for r in responses}
    assert set(by_id) == {1, 2, 3, 4, 5}, (proc.stdout, proc.stderr)
    return by_id


@pytest.fixture
def sessions(isolated_account):
    return {name: _session(params) for name, params in NO_UI_CLIENTS.items()}


def test_画面に対応しないclientにはinitializeの能力がtoolsだけ(sessions):
    for name, by_id in sessions.items():
        assert by_id[1]["result"]["capabilities"] == {"tools": {}}, name


def test_画面に対応しないclientには道具に_metaが付かず画面だけの道具も出ない(sessions):
    for name, by_id in sessions.items():
        tools = by_id[2]["result"]["tools"]
        assert tools, name
        assert [t["name"] for t in tools if "_meta" in t] == [], name
        assert [t["name"] for t in tools if t["name"].startswith("thth_ui_")] == [], name


def test_画面に対応しないclientにはresourcesが無い(sessions):
    for name, by_id in sessions.items():
        for rid in (3, 4):
            assert by_id[rid].get("error", {}).get("code") == -32601, (name, by_id[rid])


def test_画面に対応しないclientには結果にstructuredContentが付かない(sessions):
    for name, by_id in sessions.items():
        result = by_id[5]["result"]
        assert result["isError"] is False, name
        assert "structuredContent" not in result, name
        assert "_meta" not in result, name


def test_画面に対応しないclientの道具の一覧は言い方によらず1字違わない(sessions):
    lists = {name: json.dumps(by_id[2]["result"], ensure_ascii=False, sort_keys=True)
             for name, by_id in sessions.items()}
    assert len(set(lists.values())) == 1, sorted(lists)
