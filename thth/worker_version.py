"""常駐（`thth worker`）の版: 動いている版を残し、ディスクが動いたら自分で終わる。

設計 3.12.0 §6-1。release を VM が拾う仕組み（`thth run` の自己更新・`selfupdate`）は、
timer で走る短い process を exec しなおして新しい版にする。**常駐はその外にいた。**
09-25 06:05 に起動した worker は 3.11.1 の配布のあとも古い版のまま承認を拾わず、
手で restart するまで気づけなかった。

3.13.0 で常駐の名前を改めた（`thth approval-worker` → `thth worker`・unit
`thth-approval-worker.service` → `thth-worker.service`・記録 `approval-worker.json` →
`worker.json`）。unit の入れ替えは root の仕事なので自動ではしない。記録にどの unit で
動いているか（`unit`）を残し、古い unit のままなら `thth board` が入れ替えを促す。
旧い記録のファイルは、新しい記録が無いときだけ読む。

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
import re
import secrets

from . import __version__, _read_version, accounts, jst, selfupdate

# ディスクを見る間隔。release から常駐の入れ替えまでの遅れの上限（＋ systemd の RestartSec）。
CHECK_SECONDS = 30
# 読み込んだ版とディスクの版が違うので終わる（EX_TEMPFAIL）。0 以外なので Restart=on-failure で起こし直る。
EXIT_MOVED = 75
RECORD_NAME = "worker.json"
# 3.12.0 までの記録の名前（新しい記録が無いときだけ読む・書かない）。
LEGACY_RECORD_NAME = "approval-worker.json"
UNIT = "thth-worker.service"
# 3.12.0 までの unit の名前。これで動いていれば board が入れ替えを促す。
LEGACY_UNIT = "thth-approval-worker.service"
_UNIT_PATTERN = re.compile(r"/(thth-[A-Za-z0-9@._-]*\.service)$")


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


def record_path(name: str = RECORD_NAME) -> str:
    return os.path.join(accounts.thth_root(), "state", name)


def current_unit(path: str = "/proc/self/cgroup") -> str | None:
    """この process が systemd のどの unit で動いているか（Linux の cgroup から）。分からなければ None。"""
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read(65536).splitlines()
    except (OSError, ValueError):
        return None
    for line in lines:
        match = _UNIT_PATTERN.search(line.strip())
        if match:
            return match.group(1)
    return None


def record_start(start: dict, *, pid: int | None = None) -> bool:
    """起動した版を残す。書けなくても常駐は止めない（board が「記録なし」と言うだけ）。"""
    path = record_path()
    row = {"version": start.get("version"), "rev": start.get("rev"),
           "pid": os.getpid() if pid is None else pid, "started_at": jst.iso(), "unit": current_unit()}
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
    """新しい記録（`worker.json`）、無ければ 3.12.0 までの記録（`approval-worker.json`）。"""
    for name in (RECORD_NAME, LEGACY_RECORD_NAME):
        try:
            with open(record_path(name), encoding="utf-8") as stream:
                row = json.load(stream)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        return row if isinstance(row, dict) else None
    return None


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
    unit = row.get("unit") if isinstance(row.get("unit"), str) else None
    if state.get("alive") is False:
        return [f"常駐（worker）: 記録の pid {row.get('pid')} は動いていません"
                f"（最後に起動した版 {describe(row)}・{row.get('started_at')}）"]
    lines = [f"常駐（worker）: {describe(row)}  pid {row.get('pid')}・起動 {row.get('started_at')}"
             + (f"・unit {unit}" if unit else "")]
    if state.get("differs"):
        lines.append(f"  **ディスクの版は {describe(disk)} です——常駐は古い版で動いています**"
                     f"（{CHECK_SECONDS} 秒ごとに見て自分で終わり、systemd が起こし直します。"
                     f"戻らなければ sudo systemctl restart {(unit or UNIT).removesuffix('.service')}）")
    if unit == LEGACY_UNIT:
        lines.append(f"  **古い unit 名（{LEGACY_UNIT}）で動いています**——"
                     f"{UNIT} への入れ替えは docs/運用_招待する側.md §6 の手順で（自動ではしません）")
    return lines
