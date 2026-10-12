"""timer の実行（`thth run`）は repo のロックを少し待つ（masaru 10/12「直す」）。

同じ repo にぶら下がるアカウントは、名前のハッシュで決まる起点が重なると同じ分に
起動する（VM の kopicha-x と kopicha-mastodon は 10/8 から 1 日 70〜85 回、片方が
「ロック取得失敗」で丸ごと見送られていた）。`thth run` だけが
`core.RUN_LOCK_WAIT_SECONDS` まで待つ。手で打つ `thth throw` は今までどおり待たない。
"""
from __future__ import annotations

import os
import subprocess
import sys

from tests.conftest import init_real_repo
from thth import cli, collect, core

HOLD_REPO_LOCK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "helpers", "hold_repo_lock.py")


def _hold(repo_dir: str, seconds: float) -> subprocess.Popen:
    holder = subprocess.Popen(
        [sys.executable, HOLD_REPO_LOCK, repo_dir, str(seconds)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
    assert holder.stdout.readline().strip() == "locked"
    return holder


def test_throw_onceは待てば空いたロックで続ける(isolated_account_factory, tmp_path):
    repo = init_real_repo(tmp_path, "shared-wait")
    isolated_account_factory(name="wait-a-threads", repo_dir=repo)
    holder = _hold(repo, 0.5)
    try:
        result = core.throw_once("wait-a-threads", log=lambda *_a: None, wait=10)
    finally:
        holder.wait(timeout=10)
    assert result.action != "locked"


def test_throw_onceの既定は今までどおり待たない(isolated_account_factory, tmp_path):
    repo = init_real_repo(tmp_path, "shared-nowait")
    isolated_account_factory(name="wait-b-threads", repo_dir=repo)
    holder = _hold(repo, 1.5)
    try:
        result = core.throw_once("wait-b-threads", log=lambda *_a: None)
        assert result.action == "locked" and result.exit_code == 1
    finally:
        holder.wait(timeout=10)


def test_run_collectは待てば空いたrepoロックで採る(isolated_account_factory, tmp_path,
                                                  monkeypatch):
    repo = init_real_repo(tmp_path, "shared-collect")
    isolated_account_factory(name="wait-c-threads", repo_dir=repo)
    seen = []
    monkeypatch.setattr(collect.writeback, "sync_repo", lambda _d: (True, None, None))
    monkeypatch.setattr(collect, "collect_once",
                        lambda *_a, **_k: seen.append(1) or
                        {"touched": [], "posts": 0, "errors": []})
    lines = []
    holder = _hold(repo, 0.5)
    try:
        rc = collect.run_collect("wait-c-threads", adapter=object(), log=lines.append, wait=10)
    finally:
        holder.wait(timeout=10)
    assert seen == [1]
    assert not any("見送ります" in line for line in lines)
    assert rc == 0


def test_run_collectの既定は待たずに見送る(isolated_account_factory, tmp_path, monkeypatch):
    repo = init_real_repo(tmp_path, "shared-collect-nowait")
    isolated_account_factory(name="wait-d-threads", repo_dir=repo)
    monkeypatch.setattr(collect, "collect_once",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("採った")))
    lines = []
    holder = _hold(repo, 1.5)
    try:
        rc = collect.run_collect("wait-d-threads", adapter=object(), log=lines.append)
    finally:
        holder.wait(timeout=10)
    assert rc == 0
    assert any("見送ります" in line for line in lines)


def test_cmd_runは投稿と採取の両方に待ち時間を渡す(tmp_path, isolated_account_factory,
                                                  monkeypatch):
    token = tmp_path / "account.token"
    token.write_text("{}\n", encoding="utf-8")
    account = isolated_account_factory(token=str(token), production=True)
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    waits = {}

    def fake_throw(*_a, **k):
        waits["throw"] = k.get("wait")
        return core.ThrowResult(0, "production", "skip", "出すものが無い")

    def fake_collect(*_a, **k):
        waits["collect"] = k.get("wait")
        return 0

    monkeypatch.setattr(cli.core, "throw_once", fake_throw)
    monkeypatch.setattr(cli.collect_mod, "run_collect", fake_collect)
    cli.main(["run", account["name"]])
    assert waits == {"throw": core.RUN_LOCK_WAIT_SECONDS,
                     "collect": core.RUN_LOCK_WAIT_SECONDS}
    assert 0 < core.RUN_LOCK_WAIT_SECONDS < 300
