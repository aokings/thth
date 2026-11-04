"""段 F 手数の計測（設計「監査と審査の機械化と前向きな断り」§4・§5・`thth/meter.py`）。

- 控えは `state/<account>/command_meter.ndjson`（0600）・1 行 6 つの値だけ。本文・引数の値・パスは残さない。
- 断りの率（7 日・100 命令あたり）と同じ符丁の 10 分内の繰り返しを observe と dashboard の `--json` に。0 なら鍵が無い。
- 違反の 3 型（同じ符丁を 10 分に 3 回・`--preview` なしで広場に置く・lint か dry-run なしで send）。
"""
from __future__ import annotations

import datetime
import json
import os
import stat
import sys

import pytest

from tests.test_v312_report_channel import user_server  # noqa: F401
from thth import accounts, cli, dashboard_cli, jst, meter, morning

SECRET = "本文の秘密ABC"


@pytest.fixture
def two(isolated_account_factory):
    info = isolated_account_factory("kopicha-threads", project="kopicha")
    isolated_account_factory("kopicha-bsky", project="kopicha", media="bluesky")
    return info


def _raw(account):
    path = os.path.join(accounts.state_dir_for(account), meter.FILE)
    with open(path, encoding="utf-8") as stream:
        return path, stream.read()


def _at(minutes):
    return jst.now_jst() - datetime.timedelta(minutes=minutes)


def test_CLIの命令を6つの値だけで控える_本文も引数の値も残さない(two, monkeypatch, capsys, tmp_path):
    def refuse(args):
        print(f"approval_stale: {SECRET} を直してください", file=sys.stderr)
        return 2
    monkeypatch.setattr(cli, "cmd_send", refuse)
    assert cli.main(["send", "kopicha-threads", "--production"]) == 2
    monkeypatch.setattr(cli, "cmd_send", lambda args: 0)
    assert cli.main(["send", "kopicha-threads"]) == 0
    capsys.readouterr()
    path, raw = _raw("kopicha-threads")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    rows = [json.loads(line) for line in raw.splitlines()]
    assert [set(row) for row in rows] == [set(meter.KEYS)] * 2
    assert [(r["command"], r["outcome"], r["code"], r["marks"]) for r in rows] == [
        ("send", "refused", "approval_stale", ["production"]), ("send", "ok", None, [])]
    assert SECRET not in raw and str(tmp_path) not in raw


def test_読む命令は通ったときに何も書かない_断りだけ数える(two, monkeypatch, capsys):
    monkeypatch.setattr(cli, "cmd_queue", lambda args: 0)
    assert cli.main(["queue", "kopicha-threads"]) == 0
    assert not os.path.exists(os.path.join(accounts.state_dir_for("kopicha-threads"), meter.FILE))
    monkeypatch.setattr(cli, "cmd_queue", lambda args: 2)
    assert cli.main(["queue", "kopicha-threads"]) == 2
    capsys.readouterr()
    [row] = meter.read("kopicha-threads")
    assert (row["command"], row["outcome"]) == ("queue", "refused")
    one = meter.rate("kopicha-threads")
    assert (one["commands"], one["refusals"], one["read_refusals"], one["per_100"]) == (0, 0, 1, None)
    assert meter.rate_line(meter.summary(["kopicha-threads"])) == "手数（7 日）: kopicha-threads・読む命令の断り 1"
    # 報告の口は口座の state に何も足さない。
    assert not meter.note("kopicha-threads", command="report file", outcome="refused", code="by_required")
    assert not meter.note("kopicha-threads", command="mcp thth_report_file", outcome="refused", code="by_required")
    assert not meter.note("kopicha-threads", command="article diff", outcome="ok")
    assert not meter.note("kopicha-threads", command="mcp thth_posts", outcome="ok")


