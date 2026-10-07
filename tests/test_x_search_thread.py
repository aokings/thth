"""X の検索（where）と枝の読み取り（thread）・有償の読み取りの使用ルール（設計 2026-10-08）。

**本物の api.x.com は叩かない**——`thth.adapters.x.request` を偽物に差し替える。
時刻は conftest の固定（2026-09-09 10:00 JST）。
"""
from __future__ import annotations

import json
import urllib.error
from decimal import Decimal

import pytest

from thth import accounts as accounts_mod
from thth import budget_x as b
from thth import cli, server_files
from thth.adapters import base
from thth.adapters import x as x_adapter

ACCOUNT = "kanto-x"
TOKEN = "SYNTHETIC-X-TOKEN-0123456789"


@pytest.fixture
def x_account(tmp_path, isolated_account_factory):
    token = tmp_path / "x.token"
    token.write_text(json.dumps({"access_token": TOKEN, "user_id": "123", "username": "kanto"}),
                     encoding="utf-8")
    isolated_account_factory(ACCOUNT, media="x", handle="kanto", project="kanto",
                             token=str(token))
    b.configure("15", by="operator")
    return ACCOUNT


def tweet(identifier, *, author="u1", conversation=None, replied=None, text="本文"):
    row = {"id": identifier, "text": text, "author_id": author,
           "created_at": "2026-09-09T00:30:00.000Z",
           "conversation_id": conversation or identifier}
    if replied:
        row["referenced_tweets"] = [{"type": "replied_to", "id": replied}]
    return row


USERS = {"users": [{"id": "u1", "username": "alice"}, {"id": "u2", "username": "bob"}]}


@pytest.fixture
def fake(monkeypatch):
    """`x.request` の偽物。`state['routes']` に path → 応答（dict か例外か関数）。"""
    state = {"calls": [], "routes": {}}

    def request(adapter, method, path, *, query=None, **kwargs):
        state["calls"].append((method, path, dict(query or {})))
        answer = state["routes"].get(path)
        if callable(answer):
            answer = answer(query or {})
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(path, 404, "nf", {}, None)
        return 200, answer
    monkeypatch.setattr(x_adapter, "request", request)
    return state


def adapter_for(account=ACCOUNT):
    cfg = accounts_mod.load_account(account)
    from thth import adapters
    return adapters.make_adapter(cfg, accounts_mod.load_token(cfg))


def posts_rows():
    return b.read_posts()["reservations"].values()


# ----- 予約と確定の算術 -------------------------------------------------------

def test_reserve_holds_the_upper_bound_and_settles_with_the_returned_count(x_account):
    with b.posts_read(ACCOUNT, 10) as slot:
        assert b.report()["held_usd"] == "0.050"
        assert b.posts_status(ACCOUNT)["today"] == 10
        slot.dispatch()
        slot.settle(7)
    report = b.report()
    assert report["spent_estimate_usd"] == "0.035" and report["held_usd"] == "0"
    assert report["remaining_usd"] == "14.965"
    assert report["posts_read"]["today_by_account"] == {ACCOUNT: {"posts": 7, "daily_cap": 60}}
    assert report["posts_read"]["price"]["post_read_usd"] == "0.005"
    assert report["months"]["2026-09"] == {"spent_estimate_usd": "0.035", "held_usd": "0"}
    # 本人確認の台帳の形は変えない（別のファイル）。
    assert b.read()["reservations"] == {}
    with server_files.directory(b.folder(), private=True) as fd:
        names = set(__import__("os").listdir(fd))
    assert b.POSTS_FILE in names and "budget_x_posts.json" not in names


def test_not_dispatched_is_released(x_account):
    with b.posts_read(ACCOUNT, 5):
        pass
    assert [row["state"] for row in posts_rows()] == ["released"]
    assert b.report()["spent_estimate_usd"] == "0" and b.posts_status(ACCOUNT)["today"] == 0


def test_daily_cap_refuses_before_the_request(x_account):
    assert cli.main(["account", "set", ACCOUNT, "x_daily_reads", "15", "--by", "operator"]) == 0
    with b.posts_read(ACCOUNT, 10) as slot:
        slot.dispatch(); slot.settle(10)
    with pytest.raises(b.BudgetError, match="x_daily_read_cap"):
        with b.posts_read(ACCOUNT, 10):
            pytest.fail("上限を超える予約が通った")
    with b.posts_read(ACCOUNT, 5) as slot:   # ちょうど 15 本までは通る
        slot.dispatch(); slot.settle(5)
    assert b.posts_status(ACCOUNT)["today"] == 15


