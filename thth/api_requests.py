"""遠くの道（設計 3.14.0 §3.1）の VM 側: thth.me/api/v1 に届いた依頼を、その鍵の資格で行う。

向きは /activity と同じ: **VM が Worker へ署名つきで取りに行く**（`activity.sync_person` の sync に相乗り）。
VM に外から入る口は作らない。

- 押し上げるもの: 持ち主（person）ごとに、その人の**有効な鍵の表**（sha256・口座・期限。取り消し・期限切れは
  含めない）。Worker は照合の表を置き替え、表に無い鍵は Worker が先に 401 で断る。**権威は VM**: ここで資格情報の
  ファイルをもう一度読み、鍵・持ち主・口座を確かめてから行う。
- 受け取るもの: 口座ごとの待っている依頼 `{request_id, account, operation, key_sha256, body}`。
  書く口・読む口は `server_writes.serve` が受け付ける（名前の表はそちら。知らない名前は `unsupported_operation`）。
- 返すもの: `{request_id, account, status: "done", result}`。result は成功ならその JSON、断りなら
  `{error: <符丁>}`（例外を Worker に出さない）。

二重に行わない: 依頼ごとに `state/<口座>/requests/<id>.json`（0600・置き場 0700）を先に置き（`running`）、
行ったら `done` と結果を書く。sync が落ちても同じ依頼をもう一度行わない（結果を送り直すだけ）。
`running` のまま残っていた依頼（途中で落ちた）は `outcome_unknown` と答える。本文はここに残さない。
遠くの道で行ったことは `via: api` と鍵の id（credential_digest の先頭 12 字）で口座の記録に残る。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import time
from pathlib import Path

from . import accounts, jst, relay, server_files

REQUEST_ID = re.compile(r"([A-Za-z0-9_-]{43})\.([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\Z")
OPERATION = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
KEYS_MAX = 64
REQUESTS_MAX = 16
RESULT_MAX = 131072        # Worker の 1 件の上限（JSON の UTF-16 の長さ）
RESULTS_BYTES = 786432     # 1 回の sync に載せる結果の合計
RESEND_SECONDS = 150       # Worker が結果を受ける間（依頼の 120 秒＋猶予）。過ぎたら送り直さない
KEEP_SECONDS = 86400       # 行った印を残す間（二重に行わないため）
KEY_ERRORS = frozenset(("invalid_key", "key_expired"))


class Refused(Exception):
    pass


# --------------------------------------------------------------------------
# 鍵の表
# --------------------------------------------------------------------------

def _credentials(credentials_path):
    from .report_http import load_credentials
    return load_credentials(Path(credentials_path))[1]


def key_table(person, credentials_path) -> list:
    """その人の有効な鍵の表（hash だけ）。取り消し・期限切れ・運営者の鍵は含めない。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = []
    for digest, expiry, revoked, context in _credentials(credentials_path):
        if revoked or now >= expiry or context.scope != "user" or context.actor != person:
            continue
        for account in sorted(context.allowed_accounts):
            if relay.PERSON.fullmatch(account):
                rows.append({"sha256": digest, "account": account, "expires_at": jst.iso(expiry)})
    rows.sort(key=lambda row: (row["account"], row["sha256"]))
    return rows[:KEYS_MAX]


