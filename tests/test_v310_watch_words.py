"""監視語（設計 3.1.0 §3）。**語は管理者が入れる**——道具は選ばない。

見るのは 4 つ:
  - 台帳の loader が形を断る（6 語・41 字は受け取らない）。
  - `thth admin watch set|show` が入れて読める（`--by` 必須）。
  - 変更記録は presence-only——**語そのものは `admin log` に出ない**。
  - MCP の `thth_admin_watch_set` は管理者 credential の範囲だけ。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thth import accounts, admin_log, cli, morning, watch_cli

WORDS = ["コーヒー", "自家焙煎"]


def ledger(info, name="kopicha-threads"):
    return Path(info["accounts_dir"]) / (name + ".json")


@pytest.fixture
def account(isolated_account_factory):
    return isolated_account_factory("kopicha-threads", project="kopicha")


# --------------------------------------------------------------- 形の検査

@pytest.mark.parametrize("value", [
    ["1", "2", "3", "4", "5", "6"],          # 6 語は多すぎる
    ["あ" * 41],                              # 41 字は長すぎる
    [""], ["   "], [None], "コーヒー", [{"word": "コーヒー"}], ["行\n替え"],
])
def test_受け取れない監視語は台帳の入口で断る(account, value):
    path = ledger(account)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["watch_words"] = value
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(accounts.AccountError, match="invalid_watch_words"):
        accounts.load_account("kopicha-threads")


@pytest.mark.parametrize("value", [[], ["コーヒー"], ["1", "2", "3", "4", "5"], ["あ" * 40]])
def test_受け取れる監視語(account, value):
    path = ledger(account)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["watch_words"] = value
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert accounts.watch_words(accounts.load_account("kopicha-threads")) == value


# ------------------------------------------------------------------- CLI

def test_admin_watch_set_と_show(account, capsys):
    assert cli.main(["admin", "watch", "set", "kopicha-threads", *WORDS,
                     "--by", "masaru", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["watch_words"] == WORDS
    assert accounts.watch_words(accounts.load_account("kopicha-threads")) == WORDS
    assert cli.main(["admin", "watch", "show", "kopicha-threads", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["watch_words"] == WORDS and shown["n"] == 2
    assert shown["max_words"] == 5 and shown["max_chars"] == 40


def test_setは入れ替えであって追記ではない(account, capsys):
    cli.main(["admin", "watch", "set", "kopicha-threads", *WORDS, "--by", "masaru"])
    cli.main(["admin", "watch", "set", "kopicha-threads", "紅茶", "--by", "masaru"])
    capsys.readouterr()
    assert accounts.watch_words(accounts.load_account("kopicha-threads")) == ["紅茶"]


def test_byは必須(account, capsys):
    with pytest.raises(SystemExit):
        cli.main(["admin", "watch", "set", "kopicha-threads", "紅茶"])


@pytest.mark.parametrize("words,reason", [
    (["1", "2", "3", "4", "5", "6"], "invalid_watch_words"),
    (["あ" * 41], "invalid_watch_words"),
])
def test_多すぎる語も長すぎる語もCLIが断る(account, capsys, words, reason):
    assert cli.main(["admin", "watch", "set", "kopicha-threads", *words,
                     "--by", "masaru", "--json"]) == 2
    captured = capsys.readouterr()
    assert reason in captured.err
    assert json.loads(captured.out)["cannot_say"] == [reason]
    assert "watch_words" not in json.loads(ledger(account).read_text(encoding="utf-8"))


def test_知らないaccountは断って台帳を作らない(account, capsys):
    assert cli.main(["admin", "watch", "set", "nobody", "紅茶", "--by", "masaru", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["ledger_unavailable"]
    assert not (Path(account["accounts_dir"]) / "nobody.json").exists()


# ----------------------------------------------------------- 変更の記録

def test_変更記録はpresence_onlyで語を残さない(account, capsys):
    cli.main(["admin", "watch", "set", "kopicha-threads", *WORDS, "--by", "masaru"])
    capsys.readouterr()
    rows, broken = admin_log.read(event="watch_set")
    assert broken == 0 and len(rows) == 1
    row = rows[0]
    assert row["account"] == "kopicha-threads" and row["by"] == "masaru"
    assert row["diff"] == {"watch_words": ["absent", "present"]}
    assert "コーヒー" not in json.dumps(row, ensure_ascii=False)


# ------------------------------------------------------------------- MCP

@pytest.fixture
def admin_credentials(tmp_path, monkeypatch):
    root = tmp_path / "tenant"
    root.mkdir(mode=0o700)
    (root / "accounts").mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    for name in ("first", "second"):
        cfg = {key: None for key in accounts.REQUIRED_FIELDS}
        cfg.update(account=name, project="kopicha", media="threads", handle=name,
                   repo_dir=str(root / "repos/_none"), token=str(root / "secret" / name),
                   env=str(root / "secret" / ("env-" + name)),
                   queue_dir="docs/sns/queue", replies_dir="data/sns/replies")
        (root / "accounts" / (name + ".json")).write_text(json.dumps(cfg))
    token = "a" * 43
    value = dict(schema_version=1, root=str(root), credentials=[dict(
        sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revoked=False, accounts={"first": "kopicha"}, scope="admin")])
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(path))
    monkeypatch.setenv("THTH_REPORT_TOKEN", token)
    return root


def test_MCPの管理口は範囲内のaccountだけ入れ替える(admin_credentials):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert "thth_admin_watch_set" in names
    result = server.call_tool("thth_admin_watch_set",
                              {"account": "first", "words": WORDS, "by": "masaru"})
    assert not result.get("isError")
    assert json.loads(result["content"][0]["text"])["watch_words"] == WORDS
    assert accounts.watch_words(accounts.load_account("first")) == WORDS
    # credential の外の account と受け取れない語は断る（台帳は変わらない）。
    denied = server.call_tool("thth_admin_watch_set",
                              {"account": "ghost", "words": WORDS, "by": "masaru"})
    assert denied["isError"] and denied["content"][0]["text"] == "scope_unavailable"
    too_many = server.call_tool("thth_admin_watch_set",
                                {"account": "first", "words": ["1", "2", "3", "4", "5", "6"],
                                 "by": "masaru"})
    assert too_many["isError"] and too_many["content"][0]["text"] == "invalid_options"
    assert accounts.watch_words(accounts.load_account("first")) == WORDS
    assert "watch_words" not in json.loads(
        (admin_credentials / "accounts" / "second.json").read_text())


def test_読む口からは語を変えられない(account, monkeypatch):
    """道具（morning）は語を**読むだけ**——選ばない・書かない（裁定 §7-2）。"""
    def forbidden(*args, **kwargs):
        pytest.fail("読む口が監視語を書き換えた")
    monkeypatch.setattr(watch_cli, "set_words", forbidden)
    payload = morning.build("kopicha")
    world = {node["section"]: node for node in payload["sections"]}["world"]
    assert world["cannot_say"] == "no_watch_words"
