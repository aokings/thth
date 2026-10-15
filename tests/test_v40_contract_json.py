"""4.0 の柱 1（計画_4.0_2026-10-15 §3.3）のうち「足すだけ」の部分。

1. `observe --json` に `schema: "thth.observe/1"` と、節を名前で引く `sections_by_name` を足した。
   hub（media-hub `hub/snsobs.py`）と h2d（`config/sensors/thth-kanto-*.json`）が今読んでいる
   位置・鍵を**全部**ここに書き写し、(a) 古い並び（`sections[1]`・`sections[4]`）でも今までどおり
   読めること、(b) 同じ値が名前（`sections_by_name.<名前>`）でも引けること、を縛る。
   `board --json` にも `schema: "thth.board/1"`（hub は `accounts[]` を鍵の名前で読む）。
2. `thth events`（`thth.events/1`）: 記録から出来事を並べるだけ。他人の本文・username は入れない。
3. `thth summary`（`thth.summary/1`）: 数の要約。読むだけ。
4. MCP（手元・サーバ型）と thth.me の道に同じ口。
"""
from __future__ import annotations

import datetime
import json
import os
import re

import pytest

from thth import accounts, cli, events_cli, guard, inflight, jst, morning, publish_hold, report, sent, summary_cli
from tests.test_v3111_observe_counts_only import rich  # noqa: F401
from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one  # noqa: F401
from tests.test_v212_server_writes import env  # noqa: F401
from tests.test_v3140_remote_cli import ACCOUNT as RACCOUNT, home, worker  # noqa: F401

# ------------------------------------------------------------------ 読み手の道（写し）
# h2d の道の書き方（`h2d/sensor_kinds.py` の compile_path と同じ文法の小さな写し）。
_SEG = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)((?:\[-?\d+\])*)$")


def dig(data, path):
    for seg in path.split("."):
        m = _SEG.fullmatch(seg)
        assert m, seg
        assert isinstance(data, dict) and m.group(1) in data, f"見つかりません: {m.group(1)}（{path}）"
        data = data[m.group(1)]
        for idx in re.findall(r"\[(-?\d+)\]", m.group(2)):
            data = data[int(idx)]
    return data


# h2d が今読んでいる道（2026-10-15 の `h2d/config/sensors/thth-kanto-*.json`）と、名前の道。
# probe は `thth observe asmon-kanto-threads --json --no-mark --counts-only --no-world`。
H2D_READS = {
    "thth-kanto-inflight": ("sections[4].value.by_account.{a}.value.inflight.present",
                            "sections_by_name.today.value.by_account.{a}.value.inflight.present"),
    "thth-kanto-overdue": ("sections[4].value.by_account.{a}.value.overdue_items.value.n",
                           "sections_by_name.today.value.by_account.{a}.value.overdue_items.value.n"),
    "thth-kanto-replies": ("sections[1].value.by_account.{a}.value.replies.value.n",
                           "sections_by_name.unanswered.value.by_account.{a}.value.replies.value.n"),
}

# hub（media-hub `hub/snsobs.py` の summarize/collect）が読む鍵。今は節を `s["section"]` の名前で
# 引いている。名前の道（`sections_by_name`）で同じ値が引けることを縛る。
HUB_OBSERVE_READS = (
    "sections_by_name.yesterday.value.by_account.{a}.value.n",
    "sections_by_name.yesterday.value.by_account.{a}.value.metrics_median.views.median",
    "sections_by_name.yesterday.value.by_account.{a}.value.window",
    "sections_by_name.unanswered.value.by_account.{a}.value.replies.value.n",
    "sections_by_name.unanswered.value.by_account.{a}.value.replies.value.collection_stale_hours",
    "sections_by_name.unanswered.value.by_account.{a}.value.replies.value.last_fetch_hours",
    "sections_by_name.unanswered.value.by_account.{a}.value.mentions.value.n",
    "sections_by_name.today.value.by_account.{a}.value.today.value.n",
    "sections_by_name.today.value.by_account.{a}.value.overdue_items.value.n",
    "sections_by_name.today.value.by_account.{a}.value.waiting_items.value.n",
)
HUB_OBSERVE_TOP = ("counts_only", "calls", "generated_at")
# hub（`hub/hub.py` の check_sns・`scripts/health_check.py`）が `thth board --json` の `accounts[]` から読む鍵。
HUB_BOARD_KEYS = ("account", "project", "last_post_at", "approved_waiting", "held_count",
                  "held_reason_code", "inflight", "inflight_reason_code", "awaiting_confirm_count",
                  "notification_configured")


