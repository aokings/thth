"""4.4.0 会話の続く投稿の取り直し（`thth/reply_follow.py`・`collect._collect_follow`）。

kopicha 10/21: 返信が刻み（1・6・24・72・168 時間）の間に付くと、次の刻みまで台帳に入らず、
h2d の起こしが約 2 時間半遅れた。会話の続く投稿だけ、刻みとは別におおむね 20 分おきに
取り直す。性質は `--refresh` と同じ（刻みを進めない・数を取らない）。
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from thth import accounts, collect as c, jst, sent

POSTED = jst.parse("2026-10-20T00:00:00+09:00")
H = dt.timedelta(hours=1)
M = dt.timedelta(minutes=1)


class Adapter:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or {}
        self.fail = fail
        self.insight_calls, self.reply_calls = [], []

    def capabilities(self):
        return set()

    def insights(self, pid):
        self.insight_calls.append(pid)
        return {"metrics": {"views": 1}, "available": ["views"]}

    def conversation(self, pid, **kw):
        self.reply_calls.append((pid, kw))
        if self.fail:
            raise RuntimeError("fixture fail")
        return list(self.rows.get(pid, []))


class XAdapter(Adapter):
    PAID_READS = True
    CONVERSATION_MIN = 10


def _account(factory, monkeypatch, *, media="threads", **overrides):
    a = factory(name="test-" + media, media=media, **overrides)
    monkeypatch.setattr(c.writeback, "upstream_sha", lambda repo: None)
    monkeypatch.setattr(c.core, "list_queue_files", lambda *x, **k: [])
    return a


def _post(a, pid, posted_at, reply_to=None):
    sent.write(accounts.state_dir_for(a["name"]), post_id=pid, text="fixture",
               body_hash="fixture", sent_at=jst.iso(posted_at), reply_to=reply_to)


def _ledger(a, pid):
    cfg = accounts.load_account(a["name"])
    path = os.path.join(accounts.data_dirs(cfg, a["name"])["replies"], f"{pid}.ndjson")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def _run(a, adapter, now):
    return c.collect_once(a["name"], adapter=adapter, now=now, log=lambda _l: None)


def _called(adapter):
    return [pid for pid, _kw in adapter.reply_calls]


def test_会話の続く投稿だけ取り直す(tmp_path, isolated_account_factory, monkeypatch):
    """48 時間以内の投稿は取り直す。古くて返信も無い投稿は取り直さない。"""
    a = _account(isolated_account_factory, monkeypatch)
    now = POSTED + 100 * H
    _post(a, "NEW", now - 3 * H)
    _post(a, "OLD", POSTED)          # 100 時間前・返信なし
    adapter = Adapter()
    _run(a, adapter, now)            # 初回: NEW は 1h の刻み・OLD は 72h の刻み
    adapter.reply_calls.clear()
    _run(a, adapter, now + 30 * M)   # 刻みに当たらない回
    assert _called(adapter) == ["NEW"]
    follow = [r for r in _ledger(a, "NEW") if r["kind"] == "fetch"][-1]
    assert follow["trigger"] == "follow" and follow["marks"] == []


def test_古い投稿でも直近48時間に返信が付けば取り直す(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch)
    now = POSTED + 100 * H
    _post(a, "OLD", POSTED)
    adapter = Adapter(rows={"OLD": [{"id": "R1", "timestamp": jst.iso(now - 5 * H)}]})
    _run(a, adapter, now)            # 72h の刻みで R1 が台帳に入る
    adapter.reply_calls.clear()
    _run(a, adapter, now + 30 * M)
    assert _called(adapter) == ["OLD"]


def test_古い投稿でも自分が返した枝は取り直す(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch)
    now = POSTED + 100 * H
    _post(a, "OLD", POSTED)
    _post(a, "MINE", now - 2 * H, reply_to="OLD")   # 自分の返信（2 時間前）
    adapter = Adapter()
    _run(a, adapter, now)
    adapter.reply_calls.clear()
    _run(a, adapter, now + 30 * M)
    assert sorted(_called(adapter)) == ["MINE", "OLD"]


def test_20分たたない投稿は飛ばす(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch)
    _post(a, "P", POSTED)
    adapter = Adapter()
    _run(a, adapter, POSTED + 2 * H)              # 1h の刻み
    adapter.reply_calls.clear()
    _run(a, adapter, POSTED + 2 * H + 10 * M)     # 10 分後: 飛ばす
    assert _called(adapter) == []
    _run(a, adapter, POSTED + 2 * H + 19 * M)     # 19 分後: まだ飛ばす
    assert _called(adapter) == []
    _run(a, adapter, POSTED + 2 * H + 20 * M)     # 20 分後: 取り直す
    assert _called(adapter) == ["P"]
    _run(a, adapter, POSTED + 2 * H + 30 * M)     # 取り直しから 10 分: 飛ばす
    assert _called(adapter) == ["P"]


def test_間隔と窓と本数は台帳で変えられる(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch,
                 reply_follow={"every_minutes": 60, "live_hours": 2, "max_posts": 1})
    _post(a, "P", POSTED)
    adapter = Adapter()
    _run(a, adapter, POSTED + 1 * H)              # 1h の刻み
    adapter.reply_calls.clear()
    _run(a, adapter, POSTED + 1 * H + 30 * M)     # 60 分たっていない
    assert _called(adapter) == []
    _run(a, adapter, POSTED + 2 * H)              # 60 分・窓（2 時間）の内
    assert _called(adapter) == ["P"]
    _run(a, adapter, POSTED + 4 * H)              # 窓の外・返信なし
    assert _called(adapter) == ["P"]


def test_上限の本数を新しい動きの順に取る(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch)
    now = POSTED + 30 * H
    # 12 本・投稿が新しいほど動きが新しい。P00 がいちばん古い。
    for i in range(12):
        _post(a, f"P{i:02d}", POSTED + i * H)
    adapter = Adapter()
    _run(a, adapter, now)                         # 刻み（24h・6h など）で全部 1 回取る
    adapter.reply_calls.clear()
    _run(a, adapter, now + 30 * M)
    assert len(adapter.reply_calls) == 10
    assert _called(adapter) == [f"P{i:02d}" for i in range(11, 1, -1)]


def test_刻みと数の記録を進めない(tmp_path, isolated_account_factory, monkeypatch):
    """取り直しは insights を読まず、`marks: []` で記録する。6h の刻みはそのまま 6h に取る。"""
    a = _account(isolated_account_factory, monkeypatch)
    _post(a, "P", POSTED)
    adapter = Adapter(rows={"P": [{"id": "R1", "timestamp": jst.iso(POSTED + 2 * H)}]})
    _run(a, adapter, POSTED + 1 * H)              # 1h の刻み（数と返信）
    assert adapter.insight_calls == ["P"]
    _run(a, adapter, POSTED + 3 * H)              # 取り直し
    assert adapter.insight_calls == ["P"], "取り直しが数を読んでいる"
    fetches = [r for r in _ledger(a, "P") if r["kind"] == "fetch"]
    assert [r["marks"] for r in fetches] == [[1], []]
    assert [r["trigger"] for r in fetches] == ["marks", "follow"]
    assert c.due_marks(3.0, fetches, marks=c.REPLY_MARKS_HOURS) == []
    assert [r["id"] for r in _ledger(a, "P") if r["kind"] == "reply"] == ["R1"]
    _run(a, adapter, POSTED + 6 * H)              # 6h の刻みは刻みとして取る
    assert adapter.insight_calls == ["P", "P"]
    fetches = [r for r in _ledger(a, "P") if r["kind"] == "fetch"]
    assert fetches[-1]["marks"] == [6] and fetches[-1]["trigger"] == "marks"
    assert len(fetches) == 3, "刻みで取った回に取り直しでも読んでいる"


def test_Xは既定で外す(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch, media="x")
    _post(a, "123", POSTED)
    adapter = XAdapter()
    for minutes in (60, 90, 120):
        _run(a, adapter, POSTED + minutes * M)
    assert adapter.reply_calls == []


def test_Xは台帳で明示したときだけ最小の本数で読む(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch, media="x",
                 reply_follow={"enabled": True})
    _post(a, "123", POSTED)
    adapter = XAdapter()
    _run(a, adapter, POSTED + 60 * M)
    assert adapter.reply_calls == [("123", {"limit": 10})]


def test_取り直しの失敗で採取を失敗にしない(tmp_path, isolated_account_factory, monkeypatch):
    a = _account(isolated_account_factory, monkeypatch)
    _post(a, "P", POSTED)
    _run(a, Adapter(), POSTED + 1 * H)            # 1h の刻み
    adapter = Adapter(fail=True)
    lines = []
    result = c.collect_once(a["name"], adapter=adapter, now=POSTED + 2 * H, log=lines.append)
    assert _called(adapter) == ["P"]
    assert result["errors"] == [], "取り直しの失敗が採取の errors（終了コード）に入っている"
    assert result["follow"]["failed"] == [{"post_id": "P", "reason": "fixture fail"}]
    assert any("取り直し" in line for line in lines)
    # 失敗した回は記録が残らないので、次の回で取り直す。
    adapter.fail = False
    _run(a, adapter, POSTED + 2 * H + 10 * M)
    assert _called(adapter) == ["P", "P"]


@pytest.mark.parametrize("value", [
    {"every_minutes": 1}, {"live_hours": 0}, {"max_posts": 51}, {"enabled": "yes"},
    {"unknown": 1}, [], {"every_minutes": 20.5}])
def test_形の違う設定は台帳の読み込みで断る(tmp_path, isolated_account_factory, value):
    a = isolated_account_factory(name="test-threads", reply_follow=value)
    with pytest.raises(accounts.AccountError, match="invalid_reply_follow"):
        accounts.load_account(a["name"])


def test_max_posts0は切る():
    assert c.reply_follow_mod.settings({"media": "threads", "reply_follow": {"max_posts": 0}})["enabled"] is False
    assert c.reply_follow_mod.settings({"media": "bluesky"})["enabled"] is True
    assert c.reply_follow_mod.settings({"media": "mastodon"})["enabled"] is True
    assert c.reply_follow_mod.settings({"media": "x"})["enabled"] is False
