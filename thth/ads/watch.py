"""広告の口座の見張り（計画 D-5・C-2 の 5・段 4 の 3・4）。**読むだけ。Meta に書かない。**

- `collect(name)`: `/act_<id>` と木（キャンペーン・広告セット・広告の `effective_status`・予算・期間）を
  読み、`adaccount_state/<名前>/snapshot.json` に残す。日に 1 回（広告アカウントの時間帯の日）、
  `insights` を「昨日」と「今月」の範囲で。`thth collect <口座>` のたびに、その口座を `pages`／`ig_accounts`
  に持つ広告の口座を回す（`collect_linked`・`MIN_INTERVAL_MINUTES` より短い間隔では読み直さない）。
- `token_state(cfg)`: 鍵の残り日数 → `ok`・`ads_token_expiring`（**残り 14 日**・既存の 7 日の線に乗せない・
  C-2 の 5）・`expired`・`no_token`・`unreadable`・`ads_not_granted`（記録した権限に `ads_read` が無い）。
- `events(name, cfg, snapshot, now)`: 出来事（どれも続いている状態・`ongoing`）:

| 種類 | 鳴るとき |
|---|---|
| `ads_stop_unconfirmed` | **最優先**（段 5）。最後の `thth ads stop` が「止まった」と確かめられなかった（pause か読み直しが失敗）。管理画面で止める |
| `ads_active_outside_thth` | **最優先**。THTH の `activate` の記録（`ops/` の `op: activate` の `ids`）に無い物が `ACTIVE` |
| `ads_no_end_time` | 終了日の無い広告セットが `ACTIVE`（THTH は作らないので外で作られた物） |
| `ads_account_status` | `account_status` が 1（ACTIVE）以外 |
| `ads_spend_over_estimate` | 今月の実支出が THTH の予約（段 5 の `budget-<月>.json`）の 120% を越えた。予約が無ければ鳴らない |
| `ads_spend_cap_near` | `amount_spent / spend_cap` が 80% 以上（**累計**の物差し・月の残りではない） |
| `ads_budget_low` | 月の上限 `ceilings.monthly` が 0 でなく、今月の実支出が 90% 以上（予約の外の支出も数える） |
| `ads_spend_cap_unset` | `spend_cap` が 0（Meta では「上限なし」）。最後の柵が入っていない（D-1 の 1） |
| `ads_token_expiring` | 鍵の残りが 14 日を切った・過ぎた |
| `ads_read_failed` | 最後の読みが失敗した（読めない間は外で始まった配信に気づけない） |

数は 15 分ごとに更新・28 日を過ぎると動かない【確】。見積もりは「Meta の請求書ではない」。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

from .. import jst, secrets_fs
from ..meta import errors as meta_errors
from . import graphread, ledger

SNAPSHOT_FILE = "snapshot.json"
SNAPSHOT_VERSION = 1
# collect が広告の口座を読み直す最短の間隔（**THTH の線**。口座の timer は 10 分ごと・数は 15 分ごとに
# 更新【確】。1 つの広告の口座に口座が 2 つ結ばれていても、同じ刻みで 2 度読まない）。
MIN_INTERVAL_MINUTES = 10
# 鍵の知らせの線（C-2 の 5・`thth ads activate`（段 5）もこの線で断る）。
TOKEN_WARN_DAYS = 14.0
SPEND_CAP_NEAR_PCT = 80
BUDGET_LOW_PCT = 90
OVER_ESTIMATE_PCT = 120
ACTIVE = "ACTIVE"
ACCOUNT_ACTIVE = 1

OK = "ok"
NO_TOKEN = "no_token"
UNREADABLE = "unreadable"
EXPIRED = "expired"
ADS_TOKEN_EXPIRING = "ads_token_expiring"
ADS_NOT_GRANTED = "ads_not_granted"
ATTENTION = frozenset({NO_TOKEN, UNREADABLE, EXPIRED, ADS_TOKEN_EXPIRING, ADS_NOT_GRANTED})

KINDS = ("ads_stop_unconfirmed", "ads_active_outside_thth", "ads_no_end_time", "ads_account_status", "ads_spend_over_estimate",
         "ads_spend_cap_near", "ads_budget_low", "ads_spend_cap_unset", "ads_token_expiring",
         "ads_read_failed")


# --------------------------------------------------------------------------
# 鍵
# --------------------------------------------------------------------------

def token_state(cfg: dict, *, now) -> dict:
    """鍵の状態（API は叩かない）。`{"state", "remaining_days", "expires_at", "missing_scopes"}`。"""
    token = ledger.load_token(cfg)
    out = {"state": None, "remaining_days": None, "expires_at": None, "missing_scopes": None,
           "no_expiry": False, "basis": None, "data_access_expires_at": None}
    if token is None:
        out["state"] = NO_TOKEN
        return out
    if token.get("unreadable") or not isinstance(token.get("access_token"), str) or not token["access_token"]:
        out["state"] = UNREADABLE
        return out
    limits = ledger.token_limits(token)
    out["no_expiry"] = limits["no_expiry"]
    out["basis"] = limits["basis"]
    if limits["data_access_expires_at"] is not None:
        out["data_access_expires_at"] = jst.iso(
            datetime.datetime.fromtimestamp(limits["data_access_expires_at"], jst.JST))
    at = limits["effective_at"]
    if at is not None:
        # 効く期限＝鍵の期限とデータに触れる期限の早いほう（鍵が期限なしならデータの期限）。
        out["expires_at"] = jst.iso(datetime.datetime.fromtimestamp(at, jst.JST))
        out["remaining_days"] = round((at - now.timestamp()) / 86400.0, 2)
    scopes = token.get("scopes")
    if isinstance(scopes, list) and "ads_read" not in scopes:
        out["missing_scopes"] = ["ads_read"]
    remaining = out["remaining_days"]
    if remaining is not None and remaining <= 0:
        out["state"] = EXPIRED
    elif remaining is not None and remaining < TOKEN_WARN_DAYS:
        out["state"] = ADS_TOKEN_EXPIRING
    elif out["missing_scopes"]:
        out["state"] = ADS_NOT_GRANTED
    else:
        out["state"] = OK
    return out


def token_text(state: dict) -> str:
    """鍵の期限の 1 行（人の言葉）。期限なしは「期限なし」と言い、効く期限（データの期限）を添える。"""
    remaining = state.get("remaining_days")
    if state.get("no_expiry"):
        head = "鍵は期限なし（/debug_token の expires_at=0）"
        if remaining is None:
            return head + "・データに触れる期限は分かりません"
        return head + f"・データに触れる期限 {state['expires_at']}（残り {remaining:.1f} 日）"
    if remaining is None:
        return "期限が分かりません（thth ads doctor が /debug_token の今の値で書き直します）"
    what = "データに触れる期限" if state.get("basis") == "data_access" else "鍵の期限"
    return f"{what} {state['expires_at']}（残り {remaining:.1f} 日）"


# --------------------------------------------------------------------------
# 読む・残す
# --------------------------------------------------------------------------

def snapshot_path(name) -> str:
    return os.path.join(ledger.state_dir_for(name), SNAPSHOT_FILE)


def read_snapshot(name) -> dict | None:
    try:
        with open(snapshot_path(name), encoding="utf-8") as f:
            value = json.loads(f.read(4 * 1024 * 1024 + 1))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"unreadable": True}
    return value if isinstance(value, dict) else {"unreadable": True}


def _zone(cfg):
    name = cfg.get("timezone_name")
    if isinstance(name, str):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — 知らない名前は JST に倒す（日の区切りの見立てだけ）
            pass
    return jst.JST


def local_day(cfg, now) -> str:
    """広告アカウントの時間帯の日（B-9・D-3）。"""
    return now.astimezone(_zone(cfg)).date().isoformat()


def local_month(cfg, now) -> str:
    return now.astimezone(_zone(cfg)).strftime("%Y-%m")


def collect(name: str, *, now=None, reader=None, force=False) -> dict:
    """1 つの広告の口座を読み、`snapshot.json` に残す。読めなくても前の数は消さず、失敗を書き足す。"""
    now = now or jst.now_jst()
    cfg = ledger.load(name)
    previous = read_snapshot(name)
    previous = previous if isinstance(previous, dict) and not previous.get("unreadable") else {}
    if not force and _fresh(previous, now):
        return {"skipped": "fresh", "snapshot": previous}
    snapshot = {"version": SNAPSHOT_VERSION, "adaccount": name, "act_id": cfg["act_id"],
                "observed_at": jst.iso(now), "account": previous.get("account"),
                "tree": previous.get("tree"), "insights": previous.get("insights"),
                "error": None, "errors": [], "last_ok_at": previous.get("last_ok_at")}
    try:
        if reader is None:
            token = ledger.load_token(cfg)
            if not token or token.get("unreadable"):
                raise graphread.AdsReadError("ads_token_missing")
            reader = graphread.Reader(name, cfg, token)
        snapshot["account"] = reader.account()
        snapshot["tree"] = reader.tree()
        snapshot["last_ok_at"] = jst.iso(now)
    except (graphread.AdsReadError, meta_errors.RateLimited, ValueError) as exc:
        snapshot["error"] = getattr(exc, "code", None) or str(exc).split(":")[0][:80]
    if snapshot["error"] is None:
        day = local_day(cfg, now)
        insights = previous.get("insights") if isinstance(previous.get("insights"), dict) else {}
        if insights.get("day") != day:
            try:
                rows = {preset: reader.insights(preset) for preset in ("yesterday", "this_month")}
                insights = {"day": day, "month": local_month(cfg, now), "read_at": jst.iso(now),
                            **{preset: (value[0] if value else {"spend": "0", "spend_minor": 0})
                               for preset, value in rows.items()}}
            except (graphread.AdsReadError, meta_errors.RateLimited) as exc:
                snapshot["errors"].append(getattr(exc, "code", None) or "ads_insights_unavailable")
        snapshot["insights"] = insights or None
    ledger.ensure_state_dir(name)
    secrets_fs.atomic_write_json(snapshot_path(name), snapshot, mode=0o600)
    return {"skipped": None, "snapshot": snapshot}


def _fresh(previous, now) -> bool:
    at = jst.parse(previous.get("observed_at")) if isinstance(previous.get("observed_at"), str) else None
    if at is None or previous.get("error"):
        return False
    return (now - at).total_seconds() < MIN_INTERVAL_MINUTES * 60


def linked_to(account_name: str) -> list:
    """その THTH の口座を `pages`／`ig_accounts` に持つ広告の口座の名前。"""
    out = []
    for name in ledger.list_names():
        try:
            cfg = ledger.load(name)
        except ledger.AdAccountError:
            continue
        if account_name in ledger.linked(cfg):
            out.append(name)
    return out


def of_project(project: str) -> list:
    out = []
    for name in ledger.list_names():
        try:
            cfg = ledger.load(name)
        except ledger.AdAccountError:
            continue
        if cfg.get("project") == project:
            out.append(name)
    return out


def collect_linked(account_name: str, *, now=None, log=print) -> list:
    """`thth collect <口座>` の最後に呼ぶ。**失敗で採取を失敗にしない**（読めなければ snapshot に書く）。"""
    done = []
    for name in linked_to(account_name):
        try:
            result = collect(name, now=now)
        except Exception:  # noqa: BLE001 — 広告の読みで口座の採取を落とさない
            log(f"広告の口座 {name} を読めませんでした")
            continue
        if result["skipped"] is None:
            error = result["snapshot"].get("error")
            log(f"広告の口座 {name} を読みました" + (f"（読めなかった: {error}）" if error else ""))
        done.append(name)
    return done


# --------------------------------------------------------------------------
# 出来事
# --------------------------------------------------------------------------

def _thth_activated(name) -> set:
    """THTH が `activate` した物の id（段 5 の `ops/*.json` の `{"op": "activate", "ids": […]}`）。"""
    directory = os.path.join(ledger.state_dir_for(name), "ops")
    out = set()
    try:
        entries = os.listdir(directory)
    except OSError:
        return out
    for entry in entries:
        if not entry.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, entry), encoding="utf-8") as f:
                row = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and row.get("op") == "activate" and isinstance(row.get("ids"), list):
            out.update(i for i in row["ids"] if isinstance(i, str))
    return out


def _reserved_minor(name, month) -> int | None:
    """THTH の今月の予約の合計（段 5 の `budget-<月>.json`）。無ければ None。"""
    try:
        with open(os.path.join(ledger.state_dir_for(name), f"budget-{month}.json"), encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return None
    value = row.get("reserved_minor") if isinstance(row, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _ref(act_id, ids) -> str:
    return f"act_{act_id}:" + hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()[:12]


def active_ids(tree) -> list:
    out = []
    for key in ("campaigns", "adsets", "ads"):
        for row in (tree or {}).get(key) or []:
            if row.get("effective_status") == ACTIVE and isinstance(row.get("id"), str):
                out.append(row["id"])
    return out


def events(name: str, cfg: dict, snapshot, *, now, make) -> list:
    """出来事の行（`make(kind, ref, summary, **extra)` で組む）。**読むだけ。**"""
    out = []
    act = cfg["act_id"]
    label = f"広告の口座 {name}（act_{act}）"
    from . import ops
    unconfirmed = ops.stop_unconfirmed(name)
    if unconfirmed:
        out.append(make("ads_stop_unconfirmed", f"act_{act}:stop:{unconfirmed.get('op_id')}",
                        f"{label} を THTH から止めたと確かめられませんでした（{unconfirmed.get('at')}）。"
                        "管理画面で止めてください。" + str(unconfirmed.get("hub_line") or ""),
                        op_id=unconfirmed.get("op_id")))
    token = token_state(cfg, now=now)
    if token["state"] in (ADS_TOKEN_EXPIRING, EXPIRED):
        expired = token["state"] == EXPIRED
        out.append(make("ads_token_expiring", f"act_{act}:{token['expires_at']}",
                        f"{label} の鍵が{'切れました' if expired else '14 日を切りました'}（{token['expires_at']}・"
                        f"残り {token['remaining_days']:.1f} 日）。thth ads auth {name} --by <名前> で取り直す",
                        state=token["state"], remaining_days=token["remaining_days"]))
    if not isinstance(snapshot, dict) or snapshot.get("unreadable"):
        if snapshot is not None:
            out.append(make("ads_read_failed", f"act_{act}:snapshot_unreadable",
                            f"{label} の読みの記録を読めません", error="snapshot_unreadable"))
        return out
    if snapshot.get("error"):
        out.append(make("ads_read_failed", f"act_{act}:{snapshot['error']}",
                        f"{label} を読めませんでした（{snapshot['error']}・最後に読めたのは "
                        f"{snapshot.get('last_ok_at') or 'まだ無い'}）。読めない間は外で始まった配信に気づけません",
                        error=snapshot["error"], last_ok_at=snapshot.get("last_ok_at")))
    tree = snapshot.get("tree") if isinstance(snapshot.get("tree"), dict) else None
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else None
    if tree is not None:
        active = active_ids(tree)
        outside = sorted(set(active) - _thth_activated(name))
        if outside:
            out.append(make("ads_active_outside_thth", _ref(act, outside),
                            f"{label} で THTH の外で始まった配信が {len(outside)} 件動いています（Ads Manager・"
                            "公式 MCP・ほかの人）。管理画面で確かめる", ids=outside[:20], n=len(outside)))
        campaigns = {row.get("id"): row for row in tree.get("campaigns") or []}
        endless = sorted(row["id"] for row in tree.get("adsets") or []
                         if row.get("effective_status") == ACTIVE and isinstance(row.get("id"), str)
                         and not row.get("end_time")
                         and not (campaigns.get(row.get("campaign_id")) or {}).get("stop_time"))
        if endless:
            out.append(make("ads_no_end_time", _ref(act, endless),
                            f"{label} で終了日の無い広告セットが {len(endless)} 件動いています（止める道が"
                            "全部だめでも止まる柵が無い）", ids=endless[:20], n=len(endless)))
    if account is not None:
        status = account.get("account_status")
        if status is not None and status != ACCOUNT_ACTIVE:
            out.append(make("ads_account_status", f"act_{act}:status:{status}",
                            f"{label} の account_status が {status}（1 以外）", account_status=status))
        cap, spent = account.get("spend_cap"), account.get("amount_spent")
        if cap == 0:
            out.append(make("ads_spend_cap_unset", f"act_{act}:spend_cap:0",
                            f"{label} に spend_cap が入っていません（Meta では上限なし）。管理画面で入れる"
                            "（THTH からは入れない）"))
        elif isinstance(cap, int) and isinstance(spent, int) and cap > 0 and spent * 100 >= cap * SPEND_CAP_NEAR_PCT:
            out.append(make("ads_spend_cap_near", f"act_{act}:cap:{cap}",
                            f"{label} の支出が spend_cap の {spent * 100 // cap}%（累計 {spent} / {cap}・最小単位）",
                            amount_spent=spent, spend_cap=cap))
    insights = snapshot.get("insights") if isinstance(snapshot.get("insights"), dict) else None
    month_minor = ((insights or {}).get("this_month") or {}).get("spend_minor")
    if insights and insights.get("month") == local_month(cfg, now) and isinstance(month_minor, int):
        monthly = ledger.ceilings(cfg).get("monthly") or 0
        if monthly > 0 and month_minor * 100 >= monthly * BUDGET_LOW_PCT:
            out.append(make("ads_budget_low", f"act_{act}:{insights['month']}:{monthly}",
                            f"{label} の今月の支出が THTH の月の上限の {month_minor * 100 // monthly}%"
                            f"（{month_minor} / {monthly}・最小単位・予約の外の支出も数える）",
                            spend_minor=month_minor, monthly=monthly))
        reserved = _reserved_minor(name, insights["month"])
        if reserved and month_minor * 100 > reserved * OVER_ESTIMATE_PCT:
            out.append(make("ads_spend_over_estimate", f"act_{act}:{insights['month']}:{reserved}",
                            f"{label} の今月の支出が THTH の予約の {month_minor * 100 // reserved}%"
                            f"（{month_minor} / {reserved}・最小単位）", spend_minor=month_minor, reserved=reserved))
    return out


def status(name: str, *, now) -> dict:
    """`thth morning`・`thth ads show --offline` の 1 行（API は叩かない）。"""
    cfg = ledger.load(name)
    snapshot = read_snapshot(name)
    rows = events(name, cfg, snapshot, now=now,
                  make=lambda kind, ref, summary, **extra: {"kind": kind, "summary": summary})
    account = (snapshot or {}).get("account") if isinstance(snapshot, dict) else None
    tree = (snapshot or {}).get("tree") if isinstance(snapshot, dict) else None
    return {"adaccount": name, "act_id": cfg["act_id"], "project": cfg.get("project"),
            "token": token_state(cfg, now=now),
            "observed_at": (snapshot or {}).get("observed_at") if isinstance(snapshot, dict) else None,
            "account": account, "active": len(active_ids(tree)) if isinstance(tree, dict) else None,
            "stopped": ledger.stopped(name), "writes_refused": ledger.write_refusal(cfg),
            "events": rows}
