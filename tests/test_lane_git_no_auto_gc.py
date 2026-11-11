"""レーンの repo の git の壊れを防ぐ（運用 #7497・2026-11-11）。

見るのは:
  - (a) 道具が口座の repo に打つ git には、自動の gc を止める設定が付く（部分 clone で
        自動の gc が commit を消して、はてなの記事が止まった 11/7・11/11）。
  - (b) 投げる前の同期で、HEAD から辿れる commit が欠けていれば、壊れの形と直し方を言って止まる。
        commit-graph だけが古いときは止めずに注意を 1 行出す。
"""
from __future__ import annotations

import os
import subprocess

from tests.conftest import init_git_pair, run_git
from thth import account_report, collect, gitopts, plaza_from, writeback


def _argv_of(monkeypatch, module, call):
    seen = []

    def fake(argv, **kw):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake)  # 各口は呼ぶ時に subprocess.run を引く
    call()
    return seen[-1]


def test_道具の打つgitには自動のgcを止める設定が付く(monkeypatch, tmp_path):
    """柵: gitopts.git から NO_AUTO_GC を外すと赤。"""
    want = ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    repo = str(tmp_path)
    calls = [
        (writeback, lambda: writeback._run_git(repo, ["commit", "-m", "x"])),
        (writeback, lambda: writeback._run_git_bytes(repo, ["fetch", "origin"])),
        (collect, lambda: collect._git(repo, ["commit", "-m", "x"])),
        (account_report, lambda: account_report._git(repo, ["status"])),
        (plaza_from, lambda: plaza_from._git(repo, "commit", "-m", "x")),
    ]
    for module, call in calls:
        argv = _argv_of(monkeypatch, module, call)
        assert argv[:5] == ["git", *want], (module.__name__, argv)
        monkeypatch.undo()


def test_自動のgcを止める設定は本物のgitで通る(tmp_path):
    pair = init_git_pair(tmp_path, seed_content="a\n")
    r = writeback._run_git(pair["work"], ["rev-parse", "HEAD"])
    assert r.returncode == 0 and r.stdout.strip()
    assert gitopts.git("r", "status") == ["git", *gitopts.NO_AUTO_GC, "-C", "r", "status"]


def _two_loose_commits(tmp_path):
    repo = str(tmp_path / "r")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    for n in ("1", "2"):
        with open(os.path.join(repo, "a.md"), "w", encoding="utf-8") as f:
            f.write(n)
        run_git(repo, ["add", "a.md"])
        run_git(repo, [*ident, "commit", "-q", "-m", n])
    first = run_git(repo, ["rev-parse", "HEAD~1"]).stdout.strip()
    return repo, first


def test_履歴に欠けたcommitがあれば壊れの形と直し方を言う(tmp_path):
    """柵: history_damage の rev-list の確かめを消すと赤。"""
    repo, first = _two_loose_commits(tmp_path)
    assert writeback.history_damage(repo) == ""
    os.remove(os.path.join(repo, ".git", "objects", first[:2], first[2:]))
    msg = writeback.history_damage(repo)
    assert "欠けた commit" in msg and "投稿しません" in msg
    assert "git fetch --refetch origin" in msg and "thth report file" in msg


def test_commit_graphだけが古いときは止めずに注意を出す(tmp_path, monkeypatch, capsys):
    repo, _ = _two_loose_commits(tmp_path)
    real = writeback._run_git

    def graph_broken(top, args):
        if args[:1] == ["rev-list"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: exists in commit-graph but not in the object database")
        return real(top, args)

    monkeypatch.setattr(writeback, "_run_git", graph_broken)
    assert writeback.history_damage(repo) == ""
    assert "commit-graph write --reachable" in capsys.readouterr().err


def test_同期は履歴の欠けで止まる(tmp_path, monkeypatch):
    """柵: _confirm_synced から history_damage の呼び出しを消すと赤。"""
    pair = init_git_pair(tmp_path, seed_content="a\n")
    ok, why, _ = writeback.sync_repo_no_push(pair["work"])
    assert ok, why
    monkeypatch.setattr(writeback, "history_damage", lambda repo: "repo の履歴に欠けた commit があります")
    ok, why, head = writeback.sync_repo_no_push(pair["work"])
    assert not ok and "欠けた commit" in why and head is None
