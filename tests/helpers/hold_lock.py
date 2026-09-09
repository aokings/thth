#!/usr/bin/env python3
"""テスト専用: `thth.core` が使うのと同じロックファイルを確保して N 秒保持する。
`test_20260909_direct_throw_bypassed_lock`（受け入れ 9）で、別プロセスが
`thth throw` を直に叩いてもロックを踏むことを確認するための「保持者」役。
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
