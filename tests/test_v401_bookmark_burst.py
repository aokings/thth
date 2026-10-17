"""4.0.1: ブックマークの連投の窓を記事と分ける（masaru 10/17「ブクマの連投の制限はスレッズなどより緩く」）。

見るのは:
  - ブックマーク（付ける・消す）は主体の `bookmark_burst`（既定 10 分に 10 件）で数え、超えても主体を
    止めずに `burst_limit`（詳細 `bookmark_burst`・`next_at` は窓が空く時刻）でその 1 件だけ断る。
  - 記事の主体の burst は記事だけを数える（ブックマークを数えない・4 本目で止まるのは今までどおり）。
  - 既定: `daily_max_bookmarks` 30・`bookmark_same_host_daily` 3・`daily_max_self_bookmarks` 1。台帳の値が勝つ。
  - `thth principal set` の `bookmark_burst_count`・`bookmark_burst_minutes`・`bookmark_burst <件数> <分>` と show。
  - 変えないもの: bookmark_once・SNS の burst。

はてなには出ない（`guard.check` を直に・記録は `sent.write` で置く）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from tests.conftest import run_thth
from tests.hatena_s1_helpers import EXAMPLES, world  # noqa: F401
from thth import accounts, core, guard, jst, principals, sent

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)
ACCOUNT = "lane-a-hatenablog"
_SEQ = [0]


def cfg(name):
    return accounts.load_account(name)


def check(name, kind, *, now=NOW, **kwargs):
    guard.check(name, cfg(name), kind, now=now, **kwargs)


def refused(name, kind, *, now=NOW, **kwargs):
    with pytest.raises(guard.GuardRefused) as e:
        check(name, kind, now=now, **kwargs)
    return e.value


def record(world, account, *, at, kind="entry_create", url=None):
    _SEQ[0] += 1
    post_id = sent.bookmark_post_id(url) if kind.startswith("bookmark") else f"tag:blog.hatena.ne.jp,2013:blog-v401-{_SEQ[0]}"
    sent.write(world.state(account), post_id=post_id, text="本文", body_hash="0" * 64, sent_at=jst.iso(at),
               kind=kind, blog_id=world.account(account)["blog_id"], delivery=f"dl_{_SEQ[0]:012x}", url=url)


def url_of(i):
    # 同じホストの枠（既定 3）に掛からないよう、1 件ごとに別のホスト。
    return f"https://site{i}.example.com/page"


def bookmark_ten(world, *, start_minutes_ago=9):
    """10 分の窓の中に 10 件（1 件ごとに安全装置を通してから記録する）。"""
    for i in range(10):
        at = NOW - datetime.timedelta(minutes=start_minutes_ago) + datetime.timedelta(seconds=50 * i)
        check(ACCOUNT, "bookmark", now=at, bookmark_url=url_of(i))
        record(world, ACCOUNT, at=at, kind="bookmark_add", url=url_of(i))


# ---------------------------------------------------------------- ブックマークの窓

def test_10分に10件付けても主体は止まらない(world):
    """柵: ブックマークを記事の burst（30 分に 3）で数えると赤。"""
    bookmark_ten(world)
    assert principals.stopped("hatena-main") is None
    assert guard.stopped(ACCOUNT) is None
    whole = guard.principal_summary("hatena-main", principals.load_principal("hatena-main"), now=NOW)
    assert whole["bookmark_recent"] == 10 and whole["recent"] == 0


def test_11件目はburst_limitで断り主体は止めない(world):
    """柵: 超えたら主体を止める形に戻すと赤。窓が空く時刻を添える。"""
    bookmark_ten(world)
    e = refused(ACCOUNT, "bookmark", bookmark_url=url_of(99))
    assert str(e) == "burst_limit" and e.reason == "bookmark_burst"
    # 窓に入った最初の 1 件（9 分前）が窓を出る時刻 = 1 分後。
    assert e.next_at == jst.iso(NOW + datetime.timedelta(minutes=1))
    assert principals.stopped("hatena-main") is None and guard.stopped(ACCOUNT) is None
    # 消すも同じ窓
    e = refused(ACCOUNT, "bookmark_delete")
    assert str(e) == "burst_limit" and e.reason == "bookmark_burst"
    # 記事は止まらず出せる（ブックマークは記事の burst に数えない）
    check(ACCOUNT, "entry_create")
    # 窓が空けば通る
    check(ACCOUNT, "bookmark", now=jst.parse(e.next_at), bookmark_url=url_of(99))
    assert principals.stopped("hatena-main") is None


def test_ブックマークの窓は台帳で変えられる(world):
    world.edit_principal("hatena-main", bookmark_burst={"count": 2, "minutes": 60})
    for i in range(2):
        at = NOW - datetime.timedelta(minutes=50 - i)
        check(ACCOUNT, "bookmark", now=at, bookmark_url=url_of(i))
        record(world, ACCOUNT, at=at, kind="bookmark_add", url=url_of(i))
    e = refused(ACCOUNT, "bookmark", bookmark_url=url_of(9))
    assert str(e) == "burst_limit" and e.reason == "bookmark_burst"
    assert e.next_at == jst.iso(NOW + datetime.timedelta(minutes=10))


def test_ブックマークの窓は口座をまたいで主体で数える(world):
    world.edit_principal("hatena-main", bookmark={"lanes": ["lane-a", "lane-b"]},
                         bookmark_burst={"count": 2, "minutes": 10})
    record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=3), kind="bookmark_add", url=url_of(0))
    record(world, "lane-b-hatenablog", at=NOW - datetime.timedelta(minutes=2), kind="bookmark_add", url=url_of(1))
    e = refused("lane-b-hatenablog", "bookmark", bookmark_url=url_of(2))
    assert str(e) == "burst_limit" and e.reason == "bookmark_burst"
    # 別の主体は数えない
    world.edit_principal("hatena-other", bookmark={"lanes": ["lane-c"]})
    check("lane-c-hatenablog", "bookmark", bookmark_url=url_of(3))


# ---------------------------------------------------------------- 記事の burst は今までどおり

def test_記事のburstは今までどおり4本目で主体を止める(world):
    """柵: 記事の主体の burst を外すと赤。ブックマークが窓にあっても数は記事だけ。"""
    bookmark_ten(world)
    record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=20))
    record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=19))
    record(world, "lane-b-hatenablog", at=NOW - datetime.timedelta(minutes=18))
    e = refused("lane-b-hatenablog", "entry_create")
    assert str(e) == "principal_stopped" and e.reason == "burst"
    assert principals.stopped("hatena-main")["reason"] == "burst"


def test_ブックマークは記事のburstに数えない(world):
    """柵: 記事の burst の窓にブックマークを足すと赤（記事 2 本＋ブックマーク 10 件で 3 本目が通る）。"""
    bookmark_ten(world, start_minutes_ago=25)
    record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=12))
    record(world, "lane-b-hatenablog", at=NOW - datetime.timedelta(minutes=11))
    check("lane-b-hatenablog", "entry_create")
    assert principals.stopped("hatena-main") is None
    whole = guard.principal_summary("hatena-main", principals.load_principal("hatena-main"), now=NOW)
    assert whole["recent"] == 2


def test_bookmark_onceは変わらない(world):
    """ブックマークの窓に余地があっても、同じ URL の 2 回目は bookmark_once。"""
    record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=300), kind="bookmark_add", url=url_of(0))
    e = refused(ACCOUNT, "bookmark", bookmark_url=url_of(0))
    assert str(e) == "bookmark_once"


# ---------------------------------------------------------------- 既定

def test_既定の値():
    """柵: 既定を戻すと赤。自分の記事は 1 のまま（はてなのスパムの指針・S4 §5.3）。"""
    d = principals.LIMIT_DEFAULTS
    assert d["daily_max_bookmarks"] == 30
    assert d["bookmark_same_host_daily"] == 3
    assert d["daily_max_self_bookmarks"] == 1
    assert d["bookmark_burst"] == {"count": 10, "minutes": 10}
    assert d["burst"] == {"count": 3, "minutes": 30}
    assert principals.limits({}) == d
    template = json.loads((EXAMPLES / "principal-hatena.json").read_text(encoding="utf-8"))
    for key in ("daily_max_bookmarks", "bookmark_same_host_daily", "daily_max_self_bookmarks",
                "bookmark_burst", "burst"):
        assert template[key] == d[key], key


def test_台帳に書いた値が既定より勝つ(world):
    world.edit_principal("hatena-main", daily_max_bookmarks=3, bookmark_same_host_daily=1)
    lim = principals.limits(principals.load_principal("hatena-main"))
    assert lim["daily_max_bookmarks"] == 3 and lim["bookmark_same_host_daily"] == 1
    for i, minutes in enumerate((300, 200, 100)):
        record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=minutes), kind="bookmark_add", url=url_of(i))
    assert str(refused(ACCOUNT, "bookmark", bookmark_url=url_of(9))) == "bookmark_limit"


def test_1日30件で断る(world):
    for i in range(30):
        record(world, ACCOUNT, at=NOW - datetime.timedelta(minutes=500 - 15 * i), kind="bookmark_add", url=url_of(i))
    e = refused(ACCOUNT, "bookmark", bookmark_url=url_of(99))
    assert str(e) == "bookmark_limit"
    assert principals.stopped("hatena-main") is None


@pytest.mark.parametrize("value", [{"count": 0, "minutes": 10}, {"count": 10}, 5, {"count": 10, "minutes": 0}])
def test_受け取れないbookmark_burstは断る(world, value):
    data = world.principal("hatena-main")
    data["bookmark_burst"] = value
    with pytest.raises(principals.PrincipalError) as e:
        principals.validate("hatena-main", data)
    assert e.value.code == "invalid_guard_limits"


# ---------------------------------------------------------------- 断りの文

def test_断りの文は主体を止めていないと言う():
    e = guard.GuardRefused("burst_limit", reason="bookmark_burst", next_at="2026-09-09T10:01:00+09:00")
    msg = core.guard_refusal_message(ACCOUNT, e)
    assert "bookmark_burst" in msg and "主体は止めずに" in msg and "2026-09-09T10:01:00+09:00" in msg


# ---------------------------------------------------------------- principal set／show

def test_principal_setとshow(world):
    env = {"THTH_ROOT": str(world.root), "THTH_ACCOUNTS_DIR": str(world.accounts_dir)}
    r = run_thth(["principal", "set", "hatena-main", "bookmark_burst_count", "20", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert world.principal("hatena-main")["bookmark_burst"] == {"count": 20, "minutes": 10}
    r = run_thth(["principal", "set", "hatena-main", "bookmark_burst_minutes", "15", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert world.principal("hatena-main")["bookmark_burst"] == {"count": 20, "minutes": 15}
    r = run_thth(["principal", "set", "hatena-main", "bookmark_burst", "5", "30", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert world.principal("hatena-main")["bookmark_burst"] == {"count": 5, "minutes": 30}
    # 記事の burst は触らない
    assert principals.limits(principals.load_principal("hatena-main"))["burst"] == {"count": 3, "minutes": 30}
    r = run_thth(["principal", "set", "hatena-main", "bookmark_burst_count", "0", "--by", "tester"], env=env)
    assert r.returncode == 2 and "invalid_setting" in r.stderr
    r = run_thth(["principal", "show", "hatena-main"], env=env)
    assert r.returncode == 0, r.stderr
    assert "ブックマークの連投: 30 分に 5 件まで" in r.stdout
    r = run_thth(["principal", "show", "hatena-main", "--json"], env=env)
    assert json.loads(r.stdout)["limits"]["bookmark_burst"] == {"count": 5, "minutes": 30}