def _context(person, account, key_sha256, credentials_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    for digest, expiry, revoked, context in _credentials(credentials_path):
        if digest != key_sha256:
            continue
        if revoked or context.scope != "user" or context.actor != person or (
                account not in context.allowed_accounts and account not in context.excluded_accounts):
            raise Refused("invalid_key")
        if now >= expiry:
            raise Refused("key_expired")
        return context
    raise Refused("invalid_key")


# --------------------------------------------------------------------------
# 行った印
# --------------------------------------------------------------------------

def _directory(account) -> Path:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return Path(accounts.state_dir_for(account)) / "requests"


def _read(fd, name):
    try:
        value = json.loads(server_files.read_at(fd, name, private=True))
    except FileNotFoundError:
        return None
    return value if type(value) is dict else {"status": "running"}


def _write(fd, name, value, *, new=False):
    server_files.replace_at(fd, name, server_files.encode(value), new=new, private=True)


def _entry(record):
    return {"request_id": record["request_id"], "account": record["account"], "status": "done",
            "result": record["result"]}


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-16-le")) // 2


# --------------------------------------------------------------------------
# 1 件の依頼
# --------------------------------------------------------------------------

def _failure(exc) -> dict:
    from . import server_writes
    code = str(exc)
    if code not in server_writes.SAFE_ERRORS:
        return {"error": "request_failed"}
    out = {"error": code}
    detail = getattr(exc, "reason", None)
    if code == "invalid_draft":
        out["reason"] = detail if detail in server_writes.DRAFT_REASONS else "validation_failed"
    elif code == "account_stopped" and detail in server_writes.GUARD_DETAILS:
        out["reason"] = detail
    next_at = getattr(exc, "next_at", None)
    if isinstance(next_at, str):
        out["next_at"] = next_at
    return out


def _run(person, item, credentials_path) -> dict:
    """行って結果の JSON を返す（例外は外へ出さない）。"""
    from . import server_writes
    from .report_service import ReportServiceError
    account, operation, body = item["account"], item["operation"], item["body"]
    try:
        if type(body) is not dict or "operation" in body or body.get("account") != account:
            return {"error": "invalid_request"}
        context = _context(person, account, item["key_sha256"], credentials_path)
        result = server_writes.serve(context, {**body, "operation": operation}, via="api")
        result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if type(result) is not dict:
            return {"error": "request_failed"}
        return result if _size(result) <= RESULT_MAX else {"error": "result_too_large"}
    except Refused as exc:
        return {"error": str(exc)}
    except ReportServiceError as exc:
        return _failure(exc)
    except Exception:
        return {"error": "request_failed"}


def _valid(item) -> bool:
    if type(item) is not dict or set(item) != {"request_id", "account", "operation", "key_sha256", "body"}:
        return False
    match = REQUEST_ID.fullmatch(item["request_id"]) if isinstance(item["request_id"], str) else None
    return (match is not None and match.group(2) == item["account"] and accounts.name_is_safe(item["account"])
            and isinstance(item["operation"], str) and OPERATION.fullmatch(item["operation"]) is not None
            and isinstance(item["key_sha256"], str) and HEX64.fullmatch(item["key_sha256"]) is not None)


def handle(person, item, credentials_path):
    """1 件を行い `{request_id, account, status, result}` を返す。同じ依頼をもう一度は行わない。"""
    if not _valid(item):
        return None
    request_id, account = item["request_id"], item["account"]
    name = REQUEST_ID.fullmatch(request_id).group(1) + ".json"
    try:
        with server_files.directory(_directory(account), create=True, private=True) as fd:
            with server_files.lock_at(fd, ".lock"):
                old = _read(fd, name)
                if old is not None:
                    if old.get("status") == "done" and type(old.get("result")) is dict:
                        return _entry(old)
                    # 途中で落ちた・もう送った: 二度目は行わない。
                    old.update(request_id=request_id, account=account, status="done", delivered=False,
                               result={"error": "outcome_unknown"})
                    _write(fd, name, old)
                    return _entry(old)
                record = {"request_id": request_id, "account": account, "operation": item["operation"],
                          "credential": item["key_sha256"][:12], "received_at": time.time(),
                          "status": "running", "delivered": False, "result": None}
                _write(fd, name, record, new=True)
            # 行っている間は印の lock を持たない（公開は口座の lock を別に取る）。
            record["result"] = _run(person, item, credentials_path)
            record["status"] = "done"
            try:
                with server_files.lock_at(fd, ".lock"):
                    _write(fd, name, record)
            except Exception:
                # 行ったあとで印を書けなくても、行った結果は返す（印は running のまま＝二度目は行わない）。
                pass
            return _entry(record)
    except Exception:
        # 印が置けない（置き場が壊れている）: 行わずに断る。二重に行うよりよい。
        return {"request_id": request_id, "account": account, "status": "done",
                "result": {"error": "request_unavailable"}}


# --------------------------------------------------------------------------
# 送り直しと後始末
# --------------------------------------------------------------------------

def batch(entries) -> list:
    """1 回の sync に載せる分（合計が RESULTS_BYTES まで）。残りは次の sync で送り直す。"""
    out, size = [], 0
    for entry in entries:
        size += _size(entry)
        if size > RESULTS_BYTES:
            break
        out.append(entry)
    return out


def undelivered(names) -> list:
    """まだ Worker に届けていない結果（送り直す分）。古い印はここで消す。"""
    out = []
    now = time.time()
    for account in sorted(set(names)):
        try:
            with server_files.directory(_directory(account), private=True) as fd:
                with server_files.lock_at(fd, ".lock"):
                    for name in sorted(os.listdir(fd)):
                        if not name.endswith(".json") or not relay.OPAQUE.fullmatch(name[:-5]):
                            continue
                        record = _read(fd, name) or {}
                        age = now - record.get("received_at", 0) if isinstance(record.get("received_at"), (int, float)) else KEEP_SECONDS
                        if age >= KEEP_SECONDS:
                            os.unlink(name, dir_fd=fd)
                        elif (record.get("status") == "done" and not record.get("delivered")
                              and type(record.get("result")) is dict and REQUEST_ID.fullmatch(str(record.get("request_id")))):
                            if age < RESEND_SECONDS:
                                out.append(_entry(record))
                            else:
                                # Worker はもう受けない。結果は手元にも残さない。
                                record.update(delivered=True, result=None)
                                _write(fd, name, record)
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return out


def delivered(entries):
    """Worker に届いた結果を印から落とす（結果の中身は手元にも残さない）。"""
    for entry in entries:
        try:
            match = REQUEST_ID.fullmatch(entry["request_id"])
            with server_files.directory(_directory(entry["account"]), private=True) as fd:
                with server_files.lock_at(fd, ".lock"):
                    name = match.group(1) + ".json"
                    record = _read(fd, name)
                    if record is not None and record.get("status") == "done":
                        record.update(delivered=True, result=None)
                        _write(fd, name, record)
        except Exception:
            continue