def test_monthly_cap_counts_user_reads_and_post_reads_together(x_account):
    b.configure("0.06", by="operator")
    with b.user_read(ACCOUNT):
        b.before_post(); b.before_get("/2/users/me"); b.observed({"data": {"id": "1"}})
    with b.posts_read(ACCOUNT, 10) as slot:   # 0.010 + 0.050 = 0.060
        slot.dispatch(); slot.settle(10)
    with pytest.raises(b.BudgetError, match="budget_exhausted"):
        with b.posts_read(ACCOUNT, 1):
            pytest.fail("月の上限を超えた")
    with pytest.raises(b.BudgetError, match="budget_exhausted"):
        with b.user_read(ACCOUNT):
            pytest.fail("本人確認が他人の投稿の読み取りを数えていない")
    assert b.report()["spent_estimate_usd"] == "0.060" and b.report()["read_refusal"] == "budget_exhausted"


def test_failure_after_dispatch_keeps_the_upper_bound_as_uncertain(x_account, fake):
    fake["routes"]["/2/tweets/search/recent"] = urllib.error.URLError("boom " + TOKEN)
    with pytest.raises(base.AdapterError) as caught:
        adapter_for().keyword_search("コーヒー", limit=10)
    assert TOKEN not in str(caught.value)
    assert [row["state"] for row in posts_rows()] == ["uncertain"]
    assert b.report()["spent_estimate_usd"] == "0.050"
    assert b.report()["posts_read"]["month"]["uncertain"] == 1
    assert b.posts_status(ACCOUNT)["today"] == 10


def test_an_http_refusal_settles_at_zero(x_account, fake):
    fake["routes"]["/2/tweets/search/recent"] = urllib.error.HTTPError("u", 403, "no", {}, None)
    with pytest.raises(base.PermissionMissing):
        adapter_for().keyword_search("コーヒー")
    assert [(row["state"], row["count"]) for row in posts_rows()] == [("settled", 0)]


def test_the_posts_ledger_is_strictly_validated(x_account):
    with b.posts_read(ACCOUNT, 2) as slot:
        slot.dispatch(); slot.settle(2)
    with b.locked() as fd:
        value = b._read_posts(fd)
        row = next(iter(value["reservations"].values()))
        row["usd"] = "0.001"
        server_files.replace_at(fd, b.POSTS_FILE, server_files.encode(value), private=True)
    with pytest.raises(b.BudgetError, match="budget_unreadable"):
        b.report()
    with pytest.raises(b.BudgetError, match="budget_unreadable"):
        with b.posts_read(ACCOUNT, 1):
            pass


# ----- adapter の口 -----------------------------------------------------------

def test_keyword_search_row_shape_and_charge(x_account, fake):
    data = [tweet(str(1900000000000000000 + i), author="u1" if i % 2 else "u2",
                  conversation="1800000000000000000", replied="1800000000000000000")
            for i in range(10)]
    fake["routes"]["/2/tweets/search/recent"] = {"data": data, "includes": USERS,
                                                 "meta": {"result_count": 10}}
    adapter = adapter_for()
    rows = adapter.keyword_search("コーヒー", search_type="RECENT", limit=3)
    assert len(rows) == 3
    method, path, query = fake["calls"][0]
    assert (method, path) == ("GET", "/2/tweets/search/recent")
    assert query["query"] == "コーヒー" and query["max_results"] == 10 and query["sort_order"] == "recency"
    assert query["tweet.fields"] == "created_at,author_id,conversation_id,referenced_tweets"
    assert query["expansions"] == "author_id" and query["user.fields"] == "username"
    row = rows[1]
    assert row == {
        "message_id": "1900000000000000001", "username": "alice", "text": "本文",
        "timestamp": "2026-09-09T00:30:00.000Z",
        "replied_to": {"id": "1800000000000000000"}, "root_post": {"id": "1800000000000000000"},
        "medium": "x", "author_key": base.author_key("x", "alice"), "reply_deadline": None,
        "permalink": "https://x.com/alice/status/1900000000000000001"}
    # 課金は応答に入っていた本数（画面に出す 3 本ではない）。
    cost = adapter.read_cost()
    assert cost["posts"] == 10 and cost["usd_estimate"] == "0.050" and cost["today"] == 10
    assert cost["daily_cap"] == 60 and cost["cap_usd"] == "15" and cost["month_used_usd"] == "0.050"


