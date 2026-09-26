"""口座の設定を読む・変える（設計 3.12.0 段 3）。

変えられるのは **安全装置の数値だけ**（3.12.0 の `approval` は 3.13.0 で無くなった）:

| 名前 | 台帳の項目 | 締める向き |
|---|---|---|
| `daily_max_posts` | 同じ | 下げる |
| `daily_max_retracts` | 同じ | 下げる |
| `burst_count` | `burst.count` | 下げる |
| `burst_minutes` | `burst.minutes` | 上げる（数える窓が広いほど止まりやすい） |
| `hold_minutes` | 同じ | 上げる |
| `min_interval_hours` | 同じ | 上げる |

口は 3 つ。**どこから変えたかで許す向きが違う**（主セッションの裁定 2026-09-26）:

- 運営者の CLI `thth account set <口座> <名前> <値> --by <名前>`: どちらの向きも。
- `https://thth.me/activity`（持ち主が口座の secret で確かめる）: どちらの向きも。
- MCP の `thth_settings`（LLM）: **締める向きだけ**。緩める変更は
  `settings_loosen_requires_owner` で断る——暴走した LLM が自分で安全装置を外せないように。

`scheduled: false` の口座（timer に載っていない・招待の口座は今これ）では、`hold_minutes`
を 1 以上にすると「刻んでも出ない」ので `schedule_unavailable` で断る（裁定 (b)）。

変更は台帳を原子的に書き換え、変更ログに `settings_set`（値の前後）で残す。秘密は無い。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import accounts, admin_log, secrets_fs

KEYS = ("daily_max_posts", "daily_max_retracts", "burst_count", "burst_minutes",
        "hold_minutes", "min_interval_hours")
# 数値が大きいほど締まる項目（それ以外の数値は小さいほど締まる）。
TIGHTER_WHEN_LARGER = frozenset(("burst_minutes", "hold_minutes", "min_interval_hours"))
MIN_INTERVAL_MAX = 168
REASONS = frozenset(("invalid_setting", "settings_loosen_requires_owner", "schedule_unavailable",
                     "ledger_unavailable", "settings_unavailable"))


class SettingsError(ValueError):
    """静的な符丁だけを持つ断り。"""


def scheduled(cfg) -> bool:
    """timer に載っているか（台帳に無ければ載っている・`core` と同じ読み）。"""
    return cfg.get("scheduled", True) is not False


def current(cfg) -> dict:
    """いま効いている値（台帳に無い項目は既定）。"""
    limits = accounts.guard_limits(cfg)
    hours = cfg.get("min_interval_hours")
    return {
        "daily_max_posts": limits["daily_max_posts"],
        "daily_max_retracts": limits["daily_max_retracts"],
        "burst_count": limits["burst"]["count"],
        "burst_minutes": limits["burst"]["minutes"],
        "hold_minutes": limits["hold_minutes"],
        "min_interval_hours": hours if isinstance(hours, (int, float)) and not isinstance(hours, bool) else 0,
    }


def parse(key, value):
    """文字列（CLI・MCP・/activity の form）か数を、その項目の値にする。受け取れなければ断る。"""
    if key not in KEYS:
        raise SettingsError("invalid_setting")
    if isinstance(value, bool):
        raise SettingsError("invalid_setting")
    if isinstance(value, str):
        text = value.strip()
        if key == "min_interval_hours" and "." in text:
            try:
                number = float(text)
            except ValueError:
                raise SettingsError("invalid_setting") from None
            if number.is_integer():
                number = int(number)
        elif text.isdigit() and len(text) <= 6:
            number = int(text)
        else:
            raise SettingsError("invalid_setting")
    elif isinstance(value, (int, float)):
        number = value
    else:
        raise SettingsError("invalid_setting")
    if key == "min_interval_hours":
        if not 0 <= number <= MIN_INTERVAL_MAX or number != number:
            raise SettingsError("invalid_setting")
        return number
    if type(number) is not int:
        raise SettingsError("invalid_setting")
    if key == "burst_count":
        ok = 1 <= number <= accounts.BURST_COUNT_MAX
    elif key == "burst_minutes":
        ok = 1 <= number <= accounts.BURST_MINUTES_MAX
    else:
        ok = accounts.valid_guard_value(key, number)
    if not ok:
        raise SettingsError("invalid_setting")
    return number


def tightens(key, before, after) -> bool:
    """`before` → `after` が締める向き（か同じ）か。"""
    if key in TIGHTER_WHEN_LARGER:
        return after >= before
    return after <= before


def _ledger_path(account) -> Path:
    return Path(accounts.accounts_dir()) / (account + ".json")


def change(account, key, value, *, by, via="cli", tighten_only=False) -> dict:
    """1 項目を変える。戻り値は `{account, key, before, after, changed}`。"""
    admin_log.actor(by)
    if not accounts.name_is_safe(account):
        raise SettingsError("invalid_setting")
    after = parse(key, value)
    cfg = accounts.load_account(account)
    before = current(cfg)[key]
    if key == "hold_minutes" and after > 0 and not scheduled(cfg):
        # 刻んでも timer が拾わない（黙って出ない）。裁定 (b)。
        raise SettingsError("schedule_unavailable")
    if tighten_only and not tightens(key, before, after):
        raise SettingsError("settings_loosen_requires_owner")
    if before == after:
        return {"account": account, "key": key, "before": before, "after": after, "changed": False}
    path = _ledger_path(account)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingsError("ledger_unavailable") from exc
    if not isinstance(raw, dict):
        raise SettingsError("ledger_unavailable")
    original = json.loads(json.dumps(raw))
    if key in ("burst_count", "burst_minutes"):
        burst = dict(accounts.guard_limits(raw)["burst"])
        burst["count" if key == "burst_count" else "minutes"] = after
        raw["burst"] = burst
    else:
        raw[key] = after
    secrets_fs.atomic_write_json(str(path), raw)
    try:
        accounts.load_account(account)
    except accounts.AccountError:
        # 書いた台帳が読めない形なら元に戻す（通常は parse が先に断っている）。
        secrets_fs.atomic_write_json(str(path), original)
        raise SettingsError("invalid_setting") from None
    admin_log.append("settings_set", account, cfg, by=by, via=via, diff={key: [before, after]})
    return {"account": account, "key": key, "before": before, "after": after, "changed": True}


def status(account, *, now=None) -> dict:
    """安全装置の数値・止まっているか・今日の公開数/削除数（読むだけ）。

    `ignored` は台帳に残っているが読まない古い項目（3.12.0 の `approval` など）の名前。
    """
    from . import guard, jst
    if not accounts.name_is_safe(account):
        raise SettingsError("invalid_setting")
    cfg = accounts.load_account(account)
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    counts = guard.summary(account, cfg, now=now)
    halted = guard.stopped(account)
    stopped = None
    if halted is not None:
        stopped = {"reason": halted.get("reason"), "stopped_at": halted.get("stopped_at")}
    return {
        "account": account,
        "settings": current(cfg),
        "scheduled": scheduled(cfg),
        "quiet_hours": cfg.get("quiet_hours"),
        "stopped": stopped,
        "ignored": [key for key in accounts.IGNORED_FIELDS if key in cfg],
        "today": {"date": now.date().isoformat(), "posts": counts["posts_today"],
                  "retracts": counts["retracts_today"]},
    }


# --------------------------------------------------------------------------
# CLI（`thth account set|resume|status`）
# --------------------------------------------------------------------------

def _fail(reason, *, json_output=False):
    print(reason, file=sys.stderr)
    if json_output:
        print(json.dumps({"error": reason}, ensure_ascii=False))
    return 2


def _pairs(rest):
    """`<名前> <値>`・`burst <件数> <分>` を (名前, 値) の list に。"""
    if len(rest) == 3 and rest[0] == "burst":
        return [("burst_count", rest[1]), ("burst_minutes", rest[2])]
    if len(rest) == 2:
        return [(rest[0], rest[1])]
    raise SettingsError("invalid_setting")


def cmd_set(args) -> int:
    rest = list(getattr(args, "rest", None) or [])
    try:
        admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(str(exc))
    if not args.name:
        return _fail("invalid_setting: thth account set <口座> <名前> <値> --by <名前>")
    try:
        pairs = _pairs(rest)
        results = [change(args.name, key, value, by=args.by, via="cli") for key, value in pairs]
    except SettingsError as exc:
        reason = str(exc) if str(exc) in REASONS else "invalid_setting"
        if reason == "invalid_setting":
            reason += f": 名前は {'・'.join(KEYS)}（burst は `burst <件数> <分>` でも）"
        return _fail(reason, json_output=getattr(args, "json", False))
    except accounts.AccountError:
        return _fail("ledger_unavailable", json_output=getattr(args, "json", False))
    except (OSError, TypeError, admin_log.AdminLogError):
        return _fail("settings_unavailable", json_output=getattr(args, "json", False))
    if getattr(args, "json", False):
        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False))
        return 0
    return show_changes(results)


def show_changes(results) -> int:
    """`thth account set` の人向けの表示（手元の道と遠くの道が共有・`--json` の行を受ける）。"""
    for row in results:
        if row["changed"]:
            print(f"{row['account']}: {row['key']} {row['before']} → {row['after']}")
        else:
            print(f"{row['account']}: {row['key']} は既に {row['after']} です（変えていません）")
    return 0


def cmd_resume(args) -> int:
    from . import guard
    try:
        admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(str(exc))
    if not args.name or not accounts.name_is_safe(args.name) or getattr(args, "rest", None):
        return _fail("invalid_request: thth account resume <口座> --by <名前>")
    try:
        accounts.load_account(args.name)
        resumed = guard.resume(args.name, by=args.by, via="cli")
    except accounts.AccountError:
        return _fail("ledger_unavailable")
    except (OSError, ValueError):
        return _fail("guard_state_unavailable")
    if getattr(args, "json", False):
        print(json.dumps({"account": args.name, "resumed": resumed}, ensure_ascii=False))
    elif resumed:
        print(f"{args.name}: 止めた印を外しました。公開・削除・予約を再び受け付けます")
    else:
        print(f"{args.name}: 止まっていません（何も変えていません）")
    return 0


REASON_WORDS = {"burst": "急な連投（burst）で止めた", "owner": "持ち主が止めた",
                "guard_state_unreadable": "止めた印が読めない（止まっている側に倒している）"}


def cmd_status(args) -> int:
    if not args.name or getattr(args, "rest", None):
        return _fail("invalid_request: thth account status <口座>")
    try:
        row = status(args.name)
    except SettingsError as exc:
        return _fail(str(exc))
    except accounts.AccountError:
        return _fail("ledger_unavailable")
    except (OSError, ValueError, TypeError):
        return _fail("settings_unavailable")
    if getattr(args, "json", False):
        print(json.dumps(row, ensure_ascii=False))
        return 0
    return show_status(row)


def show_status(row) -> int:
    """`thth account status` の人向けの表示（手元の道と遠くの道が共有・`--json` の形を受ける）。"""
    s = row["settings"]
    print(f"{row['account']}")
    print(f"  1 日の公開の上限（daily_max_posts）: {s['daily_max_posts']}")
    print(f"  1 日の削除の上限（daily_max_retracts）: {s['daily_max_retracts']}")
    print(f"  急な連投で止める（burst）: {s['burst_minutes']} 分に {s['burst_count']} 件を超えたら")
    print(f"  取り消しの猶予（hold_minutes）: {s['hold_minutes']} 分")
    print(f"  最短間隔（min_interval_hours）: {s['min_interval_hours']} 時間（返信には掛けない）")
    print(f"  予約の timer（scheduled）: {'載っている' if row['scheduled'] else '載っていない（予約と猶予は使えない）'}")
    print(f"  今日（{row['today']['date']}）: 公開 {row['today']['posts']} 件・削除 {row['today']['retracts']} 件")
    for key in row["ignored"]:
        print(f"  古い項目 {key}（無視）")
    if row["stopped"]:
        reason = row["stopped"]["reason"]
        print(f"  → **止まっています**: {REASON_WORDS.get(reason, reason)}"
              f"（{row['stopped'].get('stopped_at') or '時刻不明'}）。"
              f"戻すのは持ち主: https://thth.me/activity か thth account resume {row['account']} --by <名前>")
    else:
        print("  → 動いています")
    return 0


# --------------------------------------------------------------------------
# MCP（`thth_settings`・`thth_account_status`）。資格の範囲は server_writes.current が確かめる。
# 止まった口座を戻す道は**ここに無い**（resume は CLI と /activity だけ）。
# --------------------------------------------------------------------------

def _service_error(reason):
    from .report_service import ReportServiceError
    return ReportServiceError(reason if reason in REASONS else "invalid_setting")


def mcp_settings(context, request) -> dict:
    """読む（key を省く）か、1 項目を**締める向きだけ**変える（key と value）。"""
    from . import server_writes
    from .report_service import ReportServiceError
    if type(request) is not dict or set(request) - {"operation", "account", "key", "value"}:
        raise ReportServiceError("invalid_request")
    account = request.get("account")
    if not isinstance(account, str):
        raise ReportServiceError("invalid_scope")
    key, value = request.get("key"), request.get("value")
    if key is None and value is None:
        cfg = server_writes.current(context, account)
        return {"account": account, "settings": current(cfg), "scheduled": scheduled(cfg),
                "keys": list(KEYS), "loosen": "https://thth.me/activity"}
    if not isinstance(key, str) or not isinstance(value, str):
        raise ReportServiceError("invalid_request")
    server_writes.current(context, account, write=True)
    try:
        row = change(account, key, value, by=context.actor, via="mcp", tighten_only=True)
    except SettingsError as exc:
        raise _service_error(str(exc)) from None
    return row


def mcp_status(context, request) -> dict:
    from . import server_writes
    from .report_service import ReportServiceError
    if type(request) is not dict or set(request) - {"operation", "account"}:
        raise ReportServiceError("invalid_request")
    account = request.get("account")
    if not isinstance(account, str):
        raise ReportServiceError("invalid_scope")
    server_writes.current(context, account)
    try:
        return status(account)
    except SettingsError as exc:
        raise _service_error(str(exc)) from None
