"""`thth schedule`: 日付順に「いつ何が出るか」を並べる（asmon 関東セッション指摘 2026-09-10）。

> queue は future が 47 行並ぶだけで、日付順に時刻・トピック・書き出しを通しで
> 見る手段がありません。
"""
from __future__ import annotations

import datetime

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import jst, report

NOW = datetime.datetime(2026, 9, 10, 10, 0, tzinfo=jst.JST)


def _write(account, name, publish_at, *, status="draft", topic=None, body=None):
    return write_queue_file(account["queue_dir"], name, fm_overrides={
        "status": status, "approved_sha": None, "publish_at": publish_at,
        "topic": topic}, body=body or f"## threads\n\n{name} の書き出しです。\n")


def test_日付順に並ぶ(isolated_account):
    _write(isolated_account, "c.md", "2026-09-13T08:00:00+09:00")
    _write(isolated_account, "a.md", "2026-09-11T08:00:00+09:00")
    _write(isolated_account, "b.md", "2026-09-12T08:00:00+09:00")

    rows = report.schedule(isolated_account["name"], now=NOW)
    assert [r["file"] for r in rows] == ["a.md", "b.md", "c.md"]
    assert rows[0]["head"] == "a.md の書き出しです。"


def test_承認済みと下書きを区別して両方出す(isolated_account):
    approved = _write(isolated_account, "a.md", "2026-09-11T08:00:00+09:00")
    assert approve_via_cli(approved).returncode == 0
    _write(isolated_account, "b.md", "2026-09-12T08:00:00+09:00")

    rows = report.schedule(isolated_account["name"], now=NOW)
    assert {r["file"]: r["status"] for r in rows} == {"a.md": "approved", "b.md": "draft"}


def test_出たものは並ばない(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "done.md", fm_overrides={
        "status": "posted", "post_id": "1", "publish_at": "2026-09-11T08:00:00+09:00",
        "posted_at": "2026-09-11T08:00:30+09:00"})
    assert report.schedule(isolated_account["name"], now=NOW) == []


def test_daysで絞れる(isolated_account):
    _write(isolated_account, "soon.md", "2026-09-11T08:00:00+09:00")
    _write(isolated_account, "later.md", "2026-09-30T08:00:00+09:00")
    rows = report.schedule(isolated_account["name"], now=NOW, days=7)
    assert [r["file"] for r in rows] == ["soon.md"]


def test_時刻を過ぎたものに印が付く(isolated_account):
    _write(isolated_account, "late.md", "2026-09-09T08:00:00+09:00")
    rows = report.schedule(isolated_account["name"], now=NOW)
    assert rows[0]["past"] is True


def test_CLIが読める形で出す(isolated_account):
    _write(isolated_account, "a.md", "2026-09-11T08:00:00+09:00", topic="中学受験")
    result = run_thth(["schedule", isolated_account["name"]])
    assert result.returncode == 0
    assert "a.md" in result.stdout and "中学受験" in result.stdout, result.stdout
