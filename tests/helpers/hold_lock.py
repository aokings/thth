#!/usr/bin/env python3
"""テスト専用: `thth.core` が使うのと同じロックファイルを確保して N 秒保持する。
`test_20260909_direct_throw_bypassed_lock`（受け入れ 9）で、別プロセスが
`thth throw` を直に叩いてもロックを踏むことを確認するための「保持者」役。

    hold_lock.py <account> <秒>           … account のロック（`thth run` が握る側）
    hold_lock.py <account> <秒> repo      … **repo のロック**（`thth collect` が握る側）

repo の側は `thth board` の「いま collect が走っています」を確かめるのに使う
（引継ぎ 2026-09-15 §3-D）。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from thth import accounts as accounts_mod  # noqa: E402
from thth import lock as lock_mod  # noqa: E402


def main() -> None:
    account_name = sys.argv[1]
    seconds = float(sys.argv[2])
    which = sys.argv[3] if len(sys.argv) > 3 else "account"
    if which == "repo":
        repo_dir = accounts_mod.load_account(account_name)["repo_dir"]
        lock_path = accounts_mod.repo_lock_path_for(repo_dir)
    else:
        state_dir = accounts_mod.state_dir_for(account_name)
        lock_path = os.path.join(state_dir, "lock")
    account_lock = lock_mod.AccountLock(lock_path)
    account_lock.acquire()
    sys.stdout.write("locked\n")
    sys.stdout.flush()
    import time
    time.sleep(seconds)
    account_lock.release()


if __name__ == "__main__":
    main()
