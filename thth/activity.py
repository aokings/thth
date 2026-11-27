"""動きの一覧 `https://thth.me/activity` の VM 側（設計 3.12.0 §3.4・§3.5・段 3）。

向きは招待・退出の relay と同じ: **VM が Worker へ署名つきで押し上げる**（`/relay/v/activity/<person>/sync`）。

- 押し上げるもの: 持ち主（person）ごとに、その人の口座の要約——安全装置の数値・
  止まっているか（理由）・今日の公開数と削除数・MCP の鍵の id（hash の先頭 12 字）と期限・
  出たもの／消したもの／予約／猶予中を新しい順に 30 行まで。**本文は先頭 60 字まで**。秘密は無い。
  Worker は 1 時間で忘れる（VM が 10 秒ごとに押し直す）。
- 受け取るもの: 持ち主が /activity で secret を入れ直して頼んだ操作（止める・戻す・予約の取り消し・
  設定を変える・鍵の取り消し・鍵の発行し直し）。VM がここで**もう一度**持ち主の口座かを確かめてから
  行い、結果を次の sync で返す。どの操作も何度行っても同じ結果になる（sync が落ちても二重にならない）。

持ち主（person）の決め方:
- 招待で用意した口座は、その口座名（口座の secret の持ち主は口座名で登録される）。
- それ以外は、その口座を含む有効な書き込みの資格情報の `actor`（1 口座だけの鍵は、取り消し・
  期限切れのあとも持ち主のまま——そこから発行し直せるように）。

鍵の発行し直し（rotate）は、Worker が bearer を作って持ち主の画面に 1 度だけ出し、**hash だけ**を
ここへ渡す。VM も hash だけを資格情報のファイルに書く（期限は 365 日・変更ログ `credential_rotated`）。

3.14.0（遠くの道・§3.1）: 同じ sync に、その人の**有効な鍵の表**（hash・口座・期限）と /api/v1 の依頼の
結果を載せ、応答で待っている依頼を受け取る（`api_requests`）。依頼があれば次の sync を 2 秒後にする。

括りの段 2（レーンの鍵）: 同じ sync に、**括りの鍵の表**（hash・括り・期限）と括りの束の結果を足す
（`workspace_keys`・`workspace_results`）。レーンの鍵は口座ごとの表に載せない（口座の数×レーンの数で 64 行を越えるため）。
`thth login --lane` の頁で持ち主が secret を入れると、Worker が操作 `lane_key`（hash だけ）を置き、ここで対応表の
`owner_of(括り) == 持ち主` を確かめてから括りの表（`thth/lane_keys.py`・資格のファイルの外）にレーンの鍵（actor＝持ち主・workspace・lane・365 日）を作る。
同じ持ち主・括り・レーンの前の鍵は取り消す。版ずれ: 括りの形を断る Worker（3.14.0）には 3.14.0 の形、
それも断る Worker（3.13.0）には 3.13.0 の形で話す（どちらもしばらく覚える）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from . import accounts, admin_log, relay, guard, jst, sent as sent_mod

HEAD = 60
ROWS_MAX = 30
ACCOUNTS_MAX = 8
ACTIONS_MAX = 16
SYNC_SECONDS = 10
BUSY_SECONDS = 2   # /api/v1 の依頼が待っているあいだの刻み（設計 3.14.0 §3.1）
BACKOFF_SECONDS = 300
CREDENTIAL_DAYS = 365
KINDS = ("stop", "resume", "cancel", "settings", "revoke", "rotate", "lane_key")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,80}\Z")
# sync の形（版ずれ）。FULL は括りの鍵の表も載せる・KEYS は 3.14.0（口座の鍵の表まで）・PLAIN は 3.13.0。
FULL, KEYS, PLAIN = 3, 2, 1
ACTION_REASONS = frozenset((
    "not_owner", "invalid_action", "credential_not_single_account", "credential_conflict",
    "credentials_full", "credentials_unavailable", "action_failed",
    "invalid_setting", "schedule_unavailable", "draft_not_editable", "scope_unavailable",
    "draft_not_verified", "schedule_commit_unconfirmed", "ledger_unavailable",
    # レーンの鍵（括りの段 2）
    "workspace_not_owned", "workspace_required", "workspace_has_no_accounts", "workspace_unavailable",
    "workspaces_map_invalid",
))
_next_sync = {}
_legacy = {}   # 新しい sync の形を断った Worker と話している人 → (古い形で話す期限, 話す形)


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
        if context.lane is not None:
            # レーンの鍵は括りの口座を全部持つが、口座の持ち主を決める鍵ではない（/activity の要約と操作の権限を広げない）。
            continue
        for account, project in context.allowed_accounts.items():
            out.setdefault(context.actor, {})[account] = project
    # 招待で入った口座は、どの括りにあっても持ち主（常駐は括りをまたいで回す・括りの段 2）。
    for workspace in accounts.list_workspace_dirs():
        try:
            scope = accounts.use_workspace(workspace)
        except accounts.AccountError:
            continue
        with scope:
            _invite_owners(out)
    return out


def _invite_owners(out):
    for name in accounts.list_account_names():
        try:
            cfg = accounts.load_account(name)
        except (accounts.AccountError, OSError, ValueError):
            continue
        if accounts.is_invite_account(cfg) and relay.PERSON.fullmatch(name):
            out.setdefault(name, {})[name] = cfg.get("project")
    # 広告の口座は `accounts/` に台帳が無いので、持ち主は広告の台帳（`adaccounts/`）から引く。
    from .ads import ledger as ads_ledger
    for name in ads_ledger.list_names():
        try:
            cfg = ads_ledger.load(name)
        except (accounts.AccountError, OSError, ValueError):
            continue
        if accounts.is_invite_account(cfg) and relay.PERSON.fullmatch(name):
            out.setdefault(name, {})[name] = cfg.get("project")


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


def _is_adaccount(account) -> bool:
    """広告の口座か（台帳が `adaccounts/` にだけある・`accounts.ledger_kind`）。両方にあれば断る。"""
    return accounts.ledger_kind(account) == accounts.ADACCOUNT_KIND


def _ads_summary(person, account, credentials_path) -> dict:
    """広告の口座の要約（公開の数も上限も持たないので 0。止まっているかと鍵だけを見せる）。"""
    from .ads import ledger as ads_ledger
    cfg = ads_ledger.load(account)
    stopped = ads_ledger.stopped(account)
    at = jst.parse((stopped or {}).get("stopped_at"))
    return {"account": account, "generated_at": jst.iso(), "scheduled": False,
            "limits": {key: 0 for key in ("daily_max_posts", "daily_max_retracts", "burst_count",
                                          "burst_minutes", "hold_minutes", "min_interval_hours")},
            "stopped": None if stopped is None else {"reason": stopped.get("reason"), "at": jst.iso(at) if at else None},
            "today": {"posts": 0, "retracts": 0},
            "credential": _credential_summary(credentials_path, account, cfg.get("project"), person), "rows": []}


def summary(person, account, credentials_path, *, now=None) -> dict:
    from . import account_settings, burst_queue, server_writes, queuefile
    if _is_adaccount(account):
        return _ads_summary(person, account, credentials_path)
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
    # THTH を通していない自分の投稿を消した記録（`retracted_outside/`・設計 持ち主の依頼で消す §4）も
    # 「消した」の行に並べる。行の形は変えない（Worker の側の検査を変えずに済ませる）。
    for record in sent_mod.outside_retractions(accounts.state_dir_for(account)):
        if jst.parse(record.get("retracted_at")) is not None:
            rows.append({"kind": "retracted", "at": record["retracted_at"], "head": _head(record.get("text")),
                         "post_id": str(record.get("post_id"))[:300], "draft_id": None, "reason": None,
                         "reply": False})
    try:
        for name, _raw, q, _verified in server_writes._rows(cfg, account):
            fm = q.front_matter
            if fm.get("status") != "approved" or fm.get("post_id"):
                continue
            at = fm.get("publish_at")
            if jst.parse(at) is None:
                continue
            # 猶予中（held_minutes）・burst の順番待ちの返信（queued_for: burst・3.25.0）・予約。
            kind = "held" if fm.get("held_minutes") else "queued" if burst_queue.is_queued(fm) else "scheduled"
            rows.append({"kind": kind, "at": at,
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
            admin_log.append(event, account, _ledger(account), by=person, via="http", diff=diff)
    return True


def _ledger(account) -> dict:
    """鍵の操作が見る台帳（設計 広告の資格と遠くの道 案 B: 名前の置き場で種類を決める）。

    広告の口座は `adaccounts/<名前>.json`（変更ログの媒体は `ads`）。どちらにも無ければ口座の台帳の断り。
    """
    if _is_adaccount(account):
        from .ads import ledger as ads_ledger
        return {**ads_ledger.load(account), "media": ads_ledger.MEDIA}
    return accounts.load_account(account)


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
    project = _ledger(account).get("project")
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
    project = _ledger(account).get("project")

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


def _valid_lane_action(action) -> bool:
    """レーンの鍵の発行（口座を持たない・括りは null か括りの名・レーンの名・hash）。"""
    return (set(action) == {"id", "kind", "workspace", "lane", "sha256"}
            and (action["workspace"] is None or isinstance(action["workspace"], str)
                 and accounts.WS_NAME.fullmatch(action["workspace"]) is not None)
            and isinstance(action["lane"], str) and LANE.fullmatch(action["lane"]) is not None
            and isinstance(action["sha256"], str) and HEX64.fullmatch(action["sha256"]) is not None)


def apply(person, action, credentials_path) -> dict:
    """1 件の操作を行い `{id, outcome, reason}` を返す（例外は外へ出さない）。

    口座の台帳のある括りに入って行う（括りの段 2）。レーンの鍵の発行は口座を持たないので別の口。
    """
    if type(action) is dict and action.get("kind") == "lane_key":
        return _apply_lane_key(person, action, credentials_path)
    account = action.get("account") if type(action) is dict else None
    try:
        scope = accounts.account_workspace(account) if isinstance(account, str) else accounts.use_workspace(accounts.MAIN_WS)
    except accounts.AccountError:
        scope = accounts.use_workspace(accounts.MAIN_WS)
    with scope:
        return _apply(person, action, credentials_path)


def _apply(person, action, credentials_path) -> dict:
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
        if kind == "stop" and _is_adaccount(account):
            from .ads import ledger as ads_ledger
            if ads_ledger.stopped(account) is None:
                ads_ledger.stop(account, "owner", by=person, via="http")
        elif kind == "stop":
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
# レーンの鍵（括りの段 2・`thth login --lane`）
# --------------------------------------------------------------------------

def _workspaces_map():
    from . import workspaces_map, ws_admin
    try:
        return workspaces_map.load(ws_admin.map_path())
    except workspaces_map.WorkspacesMapError:
        raise ValueError("workspaces_map_invalid") from None


def owned_workspaces(person) -> list:
    """対応表で、その人が持ち主の括り（権威は VM の対応表 1 枚）。"""
    table = _workspaces_map()
    return sorted(ws for ws in table.workspaces if table.owner_of(ws) == person)


def choose_workspace(person, workspace):
    """発行する括りを決める。名指しなら持ち主か、無ければ持ち主の括りが 1 つならそれ。

    ほかの人の括りと、どこにも無い括りは同じ `workspace_not_owned`（括りの名を当てて回る探りに答えない）。
    """
    owned = owned_workspaces(person)
    if workspace is not None:
        if workspace not in owned:
            raise ValueError("workspace_not_owned")
        return workspace
    if not owned:
        raise ValueError("workspace_not_owned")
    if len(owned) > 1:
        raise ValueError("workspace_required")
    return owned[0]


def issue_lane_key(credentials_path, person, workspace, lane, sha256) -> tuple:
    """レーンの鍵の行を作る。戻り値は (括り, 頼みの宛先の口座)。断りは ValueError(符丁)。

    置き場は括りの表（`thth/lane_keys.py`・`ws/<括り>/private/lane-keys.json`）で、**資格のファイルは触らない**
    （設計 §8 R1: main の版がそのまま読める形のままにする）。行は actor＝持ち主・workspace・lane・書いてよい・365 日。
    口座は行に持たず、照合のたびに括りの口座を引く（あとで足した口座も同じ鍵で動く）。同じ持ち主・括り・レーンの
    前の鍵は取り消す。同じ hash の行がもうあれば何もしない（sync が落ちて同じ操作が 2 度来ても 1 本）。
    """
    from . import lane_keys
    from .report_http import load_credentials
    if not isinstance(sha256, str) or not HEX64.fullmatch(sha256) or not isinstance(lane, str) \
            or not LANE.fullmatch(lane):
        raise ValueError("invalid_action")
    chosen = choose_workspace(person, workspace)
    try:
        names = lane_keys.workspace_accounts(chosen)
    except lane_keys.LaneKeysError as exc:
        raise ValueError(str(exc)) from None
    if not names:
        raise ValueError("workspace_has_no_accounts")
    home = sorted(names)[0]
    # ほかの鍵（資格のファイル・ほかの括りの表）と同じ hash は使わない。
    for digest, _expiry, _revoked, context in load_credentials(Path(credentials_path))[1]:
        if digest == sha256 and (context.lane != lane or context.workspace != chosen or context.actor != person):
            raise ValueError("credential_conflict")

    def fn(rows):
        for row in rows:
            if row["sha256"] == sha256:
                if (row["actor"], row["lane"], row["revoked"]) == (person, lane, False):
                    return False  # 前の sync で作った（完了を返しそこねた）
                raise ValueError("credential_conflict")
        # 前に取り消した同じレーンの行は落とし、生きている行は取り消す（表がレーンの数より大きくならない）。
        rows[:] = [row for row in rows if not (row["actor"] == person and row["lane"] == lane and row["revoked"])]
        for row in rows:
            if row["actor"] == person and row["lane"] == lane:
                row["revoked"] = True
        expires = jst.iso(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=CREDENTIAL_DAYS))
        rows.append({"sha256": sha256, "expires_at": expires, "revoked": False, "actor": person,
                     "workspace": chosen, "lane": lane, "writes": True})
        return True

    try:
        changed = lane_keys.change(chosen, fn)
    except lane_keys.LaneKeysError as exc:
        raise ValueError(str(exc) if str(exc) in ACTION_REASONS else "credentials_unavailable") from None
    if changed:
        with accounts.use_workspace(chosen):
            try:
                admin_log.append("credential_rotated", home, _ledger(home), by=person, via="http",
                                 diff={"credential": ["present", "present"], "lane": [None, lane]})
            except Exception:
                print("activity_lane_key_unlogged: " + chosen, file=sys.stderr)
    return chosen, home


def _apply_lane_key(person, action, credentials_path):
    action_id = action.get("id") if isinstance(action.get("id"), str) and relay.OPAQUE.fullmatch(action["id"]) else None
    if action_id is None:
        return None
    failed = {"id": action_id, "outcome": "failed", "reason": "invalid_action", "workspace": None, "account": None}
    if not _valid_lane_action(action):
        return failed
    try:
        workspace, home = issue_lane_key(credentials_path, person, action["workspace"], action["lane"],
                                         action["sha256"])
    except Exception as exc:
        reason = str(exc) if str(exc) in ACTION_REASONS else "action_failed"
        return {**failed, "reason": reason}
    return {"id": action_id, "outcome": "done", "reason": None, "workspace": workspace, "account": home}


# --------------------------------------------------------------------------
# 常駐の 1 巡
# --------------------------------------------------------------------------

def _summaries(person, names, credentials_path):
    out = []
    for name in names:
        try:
            with accounts.account_workspace(name):  # 要約は口座の括りの置き場から（括りの段 2）
                out.append(summary(person, name, credentials_path))
        except Exception:
            continue
    return out


def _level(person) -> int:
    until, level = _legacy.get(person, (0, FULL))
    return level if time.monotonic() < until else FULL


def _shaped(level, body, tables, results):
    """sync の本文を形に合わせる。3.14.0 以前の形では、操作の結果に括りの欄を付けない。"""
    if level < FULL:
        body = {**body, "completed": [{k: row[k] for k in ("id", "outcome", "reason")} for row in body["completed"]]}
    if level == PLAIN:
        return body
    keys, workspace_keys = tables
    out = {**body, "keys": keys, "results": [row for row in results if "workspace" not in row]}
    if level == FULL:
        out.update(workspace_keys=workspace_keys,
                   workspace_results=[row for row in results if "workspace" in row])
    return out


def _push(person, names, credentials_path, completed, tables, results, level=FULL):
    """1 回の sync。`tables` が None なら 3.13.0 の形（鍵の表・結果を載せない）。

    新しい形を古い Worker が 400 で断ったら、1 つ古い形で送り直し、しばらくその形で話す
    （deploy は Worker が先だが、逆でも /activity と口座の鍵を止めない）。戻り値は (応答, 届いた形)。
    """
    body = {"accounts": _summaries(person, names, credentials_path), "completed": completed}
    level = PLAIN if tables is None else level
    while True:
        try:
            return relay.signed_request("activity", person, "sync", _shaped(level, body, tables, results)), level
        except relay.RelayError as exc:
            if exc.status != 400 or level == PLAIN:
                raise
        level -= 1
        _legacy[person] = (time.monotonic() + BACKOFF_SECONDS, level)


def _tables(person, credentials_path):
    from . import api_requests
    return (api_requests.key_table(person, credentials_path),
            api_requests.workspace_key_table(person, credentials_path))


def _sync(person, names, credentials_path):
    """要約を押し上げ、/activity の操作と /api/v1 の依頼を行い、結果を押し上げる。

    戻り値は (操作の結果, 依頼が待っていたか)。依頼があれば常駐は次の巡を 2 秒後にする。
    """
    from . import api_requests
    # Worker の口座名の形（PERSON）に合わない名前は押し上げない（1 つで sync 全体が断られないように）。
    names = sorted(name for name in names if relay.PERSON.fullmatch(name))[:ACCOUNTS_MAX]
    level = _level(person)
    tables = _tables(person, credentials_path) if level > PLAIN else None
    keyed = sorted({row["account"] for row in (tables[0] if tables else [])}
                   | (set(api_requests.lane_accounts(person, credentials_path)) if level == FULL else set()))
    leftovers = api_requests.batch(api_requests.undelivered(keyed)) if tables is not None else []
    if level < FULL:
        leftovers = [row for row in leftovers if "workspace" not in row]
    value, level = _push(person, names, credentials_path, [], tables, leftovers, level)
    if level > PLAIN:
        api_requests.delivered([row for row in leftovers if level == FULL or "workspace" not in row])
    actions = value.get("actions") if isinstance(value, dict) else None
    if not isinstance(actions, list):
        raise relay.RelayError("relay_invalid")
    # 古い Worker の応答には requests が無い（何もしない）。
    requests = value.get("requests") if level > PLAIN else None
    requests = requests[:api_requests.REQUESTS_MAX] if isinstance(requests, list) else []
    completed = [row for row in (apply(person, action, credentials_path) for action in actions[:ACTIONS_MAX]) if row]
    results = [row for row in (api_requests.handle(person, item, credentials_path) for item in requests) if row]
    sent = api_requests.batch(results)
    if completed or sent:
        # 操作で鍵が変わった（発行し直し・レーンの鍵）なら、表を引き直して同じ sync で載せる（すぐ使えるように）。
        if completed and level > PLAIN:
            tables = _tables(person, credentials_path)
        _, level = _push(person, names, credentials_path, completed, tables if level > PLAIN else None, sent, level)
        if level > PLAIN:
            api_requests.delivered([row for row in sent if level == FULL or "workspace" not in row])
    return completed, bool(requests) or len(sent) < len(results)


def _map_owners() -> list:
    """対応表のある VM で、括りの持ち主の名（対応表が無い・読めないときは足さない）。"""
    from . import workspaces_map, ws_admin
    try:
        table = workspaces_map.load(ws_admin.map_path())
    except Exception:
        return []
    if not table.present:
        return []
    return sorted({owner for owner in (table.owner_of(ws) for ws in table.workspaces)
                   if isinstance(owner, str) and relay.PERSON.fullmatch(owner)})


def sync_person(person, names, credentials_path):
    return _sync(person, names, credentials_path)[0]


def push_now(person, credentials_path) -> bool:
    """その人の要約を今すぐ 1 回押し上げる（招待の完了の直後・3.14.3 穴 2）。

    常駐の巡は人ごとに 10 秒おきだが、Worker がまだその人を知らない間（完了ページで口座の
    secret が作られる前）の 409 で 5 分待ちに入り、`/login` が「口座の様子がまだ届いていない」を
    数分出し続けた。ここは待ちを見ずに押し、届いたら次を 10 秒後に、届かなくても 5 分待ちには
    入れない（次の巡が 10 秒後にもう一度）。例外は外へ出さない。
    """
    if not credentials_path or not relay.PERSON.fullmatch(str(person or "")):
        return False
    try:
        names = owners(credentials_path).get(person)
        if not names:
            return False
        _sync(person, names, credentials_path)
        return True
    except Exception:
        return False
    finally:
        _next_sync[person] = time.monotonic() + SYNC_SECONDS


def run_once(credentials_path):
    """`thth worker` の 1 巡から呼ぶ。人ごとに 10 秒に 1 回、/api/v1 の依頼が待っていれば 2 秒に 1 回
    （Worker に人が無ければ 5 分待つ）。"""
    if time.monotonic() < _next_sync.get("", 0):
        return
    _next_sync[""] = time.monotonic() + SYNC_SECONDS
    try:
        people = owners(credentials_path)
    except Exception:
        print("activity_owners_unavailable", file=sys.stderr)
        return
    # 括りの持ち主も回す（口座を 1 つも持たなくても、`thth login --lane` のレーンの鍵の発行を拾う・括りの段 2）。
    for person in _map_owners():
        people.setdefault(person, {})
    busy = False
    for person in sorted(people):
        if time.monotonic() < _next_sync.get(person, 0):
            continue
        try:
            _, pending = _sync(person, people[person], credentials_path)
            _next_sync[person] = time.monotonic() + (BUSY_SECONDS if pending else SYNC_SECONDS)
            busy = busy or pending
        except relay.RelayError as exc:
            _next_sync[person] = time.monotonic() + (BACKOFF_SECONDS if exc.status in (400, 404, 409, 410)
                                                     else SYNC_SECONDS * 3)
        except Exception:
            print("activity_sync_failed", file=sys.stderr)
            _next_sync[person] = time.monotonic() + SYNC_SECONDS * 3
    if busy:
        _next_sync[""] = time.monotonic() + BUSY_SECONDS
