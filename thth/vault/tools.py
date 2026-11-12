"""monban の道具と git を探して呼ぶ 1 か所（段 1・仮・masaru の答えを待たずに推しで作った・朝に覆せる）。

- 探すのは `PATH` だけ（`shutil.which`）。試験は `PATH` の先頭に偽の実行ファイルを置く（本物の monban に繋がない）。
- 呼ぶときは**引数の並び**で（shell を通さない）。秘密を引数に置かない（monban-vault も引数で秘密を受けない）。
- 出力を取るのは符丁と数を読むためだけ。取った文は利用者に写さない（`wire.monban_code`・`wire.monban_json`）。
- `passthrough=True` は標準の入出力を端末へ直に繋ぐ（THTH は読まない）。戻しの文字列が出る `monban-vault init` と、
  それを入れる `join` の標準入力に使う。
"""
from __future__ import annotations

import os
import shutil
import subprocess

from . import INSTALL_HINT  # noqa: F401 — 断りの文と同じ入れ方
from .wire import VaultRefused

MONBAN_VAULT = "monban-vault"
GIT_REMOTE = "git-remote-monban"
GIT = "git"
TIMEOUT = 600


def which(name: str) -> str | None:
    return shutil.which(name)


def need(name: str, family: str = "sync") -> str:
    """道具の道。無ければ前向きな断り（入れ方を言う）。"""
    found = which(name)
    if found is None:
        raise VaultRefused(f"{family}_tool_missing", name)
    return found


def env() -> dict:
    """子の環境: git が端末に問わない・編集の画面を開かない（`thth sync` は人の手を待たない）。"""
    out = dict(os.environ)
    out["GIT_TERMINAL_PROMPT"] = "0"
    out["GIT_EDITOR"] = "true"
    out["GIT_MERGE_AUTOEDIT"] = "no"
    out["LC_ALL"] = "C"  # git の印（fetch first など）を英語の形で読む
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        out.pop(name, None)  # --repo と違う repo を触らない（監査 段 1 の気づき）
    return out


def run(argv: list, *, cwd: str | None = None, passthrough: bool = False, stdin_passthrough: bool = False,
        family: str = "sync"):
    """`(rc, stdout, stderr)`。passthrough なら出力を取らない（stdout・stderr は空の文字列）。

    stdin_passthrough は標準入力だけを端末へ繋ぐ（出力は取る）。どちらでもなければ標準入力は空。
    """
    try:
        if passthrough:
            done = subprocess.run(argv, cwd=cwd, env=env(), timeout=TIMEOUT, check=False)
            return done.returncode, "", ""
        done = subprocess.run(argv, cwd=cwd, env=env(), timeout=TIMEOUT, check=False,
                              stdin=None if stdin_passthrough else subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace")
    except FileNotFoundError:
        raise VaultRefused(f"{family}_tool_missing", os.path.basename(str(argv[0]))) from None
    except subprocess.TimeoutExpired:
        raise VaultRefused(f"{family}_unavailable", "timeout") from None
    return done.returncode, done.stdout or "", done.stderr or ""

