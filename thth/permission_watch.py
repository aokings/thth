"""権限の不使用の見張り（計画 Meta 全部盛り §3.3・§4 の M5 の (a)(b)(c)・設計 監査と審査の機械化 §2 の (2)）。

Meta は使わない権限を止めることがある: Platform Terms 7(e)(iii) はアプリが 28 日使わなかった権限、権限の参照は
利用者ごとに 90 日、devpolicy 10.4 は広告の段を 30 日。ここは止まる前に hub へ知らせる。

**(a) 最後に成功した日**は今ある記録から出す。新しい本文は残さない:

| 記録 | 何を言えるか |
|---|---|
| `runs-*.ndjson` の `collect`（status ok） | 読む権限（`collected` が 1 本以上なら投稿ごとの読みも） |
| `runs-*.ndjson` の `where_to_appear`・`mentions`・`thread_read`・`who_is_this` | その口の権限 |
| `runs-*.ndjson` の `permission_keepalive`（status ok） | 最小の読みで使った権限（`permission` の鍵） |
| `sent/`（`sent_at`・`reply_to`・`retracted_at`） | 投稿・返信・取り下げの権限 |
| `.token` の `obtained_at` | 認可で使った権限（`auth_log` のログには成功の印が無いので、こちらを読む） |
| 広告の `snapshot.json` の `last_ok_at`・`ops/` の `result: ok` | `ads_read`・`ads_management` |

`meta_usage.json` は最新の 1 応答しか持たず成功か分からないので、日付には使わず、最小の読みを控える判断にだけ使う。
`collect` の ok は 1 本の失敗も無かった回で、権限ごとの呼び出しを 1 つずつ記録したものではない（見立て）。
記録が無い権限は `.token` の `obtained_at` から数える（`counted_from: token_obtained`）。

**見張る権限**は Advanced の権限と広告の 2 つ。Standard の権限は表に並べるだけで知らせない（口の無いものが
毎日鳴らないように）。審査が通ったら `thth admin meta set advanced …` で足す。

**最小の読み**（`keepalive`）は読むだけの権限の、アプリで 25 日・持ち主の口座で 80 日に打つ。書く権限は打たず
知らせだけ（勝手に投稿しない）。招待の口座の鍵では打たない。媒体は Meta の 3 つと広告だけなので X の予算は使わない。
Meta の待ちの間と、使用率が 50% を超えている間は打たない。同じ権限は同じ口座で 1 日 1 回まで、1 回の実行で
`MAX_CALLS_PER_RUN` 本まで。データに触れる期限（90 日）は利用者の活動で延びるので、最小の読みでは延びない見立て。

**(b)** 広告の段の数は `thth/meta/calls.py`。**(c)** 期限の台帳は `.token` の `data_access_expires_at` と、
人が読んで入れる Data Use Checkup の期日（`state/_meta/ledger.json`）。

**知らせの先は hub**: `thth board --json` の各口座の `stuck` と、管理者の observe の 1 行。本物の止まりが先で、
ここの行はその口座に止まりが無いときだけ出る。アプリ全体の話は持ち主の口座 1 つに載せる（同じ話を並べない）。
読むだけで、keepalive と set のほかは何も書かない。
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.error

from . import accounts, jst, refusal, secrets_fs
from . import runs as runs_mod
from . import sent as sent_mod

WARN_DAYS = 21          # 知らせる（28 日の 1 週間前）
KEEP_DAYS = 25          # 読むだけの権限に最小の読みを打つ
LIMIT_DAYS = 28         # Platform Terms 7(e)(iii)
USER_WARN_DAYS = 75     # 利用者ごとの 90 日の手前
USER_KEEP_DAYS = 80
USER_LIMIT_DAYS = 90
DUC_WARN_DAYS = 30
DATA_ACCESS_WARN_DAYS = 14.0
QUOTA_CEILING_PCT = 50
QUOTA_FRESH_MINUTES = 60    # 量の窓は 1 時間。古い使用率で止めない
MAX_CALLS_PER_RUN = 12
RUN_MONTHS = 4              # runs を遡る月（90 日より少し長く）

META_MEDIA = ("threads", "facebook", "instagram")
ADS = "ads"
READ, WRITE = "read", "write"
ACTION = "permission_keepalive"


def _p(media, kind, *evidence, also=None):
    """`also` は、ほかの媒体の口座でも同じ権限を使う所（媒体 → 記録の種類）。審査の表の媒体は `media` のまま。"""
    return {"media": media, "kind": kind, "evidence": evidence, "also": dict(also or {})}


# 権限 → 媒体・読む／書く・成功を言える記録。THTH の認可が求める権限は全部ここに並ぶ（試験が縛る）。
# 記録が無い権限（口がまだ無い）は `evidence` が空。口を足したら記録の種類もここに足す。
PERMISSIONS = {
    "threads_basic": _p("threads", READ, "collect"),
    "threads_content_publish": _p("threads", WRITE, "post"),
    "threads_delete": _p("threads", WRITE, "retract"),
    "threads_keyword_search": _p("threads", READ, "where"),
    "threads_location_tagging": _p("threads", READ, "location_search"),
    "threads_manage_insights": _p("threads", READ, "collect_posts"),
    "threads_manage_mentions": _p("threads", READ, "collect", "mentions"),
    "threads_manage_replies": _p("threads", WRITE, "reply", "reply_moderate", "reply_approval"),
    "threads_profile_discovery": _p("threads", READ, "who"),
    "threads_read_replies": _p("threads", READ, "collect_posts", "thread_read", "reply_pending"),
    "threads_share_to_instagram": _p("threads", WRITE, "share_to_instagram"),
    "pages_show_list": _p("facebook", READ, "auth"),
    "business_management": _p("facebook", READ, "auth"),
    "public_profile": _p("facebook", READ, "auth"),
    "pages_read_engagement": _p("facebook", READ, "collect"),
    "pages_read_user_content": _p("facebook", READ, "collect_posts"),
    "read_insights": _p("facebook", READ, "collect_posts"),
    "pages_manage_metadata": _p("facebook", READ, "webhook_subscribe"),
    "pages_manage_posts": _p("facebook", WRITE, "post", "retract"),
    "pages_manage_engagement": _p("facebook", WRITE, "reply", "reply_moderate"),
    "pages_manage_ads": _p(ADS, WRITE, "ads_op"),
    "pages_manage_cta": _p("facebook", WRITE, "page_cta_delete"),
    "publish_video": _p("facebook", WRITE, "live_create", "live_end"),
    "pages_messaging": _p("facebook", WRITE, "dm_list", "dm_read", "dm_send"),
    "pages_utility_messaging": _p("facebook", WRITE),
    "instagram_basic": _p("instagram", READ, "collect"),
    "instagram_content_publish": _p("instagram", WRITE, "post"),
    "instagram_manage_comments": _p("instagram", READ, "collect_posts", "reply", "reply_moderate"),
    "instagram_manage_insights": _p("instagram", READ, "collect_posts"),
    "instagram_manage_contents": _p("instagram", WRITE, "retract"),
    "instagram_manage_engagement": _p("instagram", WRITE, "ig_like"),
    "instagram_manage_messages": _p("instagram", WRITE, "dm_list", "dm_read", "dm_send"),
    # IG の doctor の probe と、広告の鍵の側の使用（Threads を出し先にした boost が connected_threads_user を
    # 引いた・threads を含む creative を作った・`_ad_row` の `ads_threads`・11/07 に ads の SCOPES に足した）。
    "threads_business_basic": _p("instagram", READ, "connected_threads_user", also={"ads": ("ads_threads",)}),
    "ads_read": _p(ADS, READ, "ads_snapshot"),
    "ads_management": _p(ADS, WRITE, "ads_op"),
    "catalog_management": _p(ADS, READ, "ads_catalog"),
    "leads_retrieval": _p(ADS, READ, "ads_leads"),
    "ads_mcp_management": _p(ADS, READ),     # 口が無い（外の公式の広告 MCP で使う・THTH の記録に残らない）
    # 計画 Meta 全部盛り M3 が `META_INVITE_EXTRA` に足した 3 つ。
    "instagram_manage_upcoming_events": _p("instagram", READ, "upcoming_events"),
    "read_audience_network_insights": _p(ADS, READ, "ads_network"),
    "commerce_account_read_settings": _p(ADS, READ, "ads_shop"),
    # 計画 Meta 全部盛り M4: `thth dm read --profile` が 1 欄ずつ読む相手の属性（招待の一覧に足すかは M0 で決める）。
    "pages_user_locale": _p("facebook", READ, "dm_profile"),
    "pages_user_timezone": _p("facebook", READ, "dm_profile"),
    "pages_user_gender": _p("facebook", READ, "dm_profile"),
}
# 承認済み（Threads は 10/9 までに 10 権限）と、Standard でも 30 日で下げられうる広告の 2 つ（devpolicy 10.4）。
WATCHED_BASE = frozenset({
    "threads_basic", "threads_content_publish", "threads_delete", "threads_keyword_search",
    "threads_location_tagging", "threads_manage_insights", "threads_manage_mentions",
    "threads_manage_replies", "threads_profile_discovery", "threads_read_replies",
    "ads_read", "ads_management"})
# 最小の読みを打てる広告の権限（媒体の口座は各 adapter の `KEEPALIVE_READS`）。
ADS_KEEPALIVE_READS = frozenset({"ads_read"})

RUN_ACTIONS = ("collect", "where_to_appear", "mentions", "thread_read", "who_is_this", "upcoming_events", ACTION,
               # 計画 Meta 全部盛り M4 の書く口（`thth/meta_write.py`・`thth/reaction_cli.py` の Instagram のいいね）。
               "page_cta_delete", "live_create", "live_end", "dm_list", "dm_read", "dm_send", "dm_profile", "ig_like",
               # 審査の録画で本物を打つ読む口（命令の名前・媒体・結果だけを runs に残す）。広告のものは
               # `adaccount_state/<名前>/` の runs（`_ad_row` が読む）。
               "location_search", "webhook_subscribe", "connected_threads_user",
               "ads_catalog", "ads_leads", "ads_network", "ads_shop",
               # 返信の管理（`thth reply`・`thth/edit_cli.py`・11/06）: 隠す・戻す・消す／承認待ちを読む／承認・無視。
               "reply_moderate", "reply_pending", "reply_approval")
# 知らせの重さの順（1 口座に 1 行だけ載るので、重いものを載せて残りの数を添える）。
NOTICE_REFUSALS = ("data_access_expired", "duc_overdue", "permission_unused", "data_access_expiring",
                   "duc_due_soon", "ads_tier_at_risk", "ads_tier_ready")
# 最小の読みの結果の符丁と台帳の断り（`thth/refusal_catalog_part3.py`）。
KEEPALIVE_REFUSALS = ("permission_keepalive_denied", "permission_keepalive_failed", "permission_keepalive_no_target",
                      "keepalive_quota_high", "keepalive_tried_today", "keepalive_next_run", "meta_rate_waiting")
LEDGER_REFUSALS = ("meta_ledger_key_unknown", "meta_ledger_value_invalid", "meta_ledger_unreadable")


# ---------------------------------------------------------------- 台帳（人が読んで入れる値）

LEDGER_KEYS = ("duc_due", "ads_tier", "advanced")
ADS_TIERS = ("limited", "full")


def ledger_path() -> str:
    return os.path.join(accounts.thth_root(), "state", "_meta", "ledger.json")


def read_ledger() -> dict:
    """台帳の中身（無ければ空・読めなければ `{"unreadable": True}`）。"""
    try:
        with open(ledger_path(), encoding="utf-8") as stream:
            value = json.loads(stream.read(65537))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"unreadable": True}
    return value if isinstance(value, dict) else {"unreadable": True}


def _parse_ledger_value(key, value):
    """検査した値（直せない値は `meta_ledger_value_invalid`）。"""
    if key == "duc_due":
        try:
            return datetime.date.fromisoformat(value).isoformat()
        except ValueError:
            raise LedgerRefused("meta_ledger_value_invalid") from None
    if key == "ads_tier":
        if value not in ADS_TIERS:
            raise LedgerRefused("meta_ledger_value_invalid")
        return value
    names = sorted({name.strip() for name in value.split(",") if name.strip()})
    if any(name not in PERMISSIONS for name in names):
        raise LedgerRefused("meta_ledger_value_invalid")
    return names


class LedgerRefused(ValueError):
    """台帳の断り（`code` は静的な符丁）。"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def set_ledger(key, value, *, by, now=None) -> dict:
    """台帳の 1 項目を書く（`advanced` は全部を置き換える）。誰がいつ書いたかも残す。"""
    if key not in LEDGER_KEYS:
        raise LedgerRefused("meta_ledger_key_unknown")
    row = read_ledger()
    if row.get("unreadable"):
        raise LedgerRefused("meta_ledger_unreadable")
    row[key] = _parse_ledger_value(key, value)
    row.setdefault("updated", {})[key] = {"at": jst.iso(now or jst.now_jst()), "by": by}
    secrets_fs.atomic_write_json(ledger_path(), row)
    return row


