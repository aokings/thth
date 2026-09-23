"""報告の口 第 2 段——MCP の利用者側（設計 3.1.2 §1・§2・§5）。

見るのは:
  - サーバ型の user credential で `thth_report_file → list → show` の往復。
  - 誰が置いたかは credential の `actor`（要求の欄からは作れない）。actor が
    無ければ `by_required`。
  - 利用者 scope は**自分の project の報告だけ**読める（他 project の id は
    無い id と同じ `report_not_found`）。
  - 書く口（writes）の無い credential でも置ける。admin scope には出ない。
  - 手元の stdio（pip 版）には出さない（置いても実装側に届かない）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from thth import accounts, admin_log, report_inbox

TOKEN = "b" * 43


def _tenant(tmp_path, monkeypatch, *, actor="kopicha-person", writes=False, scope="user"):
    root = tmp_path / "tenant"
    root.mkdir(mode=0o700)
    (root / "accounts").mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    for name, project in (("first", "kopicha"), ("second", "other"), ("third", "kopicha")):
        cfg = {key: None for key in accounts.REQUIRED_FIELDS}
        cfg.update(account=name, project=project, media="threads", handle=name,
                   repo_dir=str(root / "repos/_none"), token=str(root / "secret" / name),
                   env=str(root / "secret" / ("env-" + name)),
                   queue_dir="docs/sns/queue", replies_dir="data/sns/replies")
        (root / "accounts" / (name + ".json")).write_text(json.dumps(cfg))
    entry = dict(sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
                 expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                 revoked=False, accounts={"first": "kopicha"}, scope=scope)
    if actor is not None:
        entry["actor"] = actor
    if writes:
        entry["writes"] = True
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(dict(schema_version=1, root=str(root), credentials=[entry])))
    path.chmod(0o600)
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(path))
    monkeypatch.setenv("THTH_REPORT_TOKEN", TOKEN)
    from tests.test_mcp import _load_server_module
    return root, _load_server_module()


def _text(result):
    return result["content"][0]["text"]


def _file(server, **overrides):
    arguments = {"account": "first", "kind": "bug", "title": "replies が混ざる",
                 "body": "他の account の返信が返ってくる"}
    arguments.update(overrides)
    return server.call_tool("thth_report_file", arguments)


def test_MCPでfile_list_showの往復(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert {"thth_report_file", "thth_report_list", "thth_report_show"} <= names
    result = _file(server, repro="1. replies を読む")
    assert not result.get("isError"), result
    filed = json.loads(_text(result))
    assert filed["status"] == "open" and filed["project"] == "kopicha"
    listed = json.loads(_text(server.call_tool("thth_report_list", {})))
    assert [row["report_id"] for row in listed["reports"]] == [filed["report_id"]]
    shown = json.loads(_text(server.call_tool("thth_report_show", {"report_id": filed["report_id"]})))
    assert shown["reporter"] == "kopicha-person" and shown["via"] == "mcp"
    assert shown["repro"] == "1. replies を読む" and shown["body"] == "他の account の返信が返ってくる"
    rows, _ = admin_log.read(event="report_filed")
    assert [(row["by"], row["via"], row["account"]) for row in rows] == [("kopicha-person", "mcp", "first")]


def test_誰が置いたかは要求の欄から作れない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    for field in ("by", "reporter", "actor", "project", "operation"):
        denied = _file(server, **{field: "evil"})
        assert denied["isError"] and _text(denied) == "invalid_request"


def test_actorの無いcredentialはby_required(tmp_path, monkeypatch):
    root, server = _tenant(tmp_path, monkeypatch, actor=None)
    denied = _file(server)
    assert denied["isError"] and _text(denied) == "by_required"
    assert not list((root / "state").glob("_reports/r*.json"))


def test_書く口の無いcredentialでも置ける_書く口があっても同じ(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch, writes=True)
    assert not _file(server).get("isError")


def test_credentialの外のaccountには置けない(tmp_path, monkeypatch):
    root, server = _tenant(tmp_path, monkeypatch)
    for account in ("second", "third", "nobody"):
        denied = _file(server, account=account)
        assert denied["isError"] and _text(denied) == "scope_unavailable"
    assert not list((root / "state").glob("_reports/r*.json"))


def test_他のprojectの報告は読めない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    # 他 project（second）の報告と、同じ project の別 account（third）の報告を CLI 側から置く。
    theirs = report_inbox.file_report("second", kind="bug", title="他", body="他の project", by="x")
    ours = report_inbox.file_report("third", kind="request", title="同じ project", body="要望", by="y")
    listed = json.loads(_text(server.call_tool("thth_report_list", {"status": "all"})))
    assert [row["report_id"] for row in listed["reports"]] == [ours["report_id"]]
    assert listed["denominator"] == 1 and listed["unreadable"] is None
    denied = server.call_tool("thth_report_show", {"report_id": theirs["report_id"]})
    assert denied["isError"] and _text(denied) == "report_not_found"
    missing = server.call_tool("thth_report_show", {"report_id": "r20260909-00000000"})
    assert missing["isError"] and _text(missing) == "report_not_found"
    shown = json.loads(_text(server.call_tool("thth_report_show", {"report_id": ours["report_id"]})))
    assert shown["body"] == "要望"


@pytest.mark.parametrize("overrides,reason", [
    ({"body": "token: AbCdEf0123456789xyz0"}, "secret_detected"),
    ({"title": "題" * 121}, "report_too_long"),
    ({"kind": "question"}, "invalid_kind"),
    ({"title": "二行\n目"}, "invalid_report"),
])
def test_断りは静的な符丁だけ(tmp_path, monkeypatch, overrides, reason):
    _root, server = _tenant(tmp_path, monkeypatch)
    denied = _file(server, **overrides)
    assert denied["isError"] and _text(denied) == reason
    assert "AbCdEf0123456789xyz0" not in json.dumps(denied, ensure_ascii=False)


def test_重複は既存のidを返す(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    first = json.loads(_text(_file(server)))
    denied = _file(server)
    assert denied["isError"] and _text(denied) == "duplicate_report: " + first["report_id"]


def test_admin_scopeと手元のstdioには利用者の口を出さない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch, scope="admin")
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert not names & {"thth_report_file", "thth_report_list", "thth_report_show"}
    denied = _file(server)
    assert denied["isError"] and _text(denied) == "unsupported_operation"
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key)
    assert "thth_report_file" not in {tool["name"] for tool in server.TOOLS}
    assert _file(server)["isError"]
