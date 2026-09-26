"""安全装置（設計 3.12.0 §3.3）。LLM の依頼で直接出す・消す・予約するときの機械の上限。

**疑わない、暴走は機械で止める**（§1）。持ち主が台帳に書いた数値（無ければ既定）を
道具が機械的に守る。守れないものは静的な理由で断り、急な連投は口座を止める。

数え方は口座ごと・JST の日付で、材料は**既存の記録**だけ:

- 公開: `state/<account>/sent/<post_id>.json` の `sent_at`（timer・`thth send`・直接の公開の
  どれも公開の直後にここへ書く）
- 削除: 同じ記録の `retracted_at`（`thth retract` と直接の削除が足す）

新しく持つ記録は、止めた印 `state/<account>/guard/stopped.json`（0600・0700 の置き場）
だけ。止まった口座は持ち主が戻す（`resume()`）まで公開・削除・予約を断る。

断りの理由（`server_writes.SAFE_ERRORS` に同じ名前で載る）:

- `too_soon`     : 前の公開から `min_interval_hours` が経っていない（次に出せる時刻を添える）
- `quiet_hours`  : 夜間（`quiet_hours`）に今すぐ出そうとした（明ける時刻を添える）
- `daily_limit`  : その日の公開が `daily_max_posts` に達した（翌日 0 時を添える）
- `retract_limit`: その日の削除が `daily_max_retracts` に達した（翌日 0 時を添える）
- `account_stopped`（詳細 `burst`）: 直近 `burst.minutes` 分の公開と削除の合計が
  `burst.count` を超える依頼だった → 口座を止めて断る
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from . import accounts, jst, sent as sent_mod, server_files
from .report_service import ReportServiceError

REFUSALS = frozenset(("too_soon", "quiet_hours", "daily_limit", "retract_limit"))
# burst: 急な連投で道具が止めた。owner: 持ち主が /activity から止めた（段 3）。
STOP_REASONS = frozenset(("burst", "owner"))
STOP_FILE = "stopped.json"
CREDENTIAL_ID_LENGTH = 12
KINDS = ("publish", "retract", "schedule")


class GuardRefused(ReportServiceError):
    """安全装置の断り。`reason` は静的な詳細、`next_at` は次に出せる時刻（無ければ None）。"""

    def __init__(self, code, *, reason=None, next_at=None):
        super().__init__(code)
        self.reason = reason
        self.next_at = next_at


def credential_id(context) -> str | None:
    """記録に残す資格の id（`credential_digest` の先頭 12 文字）。hash の頭であって秘密ではない。"""
    digest = getattr(context, "credential_digest", None)
    return digest[:CREDENTIAL_ID_LENGTH] if isinstance(digest, str) and len(digest) >= CREDENTIAL_ID_LENGTH else None


def _directory(account) -> Path:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return Path(accounts.state_dir_for(account)) / "guard"


# --------------------------------------------------------------------------
# 止める・戻す
# --------------------------------------------------------------------------

def stopped(account) -> dict | None:
    """止まっていれば `{"reason", "stopped_at", ...}`、動いていれば None。

    読めない（置き場のモードが緩い・壊れている）ときは**止まっている側**に倒す。
    """
    try:
        with server_files.directory(_directory(account), private=True) as fd:
            raw = server_files.read_at(fd, STOP_FILE, private=True)
    except FileNotFoundError:
        return None
    except server_files.UnsafeFile as exc:
        if str(exc) in ("unsafe_server_directory", "unsafe_server_owner"):
            # 置き場のモード・持ち主が緩い: 断ることは同じだが、運営者が直せる理由のまま上げる
            # （呼び手は write_unavailable にし、運営者の CLI は 1 語と次の一手で言う・§6-5）。
            raise
        return {"reason": "guard_state_unreadable"}
    except (OSError, ValueError):
        return {"reason": "guard_state_unreadable"}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"reason": "guard_state_unreadable"}
    if type(value) is not dict or value.get("reason") not in STOP_REASONS:
        return {"reason": "guard_state_unreadable"}
    return value


def stop(account, reason, *, now=None, actor=None, via=None, credential=None) -> dict:
    """口座を止める（既に止まっていれば上書きしない）。"""
    if reason not in STOP_REASONS:
        raise ValueError("invalid_stop_reason")
    value = {"reason": reason, "stopped_at": jst.iso(now)}
    for key, item in (("actor", actor), ("via", via), ("credential", credential)):
        if isinstance(item, str) and item:
            value[key] = item
    with server_files.directory(_directory(account), create=True, private=True) as fd:
        try:
            server_files.replace_at(fd, STOP_FILE, server_files.encode(value), new=True, private=True)
        except server_files.UnsafeFile:
            # 既に止まっている（先に止めた印を消さない）。
            pass
    try:
        from . import admin_log
        admin_log.append("guard_stopped", account, accounts.load_account(account),
                         by=actor if isinstance(actor, str) and actor else "thth-guard",
                         via=via if via in ("cli", "mcp", "http", "api") else "cli",
                         diff={"stopped": [False, True], "reason": [None, reason]})
    except Exception:
        # 変更ログに残せなくても止めることは止める（止めた印が正本）。
        pass
    return value


def resume(account, *, by, via="cli") -> bool:
    """止めたのを戻す（持ち主の口から呼ぶ）。止まっていなければ False。

    口は運営者の CLI（`thth account resume`）と持ち主の `/activity`（via http）だけ。
    **MCP には出さない**——暴走して止まった LLM が自分で戻せないように（段 3）。
    """
    from . import admin_log
    admin_log.actor(by)
    try:
        with server_files.directory(_directory(account), private=True) as fd:
            try:
                os.unlink(STOP_FILE, dir_fd=fd)
            except FileNotFoundError:
                return False
            os.fsync(fd)
    except FileNotFoundError:
        return False
    try:
        admin_log.append("guard_resumed", account, accounts.load_account(account), by=by,
                         via=via if via in ("cli", "http") else "cli", diff={"stopped": [True, False]})
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------
# 数える
# --------------------------------------------------------------------------

def _events(account):
    """`sent/` の記録から (公開の時刻の list, 削除の時刻の list)。読めない時刻は数えない。"""
    posts, retracts = [], []
    for row in sent_mod.records(accounts.state_dir_for(account)):
        at = jst.parse(row.get("sent_at"))
        if at is not None:
            posts.append(at)
        at = jst.parse(row.get("retracted_at"))
        if at is not None:
            retracts.append(at)
    return posts, retracts


def _next_midnight(now):
    day = jst.to_jst(now).date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=jst.JST)


def _quiet_end(now, quiet_hours):
    hour, minute = (int(x) for x in quiet_hours[1].split(":"))
    now = jst.to_jst(now)
    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return end if end > now else end + datetime.timedelta(days=1)


def summary(account, cfg, *, now=None) -> dict:
    """数えた結果（読むだけ・`/activity` や答えに添える用）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    limits = accounts.guard_limits(cfg)
    posts, retracts = _events(account)
    today = now.date()
    window = now - datetime.timedelta(minutes=limits["burst"]["minutes"])
    return {
        "posts_today": sum(1 for at in posts if at.date() == today),
        "retracts_today": sum(1 for at in retracts if at.date() == today),
        "recent": sum(1 for at in posts + retracts if window < at <= now),
        "last_post_at": max(posts) if posts else None,
        "limits": limits,
        "posts": posts,
    }


