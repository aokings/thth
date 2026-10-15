"""3.22.0・点検 §B-20: 招待を Mastodon にも。instance は運営者が招待を作るときに決める。

見るのは:
  - `--media mastodon` は `--instance`（https の origin）が要る。Threads に `--instance` は付けない。
    Bluesky はまだ招待しない（App Password をページで受けないため）。
  - 押されたら VM で instance の client を（無ければ）登録し、S256 の認可 URL を Worker へ。
    verifier は VM の session にだけ置く。
  - code が届いたら VM で token に換え、口座（media mastodon・instance・handle は認可した人の acct）を
    用意する。同じ instance の同じ人には 2 つ目を作らない。
Worker・預かり所・Mastodon は偽物。外への通信はしない。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.parse

import pytest

from thth import accounts, admin_log, invites
from thth.adapters import auth_mastodon as masto
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, Worker, Relay  # noqa: F401

INSTANCE = "https://mastodon.example"


class Mastodon:
    def __init__(self, monkeypatch):
        self.calls, self.me = [], {"id": "109000000000000001", "acct": "someone"}
        self.forms = []
        monkeypatch.setattr(masto, "request", self)

    def __call__(self, base, path, *, data=None, token=None):
        assert base == INSTANCE
        self.calls.append(path)
        if path == "/.well-known/oauth-authorization-server":
            return {"issuer": base + "/", **{k: base + p for k, p in masto.ENDPOINTS.items()},
                    "code_challenge_methods_supported": ["S256"], "grant_types_supported": ["authorization_code"],
                    "response_types_supported": ["code"], "scopes_supported": list(masto.SCOPES),
                    "token_endpoint_auth_methods_supported": ["client_secret_post"]}
        if path == "/api/v1/apps":
            return {"client_id": "client-" + "c" * 20, "client_secret": "secret-" + "s" * 20,
                    "redirect_uris": [masto.CALLBACK], "client_secret_expires_at": 0}
        if path == "/oauth/token":
            self.forms.append(dict(data))
            return {"access_token": "token-" + "t" * 20, "token_type": "Bearer", "scope": " ".join(masto.SCOPES)}
        if path == "/api/v1/accounts/verify_credentials":
            return dict(self.me)
        raise AssertionError(path)


@pytest.fixture
def mworld(world, monkeypatch):
    return (*world, Mastodon(monkeypatch))


def make(tty, **overrides):
    values = dict(media="mastodon", instance=INSTANCE, project="outside-one", label=None, expires="30d",
                  production=False, by="masaru")
    values.update(overrides)
    row = invites.create(**values)
    return row, hashlib.sha256(tty.codes()[-1].encode()).hexdigest()


def params(worker, digest):
    parsed = urllib.parse.urlsplit(worker.invites[digest]["authorize_url"])
    assert parsed.scheme + "://" + parsed.netloc == INSTANCE and parsed.path == "/oauth/authorize"
    return dict(urllib.parse.parse_qsl(parsed.query))


@pytest.mark.parametrize("overrides,reason", [
    ({"instance": None}, "invalid_instance"),
    ({"instance": "http://mastodon.example"}, "invalid_instance"),
    ({"instance": "https://mastodon.example/path"}, "invalid_instance"),
    ({"media": "threads"}, "invalid_instance"),
    ({"media": "bluesky", "instance": None}, "invite_media_unsupported"),
])
def test_instanceの検査とBlueskyは招待しない(mworld, overrides, reason):
    root, tty, worker, relay, threads, mastodon = mworld
    with pytest.raises(invites.InviteError, match=reason):
        make(tty, **overrides)
    assert worker.calls == [] and invites.list_invites()[0] == []


def test_Workerにはinstanceとmastodonの権限を送る(mworld):
    root, tty, worker, relay, threads, mastodon = mworld
    row, digest = make(tty)
    assert row["media"] == "mastodon" and row["instance"] == INSTANCE
    (create,) = [c for c in worker.calls if c[1] == "create"]
    assert create[2]["instance"] == INSTANCE and create[2]["scopes"] == list(masto.SCOPES)
    assert mastodon.calls == []   # 押されるまで instance に触らない


def test_押されたらclientを登録してS256の認可URL_codeでmastodonの口座ができる(mworld):
    root, tty, worker, relay, threads, mastodon = mworld
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    q = params(worker, digest)
    assert q["redirect_uri"] == masto.CALLBACK and q["code_challenge_method"] == "S256"
    assert mastodon.calls == ["/.well-known/oauth-authorization-server", "/api/v1/apps"]
    session = json.loads((root / "state" / "_invites" / (row["invite_id"] + ".session.json")).read_text())
    verifier = session["code_verifier"]
    assert q["code_challenge"] == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert verifier not in json.dumps(worker.calls)
    relay.deliver(q["state"])
    invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "used" and record["handle"] == "someone"
    assert mastodon.forms[-1]["code_verifier"] == verifier
    cfg = accounts.load_account(record["account"])
    assert cfg["media"] == "mastodon" and cfg["instance"] == INSTANCE and cfg["handle"] == "someone"
    assert cfg["scheduled"] is False and cfg["production"] is False and "redirect_uri" not in cfg
    token = accounts.load_token(cfg)
    assert token["username"] == "someone" and token["user_id"] == "109000000000000001"
    assert token["auth_via"] == "relay"
    assert worker.ops("complete")[-1][2]["handle"] == "someone"
    assert admin_log.read(event="invite_used")[0][-1]["diff"] == {"account": [None, record["account"]]}


def test_同じinstanceの同じ人には2つ目を作らない(mworld):
    root, tty, worker, relay, threads, mastodon = mworld
    for _ in range(2):
        row, digest = make(tty)
        worker.click(digest)
        invites.run_once()
        relay.deliver(params(worker, digest)["state"])
        invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "open" and record["reason"] == "account_exists"
    assert not os.path.exists(os.path.join(os.environ["THTH_ACCOUNTS_DIR"], record["account"] + ".json"))


def test_acctの形が違えば口座を作らない(mworld):
    root, tty, worker, relay, threads, mastodon = mworld
    mastodon.me["acct"] = "someone@elsewhere.example"
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    relay.deliver(params(worker, digest)["state"])
    invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "open" and record["reason"] == "auth_failed"
    assert accounts.list_account_names() == []


def test_Threadsの招待は今までどおり(mworld):
    root, tty, worker, relay, threads, mastodon = mworld
    row = invites.create(media="threads", project="meta-review", by="masaru")
    assert row["media"] == "threads" and row["instance"] is None
    (create,) = [c for c in worker.calls if c[1] == "create"]
    assert "instance" not in create[2]
