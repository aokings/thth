"""5.33.0 自分の投稿の読みを 0.005 で数える（`GET /2/tweets` は Owned Reads の表に無い・多めに倒す）・
`recent_posts` は返った投稿 1 本ごと・where の金額の行（調べ docs/調べ_X_expansionsの数え方_2026-11-28.md §5）。

**本物の api.x.com は叩かない**（`x.request` を偽物に）。
"""
from __future__ import annotations

import urllib.error
from decimal import Decimal

import pytest

from thth import budget_x as b
from thth import server_files, where_cli
from thth.adapters import base
from tests.test_x_search_thread import (  # noqa: F401  （fixture をこのファイルでも使う）
    ACCOUNT, adapter_for, fake, x_account)

AT = "2026-09-09T00:30:00+00:00"
OLD_OWNED_VERSION = "2026-10-12-owned-read"


def _write(name, value):
    with server_files.directory(b.folder(), create=True, private=True) as fd:
        server_files.replace_at(fd, name, server_files.encode(value), private=True)


def old_owned_rows():
    """VM に残る形の行（版 2026-10-12-owned-read・1 本 0.001）。確定・押さえ・uncertain・released。"""
    row = {"account": ACCOUNT, "limit": 10, "price_version": OLD_OWNED_VERSION, "reservation_month": "2026-09",
           "day_jst": "2026-09-09", "at": AT}
    return {"schema_version": 1, "reservations": {
        "a" * 32: dict(row, state="settled", count=7, usd="0.007", get_at=AT),
        "b" * 32: dict(row, state="uncertain", count=None, usd="0.010", get_at=AT),
        "c" * 32: dict(row, state="released", count=None, usd="0.010", get_at=None),
        "d" * 32: dict(row, state="reserved", count=None, usd="0.010", get_at=None)}}


def owned_rows():
    return list(b.read_posts(b.OWNED)["reservations"].values())


# ----- 柵 1: 古い版の行が読める（直す前にも通ることを確かめた）---------------------------------

def test_old_owned_rows_stay_readable_and_keep_their_old_price(x_account):
    _write(b.OWNED_FILE, old_owned_rows())
    whole = b.report()
    assert whole["owned_read"]["month"]["spent_estimate_usd"] == "0.017"   # 確定 0.007＋uncertain 0.010
    assert whole["owned_read"]["month"]["held_usd"] == "0.010"
    with b.owned_read(ACCOUNT, 2) as slot:   # 古い行のあとに新しい行が足せる
        slot.dispatch(); slot.settle(2)
    assert b.report()["owned_read"]["month"]["spent_estimate_usd"] == "0.027"


def test_old_owned_row_with_a_wrong_price_is_still_refused(x_account):
    rows = old_owned_rows(); rows["reservations"]["a" * 32]["usd"] = "0.035"   # 古い版を新しい単価で書いた
    _write(b.OWNED_FILE, rows)
    with pytest.raises(b.BudgetError, match="budget_unreadable"):
        b.report()


# ----- 柵 2: 自分の投稿の読みは 1 本 0.005 --------------------------------------------------

def test_owned_reads_are_counted_at_the_standard_post_rate(x_account):
    assert b.OWNED_PRICE == b.POSTS_PRICE == Decimal("0.005")
    with b.owned_read(ACCOUNT, 4) as slot:
        assert b.report()["held_usd"] == "0.020"
        slot.dispatch(); slot.settle(3)
    [row] = owned_rows()
    assert row["usd"] == "0.015" and row["price_version"] == "2026-11-28-owned-read-standard"


# ----- 柵 3: recent_posts は返った 1 本ごと（押さえは max_results 本） -------------------------

def test_recent_posts_reserves_max_results_and_settles_returned_posts(x_account, fake):
    seen = {}

    def answer(query):
        seen["held"] = b.report()["held_usd"]
        return {"data": [{"id": str(1900000000000000000 + i), "text": "t"} for i in range(3)],
                "meta": {"result_count": 3}}
    fake["routes"]["/2/users/123/tweets"] = answer
    adapter = adapter_for()
    rows = adapter.recent_posts(limit=25)
    assert len(rows) == 3 and fake["calls"][0][2]["max_results"] == 25
    assert seen["held"] == "0.125"                  # 25 本 × 0.005 を投げる前に押さえる
    [row] = owned_rows()
    assert (row["state"], row["limit"], row["count"], row["usd"]) == ("settled", 25, 3, "0.015")
    assert b.read()["reservations"] == {}           # 本人確認の 1 回 0.010 の台帳は使わない
    assert adapter._owned["usd"] == "0.015"
    assert b.posts_status(ACCOUNT)["today"] == 0    # 1 日の本数（他人の投稿）には数えない


def test_recent_posts_unreadable_keeps_the_upper_bound(x_account, fake):
    fake["routes"]["/2/users/123/tweets"] = urllib.error.URLError("boom")
    with pytest.raises(base.AdapterError, match="x_posts_failed"):
        adapter_for().recent_posts(limit=10)
    [row] = owned_rows()
    assert (row["state"], row["usd"]) == ("uncertain", "0.050")


def test_recent_posts_http_refusal_settles_zero(x_account, fake):
    fake["routes"]["/2/users/123/tweets"] = urllib.error.HTTPError("u", 500, "no", {}, None)
    with pytest.raises(base.AdapterError, match="x_posts_http_500"):
        adapter_for().recent_posts(limit=10)
    [row] = owned_rows()
    assert (row["state"], row["count"]) == ("settled", 0)


def test_recent_posts_budget_refusal_is_before_the_request(x_account, fake):
    b.configure("0.020", by="operator")   # 5 本（0.025）が入らない
    with pytest.raises(b.BudgetError, match="budget_exhausted"):
        adapter_for().recent_posts(limit=5)
    assert fake["calls"] == [] and owned_rows() == []


def test_user_read_no_longer_covers_the_timeline_path(x_account):
    assert b.READ_PATH.fullmatch("/2/users/me") and b.READ_PATH.fullmatch("/2/users/by/username/kanto")
    assert not b.READ_PATH.fullmatch("/2/users/123/tweets")


# ----- 柵 4: where・thread の金額の行は投稿者の分も入った金額だと言う ---------------------------

def test_cost_line_says_the_amount_includes_authors():
    line = where_cli.read_cost_line({"posts": 10, "usd_estimate": "0.070", "includes_usd_estimate": "0.020",
                                     "today": 10, "daily_cap": 60, "month_used_usd": "0.070", "cap_usd": "15"})
    assert line == ("X の読み取り: 今回 10 本・約 $0.070（投稿 $0.050＋投稿者の分 $0.020）"
                    "・今日 10/60 本・今月 $0.070/$15")
    # 内訳の無い古い形は今までどおり。
    assert where_cli.read_cost_line({"posts": 1, "usd_estimate": "0.005"}).startswith("X の読み取り: 今回 1 本・約 $0.005・")
