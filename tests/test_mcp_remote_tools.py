"""段 K の柵 5: リモート MCP の道具の表（`callback/src/mcp-tools.js`）は、括りの通り道の表から作った物と 1 字も違わない。

設計: docs/設計_THTHアカウント（括り）_共通の層_叩き台_2026-11-26.md §6.1。Worker は表の通りに引数を argv に組むだけで、
通すかは VM の `server_passthrough` が決める。表がずれたまま出すと、Worker が出す道具と VM が通す命令が食い違う。
"""
from __future__ import annotations

import json

from thth import mcp_remote_tools as mrt
from thth import server_passthrough as sp


def _table():
    text = mrt.JSON_PATH.read_text(encoding="utf-8")
    body = text[text.index("export default ") + len("export default "):].rstrip().removesuffix(";")
    return json.loads(body)


def test_checked_in_table_is_the_generated_one():
    assert mrt.JSON_PATH.read_text(encoding="utf-8") == mrt.render(), \
        "callback/src/mcp-tools.js が通り道の表と食い違う（python -m thth.mcp_remote_tools --write で作り直す）"


def test_every_pass_command_is_a_tool_and_nothing_else():
    table = _table()
    assert table["operation"] == "ws_command"
    assert [tool["command"] for tool in table["tools"]] == sorted(sp.PASS) + sorted(sp.WRITE_PASS)
    assert set(mrt.DESCRIPTIONS) == set(sp.ALL)


def test_tool_flags_mirror_the_spec():
    for tool in _table()["tools"]:
        spec = sp.ALL[tool["command"]]
        argv = tool["argv"]
        assert set(argv["values"].values()) == set(spec.values), tool["name"]
        assert set(argv["texts"].values()) == set(spec.texts), tool["name"]
        assert set(argv["flags"].values()) == set(spec.flags) - mrt.HIDDEN_FLAGS, tool["name"]
        assert argv["always"] == (["--json"] if "--json" in spec.flags else [])
        assert tool["writes"] is (tool["command"] in sp.WRITE_PASS)
        props = tool["inputSchema"]["properties"]
        assert tool["inputSchema"]["additionalProperties"] is False
        # 表に無い旗（--confirm-file・--digest-only・書く命令の --by）は道具にも無い（analytics-report の --by は切り口）。
        assert "confirm_file" not in props and "digest_only" not in props
        assert not (tool["writes"] and "by" in props)
        if tool["writes"]:
            assert {"files", "account", "confirm"} <= set(props)
            assert set(tool["inputSchema"]["required"]) == {"files", "account"}
            assert tool["confirm"] == ("vm" if "--confirm" in spec.values else "worker")
        else:
            assert "confirm" not in props and tool["confirm"] is None


def test_check_mode_turns_red_when_the_table_drifts(monkeypatch, tmp_path):
    stale = tmp_path / "mcp-tools.js"
    stale.write_text(mrt.render().replace('"board"', '"board2"', 1), encoding="utf-8")
    monkeypatch.setattr(mrt, "JSON_PATH", stale)
    assert mrt.main(["--check"]) == 1
    stale.write_text(mrt.render(), encoding="utf-8")
    assert mrt.main(["--check"]) == 0


def test_every_pass_command_is_a_tool_or_says_why_not():
    """通り道に足した命令は、道具にするか、出さない理由を `NOT_TOOLS` に書くか、どちらか（黙って漏らさない）。"""
    tools = {tool["command"] for tool in _table()["tools"]}
    assert tools | set(mrt.NOT_TOOLS) == set(sp.PASS_COMMANDS)
    assert not tools & set(mrt.NOT_TOOLS)
    assert all(len(why) > 40 for why in mrt.NOT_TOOLS.values())
    assert "thth_reply" not in {tool["name"] for tool in _table()["tools"]}


def test_hold_is_a_worker_confirmed_write_tool():
    hold = next(tool for tool in _table()["tools"] if tool["command"] == "hold")
    assert hold["writes"] is True and hold["confirm"] == "worker"
    assert {"until", "reason", "undo", "account", "files"} <= set(hold["inputSchema"]["properties"])
    assert hold["annotations"]["destructiveHint"] is True
