"""3.2.0 §4.5: 報告の口の直し 3 件（kopicha の報告 r20260923-6fc4c7c2 から）。

1. 報告した側の追記: `thth report add <id> --body-file`（MCP `thth_report_add`）。
   自分の project の開いている報告にだけ（外は `report_not_found`・閉じた報告は
   `report_closed`）。返事と同じ列に `role: "reporter"` で入り、show と
   handoff の `tool.reports` に出る。4,000 字・秘密の検査・変更ログ `report_added`。
2. 置く前の似た報告: `report file` の応答に `similar`（同じ project の開いている
   報告で題の語が 1 つ以上重なるもの・最大 5 件・静的な照合）。置くのは止めない。
3. 一覧に置いた人: `report list`・`admin reports list` の行に `reporter`。
"""
from __future__ import annotations

import io
import json

import pytest

from tests.test_v312_report_inbox import BODY, _body_file, _file, two_projects  # noqa: F401
from tests.test_v312_report_mcp_user import _tenant, _text
from thth import admin_log, cli, report_inbox


def _mcp_file(server, **overrides):
    arguments = {"account": "first", "kind": "bug", "title": "replies が混ざる",
                 "body": "他の account の返信が返ってくる"}
    arguments.update(overrides)
    result = server.call_tool("thth_report_file", arguments)
    assert not result.get("isError"), result
    return json.loads(_text(result))


# ---------------------------------------------------------------- 1. 追記

