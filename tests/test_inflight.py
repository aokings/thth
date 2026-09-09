"""inflight が残っていると `thth throw` は何もせず exit 1。board に `inflight: <file>`
（発注 §5 受け入れ 8）。"""
from __future__ import annotations

import os

from tests.conftest import write_queue_file
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import jst
from thth import report as report_mod


def test_inflightが残っていればthrowは何もせずexit1(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(isolated_account["name"])
    inflight_mod.write(state_dir, file="a.md", started=jst.iso())

    result = core.throw_once(isolated_account["name"])
    assert result.exit_code == 1
    assert result.action == "inflight"
    assert result.file == "a.md"

    # queue ファイルは触られていない。
    path = os.path.join(isolated_account["queue_dir"], "a.md")
    with open(path, encoding="utf-8") as f:
        assert "status: approved" in f.read()


def test_inflightはboardにファイル名で出る(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(isolated_account["name"])
    inflight_mod.write(state_dir, file="a.md", started=jst.iso())

    summary = report_mod.board_summary()
    row = next(r for r in summary["accounts"] if r["account"] == isolated_account["name"])
    assert row["inflight"] == "a.md"
