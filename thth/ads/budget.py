"""広告の月の上限の消費（計画 D-3・段 5 の 1）。**見積もりで、Meta の請求書ではない。**

置き場: `adaccount_state/<名前>/budget-<YYYY-MM>.json`（0600）。月は**広告アカウントの時間帯**
（台帳の `timezone_name`）で数える（X の本数の UTC の月とは別・B-9）。

形:

```
{"version": 1, "month": "2026-11", "reserved_minor": 500,
 "reservations": {"<op_id>": {"state": "fired", "amount_minor": 500, "campaign_id": "…",
                               "end_time": "…", "op": "activate", "at": "…"}}}
```

状態機械（X の予算 `budget_x.py` と同じ型）: **予約（reserved）→ 発射（fired）／結果不明（unknown）／
取り消し（released）**。`activate` は通しの予算の全額を、`extend` は増やした差分を予約する。
Meta が確かに断った（何も ACTIVE にならなかった）ときだけ `released` に落とす。結果不明は使ったものとして残す。

**月の消費**（`usage()`・監査 段 5 の 1-1 で書き直した）: **実支出で縛る**（D-3「月の照合は insights の spend を
月の範囲で取る」）。

    使った見積もり = 口座の今月の実支出の全部（THTH が始めた物も外の物も・前の月から続く配信の今月の分も）
                   ＋ まだ終わっていない予約の残りの見込み（キャンペーンごとに 予約の合計 − 今月の実支出・負なら 0）

- 予約は**どの月のファイルの物も**見る（月末に始めて翌月に続く配信の残りを、翌月にも数える）。終わった予約は
  実支出だけが残る（確定の時期は「読むとき」。実支出は Meta の数が正本なので、確定のために台帳を書き換えない）。
- 同じキャンペーンの予約（activate と extend）はキャンペーンごとにまとめて 1 回だけ引く（実支出を重ねて数えない）。
- 残りの見込みは「予約の合計 − 今月の実支出」なので、前の月に使った分も見込みに残る（多く数える側・安全側）。
- キャンペーンごとの今月の実支出（`insights level=campaign`）が読めなければ `outside_unknown` を立て、
  `activate`・`extend` は `ads_spend_unknown` で断る（分からないものを 0 と数えない）。

月の上限 `ceilings.monthly` が 0 なら、どの予約も通らない（`ads_budget_exhausted`・既定は止めた状態）。
上限を上げる口は THTH に無い（VM の台帳を直接書く・D-1 の 3）。下げるのは `thth ads budget lower --monthly`。
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import re
import secrets
import time

from .. import jst, secrets_fs
from . import ledger

VERSION = 1
LIVE_STATES = ("reserved", "fired", "unknown")
STATES = LIVE_STATES + ("released",)
LOCK_FILE = "lock"


class BudgetRefused(ValueError):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def path_for(name: str, month: str) -> str:
    return os.path.join(ledger.state_dir_for(name), f"budget-{month}.json")


def previous_month(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def load(name: str, month: str) -> dict:
    """読めなければ断る（壊れた予約を 0 と読まない）。無ければ空。"""
    try:
        with open(path_for(name, month), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"version": VERSION, "month": month, "reserved_minor": 0, "reservations": {}}
    except (OSError, ValueError) as exc:
        raise BudgetRefused("ads_budget_unreadable", f"budget-{month}.json を読めません") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("reservations"), dict)
            or any(not isinstance(v, dict) or v.get("state") not in STATES
                   or not isinstance(v.get("amount_minor"), int) for v in data["reservations"].values())):
        raise BudgetRefused("ads_budget_unreadable", f"budget-{month}.json の形が違います")
    return data


def _save(name: str, data: dict) -> None:
    data["reserved_minor"] = sum(v["amount_minor"] for v in data["reservations"].values()
                                 if v["state"] in LIVE_STATES)
    ledger.ensure_state_dir(name)
    secrets_fs.atomic_write_json(path_for(name, data["month"]), data, mode=0o600)


@contextlib.contextmanager
def lock(name: str):
    """作る・始める・延ばすの排他（待たない。取れなければ `ads_busy`）。止める口は取らない。"""
    directory = ledger.ensure_state_dir(name)
    fd = os.open(os.path.join(directory, LOCK_FILE), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetRefused("ads_busy", "ほかの書く操作が走っています") from None
        yield
    finally:
        os.close(fd)


def new_op_id(now=None) -> str:
    now = now or jst.now_jst()
    # 並びは打った順（同じ秒でも `time_ns` で後の物が後ろ・`ops.stop_unconfirmed` が最後の stop を選ぶ）。
    return f"{now.strftime('%Y%m%dT%H%M%S')}-{time.time_ns():020d}-{secrets.token_hex(2)}"


def reserve(name: str, month: str, op_id: str, *, amount_minor: int, campaign_id: str, end_time: str | None,
            op: str, now=None) -> None:
    data = load(name, month)
    data["reservations"][op_id] = {"state": "reserved", "amount_minor": int(amount_minor),
                                   "campaign_id": campaign_id, "end_time": end_time, "op": op,
                                   "at": jst.iso(now)}
    _save(name, data)


def mark(name: str, month: str, op_id: str, state: str) -> None:
    if state not in STATES:
        raise ValueError("invalid_budget_state")
    data = load(name, month)
    row = data["reservations"].get(op_id)
    if row is None:
        return
    row["state"] = state
    _save(name, data)


def months(name: str) -> list:
    """予約のファイルのある月（古い順）。"""
    try:
        entries = os.listdir(ledger.state_dir_for(name))
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.startswith("budget-") and entry.endswith(".json"):
            month = entry[len("budget-"):-len(".json")]
            if len(month) == 7 and month[4] == "-" and month.replace("-", "").isdigit():
                out.append(month)
    return sorted(out)


def _end_of(row):
    value = row.get("end_time")
    if not isinstance(value, str):
        return None
    return jst.parse(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip()))


def running_reservations(name: str, *, now) -> dict:
    """まだ終わっていない生きた予約の合計（`{campaign_id: 額}`・どの月のファイルの物も）。

    終了日が読めない予約は終わっていない側に数える（安全側）。読めない予約のファイルは断る。
    """
    out = {}
    for month in months(name):
        data = load(name, month)
        for row in data["reservations"].values():
            if row["state"] not in LIVE_STATES:
                continue
            end = _end_of(row)
            if end is not None and end <= now:
                continue
            cid = row.get("campaign_id") if isinstance(row.get("campaign_id"), str) else "?"
            out[cid] = out.get(cid, 0) + row["amount_minor"]
    return out


def usage(name: str, cfg: dict, *, now, campaign_spend: dict | None) -> dict:
    """今月の消費の見立て（最小単位）。`campaign_spend` は `{campaign_id: 今月の実支出}`（読めなければ None）。"""
    from . import watch
    month = watch.local_month(cfg, now)
    running = running_reservations(name, now=now)
    spend = {k: v for k, v in (campaign_spend or {}).items() if isinstance(v, int)}
    actual = sum(spend.values()) if campaign_spend is not None else None
    remaining = sum(max(0, amount - spend.get(cid, 0)) for cid, amount in running.items())
    outside = (sum(v for k, v in spend.items() if k not in running) if campaign_spend is not None else None)
    monthly = ledger.ceilings(cfg).get("monthly") or 0
    used = remaining + (actual or 0)
    return {"month": month, "monthly": monthly, "reserved_minor": remaining, "actual_minor": actual,
            "outside_minor": outside, "outside_unknown": campaign_spend is None, "used_minor": used,
            "remaining_minor": monthly - used,
            "unit": "最小単位の整数（JPY なら円）・今月の実支出＋終わっていない予約の残り・見積もりで請求書ではない"}


def check(view: dict, amount_minor: int) -> None:
    """予約してよいか。だめなら `BudgetRefused`（静的な符丁）。"""
    if view["monthly"] <= 0:
        raise BudgetRefused("ads_budget_exhausted",
                            "月の上限 ceilings.monthly が 0 です（既定は止めた状態。上げるのは VM の台帳を直接書く）")
    if view["outside_unknown"]:
        raise BudgetRefused("ads_spend_unknown",
                            "今月のキャンペーンごとの実支出が読めません（予約の外の支出を 0 と数えない）")
    if view["used_minor"] + amount_minor > view["monthly"]:
        raise BudgetRefused("ads_budget_exhausted",
                            f"月の上限 {view['monthly']} に対し、使った見積もり {view['used_minor']}＋今回 {amount_minor}"
                            "（最小単位）で越えます")


def campaign_spend_this_month(reader) -> dict | None:
    """`insights level=campaign this_month` → `{campaign_id: spend_minor}`。読めなければ None。"""
    from ..meta import errors as meta_errors
    from . import graphread
    try:
        rows = reader.insights("this_month", level="campaign")
    except (graphread.AdsReadError, meta_errors.RateLimited):
        return None
    out = {}
    for row in rows:
        cid, value = row.get("id"), row.get("spend_minor")
        if not isinstance(cid, str):
            continue
        if not isinstance(value, int):
            return None   # 直せない値（JPY 以外・端数）があれば分からないと言う
        out[cid] = out.get(cid, 0) + value
    return out


def days_between(start: datetime.datetime, end: datetime.datetime) -> float:
    return (end - start).total_seconds() / 86400.0
