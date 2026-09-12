"""THTH（ThreadsThrower）core パッケージ。"""
from __future__ import annotations

import os as _os

# **`VERSION` は 1 か所だけ**（設計 v1.0.0・Track C1）。`thth --version` と
# `thth board` の先頭がここを読む。パッケージと同じディレクトリに置く
# （`thth/accounts.py` の `APP_DIR` と同じ、`__file__` 基準の流儀）。
_VERSION_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "VERSION")


def _read_version() -> str:
    try:
        with open(_VERSION_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        # **VERSION が無くても import 自体は落とさない**（既存の 0.x には
        # 無かった・設計 §0「いまの本番は版を持たない」）。
        return "0.0.0"


__version__ = _read_version()