def test_search_over_25_is_refused_without_request_or_reservation(x_account, fake):
    with pytest.raises(base.AdapterError, match="25"):
        adapter_for().keyword_search("コーヒー", limit=26)
    assert fake["calls"] == [] and list(posts_rows()) == []


def test_budget_refusal_becomes_an_adapter_error(x_account, fake):
    b.configure("0", by="operator")
    with pytest.raises(base.AdapterError, match="budget_exhausted"):
        adapter_for().keyword_search("コーヒー")
    assert fake["calls"] == []


ROOT = "1800000000000000000"


def conversation_routes(fake, n, *, next_token=True):
    fake["routes"]["/2/tweets/" + ROOT] = {"data": tweet(ROOT, author="u2"), "includes": USERS}
    replies = [tweet(ROOT, author="u2")]   # 根そのものも検索に出る
    replies += [tweet(str(1900000000000000000 + i), conversation=ROOT,
                      replied=ROOT if i == 0 else "1900000000000000000")
                for i in range(n - 1)]
    meta = {"result_count": len(replies)}
    if next_token:
        meta["next_token"] = "more"
    fake["routes"]["/2/tweets/search/recent"] = {"data": replies, "includes": USERS, "meta": meta}


def test_conversation_truncates_at_50_and_maps_replied_to(x_account, fake):
    conversation_routes(fake, 50)
    adapter = adapter_for()
    root = adapter.fetch_post(ROOT)
    rows = adapter.conversation(ROOT)
    # fetch_post は同じ実体の中で 1 回だけ（根を 2 度買わない）。
    assert [c[1] for c in fake["calls"]] == ["/2/tweets/" + ROOT, "/2/tweets/search/recent"]
    assert fake["calls"][1][2]["query"] == "conversation_id:" + ROOT
    assert fake["calls"][1][2]["max_results"] == 50
    assert root["root_post"] == {"id": ROOT} and root["replied_to"] is None
    assert ROOT not in [row["message_id"] for row in rows] and len(rows) == 49
    assert rows[0]["replied_to"] == {"id": ROOT}
    assert rows[1]["replied_to"] == {"id": "1900000000000000000"}
    cost = adapter.read_cost()
    assert cost["truncated"] is True and cost["posts"] == 51 and cost["usd_estimate"] == "0.255"


def test_collect_does_not_read_x_branches(x_account, fake):
    from thth import collect
    with pytest.raises(NotImplementedError, match="paid_read_not_automatic"):
        collect._fetch_replies(adapter_for(), accounts_mod.load_account(ACCOUNT), ROOT, None, set)
    assert fake["calls"] == []


# ----- where・thread の端から端まで ------------------------------------------

