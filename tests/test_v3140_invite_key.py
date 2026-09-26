"""3.14.0 段 2（§3.3）: 招待の完了ページに出したアシスタントの鍵。

見るのは:
  - Worker が完了ページを出すその要求の中で bearer を作り、本人に 1 度だけ見せる。VM が受け取るのは
    hash だけ（招待の status に載る）。常駐はその hash に、招待で足したその口座だけの資格を差し替える
    （変更ログ credential_rotated・via_invite）。何度回しても同じ。
  - その鍵が遠くの道の鍵の表に載る（持ち主は口座名）。
  - 3.13.0 の Worker（hash を返さない）なら資格はそのまま。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import stat

from thth import admin_log, api_requests, invites, relay
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world  # noqa: F401
from tests.test_v3100_invite_credential import BEARER, credentials, run_flow  # noqa: F401


def done_with_key(world, monkeypatch, digest, key):
    fake = world[2]
    fake.invites[digest]["status"] = "done"

    def status(kind, subject, operation, body):
        value = fake(kind, subject, operation, body)
        if operation == "status" and key is not None:
            value = {**value, "key_sha256": key}
        return value
    monkeypatch.setattr(relay, "signed_request", status)


def test_完了ページで出した鍵のhashに招待の口座の資格を差し替える(world, credentials, monkeypatch):
    path, operator = credentials
    record, digest = run_flow(world, path)
    assert record["credential_sha256"] == hashlib.sha256(BEARER.encode()).hexdigest()
    key = hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest()
    done_with_key(world, monkeypatch, digest, key)
    invites.run_once(str(path))
    rows = json.loads(path.read_text())["credentials"]
    assert rows[0] == operator and len(rows) == 2
    assert rows[1]["sha256"] == key and rows[1]["revoked"] is False and rows[1]["actor"] == record["account"]
    assert rows[1]["accounts"] == {record["account"]: "meta-review"} and rows[1]["writes"] is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    latest = invites.STORE.get(record["invite_id"])
    assert latest["credential_sha256"] == key and latest["key_synced"] is True and latest["approver_set"] is True
    (event,) = admin_log.read(event="credential_rotated")[0]
    assert event["account"] == record["account"] and event["diff"]["via_invite"] == [None, record["invite_id"]]
    assert key not in json.dumps(admin_log.read()[0])
    # 招待は終わり（もう Worker に聞かない）。何度回しても同じ。
    token = invites._credentials.set(str(path))
    try:
        assert not invites._active(latest)
    finally:
        invites._credentials.reset(token)
    invites.run_once(str(path))
    assert json.loads(path.read_text())["credentials"] == rows
    assert len(admin_log.read(event="credential_rotated")[0]) == 1
    assert len(admin_log.read(event="approver_set")[0]) == 1
    # その鍵が遠くの道の鍵の表に載る（持ち主は口座名）。
    assert [row["sha256"] for row in api_requests.key_table(record["account"], path)] == [key]


def test_3_13のWorkerが鍵のhashを返さなければ資格はそのまま(world, credentials, monkeypatch):
    path, _ = credentials
    record, digest = run_flow(world, path)
    before = path.read_text()
    done_with_key(world, monkeypatch, digest, None)
    invites.run_once(str(path))
    latest = invites.STORE.get(record["invite_id"])
    assert latest["key_synced"] is True and latest["credential_sha256"] == record["credential_sha256"]
    assert path.read_text() == before and admin_log.read(event="credential_rotated")[0] == []
