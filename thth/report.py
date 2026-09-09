"""`thth queue` / `thth board`（発注 §3・受け入れ 4）。CLI と MCP の両方から呼ばれる。"""
from __future__ import annotations

import os

from . import accounts as accounts_mod
from . import core
from . import inflight as inflight_mod
from . import jst
from . import queuefile
from . import select as select_mod

STATUS_KEYS = ("draft", "approved", "posted", "withdrawn")


def _peek_next(files, account_name: str):
    """「次に出るのは何か・いつか」の案内。select_one と違い、いま出せるか
    （静かな時間帯・最短間隔・文字数・重複等）は見ない。approved かつ post_id 無し
    かつ publish_at が正しい形式のものの中で最も早い 1 件を「次の予定」として返す。
    """
    candidates = []
    for qf in files:
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("account") != account_name or fm.get("post_id"):
            continue
        if fm.get("status") != "approved":
            continue
        publish_at_raw = fm.get("publish_at")
        if not publish_at_raw or "+09:00" not in publish_at_raw:
            continue
        try:
            publish_at = queuefile.parse_publish_at(publish_at_raw)
        except ValueError:
            continue
        candidates.append((publish_at, qf.path))
    if not candidates:
        return None, None
    candidates.sort(key=lambda t: (t[0], os.path.basename(t[1])))
    publish_at, path = candidates[0]
    return os.path.basename(path), publish_at.isoformat()


def queue_summary(account_name: str | None) -> dict:
    """draft/approved/posted/型外 を数え、次に出るものと時刻を返す（アカウント別）。"""
    names = [account_name] if account_name else accounts_mod.list_account_names()
    out: dict = {}
    for name in names:
        try:
            account_cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError as e:
            out[name] = {"error": str(e)}
            continue
        files = core.list_queue_files(account_cfg)
        counts = {k: 0 for k in STATUS_KEYS}
        type_mismatch = 0
        for qf in files:
            if qf.front_matter.get("account") != name and not qf.malformed:
                continue
            if qf.malformed:
                type_mismatch += 1
                continue
            status = qf.front_matter.get("status")
            if status in counts:
                counts[status] += 1
        next_file, next_at = _peek_next(files, name)
        out[name] = {
            "counts": counts,
            "type_mismatch": type_mismatch,
            "next_file": next_file,
            "next_publish_at": next_at,
        }
    return out


def board_summary() -> dict:
    """アカウント・最終投稿・approved 待ち・inflight・型外の骨（設計 §4.6・T1 は骨だけ）。"""
    accounts_out = []
    for name in accounts_mod.list_account_names():
        try:
            account_cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError as e:
            accounts_out.append({"account": name, "error": str(e)})
            continue
        files = core.list_queue_files(account_cfg)
        last_at = core.last_post_at(files, name)
        approved_waiting = sum(
            1 for qf in files
            if not qf.malformed and qf.front_matter.get("account") == name
            and qf.front_matter.get("status") == "approved"
            and not qf.front_matter.get("post_id")
        )
        type_mismatch = sum(1 for qf in files if qf.malformed)
        state_dir = accounts_mod.state_dir_for(name)
        inflight = inflight_mod.read(state_dir)
        accounts_out.append({
            "account": name,
            "project": account_cfg.get("project"),
            "last_post_at": last_at.isoformat() if last_at else None,
            "approved_waiting": approved_waiting,
            "type_mismatch": type_mismatch,
            "inflight": inflight.get("file") if inflight else None,
        })
    return {"accounts": accounts_out, "generated_at": jst.iso()}
