"""3.3.0 §F「毎朝の一枚」を「観測」にし、いつでも引けるようにする。

- `thth observe`・MCP `thth_observe`。`thth morning`／`thth_morning` は同じものの別名。
  JSON の `report_type` は `observe`、呼んだ名前は `invoked_as`。
- 第 2 段は「前回の観測から」: 窓は前回の栞の時刻から今まで。栞が無い・7 日より古い
  ときは前日 JST（`window.basis` が `since_last_observe` か `yesterday_jst`）。
- `--no-mark` では栞が進まない。
"""
from __future__ import annotations

import datetime
import json

import pytest

from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one, sections  # noqa: F401
from thth import cli, handoff_cursor, jst, morning, operations_handoff
from thth import read_coordination
from tests.test_v312_report_channel import user_server  # noqa: F401,E402


def _second(payload):
    return sections(payload)["yesterday"]


def _cursor_at(at):
    node = operations_handoff.answer(ACCOUNT, now=at)["by_account"][ACCOUNT]
    handoff_cursor.write(ACCOUNT, node, "テスト", at)


def test_別名でも同じJSONでinvoked_asだけ違う(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--json", "--no-mark"]) == 0
    observed = json.loads(capsys.readouterr().out)
    assert cli.main(["morning", PROJECT, "--json", "--no-mark"]) == 0
    alias = json.loads(capsys.readouterr().out)
    assert observed["report_type"] == alias["report_type"] == "observe"
    assert (observed["invoked_as"], alias["invoked_as"]) == ("observe", "morning")
    observed.pop("invoked_as"), alias.pop("invoked_as")
    assert observed == alias


def test_栞が無ければ前日JSTの窓(one):
    second = _second(morning.build(PROJECT, now=NOW, mark=False))
    assert second["title"] == "昨日の自分"
    value = second["value"]["by_account"][ACCOUNT]["value"]
    assert value["window"]["basis"] == "yesterday_jst"
    assert value["window"]["since"] == "2026-09-22T00:00:00+09:00"
    assert value["window"]["until"] == "2026-09-23T00:00:00+09:00"
    assert value["n"] == 1  # 昨日 09:00 の投稿


def test_栞があれば前回の観測から今までの窓(one, capsys):
    _cursor_at(datetime.datetime(2026, 9, 22, 10, 0, tzinfo=jst.JST))
    payload = morning.build(PROJECT, now=NOW, mark=False)
    second = _second(payload)
    assert second["title"] == "前回の観測から"
    value = second["value"]["by_account"][ACCOUNT]["value"]
    assert value["window"] == {"since": "2026-09-22T10:00:00+09:00",
                               "until": jst.iso(NOW), "basis": "since_last_observe",
                               "time_field": "posted_at_jst"}
    # 昨日 09:00 の投稿は前回の観測（10:00）より前なので入らない（前日固定なら入る）。
    assert value["n"] == 0
    morning.render(payload, out=print)
    out = capsys.readouterr().out
    assert "[前回の観測から]" in out and "前回の観測からの投稿 0/" in out


def test_栞が前回の観測の前なら投稿が窓に入る(one):
    _cursor_at(datetime.datetime(2026, 9, 22, 8, 0, tzinfo=jst.JST))
    value = _second(morning.build(PROJECT, now=NOW, mark=False))["value"]["by_account"][ACCOUNT]["value"]
    assert value["window"]["basis"] == "since_last_observe" and value["n"] == 1
    # 24h 前との差は従前どおり。
    assert value["posts"][0]["delta_24h"]["views"] == 30


def test_7日より古い栞なら前日JSTに戻る(one):
    _cursor_at(NOW - datetime.timedelta(days=8))
    second = _second(morning.build(PROJECT, now=NOW, mark=False))
    assert second["title"] == "昨日の自分"
    assert second["value"]["by_account"][ACCOUNT]["value"]["window"]["basis"] == "yesterday_jst"


def test_観測は栞を進めno_markでは進めない(one):
    _cursor_at(datetime.datetime(2026, 9, 22, 10, 0, tzinfo=jst.JST))
    morning.build(PROJECT, now=NOW, mark=False)
    previous, _ = handoff_cursor.read(ACCOUNT, NOW)
    assert previous["read_at"] == "2026-09-22T10:00:00+09:00"
    payload = morning.build(PROJECT, now=NOW, invoked_as="observe")
    assert payload["marked_by"] == "observe"
    previous, _ = handoff_cursor.read(ACCOUNT, NOW)
    assert previous["read_at"] == jst.iso(NOW) and previous["by"] == "observe"


def test_MCPのthth_observeの説明文とCLIの呼び方(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    tool = next(item for item in server.TOOLS if item["name"] == "thth_observe")
    assert tool["description"] == (
        "セッションの始めと区切りごとに呼ぶ。前回の観測から何があって、いまなにを"
        "すればよいかを、道具が事実だけで 1 枚にする。本文は作らない")
    seen = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(server, "run_cli", lambda args, **kwargs: seen.append(args) or Done())
    server.call_tool("thth_observe", {"target": PROJECT, "mark": False})
    server.call_tool("thth_morning", {"target": PROJECT})
    assert seen == [["observe", PROJECT, "--no-mark", "--json"], ["morning", PROJECT, "--json"]]


def test_サーバ型のthth_observeも同じ1枚(user_server):  # noqa: F811
    _root, _path, _value, server = user_server
    listed = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert {"thth_observe", "thth_morning"} <= listed
    observed = json.loads(server.call_tool("thth_observe", {"target": "first"})["content"][0]["text"])
    alias = json.loads(server.call_tool("thth_morning", {"target": "first"})["content"][0]["text"])
    assert observed["report_type"] == alias["report_type"] == "observe"
    assert (observed["invoked_as"], alias["invoked_as"]) == ("observe", "morning")


def test_observeは読む口として協調に載る():
    assert "observe" in read_coordination.READS and "morning" in read_coordination.READS


@pytest.mark.parametrize("name", ["observe", "morning"])
def test_helpの文言(name, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert name in out
    assert "セッションの始めと区切りごと" in out
