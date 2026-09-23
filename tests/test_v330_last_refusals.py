"""3.3.0 B2 直前の断りを道具が覚えておく（last_refusals）と `--from-last-refusal`。

- 断ったとき（CLI の rc≠0・MCP の isError）に、account ごとに直近 5 件の
  `{at, version, command, reason_code, account}` を `state/<account>/last_refusals.json`
  （0600）に残す。**本文・引数の値・パス・秘密は残さない**（leak probe）。
- `thth report file <account> --from-last-refusal` と MCP
  `thth_report_file {from_last_refusal: true}` で、それを再現手順として付ける。
  **その account の控えだけ**を使う。
"""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from tests.test_v312_report_channel import user_server  # noqa: F401
from thth import accounts, cli, refusals, report_inbox

SECRET_BODY = "本文の秘密ABC"
SECRET_REASON = "取り下げの秘密の理由XYZ"


@pytest.fixture
def two(isolated_account_factory):
    info = isolated_account_factory("kopicha-threads", project="kopicha")
    isolated_account_factory("kopicha-bsky", project="kopicha", media="bluesky")
    return info


def _rows(account):
    path = os.path.join(accounts.state_dir_for(account), refusals.FILE)
    with open(path, encoding="utf-8") as stream:
        return path, stream.read()


def test_CLIの断りを5つの値だけで残す_本文も引数の値もパスも残さない(two, tmp_path, capsys):
    secret_file = tmp_path / "秘密のディレクトリ" / "本文.txt"
    secret_file.parent.mkdir()
    secret_file.write_text(SECRET_BODY, encoding="utf-8")
    argv = ["retract", "kopicha-threads", "17900000000000009", "--reason", SECRET_REASON]
    assert cli.main(argv) != 0
    capsys.readouterr()
    path, raw = _rows("kopicha-threads")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    [row] = json.loads(raw)
    assert set(row) == {"at", "version", "command", "reason_code", "account"}
    assert row["command"] == "retract" and row["account"] == "kopicha-threads"
    assert row["reason_code"].startswith("exit_")
    # leak probe: 引数の値・本文・パス・一時ディレクトリの名前は 1 文字も残らない。
    for value in (SECRET_REASON, "17900000000000009", SECRET_BODY, str(tmp_path), "/",
                  "秘密"):
        assert value not in raw.replace(row["at"], "")


def test_先頭の符丁は取り出し_値と重なる語は符丁にしない(two, monkeypatch, capsys):
    def refuse(args):
        print("approval_stale: 本文の秘密ABC を直してください", file=sys.stderr)
        return 2
    monkeypatch.setattr(cli, "cmd_queue", refuse)
    assert cli.main(["queue", "kopicha-threads"]) == 2
    capsys.readouterr()
    assert refusals.latest("kopicha-threads")["reason_code"] == "approval_stale"
    assert SECRET_BODY not in _rows("kopicha-threads")[1]

    # 1 行目の先頭が引数の値（account 名・自由文）なら符丁とみなさない。
    assert refusals.reason_code("kopicha_threads: 読めません", argv=["kopicha_threads"],
                                command="queue", rc=2) == "exit_2"
    assert refusals.reason_code("secret_word_here にしてください",
                                argv=["--title", "secret_word_here"], command="x", rc=1) == "exit_1"
    assert refusals.reason_code("target_unknown", argv=["morning", "nobody"],
                                command="morning", rc=2) == "target_unknown"
    assert refusals.reason_code("そのパスがありません: /tmp/x", argv=[], rc=2) == "exit_2"


def test_直近5件だけ残し報告の口の断りは残さない(two, monkeypatch, capsys):
    def refuse(args):
        return 2
    monkeypatch.setattr(cli, "cmd_queue", refuse)
    for _ in range(7):
        cli.main(["queue", "kopicha-threads"])
    assert len(refusals.read("kopicha-threads")) == 5
    before = refusals.read("kopicha-threads")
    # --by を付け忘れた報告の断りが、元の断りを押し出さない。
    assert cli.main(["report", "file", "kopicha-threads", "--from-last-refusal",
                     "--title", "t"]) == 2
    assert refusals.read("kopicha-threads") == before
    capsys.readouterr()


def test_台帳の無いaccountや壊れた名前には書かない(two, capsys):
    assert cli.main(["run", "../outside"]) == 2
    assert not refusals.record("nobody", command="run", reason_code="exit_2")
    assert not os.path.exists(os.path.join(accounts.state_dir_for("nobody"), refusals.FILE))
    capsys.readouterr()


def test_from_last_refusalの往復で再現手順が付く(two, monkeypatch, capsys):
    def refuse(args):
        print("approval_stale: 直してください", file=sys.stderr)
        return 2
    monkeypatch.setattr(cli, "cmd_queue", refuse)
    cli.main(["queue", "kopicha-threads"])
    capsys.readouterr()
    assert cli.main(["report", "file", "kopicha-threads", "--from-last-refusal",
                     "--title", "approval_stale で断られた", "--by", "kopicha-session",
                     "--json"]) == 0
    filed = json.loads(capsys.readouterr().out)
    record = report_inbox.show(filed["report_id"])
    assert record["kind"] == "friction"  # 種類を書かなければ friction
    assert record["body"] == refusals.DEFAULT_BODY
    assert "command: thth queue" in record["repro"]
    assert "reason_code: approval_stale" in record["repro"]
    assert "account: kopicha-threads" in record["repro"]


def test_from_last_refusalは他のaccountの控えを使わない(two, monkeypatch, capsys):
    def refuse(args):
        return 2
    monkeypatch.setattr(cli, "cmd_queue", refuse)
    cli.main(["queue", "kopicha-bsky"])
    capsys.readouterr()
    assert cli.main(["report", "file", "kopicha-threads", "--from-last-refusal",
                     "--title", "t", "--by", "s"]) == 2
    assert "no_last_refusal" in capsys.readouterr().err
    # 控えのファイルに他の account の行が紛れていても使わない。
    path = os.path.join(accounts.state_dir_for("kopicha-threads"), refusals.FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump([{"at": "2026-09-09T09:00:00+09:00", "version": "3.2.0",
                    "command": "queue", "reason_code": "exit_2",
                    "account": "kopicha-bsky"}], stream)
    assert refusals.latest("kopicha-threads") is None


def test_MCPの断りも控えて_from_last_refusalで置ける(user_server):  # noqa: F811
    _root, _path, _value, server = user_server
    denied = server.call_tool("thth_draft_put", {"account": "first", "file": "x.md",
                                                 "text": SECRET_BODY})
    assert denied["isError"]
    row = refusals.latest("first")
    assert row is not None and row["command"] == "mcp thth_draft_put"
    assert SECRET_BODY not in json.dumps(refusals.read("first"), ensure_ascii=False)
    # 許されていない account の断りは控えない。
    server.call_tool("thth_draft_put", {"account": "second", "file": "x.md", "text": "t"})
    assert refusals.read("second") == []
    result = server.call_tool("thth_report_file", {
        "account": "first", "title": f"{row['reason_code']} で断られた",
        "from_last_refusal": True})
    assert not result.get("isError"), result
    filed = json.loads(result["content"][0]["text"])
    record = report_inbox.show(filed["report_id"])
    assert record["kind"] == "friction"
    assert "command: MCP thth_draft_put" in record["repro"]
