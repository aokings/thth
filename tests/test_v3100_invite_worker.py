"""3.10.0 招待リンク: VM の常駐が clicked を拾い、認可の完了で口座を用意する。設計 §1-3〜5・§2。

見るのは:
  - 押された招待だけを拾い、認可の session（state・read key）は VM で作る。認可 URL は
    Threads の authorize・戻り先は https://thth.me/callback/・権限は招待の一覧。
  - 認可の code が預かり所に届いたら、VM で token に換え、口座（台帳・token 0600）を用意する。
    handle は認可した人の username から取る。同じ Threads 口座には 2 つ目を作らない。
  - 口座の用意は変更ログ（account_added via invite・token_set・invite_used）に残る。
  - Worker に「用意ができた」（person は口座名）を届くまで送る。secret は VM に無い。
Worker・預かり所・Threads は偽物。外への通信はしない。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import urllib.parse

import pytest

from thth import accounts, admin_log, appenv, worker as worker_mod, relay as vm_relay, authflow, invites, oauth, plaza
from tests.test_v3100_invite_create import FakeTTY, FakeWorker, env as _env  # noqa: F401


class Worker(FakeWorker):
    """clicked・authorize・reset・complete・done まで真似る。"""

    def __call__(self, kind, subject, operation, body):
        if operation in ("create", "revoke") or operation in self.fail:
            return super().__call__(kind, subject, operation, body)
        self.calls.append((subject, operation, json.loads(json.dumps(body))))
        row = self.invites.get(subject)
        if row is None:
            raise vm_relay.RelayError("relay_outcome_unknown", status=404)
        if operation == "status":
            return {"status": row["status"], "expires_at": row["expires_at"], "clicked_at": row.get("clicked_at")}
        if operation == "authorize":
            assert row["status"] == "clicked"
            row.update(status="authorizing", authorize_url=body["authorize_url"])
            return {"status": "authorizing"}
        if operation == "reset":
            row.update(status="open", reason=body["reason"], authorize_url=None)
            return {"status": "open"}
        if operation == "complete":
            row.update(status="ready", **body)
            return {"status": "ready"}
        raise AssertionError(operation)

    def click(self, digest, at=None):
        self.invites[digest].update(status="clicked", clicked_at=at or invites.now_ms())

    def ops(self, operation):
        return [call for call in self.calls if call[1] == operation]


class Relay:
    """預かり所（state → read key の hash・code）。"""

    def __init__(self):
        self.rows = {}
        self.register_status = 201

    def __call__(self, session, *, register=False, timeout=10):
        if register:
            self.rows[session["state"]] = {"read_key_hash": hashlib.sha256(session["read_key"].encode()).hexdigest()}
            return self.register_status, {}
        row = self.rows.get(session["state"])
        assert row and row["read_key_hash"] == hashlib.sha256(session["read_key"].encode()).hexdigest()
        if "status" in row:
            return row["status"], {}
        if "code" not in row:
            return 404, {}
        return 200, {"code": row.pop("code"), "received_at": row.pop("received_at")}

    def deliver(self, state, code="auth-code-" + "x" * 20):
        from thth import jst
        self.rows[state].update(code=code, received_at=jst.iso())


class Threads:
    """Threads の token 交換と /me（利用者は差し替えられる）。"""

    def __init__(self, monkeypatch, username="reviewer.one", user_id="17841400000000001"):
        self.username, self.user_id, self.codes = username, user_id, []
        monkeypatch.setattr(oauth, "exchange_short_lived_token", self.short)
        monkeypatch.setattr(oauth, "exchange_long_lived_token", lambda secret, token: {"access_token": "long-" + token, "expires_in": 5184000})
        monkeypatch.setattr(oauth, "fetch_me", lambda token: {"id": self.user_id, "username": self.username})
        monkeypatch.setattr(oauth, "fetch_token_scopes", lambda token: list(invites.invite_scopes()))

    def short(self, app_id, app_secret, redirect_uri, code):
        assert redirect_uri == invites.INVITE_REDIRECT_URI
        self.codes.append(code)
        return {"access_token": "short-token-" + "y" * 20}


@pytest.fixture
def world(_env, monkeypatch):
    root, tty, _ = _env
    worker, relay = Worker(), Relay()
    monkeypatch.setattr(vm_relay, "signed_request", worker)
    monkeypatch.setattr(authflow, "relay_request", relay)
    monkeypatch.setattr(appenv, "load_app_env", lambda *a, **k: ("1234567890", "app-secret-" + "z" * 20))
    monkeypatch.setattr(invites, "OPEN_POLL_SECONDS", 0)
    invites._next_poll.clear()
    threads = Threads(monkeypatch)
    return root, tty, worker, relay, threads


def make(tty, **overrides):
    values = dict(media="threads", project="meta-review", label=None, expires="30d", production=True, by="masaru")
    values.update(overrides)
    row = invites.create(**values)
    code = tty.codes()[-1]
    return row, hashlib.sha256(code.encode()).hexdigest()


def authorize_params(worker, digest):
    url = worker.invites[digest]["authorize_url"]
    parsed = urllib.parse.urlsplit(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "threads.net", "/oauth/authorize")
    return dict(urllib.parse.parse_qsl(parsed.query))


def test_押されていない招待は拾わない_押されたらVMでsessionを作り認可URLをWorkerへ(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    invites.run_once()
    assert worker.ops("authorize") == [] and relay.rows == {}
    assert invites.STORE.get(row["invite_id"])["status"] == "open"
    worker.click(digest)
    invites.run_once()
    params = authorize_params(worker, digest)
    assert params["redirect_uri"] == "https://thth.me/callback/" and params["response_type"] == "code"
    assert params["scope"].split(",") == invites.invite_scopes()
    assert list(relay.rows) == [params["state"]]
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "authorizing"
    # session は手元の 0600 のファイルにだけ（一覧・記録には出ない）。
    session_path = root / "state" / "_invites" / (row["invite_id"] + ".session.json")
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    assert json.loads(session_path.read_text())["state"] == params["state"]
    assert params["state"] not in json.dumps(invites.list_invites())
    # Worker に渡した期限は 10 分。
    (call,) = worker.ops("authorize")
    assert 0 < call[2]["expires_at"] - invites.now_ms() <= authflow.TTL * 1000


def test_預かり所に登録できなければ認可URLを出さずopenに戻す(world):
    root, tty, worker, relay, threads = world
    relay.register_status = 503
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    assert worker.ops("authorize") == [] and worker.invites[digest]["status"] == "open"
    assert worker.invites[digest]["reason"] == "unavailable"
    assert not (root / "state" / "_invites" / (row["invite_id"] + ".session.json")).exists()


def complete_flow(world, **overrides):
    root, tty, worker, relay, threads = world
    row, digest = make(tty, **overrides)
    worker.click(digest)
    invites.run_once()
    relay.deliver(authorize_params(worker, digest)["state"])
    invites.run_once()
    return row, digest


def test_認可が済むと口座ができる_handleは認可した人から_tokenは0600(world):
    root, tty, worker, relay, threads = world
    row, digest = complete_flow(world)
    record = invites.STORE.get(row["invite_id"])
    name = record["account"]
    assert record["status"] == "used" and record["remote_ready"] is True and record["handle"] == "reviewer.one"
    cfg = accounts.load_account(name)
    assert cfg["handle"] == "reviewer.one" and cfg["user_id"] == "17841400000000001"
    assert cfg["project"] == "meta-review" and cfg["media"] == "threads" and cfg["production"] is True
    assert cfg["scheduled"] is False and cfg["invite_id"] == row["invite_id"]
    assert cfg["repo_dir"] == str(root / "repos" / "_server" / name)
    assert cfg["token"] == str(root / "secrets" / (name + ".token"))
    assert stat.S_IMODE(os.stat(cfg["token"]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(root / "secrets").st_mode) == 0o700
    token = accounts.load_token(cfg)
    assert token["username"] == "reviewer.one" and token["auth_via"] == "relay"
    assert token["scopes"] == invites.invite_scopes()
    # Worker へ: person は口座名（承認ページの人）。secret はまだどこにも無い。
    assert worker.invites[digest]["status"] == "ready"
    assert worker.ops("complete")[-1][2] == {"person": name, "account": name, "handle": "reviewer.one"}
    events = [(r["event"], r["account"]) for r in admin_log.read()[0]]
    assert events == [("invite_created", "invite-" + row["invite_id"]), ("account_added", name),
                      ("production_enabled", name), ("token_set", name), ("invite_used", "invite-" + row["invite_id"])]
    added = admin_log.read(event="account_added")[0][0]
    assert added["diff"]["via_invite"] == [None, row["invite_id"]] and added["diff"]["token"] == ["absent", "present"]
    assert "long-short" not in json.dumps(admin_log.read()[0])
    assert not (root / "state" / "_invites" / (row["invite_id"] + ".session.json")).exists()


def test_productionを付けない招待の口座は試し撃ち(world):
    row, digest = complete_flow(world, production=False)
    cfg = accounts.load_account(invites.STORE.get(row["invite_id"])["account"])
    assert cfg["production"] is False
    assert admin_log.read(event="production_enabled")[0] == []


def test_同じThreads口座で2回目の招待は断る_口座は作らない(world):
    root, tty, worker, relay, threads = world
    first, _ = complete_flow(world)
    second, digest = complete_flow(world)
    record = invites.STORE.get(second["invite_id"])
    assert record["status"] == "open" and record["reason"] == "account_exists"
    assert worker.invites[digest]["status"] == "open" and worker.invites[digest]["reason"] == "account_exists"
    assert not os.path.exists(os.path.join(os.environ["THTH_ACCOUNTS_DIR"], record["account"] + ".json"))
    # 別の Threads 口座なら、同じ招待をもう一度押して通る。
    threads.username, threads.user_id = "reviewer.two", "17841400000000002"
    worker.click(digest)
    invites.run_once()
    relay.deliver(authorize_params(worker, digest)["state"])
    invites.run_once()
    assert invites.STORE.get(second["invite_id"])["status"] == "used"


def test_招待の口座は持ち主の組に入れない_用意する前に組が付いたら断る(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    with plaza.STORE.locked() as directory:
        plaza.STORE.write(directory, {"schema_version": plaza.SCHEMA_VERSION,
                                      "owners": {"masaru": {"projects": ["meta-review"], "at": "2026-09-25T10:00:00+09:00",
                                                            "by": "masaru"}}}, name=plaza.OWNERS_FILE)
    relay.deliver(authorize_params(worker, digest)["state"])
    invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "open" and record["reason"] == "unavailable"
    assert not os.path.exists(os.path.join(os.environ["THTH_ACCOUNTS_DIR"], record["account"] + ".json"))


def test_用意した口座のprojectは持ち主の組に入らない(world):
    row, digest = complete_flow(world)
    assert plaza.owner_of("meta-review") is None and plaza.owner_groups() == {}


def test_認可の失敗と預かり所の拒否はopenに戻す(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    relay.rows[authorize_params(worker, digest)["state"]]["status"] = 401
    invites.run_once()
    assert worker.invites[digest]["reason"] == "auth_failed"
    assert invites.STORE.get(row["invite_id"])["status"] == "open"


def test_交換が断られたら口座は作らない(world, monkeypatch):
    root, tty, worker, relay, threads = world
    def refused(*a, **k):
        raise oauth.OAuthError("exchange refused")
    monkeypatch.setattr(oauth, "exchange_short_lived_token", refused)
    row, digest = complete_flow(world)
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "open" and record["reason"] == "auth_failed"
    assert accounts.list_account_names() == []


def test_変更ログに書けなければ台帳もtokenも残さない(world, monkeypatch):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    relay.deliver(authorize_params(worker, digest)["state"])
    def emit(fd, data):
        raise admin_log.AdminLogError("fault")
    monkeypatch.setattr(admin_log, "_emit", emit)
    invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "open" and record["reason"] == "unavailable"
    assert accounts.list_account_names() == []
    assert not (root / "secrets" / (record["account"] + ".token")).exists()


def test_Workerに届かなければ次の巡で用意ができたと送り直す(world):
    root, tty, worker, relay, threads = world
    worker.fail["complete"] = None
    row, digest = complete_flow(world)
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "used" and record["remote_ready"] is False
    del worker.fail["complete"]
    invites.run_once()
    assert invites.STORE.get(row["invite_id"])["remote_ready"] is True
    assert worker.invites[digest]["status"] == "ready"


def test_本人がsecretを表示したら承認者の設定だけを記録する(world):
    root, tty, worker, relay, threads = world
    row, digest = complete_flow(world)
    invites.run_once()
    assert admin_log.read(event="approver_set")[0] == []
    worker.invites[digest]["status"] = "done"
    invites.run_once()
    (event,) = admin_log.read(event="approver_set")[0]
    name = invites.STORE.get(row["invite_id"])["account"]
    assert event["account"] == name and event["via"] == "http"
    assert event["diff"] == {"credential_present": [None, True], "via_invite": [None, row["invite_id"]]}
    assert invites.STORE.get(row["invite_id"])["approver_set"] is True
    calls = len(worker.calls)
    invites.run_once()
    assert len(worker.calls) == calls  # 見届けた招待はもう問い合わせない


def test_常駐のapproval_workerが招待も回す(world, monkeypatch):
    root, tty, worker, relay, threads = world
    from thth import report_http
    monkeypatch.setattr(report_http, "load_credentials", lambda path: (str(root), []))
    row, digest = make(tty)
    worker.click(digest)
    worker_mod.run_once(str(root / "credentials.json"))
    assert invites.STORE.get(row["invite_id"])["status"] == "authorizing"
