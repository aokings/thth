"""`thth where --tag`・`--author`（Threads の `search_mode=TAG`・`author_username`）。

調査「Threads で伸ばす——X の 1 枚から活かすもの」（2026-10-12）§7 決定 1（masaru「ふたつともやる」）。
API の引数を渡すだけ・何も保存しない。Threads 以外の account では `cannot_say` に理由。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests.conftest import run_thth
from tests.test_threads_read_permissions import _adapter, _server
from tests.test_where_cli import _data_snapshot, two_media_project  # noqa: F401
from thth import accounts as accounts_mod
from thth.adapters import base as adapter_base


def _search_params(requests):
    return [q for _m, p, q in requests if p == "/v1.0/keyword_search"]


def test_adapter_passes_search_mode_and_author_username():
    with _server() as (base_url, requests):
        _adapter(base_url).keyword_search("#お茶", search_mode="TAG", author_username="alice", limit=3)
    params, = _search_params(requests)
    assert params["q"] == "お茶" and params["search_mode"] == "TAG"
    assert params["author_username"] == "alice"


def test_adapter_default_request_is_unchanged():
    with _server() as (base_url, requests):
        _adapter(base_url).keyword_search("お茶", limit=3)
    params, = _search_params(requests)
    assert "search_mode" not in params and "author_username" not in params
    assert params["q"] == "お茶"


def test_adapter_refuses_bad_mode_and_author_before_request():
    with _server() as (base_url, requests):
        a = _adapter(base_url)
        with pytest.raises(adapter_base.AdapterError):
            a.keyword_search("お茶", search_mode="USER")
        with pytest.raises(adapter_base.AdapterError):
            a.keyword_search("お茶", author_username="bad name")
        with pytest.raises(adapter_base.AdapterError):
            a.keyword_search("#", search_mode="TAG")
    assert _search_params(requests) == []


def test_where_tag_and_author_on_threads_and_not_on_bluesky(two_media_project):
    threads_acc, bluesky_acc = two_media_project
    cfg = accounts_mod.load_account(threads_acc["name"])
    before = _data_snapshot(cfg, threads_acc["name"])
    with _server() as (base_url, requests):
        r = run_thth(["where", "--project", "kopicha", "コーヒー", "--tag", "--author", "@alice", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    params, = _search_params(requests)
    assert params["search_mode"] == "TAG" and params["author_username"] == "alice"
    node = result["by_account"][threads_acc["name"]]
    assert node["by_word"]["コーヒー"]["query"] == {"search_mode": "TAG", "author_username": "alice"}
    assert node["by_word"]["コーヒー"]["posts"]   # 本文・username は今までどおり返すだけ
    # Bluesky は使えないと言う（黙って普通の検索に落とさない）。他の account は続ける。
    assert bluesky_acc["name"] not in result["by_account"]
    assert any(bluesky_acc["name"] in line and "by_tag" in line for line in result["cannot_say"])
    # 何も保存しない。
    assert _data_snapshot(cfg, threads_acc["name"]) == before


def test_where_without_flags_has_no_query_block(two_media_project):
    threads_acc, _ = two_media_project
    with _server() as (base_url, requests):
        r = run_thth(["where", threads_acc["name"], "コーヒー", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    node = json.loads(r.stdout)["by_account"][threads_acc["name"]]
    assert "query" not in node["by_word"]["コーヒー"]
    params, = _search_params(requests)
    assert "search_mode" not in params


def test_where_refuses_a_malformed_author(two_media_project):
    threads_acc, _ = two_media_project
    r = run_thth(["where", threads_acc["name"], "コーヒー", "--author", "not a name"])
    assert r.returncode == 2 and "author" in r.stderr


def test_where_on_x_says_to_write_operators_in_the_word(tmp_path, isolated_account_factory, monkeypatch):
    from thth import cli
    from thth.adapters import x as x_adapter
    token = tmp_path / "x.token"
    token.write_text(json.dumps({"access_token": "T", "user_id": "123", "username": "k"}), encoding="utf-8")
    isolated_account_factory("tag-x", media="x", handle="k", token=str(token))
    monkeypatch.setattr(x_adapter, "request", lambda *a, **k: pytest.fail("X を叩いた"))
    from thth import where_cli
    result = where_cli.answer(account_name="tag-x", words=["コーヒー"], tag=True)
    assert result["by_account"] == {}
    assert any("from:<username>" in line for line in result["cannot_say"])


def test_mcp_where_to_appear_passes_tag_and_author(monkeypatch):
    import importlib.util
    from tests.test_mcp import MCP_SERVER
    spec = importlib.util.spec_from_file_location("thth_mcp_server_tag", MCP_SERVER)
    server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)
    props = next(t for t in server.TOOLS if t["name"] == "where_to_appear")["inputSchema"]["properties"]
    assert props["tag"]["type"] == "boolean" and props["author"]["type"] == "string"
    seen = []
    monkeypatch.setattr(server, "run_cli",
                        lambda args, **kw: (seen.append(args), subprocess.CompletedProcess(args, 0, "{}", ""))[1])
    server.call_tool("where_to_appear", {"account": "a", "words": ["お茶"], "tag": True, "author": "alice"})
    assert seen and seen[0][-4:] == ["--tag", "--author", "alice", "--json"]
