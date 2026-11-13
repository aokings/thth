"""`thth leave` で実店舗のレジ（Square・スマレジ）の台帳・写し・鍵も消える（計画 実店舗 段 5 の柵・§2 の 6）。

その project の最後の口座が抜けるときだけ消す（`tests/test_store_leave.py` と同じ筋・種類が増えても同じ道を通る）。網には出ない。
"""
from __future__ import annotations

import pathlib

from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from thth import admin_log, leave
from thth.shop import ACTION_PREFIX, ledger, pos


def _store(name, project, kind):
    extra = {"public_url": "https://n.example.invalid", "publish": pos.empty_publish()}
    if kind == "square":
        extra["location"] = "L8ZQK0X5Y2W1"
        token = {"auth": "bearer", "access_token": "EAAA_synthetic_" + name}
    else:
        extra.update(contract="nigamic01", location="1")
        token = {"auth": "smaregi_client", "client_id": "cid_" + name, "client_secret": "sec_synthetic_" + name}
    ledger.write_new(name, ledger.build(name, kind=kind, project=project, **extra))
    cfg = ledger.load(name)
    ledger.write_token(cfg, {**token, "obtained_at": ledger.now_iso(), "shop_id": name, "expires_on": None,
                             "no_expiry": True})
    ledger.write_snapshot(name, shop_id=name, items=[], read_at=ledger.now_iso(), hours=[])
    return [pathlib.Path(cfg["token"]), pathlib.Path(ledger.state_dir_for(name)), pathlib.Path(ledger.path_for(name))]


def test_柵_最後の口座の退出でレジの台帳と写しと鍵が消える(env, worker):
    gone = _store("alpha-square", "alpha", "square") + _store("alpha-smaregi", "alpha", "smaregi")
    kept = _store("beta-square", "beta", "square")
    assert all(p.exists() for p in gone + kept)
    assert leave.run("alpha", by="operator")["phase"] == "completed"
    assert not any(p.exists() for p in gone)
    assert all(p.exists() for p in kept)
    for name in ("alpha-square", "alpha-smaregi"):
        events, _ = admin_log.read(account=name, event=ACTION_PREFIX + "removed")
        assert len(events) == 1 and events[0]["diff"]["deleted"] == [[], ["token", "state", "ledger"]]
