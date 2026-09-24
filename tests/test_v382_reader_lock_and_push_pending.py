"""3.8.2 の裁定 2 点（主セッション・2026-09-24）。

1. `writeback.behind_remote()`（読むだけの口の遅れの確かめ）は repo のロックを待たずに
   取りに行き、取れなければ fetch せず、前回取り込んだ remote の姿で数えて「いま確かめ
   られない（他の実行が repo を使用中）」と言う。取れたら fetch する（FETCH_HEAD は書かない）。
   承認の手元パスの読み替え（`_stale_clone_line()`）と queue 等の案内（`_behind_notices()`）も同じ。
2. `thth pull <account> --push-pending --by <名前>`: repo のロックの中で fetch し、手元が
   upstream より先にいて遅れが 0 のときだけ push する。遅れがあれば押さずに理由を言う
   （rebase はしない）。押した commit の件数と題を出す。
"""
from __future__ import annotations

import json
import os

from tests.conftest import run_git
from thth import accounts, cli, writeback
from thth import lock as lock_mod


def _paths(account):
    work = account["repo_dir"]
    return work, os.path.join(os.path.dirname(work), "seed"), os.path.join(os.path.dirname(work), "origin.git")


def _advance(repo: str, name: str, *, push: bool = True) -> None:
    with open(os.path.join(repo, name), "w", encoding="utf-8") as stream:
        stream.write(name + "\n")
    run_git(repo, ["add", name])
    run_git(repo, ["commit", "-q", "-m", f"commit {name}"])
    if push:
        run_git(repo, ["push", "-q"])


# ------------------------------------------------------------ 1. ロック中の読み手は fetch しない

def test_repoのロック中はfetchせず前回の姿で数えて確かめられないと言う_空けばfetchする(isolated_account):
    work, seed, _bare = _paths(isolated_account)
    tracked_before = run_git(work, ["rev-parse", "origin/main"]).stdout.strip()
    _advance(seed, "elsewhere.txt")  # origin が 1 commit 進む（work はまだ知らない）

    held = lock_mod.AccountLock(accounts.repo_lock_path_for(work))
    held.acquire()
    try:
        info = writeback.behind_remote(work)
        notices, _by_account = cli._behind_notices([isolated_account["name"]])
        stale = cli._stale_clone_line(work, isolated_account["name"])
    finally:
        held.release()

    # fetch していない: 追跡 ref は動かず、前回の姿で数えて 0・確かめた時刻は無い。
    assert run_git(work, ["rev-parse", "origin/main"]).stdout.strip() == tracked_before
    assert info["reason"] == writeback.BEHIND_UNCHECKED
    assert info["behind"] == 0 and info["ahead"] == 0 and info["fetched_at"] is None
    assert any("いま確かめられません（他の実行が repo を使用中）" in line for line in notices)
    assert stale is not None and "いま確かめられません（他の実行が repo を使用中）" in stale

    # 空けば従前どおり fetch して数える（FETCH_HEAD は書かない）。
    fetch_head = os.path.join(work, ".git", "FETCH_HEAD")
    before = os.path.getmtime(fetch_head) if os.path.exists(fetch_head) else None
    info = writeback.behind_remote(work)
    assert info["reason"] is None and info["behind"] == 1 and info["fetched_at"]
    after = os.path.getmtime(fetch_head) if os.path.exists(fetch_head) else None
    assert after == before


# ------------------------------------------------------------ 2. --push-pending

def test_push_pendingは手元が先にいるだけなら押して件数と題を出す(isolated_account, capsys):
    work, _seed, bare = _paths(isolated_account)
    _advance(work, "a.txt", push=False)
    _advance(work, "b.txt", push=False)

    rc = cli.main(["pull", isolated_account["name"], "--push-pending", "--by", "masaru"])
    out, err = capsys.readouterr()

    assert rc == 0, err
    assert out.splitlines() == [f"{isolated_account['name']}: 押しました: 2 commit（masaru）",
                                "  commit b.txt", "  commit a.txt"]
    assert run_git(bare, ["rev-parse", "main"]).stdout.strip() == \
        run_git(work, ["rev-parse", "HEAD"]).stdout.strip()


def test_push_pendingはupstreamに遅れていれば押さずに理由を言う_rebaseもしない(isolated_account, capsys):
    work, seed, bare = _paths(isolated_account)
    _advance(work, "a.txt", push=False)
    local_head = run_git(work, ["rev-parse", "HEAD"]).stdout.strip()
    _advance(seed, "elsewhere.txt")
    origin_head = run_git(bare, ["rev-parse", "main"]).stdout.strip()

    rc = cli.main(["pull", isolated_account["name"], "--push-pending", "--by", "masaru", "--json"])
    [row] = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert row["pushed"] is False and row["ahead"] == 1 and row["behind"] == 1
    assert row["error"].startswith("upstream に 1 commit 遅れているので押しません")
    # 手元も origin も動かさない（rebase しない・押さない）。
    assert run_git(work, ["rev-parse", "HEAD"]).stdout.strip() == local_head
    assert run_git(bare, ["rev-parse", "main"]).stdout.strip() == origin_head


def test_push_pendingは名乗りが要る(isolated_account, capsys, monkeypatch):
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    assert cli.main(["pull", isolated_account["name"], "--push-pending"]) == 1
    assert "--by を付けてください" in capsys.readouterr().err
