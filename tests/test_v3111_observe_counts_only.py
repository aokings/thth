"""3.11.1 件 2: `thth observe … --counts-only`（media-hub 向け・回答
`docs/回答_media-hub_observeを毎朝読む件_2026-09-25.md` の 5）。

出力から他人の情報と本文を落とし、数と状態だけを返す。落とすもの: 本文の先頭
（preview・head・title の本文部分）・permalink・author_key・reply_id・
post_id（数えるのには使うが出さない）・世間の段の「絡みに行く先」・語の候補。
残すもの: 各段の件数・分母・主な指標の中央値と n・予定の本数・確定待ち／保留／
inflight の数と理由コード・報告と広場の新着の数・`cannot_say`・`calls`。JSON に
`"counts_only": true`。text の表示も同じ落とし方。`--json` なしでも効く。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import accounts, cli, jst, morning, sent
from tests.conftest import write_queue_file
from tests.test_v310_morning import ACCOUNT, LONG_TEXT, NOW, PROJECT, one, previews, sections  # noqa: F401

# 落としてよいはずの鍵（残っていたら leak）。`title` は段そのものの見出し
# （`{"section": "tool", "title": "道具", ...}`）としても正規に使われるので、
# ここでは別に見る（`test_報告と広場は題を落とし数は残す` が report/plaza の
# 行の `title` だけを確かめる）。
BANNED_KEYS = {"permalink", "author_key", "reply_id", "post_id", "preview", "head",
              "targets"}


def ago(days):
    return jst.iso(NOW - datetime.timedelta(days=days))


def leaked_keys(value, found=None):
    found = [] if found is None else found
    if isinstance(value, dict):
        for key, child in value.items():
            if key in BANNED_KEYS:
                found.append(key)
            leaked_keys(child, found)
    elif isinstance(value, list):
        for child in value:
            leaked_keys(child, found)
    return found


@pytest.fixture
def rich(one, monkeypatch):
    """`one` に加え、未回答の返信 1 件と時刻超過の下書き（本文つき）を足す。"""
    from pathlib import Path
    sent.write(accounts.state_dir_for(ACCOUNT), post_id="ROOT", text="本文",
              body_hash="hash", sent_at=ago(21))
    directory = Path(accounts.data_dirs(accounts.load_account(ACCOUNT), ACCOUNT)["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    rows = [{"id": "R10", "username": "outside", "text": "返信", "timestamp": ago(10),
            "post_id": "ROOT"},
           {"kind": "fetch", "post_id": "ROOT", "collected_at": jst.iso(NOW)}]
    (directory / "ROOT.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))
    write_queue_file(one["queue_dir"], "late.md",
                     fm_overrides={"account": ACCOUNT, "status": "approved",
                                   "publish_at": "2026-09-23T05:00:00+09:00"},
                     body="## threads\n\n" + LONG_TEXT + "\n")
    return one


# ------------------------------------------------------------------ 意味試験

def test_counts_onlyはJSONに旗が立つ(one):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    assert payload["counts_only"] is True
    payload_off = morning.build(PROJECT, now=NOW, mark=False, counts_only=False)
    assert payload_off["counts_only"] is False


def test_返信と言及の行を落とし件数は残す(rich):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    entry = sections(payload)["unanswered"]["value"]["by_account"][ACCOUNT]["value"]
    replies = entry["replies"]["value"]
    assert "items" not in replies and replies["n"] == 1 and replies["denominator"] == 1
    mentions = entry["mentions"]["value"]
    assert "items" not in mentions and mentions["n"] == 1 and mentions["denominator"] == 1


def test_昨日の投稿は主な指標の中央値とnになる(one):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    value = sections(payload)["yesterday"]["value"]["by_account"][ACCOUNT]["value"]
    assert "posts" not in value and value["n"] == 1 and value["denominator"] == 1
    medians = value["metrics_median"]
    assert medians["views"] == {"median": 40, "n": 1}
    assert medians["likes"] == {"median": 3, "n": 1}


def test_世間の絡みに行く先を落とし件数は残す(one):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    value = sections(payload)["world"]["value"]["by_account"][ACCOUNT]["value"]
    word = value["by_word"]["コーヒー"]["value"]
    assert "targets" not in word
    assert word["n"] == 2 and word["distinct_authors"] == 1


def test_時刻超過は本数だけで本文とfileの中身は落ちない範囲を確かめる(rich):
    """`items`（file・head・publish_at の行）を落とし、`n`（本数）だけ残す。"""
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    overdue = sections(payload)["today"]["value"]["by_account"][ACCOUNT]["value"][
        "overdue_items"]["value"]
    assert "items" not in overdue and overdue["n"] == 1


def test_次の一手は件数だけ残り候補は空にする(rich):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    node = sections(payload)["next_steps"]["value"]
    assert node["steps"] == [] and node["n"] > 0


def test_報告と広場は題を落とし数は残す(one):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    tool = sections(payload)["tool"]["value"]
    mine = tool["project_reports"]
    assert mine["open"] is not None and mine["m"] is not None
    for row in mine.get("closed_this_version") or []:
        assert "title" not in row
    plaza = tool["plaza"]
    assert plaza["project_new"] is not None
    if plaza.get("pick"):
        assert "pick" not in plaza["pick"]  # ネストした候補（本文）を落とす
    if plaza.get("recent_trial"):
        assert "title" not in plaza["recent_trial"]


def test_no_worldと組み合わせても両方効く(one):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True, no_world=True)
    assert payload["counts_only"] is True
    assert sections(payload)["world"]["cannot_say"] == "world_skipped"


# --------------------------------------------------------------- leak probe

def test_leak_probe_permalinkやauthor_keyや本文が1つも無い(rich):
    payload = morning.build(PROJECT, now=NOW, mark=False, counts_only=True)
    leaked = leaked_keys(payload)
    assert leaked == [], leaked
    assert previews(payload) == [], "counts_only で preview/head が残っている"
    dumped = json.dumps(payload, ensure_ascii=False)
    assert LONG_TEXT not in dumped
    assert "https://www.threads.net" not in dumped   # permalink の形そのもの
    assert "a" * 16 not in dumped                     # フィクスチャの author_key


def test_JSONでもtextでも同じ落とし方(rich, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--no-mark", "--counts-only"]) == 0
    out = capsys.readouterr().out
    assert LONG_TEXT not in out
    assert "https://www.threads.net" not in out
    assert "a" * 16 not in out


# ------------------------------------------------------------------------ CLI

def test_CLIからcounts_onlyが効く(one, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--json", "--no-mark", "--counts-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts_only"] is True
    assert "items" not in sections(payload)["unanswered"]["value"]["by_account"][
        ACCOUNT]["value"]["mentions"]["value"]


def test_人向けの1枚もクラッシュしない(rich, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--no-mark", "--counts-only"]) == 0
    out = capsys.readouterr().out
    for title in ("道具", "返していないもの", "昨日の自分", "世間", "予定", "次の一手"):
        assert title in out


# ------------------------------------------------------------------------ MCP

def test_MCPのthth_observeにcounts_onlyを足せる(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    seen = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(server, "run_cli", lambda args, **kwargs: seen.append(args) or Done())
    server.call_tool("thth_observe", {"target": PROJECT, "counts_only": True})
    assert seen == [["observe", PROJECT, "--counts-only", "--json"]]
    assert server.validate_arguments("thth_observe", {"target": PROJECT, "counts_only": True})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_observe", {"target": PROJECT, "counts_only": "yes"})


def test_MCPから実際にcounts_onlyが返る(one, monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    server = _load_server_module()
    result = server.call_tool("thth_observe", {"target": PROJECT, "mark": False, "counts_only": True})
    assert not result.get("isError")
    payload = json.loads(result["content"][0]["text"])
    assert payload["counts_only"] is True

