"""報告の口 第 4 段——実装側が見落とさない・受け口の存在を LLM に返す（設計 3.1.2 §3・§3.5・§5）。

見るのは:
  - `handoff-report --since-last-read` の `tool` に、自分の project の報告の状態と
    **前回の栞から増えた返事**が出る（他 project の返事は出ない）。`report_channel`
    は開始手順（since_last_read）では常に出て、ふだんの応答には出ない。
  - `thth morning`（管理者＝CLI）の 0 段「道具」に「報告: 開いている n 件（新規 m 件）」。
    サーバ型の利用者の 1 枚には出さない。`limitations` の末尾に 1 行。
  - 断り（CLI の rc≠0・MCP の isError）に静的な 1 行。成功には載せない。
  - MCP の `tools/list`（利用者）の説明文の末尾に「うまくいかなければ thth_report_file」。
  - `thth --help` の末尾に 1 行。文面は 1 種類で、ここで固定する。
"""
from __future__ import annotations

import datetime
import hashlib
import json
from datetime import timedelta, timezone

import pytest

from thth import accounts, cli, jst, morning, operations_handoff, report_inbox

# **文面は静的**（設計 §3.5）。ここが正本の写し——変えるなら設計と一緒に。
# 3.3.0 B1・B4: friction と「報告は依頼の範囲内」の 1 文を足した。断りの行は
# account と理由の符丁を埋めた「打てる 1 行」になった（B3・`refusal_line()`）。
WELCOME = ("つまずき・迷い・期待との違いの報告は、利用者の作業の一部として歓迎します。"
           "小さいものも。重複は道具が束ねます")
CHANNEL_LINE = ("report_channel: thth report file <account> --kind bug|request|friction"
                "（MCP: thth_report_file）——" + WELCOME)
LIMITATION = "不具合・要望・つまずきは report の口へ（thth_report_file）"
HINT = "（うまくいかなければ thth_report_file）"


def test_受け口の文面は1種類で設計のまま():
    assert report_inbox.CHANNEL_LINE == CHANNEL_LINE
    assert report_inbox.CHANNEL == {
        "cli": "thth report file <account> --kind bug|request|friction --title … --body-file … --by <名前>",
        "from_last_refusal": "thth report file <account> --from-last-refusal --title … --by <名前>",
        "mcp": "thth_report_file", "when": "unexpected_or_unsupported",
        "welcome": WELCOME,
        "examples": ["止まったのに気づかなかった", "断られた理由が分からなかった",
                     "同じ操作を 3 回繰り返した"]}


@pytest.fixture
def projects(isolated_account_factory):
    info = isolated_account_factory("kopicha-threads", project="kopicha")
    isolated_account_factory("kopicha-bsky", project="kopicha", media="bluesky")
    isolated_account_factory("other-threads", project="other")
    return info


# ------------------------------------------------------------ handoff-report

def test_返事はhandoff_reportのtoolに出て_栞より後の分だけが新しい(projects, capsys):
    now = jst.now_jst()
    mine = report_inbox.file_report("kopicha-bsky", kind="bug", title="混ざる", body="本文", by="s")
    theirs = report_inbox.file_report("other-threads", kind="bug", title="他", body="他の", by="o")
    report_inbox.reply(mine["report_id"], by="masaru", text="見ています", now=now)
    report_inbox.reply(theirs["report_id"], by="masaru", text="他の project への返事", now=now)
    # 栞が無ければ全部を新しいとみなす（見落とすより重ねて見せる）。同じ project の
    # 別 account（kopicha-bsky）の報告も「自分の project の報告」に入る。
    tool = operations_handoff.answer("kopicha-threads", now=now, since_last_read=True)["tool"]
    assert tool["report_channel"] == report_inbox.CHANNEL
    reports = tool["reports"]
    assert reports["open"] == 1 and reports["denominator"] == 1 and reports["replies"] == 1
    assert [(row["report_id"], row["text"]) for row in reports["new_replies"]] == [
        (mine["report_id"], "見ています")]
    assert "他の project への返事" not in json.dumps(tool, ensure_ascii=False)

    # 栞を進めてから返事が 1 つ増えると、その 1 つだけが新しい。
    assert cli.main(["handoff-report", "kopicha-threads", "--since-last-read", "--mark-read",
                     "--by", "s", "--json"]) == 0
    capsys.readouterr()
    later = now + timedelta(hours=1)
    report_inbox.reply(mine["report_id"], by="masaru", text="直しました", now=later)
    tool = operations_handoff.answer("kopicha-threads", now=later + timedelta(minutes=1),
                                     since_last_read=True)["tool"]
    assert [row["text"] for row in tool["reports"]["new_replies"]] == ["直しました"]
    assert tool["reports"]["replies"] == 2 and tool["reports"]["since"] == jst.iso(now)


