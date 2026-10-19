"""4.3.1: 止めた口座では timer は何も出さない（masaru 10/20「止めた口座では timer も返信を出さないように直して」・
予約の投稿も止めることに「それでよいです」）。

4.3.0 までは承認の場の送り（`send_due_replies`）と thth.me・CLI の道だけが止めの印（`guard.stopped`）を見て、
timer の道（`throw_once`）は止めた口座でも予約の投稿と返信を出していた。
"""
from __future__ import annotations

import pytest

from tests.test_v430_reply_batch import NOW, _Media, _place, _status, _tick
from thth import guard


@pytest.fixture
def account(isolated_account_factory):
    return isolated_account_factory(production=True, min_interval_hours=0)


def test_止めた口座ではtimerは返信も予約の投稿も出さない(account):
    replies = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i) for i in range(3)]
    post = _place(account, "p.md", minutes_ago=40)
    guard.stop(account["name"], "owner", now=NOW)
    media = _Media()
    result, lines = _tick(account, media)
    assert result.exit_code == 0 and result.action == "stopped"
    assert media.calls == []
    assert _status(post) == "approved" and all(_status(p) == "approved" for p in replies)   # queue に残る
    assert "止まっているので出しません" in result.message
    assert f"thth account resume {account['name']}" in result.message


def test_戻したら次のtimerで出す(account):
    reply = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    post = _place(account, "p.md", minutes_ago=40)
    guard.stop(account["name"], "owner", now=NOW)
    media = _Media()
    _tick(account, media)
    assert media.calls == []
    guard.resume(account["name"], by="masaru")
    _tick(account, media, minutes=1)
    assert sorted(c[1] or "" for c in media.calls) == ["", "17990"]
    assert _status(reply) == "posted" and _status(post) == "posted"


def test_止めた口座ではdry_runも出さないと言う(account):
    _place(account, "p.md", minutes_ago=40)
    guard.stop(account["name"], "burst", now=NOW)
    from thth import core
    result = core.throw_once(account["name"], production_flag=False, adapter_factory=lambda *_: _Media(), now=NOW)
    assert result.action == "stopped" and result.mode == "rehearsal"


def test_止めの印を置けない置き場では印を読まず出す(account, monkeypatch):
    """運営者の CLI と同じ（`guard.check(cli=True)`）: 印を置けない置き場には印が無い。"""
    reply = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    monkeypatch.setattr(guard, "stop_place_usable", lambda account: False)
    monkeypatch.setattr(guard, "stopped", lambda account: pytest.fail("印を読んだ"))
    media = _Media()
    _tick(account, media)
    assert [c[1] for c in media.calls] == ["17990"] and _status(reply) == "posted"


def test_印が読めないときは止まっている側(account, monkeypatch):
    from thth import server_files
    _place(account, "r.md", reply_to="17990", minutes_ago=5)

    def unreadable(account):
        raise server_files.UnsafeFile("unsafe_server_directory")
    monkeypatch.setattr(guard, "stopped", unreadable)
    media = _Media()
    result, _ = _tick(account, media)
    assert result.action == "stopped" and media.calls == []


def test_止めた口座ではスレッド連投にも入らない(account, monkeypatch):
    from thth import threadthrow
    monkeypatch.setattr(threadthrow, "run_for_account", lambda *a, **k: pytest.fail("スレッド連投に入った"))
    guard.stop(account["name"], "owner", now=NOW)
    result, _ = _tick(account, _Media())
    assert result.action == "stopped"
