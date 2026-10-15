"""3.25.0: 返信の burst は口座を止めず、窓が空く時刻の順番待ちに並べる（kopicha・masaru 10/16）。

masaru が「ぜんぶOK」と言った Threads の返信 4 件を `thth send --reply-to` で続けて出したら、3 件目の
あとに burst（10 分に 3 件）で口座 kopicha-threads が止まり、予約まで止まった。

- 返信（reply_to あり）が burst を超える → 口座は止めない。その 1 件を queue に承認済みで置き、
  `publish_at` は「窓の中で最も古い公開・削除 + burst の分 + 1 分」。続く返信はその後ろへ。
- 投稿（返信でないもの）は今までどおり止める。1 日の上限は返信でも並べずに断る。
- timer に載っていない口座（`scheduled: false`）は並べられない → 止めずに `burst_limit`（`next_at`）。
- 手元・VM の CLI の道（`core.send_once`）と thth.me の道（`server_writes`）の両方。
- timer（`throw_once`）は並んだ返信を `publish_at` 以降に出す（最短間隔は掛けない）。
"""
from __future__ import annotations

import datetime
import glob
import os

import pytest

from thth import activity, burst_queue, core, guard, jst, queuefile, report, sent, writeback
from thth import server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_send import _digest_for
from tests.test_send_guard_cli import NOW, _Spy
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_guard import ledger

BURST = {"count": 3, "minutes": 10}


def _send(account, spy, text, *, minute, reply_to=None, second=0):
    now = NOW + datetime.timedelta(minutes=minute, seconds=second)
    return core.send_once(account["name"], text=text, reply_to=reply_to, production_flag=True,
                          confirm=_digest_for(account["name"], text, reply_to=reply_to),
                          adapter_factory=lambda *_: spy, now=now)


def _queued_files(account):
    return sorted(glob.glob(os.path.join(account["queue_dir"], "burst-reply-*.md")))


# --------------------------------------------------------------------------
# 手元・VM の CLI の道
# --------------------------------------------------------------------------

def test_返信4件を続けて出すと3件出て4件目は順番待ち_口座は止まらない(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=1, burst=BURST)
    spy = _Spy()
    for i in range(3):
        assert _send(account, spy, f"お返事{i}", minute=i, reply_to=f"1799{i}").exit_code == 0
    r = _send(account, spy, "お返事3", minute=3, reply_to="17993")
    assert r.exit_code == 0 and r.action == "queued" and r.error is None
    # 窓の中で最も古い公開（17:30）+ 10 分 + 1 分。
    assert r.publish_at == "2026-10-15T17:41:00+09:00"
    assert "burst に当たったので 17:41（JST）に出す順番待ちに並べました。止めていません" in r.message
    assert spy.n == 3
    assert guard.stopped(account["name"]) is None
    (path,) = _queued_files(account)
    fm = queuefile.parse(path).front_matter
    assert fm["status"] == "approved" and fm["reply_to"] == "17993" and fm["queued_for"] == "burst"
    assert fm["approved_via"] == "cli" and fm["approved_sha"] and fm["publish_at"] == r.publish_at
    # commit・push 済み（timer が同期した commit と照合して拾える）。
    synced = writeback.upstream_sha(account["repo_dir"])
    assert synced and writeback.matches_synced_commit(account["repo_dir"], path, tree_sha=synced,
                                                       disk_bytes=open(path, "rb").read())
    # 承認の指紋は今までの計算のまま（`compute_approved_sha`）。
    from thth.cli import _prepare_one
    prepared, problem = _prepare_one(path)
    assert problem is None and prepared["approved_sha"] == fm["approved_sha"]


def test_続く5件目は4件目より後ろに並ぶ(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=0, burst=BURST)
    spy = _Spy()
    for i in range(3):
        assert _send(account, spy, f"お返事{i}", minute=i, reply_to=f"1799{i}").exit_code == 0
    fourth = _send(account, spy, "お返事3", minute=3, reply_to="17993")
    fifth = _send(account, spy, "お返事4", minute=4, reply_to="17994")
    assert fourth.action == fifth.action == "queued"
    assert jst.parse(fifth.publish_at) > jst.parse(fourth.publish_at)
    assert fifth.publish_at == "2026-10-15T17:42:00+09:00"
    # 窓が空いても、先に並んだ返信がまだ先にあれば追い越さない。
    sixth = _send(account, spy, "お返事5", minute=10, second=30, reply_to="17995")
    assert sixth.action == "queued" and jst.parse(sixth.publish_at) > jst.parse(fifth.publish_at)
    assert len(_queued_files(account)) == 3 and spy.n == 3
    assert guard.stopped(account["name"]) is None
    # `thth schedule` の一覧で見分けられる。
    rows = report.schedule(account["name"], now=NOW)
    assert [row["queued_for"] for row in rows] == ["burst"] * 3


