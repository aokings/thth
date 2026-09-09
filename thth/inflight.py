"""`state/<account>/inflight.json`（設計 §3.5）。

投稿は取り消せない。公開の**前**に書き、post_id を md に書き戻して push が成功して
**初めて**消す（push 失敗では消さない）。次の実行の冒頭でこれが残っていれば、
そのアカウントは何もしないで exit 1 にする（select.select_one を呼ばない）。
"""
from __future__ import annotations

import json
import os


def path_for(state_dir: str) -> str:
    return os.path.join(state_dir, "inflight.json")


def read(state_dir: str) -> dict | None:
    p = path_for(state_dir)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(state_dir: str, data: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    p = path_for(state_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def write(state_dir: str, *, file: str, started: str,
          container_id: str | None = None, post_id: str | None = None) -> None:
    _write(state_dir, {
        "file": file,
        "started": started,
        "container_id": container_id,
        "post_id": post_id,
    })


def update(state_dir: str, **fields) -> None:
    data = read(state_dir) or {}
    data.update(fields)
    _write(state_dir, data)


def clear(state_dir: str) -> None:
    p = path_for(state_dir)
    if os.path.exists(p):
        os.remove(p)
