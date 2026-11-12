"""`thth leave` で店の台帳・写し・鍵も消える（設計 ショップ §7・監査 5.15.0 直したほうがよい 1）。

その project の**最後の口座**が抜けるときだけ消す（ほかの口座が残るなら、店はその口座の下書きの元なので残す）。
網には出ない（店の分は手元のファイルを消すだけ）。
"""
from __future__ import annotations

import json
import pathlib

from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from thth import admin_log, leave
from thth.shop import ACTION_PREFIX, ledger


def _store(name, project):
    ledger.write_new(name, ledger.build(name, kind="base", project=project))
    cfg = ledger.load(name)
    ledger.write_token(cfg, {"access_token": "pat_synthetic_" + name, "expires_on": "2027-01-31",
                             "obtained_at": ledger.now_iso(), "shop_id": name})
    ledger.write_snapshot(name, shop_id=name, items=[], read_at=ledger.now_iso())
    return [pathlib.Path(cfg["token"]), pathlib.Path(ledger.state_dir_for(name)), pathlib.Path(ledger.path_for(name))]


def test_柵_最後の口座の退出で店の台帳と写しと鍵が消える(env, worker):
    gone = _store("alpha-base", "alpha")
    kept = _store("beta-base", "beta")
    assert all(p.exists() for p in gone + kept)
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed"
    assert not any(p.exists() for p in gone)
    assert all(p.exists() for p in kept)
    events, _ = admin_log.read(account="alpha-base", event=ACTION_PREFIX + "removed")
    assert len(events) == 1 and events[0]["by"] == "operator"
    assert events[0]["diff"]["deleted"] == [[], ["token", "state", "ledger"]]
    # 何度呼んでも同じ（退出の再試行）。
    assert leave.run("alpha", by="operator") == result


def test_同じprojectの口座が残るなら店は消さない(env, worker):
    path = env["root"] / "accounts" / "beta.json"
    other = json.loads(path.read_text())
    other["project"] = "alpha"
    path.write_text(json.dumps(other))
    files = _store("alpha-base", "alpha")
    assert leave.run("alpha", by="operator")["phase"] == "completed"
    assert all(p.exists() for p in files)