# ---------------------------------------------------------------- 記録を読む

def _run_at(row):
    run_id = row.get("run_id")
    return jst.parse(run_id.split("-", 1)[1]) if isinstance(run_id, str) and "-" in run_id else None


def _months(now):
    year, month = now.year, now.month
    for _ in range(RUN_MONTHS):
        yield f"{year:04d}-{month:02d}"
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)


def _note(found, kind, at):
    if at is not None and (found.get(kind) is None or at > found[kind]):
        found[kind] = at


def _read_runs(state_dir, now, found, keepalive) -> None:
    """runs の要る行だけを読む（`maintain` と本数の多い行は JSON にしない）。"""
    needles = tuple(f'"action": "{action}"' for action in RUN_ACTIONS)
    for month in _months(now):
        try:
            with open(runs_mod.path_for(state_dir, month), encoding="utf-8") as stream:
                lines = stream.readlines()
        except OSError:
            continue
        for line in lines:
            if not any(needle in line for needle in needles):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            at = _run_at(row) if isinstance(row, dict) else None
            if at is None:
                continue
            action, ok = row.get("action"), row.get("status") == "ok"
            if action == ACTION:
                name = row.get("permission")
                if isinstance(name, str) and (name not in keepalive or at > keepalive[name]["at"]):
                    keepalive[name] = {"at": at, "error": None if ok else row.get("error")}
                if ok:
                    _note(found, "keepalive:" + str(name), at)
            elif not ok:
                continue
            elif action == "collect":
                _note(found, "collect", at)
                if isinstance(row.get("collected"), int) and row["collected"] > 0:
                    _note(found, "collect_posts", at)
            elif action == "where_to_appear":
                _note(found, "where", at)
            elif action == "who_is_this":
                if row.get("profile_fetched") is True:
                    _note(found, "who", at)
            else:
                _note(found, action, at)


