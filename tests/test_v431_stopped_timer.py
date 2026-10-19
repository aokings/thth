"""4.3.1: 止めた口座では timer も返信を出さない（masaru 10/20「止めた口座では timer も返信を出さないように直して」）。

4.3.0 までは承認の場の送り（`send_due_replies`）だけが止めの印（`guard.stopped`）を見て、timer の道
（`throw_once`）は見なかった。投稿は今までどおり止めの印を見ない（変えるのは返信だけ）。
"""
from __future__ import annotations

import pytest

from tests.test_v430_reply_batch import NOW, _Media, _place, _status, _tick
from thth import core, guard


@pytest.fixture
def account(isolated_account_factory):
    return isolated_account_factory(production=True, min_interval_hours=0)


def test_止めた口座ではtimerは返信を出さず投稿は出す(account):
    replies = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i) for i in range(3)]
    post = _place(account, "p.md", minutes_ago=40)
    guard.stop(account["name"], "owner", now=NOW)
    media = _Media()
    result, lines = _tick(account, media)
    assert result.exit_code == 0
    assert [c[1] for c in media.calls] == [None]                 # 投稿の 1 本だけ
    assert _status(post) == "posted"
    assert all(_status(p) == "approved" for p in replies)       # 返信は queue に残る
    line = next(line for line in lines if "返信を 0 本出しました" in line)
    assert "3 本は口座が止まっているので出していません" in line
    assert "口座は止めていません" not in line


def test_返信だけが残っていても止めた口座では出さず理由を言う(account):
    reply = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    guard.stop(account["name"], "owner", now=NOW)
    media = _Media()
    result, lines = _tick(account, media)
    assert result.exit_code == 0 and result.action == "none" and media.calls == []
    assert _status(reply) == "approved"
    assert "1 本は口座が止まっているので出していません" in result.message


def test_戻したら次のtimerで返信を出す(account):
    reply = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    guard.stop(account["name"], "owner", now=NOW)
    media = _Media()
    _tick(account, media)
    assert media.calls == []
    guard.resume(account["name"], by="masaru")
    _tick(account, media, minutes=1)
    assert [c[1] for c in media.calls] == ["17990"] and _status(reply) == "posted"


def test_窓の無い口座でも止めたら返信を出さない(account, monkeypatch):
    """返信の窓が作れない口座は返信を投稿と同じに数える（4.2 までの振る舞い）。そこでも返信は外す。"""
    monkeypatch.setattr(core, "_reply_window_for", lambda *a, **k: None)
    reply = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    guard.stop(account["name"], "owner", now=NOW)
    media = _Media()
    _tick(account, media)
    assert media.calls == [] and _status(reply) == "approved"


def test_止めの印を置けない置き場では印を読まず返信を出す(account, monkeypatch):
    """運営者の CLI と同じ（`guard.check(cli=True)`）: 印を置けない置き場には印が無い。"""
    reply = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    monkeypatch.setattr(guard, "stop_place_usable", lambda account: False)
    monkeypatch.setattr(guard, "stopped", lambda account: pytest.fail("印を読んだ"))
    media = _Media()
    _tick(account, media)
    assert [c[1] for c in media.calls] == ["17990"] and _status(reply) == "posted"