def check(account, cfg, kind, *, now=None, actor=None, via=None, credential=None,
          publish_at=None, scheduled_same_day=0, reply=False) -> None:
    """依頼が安全装置に掛かれば `GuardRefused` を上げる。掛からなければ何もしない。

    `kind`: `publish`（今すぐ出す）・`retract`（消す）・`schedule`（queue に「出してよい」と
    刻む。`publish_at` の日の公開数に、その日にすでに刻んだ件数 `scheduled_same_day` を足して見る）。
    burst を超える依頼は**口座を止めてから**断る。

    `reply`（返信）には最短間隔 `min_interval_hours` を掛けない——返信は会話なので
    （段 1〜2 の裁定 (a)）。1 日の上限と burst には数える。
    """
    if kind not in KINDS:
        raise ValueError("invalid_guard_kind")
    halted = stopped(account)
    if halted is not None:
        raise GuardRefused("account_stopped", reason=halted.get("reason"))
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    counts = summary(account, cfg, now=now)
    limits = counts["limits"]
    if kind == "publish":
        quiet = cfg.get("quiet_hours")
        from .select import in_quiet_hours
        if quiet and in_quiet_hours(now, quiet):
            raise GuardRefused("quiet_hours", next_at=jst.iso(_quiet_end(now, quiet)))
        hours = cfg.get("min_interval_hours")
        last = counts["last_post_at"]
        if (not reply and isinstance(hours, (int, float)) and not isinstance(hours, bool)
                and hours > 0 and last is not None):
            ready = last + datetime.timedelta(hours=hours)
            if now < ready:
                raise GuardRefused("too_soon", next_at=jst.iso(ready))
        if counts["posts_today"] >= limits["daily_max_posts"]:
            raise GuardRefused("daily_limit", next_at=jst.iso(_next_midnight(now)))
    elif kind == "retract":
        if counts["retracts_today"] >= limits["daily_max_retracts"]:
            raise GuardRefused("retract_limit", next_at=jst.iso(_next_midnight(now)))
    else:
        day = jst.to_jst(publish_at).date() if publish_at is not None else now.date()
        published = sum(1 for at in counts["posts"] if at.date() == day)
        if published + scheduled_same_day >= limits["daily_max_posts"]:
            nxt = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=jst.JST)
            raise GuardRefused("daily_limit", next_at=jst.iso(nxt))
        return
    # 急な連投（公開と削除の合計）。この依頼を足すと超えるなら止める。
    if counts["recent"] + 1 > limits["burst"]["count"]:
        stop(account, "burst", now=now, actor=actor, via=via, credential=credential)
        raise GuardRefused("account_stopped", reason="burst")
