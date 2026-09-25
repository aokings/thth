"""3.11.1 件 1: `thth observe … --no-world`（media-hub 向け・回答
`docs/回答_media-hub_observeを毎朝読む件_2026-09-25.md` の 5）。

第 3 段（世間・監視語の検索）を一切呼ばず、`cannot_say: "world_skipped"` にする。
API を叩くのは言及などだけ（`calls` に検索が出ない）で、`where` の実行記録も
書かない（`where_cli.answer()` 自体を呼ばない）。`--no-mark` の振る舞いは変えない。
"""
from __future__ import annotations

import json

import pytest

from thth import cli, jst, morning, where_cli
from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one, sections  # noqa: F401


def world_cell(payload):
    return sections(payload)["world"]


# ------------------------------------------------------------------ 意味試験

def test_no_worldはworldを呼ばずworld_skippedになる(one, monkeypatch):
    monkeypatch.setattr(where_cli, "answer",
                        lambda **k: pytest.fail("--no-world なのに where_cli.answer を呼んだ"))
    payload = morning.build(PROJECT, now=NOW, mark=False, no_world=True)
    cell = world_cell(payload)
    assert cell["value"] is None and cell["cannot_say"] == "world_skipped"
    # 言及の口は動く（世間だけを止めた）。
    assert sections(payload)["unanswered"]["value"]["by_account"][ACCOUNT][
        "value"]["mentions"]["value"]["n"] == 1


def test_no_worldではkeyword_searchを1回も叩かない(one, monkeypatch):
    payload = morning.build(PROJECT, now=NOW, mark=False, no_world=True)
    fake = one["fake"]
    assert ("keyword_search", "コーヒー") not in fake.calls
    assert all(call[0] != "keyword_search" for call in fake.calls)
    assert "keyword_search" not in json.dumps(payload["calls"])
    # 言及の 1 回だけ。
    assert payload["calls"] == {"threads": {"mentions": 1}}


def test_no_world以外は今まで通り世間が出る(one):
    payload = morning.build(PROJECT, now=NOW, mark=False, no_world=False)
    cell = world_cell(payload)
    assert cell["cannot_say"] is None
    assert cell["value"]["by_account"][ACCOUNT]["value"]["by_word"]["コーヒー"]["value"]["n"] == 2


def test_no_markの振る舞いは変えない(one, thth_root):
    from tests.test_v310_morning import cursor_path
    path = cursor_path(thth_root, ACCOUNT)
    payload = morning.build(PROJECT, now=NOW, mark=False, no_world=True)
    assert payload["marked"] == [] and not path.exists()


# ------------------------------------------------------------------------ CLI

def test_CLIからno_worldが効く(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--json", "--no-mark", "--no-world"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert world_cell(payload)["cannot_say"] == "world_skipped"
    assert "keyword_search" not in json.dumps(payload["calls"])


def test_morning別名でも同じ(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["morning", PROJECT, "--json", "--no-mark", "--no-world"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert world_cell(payload)["cannot_say"] == "world_skipped"


def test_人向けの1枚でもworld_skippedが読める(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--no-mark", "--no-world"]) == 0
    out = capsys.readouterr().out
    assert "world_skipped" in out


# ------------------------------------------------------------------------ MCP

def test_MCPのthth_observeにno_worldを足せる(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    seen = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(server, "run_cli", lambda args, **kwargs: seen.append(args) or Done())
    server.call_tool("thth_observe", {"target": PROJECT, "no_world": True})
    server.call_tool("thth_observe", {"target": PROJECT})
    assert seen == [["observe", PROJECT, "--no-world", "--json"],
                    ["observe", PROJECT, "--json"]]
    assert server.validate_arguments("thth_observe", {"target": PROJECT, "no_world": True})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_observe", {"target": PROJECT, "no_world": "yes"})


def test_MCPから実際にworld_skippedが返る(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    server = _load_server_module()
    result = server.call_tool("thth_observe", {"target": PROJECT, "mark": False, "no_world": True})
    assert not result.get("isError")
    payload = json.loads(result["content"][0]["text"])
    assert world_cell(payload)["cannot_say"] == "world_skipped"