def _observe(capsys, monkeypatch):
    """h2d・hub と同じ呼び方（CLI・counts-only・no-world・no-mark）。"""
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["observe", PROJECT, "--json", "--no-mark", "--counts-only", "--no-world"]) == 0
    return json.loads(capsys.readouterr().out)


# ------------------------------------------------------------------ 1. observe の約束

def test_observeに版と名前の節が付き並びはそのまま(rich, capsys, monkeypatch):
    payload = _observe(capsys, monkeypatch)
    assert payload["schema"] == "thth.observe/1" == morning.OBSERVE_SCHEMA
    assert payload["schema_version"] == 1     # 古い鍵も残す
    # 並び（4.0 で外す）は形も順も今までどおり。
    assert [s["section"] for s in payload["sections"]] == list(morning.SECTION_NAMES)
    assert list(payload["sections_by_name"]) == list(morning.SECTION_NAMES)
    for section in payload["sections"]:
        assert payload["sections_by_name"][section["section"]] == section


def test_h2dが読む位置は古い並びでも名前でも同じ値(rich, capsys, monkeypatch):
    payload = _observe(capsys, monkeypatch)
    for sensor, (old, new) in H2D_READS.items():
        old_value = dig(payload, old.format(a=ACCOUNT))
        assert dig(payload, new.format(a=ACCOUNT)) == old_value, sensor
    # 値そのもの（fixture: 未回答 1・時刻超過 1・inflight 無し）。
    assert dig(payload, H2D_READS["thth-kanto-replies"][1].format(a=ACCOUNT)) == 1
    assert dig(payload, H2D_READS["thth-kanto-overdue"][1].format(a=ACCOUNT)) == 1
    assert dig(payload, H2D_READS["thth-kanto-inflight"][1].format(a=ACCOUNT)) is False


def test_hubが読む鍵は名前の道で全部引ける(rich, capsys, monkeypatch):
    payload = _observe(capsys, monkeypatch)
    for path in HUB_OBSERVE_READS:
        dig(payload, path.format(a=ACCOUNT))
    for key in HUB_OBSERVE_TOP:
        assert key in payload
    assert payload["counts_only"] is True


