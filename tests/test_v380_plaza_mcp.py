"""3.8.0 の MCP（設計 3.8.0 §A〜§E をサーバ型の口から）。

見るのは:
  - 管理者の口に `thth_admin_plaza_owner_set`・`thth_admin_plaza_owner_unset`（by 必須）。
  - 利用者の `thth_plaza_post` に visibility（project・owner）・trial_due・from_*。open は無い。
  - `thth_plaza_show` が読んだことを控える・`thth_plaza_digest` は credential の範囲だけ。
"""
from __future__ import annotations

import json

import pytest

from thth import after_cli, plaza, plaza_reads
from tests.test_v340_plaza_mcp import _post, _tenant, _text


def _admin(tmp_path, monkeypatch):
    return _tenant(tmp_path, monkeypatch, scope="admin", actor=None,
                   allowed={"first": "kopicha", "second": "other", "third": "kopicha"})


def test_管理者の口で組を登録して解く(tmp_path, monkeypatch):
    _root, server = _admin(tmp_path, monkeypatch)
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert {"thth_admin_plaza_owner_set", "thth_admin_plaza_owner_unset"} <= names
    done = server.call_tool("thth_admin_plaza_owner_set",
                            {"owner": "masaru", "projects": ["kopicha", "other"], "by": "operator"})
    assert json.loads(_text(done))["projects"] == ["kopicha", "other"]
    listed = json.loads(_text(server.call_tool("thth_admin_plaza_list", {})))
    assert listed["owners"] == {"masaru": ["kopicha", "other"]}
    denied = server.call_tool("thth_admin_plaza_owner_set", {"owner": "masaru", "projects": "kopicha",
                                                             "by": "operator"})
    assert denied["isError"] and _text(denied) == "invalid_request"
    denied = server.call_tool("thth_admin_plaza_owner_set", {"owner": "masaru", "projects": ["kopicha"]})
    assert denied["isError"] and _text(denied) == "invalid_request"
    undone = server.call_tool("thth_admin_plaza_owner_unset", {"owner": "masaru", "by": "operator"})
    assert json.loads(_text(undone))["report_type"] == "plaza_owner_unset"
    missing = server.call_tool("thth_admin_plaza_owner_unset", {"owner": "masaru", "by": "operator"})
    assert missing["isError"] and _text(missing) == "plaza_owner_not_found"


def test_利用者はownerに置けて組の相手が読める_openは無い(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    refused = _post(server, visibility="owner")
    assert refused["isError"] and _text(refused) == "plaza_owner_unregistered"
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    posted = json.loads(_text(_post(server, visibility="owner", title="組に見せる")))
    assert posted["scope"] == "owner"
    shown = plaza.show(posted["plaza_id"], plaza.Viewer({"second": "other"}))
    assert shown["view"] == "owner"
    denied = _post(server, visibility="open", title="open は無い")
    assert denied["isError"] and _text(denied) == "open_requires_cli"
    tools = {t["name"]: t for t in server.server_tools(server.authenticated_context())}
    assert tools["thth_plaza_post"]["inputSchema"]["properties"]["visibility"]["enum"] == [
        "project", "owner"]


def test_fromの口_observedは道具の数字だけ(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    node = {"posts": {"n": 4, "views_24h": {"median": 88, "n": 4}},
            "engagements": {"n": 0, "reacted": 0, "likes_24h": {"median": None, "n": 0},
                            "replies_back_24h": {"median": None, "n": 0},
                            "views_24h": {"median": None, "n": 0}}, "cannot_say": []}
    monkeypatch.setattr(after_cli, "answer", lambda *a, **k: node)
    result = _post(server, kind="finding", title="道具の数字", body="本文の 999",
                   from_tool="after", from_account="third")
    posted = json.loads(_text(result))
    assert posted["from"] == "after" and posted["evidence_level"] == "observed"
    record = plaza.STORE.get(posted["plaza_id"])
    assert record["tool_numbers"]["numbers"]["posts"]["views_24h"] == {"median": 88, "n": 4}
    assert "999" not in json.dumps(record["tool_numbers"])
    out_of_scope = _post(server, title="他人", from_tool="after", from_account="second")
    assert out_of_scope["isError"] and _text(out_of_scope) == "from_out_of_scope"
    both = _post(server, title="両方", from_tool="after", from_account="third",
                 from_report="r20260909-00000000")
    assert both["isError"] and _text(both) == "invalid_from"
    missing = _post(server, title="account が無い", from_tool="after")
    assert missing["isError"] and _text(missing) == "invalid_from"


def test_showは読んだことを控え_digestはcredentialの範囲だけ(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    posted = json.loads(_text(_post(server, account="third", title="Bluesky の気づき",
                                    trial_due="2026-10-08")))
    assert plaza.STORE.get(posted["plaza_id"])["trial_due"] == "2026-10-08T00:00:00+09:00"
    shown = json.loads(_text(server.call_tool("thth_plaza_show", {"plaza_id": posted["plaza_id"]})))
    assert shown["read_recorded"] is True
    state = plaza_reads.load({"first": "kopicha", "third": "kopicha"})
    assert posted["plaza_id"] in plaza_reads.read_ids(state)
    digest = json.loads(_text(server.call_tool("thth_plaza_digest", {})))
    assert digest["report_type"] == "plaza_digest" and digest["n"] == 0 and digest["denominator"] == 1
    denied = server.call_tool("thth_plaza_digest", {"target": "other"})
    assert denied["isError"] and _text(denied) == "scope_unavailable"
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    by_owner = json.loads(_text(server.call_tool("thth_plaza_digest", {"target": "masaru"})))
    assert by_owner["basis"] == "owner"


@pytest.mark.parametrize("arguments", [{"from_window_days": "7"}, {"trial_due": 20261008}])
def test_型の違う値は断る(tmp_path, monkeypatch, arguments):
    _root, server = _tenant(tmp_path, monkeypatch)
    denied = _post(server, **arguments)
    assert denied["isError"] and _text(denied) == "invalid_request"
