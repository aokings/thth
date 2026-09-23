"""3.6.0 広場の施策に目的を持てる（設計 3.6.0 §A2）。

最小限（記録の欄と表示）: `post` の `goal`（4 語・任意）・一覧と 1 件の表示・CLI の
`--goal`・MCP の `thth_plaza_post` の `goal`。open・二段確認・返信の処理は変えない。
"""
from __future__ import annotations

import json

import pytest

from thth import plaza, plaza_cli
from tests.test_v340_plaza_mcp import _post as mcp_post, _tenant, _text
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)


def test_施策に目的を持てる_一覧と1件に出る(owners):
    result = post(kind="measure", goal="reach", title="冒頭を問いにする")
    shown = plaza.show(result["plaza_id"], viewer("kopicha-bsky"))
    assert shown["goal"] == "reach"
    listed = plaza.list_posts(viewer("kopicha-threads"))["posts"][0]
    assert listed["goal"] == "reach"
    lines = []
    plaza_cli.render_list(plaza.list_posts(viewer("kopicha-threads")), out=lines.append)
    assert lines[-1].endswith("目的 reach")


def test_目的は任意_4語以外は断る(owners):
    assert plaza.show(post(title="目的なし")["plaza_id"], viewer("kopicha-threads"))["goal"] is None
    for bad in ("sales", "Reach", "none", ""):
        with pytest.raises(plaza.PlazaError, match="^invalid_goal$"):
            post(goal=bad, title=f"bad {bad}")
    assert "invalid_goal" in plaza.REASONS and "invalid_goal" in plaza.NEXT


def test_目的を持たない3_5_0の書き込みも読める(owners):
    result = post(title="古い書き込み")
    record = next(row for row in plaza.load_all()[0] if row["plaza_id"] == result["plaza_id"])
    record.pop("goal")
    assert plaza._valid(record)
    record["goal"] = "sales"
    assert not plaza._valid(record)


def test_open_の写しでも目的は見える(owners, monkeypatch):
    monkeypatch.setattr(plaza, "members", lambda: frozenset({"kopicha", "other"}))
    result = post(goal="follow", visibility="open", title="フォローの型")
    shown = plaza.show(result["plaza_id"], viewer("other-threads"))
    assert shown["view"] == "open" and shown["goal"] == "follow"


def test_MCPで目的を付けて置ける(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    posted = json.loads(_text(mcp_post(server, goal="click")))
    shown = json.loads(_text(server.call_tool("thth_plaza_show", {"plaza_id": posted["plaza_id"]})))
    assert shown["goal"] == "click"
    refused = mcp_post(server, goal="sales", title="別の題")
    assert refused.get("isError")
