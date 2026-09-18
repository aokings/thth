"""`thth board` が「いま run が走っています」を言える（引継ぎ 2026-09-13「小さいもの」）。

**なぜ要るか。** board は `inflight` しか見ていなかった。`inflight` が書かれるのは
**公開の直前だけ**で、select・同期・書き戻し・採取のあいだは空——だから運用セッション
から見ると「実行中で待っている」と「止まっている」が**同じ顔**だった。

**観測が対象を壊さないこと**がここの肝。flock の保持者は OS しか知らないので、
「握られているか」を flock で試すと **board が一瞬ロックを取り、そのあいだに始まった
`thth run` が「既に実行中」で落ちる。** そうならないよう、握る側が自分の pid を
ロックファイルの中身として名乗り、board は**読むだけ**にした
（`thth.lock.AccountLock.holder_pid()`）。

固定するのは 6 つ:

  1. 別プロセスがロックを握っているあいだ、`thth board` が 1 行足す（`--json` にも）。
  2. 放したあとは出ない。
  3. **board はロックを奪わない**（board を挟んでも `thth throw` が通る）。
  4. **握り主が死んだら「走っている」と言わない**（`kill -9` で flock は OS が
     放すのに、名乗りだけ残る）。
  5. **採取の最中も 1 行出る**（引継ぎ 2026-09-15 §3-D）。`collect` は repo を
     持つアカウントでは **repo のロックしか握らない**ので、account のロックを
     見るだけの 1〜4 には出なかった——10 分ごとの採取が走っていても
     board は「止まっている」と同じ顔をしていた。
  6. **`run` を `collect` と二重に数えない**（`thth run` は repo と account の
     両方を握る。両方握られていれば run の側だけに出す）。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from tests.conftest import BIN_THTH, run_thth, write_queue_file
from thth import accounts as accounts_mod
from thth import lock as lock_mod

HOLD_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "helpers", "hold_lock.py")


def _hold(account_name, seconds, which="account"):
    holder = subprocess.Popen(
        [sys.executable, HOLD_LOCK, account_name, str(seconds), which],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
    assert holder.stdout.readline().strip() == "locked"
    return holder


def _hold_until_released(account_name):
    """Keep the lock until the parent closes stdin, regardless of CLI latency."""
    code = """
import sys
from thth import accounts, lock
held = lock.AccountLock(accounts.account_lock_path_for(sys.argv[1]))
held.acquire()
try:
    print("locked", flush=True)
    sys.stdin.read()
finally:
    held.release()
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", code, account_name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=dict(os.environ),
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert holder.stdout.readline().strip() == "locked"
    return holder


def _release(holder):
    try:
        # communicate closes stdin: that EOF is the explicit release event.
        stdout, stderr = holder.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.communicate(timeout=5)
        raise
    assert holder.returncode == 0, stdout + stderr


def _stop(holder):
    holder.terminate()
    holder.wait(timeout=5)


def test_collect_中はboardが採取の1行を足す(isolated_account):
    """`collect` は repo のロックだけを握る（引継ぎ 2026-09-15 §3-D）。"""
    name = isolated_account["name"]
    holder = _hold(name, 5, "repo")
    try:
        r = run_thth(["board"])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert f"いま collect が走っています（{name}）" in r.stdout, r.stdout
        # **run とは言わない**（account のロックは握られていない）。
        assert "いま run が走っています" not in r.stdout, r.stdout

        j = run_thth(["board", "--json"])
        summary = json.loads(j.stdout)
        assert summary["collecting"] == [name], summary["collecting"]
        assert summary["running"] == [], summary["running"]
        行 = [row for row in summary["accounts"] if row["account"] == name][0]
        assert 行["repo_running"] is True
        assert 行["repo_running_pid"] == holder.pid, (行["repo_running_pid"], holder.pid)
        assert 行["running"] is False
    finally:
        _stop(holder)


def test_collect_の1行は放したあと出ない(isolated_account):
    name = isolated_account["name"]
    _stop(_hold(name, 30, "repo"))
    r = run_thth(["board"])
    assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
    assert "いま collect が走っています" not in r.stdout, r.stdout
    summary = json.loads(run_thth(["board", "--json"]).stdout)
    assert summary["collecting"] == [], summary["collecting"]


