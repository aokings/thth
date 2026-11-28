"""5.33.0 X の expansions で付く物（`includes`）も読取予算で数える（調べ docs/調べ_X_expansionsの数え方_2026-11-28.md）。

X の知らせ（11/6）: 11/22 から `includes` に入る物は直に引いたときと同じ 1 件ごとの単価
（user 0.010・media 0.005）。資料で確かめられない所（24 時間の重複の除き・`GET /2/tweets` の
自分の投稿の media の単価）は多めに倒す。**本物の api.x.com は叩かない**（`x.request` を偽物に）。
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from thth import budget_x as b
from thth import server_files
from thth.adapters import base
from thth.adapters import x as x_adapter
from tests.test_x_search_thread import (  # noqa: F401  （fixture をこのファイルでも使う）
    ACCOUNT, USERS, adapter_for, fake, tweet, x_account)


def exp_rows():
    return list(b.read_posts(b.EXPANSIONS)["reservations"].values())


def search_body(n, users):
    return {"data": [tweet(str(1900000000000000000 + i), author=users[i % len(users)]["id"]) for i in range(n)],
            "includes": {"users": users}, "meta": {"result_count": n}}


# ----- 柵 1: 頼む expansion ごとの上限（知らない expansion は頼めない） -------------------

def test_expansion_units_are_the_upper_bound_per_request():
    assert b.expansion_units({"expansions": "author_id"}, 10) == 20          # user 2 単位 × 1 人 × 10 本
    assert b.expansion_units({"expansions": "attachments.media_keys"}, 100) == 400   # 1 本に 4 枚まで
    assert b.expansion_units({"tweet.fields": "id"}, 10) == 0
    assert b.EXPANSION_UNIT_PRICE * b.INCLUDE_UNITS["users"] == b.PRICE      # User: Read 0.010
    assert b.EXPANSION_UNIT_PRICE * b.INCLUDE_UNITS["media"] == b.POSTS_PRICE  # Media: Read 0.005
    with pytest.raises(b.BudgetError, match="expansion_not_priced"):
        b.expansion_units({"expansions": "author_id,referenced_tweets.id"}, 10)


def test_unpriced_expansion_is_refused_before_request_and_reservation(x_account, fake, monkeypatch):
    monkeypatch.setattr(x_adapter.XAdapter, "READ_FIELDS",
                        {**x_adapter.XAdapter.READ_FIELDS, "expansions": "author_id,referenced_tweets.id"})
    with pytest.raises(base.AdapterError, match="expansion_not_priced"):
        adapter_for().keyword_search("コーヒー")
    assert fake["calls"] == [] and exp_rows() == [] and list(b.read_posts()["reservations"].values()) == []


def test_included_units_counts_every_object_and_unknown_keys_high():
    assert b.included_units({"data": []}) == 0
    assert b.included_units({"includes": {"users": [{}, {}], "media": [{}]}}) == 5
    assert b.included_units({"includes": {"somethingnew": [{}]}}) == b.INCLUDE_UNKNOWN_UNITS
    assert b.included_units({"includes": {"users": {}}}) is None
    assert b.included_units({"includes": []}) is None


# ----- 柵 2: 押さえは投稿＋付く物・確定は返った物・読めなければ上限のまま ------------------

def test_search_reserves_posts_and_authors_then_settles_returned_users(x_account, fake):
    seen = {}

    def answer(query):
        seen["held"] = b.report()["held_usd"]
        return search_body(10, [{"id": "u%d" % i, "username": "n%d" % i} for i in range(3)])
    fake["routes"]["/2/tweets/search/recent"] = answer
    adapter_for().keyword_search("コーヒー", limit=10)
    # 発射の前の押さえ: 投稿 10 本 0.050＋投稿者 10 人 0.100。
    assert seen["held"] == "0.150"
    [row] = exp_rows()
    assert row["state"] == "settled" and row["limit"] == 20 and row["count"] == 6 and row["usd"] == "0.030"
    assert row["price_version"] == "2026-11-22-expansions"
    whole = b.report()
    assert whole["spent_estimate_usd"] == "0.080" and whole["held_usd"] == "0"
    assert whole["expansion_read"]["month"]["spent_estimate_usd"] == "0.030"
    assert whole["months"]["2026-09"] == {"spent_estimate_usd": "0.080", "held_usd": "0"}
    assert any("expansions で付く物" in line for line in b.cost_lines(whole))
    # 1 日の本数（x_daily_reads）は投稿だけ。付く物は数えない。
    assert b.posts_status(ACCOUNT)["today"] == 10


def test_cap_refuses_when_posts_fit_but_authors_do_not(x_account, fake):
    b.configure("0.050", by="operator")   # 投稿 10 本ちょうど。投稿者の分が入らない
    fake["routes"]["/2/tweets/search/recent"] = search_body(10, USERS["users"])
    with pytest.raises(base.AdapterError, match="budget_exhausted"):
        adapter_for().keyword_search("コーヒー")
    assert fake["calls"] == [] and exp_rows() == []


def test_unreadable_response_keeps_both_upper_bounds_as_uncertain(x_account, fake):
    fake["routes"]["/2/tweets/search/recent"] = urllib.error.URLError("boom")
    with pytest.raises(base.AdapterError):
        adapter_for().keyword_search("コーヒー")
    [row] = exp_rows()
    assert row["state"] == "uncertain" and row["usd"] == "0.100"
    assert b.report()["spent_estimate_usd"] == "0.150"


def test_unreadable_includes_shape_stays_uncertain(x_account, fake):
    body = search_body(10, USERS["users"]);body["includes"] = {"users": "not-a-list"}
    fake["routes"]["/2/tweets/search/recent"] = body
    adapter_for().keyword_search("コーヒー")
    [row] = exp_rows()
    assert row["state"] == "uncertain" and row["usd"] == "0.100"


def test_more_includes_than_reserved_is_not_settled_low(x_account, fake):
    # 1 本の応答に投稿者 2 人（本物では起きない形）: 押さえた 1 人分より少なく確定しない。
    fake["routes"]["/2/tweets/1800000000000000000"] = {"data": tweet("1800000000000000000"), "includes": USERS}
    adapter_for().fetch_post("1800000000000000000")
    [row] = exp_rows()
    assert row["state"] == "uncertain" and row["usd"] == "0.010"


def test_http_refusal_settles_includes_at_zero(x_account, fake):
    fake["routes"]["/2/tweets/search/recent"] = urllib.error.HTTPError("u", 500, "no", {}, None)
    with pytest.raises(base.AdapterError):
        adapter_for().keyword_search("コーヒー")
    [row] = exp_rows()
    assert (row["state"], row["count"], row["usd"]) == ("settled", 0, "0.000")


def test_reservation_without_expansions_writes_no_expansion_row(x_account):
    with b.posts_read(ACCOUNT, 5) as slot:
        slot.dispatch(); slot.settle(5); slot.settle_includes(3)   # 頼んでいない読みでは何もしない
    assert exp_rows() == []


# ----- 柵 3: 自分の投稿の数字の media は 0.005（Owned の 0.001 を当てにしない） ----------------

def test_owned_private_media_is_counted_at_the_standard_media_rate(x_account, fake):
    def answer(query):
        ids = query["ids"].split(",")
        body = {"data": [{"id": i, "author_id": "123", "created_at": "2026-09-09T00:00:00.000Z",
                          "public_metrics": {"impression_count": 1}} for i in ids]}
        if "expansions" in query:
            body["includes"] = {"media": [{"media_key": "3_1", "type": "video"},
                                          {"media_key": "3_2", "type": "photo"}]}
        return body
    fake["routes"]["/2/tweets"] = answer
    result = adapter_for().owned_metrics(["1900000000000000001", "1900000000000000002"],
                                         ages={"1900000000000000001": 1.0})
    [row] = exp_rows()   # 公開の数だけの要求（expansions 無し）は付く物の予約を作らない
    assert row["limit"] == 4 and row["count"] == 2 and row["usd"] == "0.010"
    assert result["usd_estimate"] == "0.012"   # 投稿 2 本 × 0.001 ＋ media 2 件 × 0.005


# ----- 柵 4: 古い版の行（VM に残る行）がそのまま読める ---------------------------------------

def _write(name, value):
    with server_files.directory(b.folder(), create=True, private=True) as fd:
        server_files.replace_at(fd, name, server_files.encode(value), private=True)


def test_ledgers_written_before_expansions_are_still_readable(x_account, fake):
    at = "2026-09-09T00:30:00+00:00"
    old = {"account": ACCOUNT, "state": "settled", "limit": 10, "count": 7, "usd": "0.035",
           "price_version": "2026-10-08-posts-read", "reservation_month": "2026-09",
           "day_jst": "2026-09-09", "at": at, "get_at": at}
    _write(b.POSTS_FILE, {"schema_version": 1, "reservations": {"a" * 32: old}})
    _write(b.OWNED_FILE, {"schema_version": 1, "reservations": {"b" * 32: dict(
        old, usd="0.007", price_version="2026-10-12-owned-read")}})
    _write(b.ARCHIVE_FILE, {"schema_version": 1, "reservations": {"c" * 32: dict(
        old, price_version="2026-10-21-posts-read-archive")}})
    value = b.read()
    value["reservations"]["d" * 32] = dict(account=ACCOUNT, state="settled", usd="0.010",
        price_version="2026-09-20-user-read", policy_version=value["policy"]["version"],
        reservation_month="2026-09", dispatch_month="2026-09", estimated_charge_month="2026-09",
        at=at, post_at=at, get_at=at)
    _write("budget_x.json", value)
    whole = b.report()
    assert whole["spent_estimate_usd"] == "0.087" and whole["expansion_read"]["month"]["requests"] == 0
    # 古い行のあとに新しい読みが足せる。
    fake["routes"]["/2/tweets/search/recent"] = search_body(10, USERS["users"])
    adapter_for().keyword_search("コーヒー")
    assert b.report()["spent_estimate_usd"] == "0.157"   # 0.087 ＋ 投稿 0.050 ＋ 投稿者 2 人 0.020


def test_expansion_ledger_is_strictly_validated(x_account, fake):
    fake["routes"]["/2/tweets/search/recent"] = search_body(10, USERS["users"])
    adapter_for().keyword_search("コーヒー")
    with b.locked() as fd:
        value = b._read_posts(fd, b.EXPANSIONS)
        next(iter(value["reservations"].values()))["usd"] = "0.001"
        server_files.replace_at(fd, b.EXPANSION_FILE, server_files.encode(value), private=True)
    with pytest.raises(b.BudgetError, match="budget_unreadable"):
        b.report()


# ----- 柵 5: 要らない欄は頼まない（user.fields は値段を変えないが、使わない所では外す） ---------

def test_archive_asks_only_username_and_search_keeps_author_metrics(x_account, fake):
    assert x_adapter.XAdapter.ARCHIVE_READ_FIELDS["user.fields"] == "username"
    assert x_adapter.XAdapter.ARCHIVE_READ_FIELDS["expansions"] == "author_id"
    # 検索・枝・リストは投稿者のフォロワー数・投稿数を画面と --json に出す（threads_read_cli.post_rows）。
    assert x_adapter.XAdapter.READ_FIELDS["user.fields"] == "username,public_metrics"
    fake["routes"]["/2/tweets/search/all"] = search_body(10, USERS["users"])
    from thth import account_settings
    account_settings.change(ACCOUNT, "x_archive_daily_reads", 100, by="operator")
    adapter = adapter_for()
    adapter.archive_search("q", per_call=10, max_total=10, start_time="2006-03-21T00:00:01Z")
    [(_m, path, query)] = fake["calls"]
    assert path == "/2/tweets/search/all" and query["user.fields"] == "username"
    assert adapter.archive_read_cost()["usd_estimate"] == "0.070"
    assert json.dumps(query).count("public_metrics") == 0
