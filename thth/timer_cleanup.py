"""退出した account の systemd unit を止める（3.5.1 件 1・報告の口 r20260923-653324b9）。

**なぜ要るか。** 実測（09-23 12:48）: kopicha-server-mastodon の `thth account leave`
が完了しても `thth@kopicha-server-mastodon.timer` は active のまま 10 分ごとに
空回りし（`account_stopped`）、`thth admin timers` の記録（`state/_admin/timers.json`）
にも残った。運用者が手で `sudo systemctl disable --now` した。退出は台帳・秘密・
state を消すが、unit は VM の `/etc/systemd/system` にあって道具の持ち物ではない
——消す段の外に落ちていた。

**sudo は 1 回だけ・`-n`（パスワードを訊かない）で試す。** VM では `wt` に
passwordless sudo があり、運用者はこれまで同じ命令で止めている。失敗（sudo が無い・
パスワードが要る・unit が無い）でも**退出は完了のまま**にする——台帳と秘密はもう
消えていて、timer は空回りするだけで何も出さない。止められなかった unit は記録に
残し、打てる命令をそのまま 1 行出す（運用者が貼って打てば終わる）。

**unit が無ければ何も呼ばない**（`systemctl list-unit-files` で確かめる）。sudo を
無駄に叩かない・「止めた」と言わない。

**subprocess と `shutil.which` はここで束ねて注入の口にする。** 試験は本物の
sudo・systemctl を呼ばない（`_run`・`_which` を差し替える）。import 時に束ねるのは、
他の試験が `subprocess.run` を丸ごと差し替えても、この口が巻き込まれないため。
"""
from __future__ import annotations

import shutil
import subprocess

from . import accounts

_run = subprocess.run
_which = shutil.which

# 記録（leave の `row['timers']`）の理由コード。None は「止めた」。
REASONS = ("no_units", "systemctl_unavailable", "unit_listing_failed",
           "sudo_unavailable", "sudo_failed")

_PREFIXES = ("thth@", "thth-collect@")
_SUFFIXES = (".timer", ".service")
LIST_PATTERNS = ("thth@*.timer", "thth-collect@*.timer", "thth-collect@*.service")


def account_units(name: str) -> tuple:
    """account ごとの unit（投稿の timer・採集の timer・採集の service）。

    `thth-maintain.timer` は account 別ではない（全 account を見て回る）ので含めない。
    `thth@<name>.service` は timer から起こされる oneshot で、timer を止めれば起きない。
    """
    return (f"thth@{name}.timer", f"thth-collect@{name}.timer",
            f"thth-collect@{name}.service")


def instance_of(unit) -> str | None:
    """`thth@<name>.timer` などから account 名。account 別の unit でなければ None。"""
    if not isinstance(unit, str):
        return None
    for prefix in _PREFIXES:
        for suffix in _SUFFIXES:
            if unit.startswith(prefix) and unit.endswith(suffix):
                name = unit[len(prefix):-len(suffix)]
                if (name and accounts.name_is_safe(name)
                        and unit in account_units(name)):
                    return name
    return None


def stop_command(units) -> str:
    """運用者がそのまま打てる 1 行（unit 名は `instance_of` を通ったものだけ）。"""
    return "sudo systemctl disable --now " + " ".join(units)


def _listed(patterns) -> tuple:
    """`systemctl list-unit-files` に出た `{名前: 状態}` と理由。呼べなければ `(None, 理由)`。"""
    if _which("systemctl") is None:
        return None, "systemctl_unavailable"
    try:
        proc = _run(["systemctl", "list-unit-files", "--no-legend", "--no-pager", *patterns],
                    capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, "unit_listing_failed"
    names = {}
    for line in (proc.stdout or "").splitlines():
        fields = line.split()
        if fields:
            names[fields[0]] = fields[1] if len(fields) > 1 else None
    # 一致が無いときも非ゼロで終わる（標準エラーは空）。標準エラーに何か出ていて、
    # 1 つも拾えていなければ「確かめられなかった」——「無い」と言わない。
    if proc.returncode and not names and (proc.stderr or "").strip():
        return None, "unit_listing_failed"
    return names, None


def stop_for_leave(name: str) -> dict:
    """退出した account の unit を `sudo -n` で 1 回だけ止める。記録の形を返す。

    `{disabled: [...], pending: [...], reason}`。reason が None なら止めた。
    """
    units = account_units(name)
    listed, reason = _listed(units)
    if listed is None:
        return {"disabled": [], "pending": [], "reason": reason}
    present = [unit for unit in units if unit in listed]
    if not present:
        return {"disabled": [], "pending": [], "reason": "no_units"}
    if _which("sudo") is None:
        return {"disabled": [], "pending": present, "reason": "sudo_unavailable"}
    try:
        proc = _run(["sudo", "-n", "systemctl", "disable", "--now", *present],
                    capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        stopped = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        stopped = False
    if not stopped:
        return {"disabled": [], "pending": present, "reason": "sudo_failed"}
    return {"disabled": present, "pending": [], "reason": None}


def valid_record(value, name) -> bool:
    """leave の記録の `timers` の形（この account の unit 名だけ・静的な理由）。"""
    if type(value) is not dict or set(value) != {"disabled", "pending", "reason"}:
        return False
    if value["reason"] is not None and value["reason"] not in REASONS:
        return False
    allowed = set(account_units(name))
    return all(type(value[key]) is list and all(unit in allowed for unit in value[key])
               for key in ("disabled", "pending"))


def orphans(ledger_names, *, cached=None, leave_rows=None, live=True, only=None) -> dict:
    """台帳の無い account の unit（`orphan_unit`）。止める命令を添える。

    出所は 3 つ: この機械の `systemctl list-unit-files`（`live=True` のときだけ）・
    前回の `admin timers` の記録（timers.json）・退出の記録の `pending`。
    `only` を渡すとその account だけ。
    """
    ledger = set(ledger_names)
    found: dict = {}

    def add(unit, source):
        name = instance_of(unit)
        if name is None or name in ledger or only is not None and name != only:
            return
        entry = found.setdefault(name, {"units": set(), "sources": set()})
        entry["units"].add(unit)
        entry["sources"].add(source)

    reason = None
    if live:
        listed, reason = _listed(LIST_PATTERNS)
        for unit, state in sorted((listed or {}).items()):
            # 止めた後も unit file は残る（`disabled`）。止まっているものは orphan と言わない。
            if state not in ("disabled", "masked"):
                add(unit, "systemd")
    nodes = (cached or {}).get("by_account") if isinstance(cached, dict) else None
    for name, node in (nodes.items() if isinstance(nodes, dict) else ()):
        units = node.get("units") if isinstance(node, dict) else None
        for unit in units if isinstance(units, list) else ():
            # 読み込まれていなかった unit（`timer_not_loaded`）は在ったと言えない。
            if isinstance(unit, dict) and unit.get("active") is not None:
                add(unit.get("unit"), "timers_snapshot")
    for name, record in (leave_rows or {}).items():
        for unit in ((record or {}).get("pending") or []):
            add(unit, "leave_pending")
    rows = []
    for name in sorted(found):
        units = [unit for unit in account_units(name) if unit in found[name]["units"]]
        rows.append({"account": name, "units": units, "reason": "orphan_unit",
                     "sources": sorted(found[name]["sources"]), "command": stop_command(units)})
    return {"orphans": rows, "orphan_listing_reason": reason if live else "not_observed_here"}