def _read_sent(state_dir, found) -> None:
    for row in sent_mod.records(state_dir):
        at = jst.parse(row.get("sent_at"))
        _note(found, "reply" if row.get("reply_to") else "post", at)
        _note(found, "retract", jst.parse(row.get("retracted_at")))
        if row.get("share_to_instagram") is True:
            _note(found, "share_to_instagram", at)


def _held(media, token) -> set:
    """鍵に乗っている権限（一覧が無い鍵は、その媒体の権限を全部持つとして数える）。"""
    if not isinstance(token, dict) or token.get("unreadable"):
        return set()
    expected = {name for name, spec in PERMISSIONS.items() if spec["media"] == media or media in spec["also"]}
    scopes = token.get("scopes")
    return expected & set(scopes) if isinstance(scopes, list) else expected


def _account_row(name, cfg, now):
    try:
        token = accounts.load_token(cfg, refresh_x=False)
    except Exception:  # noqa: BLE001 — 読めない鍵は持っていない扱い（鍵の止まりは maintain が言う）
        token = None
    state_dir = accounts.state_dir_for(name)
    found, keepalive = {}, {}
    _read_runs(state_dir, now, found, keepalive)
    _read_sent(state_dir, found)
    obtained = jst.parse(token.get("obtained_at")) if isinstance(token, dict) else None
    _note(found, "auth", obtained)
    return {"name": name, "media": cfg.get("media"), "owner": not accounts.is_invite_account(cfg), "cfg": cfg,
            "held": _held(cfg.get("media"), token), "found": found, "keepalive": keepalive, "obtained": obtained,
            "data_access": _data_access(cfg, now)}