def test_通るときの出力は変わらない(two, monkeypatch, capsys):
    def quiet(args):
        print("いつもの出力")
        return 0
    monkeypatch.setattr(cli, "cmd_send", quiet)
    assert cli.main(["send", "kopicha-threads"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "いつもの出力\n" and captured.err == ""
    assert len(meter.read("kopicha-threads")) == 1


def test_台帳の無い口座と乾式の試験には書かない(two, capsys):
    assert not meter.note("nobody", command="send", outcome="ok")
    assert not os.path.exists(os.path.join(accounts.state_dir_for("nobody"), meter.FILE))
    assert not meter.note("kopicha-threads", command="send", outcome="refused", code=None)
    assert not meter.note("kopicha-threads", command="send", outcome="ok", marks=["--secret"])
    capsys.readouterr()


def test_旗は決まった名前だけ():
    assert meter.marks_of_argv(["plaza", "post", "a", "--preview", "--title=x", "--open",
                                "--production", "--dry-run", "--confirm", "abc"]) == [
        "dry_run", "open", "preview", "production"]
    assert meter.marks_of_arguments({"preview": True, "visibility": "open", "body": SECRET}) == ["open", "preview"]
    assert meter.marks_of_arguments({"preview": "yes"}) == []


def test_率は0なら出さない_断りがあれば100命令あたり(two):
    assert meter.rate("kopicha-threads") is None
    assert meter.summary(["kopicha-threads", "kopicha-bsky"]) is None
    for i in range(8):
        meter.note("kopicha-threads", command="send", outcome="ok", now=_at(100 + i))
    meter.note("kopicha-threads", command="send", outcome="refused", code="too_soon", now=_at(30))
    meter.note("kopicha-threads", command="send", outcome="refused", code="too_soon", now=_at(25))
    # 二段確認の 1 段目は命令に数え、断りに数えない。
    meter.note("kopicha-threads", command="approve", outcome="first_stage", now=_at(20))
    # 前の 7 日（比べる相手）。
    meter.note("kopicha-threads", command="send", outcome="refused", code="target_unknown",
               now=jst.now_jst() - datetime.timedelta(days=9))
    one = meter.rate("kopicha-threads")
    assert one["commands"] == 11 and one["refusals"] == 2 and one["per_100"] == 18.2
    assert one["repeats_10min"] == 1
    assert one["top_codes"] == [{"code": "too_soon", "n": 2}]
    assert one["previous_7d"] == {"commands": 1, "refusals": 1, "per_100": 100.0}
    line = meter.rate_line(meter.summary(["kopicha-threads"]))
    assert line == ("手数（7 日）: kopicha-threads 断り 100 命令あたり 18.2（2/11）"
                    "・同じ符丁の 10 分内の繰り返し 1・前の 7 日 100.0")


def test_違反の3型(two):
    name = "kopicha-threads"
    assert meter.violation(name) is None
    # 1. 同じ符丁を 10 分に 3 回。
    for minutes in (50, 46, 42):
        meter.note(name, command="send", outcome="refused", code="too_soon", marks=["production"],
                   now=_at(minutes))
    # 2. --preview なしで広場に置いた（open の一段目だけで確定）と、--preview を見てから置いた。
    meter.note(name, command="plaza post", outcome="first_stage", marks=["open"], now=_at(40))
    meter.note(name, command="plaza post", outcome="ok", marks=["open"], now=_at(39))
    meter.note(name, command="plaza post", outcome="first_stage", marks=["preview"], now=_at(38))
    meter.note(name, command="plaza post", outcome="ok", now=_at(37))
    # 3. lint も dry-run も無い send と、dry-run のあとの send。
    meter.note(name, command="send", outcome="ok", marks=["production"], now=_at(36))
    meter.note(name, command="send", outcome="ok", now=_at(35))
    meter.note(name, command="send", outcome="ok", marks=["production"], now=_at(34))
    bad = meter.violation(name)
    assert (bad["same_code_3_in_10min"], bad["same_code_3_in_10min_codes"],
            bad["post_without_preview"], bad["send_without_check"]) == (1, ["too_soon"], 1, 1)
    line = meter.violation_line(meter.summary([name]))
    assert line.startswith("手順の飛ばし（7 日）: kopicha-threads 同じ断りを 10 分に 3 回 1（too_soon）"
                           "・--preview なしで広場に置いた 1・lint か --dry-run なしで send 1。")
    assert line.endswith("。通る順: 同じ断りは「通すには」の命令を先に・広場は先に thth plaza post … --preview・send は先に thth lint か --dry-run")


def test_lintは原稿のaccountに数える(two, capsys):
    repo = two["repo_dir"] if isinstance(two, dict) and "repo_dir" in two else None
    draft = os.path.join(repo or accounts.load_account("kopicha-threads")["repo_dir"],
                         "docs", "sns", "queue", "meter-lint.md")
    os.makedirs(os.path.dirname(draft), exist_ok=True)
    with open(draft, "w", encoding="utf-8") as stream:
        stream.write("---\naccount: kopicha-threads\nstatus: draft\n---\n本文\n")
    cli.main(["lint", draft])
    capsys.readouterr()
    rows = meter.read("kopicha-threads")
    assert [row["command"] for row in rows] == ["lint"]
    assert rows[0]["outcome"] in ("ok", "refused")


def test_observeとdashboardに鍵を足す_0なら鍵が無い(two):
    payload = morning.build("kopicha-threads", mark=False, no_world=True)
    assert "refusal_meter" not in payload["sections_by_name"]["tool"]["value"]
    board = dashboard_cli.build("kopicha")
    assert "refusal_meter" not in board
    assert "refusal_meter" not in dashboard_cli.build()

    for minutes in (9, 6, 3):
        meter.note("kopicha-threads", command="send", outcome="refused", code="too_soon", now=_at(minutes))
    payload = morning.build("kopicha-threads", mark=False, no_world=True)
    value = payload["sections_by_name"]["tool"]["value"]["refusal_meter"]
    assert value["rates"][0]["account"] == "kopicha-threads"
    assert value["violations"][0]["same_code_3_in_10min"] == 1
    lines = []
    morning.render(payload, out=lines.append)
    assert any(line.startswith("  手順の飛ばし（7 日）: ") for line in lines)
    assert any(line.startswith("  手数（7 日）: kopicha-threads 断り 100 命令あたり 100.0（3/3）") for line in lines)
    assert dashboard_cli.build("kopicha")["refusal_meter"]["rates"][0]["refusals"] == 3
    assert dashboard_cli.build()["refusal_meter"]["rates"][0]["account"] == "kopicha-threads"
    # 許された口座の外は見せない。
    assert "refusal_meter" not in dashboard_cli.build(allowed_names=["kopicha-bsky"])


def test_大きくなったら14日の内に詰め直す(two, monkeypatch):
    monkeypatch.setattr(meter, "MAX_BYTES", 2000)
    monkeypatch.setattr(meter, "MAX_ROWS", 10)
    meter.note("kopicha-threads", command="send", outcome="ok",
               now=jst.now_jst() - datetime.timedelta(days=20))
    for i in range(40):
        meter.note("kopicha-threads", command="send", outcome="ok", now=_at(40 - i))
    path, raw = _raw("kopicha-threads")
    assert os.path.getsize(path) <= 2000
    rows = meter.read("kopicha-threads")
    assert len(rows) <= 10 + 30
    floor = jst.now_jst() - datetime.timedelta(days=meter.KEEP_DAYS)
    assert rows and all(jst.parse(row["at"]) >= floor for row in rows)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_小さいファイルでも15日前の行は次に書くときに消える(two):
    """14 日の約束は大きさに依らない（監査 5.12.0「出したあとでよい」1）。"""
    meter.note("kopicha-threads", command="send", outcome="ok",
               now=jst.now_jst() - datetime.timedelta(days=15))
    meter.note("kopicha-threads", command="send", outcome="ok", now=_at(5))
    path, raw = _raw("kopicha-threads")
    assert os.path.getsize(path) < meter.MAX_BYTES
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 1 and jst.parse(rows[0]["at"]) > jst.now_jst() - datetime.timedelta(hours=1)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_MCPのサーバ型も控える_許された口座だけ(user_server):  # noqa: F811
    _root, _path, _value, server = user_server
    denied = server.call_tool("thth_draft_put", {"account": "first", "file": "x.md", "text": SECRET})
    assert denied["isError"]
    server.call_tool("thth_draft_put", {"account": "second", "file": "x.md", "text": "t"})
    rows = meter.read("first")
    assert [(r["command"], r["outcome"]) for r in rows] == [("mcp thth_draft_put", "refused")]
    assert rows[0]["code"] and SECRET not in json.dumps(rows, ensure_ascii=False)
    assert meter.read("second") == []
