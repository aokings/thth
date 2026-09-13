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
        self._write_pid(fd)

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
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return None
        return pid if pid > 0 else None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except PermissionError:
            # **居るが、こちらに信号を送る権限が無い**（別の利用者のプロセス）。
            # 「居ない」ではない——奪う側でも観る側でも、安全なのは「居る」。
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _write_pid(fd: int) -> None:
        """握った直後に**自分の pid を中身として書く**（`holder_pid()` が読む）。

        flock の保持者は OS しか知らない。**読み取りだけで「いま誰かが握って
        いるか」を答えるには、握っている側が名乗るしかない**（`thth board` が
        知るため・2026-09-13）。flock を試して調べる形にすると、**観るだけの
        board が一瞬ロックを取り、そのあいだに始まった `thth run` が
        「既に実行中」で落ちる**——観測が対象を壊す。

        中身は pid の 10 進表記だけ。**flock の意味は変わらない**（中身は
        誰も読まない・追記もしない）。
        """
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except OSError:
            pass    # 名乗れなくてもロックは有効（board が「判らない」になるだけ）

    def release(self) -> None:
        if self._fd is not None:
            try:
                # 放す前に名乗りを消す（**残すと、死んでもいないのに古い pid が
                # 残る**）。pid が生きているかは `holder_pid()` も見るので二重の網。
                os.ftruncate(self._fd, 0)
            except OSError:
                pass
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

    @classmethod
    def holder_pid(cls, lock_path: str) -> int | None:
        """そのロックを**いま握っている**プロセスの pid（読み取りだけ・**握らない**）。

        `None` は「握っている者を見つけられない」——**「誰も握っていない」の
        証明ではない**（名乗りの書き込みに失敗した・古い版が握っている・pid が
        読めない、でも `None` になる）。呼び手はこれを「走っていない」と言い換えて
        はいけない（`thth board` は**見つけたときだけ**1 行足す）。

        判定は 2 段:

        1. 中身（または mkdir 版の `pid` ファイル）から pid を読む。
        2. **その pid が生きているか**を見る。`kill -9` されると flock は OS が
           放すのに名乗りだけ残るので、生死を見ないと「永遠に実行中」になる
           （mkdir 版の stale 判定と同じ理屈）。

        **pid の使い回し**までは見分けられない（別のプロセスが同じ pid を得て
        いれば「握っている」と答える）。**L3**——ここは近似だと明記しておく。
        """
        pid = cls._read_pid(os.path.join(lock_path + ".d", "pid"))
        if pid is None:
            pid = cls._read_pid(lock_path)
        if pid is None or not cls._pid_alive(pid):
            return None
        return pid

    @classmethod
    def is_held(cls, lock_path: str) -> bool:
        """`holder_pid()` が見つかったか。**「見つからない＝空いている」ではない。**"""
        return cls.holder_pid(lock_path) is not None

    def __enter__(self) -> "AccountLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False
