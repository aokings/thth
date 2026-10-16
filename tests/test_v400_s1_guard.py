"""4.0 S1 §4.1・§4.2: 主体・口座・ブログの 3 段の安全装置と `sent.write` の新しい欄（T-2a〜T-2f）。

見るのは:
  - 止めの順: 主体 → 口座 → 媒体の断り。主体を止めると、その主体の別レーンの口座も断る（T-2a）。
  - 主体の burst は口座を横断して数える（T-2b）。
  - 同じ blog_id の 2 口座の合計でブログの上限（T-2c）。
  - ブックマークの上限・bookmark_once・entry の枠に数えない（T-2d）。
  - 予約の日の数え: 主体の全口座の approved を足す（T-2e）。
  - 主体の止めを戻す口は CLI だけ（T-2f）。
  - `sent.write` の `kind`・`blog_id`・`delivery`（無い行は SNS の公開として数える）。
"""
from __future__ import annotations

import datetime
import os

import pytest

from tests.hatena_s1_helpers import world  # noqa: F401
from thth import accounts, guard, jst, principals, publish_hold, sent

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)


def cfg(name):
    return accounts.load_account(name)


def refused(name, kind, **kwargs):
    with pytest.raises(guard.GuardRefused) as e:
        guard.check(name, cfg(name), kind, now=NOW, **kwargs)
    return e.value


def passes(name, kind, **kwargs):
    guard.check(name, cfg(name), kind, now=NOW, **kwargs)


_SEQ = [0]


def record(world, account, *, minutes_ago, kind="entry_create", blog_id=None, retracted_minutes_ago=None,
           post_id=None, url=None):
    _SEQ[0] += 1
    at = NOW - datetime.timedelta(minutes=minutes_ago)
    if post_id is None:
        post_id = (sent.bookmark_post_id(url) if kind.startswith("bookmark")
                   else f"tag:blog.hatena.ne.jp,2013:blog-test-{_SEQ[0]}")
    blog_id = blog_id or world.account(account)["blog_id"]
    sent.write(world.state(account), post_id=post_id, text="本文", body_hash="0" * 64,
               sent_at=jst.iso(at), kind=kind, blog_id=blog_id,
               delivery=f"dl_{_SEQ[0]:012x}", url=url)
    if retracted_minutes_ago is not None:
        sent.mark_retracted(world.state(account), post_id,
                            retracted_at=jst.iso(NOW - datetime.timedelta(minutes=retracted_minutes_ago)),
                            retracted_by="tester", retract_reason="test")
    return post_id


# --------------------------------------------------------------------------
# T-2a 止めの順
# --------------------------------------------------------------------------

def test_T2a_主体を止めると別レーンの口座も断り別の主体は通る(world):
    """柵: 主体の段を飛ばすと赤。"""
    passes("lane-a-hatenablog", "entry_create")
    principals.stop("hatena-main", "owner", now=NOW, actor="tester")
    for name in ("lane-a-hatenablog", "lane-b-hatenablog", "lane-a-shared"):
        for kind in ("entry_create", "entry_update", "entry_delete", "schedule"):
            e = refused(name, kind, publish_at=NOW)
            assert str(e) == "principal_stopped" and e.reason == "owner"
    e = refused("lane-a-hatenablog", "bookmark", bookmark_url="https://a.hatenablog.com/entry/1")
    assert str(e) == "principal_stopped"
    passes("lane-c-hatenablog", "entry_create")


def test_T2a_主体の止めは口座の止めより先に見る(world):
    principals.stop("hatena-main", "burst", now=NOW)
    guard.stop("lane-a-hatenablog", "owner", now=NOW)
    e = refused("lane-a-hatenablog", "entry_create")
    assert str(e) == "principal_stopped" and e.reason == "burst"
    principals.resume("hatena-main", by="tester")
    e = refused("lane-a-hatenablog", "entry_create")
    assert str(e) == "account_stopped" and e.reason == "owner"
    # 口座の止めはその口座だけ
    passes("lane-b-hatenablog", "entry_create")