def test_MCPで報告した側が書き足すとshowとhandoffに出る(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert "thth_report_add" in names
    filed = _mcp_file(server)
    added = server.call_tool("thth_report_add", {"report_id": filed["report_id"],
                                                 "text": "再現しました。13:26 の run でも同じ"})
    assert not added.get("isError"), added
    payload = json.loads(_text(added))
    assert payload["report_type"] == "report_added" and payload["n_replies"] == 1
    shown = json.loads(_text(server.call_tool("thth_report_show",
                                              {"report_id": filed["report_id"]})))
    [row] = shown["replies"]
    assert row["role"] == "reporter" and row["by"] == "kopicha-person"
    assert row["text"] == "再現しました。13:26 の run でも同じ"
    rows, _ = admin_log.read(event="report_added")
    assert [(r["by"], r["via"], r["account"]) for r in rows] == [("kopicha-person", "mcp", "first")]
    assert "再現しました" not in json.dumps(rows, ensure_ascii=False), "変更ログは presence-only"
    summary = report_inbox.handoff_summary({"first": {"project": "kopicha"}}, [])
    assert [(r["role"], r["by"]) for r in summary["new_replies"]] == [("reporter", "kopicha-person")]


def test_他のprojectの報告には書き足せない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    theirs = report_inbox.file_report("second", kind="bug", title="他", body="他の project", by="x")
    closed_theirs = report_inbox.file_report("second", kind="bug", title="他 2", body="閉じた", by="x")
    report_inbox.close(closed_theirs["report_id"], by="impl", reason="fixed", version="3.1.2")
    for report_id in (theirs["report_id"], closed_theirs["report_id"], "r20260909-00000000"):
        denied = server.call_tool("thth_report_add", {"report_id": report_id, "text": "足す"})
        assert denied["isError"] and _text(denied) == "report_not_found", report_id
    assert report_inbox.show(theirs["report_id"])["replies"] == []


def test_閉じた報告には書き足せない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    filed = _mcp_file(server)
    report_inbox.close(filed["report_id"], by="impl", reason="fixed", version="3.1.2")
    denied = server.call_tool("thth_report_add", {"report_id": filed["report_id"], "text": "足す"})
    assert denied["isError"] and _text(denied) == "report_closed"


@pytest.mark.parametrize("text,reason", [
    ("token: AbCdEf0123456789xyz0", "secret_detected"),
    ("あ" * 4001, "report_too_long"),
    ("   ", "invalid_report"),
])
def test_追記の断りは返事と同じ規律(tmp_path, monkeypatch, text, reason):
    _root, server = _tenant(tmp_path, monkeypatch)
    filed = _mcp_file(server)
    denied = server.call_tool("thth_report_add", {"report_id": filed["report_id"], "text": text})
    assert denied["isError"] and _text(denied) == reason
    assert "AbCdEf0123456789xyz0" not in json.dumps(denied, ensure_ascii=False)


def test_actorの無いcredentialは追記もby_required(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch, actor=None)
    filed = report_inbox.file_report("first", kind="bug", title="題", body="本文", by="x")
    denied = server.call_tool("thth_report_add", {"report_id": filed["report_id"], "text": "足す"})
    assert denied["isError"] and _text(denied) == "by_required"


def test_CLIのreport_addは標準入力から足せる(two_projects, tmp_path, capsys, monkeypatch):
    rc, filed, _ = _file(tmp_path, capsys)
    assert rc == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("書き足します\n"))
    rc = cli.main(["report", "add", filed["report_id"], "--body-file", "-",
                   "--by", "kopicha-session"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "書き足しました" in out.out
    record = report_inbox.show(filed["report_id"])
    assert record["replies"][-1]["role"] == "reporter"
    rc = cli.main(["report", "show", filed["report_id"]])
    assert "（報告した側の追記）" in capsys.readouterr().out


def test_CLIのreport_addはbyが要る(two_projects, tmp_path, capsys):
    rc, filed, _ = _file(tmp_path, capsys)
    rc = cli.main(["report", "add", filed["report_id"], "--body-file", _body_file(tmp_path, "足す")])
    assert rc == 2 and "by_required" in capsys.readouterr().err


# ---------------------------------------------------------------- 2. 似た報告

def test_題の語が重なる開いている報告をsimilarに返す(two_projects, tmp_path, capsys):
    _rc, first, _ = _file(tmp_path, capsys, title="replies が他 account を返す")
    _rc, closed, _ = _file(tmp_path, capsys, title="replies の古い件", body="閉じる")
    report_inbox.close(closed["report_id"], by="impl", reason="fixed", version="3.1.2")
    # 同じ project の別 account（kopicha-bsky）の報告も対象・他 project は対象外。
    _rc, sibling, _ = _file(tmp_path, capsys, account="kopicha-bsky",
                            title="Replies の数がずれる", body="別の本文")
    _rc, other, _ = _file(tmp_path, capsys, account="other-threads",
                          title="replies の件", body="他 project")
    rc, filed, _ = _file(tmp_path, capsys, title="replies_to が重複する", body="新しい本文")
    assert rc == 0
    ids = [row["report_id"] for row in filed["similar"]]
    assert set(ids) == {first["report_id"], sibling["report_id"]}
    assert other["report_id"] not in ids and closed["report_id"] not in ids
    assert set(filed["similar"][0]) == {"report_id", "title", "status"}


def test_題の語が重ならなければsimilarは空_1字の語は数えない(two_projects, tmp_path, capsys):
    _file(tmp_path, capsys, title="replies が他 account を返す")
    rc, filed, _ = _file(tmp_path, capsys, title="a が b・x", body="別の本文")
    assert rc == 0 and filed["similar"] == []


def test_similarは最大5件で置くのは止めない(two_projects, tmp_path, capsys):
    for i in range(7):
        _file(tmp_path, capsys, title=f"morning の件 {i}", body=f"本文 {i}")
    rc, filed, _ = _file(tmp_path, capsys, title="morning が遅い", body="新しい")
    assert rc == 0 and filed["status"] == "open"
    assert len(filed["similar"]) == report_inbox.SIMILAR_LIMIT == 5


def test_textの応答にも似た報告の行(two_projects, tmp_path, capsys):
    _rc, first, _ = _file(tmp_path, capsys, title="replies が他 account を返す")
    rc = cli.main(["report", "file", "kopicha-threads", "--kind", "bug",
                   "--title", "replies が遅い", "--body-file", _body_file(tmp_path, "別"),
                   "--by", "kopicha-session"])
    out = capsys.readouterr().out
    assert rc == 0 and f"似た報告: {first['report_id']}（replies が他 account を返す）" in out


# ---------------------------------------------------------------- 3. 置いた人

def test_一覧の行に置いた人が出る(two_projects, tmp_path, capsys):
    _file(tmp_path, capsys, by="kopicha-session")
    rc = cli.main(["report", "list", "kopicha-threads"])
    out = capsys.readouterr().out
    assert rc == 0 and "置いた人 kopicha-session" in out
    rc = cli.main(["admin", "reports", "list"])
    out = capsys.readouterr().out
    assert rc == 0 and "置いた人 kopicha-session" in out
    listed = report_inbox.list_reports(scope=None, status="all")
    assert listed["reports"][0]["reporter"] == "kopicha-session"
