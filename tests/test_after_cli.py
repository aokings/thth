"""`thth after` / `after_you_posted`（設計「自分の泉」§2.2・§4・発注 T0-2）。

確かめるもの:
  - 絡みの台帳 3 本 ＋ 実測の台帳（24h の刻みあり 2 本・無し 1 本）を fixture で
    作ると、`n=3`・`covered` 2 本・無い 1 本は `null`/`false`・`cannot_say` に
    「未採取: 1 本」・`min_n=5`（既定）なら中央値が出ない。
  - `--reply-to` で 1 本に絞れる。
  - 出力に禁止鍵（本文・username 等）が無い。
  - MCP `after_you_posted` を呼ぶと CLI と同じ `engagements` の中身が返る。
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os

import pytest

from thth import accounts as accounts_mod
from thth import after_cli as after_cli_mod
from thth import cli as cli_mod
from thth import engagements as engagements_mod
from thth import jst
from thth import topics as topics_mod

MCP_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "mcp", "server.py")


def _load_server_module():
    spec = importlib.util.spec_from_file_location("thth_mcp_server", MCP_SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _insight_path(account, post_id: str) -> str:
    return os.path.join(account["repo_dir"], "data", "sns", "insights", "posts",
                        f"{post_id}.ndjson")


def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _seed(account, *, min_n_ready=False):
    """絡みの台帳 3 本（P1・P2・P3）＋実測（P1・P2 は 24h の刻みあり、P3 は無し）。

    `min_n_ready=True` なら、`likes_24h` の中央値が出るところまで行を増やす
    （下の別テストで使う）。
    """
    cfg = accounts_mod.load_account(account["name"])
    rows = [
        {"schema": engagements_mod.SCHEMA, "post_id": "P1", "reply_to": "R1",
         "root_post": None, "author_key": None, "account": account["name"],
         "medium": "threads", "topic": None, "form": None, "hour_band": "朝",
         "posted_at": "2026-09-01T08:00:00+09:00", "found_by": None},
        {"schema": engagements_mod.SCHEMA, "post_id": "P2", "reply_to": "R2",
         "root_post": None, "author_key": None, "account": account["name"],
         "medium": "threads", "topic": None, "form": None, "hour_band": "夕",
         "posted_at": "2026-09-02T18:00:00+09:00", "found_by": None},
        {"schema": engagements_mod.SCHEMA, "post_id": "P3", "reply_to": "R3",
         "root_post": None, "author_key": None, "account": account["name"],
         "medium": "threads", "topic": None, "form": None, "hour_band": "夕",
         "posted_at": "2026-09-03T18:00:00+09:00", "found_by": None},
    ]
    for row in rows:
        engagements_mod.append(cfg, account["name"], row)

    _write_ndjson(_insight_path(account, "P1"), [
        {"post_id": "P1", "account": account["name"], "topic": None,
         "posted_at": "2026-09-01T08:00:00+09:00",
         "collected_at": "2026-09-02T08:10:00+09:00", "age_hours": 24.16,
         "marks": [24], "metrics": {"views": 40, "likes": 3, "replies": 1}},
    ])
    _write_ndjson(_insight_path(account, "P2"), [
        {"post_id": "P2", "account": account["name"], "topic": None,
         "posted_at": "2026-09-02T18:00:00+09:00",
         "collected_at": "2026-09-03T18:05:00+09:00", "age_hours": 24.08,
         "marks": [24], "metrics": {"views": 20, "likes": 0, "replies": 0}},
    ])
    # P3: 実測の台帳が無い（採ったのに刻みが 24 でない・とも読めるが、ここでは
    # 最も単純な「まだ 1 度も採取されていない」を試す）。
    return cfg


def _seed_measured(account, post_id: str, *, posted_at="2026-09-10T08:00:00+09:00",
                   topic="コーヒー", reply_to_marker=None, include_reply_to=True,
                   source="queue", marks=(24,), metrics=None, account_name=None):
    """所有の裏付けがある実測 1 行。reply_to の鍵の有無も作り分ける。"""
    row = {
        "post_id": post_id, "account": account_name or account["name"],
        "topic": topic, "source": source, "posted_at": posted_at,
        "collected_at": "2026-09-11T08:05:00+09:00", "age_hours": 24.08,
        "marks": list(marks), "metrics": {"views": 100} if metrics is None else metrics,
    }
    if include_reply_to:
        row["reply_to"] = reply_to_marker
    _write_ndjson(_insight_path(account, post_id), [row])


def _seed_engagement(account, post_id: str, *, topic="コーヒー",
                     posted_at="2026-09-10T08:00:00+09:00"):
    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"], {
        "schema": engagements_mod.SCHEMA, "post_id": post_id,
        "reply_to": f"parent-{post_id}", "root_post": f"root-{post_id}",
        "author_key": None, "account": account["name"], "medium": "threads",
        "topic": topic, "form": "問い→理由", "hour_band": "朝",
        "posted_at": posted_at, "found_by": "manual",
    })


# ===================================================== n / covered / cannot_say


def test_n3_covered2_未採取1本_min_nで中央値なし(isolated_account_factory):
    account = isolated_account_factory()
    _seed(account)

    result = after_cli_mod.answer(account["name"])
    eng = result["engagements"]
    assert eng["n"] == 3, eng
    by_covered = {b["post_id"]: b["covered"] for b in eng["by_branch"]}
    assert by_covered == {"P1": True, "P2": True, "P3": False}, by_covered

    p3 = next(b for b in eng["by_branch"] if b["post_id"] == "P3")
    assert p3["views_24h"] is None and p3["likes_24h"] is None
    assert p3["replies_back_24h"] is None

    assert any("未採取: 1 本" in c for c in result["cannot_say"]), result["cannot_say"]

    # 既定の min_n=5 では、covered が 2 本しか無いので中央値は出ない。
    assert eng["likes_24h"]["median"] is None and eng["likes_24h"]["n"] == 2
    assert eng["views_24h"]["median"] is None and eng["views_24h"]["n"] == 2


def test_covered行のviews_likesはmeasuredの24h行から採る(isolated_account_factory):
    account = isolated_account_factory()
    _seed(account)
    result = after_cli_mod.answer(account["name"])
    by_post = {b["post_id"]: b for b in result["engagements"]["by_branch"]}
    assert by_post["P1"]["views_24h"] == 40
    assert by_post["P1"]["likes_24h"] == 3
    assert by_post["P1"]["replies_back_24h"] == 1
    assert by_post["P2"]["views_24h"] == 20
    assert by_post["P2"]["likes_24h"] == 0


def test_reacted_の数え方(isolated_account_factory):
    account = isolated_account_factory()
    _seed(account)
    result = after_cli_mod.answer(account["name"])
    # P1: likes=3 → 反応あり。P2: likes=0・replies=0 → 反応なし。P3: null → 反応なし。
    assert result["engagements"]["reacted"] == 1, result["engagements"]


# ===================================================== 自分の根投稿 / 24h views


def test_postsは根だけを数え返信と帰属不明を混ぜない(isolated_account_factory):
    account = isolated_account_factory()
    _seed_measured(account, "ROOT", metrics={"views": 120})
    _seed_measured(account, "DIRECT-REPLY", reply_to_marker="OTHER")
    _seed_measured(account, "OLD-UNKNOWN", include_reply_to=False)
    _seed_measured(account, "SENT-UNKNOWN", source="sent")
    _seed_measured(account, "LEDGER-REPLY")
    _seed_engagement(account, "LEDGER-REPLY")

    result = after_cli_mod.answer(account["name"], min_n=1,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    posts = result["posts"]
    assert posts["n"] == 1
    assert posts["views_24h"] == {"median": 120, "n": 1}
    assert [p["post_id"] for p in posts["by_post"]] == ["ROOT"]
    assert any("根投稿か返信か判らず" in line for line in result["cannot_say"])


def test_postsの欠測はnullでmin_nとcannot_sayを伴う(isolated_account_factory):
    account = isolated_account_factory()
    _seed_measured(account, "OK", metrics={"views": 80})
    _seed_measured(account, "NO-MARK", marks=(6,), metrics={"views": 20})
    _seed_measured(account, "NO-VIEWS", metrics={"likes": 2})

    result = after_cli_mod.answer(account["name"], min_n=2,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    posts = result["posts"]
    assert posts["n"] == 3
    assert posts["views_24h"] == {"median": None, "n": 1}
    by_post = {p["post_id"]: p for p in posts["by_post"]}
    assert by_post["NO-MARK"]["views_24h"] is None
    assert by_post["NO-MARK"]["covered"] is False
    assert by_post["NO-VIEWS"]["views_24h"] is None
    assert by_post["NO-VIEWS"]["covered"] is True
    assert any("24h の刻みが未採取: 1 本" in line for line in result["cannot_say"])
    assert any("24h views が欠測: 1 本" in line for line in result["cannot_say"])
    assert any("views 中央値: n=1（2 未満）" in line for line in result["cannot_say"])


def test_postsの期間は下端を含み未来を含まない(isolated_account_factory):
    account = isolated_account_factory()
    _seed_measured(account, "AT-CUTOFF", posted_at="2026-09-07T12:00:00+09:00")
    _seed_measured(account, "TOO-OLD", posted_at="2026-09-07T11:59:59+09:00")
    _seed_measured(account, "FUTURE", posted_at="2026-09-17T12:00:01+09:00")
    result = after_cli_mod.answer(account["name"], window_days=10, min_n=1,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    assert [p["post_id"] for p in result["posts"]["by_post"]] == ["AT-CUTOFF"]


def test_reply_to条件では根投稿節を対象外にする(isolated_account_factory):
    account = isolated_account_factory()
    _seed_measured(account, "ROOT")
    _seed_engagement(account, "REPLY")
    result = after_cli_mod.answer(account["name"], reply_to="parent-REPLY", min_n=1,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    assert result["posts"]["n"] == 0
    assert result["engagements"]["n"] == 1
    assert any("返信だけの条件" in line for line in result["cannot_say"])


# ===================================================== kind / project


def test_kindはtopic分類でformには読み替えない(isolated_account_factory, monkeypatch):
    account = isolated_account_factory()
    _seed_measured(account, "ACTION", topic="中学受験")
    _seed_measured(account, "NOUN", topic="コーヒー")
    _seed_engagement(account, "ENG-ACTION", topic="中学受験")
    _seed_engagement(account, "ENG-NOUN", topic="コーヒー")
    calls = []

    def fake_kind(topic, account_name):
        calls.append((topic, account_name))
        return {"中学受験": "行動", "コーヒー": "一般名詞"}.get(topic)

    monkeypatch.setattr(topics_mod, "kind_of", fake_kind)
    result = after_cli_mod.answer(account["name"], kind="行動", min_n=1,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    assert [p["post_id"] for p in result["posts"]["by_post"]] == ["ACTION"]
    assert [b["post_id"] for b in result["engagements"]["by_branch"]] == ["ENG-ACTION"]
    assert calls and all(call[1] == account["name"] for call in calls)
    assert any("現在の topic shelf" in line for line in result["cannot_say"])


def test_未知kindはloud_reject(isolated_account_factory):
    account = isolated_account_factory()
    try:
        after_cli_mod.answer(account["name"], kind="投稿構成")
    except after_cli_mod.AfterError as exc:
        assert "知らない型" in str(exc)
    else:
        raise AssertionError("未知 kind を受け取ってしまった")


def test_projectは同じ媒体の複数accountも節を分ける(isolated_account_factory):
    a1 = isolated_account_factory("a1-threads", project="same", handle="a1")
    a2 = isolated_account_factory("a2-threads", project="same", handle="a2")
    _seed_measured(a1, "A1", account_name=a1["name"], metrics={"views": 10})
    _seed_measured(a2, "A2", account_name=a2["name"], metrics={"views": 90})
    _seed_engagement(a1, "E1")
    _seed_engagement(a2, "E2")

    result = after_cli_mod.answer(project="same", min_n=1,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    assert set(result["by_account"]) == {a1["name"], a2["name"]}
    assert result["by_account"][a1["name"]]["posts"]["views_24h"]["median"] == 10
    assert result["by_account"][a2["name"]]["posts"]["views_24h"]["median"] == 90
    assert "posts" not in result and "engagements" not in result
    assert result["by_account"][a1["name"]]["engagements"]["n"] == 1
    assert result["by_account"][a2["name"]]["engagements"]["n"] == 1


def test_account_projectの排他と不正project(isolated_account_factory, capsys):
    account = isolated_account_factory()
    rc = cli_mod.main(["after", account["name"], "--project", "nigamilab", "--json"])
    assert rc == 2
    assert "同時に指定" in capsys.readouterr().err
    rc = cli_mod.main(["after", "--project", "missing", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["by_account"] == {}
    assert any("一致する account がありません" in line for line in payload["cannot_say"])
    rc = cli_mod.main(["after", "--project", "missing"])
    human = capsys.readouterr()
    assert rc == 0
    assert "after  project missing" in human.out
    assert "一致する account がありません" in human.out


def test_壊れた台帳を0件に化けさせない(isolated_account_factory):
    account = isolated_account_factory()
    bad_measured = _insight_path(account, "BROKEN")
    os.makedirs(os.path.dirname(bad_measured), exist_ok=True)
    with open(bad_measured, "w", encoding="utf-8") as f:
        f.write("[]\n")
    bad_engagement = os.path.join(account["repo_dir"], "data", "sns", "engagements",
                                  "broken.ndjson")
    os.makedirs(os.path.dirname(bad_engagement), exist_ok=True)
    with open(bad_engagement, "w", encoding="utf-8") as f:
        f.write("not-json\n")
    result = after_cli_mod.answer(account["name"])
    assert any("読めない実測台帳: 1" in line for line in result["cannot_say"])
    assert any("読めない絡み台帳: 1" in line for line in result["cannot_say"])


def test_非文字列topicと非有限viewsは数に化けない(isolated_account_factory):
    account = isolated_account_factory()
    _seed_measured(account, "BAD-TOPIC", topic=123)
    _seed_measured(account, "NAN-VIEWS", metrics={"views": float("nan")})
    _seed_measured(account, "NEG-VIEWS", metrics={"views": -1})
    result = after_cli_mod.answer(account["name"], min_n=1,
                                  now=jst.parse("2026-09-17T12:00:00+09:00"))
    assert result["posts"]["n"] == 2
    assert result["posts"]["views_24h"] == {"median": None, "n": 0}
    assert all(p["views_24h"] is None for p in result["posts"]["by_post"])
    assert any("topic が文字列でなく" in line for line in result["cannot_say"])
    assert any("24h views が欠測: 2 本" in line for line in result["cannot_say"])


# ===================================================== --reply-to で絞る


def test_reply_toで1本に絞れる(isolated_account_factory):
    account = isolated_account_factory()
    _seed(account)
    result = after_cli_mod.answer(account["name"], reply_to="R1")
    assert result["engagements"]["n"] == 1
    assert result["engagements"]["by_branch"][0]["post_id"] == "P1"


def test_cli_reply_toで1本に絞れる(isolated_account_factory, capsys):
    account = isolated_account_factory()
    _seed(account)
    rc = cli_mod.main(["after", account["name"], "--reply-to", "R1", "--json"])
    out = capsys.readouterr().out
    assert rc == 0, out
    data = json.loads(out)
    assert data["engagements"]["n"] == 1
    assert data["engagements"]["by_branch"][0]["post_id"] == "P1"


# ===================================================== 禁止鍵が出ない


def _collect_dict_keys(obj, out: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _collect_dict_keys(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_dict_keys(item, out)


def test_出力に禁止鍵が無い(isolated_account_factory):
    account = isolated_account_factory()
    _seed(account)
    result = after_cli_mod.answer(account["name"])
    keys: set = set()
    _collect_dict_keys(result, keys)
    hit = keys & engagements_mod.FORBIDDEN_KEYS
    assert not hit, f"禁止鍵が出力に混ざっている: {hit}"
    # 文字列としても本文・仮の username が混ざっていないこと。
    text = json.dumps(result, ensure_ascii=False)
    assert "username" not in text and "\"text\"" not in text and "\"body\"" not in text


# ===================================================== MCP


def test_mcp_after_you_postedはcliと同じengagementsを返す(isolated_account_factory):
    account = isolated_account_factory()
    _seed(account)

    server = _load_server_module()
    result = server.call_tool("after_you_posted", {"account": account["name"]})
    assert result["isError"] is False, result
    mcp_payload = json.loads(result["content"][0]["text"])

    cli_payload = after_cli_mod.answer(account["name"], now=jst.parse(mcp_payload["provenance"]["updated"]))

    assert mcp_payload["engagements"] == cli_payload["engagements"]
    assert mcp_payload["comparable"] == cli_payload["comparable"]


def test_mcp_ツール一覧にafter_you_postedがある(isolated_account_factory):
    server = _load_server_module()
    names = {t["name"] for t in server.TOOLS}
    assert "after_you_posted" in names
    tool = next(t for t in server.TOOLS if t["name"] == "after_you_posted")
    assert tool["description"] == (
        "出したあとに呼ぶ。この語・この型・この枝で、自分の投稿と返信が"
        "どう受け取られたかを、件数と期間つきで返す")
    assert "project" in tool["inputSchema"]["properties"]


def test_mcp_afterのaccount_projectは排他的OR(isolated_account_factory):
    account = isolated_account_factory()
    server = _load_server_module()
    with pytest.raises(server.ToolInputError, match="account か project"):
        server.validate_arguments("after_you_posted", {})
    with pytest.raises(server.ToolInputError, match="同時に指定"):
        server.validate_arguments("after_you_posted", {
            "account": account["name"], "project": "nigamilab"})


def test_mcp_after_projectはCLIと同じby_accountを返す(isolated_account_factory):
    a1 = isolated_account_factory("mcp-a1", project="mcp-project", handle="a1")
    a2 = isolated_account_factory("mcp-a2", project="mcp-project", handle="a2")
    _seed_measured(a1, "MCP-A1", account_name=a1["name"], metrics={"views": 11})
    _seed_measured(a2, "MCP-A2", account_name=a2["name"], metrics={"views": 22})
    server = _load_server_module()
    result = server.call_tool("after_you_posted", {
        "project": "mcp-project", "min_n": 1, "window_days": 30})
    assert result["isError"] is False, result
    payload = json.loads(result["content"][0]["text"])
    assert set(payload["by_account"]) == {a1["name"], a2["name"]}
    assert "posts" not in payload and "engagements" not in payload