def test_boardに版が付きhubが読む鍵は残る(isolated_account_factory, capsys):
    isolated_account_factory(ACCOUNT, project=PROJECT, media="threads")
    assert cli.main(["board", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "thth.board/1" == report.BOARD_SCHEMA
    rows = [row for row in payload["accounts"] if row.get("account") == ACCOUNT]
    assert rows, payload["accounts"]
    for key in HUB_BOARD_KEYS:
        assert key in rows[0], key


# ------------------------------------------------------------------ 2. events

def _ago(**kw):
    return jst.iso(NOW - datetime.timedelta(**kw))


@pytest.fixture
def lived(one):
    """公開 2 本（窓の中と外）・取り下げ・他人の返信・言及・止まり 3 つを持つ口座。"""
    state = accounts.state_dir_for(ACCOUNT)
    sent.write(state, post_id="P-NEW", text="新しい投稿の題です\n本文の続き", body_hash="h", sent_at=_ago(hours=2))
    sent.write(state, post_id="P-OLD", text="古い投稿", body_hash="h", sent_at=_ago(days=10))
    sent.mark_retracted(state, "P-OLD", retracted_at=_ago(hours=1), retracted_by="masaru",
                        retract_reason="誤字")
    cfg = accounts.load_account(ACCOUNT)
    dirs = accounts.data_dirs(cfg, ACCOUNT)
    os.makedirs(dirs["replies"], exist_ok=True)
    with open(os.path.join(dirs["replies"], "P-NEW.ndjson"), "w", encoding="utf-8") as f:
        for row in ({"id": "R1", "username": "someone_else", "text": "他人の返信の本文ひみつ",
                     "timestamp": _ago(hours=1), "post_id": "P-NEW", "collected_at": _ago(minutes=30)},
                    {"id": "R0", "username": "someone_else", "text": "古い返信",
                     "timestamp": _ago(days=5), "post_id": "P-NEW", "collected_at": _ago(days=5)}):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.makedirs(dirs["inbox"], exist_ok=True)
    with open(os.path.join(dirs["inbox"], "2026-09.ndjson"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"message_id": "M1", "username": "mentioner", "text": "言及の本文ひみつ",
                            "timestamp": _ago(hours=3), "kind": "inbox", "mention_kind": "mention",
                            "collected_at": _ago(hours=3)}, ensure_ascii=False) + "\n")
    guard.stop(ACCOUNT, "owner", now=NOW - datetime.timedelta(days=3))
    publish_hold.hold(ACCOUNT, publish_hold.X_NOT_PERMITTED, file="queue/a.md", now=NOW - datetime.timedelta(days=2))
    inflight.write(state, file="queue/b.md", started=_ago(days=4))
    return one


def test_eventsは記録から並べ他人の本文を入れない(lived):
    payload = events_cli.build(ACCOUNT, since="24h", now=NOW)
    assert payload["schema"] == "thth.events/1"
    kinds = [row["kind"] for row in payload["events"]]
    assert sorted(kinds) == sorted(["published", "retracted", "reply_received", "mention_received",
                                    "account_stopped", "publish_held", "inflight_open"])
    for row in payload["events"]:
        assert {"schema", "kind", "account", "at", "occurred_at", "recorded_at", "ref", "summary",
                "event_id"} <= set(row)
        assert events_cli.EVENT_ID.fullmatch(row["event_id"])
        assert row["schema"] == "thth.events/1" and row["account"] == ACCOUNT
        assert row["at"] is None or jst.parse(row["at"]).utcoffset() == datetime.timedelta(hours=9)
    dumped = json.dumps(payload, ensure_ascii=False)
    for secret in ("他人の返信の本文ひみつ", "言及の本文ひみつ", "someone_else", "mentioner"):
        assert secret not in dumped
    by = {row["kind"]: row for row in payload["events"]}
    assert by["published"]["ref"] == "P-NEW" and "新しい投稿の題です" in by["published"]["summary"]
    assert by["reply_received"]["ref"] == "R1" and by["reply_received"]["post_id"] == "P-NEW"
    assert "新しい投稿の題です" in by["reply_received"]["summary"]
    assert by["mention_received"]["ref"] == "M1"
    # 止まっている状態は窓の前に始まっていても出す。
    for kind in ("account_stopped", "publish_held", "inflight_open"):
        assert by[kind]["ongoing"] is True
    # 時刻順（時刻の無いものは後ろ）。
    # 記録した時刻の順。返信は起きた時刻（1 時間前）と記録した時刻（30 分前）を分けて持つ。
    ats = [jst.parse(row["recorded_at"]) for row in payload["events"] if row["recorded_at"]]
    assert ats == sorted(ats)
    assert by["reply_received"]["occurred_at"] == _ago(hours=1)
    assert by["reply_received"]["recorded_at"] == _ago(minutes=30)
    # 同じ記録から何度読んでも同じ event_id。
    again = events_cli.build(ACCOUNT, since="24h", now=NOW + datetime.timedelta(minutes=5))
    assert [r["event_id"] for r in again["events"]] == [r["event_id"] for r in payload["events"]]


def test_eventsのsinceで窓を切る(lived):
    payload = events_cli.build(ACCOUNT, since=_ago(minutes=40), now=NOW)   # ISO の時刻でも切れる
    kinds = {row["kind"] for row in payload["events"]}
    # 返信は書かれたのが 1 時間前でも、採ったのが 30 分前なら窓の中。
    assert "reply_received" in kinds and "published" not in kinds and "mention_received" not in kinds
    assert {"account_stopped", "publish_held", "inflight_open"} <= kinds
    wide = events_cli.build(PROJECT, since="7d", now=NOW)
    assert wide["target_kind"] == "project"
    assert sum(1 for row in wide["events"] if row["kind"] == "reply_received") == 2


def test_eventsのCLIとlimitと断り(lived, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["events", ACCOUNT, "--since", "24h", "--limit", "2", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n"] == 2 and payload["n_total"] == 7
    assert cli.main(["events", ACCOUNT, "--since", "きのう", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["invalid_since"]
    assert cli.main(["events", "no-such", "--json"]) == 2
    capsys.readouterr()
    assert cli.main(["events", ACCOUNT]) == 0
    assert "published" in capsys.readouterr().out


# ------------------------------------------------------------------ 3. summary

def test_summaryは数だけを口座ごとに(lived, capsys, monkeypatch):
    cfg = accounts.load_account(ACCOUNT)
    daily = accounts.data_dirs(cfg, ACCOUNT)["insights_account"]
    os.makedirs(daily, exist_ok=True)
    with open(os.path.join(daily, f"{ACCOUNT}-2026-09.ndjson"), "w", encoding="utf-8") as f:
        for date, n in (("2026-09-21", 10), ("2026-09-22", 13)):
            f.write(json.dumps({"account": ACCOUNT, "date": date, "collected_at": f"{date}T23:00:00+09:00",
                                "metrics": {"followers_count": n}}) + "\n")
    payload = summary_cli.build(PROJECT, now=NOW)
    assert payload["schema"] == "thth.summary/1"
    row = payload["by_account"][ACCOUNT]
    assert row["published"]["24h"] == {"published": 1, "retracted": 1}
    assert row["published"]["7d"]["published"] == 1
    # fixture の実測（昨日 09:00 に出した p1・最新 views 40・likes 3・replies 1）。
    assert row["reactions"]["24h"]["sums"]["views"] == {"sum": 40, "n": 1}
    assert row["reactions"]["24h"]["reactions_total"] == 3 + 1 + 0
    assert row["unanswered_replies"]["n"] >= 1
    assert row["followers"] == {"latest": 13, "date": "2026-09-22", "previous": 10,
                                "previous_date": "2026-09-21", "delta": 3}
    assert row["stopped"]["guard"] == "owner" and row["stopped"]["inflight"] is True
    assert row["stopped"]["any"] is True
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "他人の返信の本文ひみつ" not in dumped and "someone_else" not in dumped
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["summary", ACCOUNT, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["by_account"][ACCOUNT]["followers"]["delta"] == 3
    assert cli.main(["summary", ACCOUNT]) == 0
    assert "フォロワー: 13" in capsys.readouterr().out


# ------------------------------------------------------------------ 4. MCP（手元）

def test_手元のMCPにeventsとsummaryがあり読むだけのCLIを呼ぶ(monkeypatch):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    names = {tool["name"] for tool in server.TOOLS}
    assert {"thth_events", "thth_summary"} <= names
    seen = []

    class Proc:
        returncode, stdout, stderr = 0, "{}", ""

    monkeypatch.setattr(server, "server_mode", lambda: False)
    monkeypatch.setattr(server, "run_cli", lambda args, *a, **k: seen.append(args) or Proc())
    server.call_tool("thth_events", server.validate_arguments(
        "thth_events", {"project": PROJECT, "since": "7d", "limit": 5}))
    server.call_tool("thth_summary", server.validate_arguments("thth_summary", {"account": ACCOUNT}))
    assert seen == [["events", PROJECT, "--since", "7d", "--limit", "5", "--json"],
                    ["summary", ACCOUNT, "--json"]]
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_summary", {})


# ------------------------------------------------------------------ 5. サーバ型 MCP と thth.me の道

def test_サーバ型の読む口にeventsとsummary_範囲は鍵の口座だけ(env):
    from thth.report_service import ReportServiceError, execute_report
    from tests.test_mcp import _load_server_module
    sent.write(accounts.state_dir_for("alpha"), post_id="A1", text="アルファの投稿", body_hash="h",
               sent_at=jst.iso(jst.now_jst() - datetime.timedelta(hours=1)))
    got = execute_report(env["context"], {"operation": "events", "account": "alpha", "since": "24h"})
    assert got["schema"] == "thth.events/1" and got["accounts"] == ["alpha"]
    assert [row["ref"] for row in got["events"] if row["kind"] == "published"] == ["A1"]
    got = execute_report(env["context"], {"operation": "summary", "account": "alpha"})
    assert got["schema"] == "thth.summary/1"
    assert got["by_account"]["alpha"]["published"]["24h"]["published"] == 1
    for request, reason in (({"operation": "events", "account": "beta"}, "scope_unavailable"),
                            ({"operation": "events", "account": "alpha", "since": "きのう"}, "invalid_options"),
                            ({"operation": "summary", "account": "alpha", "since": "24h"}, "invalid_request")):
        with pytest.raises(ReportServiceError) as caught:
            execute_report(env["context"], request)
        assert str(caught.value) == reason
    server = _load_server_module()
    names = {tool["name"] for tool in server.SERVER_READ_TOOLS}
    assert {"thth_events", "thth_summary"} <= names
    tool = next(tool for tool in server.SERVER_READ_TOOLS if tool["name"] == "thth_events")
    assert tool["inputSchema"]["required"] == ["account"] and "project" not in tool["inputSchema"]["properties"]


def test_遠くの道のeventsとsummaryは同じ口を呼ぶ(home, worker, capsys):
    from tests.test_v3140_remote_cli import run as remote_run
    rc, out, _ = remote_run(capsys, "events", RACCOUNT, "--since", "7d", "--limit", "5", "--json")
    assert rc == 0 and json.loads(out)["operation"] == "events"
    assert worker.calls[-1]["body"] == {"account": RACCOUNT, "since": "7d", "limit": 5}
    rc, out, _ = remote_run(capsys, "summary", RACCOUNT, "--json")
    assert rc == 0 and json.loads(out)["operation"] == "summary"
    assert worker.calls[-1]["path"] == "/api/v1/summary"


def _reply(row_id, *, written, collected):
    cfg = accounts.load_account(ACCOUNT)
    path = os.path.join(accounts.data_dirs(cfg, ACCOUNT)["replies"], "P-NEW.ndjson")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": row_id, "username": "someone_else", "text": "x", "timestamp": written,
                            "post_id": "P-NEW", "collected_at": collected}) + "\n")


def test_eventsのカーソルは遅れて記録されたものを取りこぼさず二度返さない(lived):
    first = events_cli.build(ACCOUNT, since="7d", now=NOW)
    cursor = first["next_cursor"]
    assert cursor.startswith("c1.")
    # 何も増えていなければ続きは空。
    empty = events_cli.build(ACCOUNT, after=cursor, now=NOW)
    assert empty["events"] == [] and empty["next_cursor"] == cursor
    # 採取の 1 回の中で付いた時刻が、前に読んだ最後の時刻より少し前（遅れて書かれた）。
    t_last = max(jst.parse(r["recorded_at"]) for r in first["events"] if r["recorded_at"])
    _reply("R-LATE", written=jst.iso(t_last - datetime.timedelta(minutes=20)),
           collected=jst.iso(t_last - datetime.timedelta(minutes=5)))
    _reply("R-NEXT", written=jst.iso(NOW), collected=jst.iso(NOW + datetime.timedelta(minutes=1)))
    got = events_cli.build(ACCOUNT, after=cursor, now=NOW + datetime.timedelta(minutes=2))
    assert [r["ref"] for r in got["events"]] == ["R-LATE", "R-NEXT"]
    # 次のカーソルの続きには、もう出さない。
    assert events_cli.build(ACCOUNT, after=got["next_cursor"], now=NOW)["events"] == []
    # limit で古い方から刻んでも、つないで読めば同じ。
    page = events_cli.build(ACCOUNT, after=cursor, limit=1, now=NOW)
    rest = events_cli.build(ACCOUNT, after=page["next_cursor"], now=NOW)
    assert [r["ref"] for r in page["events"] + rest["events"]] == ["R-LATE", "R-NEXT"]
    # event_id からも続きを読める（今の流れの、記録した時刻の順でその後ろ。遅れて書かれた R-LATE の
    # 後ろには先に記録された R1 も並ぶ——取りこぼさない側。重なりは受け手が event_id で除く）。
    late_id = got["events"][0]["event_id"]
    assert [r["ref"] for r in events_cli.build(ACCOUNT, after=late_id, now=NOW)["events"]] == ["R1", "R-NEXT"]


def test_eventsのカーソルの断り(lived):
    for kwargs, reason in (({"after": "c1.壊れた"}, "invalid_cursor"),
                           ({"after": "ev_" + "0" * 24}, "cursor_unknown"),
                           ({"after": "c1.e30", "since": "24h"}, "since_and_after")):
        with pytest.raises(events_cli.EventsError) as caught:
            events_cli.build(ACCOUNT, now=NOW, **kwargs)
        assert str(caught.value) == reason
