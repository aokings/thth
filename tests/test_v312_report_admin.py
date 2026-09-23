"""報告の口 第 3 段——実装側（管理者）の CLI・MCP と書き出し（設計 3.1.2 §1・§3・§5）。

見るのは:
  - `thth admin reports list|show|reply|close` が全 project を読み、返事と状態を書く。
  - 返事・閉じるは `--by` 必須・変更ログ（`report_replied`・`report_closed`）は
    presence-only（返事の本文は入らない）。
  - 閉じた報告は閉じ直せない。版は `3.1.2` の形だけ。
  - `admin reports export` の Markdown と、全経路の JSON 応答に**絶対パスも秘密も
    無い**（leak probe）。
  - MCP の `thth_admin_reports_*` は admin credential だけ。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from thth import accounts, admin_log, cli, report_inbox


@pytest.fixture
def filed(isolated_account_factory, tmp_path):
    info = isolated_account_factory("kopicha-threads", project="kopicha")
    isolated_account_factory("other-threads", project="other")
    root = os.environ["THTH_ROOT"]
    mine = report_inbox.file_report(
        "kopicha-threads", kind="bug", title="replies が混ざる / 他 account",
        body=f"{root}/state/kopicha-threads を見ると ```x``` が\n混ざる", by="kopicha-session",
        repro="1. thth replies kopicha-threads")
    theirs = report_inbox.file_report("other-threads", kind="request", title="欲しい形",
                                      body="週ごとの集計", by="other-session")
    return {"info": info, "mine": mine["report_id"], "theirs": theirs["report_id"], "root": root}


def _run(capsys, *argv):
    rc = cli.main(list(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _reply_file(tmp_path, text="直しました。3.1.2 で出ます"):
    path = tmp_path / "reply.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ 読む

def test_管理者は全projectを読む(filed, capsys):
    rc, out, _ = _run(capsys, "admin", "reports", "list", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert {row["report_id"] for row in payload["reports"]} == {filed["mine"], filed["theirs"]}
    assert payload["status_filter"] == "open" and payload["unreadable"] == 0
    rc, out, _ = _run(capsys, "admin", "reports", "show", filed["theirs"], "--json")
    assert rc == 0 and json.loads(out)["body"] == "週ごとの集計"


def test_人向けの一覧(filed, capsys):
    rc, out, _ = _run(capsys, "admin", "reports", "list")
    assert rc == 0 and "報告 2/2 件（open）" in out and "不具合" in out and "要望" in out


# ------------------------------------------------------------ 返事と閉じる

def test_返事を足すと報告した側のshowに出る(filed, capsys, tmp_path):
    rc, out, _ = _run(capsys, "admin", "reports", "reply", filed["mine"], "--by", "masaru",
                      "--text-file", _reply_file(tmp_path), "--json")
    assert rc == 0 and json.loads(out)["n_replies"] == 1
    rc, out, _ = _run(capsys, "report", "show", filed["mine"], "--json")
    replies = json.loads(out)["replies"]
    assert [(row["by"], row["text"]) for row in replies] == [("masaru", "直しました。3.1.2 で出ます")]
    rows, broken = admin_log.read(event="report_replied")
    assert broken == 0 and [row["diff"] for row in rows] == [{"reply": ["absent", "present"]}]
    assert "直しました" not in json.dumps(rows, ensure_ascii=False)


def test_返事にも秘密は置かない(filed, capsys, tmp_path):
    rc, out, err = _run(capsys, "admin", "reports", "reply", filed["mine"], "--by", "masaru",
                        "--text-file", _reply_file(tmp_path, "Bearer AbCdEf0123456789xyz"), "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["secret_detected"]
    assert report_inbox.show(filed["mine"])["replies"] == []


def test_返事と閉じるはbyが要る(filed, capsys, tmp_path):
    rc, out, _ = _run(capsys, "admin", "reports", "reply", filed["mine"],
                      "--text-file", _reply_file(tmp_path), "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["by_required"]
    rc, out, _ = _run(capsys, "admin", "reports", "close", filed["mine"], "--reason", "fixed",
                      "--version", "3.1.2", "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["by_required"]
    rc, out, _ = _run(capsys, "admin", "reports", "export", "--to", str(tmp_path / "x"), "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["by_required"]


def test_閉じると版と理由が残り閉じ直せない(filed, capsys):
    rc, out, _ = _run(capsys, "admin", "reports", "close", filed["mine"], "--by", "masaru",
                      "--reason", "fixed", "--version", "3.1.2", "--json")
    assert rc == 0
    closed = json.loads(out)["closed"]
    assert closed["reason"] == "fixed" and closed["version"] == "3.1.2" and closed["by"] == "masaru"
    rc, out, _ = _run(capsys, "admin", "reports", "close", filed["mine"], "--by", "masaru",
                      "--reason", "wontfix", "--version", "3.1.2", "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["report_already_closed"]
    rows, _ = admin_log.read(event="report_closed")
    assert [row["diff"] for row in rows] == [{"status": ["open", "closed"]}]
    # 既定の一覧（開いているものだけ）から消え、all では残る。
    rc, out, _ = _run(capsys, "admin", "reports", "list", "--json")
    assert [row["report_id"] for row in json.loads(out)["reports"]] == [filed["theirs"]]
    rc, out, _ = _run(capsys, "report", "list", "kopicha", "--json")
    assert json.loads(out)["reports"][0]["closed"]["version"] == "3.1.2"


@pytest.mark.parametrize("version", ["3.1", "v3.1.2", "3.1.2-rc1", ""])
def test_版の形が違えば閉じない(filed, capsys, version):
    rc, out, _ = _run(capsys, "admin", "reports", "close", filed["mine"], "--by", "masaru",
                      "--reason", "fixed", "--version", version, "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["invalid_version"]
    assert report_inbox.show(filed["mine"])["status"] == "open"


def test_無いidは断る(filed, capsys, tmp_path):
    for argv in (("show", "r20260909-00000000"), ("show", "../../x"),
                 ("reply", "r20260909-00000000", "--by", "m", "--text-file", _reply_file(tmp_path))):
        rc, out, _ = _run(capsys, "admin", "reports", *argv, "--json")
        assert rc == 2 and json.loads(out)["cannot_say"] == ["report_not_found"]


def test_ログに書けなければ返事を戻す(filed, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(admin_log, "append",
                        lambda *a, **k: (_ for _ in ()).throw(admin_log.AdminLogError("x")))
    rc, out, _ = _run(capsys, "admin", "reports", "reply", filed["mine"], "--by", "masaru",
                      "--text-file", _reply_file(tmp_path), "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["report_log_unavailable"]
    assert report_inbox.show(filed["mine"])["replies"] == []


# ------------------------------------------------------------ 書き出し

def test_書き出しは1件1ファイルで絶対パスも秘密も無い(filed, capsys, tmp_path):
    target = tmp_path / "repo" / "docs" / "報告"
    target.parent.mkdir(parents=True)
    report_inbox.reply(filed["mine"], by="masaru", text="見ています")
    rc, out, _ = _run(capsys, "admin", "reports", "export", "--to", str(target), "--by", "masaru",
                      "--json")
    assert rc == 0
    result = json.loads(out)
    assert result["n_open"] == 2 and result["n_closed_updated"] == 0
    files = sorted(path.name for path in target.iterdir())
    assert files == result["files"]
    mine = next(name for name in files if name.startswith(filed["mine"] + "_"))
    assert mine == filed["mine"] + "_replies_が混ざる_他_account.md"
    text = (target / mine).read_text(encoding="utf-8")
    assert "# replies が混ざる / 他 account" in text and "見ています" in text
    assert "$THTH_ROOT/state/kopicha-threads" in text
    # 本文の ``` が囲みを壊さない（本文より長い囲み）。
    assert "````text" in text
    # leak probe: 応答にも書き出しにも、絶対パス・tmp のパス・秘密が無い。
    for blob in [out, *[(target / name).read_text(encoding="utf-8") for name in files]]:
        assert filed["root"] not in blob and str(tmp_path) not in blob
        assert os.path.expanduser("~") not in blob and "/private/" not in blob


def test_閉じた報告は前に書き出していれば閉じた形に書き直す(filed, capsys, tmp_path):
    target = tmp_path / "out"
    report_inbox.export(str(target), by="masaru")
    report_inbox.close(filed["mine"], by="masaru", reason="fixed", version="3.1.2")
    other = report_inbox.file_report("kopicha-threads", kind="bug", title="新しい", body="別", by="s")
    report_inbox.close(other["report_id"], by="masaru", reason="invalid", version="3.1.2")
    result = report_inbox.export(str(target), by="masaru")
    assert result["n_open"] == 1 and result["n_closed_updated"] == 1
    names = sorted(path.name for path in target.iterdir())
    assert not any(name.startswith(other["report_id"]) for name in names)   # 書き出したことが無い
    closed_file = next(name for name in names if name.startswith(filed["mine"]))
    assert "- 状態: closed" in (target / closed_file).read_text(encoding="utf-8")


def test_書き出し先が使えなければ断る(filed, capsys, tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    rc, out, _ = _run(capsys, "admin", "reports", "export", "--to", str(blocker), "--by", "m", "--json")
    assert rc == 2 and json.loads(out)["cannot_say"] == ["invalid_export_target"]
    assert str(tmp_path) not in out


# ------------------------------------------------------------------ MCP

@pytest.fixture
def admin_server(tmp_path, monkeypatch):
    root = tmp_path / "tenant"
    root.mkdir(mode=0o700)
    (root / "accounts").mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    for name, project in (("first", "kopicha"), ("second", "other")):
        cfg = {key: None for key in accounts.REQUIRED_FIELDS}
        cfg.update(account=name, project=project, media="threads", handle=name,
                   repo_dir=str(root / "repos/_none"), token=str(root / "secret" / name),
                   env=str(root / "secret" / ("env-" + name)),
                   queue_dir="docs/sns/queue", replies_dir="data/sns/replies")
        (root / "accounts" / (name + ".json")).write_text(json.dumps(cfg))
    token = "c" * 43
    value = dict(schema_version=1, root=str(root), credentials=[dict(
        sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revoked=False, accounts={"first": "kopicha"}, scope="admin")])
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(path))
    monkeypatch.setenv("THTH_REPORT_TOKEN", token)
    from tests.test_mcp import _load_server_module
    return root, path, value, _load_server_module()


def test_MCPの管理口は全projectを読み返事と閉じるを書く(admin_server):
    root, _path, _value, server = admin_server
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert {"thth_admin_reports_list", "thth_admin_reports_show",
            "thth_admin_reports_reply", "thth_admin_reports_close"} <= names
    # credential の範囲（first）の外の project（second）の報告も管理者は読む。
    theirs = report_inbox.file_report("second", kind="bug", title="他", body="他の project", by="x")
    listed = json.loads(server.call_tool("thth_admin_reports_list", {})["content"][0]["text"])
    assert [row["report_id"] for row in listed["reports"]] == [theirs["report_id"]]
    result = server.call_tool("thth_admin_reports_reply",
                              {"report_id": theirs["report_id"], "text": "見ます", "by": "masaru"})
    assert not result.get("isError"), result
    result = server.call_tool("thth_admin_reports_close", {"report_id": theirs["report_id"],
                              "reason": "fixed", "version": "3.1.2", "by": "masaru"})
    assert not result.get("isError"), result
    shown = json.loads(server.call_tool("thth_admin_reports_show",
                                        {"report_id": theirs["report_id"]})["content"][0]["text"])
    assert shown["status"] == "closed" and shown["replies"][0]["text"] == "見ます"
    rows, _ = admin_log.read()
    assert [(row["event"], row["via"]) for row in rows if row["event"].startswith("report_")] == [
        ("report_filed", "cli"), ("report_replied", "mcp"), ("report_closed", "mcp")]
    # leak probe: 応答に絶対パスが無い。
    assert str(root) not in json.dumps(shown, ensure_ascii=False)


def test_MCPの返事はbyが要り_利用者のcredentialには出ない(admin_server, monkeypatch):
    root, path, value, server = admin_server
    theirs = report_inbox.file_report("second", kind="bug", title="他", body="本文", by="x")
    denied = server.call_tool("thth_admin_reports_reply", {"report_id": theirs["report_id"], "text": "t"})
    assert denied["isError"] and denied["content"][0]["text"] == "invalid_request"
    denied = server.call_tool("thth_admin_reports_close", {"report_id": theirs["report_id"],
                              "reason": "fixed", "version": "3", "by": "masaru"})
    assert denied["isError"] and denied["content"][0]["text"] == "invalid_version"
    value["credentials"][0]["scope"] = "user"
    path.write_text(json.dumps(value))
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert not any(name.startswith("thth_admin_reports_") for name in names)
    denied = server.call_tool("thth_admin_reports_list", {})
    assert denied["isError"] and denied["content"][0]["text"] == "unsupported_operation"