def test_projectでまとめても他projectの返事は出ない(projects):
    theirs = report_inbox.file_report("other-threads", kind="bug", title="他", body="他の", by="o")
    report_inbox.reply(theirs["report_id"], by="masaru", text="他の project への返事")
    payload = operations_handoff.answer(project="kopicha", since_last_read=True)
    assert payload["tool"]["reports"]["denominator"] == 0
    assert payload["tool"]["report_channel"] == report_inbox.CHANNEL
    assert "他の project への返事" not in json.dumps(payload, ensure_ascii=False)


def test_ふだんのhandoff_reportには受け口を載せない(projects):
    payload = operations_handoff.answer("kopicha-threads")
    assert "report_channel" not in payload["tool"] and "reports" not in payload["tool"]


def test_人向けのhandoff_reportにも返事と受け口の1行(projects, capsys):
    mine = report_inbox.file_report("kopicha-threads", kind="bug", title="混ざる", body="本文", by="s")
    report_inbox.reply(mine["report_id"], by="masaru", text="見ています\n二行目")
    assert cli.main(["handoff-report", "kopicha-threads", "--since-last-read"]) == 0
    out = capsys.readouterr().out
    assert "- 報告: 開いている 1/1 件・新しい返事 1 件" in out
    assert f"  - {mine['report_id']} masaru: 見ています" in out
    assert "- " + CHANNEL_LINE in out


def test_置き場が読めなくてもhandoff_reportは止まらない(projects, monkeypatch):
    monkeypatch.setattr(report_inbox, "load_all",
                        lambda: (_ for _ in ()).throw(report_inbox.ReportError("report_store_unavailable")))
    tool = operations_handoff.answer("kopicha-threads", since_last_read=True)["tool"]
    assert tool["reports"]["cannot_say"] == "report_store_unavailable"
    assert tool["reports"]["open"] is None and tool["report_channel"] == report_inbox.CHANNEL


# ------------------------------------------------------------------ morning

def _tool_section(payload):
    return next(section for section in payload["sections"] if section["section"] == "tool")