def test_返信でない投稿は今までどおり口座を止める(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=0, burst=BURST)
    spy = _Spy()
    for i in range(3):
        assert _send(account, spy, f"お返事{i}", minute=i, reply_to=f"1799{i}").exit_code == 0
    r = _send(account, spy, "ふつうの投稿", minute=3)
    assert r.action == "guard" and r.error == "account_stopped"
    assert guard.stopped(account["name"])["reason"] == "burst"
    assert _queued_files(account) == []


def test_1日の上限は返信でも並べずに断る(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=0, daily_max_posts=3,
                                       burst={"count": 2, "minutes": 10})
    spy = _Spy()
    for i in range(2):
        assert _send(account, spy, f"お返事{i}", minute=i, reply_to=f"1799{i}").exit_code == 0
    # burst に当たり、並べる日の上限（公開 2 + 並べた 0 < 3）は空いている → 並ぶ。
    assert _send(account, spy, "お返事2", minute=2, reply_to="17992").action == "queued"
    # 次は公開 2 + 並べた 1 = 3 で上限 → 並べずに daily_limit。
    r = _send(account, spy, "お返事3", minute=3, reply_to="17993")
    assert r.action == "guard" and r.error == "daily_limit"
    assert len(_queued_files(account)) == 1 and guard.stopped(account["name"]) is None


def test_timerに載っていない口座は止めずにburst_limit(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=0, burst=BURST, scheduled=False)
    spy = _Spy()
    for i in range(3):
        assert _send(account, spy, f"お返事{i}", minute=i, reply_to=f"1799{i}").exit_code == 0
    r = _send(account, spy, "お返事3", minute=3, reply_to="17993")
    assert r.exit_code == 1 and r.action == "guard" and r.error == "burst_limit"
    assert "口座は止めずに" in r.message and "2026-10-15T17:41:00+09:00" in r.message
    assert guard.stopped(account["name"]) is None and _queued_files(account) == []


def test_timerが並んだ返信をpublish_at以降に出す_最短間隔は掛けない(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=6, burst=BURST)
    spy = _Spy()
    for i in range(3):
        assert _send(account, spy, f"お返事{i}", minute=i, reply_to=f"1799{i}").exit_code == 0
    fourth = _send(account, spy, "お返事3", minute=3, reply_to="17993")
    fifth = _send(account, spy, "お返事4", minute=4, reply_to="17994")
    assert fourth.action == fifth.action == "queued"

    calls = []

    class Timer:
        def publish(self, post, *, dry_run, on_container_created=None):
            from thth.adapters import base
            calls.append((post.text, post.reply_to))
            return base.PublishResult(post_id=f"T{len(calls)}", url=None, ts=jst.iso(NOW))

        def fetch_post(self, post_id):
            return {"author_key": "0123456789abcdef"}

    def tick(minute):
        return core.throw_once(account["name"], production_flag=True, adapter_factory=lambda *_: Timer(),
                               now=NOW + datetime.timedelta(minutes=minute))

    tick(10)                      # 17:40: まだ時刻前
    assert calls == []
    tick(11)                      # 17:41: 4 件目
    assert calls == [("お返事3", "17993")]
    tick(20)                      # 17:50: 5 件目（間隔 6 時間でも順番待ちの返信には掛けない）
    assert calls == [("お返事3", "17993"), ("お返事4", "17994")]
    assert guard.stopped(account["name"]) is None


def test_guardの柵_返信のburstは止めずに窓が空く時刻を添える(isolated_account_factory):
    """「返信でも止める」に戻すと赤になる柵。"""
    account = isolated_account_factory(production=True, min_interval_hours=0, burst=BURST)
    from thth import accounts
    cfg = accounts.load_account(account["name"])
    sdir = accounts.state_dir_for(account["name"])
    for i in range(3):
        sent.write(sdir, post_id=f"90{i}", text=f"前{i}", body_hash="x",
                   sent_at=jst.iso(NOW + datetime.timedelta(minutes=i)))
    with pytest.raises(guard.GuardRefused) as caught:
        guard.check(account["name"], cfg, "publish", now=NOW + datetime.timedelta(minutes=3), reply=True)
    assert str(caught.value) == "burst_limit" and caught.value.reason == "burst"
    assert caught.value.next_at == "2026-10-15T17:41:00+09:00"
    assert guard.stopped(account["name"]) is None
    with pytest.raises(guard.GuardRefused) as stopped:
        guard.check(account["name"], cfg, "publish", now=NOW + datetime.timedelta(minutes=3))
    assert str(stopped.value) == "account_stopped" and guard.stopped(account["name"])["reason"] == "burst"


