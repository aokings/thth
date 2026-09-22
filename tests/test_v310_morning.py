"""3.1.0「毎朝の一枚」（設計 3.1.0）。6 段が揃う・欠けた段は null と静的な理由。

見るのは設計 §2・§4・§7 の約束そのもの:

  - 6 段が全部出る（偽の材料で）。
  - 1 段が落ちても他の 5 段は出る（`{"value": null, "cannot_say": "<静的>"}`）。
  - X の読取予算 0 なら 1〜3 段は `budget_exhausted`・4 段は予算の行を出す。
  - 叩いた回数（`calls`）が媒体ごと・endpoint ごとに出る。
  - 栞は既定で進み、`--no-mark` では進まない。
  - `--json` に絶対パスが無い・本文は 60 字まで。
  - 「次の一手」は候補の列挙だけ——**`body` という鍵はどこにも無い**。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import pytest

from thth import accounts, adapters, budget_x, cli, jst, morning
from thth.adapters import base as adapter_base

NOW = datetime.datetime(2026, 9, 23, 7, 0, 0, tzinfo=jst.JST)
YESTERDAY = "2026-09-22T09:00:00+09:00"
ACCOUNT = "kopicha-threads"
PROJECT = "kopicha"
LONG_TEXT = "あ" * 200

# leak probe（発注 5・`test_v210_json_contract` と同じ根）。
SERVER_PATH = re.compile(r"/(?:Users|private|tmp|srv)")


class Fake:
    """偽の媒体。`keyword_search`・`mentions` だけを持ち、落ち方も注入できる。"""

    def __init__(self, *, search=None, mentions=None, search_error=None, mentions_error=None):
        self._search = search if search is not None else []
        self._mentions = mentions if mentions is not None else []
        self._search_error = search_error
        self._mentions_error = mentions_error
        self.calls = []

    def keyword_search(self, word, **kwargs):
        self.calls.append(("keyword_search", word))
        if self._search_error is not None:
            raise self._search_error
        return list(self._search)

    def mentions(self, *, since=None):
        self.calls.append(("mentions", since))
        if self._mentions_error is not None:
            raise self._mentions_error
        return list(self._mentions)


def message(post_id, *, username="someone", text=LONG_TEXT, stamp="2026-09-23T06:00:00+09:00"):
    return {"message_id": post_id, "username": username, "author_key": "a" * 16,
            "text": text, "timestamp": stamp, "medium": "threads",
            "permalink": "https://www.threads.net/@someone/post/" + post_id,
            "has_replies": True}


def write_measured(info, name, post_id, rows):
    cfg = accounts.load_account(name)
    directory = accounts.data_dirs(cfg, name)["insights_posts"]
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, post_id + ".ndjson"), "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps({"account": name, "post_id": post_id,
                                     "posted_at": YESTERDAY, "topic": "コーヒー",
                                     "medium": "threads", **row}, ensure_ascii=False) + "\n")


@pytest.fixture
def one(isolated_account_factory, monkeypatch):
    """監視語 1 語・偽の媒体・昨日の実測 2 行を持つ 1 account。"""
    info = isolated_account_factory(ACCOUNT, project=PROJECT, media="threads",
                                    watch_words=["コーヒー"])
    monkeypatch.setattr(accounts, "load_token",
                        lambda cfg: {"access_token": "FAKE", "user_id": "1"})
    fake = Fake(search=[message("s1"), message("s2", username="other")],
                mentions=[message("m1")])
    monkeypatch.setattr(adapters, "make_adapter", lambda *a, **k: fake)
    write_measured(info, ACCOUNT, "p1", [
        {"collected_at": "2026-09-22T10:00:00+09:00", "age_hours": 1.0,
         "metrics": {"views": 10, "likes": 1, "replies": 0, "reposts": 0}},
        {"collected_at": "2026-09-23T10:00:00+09:00", "age_hours": 25.0,
         "metrics": {"views": 40, "likes": 3, "replies": 1, "reposts": 0}}])
    info["fake"] = fake
    return info


def sections(payload):
    return {node["section"]: node for node in payload["sections"]}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def previews(value, found=None):
    """`preview`／`head` の値だけを集める（60 字の約束を確かめるため）。"""
    found = [] if found is None else found
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("preview", "head") and isinstance(child, str):
                found.append(child)
            previews(child, found)
    elif isinstance(value, list):
        for child in value:
            previews(child, found)
    return found


# ------------------------------------------------------------------ 6 段

def test_六段が全部そろう(one):
    payload = morning.build(PROJECT, now=NOW)
    assert [node["section"] for node in payload["sections"]] == [
        "tool", "unanswered", "yesterday", "world", "today", "next_steps"]
    assert payload["accounts"] == [ACCOUNT] and payload["target_kind"] == "project"
    node = sections(payload)
    assert node["tool"]["value"]["version"]
    assert node["unanswered"]["value"]["by_account"][ACCOUNT]["value"]["mentions"]["value"]["n"] == 1
    assert node["yesterday"]["value"]["by_account"][ACCOUNT]["value"]["n"] == 1
    world = node["world"]["value"]["by_account"][ACCOUNT]["value"]
    assert world["by_word"]["コーヒー"]["value"]["n"] == 2
    assert world["by_word"]["コーヒー"]["value"]["distinct_authors"] == 1
    assert len(world["by_word"]["コーヒー"]["value"]["targets"]) == 2
    today = node["today"]["value"]["by_account"][ACCOUNT]["value"]
    assert today["today"]["value"]["n"] == 0 and today["queue"]["value"]["draft"] == 0
    assert all(item["cannot_say"] is None for item in payload["sections"])


def test_account名でも束ねられる(one):
    payload = morning.build(ACCOUNT, now=NOW)
    assert payload["target_kind"] == "account" and payload["accounts"] == [ACCOUNT]


def test_昨日の投稿は分母と24h前との差を出す(one):
    post = sections(morning.build(PROJECT, now=NOW))["yesterday"]["value"]["by_account"][ACCOUNT]["value"]["posts"][0]
    assert post["metrics"]["views"] == 40 and post["observations"] == 2
    assert post["delta_24h"]["views"] == 30 and post["delta_24h"]["likes"] == 2
    assert post["delta_basis"]["from"] == "2026-09-22T10:00:00+09:00"
    assert post["cannot_say"] == []


def test_今日の予定と次の一手(one, monkeypatch):
    from tests.conftest import write_queue_file
    write_queue_file(one["queue_dir"], "today.md",
                     fm_overrides={"account": ACCOUNT, "status": "draft",
                                   "publish_at": "2026-09-23T20:00:00+09:00"})
    payload = morning.build(PROJECT, now=NOW)
    today = sections(payload)["today"]["value"]["by_account"][ACCOUNT]["value"]["today"]["value"]
    assert today["n"] == 1 and today["items"][0]["status"] == "draft"
    steps = sections(payload)["next_steps"]["value"]["steps"]
    # 今日の予定があるので「出す」は出ない。絡む先は 2 件、返す先は言及 1 件。
    assert {step["kind"] for step in steps} == {"reply", "engage"}
    assert sum(step["kind"] == "engage" for step in steps) == 2


def test_予定が無い日は出すが候補に出る(one):
    steps = sections(morning.build(PROJECT, now=NOW))["next_steps"]["value"]["steps"]
    assert {"kind": "post", "account": ACCOUNT} in steps


def test_次の一手は本文を作らない(one):
    """masaru 裁定 3.1.0 §7-1。候補の列挙だけ——`body` という鍵は 1 つも無い。"""
    payload = morning.build(PROJECT, now=NOW)
    steps = sections(payload)["next_steps"]["value"]["steps"]
    assert steps
    for step in steps:
        assert "body" not in step and "text" not in step and "draft" not in step
        assert set(step) <= {"kind", "account", "post_id", "reply_id", "age_hours",
                             "permalink", "word", "replied", "my_history_present"}
    assert "body" not in set(strings(sections(payload)["next_steps"]))


# ------------------------------------------------- 段が落ちても他は出る

@pytest.mark.parametrize("target,reason", [
    ("world", "provider_timeout"), ("world", "scope_missing"), ("yesterday", "unavailable")])
def test_落ちた段はnullと理由になり残りは出る(one, monkeypatch, target, reason):
    if target == "world":
        error = (TimeoutError("timed out") if reason == "provider_timeout"
                 else adapter_base.PermissionMissing("threads_keyword_search", "no"))
        monkeypatch.setattr(adapters, "make_adapter",
                            lambda *a, **k: Fake(mentions=[message("m1")], search_error=error))
    else:
        def broken(*a, **k):
            raise OSError("台帳が読めません")
        monkeypatch.setattr(morning, "_yesterday_posts", broken)
    payload = morning.build(PROJECT, now=NOW)
    node = sections(payload)
    assert node[target]["value"] is None and node[target]["cannot_say"] == reason
    # 残りの 5 段は出ている。
    for name in ("tool", "unanswered", "today", "next_steps"):
        assert node[name]["cannot_say"] is None, name


def test_言及だけが落ちても返信の段は残る(one, monkeypatch):
    monkeypatch.setattr(adapters, "make_adapter", lambda *a, **k: Fake(
        search=[message("s1")], mentions_error=adapter_base.AdapterError("言及の取得: HTTP 500")))
    entry = sections(morning.build(PROJECT, now=NOW))["unanswered"]["value"]["by_account"][ACCOUNT]["value"]
    assert entry["mentions"]["cannot_say"] == "unavailable"
    assert entry["replies"]["value"]["n"] == 0


def test_監視語が無ければ世間の段は飛ばす(isolated_account_factory, monkeypatch):
    isolated_account_factory(ACCOUNT, project=PROJECT, media="threads")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE", "user_id": "1"})
    monkeypatch.setattr(adapters, "make_adapter",
                        lambda *a, **k: pytest.fail("監視語が無いのに検索を叩いた"))
    payload = morning.build(PROJECT, now=NOW)
    assert sections(payload)["world"]["cannot_say"] == "no_watch_words"
    assert "keyword_search" not in json.dumps(payload["calls"])


def test_媒体に口が無ければnot_supported(isolated_account_factory, thth_root, monkeypatch):
    # X には語の検索も言及も無い（予算は足りている＝予算の断りではない）。
    isolated_account_factory("x-one", project=PROJECT, media="x", watch_words=["コーヒー"])
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    budget_x.configure("1.00", by="operator")
    payload = morning.build(PROJECT, now=NOW)
    assert sections(payload)["world"]["cannot_say"] == "not_supported"
    entry = sections(payload)["unanswered"]["value"]["by_account"]["x-one"]["value"]
    assert entry["mentions"]["cannot_say"] == "not_supported"


# ------------------------------------------------------------ X の予算 0

@pytest.fixture
def x_account(isolated_account_factory, thth_root, monkeypatch):
    isolated_account_factory("x-one", project=PROJECT, media="x", watch_words=["コーヒー"])
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    budget_x.configure("0", by="operator")
    return "x-one"


def test_読取予算0なら1から3段はbudget_exhaustedで4段に予算が出る(x_account):
    payload = morning.build(PROJECT, now=NOW)
    node = sections(payload)
    for name in ("unanswered", "yesterday", "world"):
        assert node[name]["value"] is None and node[name]["cannot_say"] == "budget_exhausted", name
    budget = node["today"]["value"]["budget"]["value"]
    assert budget["x_read"]["read_refusal"] == "budget_exhausted"
    assert budget["x_read"]["cap_usd"] == "0" and budget["x_posts"]["monthly"] == 0
    assert payload["calls"] == {}


def test_X以外しか居なければ予算の行は出さない(one):
    budget = sections(morning.build(PROJECT, now=NOW))["today"]["value"]["budget"]
    assert budget["value"] is None and budget["cannot_say"] == "not_supported"


# ------------------------------------------------------------------ 回数

def test_叩いた回数が媒体とendpointごとに出る(one, isolated_account_factory, monkeypatch):
    isolated_account_factory("second-threads", project=PROJECT, media="threads",
                             watch_words=["コーヒー", "自家焙煎"])
    payload = morning.build(PROJECT, now=NOW)
    # account 1 本目 1 語・2 本目 2 語＝検索 3 回、言及は account ごとに 1 回。
    assert payload["calls"] == {"threads": {"mentions": 2, "keyword_search": 3}}


# ------------------------------------------------------------------ 栞

def cursor_path(thth_root, name):
    from pathlib import Path
    return Path(thth_root) / "state" / name / "handoff_cursor.json"


def test_既定で栞が進み_no_markでは進まない(one, thth_root):
    path = cursor_path(thth_root, ACCOUNT)
    assert not path.exists()
    assert morning.build(PROJECT, now=NOW, mark=False)["marked"] == []
    assert not path.exists(), "--no-mark なのに栞が進んだ"
    payload = morning.build(PROJECT, now=NOW)
    assert payload["marked"] == [ACCOUNT] and payload["marked_by"] == "morning"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["by"] == "morning" and saved["snapshot"]["tool_version"]


def test_栞を進めたあとは版の変化が読める(one, thth_root):
    morning.build(PROJECT, now=NOW)
    payload = morning.build(PROJECT, now=NOW)
    assert sections(payload)["tool"]["value"]["changed_since_last_read"] is False
    assert sections(payload)["tool"]["value"]["capabilities"] is None


# ------------------------------------------------------- leak probe・CLI

def test_jsonに絶対パスも本文全文も入らない(one, capsys, monkeypatch, thth_root):
    from tests.conftest import write_queue_file
    write_queue_file(one["queue_dir"], "today.md",
                     fm_overrides={"account": ACCOUNT, "status": "draft",
                                   "publish_at": "2026-09-23T20:00:00+09:00"},
                     body="## threads\n\n" + LONG_TEXT + "\n")
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["morning", PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    leaked = [text for text in strings(payload) if SERVER_PATH.match(text)]
    assert not leaked, leaked
    shown = previews(payload)
    assert shown, "本文の抜粋が 1 つも無い（切り詰めを確かめられない）"
    for text in shown:
        assert len(text) <= morning.PREVIEW_CHARS + 1, text
    assert LONG_TEXT not in json.dumps(payload, ensure_ascii=False)


def test_人向けの1枚も出る(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["morning", PROJECT]) == 0
    out = capsys.readouterr().out
    for title in ("道具", "返していないもの", "昨日の自分", "世間", "予定", "次の一手"):
        assert title in out
    assert "叩いた回数" in out and "栞を進めました" in out


def test_知らない相手はloudに断る(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["morning", "だれでもない", "--json"]) == 2
    captured = capsys.readouterr()
    assert "target_unknown" in captured.err
    assert json.loads(captured.out)["cannot_say"] == ["target_unknown"]


def test_no_markはCLIからも効く(one, capsys, thth_root, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["morning", PROJECT, "--json", "--no-mark"]) == 0
    assert json.loads(capsys.readouterr().out)["marked"] == []
    assert not cursor_path(thth_root, ACCOUNT).exists()


# ------------------------------------------------------------------- MCP

def test_MCPのthth_morningは設計の文言でCLIを呼ぶだけ(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    tool = next(item for item in server.TOOLS if item["name"] == "thth_morning")
    assert tool["description"] == (
        "朝いちばんに呼ぶ。昨日から何があって、今日なにをすればよいかを、"
        "道具が事実だけで 1 枚にする。本文は作らない")
    seen = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(server, "run_cli", lambda args, **kwargs: seen.append(args) or Done())
    server.call_tool("thth_morning", {"target": PROJECT})
    server.call_tool("thth_morning", {"target": PROJECT, "mark": False})
    assert seen == [["morning", PROJECT, "--json"],
                    ["morning", PROJECT, "--no-mark", "--json"]]
    assert server.validate_arguments("thth_morning", {"target": PROJECT, "mark": True})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_morning", {"target": PROJECT, "mark": "yes"})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_morning", {"mark": True})


def test_MCPからも1枚が返る(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    result = server.call_tool("thth_morning", {"target": PROJECT, "mark": False})
    assert not result.get("isError")
    payload = json.loads(result["content"][0]["text"])
    assert [node["section"] for node in payload["sections"]][0] == "tool"
    assert payload["marked"] == []


def test_サーバ型では許された範囲だけ読み栞は進めない(tmp_path, monkeypatch):
    import hashlib
    from datetime import datetime, timedelta, timezone
    from tests.test_mcp import _load_server_module
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
    token = "a" * 43
    value = dict(schema_version=1, root=str(root), credentials=[dict(
        sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revoked=False, accounts={"first": "kopicha"}, scope="user")])
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(path))
    monkeypatch.setenv("THTH_REPORT_TOKEN", token)
    server = _load_server_module()
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert "thth_morning" in names
    result = server.call_tool("thth_morning", {"target": "kopicha"})
    assert not result.get("isError")
    payload = json.loads(result["content"][0]["text"])
    assert payload["accounts"] == ["first"] and payload["marked"] == []
    assert "server_mode_read_only" in payload["cannot_say"]
    assert not (root / "state" / "first" / "handoff_cursor.json").exists()
    denied = server.call_tool("thth_morning", {"target": "other"})
    assert denied["isError"] and denied["content"][0]["text"] == "scope_unavailable"
