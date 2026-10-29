"""Meta の Webhook の VM 側（設計 MetaAPI 計画 段 6・`thth webhook …`・`thth worker` の取り込み）。

**流れ。** Meta → Worker の `/webhook/meta`（署名を照合して Durable Object に積む・`callback/src/webhook.js`）→
VM の `thth worker` が 30 秒ごとに署名つき relay（`/relay/v/webhook/meta/pull`）で取りに来て、
`state/<口座>/inbox/<YYYY-MM-DD>.ndjson` に落とし、落とした分を `ack` で Worker から消す。

- 口座への振り分けは `entry.id` → 台帳（ページは `page_id`・IG は `ig_user_id`）。**知らない id は捨てて数だけ残す**
  （`thth webhook status` の `unrouted`）。止めた口座の分も捨てて数える（止めた口座に新しい記録を作らない）。
- 読む側: `FacebookAdapter.inbox()`・`mentions()`（言及・訪問者の投稿）と `webhook_replies()`（自分の投稿への
  コメント。`collect` が返信の台帳に写す）。Meta は呼ばない。
- 購読: `thth webhook subscribe <口座>` が `POST /{page-id}/subscribed_apps`（ページの鍵・`pages_manage_metadata`）。
  **今の欄を読んでから足す・外さない。** IG（Facebook ログインの道）は親のページの購読で IG の通知も有効になる
  （L2: 「ページの欄をどれか 1 つ購読する」）ので、同じ口で `feed` を足す。IG の `mentions` の欄はアプリの
  管理画面で選ぶ（masaru）。IG の `comments`・`live_comments` は Advanced Access が要るので段 6 では扱わない。
- **過去の分は Meta から取り直せない。** Meta は失敗のあと 36 時間まで送り直し、それを越えた分は捨てる。
  VM から取り込めなかった時間・Worker で取り込む前に消えた数（7 日）・満杯で断った数を `outages.ndjson` に残し、
  `thth events` の `webhook_down` に出す。

置き場（どれも 0600・`state/_webhook/` は 0700）:
  `state/_webhook/subscriptions.json`  購読した口座（`thth webhook subscribe` が書く）
  `state/_webhook/status.json`         最後の取り込み・取り込めない状態の始まり・Worker の数・捨てた数
  `state/_webhook/outages.ndjson`      取り込めなかった時間・消えた数・断った数（1 件 1 行・追記）
  `state/<口座>/inbox/<日>.ndjson`      口座に振り分けた変化（30 日で消す）
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

from . import accounts, admin_log, jst, relay, server_files

# 媒体 → 購読するページの欄。IG はページの欄を 1 つ購読すると IG の通知も有効になる（上の注）。
SUBSCRIBE_FIELDS = {"facebook": ("feed", "mention"), "instagram": ("feed",)}
OBJECT_BY_MEDIUM = {"facebook": "page", "instagram": "instagram"}
ID_KEY = {"facebook": "page_id", "instagram": "ig_user_id"}
OBJECTS = frozenset(OBJECT_BY_MEDIUM.values())
# 取り込みの間隔（`thth worker` は 2 秒ごとに回るので、ここで間を空ける）。Worker の RELAY_JOB_LIMIT は 180/分。
PULL_EVERY_SECONDS = 30
# 最後の取り込みの成功がこれより新しければ「Webhook が届いている」（collect が巡回の間隔を伸ばす）。
LIVE_HOURS = 1
# 取り込めない状態がこれより続いたら events に出す（deploy の数十秒は出さない）。
DOWN_AFTER = datetime.timedelta(minutes=5)
# 購読してから一度も取り込めていない状態をこれより長く見たら events に出す。
NEVER_PULLED_AFTER = datetime.timedelta(minutes=10)
KEEP_DAYS = 30
PAGES_MAX = 50
ACK_MAX = 200
FILE_MAX = 64 * 1024 * 1024
KEY = re.compile(r"[0-9a-f]{64}\Z")
CURSOR = re.compile(r"[0-9]{13}:[0-9a-f]{64}\Z")
ENTRY_ID = re.compile(r"[0-9]{1,32}\Z")
DAY_FILE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.ndjson\Z")
WORKER_COUNTERS = ("received", "duplicates", "dropped", "expired", "overflow", "pending")


class WebhookError(ValueError):
    """取り込み・購読ができなかった（理由の符丁を持つ）。"""


def home() -> Path:
    return Path(accounts.thth_root()).resolve() / "state" / "_webhook"


def inbox_dir(account: str) -> Path:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return Path(accounts.state_dir_for(account)) / "inbox"


# ------------------------------------------------------------------ 置き場

def _read_json(fd, name, default):
    try:
        value = json.loads(server_files.read_at(fd, name, private=True, maximum=FILE_MAX))
    except FileNotFoundError:
        return default
    return value if isinstance(value, type(default)) else default


def _read_home(name, default):
    try:
        with server_files.directory(home(), private=True) as fd:
            return _read_json(fd, name, default)
    except FileNotFoundError:
        return default


def subscriptions() -> dict:
    return _read_home("subscriptions.json", {})


def status() -> dict:
    return _read_home("status.json", {})


def outages() -> list:
    try:
        with server_files.directory(home(), private=True) as fd:
            raw = server_files.read_at(fd, "outages.ndjson", private=True, maximum=FILE_MAX)
    except FileNotFoundError:
        return []
    rows = []
    for line in raw.decode("utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_line(fd, name, row):
    try:
        old = server_files.read_at(fd, name, private=True, maximum=FILE_MAX)
    except FileNotFoundError:
        old = b""
    server_files.replace_at(fd, name, old + server_files.encode(row), private=True)


def rows_for(account: str) -> list:
    """口座に振り分けた Webhook の変化（受けた順）。無ければ空。**読むだけ。**"""
    try:
        with server_files.directory(inbox_dir(account), private=True) as fd:
            out = []
            for name in sorted(os.listdir(fd)):
                if not DAY_FILE.fullmatch(name):
                    continue
                for line in server_files.read_at(fd, name, private=True, maximum=FILE_MAX).decode("utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and isinstance(row.get("key"), str):
                        out.append(row)
            return out
    except FileNotFoundError:
        return []


def counts_for(account: str) -> dict:
    """口座の記録の数（欄ごと）と最後に受けた時刻。`thth webhook status` が出す（本文は出さない）。"""
    rows = rows_for(account)
    by_field: dict = {}
    for row in rows:
        by_field[row.get("field")] = by_field.get(row.get("field"), 0) + 1
    return {"rows": len(rows), "by_field": by_field,
            "last_received_at": max((r.get("received_at") for r in rows if r.get("received_at")), default=None)}


def _store(account: str, items: list, now) -> int:
    """口座の inbox に落とす。**key で冪等**（ack が届かず同じ行がもう一度来ても 2 行にしない）。"""
    with server_files.directory(inbox_dir(account), create=True, private=True) as fd:
        with server_files.lock_at(fd, ".write.lock"):
            known, floor = set(), (now - datetime.timedelta(days=KEEP_DAYS)).date().isoformat()
            for name in sorted(os.listdir(fd)):
                if not DAY_FILE.fullmatch(name):
                    continue
                if name[:10] < floor:
                    os.unlink(name, dir_fd=fd)
                    continue
                for line in server_files.read_at(fd, name, private=True, maximum=FILE_MAX).decode("utf-8").splitlines():
                    try:
                        known.add(json.loads(line).get("key"))
                    except (ValueError, AttributeError):
                        continue
            by_day: dict = {}
            for item in items:
                if item["key"] in known:
                    continue
                known.add(item["key"])
                received = datetime.datetime.fromtimestamp(item["received_at"] / 1000, datetime.timezone.utc)
                try:
                    value = json.loads(item["value"]) if isinstance(item["value"], str) else None
                except ValueError:
                    value = None
                row = {"key": item["key"], "object": item["object"], "entry_id": item["entry_id"],
                       "field": item["field"], "time": item["time"],
                       "value": value if isinstance(value, dict) else None, "oversize": item["oversize"],
                       "received_at": jst.iso(received), "pulled_at": jst.iso(now)}
                by_day.setdefault(jst.to_jst(received).date().isoformat() + ".ndjson", []).append(row)
            for name, rows in sorted(by_day.items()):
                try:
                    old = server_files.read_at(fd, name, private=True, maximum=FILE_MAX)
                except FileNotFoundError:
                    old = b""
                server_files.replace_at(fd, name, old + b"".join(server_files.encode(r) for r in rows), private=True)
            return sum(len(rows) for rows in by_day.values())


# ------------------------------------------------------------------ 振り分け

def routes() -> dict:
    """`(object, entry_id)` → `(口座, 止めているか)`。同じ id を 2 つの口座が持てば `(None, False)`（捨てて数える）。"""
    from . import leave_gate
    out: dict = {}
    for name in accounts.list_account_names():
        try:
            with leave_gate.recovery(name):
                cfg = accounts.load_account(name)
            medium = cfg.get("media")
            if medium not in OBJECT_BY_MEDIUM:
                continue
            entry = str(cfg.get(ID_KEY[medium]) or "")
            if not ENTRY_ID.fullmatch(entry):
                continue
            stopped = leave_gate.stopped(name)
        except (accounts.AccountError, ValueError, OSError):
            continue
        key = (OBJECT_BY_MEDIUM[medium], entry)
        out[key] = (None, False) if key in out else (name, stopped)
    return out


def _valid_row(row) -> bool:
    return (type(row) is dict and isinstance(row.get("key"), str) and KEY.fullmatch(row["key"]) is not None
            and row.get("object") in OBJECTS and isinstance(row.get("entry_id"), str)
            and ENTRY_ID.fullmatch(row["entry_id"]) is not None and isinstance(row.get("field"), str)
            and type(row.get("time")) is int and (row.get("value") is None or isinstance(row.get("value"), str))
            and type(row.get("oversize")) is bool and type(row.get("received_at")) is int)


def _page(page):
    if (type(page) is not dict or type(page.get("rows")) is not list or len(page["rows"]) > 100
            or not (page.get("next") is None or (isinstance(page.get("next"), str) and CURSOR.fullmatch(page["next"])))
            or type(page.get("stats")) is not dict or not all(_valid_row(r) for r in page["rows"])):
        raise WebhookError("webhook_page_unreadable")
    return page["rows"], page["next"], {k: page["stats"].get(k) for k in WORKER_COUNTERS + ("last_received_at",)}


# ------------------------------------------------------------------ 取り込み

def pull_once(*, now=None) -> dict:
    """1 回取り込む（頁を辿り、落とした分を ack）。同時に 2 つは走らせない（`pull.lock`）。"""
    with server_files.directory(home(), create=True, private=True) as fd:
        try:
            with server_files.lock_at(fd, "pull.lock"):
                return _pull(fd, now or jst.now_jst())
        except BlockingIOError:
            raise WebhookError("webhook_pull_busy") from None


def _pull(fd, now) -> dict:
    state = _read_json(fd, "status.json", {})
    state["last_attempt_at"] = jst.iso(now)
    table = routes()
    after, seen, stats = None, 0, None
    routed: dict = {}
    unrouted: dict = {}
    stopped = 0
    try:
        for _ in range(PAGES_MAX):
            rows, after_next, stats = _page(relay.signed_request("webhook", "meta", "pull", {"after": after}))
            keys, by_account = [], {}
            for row in rows:
                name, is_stopped = table.get((row["object"], row["entry_id"]), (None, False))
                keys.append(row["key"])
                if name is None:
                    unrouted[row["object"]] = unrouted.get(row["object"], 0) + 1
                elif is_stopped:
                    stopped += 1
                else:
                    by_account.setdefault(name, []).append(row)
            for name, items in by_account.items():
                _store(name, items, now)
                routed[name] = routed.get(name, 0) + len(items)
            for i in range(0, len(keys), ACK_MAX):
                result = relay.signed_request("webhook", "meta", "ack", {"keys": keys[i:i + ACK_MAX]})
                if type(result) is not dict or type(result.get("acked")) is not int:
                    raise WebhookError("webhook_ack_unconfirmed")
            seen += len(rows)
            if after_next is None:
                break
            after = after_next
    except (relay.RelayError, WebhookError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or (str(exc).split(":")[0] if str(exc) else type(exc).__name__)
        if not state.get("down_since"):
            state["down_since"], state["down_reason"] = jst.iso(now), reason[:80]
        server_files.replace_at(fd, "status.json", server_files.encode(state), private=True)
        raise WebhookError("webhook_pull_failed: " + reason[:80]) from None
    previous = state.get("worker") if isinstance(state.get("worker"), dict) else None
    if state.get("down_since"):
        began = jst.parse(state["down_since"])
        if began is not None and now - began >= DOWN_AFTER:
            _append_line(fd, "outages.ndjson", {"kind": "pull_failed", "from": state["down_since"], "to": jst.iso(now),
                                                "reason": state.get("down_reason")})
        state.pop("down_since", None)
        state.pop("down_reason", None)
    if previous is not None:
        for counter, kind in (("expired", "expired"), ("overflow", "capacity")):
            delta = (stats.get(counter) or 0) - (previous.get(counter) or 0)
            if delta > 0:
                _append_line(fd, "outages.ndjson", {"kind": kind, "at": jst.iso(now), "count": delta})
    totals = state.get("routed") if isinstance(state.get("routed"), dict) else {}
    for name, n in routed.items():
        totals[name] = totals.get(name, 0) + n
    dropped = state.get("unrouted") if isinstance(state.get("unrouted"), dict) else {}
    for obj, n in unrouted.items():
        dropped[obj] = dropped.get(obj, 0) + n
    state.update(last_ok_at=jst.iso(now), worker=stats, routed=totals, unrouted=dropped,
                 stopped=(state.get("stopped") or 0) + stopped)
    server_files.replace_at(fd, "status.json", server_files.encode(state), private=True)
    return {"pulled": seen, "routed": routed, "unrouted": unrouted, "stopped": stopped, "worker": stats}


_last_run = [None]


def run_due() -> dict | None:
    """`thth worker` の 1 巡から。**購読が 1 つも無ければ何もしない**（Worker を叩かない）。30 秒に 1 回。

    失敗は `status.json` に残して上げない（常駐の他の仕事を止めない）。
    """
    try:
        if not subscriptions():
            return None
    except (OSError, ValueError):
        return None
    tick = time.monotonic()
    if _last_run[0] is not None and tick - _last_run[0] < PULL_EVERY_SECONDS:
        return None
    _last_run[0] = tick
    try:
        return pull_once()
    except (WebhookError, OSError, ValueError, accounts.AccountError):
        return None


def live(account: str, *, now=None) -> bool:
    """この口座に Webhook が届いている（購読している・最後の取り込みの成功が `LIVE_HOURS` 以内・止まっていない）。"""
    try:
        if account not in subscriptions():
            return False
        state = status()
    except (OSError, ValueError):
        return False
    ok = jst.parse(state.get("last_ok_at")) if isinstance(state.get("last_ok_at"), str) else None
    now = now or jst.now_jst()
    return ok is not None and not state.get("down_since") and now - ok <= datetime.timedelta(hours=LIVE_HOURS)


# ------------------------------------------------------------------ 購読

def _app_id():
    from . import appenv
    try:
        app_id, _secret, _config = appenv.load_meta_app_env(log=lambda _line: None)
    except (appenv.AppEnvError, OSError):
        return None
    return app_id or None


def subscribe(account: str, *, by: str) -> dict:
    """ページに THTH のアプリの Webhook を購読させる（足すだけ・外さない）。止めた口座は断る。"""
    admin_log.actor(by)
    from . import adapters, leave_gate
    leave_gate.require_active(account)
    cfg = accounts.load_account(account)
    medium = cfg.get("media")
    if medium not in SUBSCRIBE_FIELDS:
        raise WebhookError("webhook_unsupported_medium: Webhook の購読は facebook・instagram の口座だけです"
                           f"（この口座は {medium}）")
    entry = str(cfg.get(ID_KEY[medium]) or "")
    adapter = adapters.make_adapter(cfg, accounts.load_token(cfg))
    result = adapter.subscribe_webhooks(SUBSCRIBE_FIELDS[medium], app_id=_app_id())
    record = {"medium": medium, "object": OBJECT_BY_MEDIUM[medium], "entry_id": entry,
              "page_id": str(cfg.get("page_id") or ""), "fields": result["fields"],
              "wanted": list(SUBSCRIBE_FIELDS[medium]), "subscribed_at": jst.iso(), "by": by}
    with server_files.directory(home(), create=True, private=True) as fd:
        with server_files.lock_at(fd, "subscriptions.lock"):
            subs = _read_json(fd, "subscriptions.json", {})
            subs[account] = record
            server_files.replace_at(fd, "subscriptions.json", server_files.encode(subs), private=True)
    admin_log.append("webhook_subscribed", account, cfg, by=by,
                     diff={"subscribed_fields": [result["before"], result["fields"]]})
    return {"account": account, "medium": medium, **result}


# ------------------------------------------------------------------ events（`thth events` の webhook_down）

def _range(row) -> str:
    def short(value):
        at = jst.parse(value) if isinstance(value, str) else None
        return at.strftime("%m/%d %H:%M") if at else "?"
    return f"{short(row.get('from'))}〜{short(row.get('to'))}"


def events_for(account: str, *, now, make) -> list:
    """購読している口座の `webhook_down`（受け口から取り込めなかった時間・消えた数・断った数・今も続く状態）。"""
    subs = subscriptions()
    if account not in subs:
        return []
    out = []
    for row in outages():
        kind = row.get("kind")
        if kind == "pull_failed":
            out.append(make(jst.parse(row.get("from")), jst.parse(row.get("to")), "pull_failed",
                            f"Webhook の受け口から取り込めなかった時間: {_range(row)}（{row.get('reason')}）。"
                            "Meta は 36 時間まで送り直す。それより長く落ちた分は取り直せない", False))
        elif kind in ("expired", "capacity"):
            at = jst.parse(row.get("at"))
            text = (f"Webhook の受け口で、取り込む前に 7 日を過ぎて消えた通知 {row.get('count')} 件（取り直せない）"
                    if kind == "expired" else
                    f"Webhook の受け口が満杯で断った通知 {row.get('count')} 件（Meta が 36 時間まで送り直す）")
            out.append(make(at, at, kind, text, False))
    state = status()
    began = jst.parse(state.get("down_since")) if isinstance(state.get("down_since"), str) else None
    if began is not None and now - began >= DOWN_AFTER:
        out.append(make(began, began, "pull_failed",
                        f"Webhook の受け口から取り込めていません（{jst.iso(began)} から・{state.get('down_reason')}）。"
                        "36 時間を越えると Meta は送り直さない", True))
    subscribed = jst.parse(subs[account].get("subscribed_at")) if isinstance(subs[account].get("subscribed_at"), str) else None
    if not state.get("last_ok_at") and began is None and subscribed is not None and now - subscribed >= NEVER_PULLED_AFTER:
        out.append(make(subscribed, subscribed, "never_pulled",
                        "Webhook を購読したが、VM の取り込みが一度も成功していない（thth worker が動いているか）", True))
    return out


# ------------------------------------------------------------------ CLI

def _print(value, as_json):
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_subscribe(args) -> int:
    from .adapters import base
    try:
        result = subscribe(args.account, by=args.by)
    except base.PermissionMissing as exc:
        print(f"{args.account}: {exc.permission} がトークンに乗っていません。thth auth {args.account} で認可し直す"
              "（購読していません）", file=sys.stderr)
        return 2
    except (WebhookError, accounts.AccountError, base.AdapterError, ValueError, OSError) as exc:
        from . import redact
        print(f"{args.account}: webhook_subscribe_failed: {redact.redact(str(exc))}", file=sys.stderr)
        return 1
    if args.json:
        _print(result, True)
        return 0
    added = "・".join(result["added"]) or "（足す欄なし・もう購読済み）"
    print(f"{args.account}: ページ {result['page_id']} の Webhook を購読しました。欄: {'・'.join(result['fields'])}"
          f"（今回足した欄: {added}）")
    if result["medium"] == "instagram":
        print("  IG の mentions は、アプリの管理画面（Webhooks → Instagram）で mentions の欄を選んだときだけ届きます。"
              "届いたかは thth webhook status で見ます")
    print("  届いた通知は thth worker が取り込み、thth replies・thth mentions・thth events に出ます。")
    return 0


def cmd_pull(args) -> int:
    try:
        result = pull_once()
    except (WebhookError, OSError, ValueError, accounts.AccountError) as exc:
        print(f"webhook_pull_failed: {str(exc)[:200]}", file=sys.stderr)
        return 1
    if args.json:
        _print(result, True)
    else:
        print(f"取り込み {result['pulled']} 件（口座へ {sum(result['routed'].values())}・知らない id "
              f"{sum(result['unrouted'].values())}・止めた口座 {result['stopped']}）")
    return 0


def cmd_status(args) -> int:
    subs = subscriptions()
    state = status()
    payload = {"subscriptions": subs, "status": state,
               "accounts": {name: counts_for(name) for name in sorted(subs) if accounts.name_is_safe(name)},
               "outages": outages()[-20:], "live": {name: live(name) for name in sorted(subs)}}
    if args.json:
        _print(payload, True)
        return 0
    if not subs:
        print("Webhook を購読している口座はありません（thth webhook subscribe <口座> --by <名前>）")
    for name, row in sorted(subs.items()):
        c = payload["accounts"].get(name, {})
        print(f"{name}: {row.get('medium')} {row.get('object')} {row.get('entry_id')}  欄 {'・'.join(row.get('fields') or [])}"
              f"  記録 {c.get('rows', 0)} 件 {c.get('by_field', {})}  届いている: {'はい' if payload['live'][name] else 'いいえ'}")
    print(f"最後の取り込み: {state.get('last_ok_at') or 'まだ'}"
          + (f"  取り込めていない: {state['down_since']} から（{state.get('down_reason')}）" if state.get("down_since") else ""))
    worker = state.get("worker") or {}
    if worker:
        print("Worker: " + " ".join(f"{k}={worker.get(k)}" for k in WORKER_COUNTERS))
    if state.get("unrouted") or state.get("stopped"):
        print(f"捨てた通知: 知らない id {state.get('unrouted') or {}}・止めた口座 {state.get('stopped') or 0}")
    return 0


def register(sub) -> None:
    parser = sub.add_parser("webhook", help="Meta の Webhook（FB の feed・mention）の購読と取り込みの様子")
    ops = parser.add_subparsers(required=True)
    p = ops.add_parser("subscribe", help="ページに Webhook を購読させる（足すだけ・外さない）")
    p.add_argument("account")
    p.add_argument("--by", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_subscribe)
    p = ops.add_parser("pull", help="Worker から 1 回取り込む（ふだんは thth worker が 30 秒ごとに）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pull)
    p = ops.add_parser("status", help="購読・最後の取り込み・口座ごとの数・取り込めなかった時間（読むだけ）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)