def test_T2a_媒体の断りの印は口座の止めのあと(world):
    # 印の置き場は publish_hold と同じ（X 以外は自動では置かないので、手で置く）
    from thth import server_files
    from pathlib import Path
    directory = Path(accounts.state_dir_for("lane-a-hatenablog")) / "publish_hold"
    with server_files.directory(directory, create=True, private=True) as fd:
        server_files.replace_at(fd, publish_hold.HOLD_FILE,
                                server_files.encode({"reason": "x_not_permitted", "held_at": jst.iso(NOW)}),
                                private=True)
    e = refused("lane-a-hatenablog", "entry_create")
    assert str(e) == "account_stopped" and e.reason is None
    passes("lane-b-hatenablog", "entry_create")


def test_T2a_読めない主体の止めの印は止まっている側(world):
    principals.ensure_state_dir("hatena-main")
    directory = os.path.join(principals.state_dir_for("hatena-main"), "guard")
    os.makedirs(directory, mode=0o700)
    with open(os.path.join(directory, "stopped.json"), "w") as f:
        f.write("{broken")
    os.chmod(os.path.join(directory, "stopped.json"), 0o600)
    e = refused("lane-b-hatenablog", "entry_create")
    assert str(e) == "principal_stopped" and e.reason == "guard_state_unreadable"


def test_T2a_ロックの中の数え直しで主体の台帳からレーンを外すと断る(world):
    c = cfg("lane-a-hatenablog")  # 読んだあとに主体の台帳を変える（承認のあとに外した形）
    world.edit_principal("hatena-main", blogs=[{"blog_id": "a.hatenablog.com", "lanes": ["lane-b"]}])
    with pytest.raises(guard.GuardRefused) as e:
        guard.check("lane-a-hatenablog", c, "entry_create", now=NOW)
    assert str(e.value) == "blog_not_allowed_for_lane"


# --------------------------------------------------------------------------
# T-2b 主体の burst
# --------------------------------------------------------------------------

def test_T2b_主体のburstは口座を横断する(world):
    """柵: 主体の `recent` を口座ごとにすると赤。"""
    record(world, "lane-a-hatenablog", minutes_ago=9)
    record(world, "lane-a-hatenablog", minutes_ago=8)
    record(world, "lane-b-hatenablog", minutes_ago=7)
    e = refused("lane-a-hatenablog", "entry_create")
    assert str(e) == "principal_stopped" and e.reason == "burst"
    assert principals.stopped("hatena-main")["reason"] == "burst"
    # 口座ごとの burst（10 分 3 件）には掛からない＝口座は止まっていない
    assert guard.stopped("lane-a-hatenablog") is None
    assert guard.stopped("lane-b-hatenablog") is None
    e = refused("lane-b-hatenablog", "entry_create")
    assert str(e) == "principal_stopped"


def test_T2b_主体のburstはブックマークも合算する(world):
    record(world, "lane-a-hatenablog", minutes_ago=20)
    record(world, "lane-a-hatenablog", minutes_ago=19, kind="bookmark_add", url="https://a.hatenablog.com/entry/1")
    record(world, "lane-b-hatenablog", minutes_ago=18)
    e = refused("lane-b-hatenablog", "entry_create")
    assert str(e) == "principal_stopped" and e.reason == "burst"


def test_T2b_窓の外なら止めない(world):
    record(world, "lane-a-hatenablog", minutes_ago=50)
    record(world, "lane-a-hatenablog", minutes_ago=40)
    record(world, "lane-b-hatenablog", minutes_ago=35)
    passes("lane-a-hatenablog", "entry_create")
    assert principals.stopped("hatena-main") is None


# --------------------------------------------------------------------------
# T-2c ブログの上限・主体の 1 日の上限
# --------------------------------------------------------------------------

def test_T2c_同じblog_idの2口座の合計でblog_daily_limit(world):
    world.edit_principal("hatena-main", blogs=[
        {"blog_id": "a.hatenablog.com", "lanes": ["lane-a"]},
        {"blog_id": "b.hatenablog.com", "lanes": ["lane-b"]},
        {"blog_id": "shared.hatenablog.com", "lanes": ["lane-a", "lane-b"],
         "daily_max_posts": 2, "daily_max_retracts": 1}])
    record(world, "lane-a-shared", minutes_ago=180)
    passes("lane-a-shared", "entry_create")
    record(world, "lane-b-shared", minutes_ago=120)
    e = refused("lane-a-shared", "entry_create")
    assert str(e) == "blog_daily_limit"
    assert e.next_at == "2026-09-10T00:00:00+09:00"
    # 別のブログは通る（主体の上限 6 には届いていない）
    passes("lane-a-hatenablog", "entry_create")
    # 削除の枠も同じ数え方
    record(world, "lane-b-shared", minutes_ago=100, kind="entry_delete")
    assert str(refused("lane-a-shared", "entry_delete")) == "blog_retract_limit"


