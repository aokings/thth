"""承認の常駐（`thth approval-worker`）の版: 動いている版を残し、ディスクが動いたら自分で終わる。

設計 3.12.0 §6-1。release を VM が拾う仕組み（`thth run` の自己更新・`selfupdate`）は、
timer で走る短い process を exec しなおして新しい版にする。**常駐はその外にいた。**
09-25 06:05 に起動した worker は 3.11.1 の配布のあとも古い版のまま承認を拾わず、
手で restart するまで気づけなかった。

ここでは:
  - 常駐が起動したときに、読み込んだ版（`VERSION` と commit）を state に 1 件残す
    （`thth board` がそれを読み、ディスクの版と違えば知らせる）。
  - 常駐は `CHECK_SECONDS` ごとにディスクの版を見て、読み込んだ版と違えば
    `EXIT_MOVED` で終わる。unit は `Restart=on-failure` なので systemd が新しい版で
    起こし直す（root の権限も systemctl も要らない）。
秘密は読まない・書かない（版と pid と時刻だけ）。
"""
from __future__ import annotations

import json
import os
import secrets

from . import __version__, _read_version, accounts, jst, selfupdate

# ディスクを見る間隔。release から常駐の入れ替えまでの遅れの上限（＋ systemd の RestartSec）。
CHECK_SECONDS = 30
# 読み込んだ版とディスクの版が違うので終わる（EX_TEMPFAIL）。0 以外なので Restart=on-failure で起こし直る。
EXIT_MOVED = 75
RECORD_NAME = "approval-worker.json"


def loaded() -> dict:
    """この process が読み込んだ版（import した時点の VERSION と commit）。"""
    return {"version": __version__, "rev": selfupdate.LOADED_REV}


def on_disk() -> dict:
    """いまディスクにある版（VERSION を読み直し、HEAD を見る）。"""
    return {"version": _read_version(), "rev": selfupdate.head()}


def moved(start: dict, now: dict) -> bool:
    """両方で読めた欄のどれかが違えば True（読めない欄では言わない）。"""
    for key in ("version", "rev"):
        a, b = (start or {}).get(key), (now or {}).get(key)
        if a and b and a != b:
            return True
    return False


def describe(row: dict) -> str:
    rev = (row or {}).get("rev")
    return f"{(row or {}).get('version') or '版不明'}（{rev[:7] if isinstance(rev, str) else 'commit 不明'}）"


def record_path() -> str:
    return os.path.join(accounts.thth_root(), "state", RECORD_NAME)


def record_start(start: dict, *, pid: int | None = None) -> bool:
    """起動した版を残す。書けなくても常駐は止めない（board が「記録なし」と言うだけ）。"""
    path = record_path()
    row = {"version": start.get("version"), "rev": start.get("rev"),
           "pid": os.getpid() if pid is None else pid, "started_at": jst.iso()}
    temp = f"{path}.{secrets.token_hex(8)}.tmp"
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(row, stream, ensure_ascii=False)
        os.replace(temp, path)
        return True
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        return False


def read_record() -> dict | None:
    try:
        with open(record_path(), encoding="utf-8") as stream:
            row = json.load(stream)
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def _alive(pid) -> bool | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def status() -> dict | None:
    """board 用: 記録された常駐の版・ディスクの版・違うか・pid が生きているか。記録が無ければ None。"""
    row = read_record()
    if row is None:
        return None
    disk = on_disk()
    return {"running": row, "disk": disk, "differs": moved(row, disk), "alive": _alive(row.get("pid"))}


def board_lines(state: dict | None) -> list:
    """`thth board` の人向けの行。記録が無ければ何も言わない（常駐を置かない機械もある）。"""
    if not state:
        return []
    row, disk = state.get("running") or {}, state.get("disk") or {}
    if state.get("alive") is False:
        return [f"承認の常駐: 記録の pid {row.get('pid')} は動いていません"
                f"（最後に起動した版 {describe(row)}・{row.get('started_at')}）"]
    lines = [f"承認の常駐: {describe(row)}  pid {row.get('pid')}・起動 {row.get('started_at')}"]
    if state.get("differs"):
        lines.append(f"  **ディスクの版は {describe(disk)} です——常駐は古い版で動いています**"
                     f"（{CHECK_SECONDS} 秒ごとに見て自分で終わり、systemd が起こし直します。"
                     f"戻らなければ sudo systemctl restart thth-approval-worker）")
    return lines
