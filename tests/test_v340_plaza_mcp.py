"""施策の広場 第 6 段——MCP（設計 3.4.0 §4）。

見るのは:
  - サーバ型の user credential で `thth_plaza_post → list → show → reply → update` の往復。
  - 誰が書いたかは credential の `actor`（要求の欄からは作れない）。actor が無ければ
    `by_required`。credential の外の account には書けない。
  - **他の持ち主の project 範囲の書き込みは読めない**（無い id と同じ `plaza_not_found`）。
  - 管理者の口（list・show・join・leave・hide）は admin scope だけ。利用者の口は admin に出ない。
  - 手元の stdio（pip 版）には出さない。断りは静的な符丁だけ。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from thth import accounts, admin_log, plaza

TOKEN = "c" * 43


def _tenant(tmp_path, monkeypatch, *, actor="kopicha-person", scope="user",
            allowed=None):
    root = tmp_path / "tenant"
    root.mkdir(mode=0o700)
    (root / "accounts").mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    for name, project, media in (("first", "kopicha", "threads"), ("second", "other", "threads"),
                                 ("third", "kopicha", "bluesky")):
        cfg = {key: None for key in accounts.REQUIRED_FIELDS}
        cfg.update(account=name, project=project, media=media, handle=name,
                   repo_dir=str(root / "repos/_none"), token=str(root / "secret" / name),
                   env=str(root / "secret" / ("env-" + name)),
                   queue_dir="docs/sns/queue", replies_dir="data/sns/replies")
        (root / "accounts" / (name + ".json")).write_text(json.dumps(cfg))
    entry = dict(sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
                 expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                 revoked=False, accounts=allowed or {"first": "kopicha", "third": "kopicha"},
                 scope=scope)
    if actor is not None:
        entry["actor"] = actor
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(dict(schema_version=1, root=str(root), credentials=[entry])))
    path.chmod(0o600)
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(path))
    monkeypatch.setenv("THTH_REPORT_TOKEN", TOKEN)
    from tests.test_mcp import _load_server_module
    return root, _load_server_module()


def _text(result):
    return result["content"][0]["text"]


def _post(server, **overrides):
    arguments = {"account": "first", "kind": "finding", "title": "朝は伸びる",
                 "body": "朝の問いかけで返信が増えた気がする", "scope": "Threads の朝の投稿"}
    arguments.update(overrides)
    return server.call_tool("thth_plaza_post", arguments)


def test_MCPで置いて読んで返して判定する(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert {"thth_plaza_post", "thth_plaza_list", "thth_plaza_show", "thth_plaza_reply",
            "thth_plaza_update"} <= names
    assert not any(name.startswith("thth_admin_plaza") for name in names)
    result = _post(server)
    assert not result.get("isError"), result
    posted = json.loads(_text(result))
    assert posted["project"] == "kopicha" and posted["scope"] == "project"
    listed = json.loads(_text(server.call_tool("thth_plaza_list", {})))
    assert [row["plaza_id"] for row in listed["posts"]] == [posted["plaza_id"]]
    shown = json.loads(_text(server.call_tool("thth_plaza_show", {"plaza_id": posted["plaza_id"]})))
    assert shown["by"] == "kopicha-person" and shown["via"] == "mcp"
    replied = server.call_tool("thth_plaza_reply", {"plaza_id": posted["plaza_id"], "account": "third",
                                                    "kind": "trial", "result": "not_reproduced",
                                                    "text": "Bluesky では変わらない"})
    assert not replied.get("isError"), replied
    assert json.loads(_text(replied))["trials"]["not_reproduced"] == 1
    measure = json.loads(_text(_post(server, kind="measure", title="施策",
                                     how="thth measured first")))
    updated = server.call_tool("thth_plaza_update", {"plaza_id": measure["plaza_id"], "account": "third",
                                                     "verdict": "inconclusive", "reason": "宣言がまだ"})
    assert json.loads(_text(updated))["verdict"] == "inconclusive"
    rows, _ = admin_log.read(event="plaza_posted")
    assert {(row["by"], row["via"]) for row in rows} == {("kopicha-person", "mcp")}


def test_誰が書いたかは要求の欄から作れない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    for field in ("by", "actor", "project", "operation", "visibility"):
        denied = _post(server, **{field: "evil"})
        assert denied["isError"] and _text(denied) == "invalid_request"


def test_actorの無いcredentialはby_required(tmp_path, monkeypatch):
    root, server = _tenant(tmp_path, monkeypatch, actor=None)
    denied = _post(server)
    assert denied["isError"] and _text(denied) == "by_required"
    assert not list((root / "state").glob("_plaza/p*.json"))


def test_credentialの外のaccountには書けない(tmp_path, monkeypatch):
    root, server = _tenant(tmp_path, monkeypatch)
    for account in ("second", "nobody"):
        denied = _post(server, account=account)
        assert denied["isError"] and _text(denied) == "scope_unavailable"
    assert not list((root / "state").glob("_plaza/p*.json"))


def test_他の持ち主のproject範囲は読めない(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    theirs = plaza.post("second", kind="finding", title="OTHER-TITLE", body="OTHER-BODY",
                        scope_note="other の範囲", by="x")
    ours = plaza.post("third", kind="question", title="同じ持ち主", body="問い",
                      scope_note="Bluesky", by="y")
    out = _text(server.call_tool("thth_plaza_list", {}))
    listed = json.loads(out)
    assert [row["plaza_id"] for row in listed["posts"]] == [ours["plaza_id"]]
    assert "OTHER-TITLE" not in out and listed["unreadable"] is None
    denied = server.call_tool("thth_plaza_show", {"plaza_id": theirs["plaza_id"]})
    assert denied["isError"] and _text(denied) == "plaza_not_found"
    missing = server.call_tool("thth_plaza_show", {"plaza_id": "p20260909-00000000"})
    assert missing["isError"] and _text(missing) == "plaza_not_found"
    denied = server.call_tool("thth_plaza_reply", {"plaza_id": theirs["plaza_id"], "account": "first",
                                                   "kind": "agree"})
    assert denied["isError"] and _text(denied) == "plaza_not_found"
    denied = server.call_tool("thth_plaza_list", {"project": "other"})
    assert denied["isError"] and _text(denied) == "scope_unavailable"


def test_MCPにはopenにする口が無い_読むのは参加した持ち主どうし(tmp_path, monkeypatch):
    # open にするのは人の CLI の二段確認だけ（裁定 09-23）。MCP は project の範囲まで。
    root, server = _tenant(tmp_path, monkeypatch)
    names = {tool["name"]: tool for tool in server.server_tools(server.authenticated_context())}
    assert "open" not in names["thth_plaza_post"]["inputSchema"]["properties"]
    assert names["thth_plaza_update"]["inputSchema"]["properties"]["visibility"]["enum"] == ["project"]
    plaza.set_membership("kopicha", joined=True, by="operator")
    plaza.set_membership("other", joined=True, by="operator")
    denied = _post(server, open=True)
    assert denied["isError"] and _text(denied) == "invalid_request"
    mine = json.loads(_text(_post(server)))
    denied = server.call_tool("thth_plaza_update", {"plaza_id": mine["plaza_id"], "account": "first",
                                                    "visibility": "open"})
    assert denied["isError"] and _text(denied) == "open_requires_cli"
    assert plaza.STORE.get(mine["plaza_id"])["scope"] == "project"
    preview = plaza.post("second", kind="finding", title="other の公開", body="夜が伸びる",
                         scope_note="other の夜", by="other-person", visibility="open")
    theirs = plaza.post("second", kind="finding", title="other の公開", body="夜が伸びる",
                        scope_note="other の夜", by="other-person", visibility="open",
                        confirm=preview["digest"])
    listed = json.loads(_text(server.call_tool("thth_plaza_list", {"open": True})))
    row = listed["posts"][0]
    assert row["plaza_id"] == theirs["plaza_id"] and row["view"] == "open"
    shown = json.loads(_text(server.call_tool("thth_plaza_show", {"plaza_id": theirs["plaza_id"]})))
    assert "other-person" not in json.dumps(shown, ensure_ascii=False)
    assert "second" not in json.dumps(shown, ensure_ascii=False)
    # project に戻すのは MCP でもできる（他の持ち主から見えなくする側）。
    plaza_id = plaza.post("first", kind="finding", title="kopicha の公開", body="朝",
                          scope_note="朝", by="k", visibility="open",
                          confirm=plaza.post("first", kind="finding", title="kopicha の公開",
                                             body="朝", scope_note="朝", by="k",
                                             visibility="open")["digest"])["plaza_id"]
    back = server.call_tool("thth_plaza_update", {"plaza_id": plaza_id, "account": "first",
                                                  "visibility": "project"})
    assert json.loads(_text(back))["scope"] == "project"


@pytest.mark.parametrize("overrides,reason", [
    ({"body": "token: AbCdEf0123456789xyz0"}, "secret_detected"),
    ({"title": "題" * 121}, "post_too_long"),
    ({"scope": " "}, "scope_required"),
    ({"kind": "measure"}, "how_required"),
    ({"evidence_level": "observed"}, "observed_is_tool_only"),
    ({"declarations": ["not-an-object"]}, "invalid_request"),
    ({"open": "yes"}, "invalid_request"),
])
def test_断りは静的な符丁だけ(tmp_path, monkeypatch, overrides, reason):
    _root, server = _tenant(tmp_path, monkeypatch)
    denied = _post(server, **overrides)
    assert denied["isError"] and _text(denied) == reason
    assert "AbCdEf0123456789xyz0" not in json.dumps(denied, ensure_ascii=False)


def test_observedを名乗る値は道具の断り(tmp_path, monkeypatch):
    # inputSchema の enum の外の値もサービスの口まで届き、道具が断る（名乗れない）。
    from thth.report_service import execute_user_plaza, ReportServiceError
    _root, server = _tenant(tmp_path, monkeypatch)
    with pytest.raises(ReportServiceError, match="^observed_is_tool_only$"):
        execute_user_plaza(server.authenticated_context(),
                           {"operation": "plaza_post", "account": "first", "kind": "finding",
                            "title": "t", "body": "b", "scope": "s", "evidence_level": "observed"})


def test_重複は既存のidを返す(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    first = json.loads(_text(_post(server)))
    again = _post(server)
    assert again["isError"] and _text(again) == "duplicate_post: " + first["plaza_id"]


def test_管理者の口(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch, scope="admin", actor=None,
                            allowed={"first": "kopicha", "second": "other", "third": "kopicha"})
    context = server.authenticated_context()
    names = {tool["name"] for tool in server.server_tools(context)}
    assert {"thth_admin_plaza_list", "thth_admin_plaza_show", "thth_admin_plaza_join",
            "thth_admin_plaza_leave", "thth_admin_plaza_hide"} <= names
    assert "thth_plaza_post" not in names
    posted = plaza.post("second", kind="finding", title="t", body="b", scope_note="s", by="x")
    joined = server.call_tool("thth_admin_plaza_join", {"project": "other", "by": "operator"})
    assert json.loads(_text(joined))["joined"] is True
    hidden = server.call_tool("thth_admin_plaza_hide", {"plaza_id": posted["plaza_id"],
                                                         "reason": "確認のため", "by": "operator"})
    assert json.loads(_text(hidden))["hidden"]["reason"] == "確認のため"
    listed = json.loads(_text(server.call_tool("thth_admin_plaza_list", {})))
    assert listed["joined_projects"] == ["other"] and listed["posts"][0]["hidden"] is True
    shown = json.loads(_text(server.call_tool("thth_admin_plaza_show", {"plaza_id": posted["plaza_id"]})))
    assert shown["body"] == "b"
    denied = server.call_tool("thth_admin_plaza_join", {"project": "other"})
    assert denied["isError"] and _text(denied) == "invalid_request"
    assert json.loads(_text(server.call_tool("thth_admin_plaza_leave",
                                             {"project": "other", "by": "operator"})))["joined"] is False


def test_手元のstdioには出さない(monkeypatch):
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    assert not any(tool["name"].startswith("thth_plaza") for tool in server.TOOLS)
