"""3.3.0 B4 軽い種類 `friction`（迷った・分かりにくかった・同じ操作を繰り返した）。

`bug`・`request` に足す。提案が無くても置ける（何が起きたかだけの本文で通る）。
"""
from __future__ import annotations

import json

import pytest

from tests.test_v312_report_channel import user_server  # noqa: F401
from thth import cli, report_inbox


@pytest.fixture
def one(isolated_account_factory):
    return isolated_account_factory("kopicha-threads", project="kopicha")


def test_frictionは提案が無くても置けて一覧につまずきと出る(one, tmp_path, capsys):
    body = tmp_path / "body.txt"
    body.write_text("queue と schedule のどちらを見ればよいか迷った。", encoding="utf-8")
    assert cli.main(["report", "file", "kopicha-threads", "--kind", "friction",
                     "--title", "どちらを見るか迷った", "--body-file", str(body),
                     "--by", "kopicha-session", "--json"]) == 0
    filed = json.loads(capsys.readouterr().out)
    assert filed["kind"] == "friction"
    assert report_inbox.show(filed["report_id"])["kind"] == "friction"
    assert cli.main(["report", "list", "kopicha"]) == 0
    assert "つまずき" in capsys.readouterr().out
    assert report_inbox.KIND_LABELS["friction"] == "つまずき"


def test_知らない種類の断りは3つの種類を言う(one):
    with pytest.raises(report_inbox.ReportError) as caught:
        report_inbox.file_report("kopicha-threads", kind="idea", title="t", body="b", by="s")
    assert str(caught.value) == "invalid_kind"
    assert "friction" in report_inbox.NEXT["invalid_kind"]


def test_MCPでもfrictionを置ける(user_server):  # noqa: F811
    _root, _path, _value, server = user_server
    schema = next(tool for tool in server.REPORT_TOOLS
                  if tool["name"] == "thth_report_file")["inputSchema"]
    assert schema["properties"]["kind"]["enum"] == ["bug", "request", "friction"]
    result = server.call_tool("thth_report_file", {
        "account": "first", "kind": "friction", "title": "同じ操作を 3 回繰り返した",
        "body": "approve の一段目を 3 回打った"})
    assert not result.get("isError")
    assert json.loads(result["content"][0]["text"])["kind"] == "friction"
