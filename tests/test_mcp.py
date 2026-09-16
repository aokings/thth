"""薄い MCP（発注 §5 受け入れ 12）。CLI を subprocess で呼んで JSON を返すだけで、
業務論理を持たない。CLI を差し替えたら出力が変わることで確認する。"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import importlib.util

import pytest

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
    # 2026-09-13 に `before_you_post` を足した（設計 v2 §1・§6 v2-1・読むだけ）。
    # 2026-09-16 に `after_you_posted` を足した（設計「自分の泉」§2.2・T0-2・読むだけ）。
    # 2026-09-16 に `thread_read` を足した（設計「自分の泉」§2.1・T1-3・読むだけ）。
    # 2026-09-16 に `where_to_appear` を足した（設計「自分の泉」§2.3・T2-3・読むだけ）。
    # 2026-09-16 に `who_is_this` を足した（設計「自分の泉」§2.4・T3-3・読むだけ）。
    assert tool_names == {"thth_lint", "thth_queue", "thth_preview", "thth_board",
                          "thth_topic_context", "thth_topic_evaluate",
                          "thth_topic_decision", "before_you_post", "after_you_posted",
                          "thread_read", "where_to_appear", "who_is_this"}
    # 副作用のあるものは 1 つも出ていない。
    assert not (tool_names & {"thth_approve", "thth_throw", "thth_token",
                               "thth_auth", "thth_refresh", "thth_revoke",
                               "thth_app",
                               "thth_topic_observe", "thth_topic_record_decision"})
    call_result = by_id[3]["result"]
    payload = json.loads(call_result["content"][0]["text"])
    assert "accounts" in payload


# ============================================== thread_read（T1-3）

def test_thread_readの説明文は設計のままで固定(isolated_account):
    """**実装が 1 語でも足したら設計書でなく実装を戻す**（設計「自分の泉」§2 頭書き）。"""
    server = _load_server_module()
    tool = next(t for t in server.TOOLS if t["name"] == "thread_read")
    assert tool["description"] == (
        "返信を書く前に呼ぶ。この投稿の枝を、誰が・いつ・何を・誰に向けて"
        "言ったかの順で返す。何も保存しない")


def test_mcp_thread_readはcliと同じjsonが返る(isolated_account_factory, monkeypatch, tmp_path):
    from tests.conftest import run_thth
    from tests.test_threads_read_permissions import OTHER_POST, OTHER_POST_ID, _server

    with _server() as (base_url, requests):
        token_path = str(tmp_path / "threads.token")
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump({"access_token": "FAKE-SECRET", "user_id": "999999",
                      "username": "nigamilab", "scopes": None}, f)
        account = isolated_account_factory(
            "nigamilab-mcp-thread-test", media="threads", handle="nigamilab",
            token=token_path)
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)

        server = _load_server_module()
        result = server.call_tool(
            "thread_read", {"account": account["name"], "post_id": OTHER_POST_ID})

        cli_result = run_thth(["thread", account["name"], OTHER_POST_ID, "--json"])

    assert result["isError"] is False, result
    mcp_payload = json.loads(result["content"][0]["text"])
    assert cli_result.returncode == 0, cli_result.stdout + cli_result.stderr
    cli_payload = json.loads(cli_result.stdout)

    assert mcp_payload["root"]["post_id"] == OTHER_POST_ID
    assert mcp_payload["root"]["text"] == OTHER_POST["text"]
    assert mcp_payload["messages"] == []
    # **CLI と MCP は同じ CLI を呼ぶだけ**（`fetched_at` は毎回変わるので除く）。
    for key in ("root", "messages", "counts", "you_and_them"):
        assert mcp_payload[key] == cli_payload[key]


# ============================================== where_to_appear（T2-3）

def test_where_to_appearの説明文は設計のままで固定(isolated_account):
    """**実装が 1 語でも足したら設計書でなく実装を戻す**（設計「自分の泉」§2 頭書き）。"""
    server = _load_server_module()
    tool = next(t for t in server.TOOLS if t["name"] == "where_to_appear")
    assert tool["description"] == (
        "絡みに行く先を選ぶ前に呼ぶ。検索の一覧に、自分の履歴（この語・"
        "この相手で何が起きたか）を重ねて返す。選ぶのは呼ぶ側")


def test_where_to_appearはaccountもprojectも無ければ32602(isolated_account):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("where_to_appear", {"words": ["お茶"]})
    # account か project のどちらかがあれば通る。
    server.validate_arguments("where_to_appear",
                              {"account": "a", "words": ["お茶"]})
    server.validate_arguments("where_to_appear",
                              {"project": "p", "words": ["お茶"]})


def test_where_to_appearはwordsの要素が文字列でなければ断る(isolated_account):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments(
            "where_to_appear", {"account": "a", "words": [1]})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments(
            "where_to_appear", {"account": "a", "words": ["--json"]})


def test_mcp_where_to_appearはcliと同じjsonが返る(isolated_account_factory, monkeypatch, tmp_path):
    from tests.conftest import run_thth
    from tests.test_threads_read_permissions import _server

    with _server() as (base_url, _requests):
        token_path = str(tmp_path / "threads.token")
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump({"access_token": "FAKE-SECRET", "user_id": "999999",
                      "username": "nigamilab", "scopes": None}, f)
        account = isolated_account_factory(
            "nigamilab-mcp-where-test", media="threads", handle="nigamilab",
            token=token_path)
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)

        server = _load_server_module()
        result = server.call_tool(
            "where_to_appear", {"account": account["name"], "words": ["お茶"]})

        cli_result = run_thth(["where", account["name"], "お茶", "--json"])

    assert result["isError"] is False, result
    mcp_payload = json.loads(result["content"][0]["text"])
    assert cli_result.returncode == 0, cli_result.stdout + cli_result.stderr
    cli_payload = json.loads(cli_result.stdout)

    assert account["name"] in mcp_payload["by_account"]
    # **CLI と MCP は同じ CLI を呼ぶだけ**（`fetched_at` は毎回変わるので除く）。
    for key in ("account", "project", "words", "by_account", "cannot_say"):
        assert mcp_payload[key] == cli_payload[key]


def test_mcp_where_to_appearはprojectとrecentとlimitも渡す(tmp_path, monkeypatch):
    """`call_tool()` が `--project`・`--recent`・`--limit` を正しく組み立てることを、
    CLI を差し替えて確かめる（`test_mcpに業務論理が無い` と同じ手法）。"""
    server = _load_server_module()
    fake_cli = tmp_path / "fake-thth"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "print(json.dumps({'args': sys.argv[1:]}))\n"
    )
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(server, "THTH_BIN", str(fake_cli))

    result = server.call_tool("where_to_appear", {
        "project": "kopicha", "words": ["お茶", "コーヒー"],
        "recent": True, "limit": 10})
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"args": ["where", "--project", "kopicha", "お茶", "コーヒー",
                               "--recent", "--limit", "10", "--json"]}


# ============================================== who_is_this（T3-3）

def test_who_is_thisの説明文は設計のままで固定(isolated_account):
    """**実装が 1 語でも足したら設計書でなく実装を戻す**（設計「自分の泉」§2 頭書き）。"""
    server = _load_server_module()
    tool = next(t for t in server.TOOLS if t["name"] == "who_is_this")
    assert tool["description"] == (
        "返信する相手を確かめる。この仮名と自分のアカウントの接触の"
        "回数・時期・反応を返す。発言の内容は持たない")


def test_who_is_thisはaccountもprojectも無ければ32602(isolated_account):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("who_is_this", {"author_key": "0" * 16})
    # account か project のどちらかがあれば通る。
    server.validate_arguments("who_is_this",
                              {"account": "a", "author_key": "0" * 16})
    server.validate_arguments("who_is_this",
                              {"project": "p", "author_key": "0" * 16})


def test_who_is_thisはauthor_keyもusernameも無ければ32602(isolated_account):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("who_is_this", {"account": "a"})
    # author_key か username のどちらかがあれば通る。
    server.validate_arguments("who_is_this", {"account": "a", "author_key": "0" * 16})
    server.validate_arguments("who_is_this", {"account": "a", "username": "bob"})


def test_mcp_who_is_thisはcliと同じjsonが返る(isolated_account_factory, tmp_path):
    from tests.conftest import run_thth
    from thth import accounts as accounts_mod
    from thth import engagements as engagements_mod
    from thth.adapters import base as adapter_base

    account = isolated_account_factory(media="threads", handle="nigamilab")
    cfg = accounts_mod.load_account(account["name"])
    bob_key = adapter_base.author_key("threads", "bob")
    engagements_mod.append(cfg, account["name"], {
        "post_id": "P1", "reply_to": "R1", "root_post": "ROOT1",
        "author_key": bob_key, "account": account["name"], "medium": "threads",
        "topic": None, "kind": None, "hour_band": "朝",
        "posted_at": "2026-09-01T08:00:00+09:00", "found_by": "manual"})

    server = _load_server_module()
    result = server.call_tool(
        "who_is_this", {"account": account["name"], "author_key": bob_key})
    cli_result = run_thth(["who", account["name"], bob_key, "--json"])

    assert result["isError"] is False, result
    mcp_payload = json.loads(result["content"][0]["text"])
    assert cli_result.returncode == 0, cli_result.stdout + cli_result.stderr
    cli_payload = json.loads(cli_result.stdout)

    assert mcp_payload["author_key"] == bob_key
    for key in ("author_key", "met", "first", "last", "threads", "profile", "cannot_say"):
        assert mcp_payload[key] == cli_payload[key]


def test_mcp_who_is_thisはprojectとusernameとprofileも渡す(tmp_path, monkeypatch):
    """`call_tool()` が `--project`・`@username`・`--profile` を正しく組み立てる
    ことを、CLI を差し替えて確かめる（`test_mcpに業務論理が無い` と同じ手法）。"""
    server = _load_server_module()
    fake_cli = tmp_path / "fake-thth"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "print(json.dumps({'args': sys.argv[1:]}))\n"
    )
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(server, "THTH_BIN", str(fake_cli))

    result = server.call_tool("who_is_this", {
        "project": "kopicha", "username": "bob", "profile": True})
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"args": ["who", "--project", "kopicha", "@bob",
                               "--profile", "--json"]}
