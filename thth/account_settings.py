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
| `x_daily_reads` | 同じ（X の口座だけ・既定 60） | 下げる（JST の 1 日に読む他人の投稿の本数・有償） |
| `daily_max_reactions` | 同じ（既定 50・3.15.0） | 下げる（再投稿・いいね・取り消しの合計。公開の枠とは別） |
| `x_archive_daily_reads` | 同じ（X の口座だけ・既定 0・4.5.0） | 下げる（JST の 1 日に全期間の検索で読む本数・材料集め・有償。`x_daily_reads` とは別に数える） |

口は 3 つ。**どこから変えたかで許す向きが違う**（主セッションの裁定 2026-09-26）:

- 運営者の CLI `thth account set <口座> <名前> <値> --by <名前>`: どちらの向きも。
- `https://thth.me/activity`（持ち主が口座の secret で確かめる）: どちらの向きも。
- MCP の `thth_settings`（LLM）: **締める向きだけ**。緩める変更は
  `settings_loosen_requires_owner` で断る——暴走した LLM が自分で安全装置を外せないように。

`scheduled: false` の口座（timer に載っていない・招待の口座は作ったときこれ）では、`hold_minutes`
を 1 以上にすると「刻んでも出ない」ので `schedule_unavailable` で断る（裁定 (b)）。
運営者の CLI だけが `thth account set <口座> scheduled true --by <名前>` で timer に載せる印を
立てられる（10/15・点検 §B-21。unit は運営者が `thth systemd <口座>` で置く）。

変更は台帳を原子的に書き換え、変更ログに `settings_set`（値の前後）で残す。秘密は無い。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import accounts, admin_log, secrets_fs

KEYS = ("daily_max_posts", "daily_max_retracts", "burst_count", "burst_minutes",
        "hold_minutes", "min_interval_hours", "x_daily_reads", "daily_max_reactions",
        "x_archive_daily_reads")
# X の口座だけが持つ項目（有償の読み取りの 1 日の本数・設計 2026-10-08・全期間は 4.5.0）。
X_ONLY_KEYS = frozenset(("x_daily_reads", "x_archive_daily_reads"))
# 数値が大きいほど締まる項目（それ以外の数値は小さいほど締まる）。
TIGHTER_WHEN_LARGER = frozenset(("burst_minutes", "hold_minutes", "min_interval_hours"))
MIN_INTERVAL_MAX = 168
REASONS = frozenset(("invalid_setting", "settings_loosen_requires_owner", "schedule_unavailable",
                     "ledger_unavailable", "settings_unavailable", "invalid_handle",
                     "handle_locked_by_token"))


class SettingsError(ValueError):
    """静的な符丁だけを持つ断り。"""


def scheduled(cfg) -> bool:
    """timer に載っているか（台帳に無ければ載っている・`core` と同じ読み）。"""
    return cfg.get("scheduled", True) is not False


