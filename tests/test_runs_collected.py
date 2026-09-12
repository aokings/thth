"""投稿の実行記録は収集を測っていない（外部レビュー C・2026-09-12）。

`collected: 0` が固定で入っていたので、**「見に行って 0 件だった」と読めた。**
収集は投稿処理の**あと**に走る。**測っていないものを 0 と書かない。**
"""
from __future__ import annotations

import datetime

from thth import core, runs as runs_mod


def test_投稿の実行記録は収集件数を0と書かない(isolated_account, monkeypatch):
    書いた = []
    monkeypatch.setattr(runs_mod, "append_run",
                         lambda state_dir, record, month: 書いた.append(record))
    core._append_run("/tmp/どこでもよい", isolated_account["name"],
                      "run-1", "rehearsal", "post", "a.md", None,
                      datetime.datetime(2026, 9, 12, 10, 0, 0),
                      status="ok", error=None)
    assert 書いた, "実行記録が書かれていない"
    assert 書いた[0]["collected"] is None, \
        "**測っていないものを 0 と書いている**（見に行って 0 件だった、と読める）"