def _data_access(cfg, now):
    from . import maintain
    access = maintain.data_access_for(cfg, now=now)
    return access if access else None


def _ad_row(name, now):
    from .ads import ledger, ops, watch
    cfg = ledger.load(name)
    token = ledger.load_token(cfg)
    found, keepalive = {}, {}
    _read_runs(ledger.state_dir_for(name), now, found, keepalive)
    snapshot = watch.read_snapshot(name) or {}
    _note(found, "ads_snapshot", jst.parse(snapshot.get("last_ok_at")))
    for row in ops.read_ops(name):
        at = jst.parse(row.get("at"))
        if row.get("result") == "ok":
            _note(found, "ads_op", at)
        plan = row.get("plan") if isinstance(row.get("plan"), dict) else {}
        created = row.get("created") if isinstance(row.get("created"), dict) else {}
        # threads_business_basic の広告の側の使用: connected_threads_user を引けた（そのあと作るのに失敗しても
        # 読みは通った）か、threads_user_id を入れた creative を作れた。
        if row.get("op") == "boost" and (plan.get("threads_user_source") == "connected_threads_user"
                                         or (plan.get("threads_user_id") and created.get("creative_id"))):
            _note(found, "ads_threads", at)
    obtained = jst.parse(token.get("obtained_at")) if isinstance(token, dict) else None
    limit = ledger.token_limits(token or {})["data_access_expires_at"] if isinstance(token, dict) else None
    access = None
    if limit is not None:
        remaining = (limit - now.timestamp()) / 86400.0
        state = ("data_access_expired" if remaining <= 0
                 else "data_access_expiring" if remaining < DATA_ACCESS_WARN_DAYS else None)
        access = {"state": state, "remaining_days": round(remaining, 2),
                  "expires_at": jst.iso(datetime.datetime.fromtimestamp(limit, jst.JST))}
    return {"name": name, "media": ADS, "owner": True, "cfg": cfg, "held": _held(ADS, token), "found": found,
            "keepalive": keepalive, "obtained": obtained, "data_access": access}


