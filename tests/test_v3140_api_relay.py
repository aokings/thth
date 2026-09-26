"""3.14.0 段 2: 遠くの道（thth.me/api/v1）の VM 側——鍵の表の押し上げ・依頼の受け渡し・2 秒の刻み。

見るのは:
  - sync に、その人の**有効な鍵の表**（hash・口座・期限）が載る。取り消し・期限切れ・他人の鍵は載らない。
    bearer の値はどこにも出ない。発行し直し・取り消しのあとの sync で揃う。
  - sync の応答の依頼を、その鍵の資格で行い（`server_writes.serve`・via api）、結果を次の sync で返す。
    記録（sent・変更ログ）に via: api と鍵の id が残る。
  - 知らない鍵・他人の鍵・期限切れ・取り消した鍵は行わずに断る（権威は VM）。知らない operation は
    `unsupported_operation`。本文の account と依頼の口座が違えば `invalid_request`。
  - 同じ依頼は二度行わない（sync が落ちても結果を送り直すだけ・途中で落ちたものは outcome_unknown）。
    印は state/<口座>/requests/<id>.json（0600・置き場 0700）・本文は残さない。
  - 依頼があれば次の巡を 2 秒後に、無ければ 10 秒後に。
  - 古い Worker（3.13.0）: 3.14.0 の形を 400 で断られたら今の形で送り直す・応答に requests が無ければ何もしない。
  - 招待の完了ページで出した鍵の hash に、招待の口座の資格を差し替える（値は VM に来ない）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import secrets
import stat
import time

import pytest

from thth import activity, admin_log, api_requests, guard, relay, sent, server_writes as writes
from tests.test_v212_server_writes import env, save_config  # noqa: F401
from tests.test_v3120_guard import credential_id, ledger, publisher  # noqa: F401


def key_of(env):
    return hashlib.sha256(env["bearer"].encode()).hexdigest()


class ApiWorker:
    """Worker の sync の偽物: 鍵の表を受け取り、口座ごとの依頼を渡し、結果を受け取る。"""

    def __init__(self, *, legacy=False):
        self.bodies = []
        self.pending = {}
        self.results = {}
        self.legacy = legacy

    def __call__(self, kind, subject, operation, body):
        assert (kind, operation) == ("activity", "sync")
        body = json.loads(json.dumps(body))
        if self.legacy and ("keys" in body or "results" in body):
            raise relay.RelayError("relay_outcome_unknown", status=400)
        self.bodies.append((subject, body))
        if "keys" not in body:
            return {"status": "synced", "actions": []}
        keys = {row["sha256"] for row in body["keys"]}
        for row in body["results"]:
            self.results.setdefault(row["request_id"], []).append(row)
            self.pending.pop(row["request_id"], None)
        handed = [item for item in self.pending.values() if item["key_sha256"] in keys or item.get("force")]
        return {"status": "synced", "actions": [],
                "requests": [{k: v for k, v in item.items() if k != "force"} for item in handed]}

    def ask(self, operation, key_sha256, account="alpha", force=False, **body):
        request_id = secrets.token_urlsafe(32) + "." + account
        self.pending[request_id] = {"request_id": request_id, "account": account, "operation": operation,
                                    "key_sha256": key_sha256, "body": {"account": account, **body}}
        if force:
            self.pending[request_id]["force"] = True
        return request_id

    def result(self, request_id):
        return self.results[request_id][-1]["result"]

    def keys(self, person="person"):
        return [body["keys"] for subject, body in self.bodies if subject == person and "keys" in body][-1]


@pytest.fixture
def worker(monkeypatch):
    fake = ApiWorker()
    monkeypatch.setattr(relay, "signed_request", fake)
    activity._next_sync.clear()
    activity._legacy.clear()
    return fake


def sync(env):
    return activity._sync("person", ["alpha"], env["path"])


# --------------------------------------------------------------------------
# 鍵の表
# --------------------------------------------------------------------------

def test_鍵の表は有効な鍵のhashだけ_取り消し期限切れ他人は載らない(env, worker):
    now = datetime.datetime.now(datetime.timezone.utc)
    other = dict(env["value"]["credentials"][0])
    rows = [dict(other, sha256=hashlib.sha256(name.encode()).hexdigest(), **extra) for name, extra in (
        ("revoked", {"revoked": True}),
        ("expired", {"expires_at": (now - datetime.timedelta(minutes=1)).isoformat()}),
        ("stranger", {"actor": "stranger"}),
        ("beta", {"accounts": {"beta": "beta"}}))]
    env["value"]["credentials"].extend(rows)
    save_config(env)
    sync(env)
    table = worker.keys()
    assert [(row["account"], row["sha256"]) for row in table] == sorted(
        [("alpha", key_of(env)), ("beta", hashlib.sha256(b"beta").hexdigest())])
    assert all(set(row) == {"sha256", "account", "expires_at"} for row in table)
    assert env["bearer"] not in json.dumps(worker.bodies)
    # 取り消したあとの sync で表から外れる。
    env["value"]["credentials"][0]["revoked"] = True
    save_config(env)
    sync(env)
    assert [row["account"] for row in worker.keys()] == ["beta"]


# --------------------------------------------------------------------------
# 依頼の受け渡し
# --------------------------------------------------------------------------

def test_依頼をその鍵の資格で行い_結果を返し_記録にvia_apiと鍵のidが残る(env, worker, publisher):
    request_id = worker.ask("send_request", key_of(env), body="遠くの道の本文")
    completed, pending = sync(env)
    assert completed == [] and pending is True
    result = worker.result(request_id)
    assert result["status"] == "published" and result["post_id"] == "123456" and result["via"] == "api"
    assert publisher == [("publish", "遠くの道の本文")]
    (row,) = sent.records(str(env["root"] / "state/alpha"))
    assert row["via"] == "api" and row["credential"] == credential_id(env)
    (event,) = admin_log.read(event="sent")[0]
    assert event["via"] == "api"
    assert worker.results[request_id][0]["account"] == "alpha" and worker.results[request_id][0]["status"] == "done"
    # 印は 0600・置き場は 0700・本文と bearer は残さない。結果は届いたら手元から落とす。
    directory = env["root"] / "state/alpha/requests"
    (mark,) = directory.glob("*.json")
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700 and stat.S_IMODE(mark.stat().st_mode) == 0o600
    record = json.loads(mark.read_text())
    assert record["status"] == "done" and record["delivered"] is True and record["result"] is None
    assert record["credential"] == credential_id(env)
    assert "遠くの道の本文" not in mark.read_text() and env["bearer"] not in mark.read_text()


def test_読む口と知らないoperation(env, worker):
    listed = worker.ask("draft_list", key_of(env))
    posts = worker.ask("posts", key_of(env))
    shaped = worker.ask("draft_list", key_of(env))
    worker.pending[shaped]["body"]["operation"] = "send_request"
    sync(env)
    assert worker.result(listed) == {"account": "alpha", "drafts": []}
    # 班 A の読む口が入るまでは server_writes の受け付けが断る。
    assert worker.result(posts) == {"error": "unsupported_operation"}
    assert worker.result(shaped) == {"error": "invalid_request"}


def test_鍵が違う_他人の鍵_期限切れ_取り消しは行わずに断る(env, worker, publisher):
    now = datetime.datetime.now(datetime.timezone.utc)
    stranger = hashlib.sha256(b"stranger").hexdigest()
    expired = hashlib.sha256(b"expired").hexdigest()
    base = env["value"]["credentials"][0]
    env["value"]["credentials"].extend([
        dict(base, sha256=stranger, actor="stranger"),
        dict(base, sha256=expired, expires_at=(now - datetime.timedelta(minutes=1)).isoformat())])
    save_config(env)
    unknown = worker.ask("send_request", "0" * 64, force=True, body="出ない")
    other = worker.ask("send_request", stranger, force=True, body="出ない")
    late = worker.ask("send_request", expired, force=True, body="出ない")
    wrong = worker.ask("send_request", key_of(env), account="beta", force=True, body="出ない")
    sync(env)
    assert worker.result(unknown) == {"error": "invalid_key"}
    assert worker.result(other) == {"error": "invalid_key"}
    assert worker.result(late) == {"error": "key_expired"}
    assert worker.result(wrong) == {"error": "invalid_key"}
    env["value"]["credentials"][0]["revoked"] = True
    save_config(env)
    revoked = worker.ask("send_request", key_of(env), force=True, body="出ない")
    sync(env)
    assert worker.result(revoked) == {"error": "invalid_key"}
    assert publisher == []


def test_安全装置の断りは符丁で返る(env, worker, publisher):
    guard.stop("alpha", "owner")
    request_id = worker.ask("send_request", key_of(env), body="止まっている")
    sync(env)
    assert worker.result(request_id) == {"error": "account_stopped", "reason": "owner"}
    assert publisher == []


def test_同じ依頼は二度行わない_落ちたsyncは結果を送り直すだけ(env, worker, publisher, monkeypatch):
    request_id = worker.ask("send_request", key_of(env), body="一度だけ")
    item = dict(worker.pending[request_id])
    real = relay.signed_request
    calls = []

    def flaky(kind, subject, operation, body):
        calls.append(body)
        if body.get("results"):
            raise relay.RelayError("relay_outcome_unknown")
        return real(kind, subject, operation, body)
    monkeypatch.setattr(relay, "signed_request", flaky)
    with pytest.raises(relay.RelayError):
        sync(env)
    assert publisher == [("publish", "一度だけ")] and request_id not in worker.results
    # Worker はまだ待っている依頼として渡す。VM は行わずに、行った結果を送り直す。
    monkeypatch.setattr(relay, "signed_request", real)
    sync(env)
    assert publisher == [("publish", "一度だけ")]
    assert worker.result(request_id)["post_id"] == "123456"
    # 届いたあとにもう一度渡されても（Worker の取り違え）行わない。
    worker.pending[request_id] = item
    sync(env)
    assert publisher == [("publish", "一度だけ")]
    assert worker.result(request_id) == {"error": "outcome_unknown"}


def test_途中で落ちた依頼はoutcome_unknown(env, worker, publisher):
    request_id = worker.ask("send_request", key_of(env), body="途中で落ちた")
    directory = env["root"] / "state/alpha/requests"
    directory.mkdir(mode=0o700, parents=True)
    mark = directory / (request_id.split(".")[0] + ".json")
    mark.write_text(json.dumps({"request_id": request_id, "account": "alpha", "status": "running",
                                "received_at": time.time(), "delivered": False, "result": None}))
    mark.chmod(0o600)
    sync(env)
    assert worker.result(request_id) == {"error": "outcome_unknown"} and publisher == []


def test_古い印は消え_受けられなくなった結果は送り直さない(env, worker):
    directory = env["root"] / "state/alpha/requests"
    directory.mkdir(mode=0o700, parents=True)
    for age, name in ((200, "late"), (api_requests.KEEP_SECONDS + 1, "old")):
        request_id = hashlib.sha256(name.encode()).hexdigest()[:43] + ".alpha"
        path = directory / (request_id.split(".")[0] + ".json")
        path.write_text(json.dumps({"request_id": request_id, "account": "alpha", "status": "done",
                                    "received_at": time.time() - age, "delivered": False, "result": {"ok": True}}))
        path.chmod(0o600)
    sync(env)
    assert all(body["results"] == [] for _, body in worker.bodies)
    (left,) = directory.glob("*.json")
    assert json.loads(left.read_text())["result"] is None


# --------------------------------------------------------------------------
# 刻み
# --------------------------------------------------------------------------

def test_依頼があれば次の巡は2秒後_無ければ10秒後(env, worker):
    activity.run_once(env["path"])
    assert activity._next_sync["person"] - time.monotonic() > activity.BUSY_SECONDS + 1
    assert activity._next_sync[""] - time.monotonic() > activity.BUSY_SECONDS + 1
    worker.ask("draft_list", key_of(env))
    activity._next_sync.clear()
    activity.run_once(env["path"])
    assert activity._next_sync["person"] - time.monotonic() <= activity.BUSY_SECONDS
    assert activity._next_sync[""] - time.monotonic() <= activity.BUSY_SECONDS


# --------------------------------------------------------------------------
# 版ずれ
# --------------------------------------------------------------------------

def test_古いWorkerは3_14の形を断る_今の形で送り直して動きの一覧は止めない(env, monkeypatch):
    old = ApiWorker(legacy=True)
    monkeypatch.setattr(relay, "signed_request", old)
    activity._next_sync.clear()
    activity._legacy.clear()
    assert sync(env) == ([], False)
    assert old.bodies and all(set(body) == {"accounts", "completed"} for _, body in old.bodies)
    # しばらくは古い形で話す（毎回 400 を踏まない）。
    count = len(old.bodies)
    sync(env)
    assert len(old.bodies) == count + 1


def test_古いWorkerの応答にrequestsが無ければ何もしない(env, monkeypatch):
    monkeypatch.setattr(relay, "signed_request", lambda *a: {"status": "synced", "actions": []})
    activity._legacy.clear()
    assert sync(env) == ([], False)
    assert not (env["root"] / "state/alpha/requests").exists()


# --------------------------------------------------------------------------
# 受け付けの口
# --------------------------------------------------------------------------

def test_serveは書く口と読む口を分け_知らない名前は断る(env):
    from thth.report_service import ReportServiceError
    assert writes.serve(env["context"], {"operation": "draft_list", "account": "alpha"}, via="api")["drafts"] == []
    with pytest.raises(ReportServiceError) as caught:
        writes.serve(env["context"], {"operation": "throw", "account": "alpha"}, via="api")
    assert str(caught.value) == "unsupported_operation"
