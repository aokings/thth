"""ロック（発注 §5 受け入れ 9）。timer 実行中に `thth throw` を直に呼んでもロックを踏む。

test_20260909_direct_throw_bypassed_lock: 設計 §3.7 で見つかったバグ（flock を
bash ラッパに置くと、入口が増えたときにロックを踏まない経路ができる）の再発防止。
ここでは「別プロセスが core と同じロックファイルを保持している間、`thth throw` を
直に呼ぶ」ことでこれを確かめる（`thth run` を経由しなくても踏むことを示すのが要点）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from tests.conftest import BIN_THTH, run_thth, write_queue_file

HOLD_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers", "hold_lock.py")


def test_20260909_direct_throw_bypassed_lock(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")
    account_name = isolated_account["name"]

    holder = subprocess.Popen(
        [sys.executable, HOLD_LOCK, account_name, "3"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
    )
    try:
        # holder がロックを取るまで待つ（"locked" の 1 行が出るまで）。
        line = holder.stdout.readline()
        assert line.strip() == "locked"

        # 「timer が thth run で走っている最中に MCP・手打ちが thth throw を直に呼ぶ」を
        # 模す。ロックは core（thth.core.throw_once）の入口にあるので、run を経由しない
        # 直の throw 呼び出しでも踏むはず。
        result = run_thth(["throw", account_name])
        assert result.returncode == 1
        assert "既に実行中" in result.stdout or "既に実行中" in result.stderr
    finally:
        holder.wait(timeout=10)


def test_9_ロック解放後は続けて投げられる(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")
    account_name = isolated_account["name"]

    holder = subprocess.Popen(
        [sys.executable, HOLD_LOCK, account_name, "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
    )
    try:
        line = holder.stdout.readline()
        assert line.strip() == "locked"
        blocked = run_thth(["throw", account_name])
        assert blocked.returncode == 1
    finally:
        holder.wait(timeout=10)

    # holder が解放した後は、通常どおり dry-run で投げられる（exit 0・skip）。
    time.sleep(0.2)
    result = run_thth(["throw", account_name, "--json"])
    assert result.returncode == 0