def test_管理者の毎朝の一枚に開いている件数と新規(projects, capsys, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    now = jst.now_jst()
    old = report_inbox.file_report("other-threads", kind="bug", title="古い", body="古い",
                                   by="o", now=now - timedelta(hours=30))
    report_inbox.file_report("other-threads", kind="request", title="新しい", body="新しい", by="o")
    closed = report_inbox.file_report("kopicha-threads", kind="bug", title="閉じた", body="閉", by="s")
    report_inbox.close(closed["report_id"], by="masaru", reason="fixed", version="3.1.2")
    payload = morning.build("kopicha", now=now, mark=False)
    reports = _tool_section(payload)["value"]["reports"]
    assert (reports["open"], reports["new"], reports["denominator"]) == (2, 1, 3)
    assert reports["next"] == "thth admin reports list --status open" and reports["cannot_say"] is None
    assert payload["limitations"][-1] == LIMITATION
    morning.render(payload, out=lambda line: print(line))
    out = capsys.readouterr().out
    assert "報告: 開いている 2 件（新規 1 件・直近 24 時間）  → thth admin reports list --status open" in out
    # 0 段は件数だけ（id と題は第 5 段「次の一手」に 1 件 1 行で並ぶ）。
    assert old["report_id"] not in json.dumps(_tool_section(payload))


def test_管理者の次の一手に開いている報告が1件1行で本文は載らない(projects, capsys, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    long_title = "題" * 70
    first = report_inbox.file_report("other-threads", kind="bug", title=long_title,
                                     body="本文は次の一手に載らない", by="o")
    second = report_inbox.file_report("kopicha-threads", kind="request", title="欲しい形",
                                      body="これも載らない", by="s")
    closed = report_inbox.file_report("kopicha-threads", kind="bug", title="閉じた", body="閉", by="s")
    report_inbox.close(closed["report_id"], by="masaru", reason="fixed", version="3.1.2")
    payload = morning.build("kopicha", mark=False)
    steps = next(s for s in payload["sections"] if s["section"] == "next_steps")["value"]["steps"]
    rows = [step for step in steps if step["kind"] == "report"]
    assert sorted(rows, key=lambda row: row["report_kind"]) == [
        {"kind": "report", "report_id": first["report_id"], "report_kind": "bug", "title": "題" * 60},
        {"kind": "report", "report_id": second["report_id"], "report_kind": "request", "title": "欲しい形"}]
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "本文は次の一手に載らない" not in dumped and "これも載らない" not in dumped
    assert "body" not in json.dumps(rows)
    morning.render(payload, out=lambda line: print(line))
    assert f"  報告  {second['report_id']}  request  欲しい形" in capsys.readouterr().out
    # サーバ型の利用者の 1 枚には並べない。
    user = morning.build("kopicha", mark=False, allowed_names=("kopicha-threads",))
    user_steps = next(s for s in user["sections"] if s["section"] == "next_steps")["value"]["steps"]
    assert not [step for step in user_steps if step["kind"] == "report"]


def test_サーバ型の利用者の一枚には件数を出さない(projects, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    report_inbox.file_report("other-threads", kind="bug", title="他", body="他の", by="o")
    payload = morning.build("kopicha", mark=False, allowed_names=("kopicha-threads",))
    assert _tool_section(payload)["value"]["reports"] is None
    assert payload["limitations"][-1] == LIMITATION


# ------------------------------------------------------------ CLI の断り

def test_CLIの断りの後ろに受け口の1行_成功には載せない(projects, tmp_path, capsys):
    assert cli.main(["morning", "nobody", "--json"]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    # 3.3.0 B3: 理由の符丁を埋めた打てる 1 行（account を持たない命令は <account> のまま）。
    assert err == ["target_unknown",
                   'report_channel: thth report file <account> --kind friction --from-last-refusal'
                   ' --title "target_unknown で断られた" --by <名前>（' + WELCOME + '）']
    assert cli.main(["report", "list", "kopicha", "--json"]) == 0
    assert CHANNEL_LINE not in capsys.readouterr().err


def test_lintの検査結果の1は断りではない(projects, tmp_path, capsys):
    bad = tmp_path / "bad.md"
    bad.write_text("---\nthth: 1\n---\n本文\n", encoding="utf-8")
    rc = cli.main(["lint", str(bad), "--json"])
    assert rc == 1
    assert "report_channel" not in capsys.readouterr().err


def test_helpの末尾に1行(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out.rstrip().splitlines()
    assert out[-1] == "不具合・要望・つまずきは report の口へ: thth report file <account> --kind bug|request|friction（MCP: thth_report_file）"
    with pytest.raises(SystemExit):
        cli.main(["report", "--help"])
    assert "道具が断った・結果が期待と違った・欲しい形がある" in capsys.readouterr().out


# ------------------------------------------------------------------ MCP

@pytest.fixture
def user_server(tmp_path, monkeypatch):
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
    token = "d" * 43
    value = dict(schema_version=1, root=str(root), credentials=[dict(
        sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=(datetime.datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revoked=False, accounts={"first": "kopicha"}, scope="user", actor="kopicha-person")])
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(path))
    monkeypatch.setenv("THTH_REPORT_TOKEN", token)
    from tests.test_mcp import _load_server_module
    return root, path, value, _load_server_module()


def test_MCPの断りは2つ目のtextに受け口_成功には載せない(user_server):
    _root, _path, _value, server = user_server
    denied = server.call_tool("thth_morning", {"target": "other"})
    assert denied["isError"] and denied["content"][0]["text"] == "scope_unavailable"
    assert denied["content"][1] == {"type": "text", "text": CHANNEL_LINE}
    ok = server.call_tool("thth_report_list", {})
    assert not ok.get("isError") and len(ok["content"]) == 1
    assert CHANNEL_LINE not in json.dumps(ok, ensure_ascii=False)


def test_MCPのoperations_handoffにも返事と受け口(user_server):
    _root, _path, _value, server = user_server
    filed = json.loads(server.call_tool("thth_report_file", {
        "account": "first", "kind": "bug", "title": "題", "body": "本文"})["content"][0]["text"])
    report_inbox.reply(filed["report_id"], by="masaru", text="見ています")
    theirs = report_inbox.file_report("second", kind="bug", title="他", body="他の", by="o")
    report_inbox.reply(theirs["report_id"], by="masaru", text="他の project への返事")
    result = server.call_tool("operations_handoff", {"account": "first", "since_last_read": True})
    payload = json.loads(result["content"][0]["text"])
    assert payload["tool"]["report_channel"] == report_inbox.CHANNEL
    assert [row["text"] for row in payload["tool"]["reports"]["new_replies"]] == ["見ています"]
    assert "他の project への返事" not in result["content"][0]["text"]


def test_利用者のtools_listの説明文の末尾に1句(user_server, monkeypatch):
    _root, path, value, server = user_server
    listed = server._handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]
    for tool in listed:
        if tool["name"].startswith("thth_report_"):
            assert HINT not in tool["description"]
        else:
            assert tool["description"].endswith(HINT), tool["name"]
    file_tool = next(tool for tool in listed if tool["name"] == "thth_report_file")
    assert file_tool["description"].startswith("道具が断った・結果が期待と違った・欲しい形がある・迷った、のどれかならこれで置く")
    # 定数（売り文句の正本）は書き換えない。
    assert not any(tool["description"].endswith(HINT) for tool in server.TOOLS)
    # 管理者の一覧には足さない（管理者は報告を置く側ではない）。
    value["credentials"][0].update(scope="admin")
    value["credentials"][0].pop("actor")
    path.write_text(json.dumps(value))
    admin = server._handle_request({"id": 2, "method": "tools/list"})["result"]["tools"]
    assert admin and not any(tool["description"].endswith(HINT) for tool in admin)


def test_手元のstdioの断りにも受け口(monkeypatch):
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    denied = server.call_tool("no_such_tool", {})
    assert denied["isError"] and denied["content"][-1]["text"] == CHANNEL_LINE
