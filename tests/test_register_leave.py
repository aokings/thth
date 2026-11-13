"""`thth leave` で台帳の控えと認めた記録も消える（計画 実店舗 §2 の 6・段 1）。

その project の**最後の口座**が抜けるときだけ消す。台帳のフォルダ（持ち主の物）は消さない。網には出ない。
"""
from __future__ import annotations

import json
import pathlib

from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from thth import admin_log, leave
from thth.register import ACTION_PREFIX, ledger


def _register(name, project, tmp):
    folder = pathlib.Path(tmp) / f"folder-{name}"
    folder.mkdir()
    (folder / "行.md").write_text("---\nid: a\nkind: voice\nstatus: 本採用\nsource: owner\nconfirmed: 推測\n"
                                  "review_by: 2099-01-01\npublic: true\n---\n本文\n", encoding="utf-8")
    ledger.write_new(name, ledger.build(name, project=project, path=str(folder), kind="store"))
    ledger.write_confirmed(name, {"a": {"digest": "x", "at": "t", "by": "m"}})
    return folder, [pathlib.Path(ledger.state_dir_for(name)), pathlib.Path(ledger.path_for(name))]


def test_柵_最後の口座の退出で台帳の控えと記録が消え台帳のフォルダは残る(env, worker, tmp_path):
    folder, gone = _register("alpha-store", "alpha", tmp_path)
    other_folder, kept = _register("beta-store", "beta", tmp_path)
    assert all(p.exists() for p in gone + kept)
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed"
    assert not any(p.exists() for p in gone)
    assert all(p.exists() for p in kept)
    assert (folder / "行.md").exists() and (other_folder / "行.md").exists()
    events, _ = admin_log.read(account="alpha-store", event=ACTION_PREFIX + "removed")
    assert len(events) == 1 and events[0]["by"] == "operator"
    assert leave.run("alpha", by="operator") == result


def test_同じprojectの口座が残るなら台帳の控えは消さない(env, worker, tmp_path):
    path = env["root"] / "accounts" / "beta.json"
    other = json.loads(path.read_text())
    other["project"] = "alpha"
    path.write_text(json.dumps(other))
    _folder, files = _register("alpha-store", "alpha", tmp_path)
    assert leave.run("alpha", by="operator")["phase"] == "completed"
    assert all(p.exists() for p in files)