def test_where_on_x_shows_the_cost_line(x_account, fake, capsys):
    data = [tweet(str(1900000000000000000 + i)) for i in range(10)]
    fake["routes"]["/2/tweets/search/recent"] = {"data": data, "includes": USERS}
    assert cli.main(["where", ACCOUNT, "コーヒー", "--max-per-author", "2"]) == 0
    out = capsys.readouterr().out
    assert "X の読み取り: 今回 10 本・約 $0.050・今日 10/60 本・今月 $0.050/$15" in out
    assert fake["calls"][0][2]["max_results"] == 10 and fake["calls"][0][2]["sort_order"] == "relevancy"
    assert cli.main(["where", ACCOUNT, "コーヒー", "--recent", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    node = result["by_account"][ACCOUNT]
    assert node["x_read_cost"]["posts"] == 10 and node["x_read_cost"]["today"] == 20
    assert len(node["by_word"]["コーヒー"]["posts"]) == 10
    assert fake["calls"][1][2]["sort_order"] == "recency"


def test_where_refuses_more_than_25_on_x(x_account, fake, capsys):
    assert cli.main(["where", ACCOUNT, "コーヒー", "--limit", "30"]) == 2
    assert "25" in capsys.readouterr().err and fake["calls"] == []


def test_where_daily_cap_is_said_per_word(x_account, fake, capsys):
    assert cli.main(["account", "set", ACCOUNT, "x_daily_reads", "5", "--by", "operator"]) == 0
    capsys.readouterr()
    assert cli.main(["where", ACCOUNT, "コーヒー", "--json"]) == 0
    node = json.loads(capsys.readouterr().out)["by_account"][ACCOUNT]
    assert any("x_daily_read_cap" in line for line in node["cannot_say"])
    assert fake["calls"] == [] and node["x_read_cost"]["posts"] == 0


def test_thread_on_x_shows_ids_truncation_and_cost(x_account, fake, capsys):
    conversation_routes(fake, 3, next_token=False)
    assert cli.main(["thread", ACCOUNT, ROOT]) == 0
    out = capsys.readouterr().out
    assert f"id={ROOT}" in out and "id=1900000000000000000" in out
    assert "X の読み取り: 今回 4 本・約 $0.020・今日 4/60 本" in out
    conversation_routes(fake, 50)
    assert cli.main(["thread", ACCOUNT, ROOT, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["truncated"] is True and result["x_read_cost"]["truncated"] is True
    assert result["provenance"]["source"].startswith("x:")
    assert result["messages"][0]["depth"] == 1


# ----- 設定 -------------------------------------------------------------------

def test_account_set_x_daily_reads(x_account, isolated_account_factory, capsys):
    assert cli.main(["account", "set", ACCOUNT, "x_daily_reads", "30", "--by", "operator"]) == 0
    assert accounts_mod.load_account(ACCOUNT)["x_daily_reads"] == 30
    assert b.daily_cap(ACCOUNT) == 30
    assert cli.main(["account", "set", ACCOUNT, "x_daily_reads", "-1", "--by", "operator"]) == 2
    assert cli.main(["account", "status", ACCOUNT]) == 0
    assert "x_daily_reads）: 30 本" in capsys.readouterr().out
    isolated_account_factory("kanto-threads", media="threads", project="kanto")
    assert cli.main(["account", "set", "kanto-threads", "x_daily_reads", "30", "--by", "operator"]) == 2
    assert "x_daily_reads" not in accounts_mod.load_account("kanto-threads")
    from thth import account_settings
    assert "x_daily_reads" not in account_settings.current(accounts_mod.load_account("kanto-threads"))


def test_unreadable_daily_cap_fails_closed(x_account, isolated_account_factory):
    isolated_account_factory("kanto-x2", media="x", project="kanto", x_daily_reads="lots")
    with pytest.raises(b.BudgetError, match="x_daily_read_cap_unreadable"):
        with b.posts_read("kanto-x2", 1):
            pass


def test_morning_does_not_search_x(x_account):
    from thth import morning
    assert Decimal(b.report()["cap_usd"]) == 15
    cell = morning._world(ACCOUNT, accounts_mod.load_account(ACCOUNT) | {"watch_words": ["コーヒー"]},
                          None, lambda *a: pytest.fail("毎朝の一枚が X を検索した"))
    assert cell["cannot_say"] == "not_supported"


def test_timer_collect_skips_x_posts_quietly(tmp_path, isolated_account_factory):
    # timer の採取は X の投稿の数も返信も読まず、誤りも積まない（10 分ごとに知らせが鳴らない）。
    import datetime
    from thth import accounts as accounts_mod, collect as collect_mod, jst, sent as sent_mod
    a = isolated_account_factory(name="kx", repo_dir=str(tmp_path / "repos" / "_none"),
                                 media="x", handle="kx", production=True, scheduled=False)
    sent_mod.write(accounts_mod.state_dir_for(a["name"]), post_id="1234567890", text="本文",
                   body_hash="h", sent_at="2026-10-08T10:00:00+09:00")

    class Paid:
        PAID_READS = True
        @classmethod
        def capabilities(cls): return set()
        def insights(self, post_id): raise AssertionError("insights を読んだ")
        def conversation(self, post_id, since=None): raise AssertionError("返信を読んだ")

    now = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=jst.JST)
    result = collect_mod.collect_once(a["name"], adapter=Paid(), now=now, log=lambda _l: None)
    assert result["errors"] == [] and result["posts"] == 1