def test_run_中はcollectと二重に数えない(isolated_account):
    """`thth run` は repo と account の両方を握る。**run の側にだけ出す。**"""
    name = isolated_account["name"]
    repo_holder = _hold(name, 5, "repo")
    account_holder = _hold(name, 5, "account")
    try:
        r = run_thth(["board"])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert f"いま run が走っています（{name}）" in r.stdout, r.stdout
        assert "いま collect が走っています" not in r.stdout, r.stdout
        summary = json.loads(run_thth(["board", "--json"]).stdout)
        assert summary["running"] == [name]
        assert summary["collecting"] == [], summary["collecting"]
    finally:
        _stop(account_holder)
        _stop(repo_holder)


def test_run_中はboardが1行足す(isolated_account):
    name = isolated_account["name"]
    holder = _hold(name, 5)
    try:
        r = run_thth(["board"])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert f"いま run が走っています（{name}）" in r.stdout, r.stdout

        j = run_thth(["board", "--json"])
        summary = json.loads(j.stdout)
        assert summary["running"] == [name], summary["running"]
        行 = [row for row in summary["accounts"] if row["account"] == name][0]
        assert 行["running"] is True
        assert 行["running_pid"] == holder.pid, (行["running_pid"], holder.pid)
    finally:
        holder.wait(timeout=15)


def test_放したあとは走っているとは言わない(isolated_account):
    name = isolated_account["name"]
    holder = _hold(name, 0.2)
    holder.wait(timeout=15)
    time.sleep(0.2)

    r = run_thth(["board"])
    assert "いま run が走っています" not in r.stdout, r.stdout
    summary = json.loads(run_thth(["board", "--json"]).stdout)
    assert summary["running"] == [], summary["running"]


def test_boardはロックを奪わない(isolated_account):
    """**観測が対象を壊さない。** board を挟んでも、放した直後の `throw` は通る。

    flock を試して調べる形にしていたら、board が一瞬握るので、**そのあいだに
    始まった run が「既に実行中」で落ちる**。ここではその逆——board を何度も
    打っても、ロックの状態が変わらないことを見る。
    """
    write_queue_file(isolated_account["queue_dir"], "a.md")
    name = isolated_account["name"]
    lock_path = accounts_mod.account_lock_path_for(name)

    holder = _hold_until_released(name)
    try:
        for _ in range(3):
            run_thth(["board"])
        # board を 3 回挟んでも、握っているのは holder のまま。
        assert lock_mod.AccountLock.holder_pid(lock_path) == holder.pid
        # 握られているあいだは throw が弾かれる（board が奪っていない証拠）。
        blocked = run_thth(["throw", name])
        assert blocked.returncode == 1, blocked.stdout + blocked.stderr
    finally:
        _release(holder)

    # 放したあとは通る（board が握りっぱなしにしていない証拠）。
    assert lock_mod.AccountLock.holder_pid(lock_path) is None
    ok = run_thth(["throw", name])
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_握り主が死んでいたら走っているとは言わない(isolated_account, thth_root):
    """`kill -9` は flock を OS が放すが、**名乗り（pid）は残る**。

    生死を見ないと「永遠に実行中」と出続ける（mkdir 版の stale 判定と同じ理屈）。
    """
    name = isolated_account["name"]
    holder = _hold(name, 30)
    holder.send_signal(signal.SIGKILL)
    holder.wait(timeout=15)

    lock_path = accounts_mod.account_lock_path_for(name)
    # 名乗りそのものは残っている（＝この試験が「消えたから出ない」を見ていない）。
    with open(lock_path, encoding="utf-8") as f:
        assert f.read().strip() == str(holder.pid)

    assert lock_mod.AccountLock.holder_pid(lock_path) is None
    r = run_thth(["board"])
    assert "いま run が走っています" not in r.stdout, r.stdout


def test_自己更新のロックも見る(isolated_account, thth_root):
    """`_app.lock`（`selfupdate._pull_locked()`）。**アカウント別ではない。**"""
    lock = lock_mod.AccountLock(accounts_mod.app_lock_path())
    lock.acquire()
    try:
        r = run_thth(["board"])
        assert "いま自己更新が走っています" in r.stdout, r.stdout
        summary = json.loads(run_thth(["board", "--json"]).stdout)
        assert "_app" in summary["running"], summary["running"]
    finally:
        lock.release()

    summary = json.loads(run_thth(["board", "--json"]).stdout)
    assert "_app" not in summary["running"], summary["running"]