def _rows(now) -> list:
    """Meta の口座と広告の口座の行。止めた口座と読めない台帳は飛ばす。X はここに入らない。"""
    rows = []
    for name in accounts.list_account_names():
        try:
            cfg = accounts.load_account(name)
        except (accounts.AccountError, ValueError, TypeError):
            continue
        if cfg.get("media") in META_MEDIA:
            rows.append(_account_row(name, cfg, now))
    from .ads import ledger
    for name in ledger.list_names():
        try:
            rows.append(_ad_row(name, now))
        except (accounts.AccountError, OSError, ValueError):
            continue
    return rows


# ---------------------------------------------------------------- 見立て

def _last(row, permission):
    """その口座の最後の成功（時刻・無ければ None）。"""
    spec = PERMISSIONS[permission]
    evidence = spec["evidence"] if row["media"] == spec["media"] else spec["also"].get(row["media"], ())
    times = [row["found"].get(kind) for kind in (*evidence, "keepalive:" + permission)]
    return max((at for at in times if at is not None), default=None)


def _stage(days, warn, keep, limit) -> str:
    if days is None:
        return "unknown"
    return "quiet" if days < warn else "warn" if days < keep else "keep" if days < limit else "over"


def _can_call(row, permission) -> bool:
    if PERMISSIONS[permission]["kind"] != READ:
        return False
    if row["media"] == ADS:
        return permission in ADS_KEEPALIVE_READS
    from . import adapters
    return permission in getattr(adapters.adapter_class(row["media"]), "KEEPALIVE_READS", ())


def _permission_row(permission, rows, watched, now) -> dict:
    spec = PERMISSIONS[permission]
    same = [row for row in rows if row["media"] == spec["media"] or row["media"] in spec["also"]]
    holders = [row for row in same if permission in row["held"]]
    best = max(((at, row["name"]) for row in same if (at := _last(row, permission))), default=(None, None))
    since, counted_from = best[0], "record"
    if since is None:
        since = min((row["obtained"] for row in holders if row["obtained"]), default=None)
        counted_from = "token_obtained" if since else None
    days = (now - since).days if since else None
    tried = [row["keepalive"][permission] for row in holders if permission in row["keepalive"]]
    latest = max(tried, key=lambda item: item["at"], default=None)
    return {"permission": permission, "media": spec["media"], "kind": spec["kind"],
            "watched": permission in watched, "holders": len(holders),
            "last_success_at": jst.iso(best[0]) if best[0] else None, "last_success_account": best[1],
            "counted_from": counted_from, "days_idle": days,
            "stage": _stage(days, WARN_DAYS, KEEP_DAYS, LIMIT_DAYS) if holders else "not_held",
            "keepalive": any(_can_call(row, permission) for row in holders[:1]),
            "keepalive_error": latest["error"] if latest else None,
            "since": jst.iso(since + datetime.timedelta(days=WARN_DAYS)) if since else None}


