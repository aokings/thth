"""``thth run`` の終わりに、VM の hub へ「脈」を 1 行置く（media-hub の依頼・
`設計_hubの脈_healthchecks卒業_2026-09-27.md` §2・§4）。

Healthchecks への ping と役割を分けない——同じ分岐（成功／`/fail` と同じとき）で
並べて打つだけの、もう 1 つの死活通知の口。hub 側が期待（`every_hours`・
`grace_hours`）を判定するので、ここでは間隔も判定も持たない。

**実体が無ければ黙って何もしない。** timer から呼ばれるときは非対話のシェルで
`~/.local/bin` が `PATH` に無いことがあるので、既定値はフルパスにしている。
"""
from __future__ import annotations

import subprocess

from . import accounts as accounts_mod

# VM 側の実体（timer からも通る、フルパス・masaru 決定 2026-09-27）。
DEFAULT_HUB_BIN = "/home/wt/.local/bin/hub"
ENV_KEY = "THTH_HUB_PULSE"
TIMEOUT_SECONDS = 10.0
# note は理由の区分と本数だけ（秘密・原稿名・本文を含めない）。
NOTE_LIMIT = 80


def _hub_bin() -> str:
    import os
    value = os.environ.get(ENV_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else DEFAULT_HUB_BIN


def _safe_note(note) -> str | None:
    if not isinstance(note, str):
        return None
    # 改行・制御文字は 1 行の note に混ざらないよう空白に落としてから詰める。
    flat = "".join(ch if ch.isprintable() else " " for ch in note)
    flat = " ".join(flat.split())
    return flat[:NOTE_LIMIT] if flat else None


def send(account: str, *, fail: bool = False, note: str | None = None,
          timeout: float = TIMEOUT_SECONDS) -> str:
    """``hub pulse thth-<account>`` を 1 回試す。run の rc には一切関わらない。

    返り値は runs／ログに残す 1 語:
    - ``pulse_sent``: 実行できた（hub 自身の判定・受理は問わない）
    - ``pulse_unavailable``: hub の実体が無い・実行できない（黙って何もしない側の記録）
    - ``pulse_invalid_account``: account 名が安全に使える形でない
    """
    try:
        accounts_mod.validate_name(account)
    except accounts_mod.AccountError:
        return "pulse_invalid_account"

    cmd = [_hub_bin(), "pulse", f"thth-{account}"]
    if fail:
        cmd.append("--fail")
    safe_note = _safe_note(note)
    if safe_note:
        cmd += ["--note", safe_note]

    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)
        return "pulse_sent"
    except (OSError, subprocess.SubprocessError):
        # 実体が無い（FileNotFoundError）・実行権限が無い・timeout も含めて、
        # ここでは「黙って何もしない」——run の rc も例外も一切変えない。
        return "pulse_unavailable"
