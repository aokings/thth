"""`max_per_run`（台帳・既定 1）: 1 実行で出す本数（設計 §3.3・§3.6・masaru 指摘
2026-09-09）。1 本ごとに select をやり直す（前の投稿が次の select の
last_post_at に効く）ので、min_interval_hours > 0 なら max_per_run を増やしても
実際には 1 本しか出ない。これは正しい挙動（設計 §3.6）。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess

from tests.conftest import make_queue_text, run_git
from tests.test_fake_api import _adapter, fake_threads_server
from thth import accounts as accounts_mod
from thth import core
from thth import runs as runs_mod

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")


def _init_git_pair_multi(tmp_path, files: dict) -> dict:
    """`tests/conftest.py::init_git_pair()` の複数ファイル版（同じ流儀:
    bare origin → seed（複数ファイルを 1 commit）→ work）。"""
    bare = str(tmp_path / "origin.git")
    seed = str(tmp_path / "seed")
    work = str(tmp_path / "work")

    subprocess.run(["git", "init", "--bare", "-b", "main", bare], check=True,
                    capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", seed], check=True, capture_output=True, text=True)
    run_git(seed, ["config", "user.email", "thth-test@example.invalid"])
    run_git(seed, ["config", "user.name", "thth-test"])

    queue_dir = os.path.join(seed, "docs", "sns", "queue")
    os.makedirs(queue_dir, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(queue_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
    run_git(seed, ["add", "-A"])
    run_git(seed, ["commit", "-m", "seed"])
    run_git(seed, ["remote", "add", "origin", bare])
    run_git(seed, ["push", "-u", "origin", "main"])

    subprocess.run(["git", "clone", bare, work], check=True, capture_output=True, text=True)
    run_git(work, ["config", "user.email", "thth-test@example.invalid"])
    run_git(work, ["config", "user.name", "thth-test"])

    return {"bare": bare, "seed": seed, "work": work,
            "queue_dir": os.path.join(work, "docs", "sns", "queue")}


def _three_approved_files():
    return {
        "a.md": make_queue_text(
            fm_overrides={"publish_at": "2026-09-09T07:00:00+09:00"},
            body="## threads\n\n本文A\n"),
        "b.md": make_queue_text(
            fm_overrides={"publish_at": "2026-09-09T08:00:00+09:00"},
            body="## threads\n\n本文B\n"),
        "c.md": make_queue_text(
            fm_overrides={"publish_at": "2026-09-09T09:00:00+09:00"},
            body="## threads\n\n本文C\n"),
    }


def test_min_interval_0でmax_per_run3なら3本とも出る(isolated_account_factory, tmp_path):
    pair = _init_git_pair_multi(tmp_path, _three_approved_files())
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True,
        min_interval_hours=0, max_per_run=3)
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True,
                                  adapter_factory=factory, now=NOW)

    assert result.exit_code == 0
    assert result.action == "post"

    runs = runs_mod.read_runs(state_dir)
    posted = [r for r in runs if r["action"] == "post" and r["status"] == "ok"]
    assert len(posted) == 3
    # publish_at 昇順（a→b→c）で出たはず。
    assert [r["file"] for r in posted] == ["a.md", "b.md", "c.md"]

    for name in ("a.md", "b.md", "c.md"):
        with open(os.path.join(pair["work"], "docs", "sns", "queue", name), encoding="utf-8") as f:
            assert "status: posted" in f.read()


def test_min_interval_6でmax_per_run3でも実際には1本しか出ない(isolated_account_factory, tmp_path):
    pair = _init_git_pair_multi(tmp_path, _three_approved_files())
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True,
        min_interval_hours=6, max_per_run=3)
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True,
                                  adapter_factory=factory, now=NOW)

    assert result.exit_code == 0
    assert result.action == "post"

    runs = runs_mod.read_runs(state_dir)
    posted = [r for r in runs if r["action"] == "post" and r["status"] == "ok"]
    assert len(posted) == 1
    assert posted[0]["file"] == "a.md"

    # b・c はまだ approved のまま（min_interval に阻まれて選ばれていない）。
    for name in ("b.md", "c.md"):
        with open(os.path.join(pair["work"], "docs", "sns", "queue", name), encoding="utf-8") as f:
            assert "status: approved" in f.read()


def test_既定のmax_per_runは1(isolated_account_factory, tmp_path):
    """台帳に `max_per_run` が無い（旧い台帳・テスト fixture の既定）ときは 1 本だけ。"""
    pair = _init_git_pair_multi(tmp_path, _three_approved_files())
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True, min_interval_hours=0)
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True,
                                  adapter_factory=factory, now=NOW)

    assert result.exit_code == 0
    runs = runs_mod.read_runs(state_dir)
    posted = [r for r in runs if r["action"] == "post" and r["status"] == "ok"]
    assert len(posted) == 1


def test_途中で曖昧な失敗が起きたらそこで打ち切る(isolated_account_factory, tmp_path):
    """1 本目が timeout（出たか分からない失敗）→ inflight が残り、2 本目には進まない
    （設計 §3.3: 途中で失敗したらそこで打ち切る）。"""
    pair = _init_git_pair_multi(tmp_path, _three_approved_files())
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True,
        min_interval_hours=0, max_per_run=3)
    state_dir = accounts_mod.state_dir_for(account["name"])

    with fake_threads_server({"create": "ok", "publish": "ok", "publish_delay": 3}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url, timeout=0.5)

        result = core.throw_once(account["name"], production_flag=True,
                                  adapter_factory=factory, now=NOW)

    assert result.exit_code == 1
    assert result.action == "inflight"

    runs = runs_mod.read_runs(state_dir)
    posted = [r for r in runs if r["action"] == "post" and r["status"] == "ok"]
    assert len(posted) == 0  # 1 本も確定していない（1 本目が曖昧なまま止まった）

    # b・c は触られていない（2 本目以降は試みていない）。
    for name in ("a.md", "b.md", "c.md"):
        with open(os.path.join(pair["work"], "docs", "sns", "queue", name), encoding="utf-8") as f:
            assert "status: approved" in f.read()
