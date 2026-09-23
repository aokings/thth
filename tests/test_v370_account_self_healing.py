"""3.7.0 §B1 `thth account` の「投稿できません」から、run が自分で直すものを分ける。

repo が upstream より遅れているだけ（ahead 0）なら、次の `thth run` の `sync_repo()`
が取り込む——「投稿できません」に入れず「run が自分で直すもの」に出す。判定は
board と同じ `writeback.sync_state()` で、**board と食い違わない**ことを固定する。
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import run_git
from tests.test_account_report import _ready_account
from thth import account_report, report, writeback


def _push_from_seed(pair, name="later.txt"):
    """別の clone（seed）から 1 commit push し、work で fetch する（work は遅れるだけ）。"""
    (Path(pair["seed"]) / name).write_text("あとから\n")
    run_git(pair["seed"], ["add", name])
    run_git(pair["seed"], ["commit", "-m", "あとから"])
    run_git(pair["seed"], ["push", "origin", "main"])
    run_git(pair["work"], ["fetch", "origin"])


def _board_row(name):
    return next(row for row in report.board_summary()["accounts"] if row["account"] == name)


def test_遅れているだけのrepoは投稿できませんに入れず_次のrunが取り込むと言う(
        tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    _push_from_seed(pair)
    _push_from_seed(pair, "later2.txt")
    detail = account_report.account_detail(account["name"], remote=False)
    assert detail["repo"]["synced"] is False
    assert detail["repo_sync"] == {"state": "behind_only", "ahead": 0, "behind": 2}
    assert detail["ready"] is True, detail["blockers"]
    assert detail["blockers"] == []
    assert detail["self_healing"] == [
        "repo: upstream より 2 commit 遅れています——次の run が取り込みます（遅れ 2 commit）"]
    text = account_report.render(detail)
    assert "→ **投稿できます**" in text and "→ **投稿できません**" not in text
    assert "run が自分で直すもの" in text and "次の run が取り込みます（遅れ 2 commit）" in text

    # board と食い違わない: 同じ sync_state・止めるもの（inflight・held）が無い。
    row = _board_row(account["name"])
    assert row["repo_sync"] == detail["repo_sync"]
    assert row["inflight"] is None and row["held_count"] == 0


def test_pushしていないcommitは従前どおり止めるもの_boardも同じ状態(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    (Path(pair["work"]) / "note.txt").write_text("未 push\n")
    run_git(pair["work"], ["add", "note.txt"])
    run_git(pair["work"], ["commit", "-m", "push していない"])
    detail = account_report.account_detail(account["name"], remote=False)
    assert detail["repo_sync"]["state"] == writeback.SYNC_AHEAD
    assert detail["ready"] is False and any("upstream" in b for b in detail["blockers"])
    assert detail["self_healing"] == []
    assert "→ **投稿できません**（run を止めるもの）" in account_report.render(detail)
    assert _board_row(account["name"])["repo_sync"] == detail["repo_sync"]


def test_遅れと未pushの両方は止めるもの(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    _push_from_seed(pair)
    (Path(pair["work"]) / "note.txt").write_text("未 push\n")
    run_git(pair["work"], ["add", "note.txt"])
    run_git(pair["work"], ["commit", "-m", "push していない"])
    detail = account_report.account_detail(account["name"], remote=False)
    assert detail["repo_sync"]["state"] == writeback.SYNC_DIVERGED
    assert detail["ready"] is False and detail["self_healing"] == []


def test_出られない原稿は別の段_投稿できますのまま_boardのheld_countと数が一致(
        tmp_path, isolated_account_factory):
    """裁定 09-24: held は「投稿できません」に入れない。その原稿が出られないだけで、account は
    他の原稿を出せる。「出られない原稿（held）n 本・名前」の段に出し、board と数を揃える。"""
    from tests.conftest import write_queue_file
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    for name in ("stale1.md", "stale2.md"):
        write_queue_file(pair["queue_dir"], name, body=f"## threads\n\n{name} の本文。\n",
                         fm_overrides={"account": account["name"], "status": "approved",
                                       "approved_sha": "0" * 64,
                                       "publish_at": "2026-09-01T08:00:00+09:00"})
    run_git(pair["work"], ["pull", "--ff-only"])
    detail = account_report.account_detail(account["name"], remote=False)
    row = _board_row(account["name"])
    assert row["held_count"] == detail["held"]["n"] == 2
    assert sorted(detail["held"]["files"]) == ["stale1.md", "stale2.md"]
    assert detail["held"]["reason_code"] == "approved_but_held: approval_stale 2"
    assert detail["ready"] is True and detail["blockers"] == []
    text = account_report.render(detail)
    assert "→ **投稿できます**" in text and "→ **投稿できません**" not in text
    assert "→ 出られない原稿（held）2 本: " in text and "stale1.md" in text
