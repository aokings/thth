"""3.14.3 穴 2: 招待の完了の直後に、その人の要約を Worker へすぐ押し上げる。

- 口座を作った直後と、完了ページで口座の secret ができた（Worker の status が done）直後に 1 回ずつ。
- Worker がまだその人を知らない（409）ときも 5 分待ちに入れない（次の巡は 10 秒後）。
- 資格情報のファイルを渡されていない常駐では押さない。
"""
from __future__ import annotations

import time

from thth import activity, invites, relay as vm_relay
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, make, authorize_params, complete_flow  # noqa: F401
from tests.test_v3100_invite_credential import credentials, run_flow  # noqa: F401


def _spy(monkeypatch, worker, status=None):
    synced = []

    def signed(kind, subject, operation, body):
        if kind == "activity":
            synced.append((subject, [row["account"] for row in body["accounts"]]))
            if status is not None:
                raise vm_relay.RelayError("relay_rejected", status=status)
            return {"status": "synced", "actions": [], "requests": []}
        return worker(kind, subject, operation, body)
    monkeypatch.setattr(vm_relay, "signed_request", signed)
    return synced


def test_口座を作った直後とsecretができた直後に押し上げる(world, credentials, monkeypatch):
    activity._next_sync.clear()
    worker = world[2]
    synced = _spy(monkeypatch, worker)
    record, digest = run_flow(world, credentials[0])
    name = record["account"]
    assert synced == [(name, [name])]
    worker.invites[digest]["status"] = "done"
    invites._next_poll.clear()
    invites.run_once(str(credentials[0]))
    assert synced == [(name, [name])] * 2
    assert activity._next_sync[name] <= time.monotonic() + activity.SYNC_SECONDS


def test_Workerがまだ知らなくても5分待ちに入れない(world, credentials, monkeypatch):
    activity._next_sync.clear()
    synced = _spy(monkeypatch, world[2], status=409)
    record, _ = run_flow(world, credentials[0])
    assert synced and activity._next_sync[record["account"]] <= time.monotonic() + activity.SYNC_SECONDS


def test_資格情報を渡されていなければ押さない(world, monkeypatch):
    synced = _spy(monkeypatch, world[2])
    complete_flow(world)
    assert synced == []
