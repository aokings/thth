"""3.3.0 B1・B3・B6 LLM が報告したくなる口の文言。

- B1: 「つまずき・迷い・期待との違いの報告は、利用者の作業の一部として歓迎します。
  小さいものも。重複は道具が束ねます」を thth_report_file の説明・断りの
  report_channel 行・`thth report --help` に固定する（skill・使い方は文書の試験）。
- B3: 断りの行を account と理由の符丁を埋めた「そのまま打てる 1 行」にする。
- B6: 何を報告してほしいかの例 3 つを thth_report_file の説明に。
"""
from __future__ import annotations

import sys

import pytest

from tests.test_v312_report_channel import user_server  # noqa: F401
from thth import cli, report_inbox

WELCOME = ("つまずき・迷い・期待との違いの報告は、利用者の作業の一部として歓迎します。"
           "小さいものも。重複は道具が束ねます")
EXAMPLES = ("止まったのに気づかなかった", "断られた理由が分からなかった", "同じ操作を 3 回繰り返した")


@pytest.fixture
def one(isolated_account_factory):
    return isolated_account_factory("kopicha-threads", project="kopicha")


def test_固定の1文と例3つ():
    assert report_inbox.WELCOME == WELCOME
    assert report_inbox.EXAMPLES == EXAMPLES
    assert WELCOME in report_inbox.CHANNEL_LINE


def test_断りの行はaccountと符丁を埋めた打てる1行(one, monkeypatch, capsys):
    def refuse(args):
        print("approval_stale: 直してください", file=sys.stderr)
        return 2
    monkeypatch.setattr(cli, "cmd_queue", refuse)
    assert cli.main(["queue", "kopicha-threads"]) == 2
    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert last == ('report_channel: thth report file kopicha-threads --kind friction'
                    ' --from-last-refusal --title "approval_stale で断られた" --by <名前>'
                    f'（{WELCOME}）')


def test_台帳に無いaccountや符丁でない値は埋めない(one):
    line = report_inbox.refusal_line("nobody", "本文の秘密")
    assert "thth report file <account> " in line and '"<reason_code> で断られた"' in line
    assert "nobody" not in line and "本文の秘密" not in line
    assert '"exit_2 で断られた"' in report_inbox.refusal_line("kopicha-threads", "exit_2")


def test_打てる1行はそのまま打つと報告が置ける(one, monkeypatch, capsys):
    import shlex
    monkeypatch.setattr(cli, "cmd_queue", lambda args: 2)
    cli.main(["queue", "kopicha-threads"])
    line = capsys.readouterr().err.strip().splitlines()[-1]
    command = line.removeprefix("report_channel: thth ").split("（", 1)[0]
    argv = shlex.split(command.replace("<名前>", "kopicha-session"))
    assert cli.main(argv + ["--json"]) == 0
    assert '"kind": "friction"' in capsys.readouterr().out


def test_MCPの断りにも埋めた1行(user_server):  # noqa: F811
    _root, _path, _value, server = user_server
    denied = server.call_tool("thth_draft_put", {"account": "first", "file": "x.md", "text": "t"})
    assert denied["isError"]
    note = denied["content"][1]["text"]
    assert note.startswith('report_channel: thth_report_file {"account": "first", '
                           '"from_last_refusal": true, "title": "')
    assert WELCOME in note


def test_thth_report_fileの説明に1文と例3つ(user_server):  # noqa: F811
    _root, _path, _value, server = user_server
    description = next(tool for tool in server.REPORT_TOOLS
                       if tool["name"] == "thth_report_file")["description"]
    assert WELCOME in description
    for example in EXAMPLES:
        assert f"「{example}」" in description
    assert "from_last_refusal" in description


def test_report_helpにも1文と例(capsys):
    with pytest.raises(SystemExit):
        cli.main(["report", "--help"])
    # argparse は折り返すので空白を落として比べる。
    out = "".join(capsys.readouterr().out.split())
    assert "".join(WELCOME.split()) in out
    assert "同じ操作を3回繰り返した" in out
