"""3.10.0 招待リンク: 招待の記録と CLI（create・revoke・list）。設計 §1-1・§2。

見るのは:
  - code は tty に 1 回だけ。VM の記録・変更ログ・stdout・stderr には hash だけ（code は無い）。
  - Worker への登録は code の hash を subject にし、控えのメモ（label）は送らない。
  - 持ち主の組に入っている project では作らない（外の人を組に入れない）。
  - 取り消しは Worker が先。使用済みは取り消さない。
Worker は偽物（`approval_relay.signed_request` を差し替える）。外への通信はしない。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import socket

import pytest

from thth import admin_log, approval_relay, cli, invites, plaza


class FakeTTY:
    def __init__(self, fail=False):
        self.stream = io.StringIO()
        self.fail = fail

    def __call__(self):
        import contextlib

        @contextlib.contextmanager
        def opened():
            if self.fail:
                raise approval_relay.RelayError("approver_tty_required")
            yield self.stream
        return opened()

    def codes(self):
        return re.findall(r"/invite/([A-Za-z0-9_-]{43})\n", self.stream.getvalue())


class FakeWorker:
    """Worker の招待の置き場を真似る（hash → 状態）。"""

    def __init__(self):
        self.calls = []
        self.invites = {}
        self.fail = {}

    def __call__(self, kind, subject, operation, body):
        assert kind == "invite"
        self.calls.append((subject, operation, json.loads(json.dumps(body))))
        if operation in self.fail:
            raise approval_relay.RelayError("approval_relay_outcome_unknown", status=self.fail[operation])
        if operation == "create":
            self.invites[subject] = {"status": "open", **body}
            return {"status": "open"}
        if operation == "revoke":
            if subject not in self.invites:
                raise approval_relay.RelayError("approval_relay_outcome_unknown", status=404)
            self.invites[subject]["status"] = "revoked"
            return {"status": "revoked"}
        if operation == "status":
            row = self.invites.get(subject)
            if row is None:
                raise approval_relay.RelayError("approval_relay_outcome_unknown", status=404)
            return {"status": row["status"], "expires_at": row["expires_at"], "clicked_at": row.get("clicked_at")}
        raise AssertionError(operation)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("external network"))
    tty, worker = FakeTTY(), FakeWorker()
    monkeypatch.setattr(invites, "terminal", tty)
    monkeypatch.setattr(approval_relay, "signed_request", worker)
    return root, tty, worker


def make(**overrides):
    values = dict(media="threads", project="meta-review", label="審査員 A", expires="30d", production=True, by="masaru")
    values.update(overrides)
    return invites.create(**values)


def test_codeはttyに1回だけ_VMにはhashだけ_Workerにはlabelを送らない(env, capsys):
    root, tty, worker = env
    row = make()
    codes = tty.codes()
    assert len(codes) == 1
    code = codes[0]
    digest = hashlib.sha256(code.encode()).hexdigest()
    # Worker への登録: subject は hash・本文は media/production/expires_at だけ。
    assert worker.calls == [(digest, "create", {"media": "threads", "production": True,
                                                "expires_at": worker.invites[digest]["expires_at"],
                                                "scopes": invites.invite_scopes()})]
    assert "threads_share_to_instagram" not in invites.invite_scopes()
    assert {"threads_basic", "threads_content_publish", "threads_delete"} <= set(invites.invite_scopes())
    # 手元の記録は hash だけ。どのファイルにも code は無い。
    record = invites.STORE.get(row["invite_id"])
    assert record["code_hash"] == digest and record["status"] == "open"
    assert record["account"].startswith("inv-meta-review-") and record["production"] is True
    for path in root.rglob("*"):
        if path.is_file():
            assert code not in path.read_text(errors="ignore"), path
    out = capsys.readouterr()
    assert code not in out.out + out.err
    assert "code_hash" not in row and code not in json.dumps(row)
    rows, broken = admin_log.read(event="invite_created")
    assert not broken and len(rows) == 1 and rows[0]["account"] == "invite-" + row["invite_id"]
    assert code not in json.dumps(rows) and digest not in json.dumps(rows)
    # 期限は 30 日（最長 90 日）。
    assert 29 * 86_400_000 < record["expires_at"] - invites.now_ms() <= 30 * 86_400_000


@pytest.mark.parametrize("overrides,reason", [
    (dict(media="bluesky"), "invite_media_unsupported"),
    (dict(expires="91d"), "invalid_expires"),
    (dict(expires="0d"), "invalid_expires"),
    (dict(project="../x"), "invalid_project"),
    (dict(label="二行\nの控え"), "invalid_label"),
])
def test_形の断りはWorkerに何も送らずcodeも作らない(env, overrides, reason):
    root, tty, worker = env
    with pytest.raises(invites.InviteError, match="^" + reason + "$"):
        make(**overrides)
    assert worker.calls == [] and tty.codes() == []
    assert invites.list_invites() == ([], 0)


def test_持ち主の組に入っているprojectでは作らない(env):
    root, tty, worker = env
    with plaza.STORE.locked() as directory:
        plaza.STORE.write(directory, {"schema_version": plaza.SCHEMA_VERSION,
                                      "owners": {"masaru": {"projects": ["meta-review", "kopicha"],
                                                            "at": "2026-09-25T10:00:00+09:00", "by": "masaru"}}},
                          name=plaza.OWNERS_FILE)
    with pytest.raises(invites.InviteError, match="^invite_project_in_owner_group$"):
        make()
    assert worker.calls == [] and tty.codes() == []
    assert make(project="outside")["status"] == "open"


def test_ttyが無ければcodeを作る前に断る(env, monkeypatch):
    root, tty, worker = env
    monkeypatch.setattr(invites, "terminal", FakeTTY(fail=True))
    with pytest.raises(invites.InviteError, match="^invite_tty_required$"):
        make()
    assert worker.calls == [] and invites.list_invites() == ([], 0)


@pytest.mark.parametrize("status,expected", [(400, "failed"), (None, "unknown"), (503, "unknown")])
def test_Workerが断る_分からないときはURLを出さない(env, status, expected):
    root, tty, worker = env
    worker.fail["create"] = status
    with pytest.raises(invites.InviteError):
        make()
    assert tty.codes() == []
    rows, _ = invites.list_invites()
    assert [row["status"] for row in rows] == [expected]
    assert admin_log.read(event="invite_created")[0] == []


def test_取り消しはWorkerが先_使用済みは断る(env):
    root, tty, worker = env
    row = make()
    digest = invites.STORE.get(row["invite_id"])["code_hash"]
    assert invites.revoke(row["invite_id"], by="masaru")["status"] == "revoked"
    assert worker.invites[digest]["status"] == "revoked"
    assert [r["event"] for r in admin_log.read()[0]] == ["invite_created", "invite_revoked"]
    with pytest.raises(invites.InviteError, match="^invite_not_open$"):
        invites.revoke(row["invite_id"], by="masaru")
    # 使用済みの招待は Worker に問い合わせずに断る（口座は thth account leave で止める）。
    used = make()
    record = invites.STORE.get(used["invite_id"])
    record["status"] = "used"
    with invites.STORE.locked() as directory:
        invites.STORE.write(directory, record)
    calls = len(worker.calls)
    with pytest.raises(invites.InviteError, match="^invite_already_used$"):
        invites.revoke(used["invite_id"], by="masaru")
    assert len(worker.calls) == calls


def test_Workerで口座の用意が済んでいれば409で断る(env):
    root, tty, worker = env
    row = make()
    worker.fail["revoke"] = 409
    with pytest.raises(invites.InviteError, match="^invite_already_used$"):
        invites.revoke(row["invite_id"], by="masaru")
    assert invites.STORE.get(row["invite_id"])["status"] == "open"


def test_登録が不明な招待は_Workerに無ければ取り消し済みにできる(env):
    root, tty, worker = env
    worker.fail["create"] = None
    with pytest.raises(invites.InviteError):
        make()
    del worker.fail["create"]
    (row,), _ = invites.list_invites()
    assert invites.revoke(row["invite_id"], by="masaru")["status"] == "revoked"


def test_CLI_作る_一覧_取り消す(env, capsys):
    root, tty, worker = env
    assert cli.main(["admin", "invite", "create", "--media", "threads", "--project", "meta-review",
                     "--label", "審査", "--expires", "14d", "--production", "--by", "masaru"]) == 0
    out = capsys.readouterr()
    code = tty.codes()[0]
    assert out.out.startswith("invite_created: id=") and code not in out.out + out.err
    invite_id = re.search(r"id=([0-9a-f]{12})", out.out).group(1)
    assert cli.main(["admin", "invite", "list", "--json"]) == 0
    listed = capsys.readouterr().out
    assert code not in listed and "code_hash" not in listed
    assert json.loads(listed)["invites"][0]["invite_id"] == invite_id
    assert cli.main(["admin", "invite", "revoke", invite_id, "--by", "masaru"]) == 0
    assert cli.main(["admin", "invite", "create", "--media", "mastodon", "--project", "p", "--by", "masaru"]) == 2
    err = capsys.readouterr().err
    assert "invite_media_unsupported: " in err and "thth auth" in err
    with pytest.raises(SystemExit):
        cli.main(["admin", "invite", "create", "--media", "threads", "--project", "p"])
