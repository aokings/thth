#!/usr/bin/env python3
"""テスト専用: `thth.accounts.repo_lock_path_for()` と同じ repo 単位ロックを確保して
N 秒保持する（外部レビュー §2・受け入れ 7）。「同じ clone を持つ別アカウントの
`thth throw` が実プロセスで弾かれる」ことを確かめるための「保持者」役
（`tests/helpers/hold_lock.py` の repo 版）。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from thth import accounts as accounts_mod  # noqa: E402
from thth import lock as lock_mod  # noqa: E402


def main() -> None:
    repo_dir = sys.argv[1]
    seconds = float(sys.argv[2])
    lock_path = accounts_mod.repo_lock_path_for(repo_dir)
    repo_lock = lock_mod.AccountLock(lock_path)
    repo_lock.acquire()
    sys.stdout.write("locked\n")
    sys.stdout.flush()
    import time
    time.sleep(seconds)
    repo_lock.release()


if __name__ == "__main__":
    main()
