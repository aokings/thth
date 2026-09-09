"""ロック。**core の入口に置く**（発注 §0-1・設計 §3.7）。

timer・薄い MCP・手打ちの、どの入口から `thth throw`（および `thth run` の中身）を
呼んでも同じロックを踏むように、CLI のサブコマンド層でなく `thth.core.throw_once()`
の中でロックを確保する。ラッパ（`bin/thth-run`）や CLI 引数解析にロックを置くと、
入口を増やしたときにロックを踏まない経路ができてしまう
（設計 §3.7 で見つかったバグ・受け入れ 9 番の主目的）。

`fcntl.flock` は Mac・Linux どちらの CPython でも動く（flock(2) 自体は BSD 系にも
ある。無いのは bash の `flock(1)` コマンドの方で、Python から直接 syscall を叩く
ここでは関係ない）。それでも `fcntl` が無い環境向けに、watchtower `bin/wt-run` の
mkdir ロック（プロセスの生死を pid ファイルで確認し、死んでいれば stale として
奪う）を写して fallback に置く。
"""
from __future__ import annotations

import os


class LockBusy(Exception):
    """ロックがすでに別プロセスに取られている。"""


class AccountLock:
    """1 アカウント分の実行ロック。`with AccountLock(path):` で使う。

    `acquire()` は非ブロッキング（`flock(..., LOCK_NB)` 相当）で、取れなければ
    即座に `LockBusy` を投げる。設計 §3.3 の「timer 実行中に直に呼ぶと待たされる」は
    実装としては「即座に弾かれる」を採る（watchtower `wt-run` と同じ選択・§6.1）。
    """

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fd: int | None = None
        self._mkdir_path: str | None = None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        try:
            import fcntl
        except ImportError:
            self._acquire_mkdir()
            return
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise LockBusy(f"ロック取得失敗（flock）: {self.lock_path}")
        self._fd = fd

    def _acquire_mkdir(self) -> None:
        lock_dir = self.lock_path + ".d"
        pid_path = os.path.join(lock_dir, "pid")
        try:
            os.mkdir(lock_dir)
        except FileExistsError:
            pid = self._read_pid(pid_path)
            alive = pid is not None and self._pid_alive(pid)
            if pid is None or alive:
                # pid が読めない場合は「生きている」とみなして保守的に扱う（安全側）。
                raise LockBusy(f"ロック取得失敗（mkdir）: {lock_dir}")
            # stale: 前の保持者が死んでいるので奪う。
            try:
                os.remove(pid_path)
            except OSError:
                pass
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass
            try:
                os.mkdir(lock_dir)
            except FileExistsError:
                raise LockBusy(f"ロック取得失敗（mkdir・競合）: {lock_dir}")
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        self._mkdir_path = lock_dir

    @staticmethod
    def _read_pid(pid_path: str) -> int | None:
        try:
            with open(pid_path, encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            os.close(self._fd)
            self._fd = None
        if self._mkdir_path is not None:
            try:
                os.remove(os.path.join(self._mkdir_path, "pid"))
            except OSError:
                pass
            try:
                os.rmdir(self._mkdir_path)
            except OSError:
                pass
            self._mkdir_path = None

    def __enter__(self) -> "AccountLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False
