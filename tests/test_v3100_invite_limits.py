"""3.10.0 招待リンク: 期限・1 回きり・取り消し・常駐が落ちていたとき。設計 §2。

見るのは:
  - 期限を過ぎた招待は、Worker が押されたと言っても VM が口座を作らない（手元でも期限を見る）。
  - 取り消した招待は、認可の途中でも code を交換しない。
  - 使われた招待はもう session を作らない（1 回きり）。
  - 常駐が止まっていて押されてから 2 分以上経って拾ったら、運用通知を管理者に 1 回だけ。
  - 口座を書いたあとで常駐が止まっても、次の巡で台帳から続ける（2 つ目の交換をしない）。
"""
from __future__ import annotations

import json

import pytest

from thth import accounts, admin_log, incident, invites
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, make, authorize_params, complete_flow  # noqa: F401


def backdate(invite_id, **changes):
    record = invites.STORE.get(invite_id)
    record.update(changes)
    with invites.STORE.locked() as directory:
        invites.STORE.write(directory, record)


def test_期限を過ぎた招待はWorkerが押されたと言っても口座を作らない(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    backdate(row["invite_id"], expires_at=invites.now_ms() - 1)
    invites.run_once()
    assert invites.STORE.get(row["invite_id"])["status"] == "expired"
    assert worker.ops("authorize") == [] and worker.ops("status") == [] and relay.rows == {}


def test_認可の途中で期限が来たらcodeが届いていても交換しない(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    relay.deliver(authorize_params(worker, digest)["state"])
    backdate(row["invite_id"], expires_at=invites.now_ms() - 1)
    invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "expired" and threads.codes == []
    assert accounts.list_account_names() == []
    assert not (root / "state" / "_invites" / (row["invite_id"] + ".session.json")).exists()
    # 期限切れの招待はもう回さない。
    calls = len(worker.calls)
    invites.run_once()
    assert len(worker.calls) == calls


def test_認可の途中で取り消したらcodeを交換しない(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    relay.deliver(authorize_params(worker, digest)["state"])
    invites.revoke(row["invite_id"], by="masaru")
    assert worker.invites[digest]["status"] == "revoked"
    assert not (root / "state" / "_invites" / (row["invite_id"] + ".session.json")).exists()
    invites.run_once()
    assert threads.codes == [] and accounts.list_account_names() == []
    assert invites.STORE.get(row["invite_id"])["status"] == "revoked"


def test_使われた招待はもうsessionを作らない(world):
    root, tty, worker, relay, threads = world
    row, digest = complete_flow(world)
    worker.invites[digest]["status"] = "done"
    invites.run_once()
    # Worker が（偽って）もう一度押されたと言っても、使用済みの招待は認可を始めない。
    worker.click(digest)
    registered = len(relay.rows)
    for _ in range(3):
        invites.run_once()
    assert len(worker.ops("authorize")) == 1 and len(relay.rows) == registered
    assert len(threads.codes) == 1 and len(accounts.list_account_names()) == 1


def test_Workerで消えた招待と取り消された招待は手元も閉じる(world):
    root, tty, worker, relay, threads = world
    gone, gone_digest = make(tty)
    revoked, revoked_digest = make(tty)
    del worker.invites[gone_digest]
    worker.invites[revoked_digest]["status"] = "revoked"
    invites.run_once()
    assert invites.STORE.get(gone["invite_id"])["status"] == "expired"
    assert invites.STORE.get(revoked["invite_id"])["status"] == "revoked"


def test_resetが届かなかったら次の巡で送り直す(world):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.invites[digest].update(status="authorizing", authorize_url="https://threads.net/oauth/authorize?state=x")
    invites.run_once()
    assert worker.ops("reset")[-1][2] == {"reason": "auth_failed"}
    assert worker.invites[digest]["status"] == "open"


@pytest.fixture
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(incident, "readiness", lambda cfg: {"user_configured": False, "admin_configured": True,
                                                           "smtp_configured": True})
    monkeypatch.setattr(incident, "settings", lambda cfg: {"admin": "ops@example.test", "user": None})
    monkeypatch.setattr(incident, "_send", lambda s, recipient, event, account: sent.append((recipient, event, account)) or True)
    return sent


def test_常駐が止まっていて2分以上経って拾ったら運用通知を1回だけ(world, mailbox):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest, at=invites.now_ms() - 180_000)
    invites.run_once()
    assert invites.STORE.get(row["invite_id"])["status"] == "authorizing"  # 遅れても進める
    ((recipient, event, account),) = mailbox
    assert recipient == "ops@example.test" and account == "invite-" + row["invite_id"]
    assert event["state"] == "admin_change" and event["admin_event"]["event"] == "invite_stalled"
    assert event["admin_event"]["waited_seconds"] >= 180 and "thth-approval-worker" in event["admin_event"]["next"]
    code = tty.codes()[-1]
    assert code not in json.dumps(event) and digest not in json.dumps(event)
    # もう一度遅れて押されても、同じ招待で 2 通目は送らない。
    worker.invites[digest].update(status="open")
    invites.STORE.get(row["invite_id"])
    backdate(row["invite_id"], status="open")
    worker.click(digest, at=invites.now_ms() - 300_000)
    invites.run_once()
    assert len(mailbox) == 1


def test_すぐ拾えたら運用通知は出さない(world, mailbox):
    root, tty, worker, relay, threads = world
    row, digest = make(tty)
    worker.click(digest, at=invites.now_ms() - 5_000)
    invites.run_once()
    assert mailbox == [] and invites.STORE.get(row["invite_id"]).get("stall_notified") is None


def test_口座を書いたあとで止まっても次の巡で台帳から続ける(world):
    root, tty, worker, relay, threads = world
    worker.fail["complete"] = None
    row, digest = complete_flow(world)
    del worker.fail["complete"]
    # 記録を used にする前に止まった形に戻す（session のファイルも残っている）。
    backdate(row["invite_id"], status="authorizing", remote_ready=False)
    session = root / "state" / "_invites" / (row["invite_id"] + ".session.json")
    session.write_text(json.dumps({"state": "s" * 43, "read_key": "r" * 43, "created_at": invites.now_ms()}))
    session.chmod(0o600)
    invites.run_once()
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "used" and record["remote_ready"] is True and record["handle"] == "reviewer.one"
    assert len(threads.codes) == 1 and not session.exists()
    assert len(admin_log.read(event="account_added")[0]) == 1
