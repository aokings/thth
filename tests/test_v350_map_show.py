"""観測の地図 第 4 段——`thth map show` と MCP `thth_map_show`（設計 3.5.0 §3）。

見るのは:
  - 点ごとに自分の層・広場の層・世間の層（既定は無効で null と `world_layer_disabled`）。
  - 窓（since〜until）・点と線の数と上限・保持の日数が出る。点が無ければ `no_map_nodes`。
  - `--node` はその点と包含で隣り合う点だけ。無い点・読めない since・台帳に無い project は
    静的な理由で断る（`--json` は `{"cannot_say": [...]}`）。
  - MCP: 手元の stdio は CLI を呼ぶだけ。サーバ型は credential が許した project だけ
    （他の持ち主の project は在っても無くても同じ `scope_unavailable`）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import cli, jst, map_store, map_view
from tests.test_analytics_comparison import seed
from tests.test_v340_plaza_store import owners, post  # noqa: F401  (fixture)
from tests.test_v340_plaza_mcp import _tenant, _text


@pytest.fixture
def mapped(owners):
    for word in ("コーヒー", "スペシャルティコーヒー", "紅茶"):
        map_store.add_node("kopicha", word, by="masaru")
    map_store.add_edge("kopicha", "スペシャルティコーヒー", "コーヒー", by="masaru")
    return owners


def test_点ごとに層が並び世間の層は既定で無効(mapped, capsys, monkeypatch):
    monkeypatch.delenv("THTH_MAP_WORLD", raising=False)
    now = jst.now_jst()
    account = mapped["kopicha-threads"]
    for i in range(5):
        seed(account, f"c{i}", now - datetime.timedelta(days=2, hours=i), value=10 * (i + 1),
             extra={"topic": "コーヒー"})
    post(title="コーヒーは朝がよい", body="気づき")
    rc = cli.main(["map", "show", "kopicha", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and payload["report_type"] == "map_show" and payload["project"] == "kopicha"
    assert [node["word"] for node in payload["nodes"]] == ["コーヒー", "スペシャルティコーヒー", "紅茶"]
    coffee = payload["nodes"][0]
    mine = coffee["self"]["by_account"]["kopicha-threads"]
    assert mine["posts"] == 5 and mine["metrics"]["views"]["median"] == 30
    assert coffee["plaza"]["own"]["finding"] == 1
    assert coffee["world"] == {"cannot_say": "world_layer_disabled", "by_medium": None}
    assert payload["world_layer"] == {"enabled": False, "cannot_say": "world_layer_disabled"}
    assert payload["edges"]["broader"] == [{"narrower": "スペシャルティコーヒー", "broader": "コーヒー",
                                            "kind": "broader"}]
    assert payload["edges"]["co"] == []
    assert payload["limits"] == {"n_nodes": 3, "max_nodes": 20, "n_edges": 1, "retention_days": 180}
    assert payload["window"]["until"] == jst.iso(now)
    assert payload["window"]["since"] == jst.iso(now - datetime.timedelta(days=30))
    assert set(payload["accounts"]) == {"kopicha-threads", "kopicha-bsky", "kopicha-mstdn"}
    # 人向けの画面。
    assert cli.main(["map", "show", "kopicha"]) == 0
    out = capsys.readouterr().out
    assert "世間の層: 無効（world_layer_disabled）" in out and "[コーヒー]" in out
    assert "⊃ スペシャルティコーヒー" in out


def test_nodeは隣り合う点だけ(mapped, capsys):
    rc = cli.main(["map", "show", "kopicha", "--node", "#コーヒー", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [node["word"] for node in payload["nodes"]] == ["コーヒー", "スペシャルティコーヒー"]
    assert payload["neighbors"] == {"node": "コーヒー", "broader": [],
                                    "narrower": ["スペシャルティコーヒー"], "co": []}


@pytest.mark.parametrize("argv, reason", [
    (["map", "show", "kopicha", "--node", "緑茶"], "node_not_found"),
    (["map", "show", "kopicha", "--since", "昨日"], "invalid_since"),
    (["map", "show", "nobody"], "project_unknown"),
])
def test_断りは静的な理由(mapped, capsys, argv, reason):
    rc = cli.main(argv + ["--json"])
    captured = capsys.readouterr()
    assert rc == 2 and json.loads(captured.out) == {"cannot_say": [reason]}
    assert captured.err.startswith(reason + ": ")


def test_点が無ければno_map_nodes(owners):
    payload = map_view.show("other")
    assert payload["nodes"] == [] and payload["cannot_say"] == ["no_map_nodes"]


def test_手元のMCPはCLIを呼ぶだけ(monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    tool = next(item for item in server.TOOLS if item["name"] == "thth_map_show")
    assert tool["description"].startswith("話題を選ぶ前に呼ぶ。") and "読むだけ" in tool["description"]
    seen = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(server, "run_cli", lambda args, **kwargs: seen.append(args) or Done())
    server.call_tool("thth_map_show", {"project": "kopicha"})
    server.call_tool("thth_map_show", {"project": "kopicha", "node": "コーヒー", "since": "12w"})
    assert seen == [["map", "show", "kopicha", "--json"],
                    ["map", "show", "kopicha", "--node", "コーヒー", "--since", "12w", "--json"]]
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_map_show", {"node": "コーヒー"})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_map_show", {"project": "kopicha", "by": "x"})
    # 点と線を足す口は MCP に出さない（人が CLI で足す）。
    assert not any("map" in item["name"] and item["name"] != "thth_map_show" for item in server.TOOLS)


def test_サーバ型はcredentialのprojectだけ(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert "thth_map_show" in names
    map_store.add_node("kopicha", "コーヒー", by="masaru")
    map_store.add_node("other", "他の持ち主の話題", by="masaru")
    result = server.call_tool("thth_map_show", {"project": "kopicha"})
    assert not result.get("isError"), result
    payload = json.loads(_text(result))
    assert payload["project"] == "kopicha" and set(payload["accounts"]) == {"first", "third"}
    assert [node["word"] for node in payload["nodes"]] == ["コーヒー"]
    by_account = json.loads(_text(server.call_tool("thth_map_show", {"project": "first"})))
    assert by_account["project"] == "kopicha"
    for target in ("other", "second", "nobody"):
        denied = server.call_tool("thth_map_show", {"project": target})
        assert denied["isError"] and _text(denied) == "scope_unavailable", target
    assert "他の持ち主の話題" not in json.dumps(payload, ensure_ascii=False)
