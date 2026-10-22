"""4.5.2: 予約の在庫が 48 時間分を切ったら `thth events` に `schedule_low`（続いている状態）を出す。

10/19〜22 にこぴちゃ（Bluesky・Mastodon）と関東の予約が尽きて投稿が止まった。人が気づいたのは
止まったあとだった（ナイトシフト 10/22）。h2d は events を読むので、そこへ前もって出す。
見るのは: 動いている口座だけ・承認済みで未来だけ数える・最後の予約が 48 時間より先なら出ない・
読むだけで何も書かない。
"""
from __future__ import annotations

import datetime
import os

from tests.conftest import write_queue_file
from thth import accounts as accounts_mod, events_cli, jst, sent


def _put(account, name, *, hours, status="approved"):
    """今から `hours` 時間後に出る予約を 1 本置く（commit・push 済み）。"""
    at = jst.now_jst() + datetime.timedelta(hours=hours)
    write_queue_file(account["queue_dir"], name, body="## threads\n\n予約の本文です。\n",
                     fm_overrides={"account": account["name"], "status": status,
                                   "publish_at": jst.iso(at)})


def _published(account, *, days_ago):
    at = jst.now_jst() - datetime.timedelta(days=days_ago)
    sent.write(accounts_mod.state_dir_for(account["name"]), post_id=f"p-{days_ago}", text="公開した本文",
               body_hash="0" * 64, sent_at=jst.iso(at))


def _low(account):
    payload = events_cli.build(account["name"], since="7d", now=jst.now_jst())
    return [e for e in payload["events"] if e["kind"] == "schedule_low"], payload


def test_active_account_with_short_stock_is_flagged(isolated_account):
    _published(isolated_account, days_ago=1)
    _put(isolated_account, "a.md", hours=20)
    rows, payload = _low(isolated_account)
    assert len(rows) == 1
    row = rows[0]
    assert row["ongoing"] is True and row["ref"] == "stock"
    assert row["floor_hours"] == 48 and 18 <= row["stock_hours"] <= 20
    assert row["approved_future"] == 1 and row["last_publish_at"]
    assert payload["by_kind"]["schedule_low"] == 1


def test_enough_stock_is_quiet(isolated_account):
    _published(isolated_account, days_ago=1)
    _put(isolated_account, "a.md", hours=20)
    _put(isolated_account, "b.md", hours=72)
    rows, _ = _low(isolated_account)
    assert rows == []


def test_no_approved_future_at_all_is_flagged_with_zero_hours(isolated_account):
    _published(isolated_account, days_ago=2)
    rows, _ = _low(isolated_account)
    assert len(rows) == 1 and rows[0]["stock_hours"] == 0 and "last_publish_at" not in rows[0]


def test_drafts_do_not_count_as_stock(isolated_account):
    _published(isolated_account, days_ago=1)
    _put(isolated_account, "draft.md", hours=200, status="draft")
    rows, _ = _low(isolated_account)
    assert len(rows) == 1 and rows[0]["approved_future"] == 0


def test_account_not_publishing_lately_is_not_flagged(isolated_account):
    _published(isolated_account, days_ago=10)   # 7 日より前
    rows, _ = _low(isolated_account)
    assert rows == []


def test_account_that_never_published_is_not_flagged(isolated_account):
    rows, _ = _low(isolated_account)
    assert rows == []


def test_reading_writes_nothing(isolated_account):
    _published(isolated_account, days_ago=1)
    before = sorted(os.listdir(isolated_account["queue_dir"]))
    _low(isolated_account)
    assert sorted(os.listdir(isolated_account["queue_dir"])) == before