def _user_rows(rows, watched, now) -> list:
    """持ち主の口座ごとの 90 日（75 日を越えた権限だけ）。"""
    out = []
    for row in rows:
        if not row["owner"]:
            continue
        for permission in sorted(row["held"] & watched):
            since = _last(row, permission) or row["obtained"]
            days = (now - since).days if since else None
            stage = _stage(days, USER_WARN_DAYS, USER_KEEP_DAYS, USER_LIMIT_DAYS)
            if stage not in ("quiet", "unknown"):
                out.append({"account": row["name"], "media": row["media"], "permission": permission,
                            "kind": PERMISSIONS[permission]["kind"], "days_idle": days, "stage": stage,
                            "since": jst.iso(since + datetime.timedelta(days=USER_WARN_DAYS))})
    return out


def _duc(ledger, now) -> dict:
    due = ledger.get("duc_due")
    try:
        days = (datetime.date.fromisoformat(due) - now.date()).days
    except (TypeError, ValueError):   # 無い・手で壊した値は「分からない」（`set` は形を検査して書く）
        return {"due": None, "days_left": None, "state": "unset"}
    state = "overdue" if days < 0 else "due_soon" if days <= DUC_WARN_DAYS else "ok"
    return {"due": due, "days_left": days, "state": state}


def _ads_tier(rows, ledger, now) -> dict:
    from .ads import ledger as ads_ledger
    from .meta import calls
    names = [row["name"] for row in rows if row["media"] == ADS]
    paths = [calls.path_beside(ads_ledger.usage_path_for(name)) for name in names]
    return {"tier": ledger.get("ads_tier") or "limited", "adaccounts": len(names), **calls.summary(paths, now=now)}


def _parts(code, **values) -> dict:
    found = refusal.parts(code, **values) or {}
    return {"fix": found.get("fix"), "better": found.get("better")}


def _notice(code, what, since, level, account=None, **values) -> dict:
    return {"code": code, "since": since, "what": what, "level": level, "account": account,
            **_parts(code, **({"account": account} if account else {}), **values)}


def _permission_notice(rows) -> dict | None:
    warned = sorted((row for row in rows if row["watched"] and row["stage"] in ("warn", "keep", "over")),
                    key=lambda row: -row["days_idle"])
    if not warned:
        return None
    head = warned[0]
    writes = sum(1 for row in warned if row["kind"] == WRITE)
    what = (f"権限 {len(warned)} 件が {WARN_DAYS} 日以上使われていません（{head['permission']} {head['days_idle']} 日"
            + (f"ほか・うち書く権限 {writes} 件" if len(warned) > 1 else "") + "）")
    over = any(row["stage"] == "over" for row in warned)
    notice = _notice("permission_unused", what, min(row["since"] for row in warned), "red" if over else "yellow")
    notice["media"] = head["media"]
    notice["permissions"] = [row["permission"] for row in warned]
    return notice


def _notices(permissions, users, rows, duc, tier, ledger) -> list:
    out = []
    notice = _permission_notice(permissions)
    if notice:
        out.append(notice)
    by_account = {}
    for row in users:
        by_account.setdefault(row["account"], []).append(row)
    for name, group in sorted(by_account.items()):
        head = max(group, key=lambda row: row["days_idle"])
        what = (f"この口座で権限 {len(group)} 件が {USER_WARN_DAYS} 日以上使われていません"
                f"（{head['permission']} {head['days_idle']} 日）。{USER_LIMIT_DAYS} 日で認可のやり直しが要ることがあります")
        out.append(_notice("permission_unused", what, min(row["since"] for row in group),
                           "red" if head["stage"] == "over" else "yellow", account=name))
    for row in rows:
        access = row["data_access"]
        if row["owner"] and access and access["state"]:
            expired = access["state"] == "data_access_expired"
            what = (f"{row['name']} のデータに触れる期限（{access['expires_at']}）を過ぎました" if expired else
                    f"{row['name']} のデータに触れる期限まで残り {access['remaining_days']:.1f} 日です（{access['expires_at']}）")
            target = None if row["media"] == ADS else row["name"]
            out.append(_notice(access["state"], what, access["expires_at"] if expired else None,
                               "red" if expired else "yellow", account=target))
            if target is None:
                out[-1]["fix"] = f"`thth ads auth {row['name']} --by <名前>` で認可し直してください"
    if duc["state"] == "overdue":
        out.append(_notice("duc_overdue", f"Data Use Checkup の期日（{duc['due']}）を過ぎています", duc["due"], "red"))
    elif duc["state"] == "due_soon":
        out.append(_notice("duc_due_soon", f"Data Use Checkup の期日まで残り {duc['days_left']} 日です（{duc['due']}）",
                           None, "yellow"))
    if tier["adaccounts"] and tier["tier"] == "limited" and tier["meets"]:
        out.append(_notice("ads_tier_ready", f"Marketing API の Full の条件に届きました（15 日で {tier['calls_15d']} 回"
                           f"・直近 {tier['last_n']} 回のエラー率 {tier['error_rate']:.1%}）", None, "yellow"))
    if tier["adaccounts"] and tier["tier"] == "full" and not tier["meets"]:
        out.append(_notice("ads_tier_at_risk", f"Marketing API の Full の条件を割っています（15 日で {tier['calls_15d']} 回"
                           f"・エラー率 {tier['error_rate'] if tier['error_rate'] is not None else '不明'}）",
                           None, "yellow"))
    return out


