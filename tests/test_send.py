"""`thth send`（同席の様態・設計 §3.7）。queue を通さずその場で 1 本出す経路。

不在の様態（timer→throw）との違いは queue と承認だけで、**守りは同じものを通る**
ことを確かめる: fail-closed（台帳 production: true が無ければ dry-run）・ロック・
inflight・切り詰めない。
"""
from __future__ import annotations

import os

from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod


def test_送るのは渡された本文そのもの_整形も切り詰めもしない(isolated_account_factory):
    account = isolated_account_factory(production=True)
    sent = {}

    class _Spy:
        def publish(self, post, *, dry_run, on_container_created=None):
            sent["text"] = post.text
            sent["topic"] = post.topic
            from thth.adapters import base
            return base.PublishResult(post_id="P1", url=None, ts="2026-09-09T00:00:00+09:00")

    body = "  前後に空白のある本文。\n"
    r = core.send_once(account["name"], text=body, topic="#苦味",
                       production_flag=True, adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 0 and r.post_id == "P1"
    # strip だけはする（末尾改行を数に含めない規約・§4.1）が、中身は変えない。
    assert sent["text"] == "前後に空白のある本文。"
    assert sent["topic"] == "苦味"   # 先頭の # は落ちる


def test_台帳がproductionでなければ本文を出さない(isolated_account_factory):
    account = isolated_account_factory(production=False)
    called = {"n": 0}

    class _Spy:
        def publish(self, *a, **k):
            called["n"] += 1
            raise AssertionError("dry-run なのに publish を呼んだ")

    lines = []
    r = core.send_once(account["name"], text="出てはいけない本文",
                       production_flag=True, adapter_factory=lambda *_: _Spy(),
                       log=lines.append)
    assert r.exit_code == 0 and r.action == "skip"
    assert called["n"] == 0
    assert "mode: rehearsal" in lines[0]


def test_長すぎる本文は切り詰めずに落とす(isolated_account_factory):
    account = isolated_account_factory(production=True)

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("長すぎるのに publish を呼んだ")

    r = core.send_once(account["name"], text="あ" * 501,
                       production_flag=True, adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 1 and r.action == "skip"


def test_inflightが残っていれば何もしない(isolated_account_factory):
    account = isolated_account_factory(production=True)
    state_dir = accounts_mod.state_dir_for(account["name"])
    inflight_mod.write(state_dir, file="(前回の残骸)", started="2026-09-09T00:00:00+09:00",
                       container_id=None)

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("inflight が残っているのに publish を呼んだ")

    r = core.send_once(account["name"], text="本文", production_flag=True,
                       adapter_factory=lambda *_: _Spy())
    assert r.exit_code == 1 and r.action == "inflight"
