"""3.10.0 招待リンク: 招待で用意した口座だけの資格情報（裁定 2026-09-25）。

見るのは:
  - 口座を用意したら、常駐の資格情報のファイルに 1 件だけ増える。accounts はその口座 1 つ・
    scope user・writes true・actor は口座名。既存の資格情報は変えない。0600 のまま。
  - bearer の値はどこにも出ない（ファイル・変更ログ・招待の記録・stdout・stderr）。残るのは hash だけ。
  - その資格情報で承認 job の文脈が読める（server_writes が口座を引ける）。
  - 退出（thth account leave）で消える。変更ログは credential_added・credential_removed。
"""
from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from thth import admin_log, approval_relay, invites, leave, report_http, server_writes
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, make, authorize_params  # noqa: F401

BEARER = "invite-bearer-must-never-appear-" + "q" * 16


@pytest.fixture
def credentials(world, monkeypatch):
    root = world[0]
    private = root.parent / "private"
    private.mkdir(mode=0o700)
    path = private / "report-credentials.json"
    operator = {"sha256": hashlib.sha256(b"operator-bearer").hexdigest(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "revoked": False, "accounts": {"other-threads": "other"}, "writes": True, "actor": "masaru"}
    path.write_text(json.dumps({"schema_version": 1, "root": str(root), "credentials": [operator]}))
    path.chmod(0o600)
    monkeypatch.setattr(invites, "_new_bearer", lambda: BEARER)
    return path, operator


def run_flow(world, path):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once(str(path))
    relay.deliver(authorize_params(worker, digest)["state"])
    invites.run_once(str(path))
    return invites.STORE.get(row["invite_id"]), digest


def test_完了で資格情報が1件増え_その口座だけ(world, credentials):
    path, operator = credentials
    record, _ = run_flow(world, path)
    name = record["account"]
    rows = json.loads(path.read_text())["credentials"]
    assert rows[0] == operator and len(rows) == 2
    added = rows[1]
    assert added["accounts"] == {name: "meta-review"} and added["scope"] == "user"
    assert added["writes"] is True and added["actor"] == name and added["revoked"] is False
    assert added["sha256"] == hashlib.sha256(BEARER.encode()).hexdigest() == record["credential_sha256"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # 常駐と MCP が読む形のまま。その資格情報でその口座の書き込みの文脈が引ける。
    root, loaded = report_http.load_credentials(path)
    context = next(item[3] for item in loaded if item[0] == added["sha256"])
    assert context.allowed_accounts == {name: "meta-review"} and context.actor == name and context.writes
    assert server_writes.current(context, name, write=True)["account"] == name
    (event,) = admin_log.read(event="credential_added")[0]
    assert event["account"] == name and event["diff"]["via_invite"] == [None, record["invite_id"]]
    # 2 巡目以降は足さない。
    invites.run_once(str(path))
    assert len(json.loads(path.read_text())["credentials"]) == 2


def test_bearerの値はどこにも出ない(world, credentials, capsys):
    path, _ = credentials
    record, _ = run_flow(world, path)
    root = world[0]
    for file in list(root.rglob("*")) + list(path.parent.rglob("*")):
        if file.is_file():
            assert BEARER not in file.read_text(errors="ignore"), file
    out = capsys.readouterr()
    assert BEARER not in out.out + out.err
    logged = json.dumps(admin_log.read()[0])
    assert BEARER not in logged and record["credential_sha256"] not in logged
    assert "credential_sha256" not in json.dumps(invites.list_invites())


def test_資格情報のファイルが無い_壊れていれば足さずに次の巡で(world, credentials):
    path, _ = credentials
    good = path.read_bytes()
    path.write_text("{broken")
    path.chmod(0o600)
    record, _ = run_flow(world, path)
    assert record["status"] == "used" and "credential_sha256" not in record
    path.write_bytes(good)
    invites.run_once(str(path))
    assert invites.STORE.get(record["invite_id"])["credential_sha256"]
    assert len(json.loads(path.read_text())["credentials"]) == 2


def test_退出でその口座の資格情報が消える(world, credentials, monkeypatch):
    path, operator = credentials
    record, _ = run_flow(world, path)
    name = record["account"]
    invite_worker = approval_relay.signed_request
    monkeypatch.setattr(approval_relay, "signed_request",
                        lambda kind, subject, operation, body: {"status": "revoked"} if kind == "account"
                        else invite_worker(kind, subject, operation, body))
    result = leave.run(name, by="masaru")
    assert result["phase"] == "completed"
    assert json.loads(path.read_text())["credentials"] == [operator]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    (event,) = admin_log.read(event="credential_removed")[0]
    assert event["account"] == name
    # もう一度打っても同じ結果（何もしない）。
    leave.run(name, by="masaru")
    assert json.loads(path.read_text())["credentials"] == [operator]
    assert len(admin_log.read(event="credential_removed")[0]) == 1
