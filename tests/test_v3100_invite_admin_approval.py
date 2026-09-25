"""3.10.0 招待リンク: 運営者の口で下書きを置き、承認 URL を出す（裁定 2026-09-25）。

見るのは:
  - `thth admin draft put` と `thth admin approval request` で、招待の口座の承認 URL が出る。
  - 承認 URL はその口座の承認者（口座名）あて。bearer は使わない（その口座だけの資格情報の
    1 件に結ぶ）。変更ログ approval_requested は via cli・by は運営者。
  - 招待で用意していない口座（運営者自身の repo 型の口座）には出さない。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from thth import accounts, admin_log, approval_jobs, approval_relay, cli, invites
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, authorize_params, make  # noqa: F401
from tests.test_v3100_invite_credential import credentials, run_flow  # noqa: F401


@pytest.fixture
def sessions(world, credentials, monkeypatch):
    path, _ = credentials
    record, _ = run_flow(world, path)
    # 3.12.0 より前に用意した招待の口座（台帳に approval が無い）は all として承認 job の道を通る。
    ledger = Path(accounts.accounts_dir()) / f"{record['account']}.json"
    data = json.loads(ledger.read_text())
    assert data.pop("approval") == "none"
    ledger.write_text(json.dumps(data))
    created = []
    invite_worker = approval_relay.signed_request

    def worker(kind, subject, operation, body):
        if kind == "session" and operation == "create":
            created.append(json.loads(json.dumps(body)))
            return {"status": "pending", "expires_at": int(time.time() * 1000) + 600_000}
        return invite_worker(kind, subject, operation, body)
    monkeypatch.setattr(approval_relay, "signed_request", worker)
    return record, created


def test_運営者の口で下書きを置き_その口座の承認者あての承認URLが出る(world, sessions, capsys, tmp_path):
    record, created = sessions
    name = record["account"]
    body = tmp_path / "body.txt"
    body.write_text("審査用の下書きです。")
    assert cli.main(["admin", "draft", "put", name, "--body-file", str(body), "--by", "masaru"]) == 0
    draft_id = re.search(r"draft_id=([0-9a-f]{64})", capsys.readouterr().out).group(1)
    assert cli.main(["admin", "approval", "request", name, "--draft", draft_id, "--send", "--by", "masaru"]) == 0
    out = capsys.readouterr().out
    # 3.11.0: 既定で本人の承認待ちの一覧にも出すので、一覧の行が先に出て、承認 URL は「開いてから 10 分」。
    assert re.search(r"承認 URL（開いてから 10 分）: https://thth\.me/approve/[A-Za-z0-9_-]{43}\n", out)
    assert f"承認待ちの一覧: https://thth.me/pending（本人がユーザ名 {name} と承認 secret で入る" in out
    (session,) = created
    assert session["person"] == name and session["account"] == name and session["kind"] == "send"
    assert session["text"].startswith("審査用の下書きです。")
    # job はその口座だけの資格情報の 1 件（hash）に結ぶ。常駐の既存の再確認がそのまま効く。
    (job_file,) = (world[0] / "state" / name / "approval-jobs").glob("*.json")
    job = json.loads(job_file.read_text())
    assert job["actor"] == name and job["credential_digest"] == record["credential_sha256"] and job["via"] == "cli"
    (event,) = admin_log.read(event="approval_requested")[0] + admin_log.read(event="send_requested")[0]
    assert event["account"] == name and event["via"] == "cli" and event["by"] == "masaru"
    # 原稿の承認（--send なし）も同じ人あて。
    assert cli.main(["admin", "approval", "request", name, "--draft", draft_id, "--by", "masaru"]) == 0
    assert created[-1]["person"] == name and created[-1]["kind"] == "approve"
    (event,) = admin_log.read(event="approval_requested")[0]
    assert event["via"] == "cli" and event["by"] == "masaru"


def test_招待で用意していない口座には出さない(world, sessions, capsys, isolated_account_factory):
    record, created = sessions
    isolated_account_factory("masaru-threads", project="masaru")
    for argv in (["admin", "approval", "request", "masaru-threads", "--draft", "0" * 64, "--by", "masaru"],
                 ["admin", "approval", "request", "no-such-account", "--draft", "0" * 64, "--by", "masaru"]):
        assert cli.main(argv) == 2
        assert "admin_approval_invite_account_only: " in capsys.readouterr().err
    assert created == [] and admin_log.read(event="approval_requested")[0] == []


def test_その口座の資格情報が無ければ断る(world, sessions, capsys, credentials):
    record, created = sessions
    path, operator = credentials
    config = json.loads(path.read_text())
    config["credentials"] = [operator]
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    assert cli.main(["admin", "approval", "request", record["account"], "--draft", "0" * 64, "--by", "masaru"]) == 2
    assert "admin_approval_credential_missing: " in capsys.readouterr().err
    assert created == []


def test_常駐はcliで作ったjobも読める(world, sessions, tmp_path):
    record, created = sessions
    name = record["account"]
    row = invites.admin_draft_put(name, body="本文", by="masaru")
    result = invites.admin_approval_request(name, draft=row["draft_id"], by="masaru")
    with approval_jobs.server_files.directory(approval_jobs.directory(name), private=True) as fd:
        job = approval_jobs._load(fd, result["job_id"])
    assert job["status"] == "pending" and job["via"] == "cli"


def test_no_listは一覧に出さず承認URLは10分(world, sessions, capsys):
    # 3.11.0: 既定は本人の承認待ちの一覧にも出す。--no-list は従来どおり URL だけ（10 分）。
    record, created = sessions
    name = record["account"]
    row = invites.admin_draft_put(name, body="本文", by="masaru")
    assert cli.main(["admin", "approval", "request", name, "--draft", row["draft_id"], "--no-list", "--by", "masaru"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"承認 URL（10 分）: https://thth\.me/approve/[A-Za-z0-9_-]{43}\n", out)
    assert "承認待ちの一覧" not in out and "listed" not in created[-1]
    assert cli.main(["admin", "approval", "request", name, "--draft", row["draft_id"], "--by", "masaru"]) == 0
    assert created[-1]["listed"] is True
