"""5.10.0（設計 効果測定と連動 §7 段 6）: 測っているふりを無くす・済んだ糸を雑音にしない。

見るのは:
- 返信を数える段は名でなく印（`replies_within_hours=`）で決まる。`engage` でも返信を数え、告知の本数では ✓ にならない。
  `warm` は印が無くても今までどおり既定の時間で数える。表示の段の名は窓口が書いた名のまま。
- 全段を満たした糸は `state: done`。7 日動かなくても停滞にならない。`next` は closed（任意）と広場に置く。
- events は done の糸に `scenario_stalled` を出さず、`scenario_done`（続いている状態）を出す。
- dashboard の要対応に「済んだ糸」と「時刻を過ぎて未投稿」（morning と同じ定義）。
- observe の広場の候補と seeds の列 4 に done の糸も入る（closed は今までどおり）。
- seeds で媒体として取れない数（Bluesky の views）は `below_min_n` でなく `metric_unavailable:<媒体>`。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib

import pytest

from tests.test_v500_scenario import NOW, PROJECT, _sns, _write, kp  # noqa: F401
from thth import (accounts as accounts_mod, after_cli, dashboard_cli, events_cli, jst, morning, plaza_moments,
                  scenario, scenario_seeds, sent)

DONE_THREAD = """---
thth_scenario: 1
kind: thread
id: done-01
title: 済んだ糸
project: kp
started: 2026-10-10
---
- promote: threads,x need=1
"""

ENGAGE_THREAD = """---
thth_scenario: 1
kind: thread
id: eng-01
title: 返信を測る糸
project: kp
started: 2026-10-20
---
- promote: threads need=1
- engage: threads replies_within_hours=48
"""


@pytest.fixture
def world(kp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    return kp


def _posted(kp, file, *, thread, post_id, at, account="kp-threads", media="threads"):  # noqa: F811
    _write(os.path.join(kp["queue"], file),
           _sns(account, status="posted", publish_at=at, thread=thread, media=media,
                extra={"post_id": post_id, "posted_at": at}))


def _replies(post_id, rows):
    cfg = accounts_mod.load_account("kp-threads")
    directory = pathlib.Path(accounts_mod.data_dirs(cfg, "kp-threads")["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{post_id}.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _thread(ident):
    return next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == ident)


# ------------------------------------------------------------------ 返信の段は印で決まる

def test_engageに印があれば返信を数え_告知の本数ではチェックにならない(world):
    _write(os.path.join(world["scen"], "eng-01.md"), ENGAGE_THREAD)
    _posted(world, "th-eng.md", thread="eng-01", post_id="ENG", at="2026-10-27T08:00:00+09:00")
    row = _thread("eng-01")
    engage = row["stages"][1]
    # 告知の 1 本は promote の段だけが数える。engage は返信の段（0 件なので満たしていない）。
    assert engage["name"] == "engage" and engage["replies_within_hours"] == 48
    assert engage["replies"] == 0 and engage["overdue"] == 0 and engage["satisfied"] is False
    assert "done" not in engage and "need" not in engage
    assert engage["label"] == "2 段目"   # 表示の段の名は窓口が書いた名のまま（日本語の名を作らない）
    assert row["stage"] == {"name": "engage", "index": 2, "of": 2} and row["state"] == "active"
    assert row["next"][0]["kind"] == "wait"
    # 返信が 2 件来て、1 件に返した（48 時間の内）。返信の段が満ちて糸は done。
    _replies("ENG", [{"id": "R1", "username": "outside", "text": "PRIVATE REPLY",
                      "timestamp": "2026-10-27T20:00:00+09:00", "post_id": "ENG"},
                     {"id": "R2", "username": "outside", "text": "PRIVATE REPLY",
                      "timestamp": "2026-10-27T21:00:00+09:00", "post_id": "ENG"}])
    sent.write(accounts_mod.state_dir_for("kp-threads"), post_id="ANSWER", text="PRIVATE SENT", body_hash="h",
               sent_at="2026-10-27T20:30:00+09:00", reply_to="R1")
    row = _thread("eng-01")
    engage = row["stages"][1]
    assert engage["replies"] == 2 and engage["overdue"] == 0 and engage["satisfied"] is True
    assert row["state"] == "done"
    assert "PRIVATE" not in json.dumps(row, ensure_ascii=False)


def test_返信の段の印はSNSだけ_warmの既定は今までどおり(world):
    _write(os.path.join(world["scen"], "w.md"),
           "---\nthth_scenario: 1\nkind: thread\nid: w\n---\n- warm: threads\n")
    _write(os.path.join(world["scen"], "bad.md"),
           "---\nthth_scenario: 1\nkind: thread\nid: bad\n---\n- engage: hatenablog replies_within_hours=24\n")
    warm = _thread("w")["stages"][0]
    assert warm["replies_within_hours"] == scenario.DEFAULT_REPLIES_WITHIN_HOURS == 24
    assert warm["replies"] == 0 and warm["satisfied"] is False
    problems = scenario.lint(PROJECT, now=NOW)["problems"]
    assert any(p["code"] == "stage_medium_unknown" and p["id"] == "bad" for p in problems)


# ------------------------------------------------------------------ done

def test_全段を満たして7日動かなくても停滞でなくdone(world):
    _write(os.path.join(world["scen"], "done-01.md"), DONE_THREAD)
    _posted(world, "th-done.md", thread="done-01", post_id="D1", at="2026-10-15T07:00:00+09:00")
    row = _thread("done-01")
    assert row["state"] == "done" and row["stalled"] is None
    assert row["idle_days"] == 13
    assert row["done"] == {"since": "2026-10-15T07:00:00+09:00", "since_from": "movement", "days": 13,
                           "label": "全部の段が済んだ（閉じるか広場に置く）"}
    assert [n["kind"] for n in row["next"]] == ["close", "plaza"]
    assert "任意" in row["next"][0]["what"]
    assert row["next"][1]["what"].startswith("thth plaza post <口座> --kind measure --from scenario kp done-01 ")
    assert "done" in scenario.STATES
    # closed は今までどおり読む（done より先）。
    _write(os.path.join(world["scen"], "done-01.md"), DONE_THREAD + "\n- 2026-10-27 closed ここで区切る\n")
    row = _thread("done-01")
    assert row["state"] == "closed" and row["done"] is None and row["next"] == []


def test_doneの糸にscenario_stalledは出ずscenario_doneが出る(world):
    _write(os.path.join(world["scen"], "done-01.md"), DONE_THREAD)
    _posted(world, "th-done.md", thread="done-01", post_id="D1", at="2026-10-15T07:00:00+09:00")
    events = events_cli.build(PROJECT, since="24h", now=NOW)["events"]
    assert not [e for e in events if e["kind"] == "scenario_stalled"]
    done = [e for e in events if e["kind"] == "scenario_done"]
    assert len(done) == 1 and done[0]["ongoing"] is True and done[0]["ref"] == "done-01"
    raw = f"thth.scenario/1|{PROJECT}|done-01|done"
    assert done[0]["event_id"] == "ev_" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    assert done[0]["days"] == 13 and [n["kind"] for n in done[0]["next"]] == ["close", "plaza"]


# ------------------------------------------------------------------ dashboard の要対応

def test_要対応に済んだ糸と時刻を過ぎた原稿(world):
    _write(os.path.join(world["scen"], "done-01.md"), DONE_THREAD)
    _posted(world, "th-done.md", thread="done-01", post_id="D1", at="2026-10-15T07:00:00+09:00")
    # 08:00 の予定で承認済み・まだ出ていない（今は 11:20）。
    _write(os.path.join(world["queue"], "th-late.md"),
           _sns("kp-threads", status="approved", publish_at="2026-10-28T08:00:00+09:00"))
    got = dashboard_cli.build(PROJECT, now=NOW)
    by_kind = {}
    for row in got["attention"]:
        by_kind.setdefault(row["kind"], []).append(row)
    assert "scenario_stalled" not in by_kind
    done = by_kind["scenario_done"]
    assert len(done) == 1 and done[0]["ref"] == "done-01" and done[0]["days"] == 13
    assert done[0]["title"] == "済んだ糸 — 全部の段が済んだ（閉じるか広場に置く）"
    assert [a["label"] for a in done[0]["actions"]] == ["広場に置く", "閉じる", "開く"]
    late = by_kind["publish_overdue"]
    assert [r["ref"] for r in late] == ["th-late.md"] and late[0]["account"] == "kp-threads"
    assert late[0]["days"] == 0 and late[0]["elapsed_hours"] == 3.3
    assert "時刻を過ぎて出ていない" in late[0]["title"]
    # 定義は morning と同じ（二重に数えない）: morning の overdue_items と同じ原稿・同じ時間。
    rows = morning._overdue_rows("kp-threads", NOW)
    assert [(r["file"], r["elapsed_hours"]) for r in rows["items"]] == [("th-late.md", 3.3)]
    assert got["kpi"]["attention"]["by_kind"]["publish_overdue"] == 1
    assert got["scenario"]["n_campaigns_done"] == 1
    # 型の表にも日本語がある。
    assert dashboard_cli.KIND_JA["scenario_done"] == "済んだ糸"


# ------------------------------------------------------------------ 広場の候補と seeds の列 4

def test_広場の候補とseedsの列4にdoneの糸が入る(world, monkeypatch):
    _write(os.path.join(world["scen"], "done-01.md"), DONE_THREAD)
    _posted(world, "th-done.md", thread="done-01", post_id="D1", at="2026-10-15T07:00:00+09:00")
    monkeypatch.setattr(after_cli, "reaction_lookup", lambda account: lambda post_id: {"covered": False})
    configs = {"kp-threads": {"project": "kp", "media": "threads"}, "kp-x": {"project": "kp", "media": "x"}}
    steps = [s for s in plaza_moments.trigger_steps(configs, since=NOW - datetime.timedelta(days=1), now=NOW)
             if s["candidate"] == "post_scenario"]
    assert [(s["thread"], s["state"], s["closed_at"], s["done_at"]) for s in steps] == [
        ("done-01", "done", None, "2026-10-15T07:00:00+09:00")]
    column = next(c for c in scenario_seeds.build(PROJECT, now=NOW)["columns"] if c["key"] == "closed_threads")
    assert [(r["thread"], r["state"], r["done_at"], r["media_declared_untried"]) for r in column["rows"]] == [
        ("done-01", "done", "2026-10-15T07:00:00+09:00", ["x"])]
    # 最後の動きが 30 日より前の done の糸は広場の候補に出さない。
    later = NOW + datetime.timedelta(days=40)
    assert not [s for s in plaza_moments.trigger_steps(configs, since=later, now=later)
                if s["candidate"] == "post_scenario"]


# ------------------------------------------------------------------ seeds の metric_unavailable

def test_媒体で取れない数はmetric_unavailable(world, isolated_account_factory, monkeypatch):
    isolated_account_factory("kp-bsky", project=PROJECT, media="bluesky", handle="kp.bsky.social")
    _posted(world, "bs-1.md", thread=None, post_id="at://b1", at="2026-10-25T07:00:00+09:00",
            account="kp-bsky", media="bluesky")
    monkeypatch.setattr(after_cli, "reaction_lookup", lambda account: lambda post_id: {
        "views_24h": None, "likes_24h": 2, "replies_back_24h": 1, "covered": True})
    payload = scenario_seeds.build(PROJECT, now=NOW)
    views = next(c for c in payload["columns"] if c["key"] == "views")
    replies = next(c for c in payload["columns"] if c["key"] == "replies")
    assert "metric_unavailable:bluesky" in views["cannot_say"]
    assert "below_min_n:bluesky" not in views["cannot_say"]
    assert views["by_medium"]["bluesky"]["metric_unavailable"] is True
    # 返信は Bluesky でも取れる。数が少ないのは below_min_n のまま。
    assert "below_min_n:bluesky" in replies["cannot_say"]
    assert "metric_unavailable" not in replies["by_medium"]["bluesky"]
