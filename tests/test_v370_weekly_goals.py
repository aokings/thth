"""3.7.0 §A3 週の表（`thth analytics-report <account> --weekly-goals [--weeks 6]`）。

- 週の表は観察の表（因果とは言わない）。週ごとに目的ごとの本数と followers の増分
  （週初と週末の差・欠測は null）。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.test_after_cli import _insight_path, _write_ndjson
from thth import accounts as accounts_mod
from thth import analytics_weekly, cli, jst
from thth import sent as sent_mod

NOW = jst.parse("2026-09-24T12:00:00+09:00")   # 木曜


def _post(account, post_id, posted, goal):
    row = {"account": account["name"], "post_id": post_id, "posted_at": jst.iso(posted),
           "collected_at": jst.iso(posted + datetime.timedelta(hours=25)), "age_hours": 25,
           "marks": [24], "reply_to": None, "topic": None,
           "metrics": {"views": 10, "likes": 0, "replies": 0, "reposts": 0}}
    _write_ndjson(_insight_path(account, post_id), [row])
    if goal is not None:
        sent_mod.write(accounts_mod.state_dir_for(account["name"]), post_id=post_id, text="本文",
                       body_hash="h", sent_at=jst.iso(posted), goal=goal)


def _followers(account, counts):
    cfg = accounts_mod.load_account(account["name"])
    folder = accounts_mod.data_dirs(cfg, account["name"])["insights_account"]
    _write_ndjson(os.path.join(folder, f"{account['name']}-2026-09.ndjson"),
                  [{"account": account["name"], "date": date, "metrics": {"followers_count": n}}
                   for date, n in counts.items()])


# ------------------------------------------------------------ A3

@pytest.fixture
def weekly(isolated_account_factory):
    account = isolated_account_factory()
    # 週は月曜始まり（JST）。09-21（月）〜09-24（木・途中）と 09-14〜09-20・09-07〜09-13。
    _post(account, "R1", jst.parse("2026-09-15T09:00:00+09:00"), "reach")
    _post(account, "C1", jst.parse("2026-09-16T09:00:00+09:00"), "click")
    _post(account, "F1", jst.parse("2026-09-22T09:00:00+09:00"), "follow")
    _post(account, "F2", jst.parse("2026-09-23T09:00:00+09:00"), "follow")
    _post(account, "OLD", jst.parse("2026-09-08T09:00:00+09:00"), None)   # 3.6.0 より前
    counts = {"2026-09-06": 90, "2026-09-13": 95, "2026-09-20": 101, "2026-09-23": 110}
    _followers(account, counts)
    return account, analytics_weekly.answer(account["name"], weeks=3, now=NOW)


def test_週の表は目的ごとの本数とfollowersの増分_欠測はnull(weekly):
    _account, payload = weekly
    assert payload["report_type"] == "weekly_goals"
    assert payload["observational_difference"] is True and payload["causal"] is False
    assert payload["week_basis"].startswith("月曜始まり")
    rows = payload["weeks"]
    assert [row["week_start"] for row in rows] == ["2026-09-07", "2026-09-14", "2026-09-21"]
    assert rows[0]["posts"]["unrecorded"] == 1 and rows[0]["n_posts"] == 1
    assert rows[1]["posts"]["reach"] == 1 and rows[1]["posts"]["click"] == 1
    assert rows[2]["posts"]["follow"] == 2
    assert rows[0]["followers"]["delta"] == 5, "09-06（前週末）95-90"
    assert rows[1]["followers"] == {"start": 95, "start_date": "2026-09-13", "end": 101,
                                    "end_date": "2026-09-20", "delta": 6, "reason": None}
    assert rows[2]["partial"] is True and rows[2]["followers"]["end_date"] == "2026-09-23"
    assert rows[2]["followers"]["delta"] == 9


def test_週の表の欠測はnullと理由(isolated_account_factory):
    account = isolated_account_factory()
    _followers(account, {"2026-09-13": 95})
    payload = analytics_weekly.answer(account["name"], weeks=2, now=NOW)
    last = payload["weeks"][-1]["followers"]
    assert last["delta"] is None and last["reason"] == "followers_missing_start"
    first = payload["weeks"][0]["followers"]
    assert first["delta"] is None and first["reason"] == "followers_missing_end"


def test_週の表のCLIとmarkdown(weekly, capsys):
    account, _payload = weekly
    rc = cli.main(["analytics-report", account["name"], "--weekly-goals", "--weeks", "3", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and json.loads(out)["report_type"] == "weekly_goals"
    text = analytics_weekly.render_markdown(analytics_weekly.answer(account["name"], weeks=3,
                                                                    now=NOW))
    assert "観察の表（因果ではない）" in text
    assert "| 2026-09-14 |" in text
    rc = cli.main(["analytics-report", account["name"], "--weekly-goals", "--compare-previous"])
    assert rc == 2
