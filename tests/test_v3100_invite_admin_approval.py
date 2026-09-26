"""3.10.0 招待リンク: 運営者の口で招待の口座に下書きを置く（裁定 2026-09-25）。

3.13.0 で承認ページを消したので、承認 URL を出す口（`thth admin approval request`）は無い。
見るのは:
  - `thth admin draft put` で、招待の口座に下書きが置ける（bearer は使わない・その口座だけの
    資格情報の 1 件で書き込みの口を通す）。承認の session は作らない。
  - `thth admin approval request` は無い（argparse が断る）。
  - 招待で用意していない口座（運営者自身の repo 型の口座）には使えない。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from thth import accounts, admin_log, approval_relay, cli, invites
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, authorize_params, make  # noqa: F401
from tests.test_v3100_invite_credential import credentials, run_flow  # noqa: F401


@pytest.fixture
def sessions(world, credentials, monkeypatch):
    """招待の口座を用意し、Worker への承認の session の呼び出しを数える（3.13.0 では 0 のまま）。"""
    path, _ = credentials
    record, _ = run_flow(world, path)
    ledger = Path(accounts.accounts_dir()) / f"{record['account']}.json"
    assert "approval" not in json.loads(ledger.read_text())
    created = []
    invite_worker = approval_relay.signed_request

    def worker(kind, subject, operation, body):
        if kind == "session":
            created.append((operation, json.loads(json.dumps(body))))
            raise AssertionError("3.13.0: no approval session")
        return invite_worker(kind, subject, operation, body)
    monkeypatch.setattr(approval_relay, "signed_request", worker)
    return record, created


def test_運営者の口で招待の口座に下書きを置く_承認のsessionは作らない(world, sessions, capsys, tmp_path):
    record, created = sessions
    name = record["account"]
    body = tmp_path / "body.txt"
    body.write_text("審査用の下書きです。")
    assert cli.main(["admin", "draft", "put", name, "--body-file", str(body), "--by", "masaru"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"draft_put: account=" + re.escape(name) + r" draft_id=[0-9a-f]{64} status=draft", out)
    assert "approve" not in out and "/pending" not in out
    assert created == []
    assert not (world[0] / "state" / name / "approval-jobs").exists()
    assert admin_log.read(event="approval_requested")[0] == []


def test_admin_approval_requestは無い(world, sessions, capsys):
    record, created = sessions
    with pytest.raises(SystemExit) as caught:
        cli.main(["admin", "approval", "request", record["account"], "--draft", "0" * 64, "--by", "masaru"])
    assert caught.value.code == 2
    assert "invalid choice: 'approval'" in capsys.readouterr().err
    assert not hasattr(invites, "admin_approval_request") and created == []


def test_招待で用意していない口座には使えない(world, sessions, capsys, isolated_account_factory, tmp_path):
    record, created = sessions
    isolated_account_factory("masaru-threads", project="masaru")
    body = tmp_path / "body.txt"
    body.write_text("本文")
    for name in ("masaru-threads", "no-such-account"):
        assert cli.main(["admin", "draft", "put", name, "--body-file", str(body), "--by", "masaru"]) == 2
        assert "admin_approval_invite_account_only: " in capsys.readouterr().err
    assert created == []


def test_その口座の資格情報が無ければ断る(world, sessions, capsys, credentials, tmp_path):
    record, created = sessions
    path, operator = credentials
    config = json.loads(path.read_text())
    config["credentials"] = [operator]
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    body = tmp_path / "body.txt"
    body.write_text("本文")
    assert cli.main(["admin", "draft", "put", record["account"], "--body-file", str(body), "--by", "masaru"]) == 2
    assert "admin_approval_credential_missing: " in capsys.readouterr().err
    assert created == []
