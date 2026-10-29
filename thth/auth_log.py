"""`thth auth` の既定のログ（10/29 段 0・設計 MetaAPI 計画 段 0 の 10）。

10/29、VM で認可のログを打った人が `> /tmp/...` に向けたら途中で消え、何が起きたか後から読めなかった
（原因は追わない）。**道具の側で決まった置き場を持つ**: `$THTH_ROOT/state/_auth/<口座>-<日時>.log`。
画面には最初に「ログ: <パス>」を 1 行出し、以後の行は画面とログの両方に出す。

- 書くのは**画面に出したのと同じ行だけ**（`redact` を通した後の文）。鍵・secret・code の値は
  `redact` の登録済みの値として伏せられる——ここで新しく何かを読むことはない。
- 置き場は 0700、ファイルは 0600（認可の URL の `state` を含むので他人に読ませない）。
- ログを開けなくても認可は止めない（画面に「ログを書けません」と 1 行出して、画面だけで続ける）。
  ログは控えであって、認可の条件ではない。
"""
from __future__ import annotations

import contextlib
import os
import re
import sys

from . import accounts as accounts_mod
from . import jst
from . import redact as redact_mod

DIRNAME = "_auth"
# 口座名はファイル名になる（`accounts.load_account` が形を縛るが、ここでも区切りを通さない）。
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def directory() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", DIRNAME)


def path_for(account: str, now=None) -> str:
    stamp = (now or jst.now_jst()).strftime("%Y%m%d-%H%M%S")
    return os.path.join(directory(), f"{_SAFE.sub('_', account) or 'account'}-{stamp}.log")


class Tee:
    """画面（`print`）とログのファイルの両方へ同じ行を出す。"""

    def __init__(self, handle):
        self._handle = handle

    def _write(self, line):
        if self._handle is None:
            return
        try:
            self._handle.write(redact_mod.redact(str(line)) + "\n")
            self._handle.flush()
        except OSError:
            self._handle = None

    def log(self, line):
        print(line)
        self._write(line)

    def human_output(self, line):
        print(line)
        self._write(line)


@contextlib.contextmanager
def open_for(account: str, *, now=None, write: bool = True):
    """ログのファイルを開いて `Tee` を渡す。開けなければ（または `write=False`）画面だけの `Tee`。"""
    handle = None
    if not write:
        yield Tee(None)
        return
    path = path_for(account, now=now)
    try:
        os.makedirs(directory(), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except OSError as exc:
        print(f"ログを書けません（{type(exc).__name__}）。画面だけで続けます", file=sys.stderr)
    else:
        print(f"ログ: {path}", file=sys.stderr)
    try:
        yield Tee(handle)
    finally:
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