def current(cfg) -> dict:
    """いま効いている値（台帳に無い項目は既定）。"""
    limits = accounts.guard_limits(cfg)
    hours = cfg.get("min_interval_hours")
    extra = {}
    if (cfg or {}).get("media") == "x":
        from . import budget_x
        reads = cfg.get(budget_x.DAILY_READS_KEY)
        extra["x_daily_reads"] = (reads if type(reads) is int and 0 <= reads <= budget_x.MAX_DAILY_READS
                                  else budget_x.DEFAULT_DAILY_READS)
        archive = cfg.get(budget_x.ARCHIVE_DAILY_READS_KEY)
        extra["x_archive_daily_reads"] = (archive if type(archive) is int
                                          and 0 <= archive <= budget_x.MAX_ARCHIVE_DAILY_READS
                                          else budget_x.DEFAULT_ARCHIVE_DAILY_READS)
    return {
        "daily_max_posts": limits["daily_max_posts"],
        "daily_max_retracts": limits["daily_max_retracts"],
        "burst_count": limits["burst"]["count"],
        "burst_minutes": limits["burst"]["minutes"],
        "hold_minutes": limits["hold_minutes"],
        "min_interval_hours": hours if isinstance(hours, (int, float)) and not isinstance(hours, bool) else 0,
        "daily_max_reactions": limits["daily_max_reactions"],
        **extra,
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
    elif key == "x_daily_reads":
        from . import budget_x
        ok = 0 <= number <= budget_x.MAX_DAILY_READS
    elif key == "x_archive_daily_reads":
        from . import budget_x
        ok = 0 <= number <= budget_x.MAX_ARCHIVE_DAILY_READS
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
    if key in X_ONLY_KEYS and cfg.get("media") != "x":
        # X の口座でなければ意味の無い項目（黙って台帳に置かない）。
        raise SettingsError("invalid_setting")
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
    # 段 3: 今の鍵（別名）で書いたら、その鍵が指す行の `limits` を外す（`limits` が勝つので、残すと変更が効かない）。
    from . import limits_config
    limits_config.drop_aliased(raw, "account", "burst" if key in ("burst_count", "burst_minutes") else key,
                               cfg.get("media"))
    secrets_fs.atomic_write_json(str(path), raw)
    try:
        accounts.load_account(account)
    except accounts.AccountError:
        # 書いた台帳が読めない形なら元に戻す（通常は parse が先に断っている）。
        secrets_fs.atomic_write_json(str(path), original)
        raise SettingsError("invalid_setting") from None
    admin_log.append("settings_set", account, cfg, by=by, via=via, diff={key: [before, after]})
    return {"account": account, "key": key, "before": before, "after": after, "changed": True}


HANDLE_RE = re.compile(r"[A-Za-z0-9._-]{1,253}")


SCHEDULED_VALUES = {"true": True, "false": False}


def change_scheduled(account, value, *, by) -> dict:
    """台帳の `scheduled`（timer に載せるか）を変える（**運営者の CLI だけ**・10/15・点検 §B-21）。

    招待の口座は `scheduled: false` で作られ、予約と猶予が `schedule_unavailable` で断られる。
    それまでは ssh で JSON を手で書き換えるしかなかった。`true` にしても timer の unit は
    置かれないので、置き方（`thth systemd <口座>`）を表示で言う。`false` に戻すときは、
    猶予（`hold_minutes`）が 1 以上なら断る（刻んだ原稿が黙って出なくなる・裁定 (b)）。
    MCP・遠くの道（`thth.me`）からは変えられない（`KEYS` に無いので `change()` が断る）。
    """
    admin_log.actor(by)
    if not accounts.name_is_safe(account):
        raise SettingsError("invalid_setting")
    key = str(value or "").strip().lower()
    if key not in SCHEDULED_VALUES:
        raise SettingsError("invalid_setting")
    after = SCHEDULED_VALUES[key]
    cfg = accounts.load_account(account)
    before = scheduled(cfg)
    if before == after:
        return {"account": account, "key": "scheduled", "before": before, "after": after, "changed": False}
    if not after and current(cfg)["hold_minutes"] > 0:
        raise SettingsError("schedule_unavailable")
    path = _ledger_path(account)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingsError("ledger_unavailable") from exc
    if not isinstance(raw, dict):
        raise SettingsError("ledger_unavailable")
    original = json.loads(json.dumps(raw))
    raw["scheduled"] = after
    secrets_fs.atomic_write_json(str(path), raw)
    try:
        accounts.load_account(account)
    except accounts.AccountError:
        secrets_fs.atomic_write_json(str(path), original)
        raise SettingsError("ledger_unavailable") from None
    admin_log.append("settings_set", account, cfg, by=by, via="cli", diff={"scheduled": [before, after]})
    return {"account": account, "key": "scheduled", "before": before, "after": after, "changed": True}


def change_handle(account, value, *, by) -> dict:
    """台帳の `handle` を直す（**運営者の CLI だけ・トークンが無いときだけ**・10/15）。

    `thth account add` の Threads は `--handle` を省くと project 名が入る。外れていると
    `thth auth` が取り違え防止で「保存しませんでした」と断り、直す口が無かった
    （music-research-threads を ssh で JSON を手で書き換えて直した）。

    **トークンがあれば断る**（`handle_locked_by_token`）。handle は取り違え防止の
    照合の片側で、トークンのあとに書き換えられると、別のアカウントのトークンに
    この口座の queue を出させる道になる。認可のあとに変えたいなら、トークンを
    外してから認可し直す。MCP・遠くの道（`thth.me`）からは変えられない
    （`KEYS` に無いので `change()` が断る）。
    """
    admin_log.actor(by)
    if not accounts.name_is_safe(account):
        raise SettingsError("invalid_setting")
    after = str(value or "").strip().lstrip("@")
    if not HANDLE_RE.fullmatch(after):
        raise SettingsError("invalid_handle")
    cfg = accounts.load_account(account)
    before = cfg.get("handle")
    if before == after:
        return {"account": account, "key": "handle", "before": before, "after": after, "changed": False}
    try:
        token = accounts.load_token(cfg, refresh_x=False)
    except (OSError, ValueError):
        token = True  # 読めないトークンも「在る」として扱う（外して認可し直すまで変えない）
    if token is not None:
        raise SettingsError("handle_locked_by_token")
    path = _ledger_path(account)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingsError("ledger_unavailable") from exc
    if not isinstance(raw, dict):
        raise SettingsError("ledger_unavailable")
    original = json.loads(json.dumps(raw))
    raw["handle"] = after
    secrets_fs.atomic_write_json(str(path), raw)
    try:
        accounts.load_account(account)
    except accounts.AccountError:
        secrets_fs.atomic_write_json(str(path), original)
        raise SettingsError("invalid_handle") from None
    admin_log.append("settings_set", account, cfg, by=by, via="cli", diff={"handle": [before, after]})
    return {"account": account, "key": "handle", "before": before, "after": after, "changed": True}


def status(account, *, now=None, table=False) -> dict:
    """安全装置の数値・止まっているか・今日の公開数/削除数（読むだけ）。

    `ignored` は台帳に残っているが読まない古い項目（3.12.0 の `approval` など）の名前。
    `table`（手元の CLI だけ・段 3）: 柵の表の行ごとの数・出所・確かさ（`limits_table`）と、台帳に明示された
    今の鍵（`legacy_keys`・別名・非推奨の 1 行）を足す。MCP・遠くの道の答えは今の形のまま。
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
    # 媒体の断りで止めた自動の投稿（3.19.2）。それまでは手元の道の人向けの表示だけが言い、
    # thth.me の道と MCP では止まっていても「動いています」と出た（10/15 調べ 食い違い 13）。足すだけ。
    from . import publish_hold
    held = publish_hold.held(account)
    hold = None
    if held is not None:
        hold = {"reason": held.get("reason"), "held_at": held.get("held_at"),
                "message": publish_hold.message(account, held)}
    extra = {}
    if table:
        from . import limits_config, limits_view
        try:
            extra["limits_table"] = limits_view.account_table(account, cfg, now=now)
        except ValueError:
            extra["limits_table"] = None   # 媒体の台帳が検査を通らない（thth media check）
        extra["legacy_keys"] = limits_config.legacy_keys("account", cfg)
    return {
        **extra,
        "account": account,
        "settings": current(cfg),
        "scheduled": scheduled(cfg),
        "quiet_hours": cfg.get("quiet_hours"),
        "stopped": stopped,
        "publish_hold": hold,
        "ignored": [key for key in accounts.IGNORED_FIELDS if key in cfg],
        "today": {"date": now.date().isoformat(), "posts": counts["posts_today"],
                  "retracts": counts["retracts_today"],
                  "reactions": guard.reactions_today(account, now=now)},
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


def _set_rule(args, rest) -> int:
    """新しい名前（柵の表の行の id・段 3）: `thth account set <口座> <行の id> <値…>|- [--reason <理由>]`。"""
    from . import limits_config
    rule_id, words = rest[0], rest[1:]
    json_output = getattr(args, "json", False)
    try:
        row = limits_config.change("account", args.name, rule_id, words, by=args.by, via="cli",
                                   reason=getattr(args, "reason", None))
    except limits_config.LimitsError as exc:
        return _fail(limits_config.message(exc.code, exc.key, "account"), json_output=json_output)
    except accounts.AccountError:
        return _fail("ledger_unavailable", json_output=json_output)
    except (OSError, TypeError, ValueError, admin_log.AdminLogError):
        return _fail("settings_unavailable", json_output=json_output)
    if json_output:
        print(json.dumps(row, ensure_ascii=False))
        return 0
    show_rule_change(row, "account")
    return 0


def show_rule_change(row, holder) -> None:
    """行の id で変えたときの人向けの表示（口座・主体で共有）。"""
    def shown(value):
        if isinstance(value, dict):
            return f"{value['minutes']} 分に {value['count']}"
        return "既定" if value is None else str(value)
    name = row[holder]
    if row["changed"]:
        print(f"{name}: {row['key']} {shown(row['before'])} → {shown(row['after'])}"
              f"（{'台帳の limits' if row.get('via') == 'limits' else '明示を外して既定／今の鍵'}）")
    else:
        print(f"{name}: {row['key']} は既に {shown(row['after'])} です（変えていません）")
    if row.get("floor_raised"):
        print(f"  最低線を持ち主が上げています（理由: {row.get('floor_reason')}）。変更ログに残しました")


def cmd_set(args) -> int:
    rest = list(getattr(args, "rest", None) or [])
    try:
        admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(str(exc))
    if not args.name:
        return _fail("invalid_setting: thth account set <口座> <名前> <値> --by <名前>")
    from . import limits_config
    if rest and limits_config.holder_of(rest[0]) is not None or (rest and "." in rest[0]):
        return _set_rule(args, rest)
    try:
        pairs = _pairs(rest)
        results = [change_handle(args.name, value, by=args.by) if key == "handle"
                   else change_scheduled(args.name, value, by=args.by) if key == "scheduled"
                   else change(args.name, key, value, by=args.by, via="cli") for key, value in pairs]
    except SettingsError as exc:
        reason = str(exc) if str(exc) in REASONS else "invalid_setting"
        if reason == "invalid_setting":
            reason += (f": 名前は {'・'.join(KEYS)}・handle・scheduled（true／false）"
                       "（burst は `burst <件数> <分>` でも）・柵の行の id（"
                       + "・".join(limits_config.ACCOUNT_ROWS) + "）")
        elif reason == "invalid_handle":
            reason += ": handle は英数字と `.`・`_`・`-` だけ（先頭の @ は外す）"
        elif reason == "schedule_unavailable" and any(k == "scheduled" for k, _ in pairs):
            reason += (f": {args.name} は猶予（hold_minutes）が 1 以上なので timer から外しません。"
                       f"先に thth account set {args.name} hold_minutes 0 --by <名前>")
        elif reason == "handle_locked_by_token":
            reason += (f": {args.name} にはトークンがあるので handle を変えません（取り違え防止の"
                       "照合の片側のため）。変えるなら、トークンを外してから handle を直し、認可し直す")
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
        if row["key"] == "scheduled" and row["after"] is True:
            name = row["account"]
            print(f"  timer を置くまで予約は出ません（VM・管理者）: thth systemd {name} | sudo tee "
                  f"/etc/systemd/system/thth@{name}.timer && sudo systemctl daemon-reload && "
                  f"sudo systemctl enable --now thth@{name}.timer")
        elif row["key"] == "scheduled" and row["changed"]:
            name = row["account"]
            print(f"  timer を外す（VM・管理者）: sudo systemctl disable --now thth@{name}.timer。"
                  f"採集は thth systemd {name} --collect-only の timer で")
    return 0


def cmd_resume(args) -> int:
    """安全装置の止め（`guard`）と、媒体の断りで止めた自動の投稿（`publish_hold`・3.19.2）を
    戻す。どちらも人の口からだけ（MCP には出さない）。"""
    from . import guard, publish_hold
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
    try:
        publish_resumed = publish_hold.release(args.name, by=args.by, via="cli")
    except (OSError, ValueError):
        return _fail("publish_hold_unavailable")
    if getattr(args, "json", False):
        print(json.dumps({"account": args.name, "resumed": resumed,
                          "publish_resumed": publish_resumed}, ensure_ascii=False))
        return 0
    if resumed:
        print(f"{args.name}: 止めた印を外しました。公開・削除・予約を再び受け付けます")
    if publish_resumed:
        print(f"{args.name}: 媒体の断りで止めていた自動の投稿を再開します。次の run から投げます"
              "（まだ断られたら 1 回でまた止まります）")
    if not resumed and not publish_resumed:
        print(f"{args.name}: 止まっていません（何も変えていません）")
    return 0


REASON_WORDS = {"burst": "急な連投（burst）で止めた", "owner": "持ち主が止めた",
                "guard_state_unreadable": "止めた印が読めない（止まっている側に倒している）"}


def cmd_status(args) -> int:
    if not args.name or getattr(args, "rest", None):
        return _fail("invalid_request: thth account status <口座>")
    try:
        row = status(args.name, table=True)
    except SettingsError as exc:
        return _fail(str(exc))
    except accounts.AccountError:
        return _fail("ledger_unavailable")
    except (OSError, ValueError, TypeError):
        return _fail("settings_unavailable")
    if getattr(args, "json", False):
        print(json.dumps(row, ensure_ascii=False))
        return 0
    # 媒体の断りで止めた自動の投稿（3.19.2）は `status()` の `publish_hold` に入っている（10/15 から
    # thth.me の道・MCP も同じ）。
    return show_status(row)


def show_status(row, *, publish_hold_line=None) -> int:
    """`thth account status` の人向けの表示（手元の道と遠くの道が共有・`--json` の形を受ける）。"""
    s = row["settings"]
    if publish_hold_line is None and isinstance(row.get("publish_hold"), dict):
        publish_hold_line = row["publish_hold"].get("message") or "自動の投稿を止めています"
    print(f"{row['account']}")
    # どの道に掛かるかを言う（10/15: 掛かっていない道があるのに守られていると読まれた）。
    print("  安全装置の掛かる所: thth send と thth retract（VM の CLI・thth.me・MCP）と thth.me の予約。"
          "timer が出す予約の原稿と retract --already-gone（DELETE を呼ばない）には掛からない（数には入る）")
    print(f"  1 日の公開の上限（daily_max_posts）: {s['daily_max_posts']}")
    print(f"  1 日の削除の上限（daily_max_retracts）: {s['daily_max_retracts']}")
    print(f"  急な連投で止める（burst）: {s['burst_minutes']} 分に {s['burst_count']} 件を超えたら")
    print(f"  取り消しの猶予（hold_minutes）: {s['hold_minutes']} 分")
    print(f"  最短間隔（min_interval_hours）: {s['min_interval_hours']} 時間（返信には掛けない）")
    if "daily_max_reactions" in s:
        print(f"  1 日の反応の上限（daily_max_reactions）: {s['daily_max_reactions']}（再投稿・いいね・取り消し。公開の枠とは別）")
    if "x_daily_reads" in s:
        print(f"  1 日（JST）に読む他人の X の投稿の上限（x_daily_reads）: {s['x_daily_reads']} 本（有償・1 本 約 0.005 USD）")
    if "x_archive_daily_reads" in s:
        print(f"  1 日（JST）に全期間の検索で読む本数の上限（x_archive_daily_reads）: {s['x_archive_daily_reads']} 本"
              "（材料集め・有償・1 本 約 0.005 USD・0 は読まない）")
    print(f"  予約の timer（scheduled）: {'載っている' if row['scheduled'] else '載っていない（予約と猶予は使えない）'}")
    reacted = row['today'].get('reactions')
    print(f"  今日（{row['today']['date']}）: 公開 {row['today']['posts']} 件・削除 {row['today']['retracts']} 件"
          + (f"・反応 {reacted} 件" if reacted is not None else ""))
    for key in row["ignored"]:
        print(f"  古い項目 {key}（無視）")
    if row.get("limits_table") is not None or "legacy_keys" in row:
        from . import limits_view
        if row.get("limits_table") is None:
            print("  上限と連投の柵: 媒体の台帳が検査を通らないので表を出せません（thth media check）")
            table_lines = limits_view.lines([], legacy=row.get("legacy_keys") or (),
                                            set_hint=f"thth account set {row['account']} <行の id> <値> --by <名前>")[1:]
        else:
            table_lines = limits_view.lines(row["limits_table"], legacy=row.get("legacy_keys") or (),
                                            set_hint=f"thth account set {row['account']} <行の id> <値> --by <名前>")
        for line in table_lines:
            print(line)
    if row["stopped"]:
        reason = row["stopped"]["reason"]
        print(f"  → **止まっています**: {REASON_WORDS.get(reason, reason)}"
              f"（{row['stopped'].get('stopped_at') or '時刻不明'}）。"
              f"戻すのは持ち主: https://thth.me/activity か thth account resume {row['account']} --by <名前>")
    if publish_hold_line:
        print(f"  → **{publish_hold_line}**")
    if not row["stopped"] and not publish_hold_line:
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