def test_T2c_主体の1日の上限は全ブログの合計(world):
    world.edit_principal("hatena-main", daily_max_posts=2, daily_max_retracts=1)
    record(world, "lane-a-hatenablog", minutes_ago=300)
    record(world, "lane-b-hatenablog", minutes_ago=200)
    e = refused("lane-a-shared", "entry_update")
    assert str(e) == "principal_daily_limit"
    passes("lane-c-hatenablog", "entry_create")
    record(world, "lane-b-hatenablog", minutes_ago=150, retracted_minutes_ago=100)
    assert str(refused("lane-a-hatenablog", "entry_delete")) == "principal_retract_limit"


def test_T2c_口座の上限と最短間隔と夜間は今のまま効く(world, frozen_now_jst):
    record(world, "lane-a-hatenablog", minutes_ago=300)
    record(world, "lane-a-hatenablog", minutes_ago=200)
    record(world, "lane-a-hatenablog", minutes_ago=100)
    assert str(refused("lane-a-hatenablog", "entry_create")) == "daily_limit"
    late = NOW.replace(hour=23, minute=30)
    with pytest.raises(guard.GuardRefused) as e:
        guard.check("lane-b-hatenablog", cfg("lane-b-hatenablog"), "entry_create", now=late)
    assert str(e.value) == "quiet_hours"


def test_T2c_publishとretractの語ははてなの口座ではentryの語に読み替える(world):
    world.edit_principal("hatena-main", daily_max_posts=1)
    record(world, "lane-b-hatenablog", minutes_ago=300)
    assert str(refused("lane-a-hatenablog", "publish")) == "principal_daily_limit"


# --------------------------------------------------------------------------
# T-2d ブックマーク
# --------------------------------------------------------------------------

def test_T2d_ブックマークの上限と_entryの枠に数えない(world):
    for i, minutes in enumerate((300, 200, 100)):
        record(world, "lane-a-hatenablog", minutes_ago=minutes, kind="bookmark_add",
               url=f"https://a.hatenablog.com/entry/{i}")
    e = refused("lane-a-hatenablog", "bookmark", bookmark_url="https://a.hatenablog.com/entry/9")
    assert str(e) == "bookmark_limit"
    assert str(refused("lane-a-hatenablog", "bookmark_delete")) == "bookmark_limit"
    # entry の枠（口座 3・主体 6）には数えない
    passes("lane-a-hatenablog", "entry_create")
    assert guard.summary("lane-a-hatenablog", cfg("lane-a-hatenablog"), now=NOW)["posts_today"] == 0


def test_T2d_削除も枠に数える(world):
    record(world, "lane-a-hatenablog", minutes_ago=300, kind="bookmark_add",
           url="https://a.hatenablog.com/entry/1", retracted_minutes_ago=250)
    record(world, "lane-a-hatenablog", minutes_ago=200, kind="bookmark_add",
           url="https://a.hatenablog.com/entry/2")
    e = refused("lane-a-hatenablog", "bookmark", bookmark_url="https://a.hatenablog.com/entry/3")
    assert str(e) == "bookmark_limit"
    # 主体の daily_max_retracts には数えない
    passes("lane-a-hatenablog", "entry_delete")


def test_T2d_bookmark_onceは削除後の2回目も断る(world):
    url = "https://a.hatenablog.com/entry/1"
    # 4.0 S4 §5.3: 同じホストの 1 日の枠（既定 1）はここで見るものではないので上げる。
    world.edit_principal("hatena-main", bookmark_same_host_daily=10)
    record(world, "lane-a-hatenablog", minutes_ago=300, kind="bookmark_add", url=url,
           retracted_minutes_ago=280)
    e = refused("lane-a-hatenablog", "bookmark", bookmark_url=url)
    assert str(e) == "bookmark_once"
    # 別の口座（同じ主体）から同じ URL も断る
    world.edit_principal("hatena-main", bookmark={"lanes": ["lane-a", "lane-b"]})
    assert str(refused("lane-b-hatenablog", "bookmark", bookmark_url=url)) == "bookmark_once"
    assert refused("lane-b-hatenablog", "bookmark", bookmark_url=url).reason == "same_principal"
    passes("lane-b-hatenablog", "bookmark", bookmark_url="https://a.hatenablog.com/entry/2")


