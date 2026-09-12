"""`~/.config/thth/app.env`（`THREADS_APP_ID`・`THREADS_APP_SECRET`）の読み（設計 §3.1）。

**全アカウント共通の 1 本**。masaru が書く。ここは読むだけで、値を加工・出力しない
（呼び出し側の `thth/oauth.py` も値を標準出力に出さない）。
"""
from __future__ import annotations

import os

from . import secrets_fs

REQUIRED_KEYS = ("THREADS_APP_ID", "THREADS_APP_SECRET")


class AppEnvError(Exception):
    """app.env が無い・壊れている・項目が足りない。"""


def default_path() -> str:
    # THTH_APP_ENV_PATH はテスト用の隔離のみに使う（`accounts.THTH_APP_DIR` と同じ流儀）。
    # 未設定なら実運用どおり ~/.config/thth/app.env を見る。
    return os.environ.get("THTH_APP_ENV_PATH") or os.path.expanduser("~/.config/thth/app.env")


def _parse_env_file(path: str) -> dict:
    data = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            data[key] = value
    return data


def load_app_env(path: str | None = None, *, log=print) -> tuple[str, str]:
    """`(THREADS_APP_ID, THREADS_APP_SECRET)` を返す。無ければ `AppEnvError`。"""
    path = path or default_path()
    if not os.path.exists(path):
        raise AppEnvError(
            f"app.env が無い: {path}（運用者が ~/.config/thth/app.env に "
            "THREADS_APP_ID・THREADS_APP_SECRET を書く。設計 §3.1・§9）"
        )
    secrets_fs.ensure_mode_600(path, log=log)
    data = _parse_env_file(path)
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise AppEnvError(f"app.env に項目が足りません: {missing}（{path}）")
    return data["THREADS_APP_ID"], data["THREADS_APP_SECRET"]