def build(*, now=None) -> dict:
    """見張りの全部（読むだけ）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    return _view(_rows(now), now)


def _view(rows, now) -> dict:
    ledger = read_ledger()
    unreadable = bool(ledger.get("unreadable"))
    extra = ledger.get("advanced") if isinstance(ledger.get("advanced"), list) else []
    watched = WATCHED_BASE | {name for name in extra if name in PERMISSIONS}
    permissions = [_permission_row(name, rows, watched, now) for name in sorted(PERMISSIONS)]
    users = _user_rows(rows, watched, now)
    duc = _duc(ledger, now)
    tier = _ads_tier(rows, ledger, now)
    return {"generated_at": jst.iso(now), "permissions": permissions, "accounts": users,
            "data_access": [{"account": row["name"], **row["data_access"]} for row in rows if row["data_access"]],
            "duc": duc, "ads_tier": tier, "cannot_say": "meta_ledger_unreadable" if unreadable else None,
            "notices": _notices(permissions, users, rows, duc, tier, ledger),
            "representative": _representative(rows, permissions)}


def _representative(rows, permissions):
    """アプリ全体の話を載せる口座（いちばん古い権限を持つ媒体の、名前順で最初の持ち主の口座）。"""
    owners = [row for row in rows if row["owner"] and row["media"] != ADS]
    if not owners:
        return None
    idle = [row for row in permissions if row["watched"] and row["stage"] in ("warn", "keep", "over")]
    media = max(idle, key=lambda row: row["days_idle"])["media"] if idle else None
    same = [row["name"] for row in owners if row["media"] == media]
    return sorted(same or [row["name"] for row in owners])[0]


def stuck_rows(view) -> dict:
    """board の `stuck` に載せる行（口座 → 行）。鍵・本文・宛先は入らない。"""
    groups = {}
    for notice in view["notices"]:
        target = notice["account"] or view["representative"]
        if target:
            groups.setdefault(target, []).append(notice)
    out = {}
    for name, group in groups.items():
        head = min(group, key=lambda row: NOTICE_REFUSALS.index(row["code"]))
        rest = f"（ほか {len(group) - 1} 件: thth admin meta watch）" if len(group) > 1 else ""
        out[name] = {"code": head["code"], "since": head["since"], "what": head["what"] + rest,
                     "fix": head["fix"], "better": head["better"], "level": head["level"]}
    return out


def board_cell(view) -> dict:
    """board の上の段の `meta_watch`（足すだけ）。数と符丁だけ。"""
    return {"notices": [{key: row[key] for key in ("code", "since", "what", "fix", "level", "account")}
                        for row in view["notices"]],
            "duc": view["duc"], "ads_tier": view["ads_tier"], "cannot_say": view["cannot_say"]}


def observe_line(view) -> str | None:
    """observe の 1 行（知らせが無ければ None）。期日が台帳に無いだけのときも 1 行で言う。"""
    parts = [row["what"] for row in view["notices"]]
    if view["cannot_say"]:
        parts.append("台帳を読めません")
    elif view["duc"]["state"] == "unset" and any(row["holders"] for row in view["permissions"]):
        parts.append("Data Use Checkup の期日が台帳にありません")
    return "Meta の見張り: " + "／".join(parts) + "（thth admin meta watch）" if parts else None


# ---------------------------------------------------------------- 最小の読み

def _plan(rows, view) -> list:
    """打つ `(口座の行, 権限)`。アプリの 25 日が先、持ち主の口座の 80 日があと。招待の口座の鍵は使わない。"""
    out, seen = [], set()

    def add(row, permission):
        if (row["name"], permission) not in seen and _can_call(row, permission):
            seen.add((row["name"], permission))
            out.append((row, permission))

    for item in view["permissions"]:
        if item["watched"] and item["kind"] == READ and item["stage"] in ("keep", "over"):
            holders = [row for row in rows if row["owner"] and item["permission"] in row["held"]]
            if holders:
                add(sorted(holders, key=lambda row: row["name"])[0], item["permission"])
    by_name = {row["name"]: row for row in rows}
    for item in view["accounts"]:
        if item["kind"] == READ and item["stage"] in ("keep", "over"):
            add(by_name[item["account"]], item["permission"])
    return out


def _usage_path(row):
    if row["media"] != ADS:
        return None
    from .ads import ledger
    return ledger.usage_path_for(row["name"])


def _quota_block(row, now) -> str | None:
    """打たない理由（Meta の待ち・使用率）。無ければ None。Threads は量のヘッダを残していないので見ない。"""
    from .meta import usage
    name = None if row["media"] == ADS else row["name"]
    path = _usage_path(row)
    if usage.waiting(name, now=now, path=path):
        return "meta_rate_waiting"
    value = usage.read(name, path=path) or {}
    at = jst.parse(value.get("observed_at"))
    if at is None or (now - at).total_seconds() > QUOTA_FRESH_MINUTES * 60:
        return None
    headers = value.get("headers") if isinstance(value.get("headers"), dict) else {}
    rows = [headers.get("app_usage")]
    for items in (headers.get("business_use_case_usage") or {}).values():
        rows.extend(items if isinstance(items, list) else [])
    percents = [item.get(key) for item in rows if isinstance(item, dict)
                for key in ("call_count", "total_cputime", "total_time")]
    if any(isinstance(pct, (int, float)) and pct >= QUOTA_CEILING_PCT for pct in percents):
        return "keepalive_quota_high"
    return None


def _call(row, permission) -> None:
    if row["media"] == ADS:
        from .ads import graphread, ledger
        graphread.Reader(row["name"], row["cfg"], ledger.load_token(row["cfg"])).account()
        return
    from . import adapters
    token = accounts.load_token(row["cfg"], refresh_x=False)
    adapters.make_adapter(row["cfg"], token).keepalive_read(permission)


def _error_code(exc) -> str:
    from .adapters import base
    if str(exc).startswith("permission_keepalive_no_target"):
        return "permission_keepalive_no_target"
    denied = isinstance(exc, base.PermissionMissing) or (
        isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500)
    return "permission_keepalive_denied" if denied else "permission_keepalive_failed"


def _record(row, permission, error, now) -> None:
    line = {"account": row["name"], "action": ACTION, "medium": row["media"], "permission": permission,
            "status": "error" if error else "ok", "error": error}
    if row["media"] == ADS:
        from .ads import ledger
        runs_mod.append_run(ledger.ensure_state_dir(row["name"]),
                            {**line, "mode": "read", "run_id": f"{ACTION}-{jst.iso(now)}"}, jst.month_str(now))
    else:
        runs_mod.record_minimal(row["name"], line, now=now)


def _tried_today(row, permission, now) -> bool:
    last = row["keepalive"].get(permission)
    return bool(last) and jst.to_jst(last["at"]).date() == now.date()


def keepalive(*, now=None, dry_run=False) -> dict:
    """要る権限にだけ最小の読みを打つ。`dry_run` は網に出ず、何も書かない。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    rows = _rows(now)
    view = _view(rows, now)
    results, called = [], 0
    for row, permission in _plan(rows, view):
        item = {"account": row["name"], "permission": permission}
        skip = "keepalive_tried_today" if _tried_today(row, permission, now) else _quota_block(row, now)
        if skip is None and called >= MAX_CALLS_PER_RUN:
            skip = "keepalive_next_run"
        if skip is not None:
            results.append({**item, "result": "skipped", "reason": skip})
            continue
        if dry_run:
            results.append({**item, "result": "would_call"})
            continue
        called += 1
        error = None
        try:
            _call(row, permission)
        except accounts.AccountStopped:
            results.append({**item, "result": "skipped", "reason": "account_stopped"})
            continue
        except Exception as exc:  # noqa: BLE001 — 1 つの読みで残りを止めない（文は残さず符丁だけ）
            error = _error_code(exc)
        _record(row, permission, error, now)
        results.append({**item, "result": "error" if error else "ok", **({"error": error} if error else {})})
    return {"generated_at": jst.iso(now), "dry_run": bool(dry_run), "called": called, "results": results}