def test_T2d_ブックマークを許していないレーンは断る(world):
    e = refused("lane-b-hatenablog", "bookmark", bookmark_url="https://b.hatenablog.com/entry/1")
    assert str(e) == "bookmark_not_allowed_for_lane"


def test_T2d_ブックマークに夜間は掛けない(world):
    late = NOW.replace(hour=23, minute=30)
    guard.check("lane-a-hatenablog", cfg("lane-a-hatenablog"), "bookmark", now=late,
                bookmark_url="https://a.hatenablog.com/entry/1")


def test_T2d_URLの無いブックマークは数えられないので断る(world):
    with pytest.raises(ValueError):
        guard.check("lane-a-hatenablog", cfg("lane-a-hatenablog"), "bookmark", now=NOW)


# --------------------------------------------------------------------------
# T-2e 予約の日の数え
# --------------------------------------------------------------------------

def _queue(world, repo, name, *, account, publish_at, status="approved", post_id=None):
    directory = world.tmp / repo / "docs" / "sns" / "queue"
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", "thth: 1", f"account: {account}", f"status: {status}", f"publish_at: {publish_at}"]
    if post_id:
        lines.append(f"post_id: {post_id}")
    lines += ["---", "", "## hatenablog", ""]
    (directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_T2e_scheduleは主体の全口座のapprovedを足す(world):
    world.edit_principal("hatena-main", daily_max_posts=2)
    day = "2026-09-12T08:00:00+09:00"
    _queue(world, "repo-a", "one.md", account="lane-a-hatenablog", publish_at=day)
    _queue(world, "repo-b", "two.md", account="lane-b-hatenablog", publish_at=day)
    _queue(world, "repo-b", "draft.md", account="lane-b-hatenablog", publish_at=day, status="draft")
    _queue(world, "repo-b", "done.md", account="lane-b-hatenablog", publish_at=day, post_id="tag:x")
    _queue(world, "repo-b", "other-day.md", account="lane-b-hatenablog", publish_at="2026-09-13T08:00:00+09:00")
    when = jst.parse(day)
    assert principals.scheduled_same_day("hatena-main", when.date()) == 2
    e = refused("lane-a-hatenablog", "schedule", publish_at=when, scheduled_same_day=1)
    assert str(e) == "principal_daily_limit"
    assert e.next_at == "2026-09-13T00:00:00+09:00"
    # 別の日は通る（その日の approved は 1 本）
    passes("lane-a-hatenablog", "schedule", publish_at=jst.parse("2026-09-13T08:00:00+09:00"))
    # 呼び手が数えた値を渡せばそれを使う
    passes("lane-a-hatenablog", "schedule", publish_at=when, scheduled_same_day=1,
           principal_scheduled_same_day=1)


def test_T2e_その日に出した数も足す(world):
    world.edit_principal("hatena-main", daily_max_posts=2)
    record(world, "lane-b-hatenablog", minutes_ago=60)
    _queue(world, "repo-a", "one.md", account="lane-a-hatenablog", publish_at="2026-09-09T20:00:00+09:00")
    e = refused("lane-a-hatenablog", "schedule", publish_at=NOW.replace(hour=20))
    assert str(e) == "principal_daily_limit"


# --------------------------------------------------------------------------
# T-2f 主体の止めを戻す口は CLI だけ
# --------------------------------------------------------------------------

def test_T2f_principal_resumeはMCPの道具に無い(monkeypatch):
    """柵: 道具の一覧に足すと赤。"""
    from tests.test_mcp import _load_server_module
    for key in ('THTH_REPORT_CREDENTIALS', 'THTH_REPORT_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    names = {tool["name"] for tool in server.TOOLS + server.ADMIN_TOOLS + server.SERVER_TOOLS}
    for name in names:
        assert "principal" not in name, name
        assert "resume" not in name, name
    from thth import server_writes
    for operation in server_writes.PUBLISHING_OPERATIONS | server_writes.READ_OPERATIONS:
        assert "principal" not in operation and "resume" not in operation


def test_T2f_符丁はSAFE_ERRORSに載る():
    from thth.server_writes import SAFE_ERRORS
    for code in ("principal_stopped", "principal_daily_limit", "principal_retract_limit", "bookmark_limit",
                 "blog_daily_limit", "blog_retract_limit", "bookmark_once", "blog_not_allowed_for_lane"):
        assert code in SAFE_ERRORS
        assert code in guard.REFUSALS


# --------------------------------------------------------------------------
# sent.write の欄
# --------------------------------------------------------------------------

def test_sent_writeのkindとblog_idとdelivery(world):
    state = world.state("lane-a-hatenablog")
    path = sent.write(state, post_id="tag:blog.hatena.ne.jp,2013:blog-x-1", text="t", body_hash="h",
                      sent_at=jst.iso(NOW), kind="entry_create", blog_id="a.hatenablog.com",
                      delivery="dl_3f9c2a7e1b4d")
    row = sent.read(state, "tag:blog.hatena.ne.jp,2013:blog-x-1")
    assert (row["kind"], row["blog_id"], row["delivery"]) == ("entry_create", "a.hatenablog.com", "dl_3f9c2a7e1b4d")
    # はてなの記録は上書きしない（create の行を update で潰すと数が減る）
    with pytest.raises(ValueError, match="sent_record_exists"):
        sent.write(state, post_id="tag:blog.hatena.ne.jp,2013:blog-x-1", text="t", body_hash="h",
                   sent_at=jst.iso(NOW), kind="entry_update", blog_id="a.hatenablog.com")
    assert os.path.exists(path)


@pytest.mark.parametrize("kwargs", [
    {"kind": "publish"}, {"kind": "entry_create", "blog_id": "A.HATENABLOG.COM"},
    {"kind": "entry_create", "delivery": "dl_xyz"}, {"blog_id": "a.hatenablog.com"},
    {"kind": "bookmark_add", "url": "ftp://x"},
])
def test_sent_writeは知らない値を断る(tmp_path, kwargs):
    with pytest.raises(ValueError):
        sent.write(str(tmp_path), post_id="p1", text="t", body_hash="h", sent_at=jst.iso(NOW), **kwargs)


def test_sent_writeのSNSの行は今までと同じ形(tmp_path):
    sent.write(str(tmp_path), post_id="123", text="t", body_hash="h", sent_at=jst.iso(NOW))
    assert set(sent.read(str(tmp_path), "123")) == {"post_id", "text", "body_hash", "sent_at",
                                                     "approved_fingerprint", "reply_to"}
    # SNS の行は今までどおり上書きできる（既存の経路を変えない）
    sent.write(str(tmp_path), post_id="123", text="t2", body_hash="h", sent_at=jst.iso(NOW))
    assert sent.read(str(tmp_path), "123")["text"] == "t2"


def test_bookmark_post_idはファイル名にできる():
    pid = sent.bookmark_post_id("https://a.hatenablog.com/entry/1")
    assert pid.startswith("bm:") and len(pid) == 27
    from thth import postid
    assert postid.is_usable(pid)


def test_SNSの口座の数え方は変わらない(world, tmp_path):
    """kind の無い行は SNS の公開として今までどおり数える。"""
    from tests.hatena_s1_helpers import account_data
    data = account_data("lane-a-threads", project="lane-a", principal="hatena-main",
                        blog_id="a.hatenablog.com", token=tmp_path / "t.token", handle="who")
    data["media"] = "threads"
    world.write_account(data)
    state = world.state("lane-a-threads")
    sent.write(state, post_id="1", text="t", body_hash="h", sent_at=jst.iso(NOW - datetime.timedelta(minutes=5)))
    sent.write(state, post_id="2", text="t", body_hash="h", sent_at=jst.iso(NOW - datetime.timedelta(minutes=4)))
    sent.mark_retracted(state, "2", retracted_at=jst.iso(NOW - datetime.timedelta(minutes=3)),
                        retracted_by="t", retract_reason="r")
    s = guard.summary("lane-a-threads", cfg("lane-a-threads"), now=NOW)
    assert (s["posts_today"], s["retracts_today"], s["recent"]) == (2, 1, 3)
    # 主体は止めても SNS の口座には効かない（principal を書いてあっても読まない）
    principals.stop("hatena-main", "owner", now=NOW)
    with pytest.raises(guard.GuardRefused) as e:
        guard.check("lane-a-threads", cfg("lane-a-threads"), "publish", now=NOW)
    assert str(e.value) == "account_stopped"  # 口座の burst（3 件/10 分）に掛かる＝今までどおり
    with pytest.raises(ValueError):
        guard.check("lane-a-threads", cfg("lane-a-threads"), "entry_create", now=NOW)
