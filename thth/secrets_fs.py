"""秘密ファイルの読み書き（`app.env`・`<account>.token`）に共通の作法。

- パーミッションは常に 600。作成時はもちろん、既存ファイルの読み込み時にも
  600 でなければ警告して直す（設計「masaru が実際に叩く」節・T2a の指示）。
- 書き込みは一時ファイル ＋ `os.replace` で原子的に行う（読み手が半端な内容を
  見ない・crash-safe）。一時ファイルも書いた直後に 600 へ絞ってから rename する
  （rename までの一瞬でも他ユーザーに読めるパーミッションを作らない）。
"""
from __future__ import annotations

import json
import os
import stat
import tempfile


def ensure_mode_600(path: str, *, log=print) -> None:
    """`path` が 600 でなければ警告して直す。存在しなければ何もしない。"""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return
    if mode != 0o600:
        log(f"警告: {path} のパーミッションが {oct(mode)} です。600 に直します。")
        os.chmod(path, 0o600)


def atomic_write_json(path: str, data: dict, *, mode: int = 0o600) -> None:
    """`data` を JSON として `path` に原子的に書く（一時ファイル ＋ `os.replace`）。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".thth-tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
