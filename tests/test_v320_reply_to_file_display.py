"""3.2.0 `reply_to_file` の見せ方（設計 3.2.0 §2・§4・§6）。

待ちは**時刻超過に数えない**。代わりに「返信待ち」として数え、board の要確認と
morning の予定の段に**待っている原稿の名前と待ち先**を出す。handoff の queue に
`waiting_reply` の数。`thth preview` は「返信先: <file>（未解決・待ち）」または
「返信先: <file> → <post_id>」。
"""
from __future__ import annotations

import datetime
import json

from tests.conftest import run_thth, write_queue_file
from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one, sections  # noqa: F401
from thth import lint as lint_mod
from thth import morning
from thth import operations_handoff as handoff
from thth import report as report_mod

Q = "q.md"
A = "a.md"
Q_POST_ID = "17900000000000001"
BOARD_NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))


def _question(queue_dir, *, posted, account=None, publish_at="2026-09-09T05:00:00+09:00"):
    fm = {"publish_at": publish_at}
    if account:
        fm["account"] = account
    if posted:
        fm.update(status="posted", post_id=Q_POST_ID, posted_at=publish_at)
    return write_queue_file(queue_dir, Q, fm_overrides=fm, body="## threads\n\n問い\n")


def _answer(queue_dir, *, account=None, publish_at="2026-09-09T07:00:00+09:00"):
    fm = {"reply_to_file": Q, "publish_at": publish_at}
    if account:
        fm["account"] = account
    return write_queue_file(queue_dir, A, fm_overrides=fm, body="## threads\n\n答え\n")


# ---------------------------------------------------------------- board

def test_boardは待ちを時刻超過に数えず名前と待ち先を出す(isolated_account):
    qdir = isolated_account["queue_dir"]
    # 問いは承認済みだが未来（まだ出ない）・答えは 3 時間前の予定（時刻は過ぎている）。
    _question(qdir, posted=False, publish_at="2026-09-09T23:00:00+09:00")
    _answer(qdir)
    summary = report_mod.board_summary(now=BOARD_NOW)
    row = next(r for r in summary["accounts"] if r["account"] == isolated_account["name"])
    assert row["waiting_items"] == [{"file": A, "waiting_for": Q}]
    assert row["waiting_reply_count"] == 1
    reasons = {item["file"]: item["reason"] for item in row["needs_review"]}
    assert reasons[A] == "reply_to_unresolved: waiting_for q.md"
    assert not any(item["reason"] in ("overdue", "rehearsal") for item in row["needs_review"])


def test_boardの文面に返信待ちが出る(isolated_account):
    qdir = isolated_account["queue_dir"]
    _question(qdir, posted=False, publish_at="2030-01-01T23:00:00+09:00")
    _answer(qdir, publish_at="2020-01-01T08:00:00+09:00")
    result = run_thth(["board"])
    assert result.returncode == 0, result.stderr
    assert "返信待ち 1 件" in result.stdout
    assert f"{A} — 返信待ち: {Q} が出たら返信します" in result.stdout


def test_queueとscheduleにも待ちが出る(isolated_account):
    qdir = isolated_account["queue_dir"]
    _question(qdir, posted=False, publish_at="2030-01-01T23:00:00+09:00")
    _answer(qdir, publish_at="2020-01-01T08:00:00+09:00")
    info = report_mod.queue_summary(isolated_account["name"], now=BOARD_NOW)[isolated_account["name"]]
    assert info["waiting_reply"] == 1
    rows = {row["file"]: row for row in report_mod.schedule(isolated_account["name"], now=BOARD_NOW)}
    assert rows[A]["reply_to_file"] == Q and rows[A]["waiting_for"] == Q
    shown = run_thth(["schedule", isolated_account["name"]])
    assert f"← 返信待ち: {Q}" in shown.stdout


# ---------------------------------------------------------------- handoff

def test_handoffは待ちをwaiting_replyに数えoverdueに数えない(isolated_account):
    qdir = isolated_account["queue_dir"]
    _question(qdir, posted=False, publish_at="2026-09-09T23:00:00+09:00")
    _answer(qdir, publish_at="2026-09-09T05:00:00+09:00")          # 5 時間超過
    name = isolated_account["name"]
    counts = handoff.answer(name, now=BOARD_NOW)["by_account"][name]["queue"]["counts"]
    assert counts["waiting_reply"] == 1
    assert counts["overdue"] == 0
    assert counts["approved_waiting"] == 2


def test_handoffは解決した答えが遅れていれば時刻超過に数える(isolated_account):
    qdir = isolated_account["queue_dir"]
    _question(qdir, posted=True)
    _answer(qdir, publish_at="2026-09-09T05:00:00+09:00")
    name = isolated_account["name"]
    counts = handoff.answer(name, now=BOARD_NOW)["by_account"][name]["queue"]["counts"]
    assert counts["waiting_reply"] == 0
    assert counts["overdue"] == 1


# ---------------------------------------------------------------- morning

def test_morningは返信待ちを予定の段に名前と待ち先で出す(one):
    qdir = one["queue_dir"]
    _question(qdir, posted=False, account=ACCOUNT, publish_at="2026-09-23T23:00:00+09:00")
    _answer(qdir, account=ACCOUNT, publish_at="2026-09-23T05:00:00+09:00")   # 2h 超過
    payload = morning.build(PROJECT, now=NOW, mark=False)
    today = sections(payload)["today"]["value"]["by_account"][ACCOUNT]["value"]
    assert today["overdue_items"]["value"]["n"] == 0, "待ちは時刻超過に数えない"
    waiting = today["waiting_items"]["value"]
    assert waiting["n"] == 1
    [row] = waiting["items"]
    assert row["file"] == A and row["waiting_for"] == Q
    assert row["reason"] == "reply_to_unresolved: waiting_for q.md"
    assert today["queue"]["value"]["waiting_reply"] == 1
    assert today["queue"]["value"]["overdue"] == 0
    assert not [s for s in sections(payload)["next_steps"]["value"]["steps"]
                if s["kind"] == "overdue" and s["file"] == A]
    lines = []
    morning.render(payload, out=lines.append)
    assert any(f"返信待ち: {A} → {Q} が出たら返信します" in line for line in lines)
    assert any("返信待ち 1" in line for line in lines)


# ---------------------------------------------------------------- preview

def test_previewは返信先の原稿と解決の見込みを出す(isolated_account):
    qdir = isolated_account["queue_dir"]
    _question(qdir, posted=False)
    path = _answer(qdir)
    assert lint_mod.preview_file(path).endswith("\n返信先: q.md（未解決・待ち）")
    _question(qdir, posted=True)
    assert lint_mod.preview_file(path) == f"答え\n返信先: q.md → {Q_POST_ID}"
    shown = run_thth(["preview", path, "--json"])
    payload = json.loads(shown.stdout)
    assert payload["reply_to_file"] == Q and payload["reply_to"] == Q_POST_ID
    assert payload["unresolved"] is None


def test_reply_to_fileの無い原稿のpreviewは変わらない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "plain.md")
    assert lint_mod.preview_file(path) == "本文です。"
    payload = json.loads(run_thth(["preview", path, "--json"]).stdout)
    assert set(payload) == {"file", "text", "topic"}