def test_burst_slotの計算():
    """候補は「今」と「各出来事 + 分 + 1 分」。並んだものより後ろ。"""
    cfg = {"burst": {"count": 3, "minutes": 10}}
    at = lambda m: NOW + datetime.timedelta(minutes=m)  # noqa: E731
    import thth.guard as g
    orig = g._events
    try:
        g._events = lambda account: ([at(0), at(1), at(2)], [])
        assert g.burst_slot("x", cfg, now=at(3)) == at(11)
        assert g.burst_slot("x", cfg, now=at(3), pending=[at(11)]) == at(12)
        assert g.burst_slot("x", cfg, now=at(3), pending=[at(11), at(12)]) == at(13)
        # 3 件目で窓が詰まる（11・12・13）→ 次は 11 + 11 分。
        assert g.burst_slot("x", cfg, now=at(3), pending=[at(11), at(12), at(13)]) == at(22)
        g._events = lambda account: ([], [])
        assert g.burst_slot("x", cfg, now=at(3)) == at(3)
    finally:
        g._events = orig


# --------------------------------------------------------------------------
# thth.me の道（send_request）
# --------------------------------------------------------------------------

@pytest.fixture
def publisher(monkeypatch):
    """thth.me の道の偽の媒体（返信の絡みの台帳が引く `fetch_post` も持つ）。"""
    from thth.adapters.base import PublishResult
    from thth import adapters
    calls = []

    class Adapter:
        def publish(self, post, **kwargs):
            calls.append(('publish', post.text))
            return PublishResult(f'5550{len(calls)}', None, jst.iso())

        def fetch_post(self, post_id):
            return {'author_key': '0123456789abcdef'}
    monkeypatch.setattr(adapters, 'make_adapter', lambda *a: Adapter())
    return calls


def _call(env, **request):
    return writes.execute(env['context'], dict(account='alpha', **request), via='mcp')


def _write_recent(env, n):
    state = env['root'] / 'state/alpha'
    for i in range(n):
        sent.write(str(state), post_id=f'90{i}', text=f'前の返信{i}', body_hash='x', sent_at=jst.iso())


def test_thth_meの道でも返信は順番待ちに並び_口座は止まらない(env, remote, publisher):
    ledger(env, burst={'count': 3, 'minutes': 10}, min_interval_hours=1)
    results = [_call(env, operation='send_request', body=f'お返事{i}', reply_to=f'1799{i}') for i in range(5)]
    assert [r['status'] for r in results] == ['published'] * 3 + ['queued'] * 2
    fourth, fifth = results[3], results[4]
    assert fourth['reason'] == 'burst' and fourth['account'] == 'alpha'
    first_sent = min(jst.parse(row['sent_at']) for row in sent.records(str(env['root'] / 'state/alpha')))
    assert jst.parse(fourth['publish_at']) == first_sent + datetime.timedelta(minutes=11)
    assert jst.parse(fifth['publish_at']) > jst.parse(fourth['publish_at'])
    assert len([c for c in publisher if c[0] == 'publish']) == 3
    assert guard.stopped('alpha') is None
    texts = sorted((env['root'] / 'repos/_server/alpha/queue').glob('*.md'))
    queued = [p.read_text() for p in texts if 'queued_for: burst' in p.read_text()]
    assert len(queued) == 2 and all('status: approved' in t and 'approved_via: mcp' in t for t in queued)
    # /activity で「順番待ち」として見分けられる（返信の印も付く）。
    rows = activity.summary('person', 'alpha', env['path'])['rows']
    kinds = [r for r in rows if r['kind'] == 'queued']
    assert len(kinds) == 2 and all(r['reply'] and r['draft_id'] for r in kinds)


def test_thth_meの道でも返信でない投稿は止める(env, remote, publisher):
    ledger(env, burst={'count': 2, 'minutes': 10})
    _write_recent(env, 2)
    with pytest.raises(ReportServiceError) as caught:
        _call(env, operation='send_request', body='ふつうの投稿')
    assert str(caught.value) == 'account_stopped' and guard.stopped('alpha')['reason'] == 'burst'


def test_thth_meの道でscheduled_falseは止めずにburst_limit(env, remote, publisher):
    ledger(env, burst={'count': 2, 'minutes': 10}, scheduled=False)
    _write_recent(env, 2)
    with pytest.raises(ReportServiceError) as caught:
        _call(env, operation='send_request', body='お返事', reply_to='17990')
    assert str(caught.value) == 'burst_limit' and caught.value.reason == 'burst' and caught.value.next_at
    assert guard.stopped('alpha') is None and publisher == []
    assert 'burst_limit' in writes.SAFE_ERRORS


def test_順番待ちの印はqueued_forだけで指紋は変えない():
    assert burst_queue.is_queued({'queued_for': 'burst'})
    assert not burst_queue.is_queued({'held_minutes': '5'}) and not burst_queue.is_queued(None)
