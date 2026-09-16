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

from thth import accounts as accounts_mod
from thth import after_cli as after_cli_mod
from thth import cli as cli_mod
from thth import engagements as engagements_mod
from thth import jst

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
