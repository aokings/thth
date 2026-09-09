"""clone 単位のロック（外部レビュー §2・受け入れ 7・8）。

設計は「1 repo に clone は 1 つ、アカウントは複数ぶら下がってよい」。ロックが
account 単位だけだと、別アカウントの投稿でも同じ index・作業ツリー・rebase 状態を
同時に触れる。`repo_dir` を正規化した単位のロックを足し、**取得順を repo → account
に固定する**（逆順を作らない＝デッドロックを作らない）。
"""
from __future__ import annotations

import os
import subprocess
import sys

from tests.conftest import run_thth
from thth import accounts as accounts_mod
from thth import core
from thth import lock as lock_mod

HOLD_REPO_LOCK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "helpers", "hold_repo_lock.py")


# 受け入れ 8: ロックの取得順が repo → account であること（順序をテストで固定する）。
def test_8_ロック取得順はrepoからaccount(isolated_account):
    order = []

    class _SpyLock:
        def __init__(self, path):
            self.path = path

        def acquire(self):
            order.append(("acquire", self.path))

        def release(self):
            order.append(("release", self.path))

    import thth.core as core_mod
    original = core_mod.lock_mod.AccountLock
    core_mod.lock_mod.AccountLock = _SpyLock
    try:
        account_cfg = accounts_mod.load_account(isolated_account["name"])
        state_dir = accounts_mod.state_dir_for(isolated_account["name"])
        with core_mod._account_locks(isolated_account["name"], account_cfg, state_dir):
            pass
    finally:
        core_mod.lock_mod.AccountLock = original

    acquires = [p for (op, p) in order if op == "acquire"]
    releases = [p for (op, p) in order if op == "release"]
    assert len(acquires) == 2
    # repo ロックが先（パスに `_repos` を含む）、account ロックが後（state_dir/lock）。
    assert "_repos" in acquires[0]
    assert acquires[1] == os.path.join(state_dir, "lock")
    # release は取得と逆順（account → repo）。
    assert releases == list(reversed(acquires))


# 受け入れ 7: 同じ repo_dir を持つ 2 アカウントを同時に走らせると、後から来たほうが
# 弾かれる（実プロセスで確かめる。tests/helpers/hold_lock.py の流儀）。
def test_7_同じrepo_dirの2アカウント同時実行は後から来たほうが弾かれる(
        isolated_account_factory, tmp_path):
    shared_repo = str(tmp_path / "shared-repo")
    os.makedirs(os.path.join(shared_repo, "docs", "sns", "queue"), exist_ok=True)
    acct_a = isolated_account_factory(name="acct-a-threads", repo_dir=shared_repo)
    isolated_account_factory(name="acct-b-threads", repo_dir=shared_repo)

    holder = subprocess.Popen(
        [sys.executable, HOLD_REPO_LOCK, shared_repo, "3"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
    )
    try:
        line = holder.stdout.readline()
        assert line.strip() == "locked"

        # 別アカウント（acct-b）だが同じ repo_dir なので、repo ロックで弾かれる。
        result = run_thth(["throw", "acct-b-threads"])
        assert result.returncode == 1
        assert "既に実行中" in result.stdout or "既に実行中" in result.stderr

        # 同じ repo_dir の別名（acct-a）でも同様に弾かれる。
        result_a = run_thth(["throw", acct_a["name"]])
        assert result_a.returncode == 1
    finally:
        holder.wait(timeout=10)


def test_repoロックが解放されれば続けて投げられる(isolated_account_factory, tmp_path):
    shared_repo = str(tmp_path / "shared-repo2")
    os.makedirs(os.path.join(shared_repo, "docs", "sns", "queue"), exist_ok=True)
    isolated_account_factory(name="acct-c-threads", repo_dir=shared_repo)

    holder = subprocess.Popen(
        [sys.executable, HOLD_REPO_LOCK, shared_repo, "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
    )
    try:
        line = holder.stdout.readline()
        assert line.strip() == "locked"
        blocked = run_thth(["throw", "acct-c-threads"])
        assert blocked.returncode == 1
    finally:
        holder.wait(timeout=10)

    import time
    time.sleep(0.2)
    result = run_thth(["throw", "acct-c-threads", "--json"])
    assert result.returncode == 0


# repo を持たないアカウント（masaru-threads の repos/_none 相当）でも壊れないこと。
def test_repoを持たないアカウントでもロックが壊れない(isolated_account_factory, tmp_path):
    none_repo = str(tmp_path / "repos" / "_none")  # 実在しないディレクトリのまま
    account = isolated_account_factory(name="solo-threads", repo_dir=none_repo)
    result = core.throw_once(account["name"])
    # queue_dir が無い（型外0本）ので何も出さないが、ロック確保自体で例外にならない。
    assert result.exit_code == 0
    assert result.action == "none"


def test_repo_lock_path_forは同じ実パスなら同じパスを返す(tmp_path):
    real_dir = tmp_path / "repo"
    real_dir.mkdir()
    p1 = accounts_mod.repo_lock_path_for(str(real_dir))
    p2 = accounts_mod.repo_lock_path_for(str(real_dir) + os.sep)
    assert p1 == p2


def test_repo_lock_path_forは存在しないパスでも壊れない(tmp_path):
    missing = str(tmp_path / "does" / "not" / "exist")
    p = accounts_mod.repo_lock_path_for(missing)
    assert p.endswith(".lock")
