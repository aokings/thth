"""動きの一覧 `https://thth.me/activity` の VM 側（設計 3.12.0 §3.4・§3.5・段 3）。

向きは承認の relay と同じ: **VM が Worker へ署名つきで押し上げる**（`/approval/activity/<person>/sync`）。

- 押し上げるもの: 持ち主（person）ごとに、その人の口座の要約——approval と安全装置の数値・
  止まっているか（理由）・今日の公開数と削除数・MCP の鍵の id（hash の先頭 12 字）と期限・
  出たもの／消したもの／予約／猶予中を新しい順に 30 行まで。**本文は先頭 60 字まで**。秘密は無い。
  Worker は 1 時間で忘れる（VM が 10 秒ごとに押し直す）。
- 受け取るもの: 持ち主が /activity で secret を入れ直して頼んだ操作（止める・戻す・予約の取り消し・
  設定を変える・鍵の取り消し・鍵の発行し直し）。VM がここで**もう一度**持ち主の口座かを確かめてから
  行い、結果を次の sync で返す。どの操作も何度行っても同じ結果になる（sync が落ちても二重にならない）。

持ち主（person）の決め方:
- 招待で用意した口座は、その口座名（承認ページの人は口座名で登録される）。
- それ以外は、その口座を含む有効な書き込みの資格情報の `actor`（1 口座だけの鍵は、取り消し・
  期限切れのあとも持ち主のまま——そこから発行し直せるように）。

鍵の発行し直し（rotate）は、Worker が bearer を作って持ち主の画面に 1 度だけ出し、**hash だけ**を
ここへ渡す。VM も hash だけを資格情報のファイルに書く（期限は 365 日・変更ログ `credential_rotated`）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from . import accounts, admin_log, approval_relay as relay, guard, jst, sent as sent_mod

HEAD = 60
ROWS_MAX = 30
ACCOUNTS_MAX = 8
ACTIONS_MAX = 16
SYNC_SECONDS = 10
BACKOFF_SECONDS = 300
CREDENTIAL_DAYS = 365
KINDS = ("stop", "resume", "cancel", "settings", "revoke", "rotate")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ACTION_REASONS = frozenset((
    "not_owner", "invalid_action", "credential_not_single_account", "credential_conflict",
    "credentials_full", "credentials_unavailable", "action_failed",
    "invalid_setting", "schedule_unavailable", "draft_not_editable", "scope_unavailable",
    "draft_not_verified", "schedule_commit_unconfirmed", "ledger_unavailable",
))
_next_sync = {}


# --------------------------------------------------------------------------
# 持ち主
# --------------------------------------------------------------------------

def owners(credentials_path) -> dict:
    """{person: {account: project}}。"""
    from .report_http import load_credentials
    _, credentials = load_credentials(Path(credentials_path))
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {}
    for _digest, expiry, revoked, context in credentials:
        if (context.scope != "user" or not context.writes or not isinstance(context.actor, str)
                or not relay.PERSON.fullmatch(context.actor)):
            continue
        # 取り消した・期限の切れた鍵でも、1 口座だけの鍵なら持ち主のまま（/activity で発行し直せるように）。
        # 複数の口座にまたがる鍵は、生きているものだけで数える。
        if (revoked or now >= expiry) and len(context.allowed_accounts) != 1:
            continue
        for account, project in context.allowed_accounts.items():
            out.setdefault(context.actor, {})[account] = project
    for name in accounts.list_account_names():
        try:
            cfg = accounts.load_account(name)
        except (accounts.AccountError, OSError, ValueError):
            continue
        if accounts.is_invite_account(cfg) and relay.PERSON.fullmatch(name):
            out.setdefault(name, {})[name] = cfg.get("project")
    return out


def owns(credentials_path, person, account) -> bool:
    return account in owners(credentials_path).get(person, {})


# --------------------------------------------------------------------------
# 要約（Worker へ押し上げる形）
# --------------------------------------------------------------------------

def _head(text) -> str:
    """先頭 60 字（code point）。改行は空白に、制御文字は落とす（Worker は制御文字を含む要約を受けない）。"""
    value = " ".join(str(text or "").split())
    value = "".join(ch for ch in value if not (ord(ch) < 32 or ord(ch) == 127))
    return value[:HEAD]


def _credential_rows(config, account, project, person):
    return [row for row in config.get("credentials", [])
            if isinstance(row, dict) and row.get("accounts") == {account: project}
            and row.get("actor") == person and row.get("scope", "user") == "user"]


def _credential_summary(credentials_path, account, project, person):
    try:
        from .invites import _read_credentials
        config = json.loads(_read_credentials(Path(credentials_path).absolute()))
    except (OSError, ValueError):
        return None
    rows = _credential_rows(config, account, project, person)
    live = [row for row in rows if row.get("revoked") is False] or rows
    if not live:
        return None
    row = live[0]
    return {"id": str(row.get("sha256", ""))[:guard.CREDENTIAL_ID_LENGTH],
            "expires_at": str(row.get("expires_at", ""))[:40], "revoked": row.get("revoked") is not False}


def summary(person, account, credentials_path, *, now=None) -> dict:
    from . import account_settings, server_writes, queuefile
    cfg = accounts.load_account(account)
    row = account_settings.status(account, now=now)
    rows = []
    for record in sent_mod.records(accounts.state_dir_for(account)):
        head = _head(record.get("text"))
        post_id = str(record.get("post_id"))[:300]
        if jst.parse(record.get("sent_at")) is not None:
            rows.append({"kind": "published", "at": record["sent_at"], "head": head, "post_id": post_id,
                         "draft_id": None, "reason": None, "reply": bool(record.get("reply_to"))})
        if jst.parse(record.get("retracted_at")) is not None:
            rows.append({"kind": "retracted", "at": record["retracted_at"], "head": head, "post_id": post_id,
                         "draft_id": None, "reason": None, "reply": bool(record.get("reply_to"))})
    try:
        for name, _raw, q, _verified in server_writes._rows(cfg, account):
            fm = q.front_matter
            if fm.get("status") != "approved" or fm.get("post_id"):
                continue
            at = fm.get("publish_at")
            if jst.parse(at) is None:
                continue
            rows.append({"kind": "held" if fm.get("held_minutes") else "scheduled", "at": at,
                         "head": _head(queuefile.extract_section(q.body, cfg["media"])), "post_id": None,
                         "draft_id": server_writes._id(name), "reason": None, "reply": bool(fm.get("reply_to"))})
    except Exception:
        # queue が読めなくても、出たもの・消したもの・止まった理由は見せる。
        pass
    stopped = row["stopped"]
    if stopped is not None:
        rows.append({"kind": "stopped", "at": stopped.get("stopped_at") or jst.iso(), "head": "", "post_id": None,
                     "draft_id": None, "reason": stopped.get("reason"), "reply": False})
    rows.sort(key=lambda item: jst.parse(item["at"]) or datetime.datetime.min.replace(tzinfo=jst.JST), reverse=True)
    for item in rows:
        item["at"] = jst.iso(jst.parse(item["at"]))
    s = row["settings"]
    return {
        "account": account,
        "generated_at": jst.iso(),
        "approval": s["approval"],
        "scheduled": row["scheduled"],
        "limits": {key: s[key] for key in ("daily_max_posts", "daily_max_retracts", "burst_count",
                                           "burst_minutes", "hold_minutes", "min_interval_hours")},
        "stopped": None if stopped is None else {"reason": stopped.get("reason"),
                                                 "at": jst.iso(jst.parse(stopped.get("stopped_at")))
                                                 if jst.parse(stopped.get("stopped_at")) else None},
        "today": {"posts": row["today"]["posts"], "retracts": row["today"]["retracts"]},
        "credential": _credential_summary(credentials_path, account, cfg.get("project"), person),
        "rows": rows[:ROWS_MAX],
    }


# --------------------------------------------------------------------------
# 鍵（MCP の資格）
# --------------------------------------------------------------------------

def _rewrite_credentials(credentials_path, change, event, account, person, diff):
    """資格情報のファイルを排他の中で読み、`change(config)` が True を返したら書き換えて記録する。"""
    from . import report_http
    from .invites import _locked_credentials, _read_credentials, _replace_credentials
    path = Path(credentials_path).absolute()
    with _locked_credentials(path):
        before = _read_credentials(path)
        report_http.load_credentials(path)
        config = json.loads(before)
        if not change(config):
            return False
        data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()

        def rollback():
            _replace_credentials(path, before)

        with admin_log.transaction(rollback=rollback):
            _replace_credentials(path, data)
            report_http.load_credentials(path)
            admin_log.append(event, account, accounts.load_account(account), by=person, via="http", diff=diff)
    return True


def _single(config, account, project, person):
    rows = _credential_rows(config, account, project, person)
    others = [row for row in config.get("credentials", []) if isinstance(row, dict) and row not in rows
              and row.get("actor") == person and account in (row.get("accounts") or {})]
    if others and not rows:
        # その人の鍵は他の口座と共有（1 口座だけの鍵が無い）。ここから触ると他の口座も巻き込む。
        raise ValueError("credential_not_single_account")
    return rows


def rotate(credentials_path, account, person, sha256):
    """その口座だけの鍵を新しい hash に差し替える（無ければ 1 件足す）。前の鍵はその場で使えなくなる。"""
    if not isinstance(sha256, str) or not HEX64.fullmatch(sha256):
        raise ValueError("invalid_action")
    project = accounts.load_account(account).get("project")
    old = []

    def change(config):
        rows = _single(config, account, project, person)
        if any(row.get("sha256") == sha256 and row.get("revoked") is False for row in rows):
            return False  # 既に差し替え済み（前の sync の完了を返しそこねた）
        if any(isinstance(row, dict) and row.get("sha256") == sha256 for row in config["credentials"]):
            raise ValueError("credential_conflict")
        expires = jst.iso(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=CREDENTIAL_DAYS))
        if rows:
            old.extend(row["sha256"] for row in rows)
            rows[0].update(sha256=sha256, revoked=False, expires_at=expires, writes=True)
            for row in rows[1:]:
                row["revoked"] = True
        else:
            if len(config["credentials"]) >= 100:
                raise ValueError("credentials_full")
            config["credentials"].append({"sha256": sha256, "expires_at": expires, "revoked": False,
                                          "accounts": {account: project}, "scope": "user", "writes": True,
                                          "actor": person})
        return True

    changed = _rewrite_credentials(credentials_path, change, "credential_rotated", account, person,
                                   {"credential": ["present", "present"], "generation": ["previous", "new"]})
    if changed and old:
        _follow_invite(account, old, sha256)
    return changed


def _follow_invite(account, old, sha256):
    """招待の記録が前の鍵の hash を持っていれば新しい hash に（退出の後始末と運営者の口が同じ鍵を見る）。"""
    from . import invites
    try:
        records, _ = invites.STORE.load()
        for record in records:
            if record.get("account") == account and record.get("credential_sha256") in old:
                with invites.STORE.locked() as directory:
                    latest = invites.STORE.find(directory, record["invite_id"])
                    invites._save(directory, latest, credential_sha256=sha256)
    except Exception:
        print("activity_invite_record_unsynced: " + account, file=sys.stderr)


def revoke(credentials_path, account, person):
    """その口座だけの鍵を取り消す（行は残して revoked: true）。"""
    project = accounts.load_account(account).get("project")

    def change(config):
        rows = [row for row in _single(config, account, project, person) if row.get("revoked") is False]
        for row in rows:
            row["revoked"] = True
        return bool(rows)

    return _rewrite_credentials(credentials_path, change, "credential_revoked", account, person,
                                {"credential": ["present", "revoked"]})


# --------------------------------------------------------------------------
# 持ち主の操作
# --------------------------------------------------------------------------

def _valid_action(action):
    if type(action) is not dict or not isinstance(action.get("id"), str) or not relay.OPAQUE.fullmatch(action["id"]):
        return False
    kind = action.get("kind")
    if kind not in KINDS or not accounts.name_is_safe(action.get("account")):
        return False
    extra = set(action) - {"id", "kind", "account"}
    if kind == "cancel":
        return extra == {"draft_id"} and isinstance(action["draft_id"], str) and HEX64.fullmatch(action["draft_id"])
    if kind == "settings":
        return extra == {"key", "value"} and isinstance(action["key"], str) and isinstance(action["value"], str)
    if kind == "rotate":
        return extra == {"sha256"} and isinstance(action["sha256"], str) and HEX64.fullmatch(action["sha256"])
    return not extra


def apply(person, action, credentials_path) -> dict:
    """1 件の操作を行い `{id, outcome, reason}` を返す（例外は外へ出さない）。"""
    action_id = action.get("id") if type(action) is dict and isinstance(action.get("id"), str) \
        and relay.OPAQUE.fullmatch(action.get("id")) else None
    if action_id is None:
        return None
    if not _valid_action(action):
        return {"id": action_id, "outcome": "failed", "reason": "invalid_action"}
    account, kind = action["account"], action["kind"]
    try:
        if not owns(credentials_path, person, account):
            return {"id": action_id, "outcome": "failed", "reason": "not_owner"}
        if kind == "stop":
            if guard.stopped(account) is None:
                guard.stop(account, "owner", actor=person, via="http")
        elif kind == "resume":
            guard.resume(account, by=person, via="http")
        elif kind == "cancel":
            from . import server_writes
            if server_writes.cancel_schedule(account, action["draft_id"], by=person, via="http") == "cancelled":
                admin_log.append("schedule_cancelled", account, accounts.load_account(account), by=person,
                                 via="http", diff={"status": ["approved", "draft"]})
        elif kind == "settings":
            from . import account_settings
            account_settings.change(account, action["key"], action["value"], by=person, via="http")
        elif kind == "revoke":
            revoke(credentials_path, account, person)
        else:
            rotate(credentials_path, account, person, action["sha256"])
        return {"id": action_id, "outcome": "done", "reason": None}
    except Exception as exc:
        reason = str(exc) if str(exc) in ACTION_REASONS else "action_failed"
        return {"id": action_id, "outcome": "failed", "reason": reason}


# --------------------------------------------------------------------------
# 常駐の 1 巡
# --------------------------------------------------------------------------

def _summaries(person, names, credentials_path):
    out = []
    for name in names:
        try:
            out.append(summary(person, name, credentials_path))
        except Exception:
            continue
    return out


def sync_person(person, names, credentials_path):
    # Worker の口座名の形（PERSON）に合わない名前は押し上げない（1 つで sync 全体が断られないように）。
    names = sorted(name for name in names if relay.PERSON.fullmatch(name))[:ACCOUNTS_MAX]
    value = relay.signed_request("activity", person, "sync",
                                 {"accounts": _summaries(person, names, credentials_path), "completed": []})
    actions = value.get("actions") if isinstance(value, dict) else None
    if not isinstance(actions, list):
        raise relay.RelayError("approval_relay_invalid")
    completed = [row for row in (apply(person, action, credentials_path) for action in actions[:ACTIONS_MAX]) if row]
    if completed:
        relay.signed_request("activity", person, "sync",
                             {"accounts": _summaries(person, names, credentials_path), "completed": completed})
    return completed


def run_once(credentials_path):
    """`thth approval-worker` の 1 巡から呼ぶ。人ごとに 10 秒に 1 回（Worker に人が無ければ 5 分待つ）。"""
    if time.monotonic() < _next_sync.get("", 0):
        return
    _next_sync[""] = time.monotonic() + SYNC_SECONDS
    try:
        people = owners(credentials_path)
    except Exception:
        print("activity_owners_unavailable", file=sys.stderr)
        return
    for person in sorted(people):
        if time.monotonic() < _next_sync.get(person, 0):
            continue
        try:
            sync_person(person, people[person], credentials_path)
            _next_sync[person] = time.monotonic() + SYNC_SECONDS
        except relay.RelayError as exc:
            _next_sync[person] = time.monotonic() + (BACKOFF_SECONDS if exc.status in (400, 404, 409, 410)
                                                     else SYNC_SECONDS * 3)
        except Exception:
            print("activity_sync_failed", file=sys.stderr)
            _next_sync[person] = time.monotonic() + SYNC_SECONDS * 3
