"""4.5.0: X の全期間の検索（`thth archive`・`GET /2/tweets/search/all`）と件数（`--count-only`・
`GET /2/tweets/counts/all`）の柵。

**本物の api.x.com は叩かない**——`thth.adapters.x.request` を偽物に差し替える。縛ること:
- 予算が 0 なら叩かない（月の上限・全期間の 1 日の本数の既定 0）。
- 上限を超える指定は要求の前に断る（予約も残さない）。
- 課金は返った本数（押さえた本数ではない）。
- next_token を辿っても総本数の上限で止まる。
- 件数の口は 1 回 0.010 USD で数える。
- timer の道（collect・morning など）から呼べない。
"""
from __future__ import annotations

import argparse
import json
import urllib.error

import pytest

from thth import account_settings
from thth import budget_x as b
from thth import archive_cli, x_archive
from thth.adapters import x as x_adapter
from tests.test_x_community import ACCOUNT, _account  # noqa: F401


@pytest.fixture
def x_account(tmp_path, isolated_account_factory, monkeypatch):
    _account(tmp_path, isolated_account_factory)
    b.configure("15", by="operator")
    account_settings.change(ACCOUNT, "x_archive_daily_reads", 1000, by="operator")
    monkeypatch.setattr(x_adapter.XAdapter, "ARCHIVE_PAGE_PAUSE_SECONDS", 0)
    return ACCOUNT


def _posts(n, *, start=0):
    return {"data": [{"id": str(1000000000000000000 + start + i), "author_id": "u1",
                      "created_at": "2019-05-20T03:04:05.000Z", "text": f"マリーゴールド {start + i}"}
                     for i in range(n)],
            "includes": {"users": [{"id": "u1", "username": "aimyon36"}]}}


@pytest.fixture
def fake(monkeypatch):
    """`answer(path, query, index)` が応答を返す。例外なら上げる。呼ばれた要求は `calls` に。"""
    state = {"calls": [], "answer": None}

    def request(adapter, method, path, *, query=None, **kwargs):
        state["calls"].append({"method": method, "path": path, "query": dict(query or {})})
        value = state["answer"](path, dict(query or {}), len(state["calls"]) - 1)
        if isinstance(value, BaseException):
            raise value
        return 200, value
    monkeypatch.setattr(x_adapter, "request", request)
    return state


def run(capsys, *, count_only=False, **kw):
    values = dict(account=ACCOUNT, handle="@aimyon36", title="マリーゴールド", include_retweets=False,
                  query=None, since=None, until=None, per_call=None, max_total=None, max_pages=None,
                  sort="recency", count_only=count_only, granularity="day", json=True)
    values.update(kw)
    rc = archive_cli.cmd_archive(argparse.Namespace(**values))
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if out.out.strip() else None), out


def rows(kind):
    return list(b.read_posts(kind)["reservations"].values())


# ----- 柵 1: 予算が 0 なら叩かない ----------------------------------------------

def test_zero_monthly_budget_never_calls(x_account, fake, capsys):
    b.configure("0", by="operator")
    fake["answer"] = lambda *a: _posts(10)
    rc, payload, _ = run(capsys)
    assert rc == 1 and payload["error"] == "budget_exhausted"
    assert fake["calls"] == [] and rows(b.ARCHIVE) == []


def test_zero_monthly_budget_never_counts(x_account, fake, capsys):
    b.configure("0", by="operator")
    fake["answer"] = lambda *a: {"data": [], "meta": {"total_post_count": 0}}
    rc, payload, _ = run(capsys, count_only=True, since="2026-08-20")
    assert rc == 1 and payload["error"] == "budget_exhausted"
    assert fake["calls"] == [] and rows(b.COUNTS_ALL) == []


def test_archive_daily_cap_defaults_to_zero(x_account, fake, capsys):
    account_settings.change(ACCOUNT, "x_archive_daily_reads", 0, by="operator")
    fake["answer"] = lambda *a: _posts(10)
    rc, payload, _ = run(capsys)
    assert rc == 1 and payload["error"] == "x_archive_daily_read_cap"
    assert fake["calls"] == []


# ----- 柵 2: 上限を超える指定は要求の前に断る --------------------------------------

@pytest.mark.parametrize("kw", [
    {"per_call": 101}, {"per_call": 9}, {"max_total": 501}, {"max_total": 5},
    {"per_call": 50, "max_total": 20}, {"max_pages": 51}, {"max_pages": 0},
])
def test_out_of_range_is_refused_before_request(x_account, fake, capsys, kw):
    fake["answer"] = lambda *a: pytest.fail("範囲外の指定で X を叩いた")
    rc, payload, _ = run(capsys, **kw)
    assert rc == 2 and payload["error"] == "x_archive_limit"
    assert fake["calls"] == [] and rows(b.ARCHIVE) == []


def test_count_window_needing_more_pages_is_refused_before_request(x_account, fake, capsys):
    fake["answer"] = lambda *a: pytest.fail("ページの足りない件数で X を叩いた")
    rc, payload, _ = run(capsys, count_only=True)          # 2006 年から・既定 1 ページ
    assert rc == 2 and payload["error"] == "x_archive_limit"
    assert payload["pages_needed"] > 200 and payload["usd_estimate_if_run"].startswith("2.")
    assert fake["calls"] == [] and rows(b.COUNTS_ALL) == []


def test_adapter_refuses_out_of_range_too(x_account, fake):
    adapter = x_adapter.XAdapter(access_token="t")
    adapter.auth_account = ACCOUNT
    with pytest.raises(x_adapter.AdapterError, match="x_archive_limit"):
        adapter.archive_search("q", per_call=500, max_total=500, start_time="2006-03-21T00:00:01Z")
    assert fake["calls"] == []


# ----- 柵 3: 課金は返った本数 -----------------------------------------------------

def test_charge_is_returned_posts_not_reserved(x_account, fake, capsys):
    fake["answer"] = lambda *a: _posts(7)
    rc, payload, _ = run(capsys, per_call=50, max_total=50)
    assert rc == 0
    [call] = fake["calls"]
    assert call["path"] == "/2/tweets/search/all"
    assert call["query"]["max_results"] == 50
    assert call["query"]["query"] == 'from:aimyon36 "マリーゴールド" -is:retweet'
    assert call["query"]["start_time"] == "2006-03-21T00:00:01Z" and "end_time" not in call["query"]
    [ledger] = rows(b.ARCHIVE)
    assert ledger["state"] == "settled" and ledger["limit"] == 50 and ledger["count"] == 7
    assert ledger["usd"] == "0.035"
    cost = payload["x_read_cost"]
    assert cost["posts"] == 7 and cost["usd_estimate"] == "0.035" and cost["requests"] == 1
    assert cost["today"] == 7 and cost["daily_cap"] == 1000
    report = b.report()
    assert report["spent_estimate_usd"] == "0.035"
    assert report["archive_read"]["month"]["posts"] == 7
    # 今の口座の 1 日の本数（x_daily_reads）には数えない。
    assert b.posts_status(ACCOUNT)["today"] == 0
    first = payload["posts"][0]
    assert first == {"post_id": "1000000000000000000", "url": "https://x.com/aimyon36/status/1000000000000000000",
                     "created_at": "2019-05-20T03:04:05.000Z", "text": "マリーゴールド 0",
                     "author_username": "aimyon36", "conversation_id": None}


def test_unreadable_response_stays_counted_and_is_not_retried(x_account, fake, capsys):
    fake["answer"] = lambda *a: urllib.error.URLError("reset")
    rc, payload, _ = run(capsys, per_call=20, max_total=20)
    assert rc == 1
    assert len(fake["calls"]) == 1
    [ledger] = rows(b.ARCHIVE)
    assert ledger["state"] == "uncertain" and ledger["usd"] == "0.100"


# ----- 柵 4: next_token を辿っても総本数の上限で止まる --------------------------------

def _endless(path, query, index):
    value = _posts(query["max_results"], start=index * 1000)
    value["meta"] = {"result_count": query["max_results"], "next_token": f"t{index + 1}"}
    return value


def test_paging_stops_at_max_total(x_account, fake, capsys):
    fake["answer"] = _endless
    rc, payload, _ = run(capsys, per_call=10, max_total=30)
    assert rc == 0
    assert len(fake["calls"]) == 3
    assert [c["query"].get("next_token") for c in fake["calls"]] == [None, "t1", "t2"]
    assert payload["n"] == 30 and payload["more"] is True
    assert sum(row["count"] for row in rows(b.ARCHIVE)) == 30


def test_paging_never_asks_beyond_remaining(x_account, fake, capsys):
    # 残り 5 本は X に頼めない（下限 10）ので、2 ページで止める。課金は 20 本を超えない。
    fake["answer"] = _endless
    rc, payload, _ = run(capsys, per_call=10, max_total=25)
    assert rc == 0 and len(fake["calls"]) == 2
    assert sum(row["limit"] for row in rows(b.ARCHIVE)) == 20 and payload["more"] is True


def test_paging_stops_at_max_pages_on_empty_pages(x_account, fake, capsys):
    def empty(path, query, index):
        return {"meta": {"result_count": 0, "next_token": f"t{index + 1}"}}
    fake["answer"] = empty
    rc, payload, _ = run(capsys, max_pages=3)
    assert rc == 0 and len(fake["calls"]) == 3 and payload["n"] == 0
    assert all(row["count"] == 0 and row["usd"] == "0.000" for row in rows(b.ARCHIVE))


def test_error_on_later_page_returns_earlier_rows_without_retry(x_account, fake, capsys):
    def flaky(path, query, index):
        if index == 0:
            return _endless(path, query, index)
        return urllib.error.HTTPError(path, 429, "slow", {}, None)
    fake["answer"] = flaky
    rc, payload, _ = run(capsys, per_call=10, max_total=50)
    assert rc == 0 and len(fake["calls"]) == 2
    assert payload["n"] == 10 and payload["stopped"] == "provider_rate_limited"
    assert sum(row["count"] for row in rows(b.ARCHIVE)) == 10


# ----- 柵 5: 件数の口は 1 回 0.010 USD ----------------------------------------------

def test_counts_all_charges_per_request(x_account, fake, capsys):
    def counts(path, query, index):
        value = {"data": [{"start": f"2026-0{index + 8}-01T00:00:00.000Z", "end": f"2026-0{index + 8}-02T00:00:00.000Z",
                           "post_count": 3 + index}], "meta": {"total_post_count": 3 + index}}
        if index == 0:
            value["meta"]["next_token"] = "c1"
        return value
    fake["answer"] = counts
    rc, payload, _ = run(capsys, count_only=True, since="2026-08-01", max_pages=3)
    assert rc == 0
    assert [c["path"] for c in fake["calls"]] == ["/2/tweets/counts/all"] * 2
    assert fake["calls"][0]["query"]["granularity"] == "day"
    assert fake["calls"][1]["query"]["next_token"] == "c1"
    assert payload["total_post_count"] == 7 and payload["complete"] is True
    ledger = rows(b.COUNTS_ALL)
    assert len(ledger) == 2 and all(row["usd"] == "0.010" and row["count"] == 1 for row in ledger)
    cost = payload["x_read_cost"]
    assert cost["counts_requests"] == 2 and cost["counts_usd_estimate"] == "0.020" and cost["posts"] == 0
    report = b.report()
    assert report["spent_estimate_usd"] == "0.020" and report["counts_all_read"]["month"]["requests"] == 2
    assert any("Counts: All・1 回 $0.010" in line for line in b.cost_lines(report))
    assert rows(b.ARCHIVE) == []


# ----- 柵 6: timer の道から呼べない ------------------------------------------------

def test_unattended_mark_refuses_before_request(x_account, fake, capsys):
    fake["answer"] = lambda *a: pytest.fail("人の居ない道で X を叩いた")
    adapter = x_adapter.XAdapter(access_token="t")
    adapter.auth_account = ACCOUNT
    with x_archive.unattended():
        with pytest.raises(x_adapter.AdapterError, match="x_archive_unattended"):
            adapter.archive_search("q", start_time="2006-03-21T00:00:01Z")
        with pytest.raises(x_adapter.AdapterError, match="x_archive_unattended"):
            adapter.archive_counts("q", start_time="2026-08-20T00:00:00Z")
        rc, payload, _ = run(capsys)
        assert rc == 2 and payload["error"] == "x_archive_unattended"
    assert fake["calls"] == [] and rows(b.ARCHIVE) == [] and rows(b.COUNTS_ALL) == []
    assert not x_archive.is_unattended()


@pytest.mark.parametrize("argv", [["collect", ACCOUNT], ["morning", ACCOUNT], ["run", ACCOUNT], ["maintain"]])
def test_timer_commands_raise_the_mark(x_account, monkeypatch, argv):
    from thth import cli, read_coordination
    seen = []
    monkeypatch.setattr(read_coordination, "invoke", lambda args, command: seen.append(x_archive.is_unattended()) or 0)
    assert cli.main(argv) == 0
    assert seen == [True]


def test_archive_command_runs_attended(x_account, monkeypatch):
    from thth import cli, read_coordination
    seen = []
    monkeypatch.setattr(read_coordination, "invoke", lambda args, command: seen.append(x_archive.is_unattended()) or 0)
    assert cli.main(["archive", ACCOUNT, "--from", "a", "--title", "b"]) == 0
    assert seen == [False]


def test_collect_once_raises_the_mark(monkeypatch):
    from thth import collect
    seen = []
    monkeypatch.setattr(collect, "_collect_once", lambda *a, **k: seen.append(x_archive.is_unattended()) or {})
    # `leave_gate.leased` の外側（口座の lease）を外して、印を立てる中身だけを呼ぶ。
    collect.collect_once.__wrapped__("acct", adapter=None)
    assert seen == [True] and not x_archive.is_unattended()


def test_archive_is_not_a_capability():
    # `where`・`map`・`morning` が capability で拾わない。
    assert not {"archive_search", "archive_counts"} & set(x_adapter.XAdapter.CAPABILITIES)


# ----- 語の組み立て -------------------------------------------------------------

def test_query_builder_quotes_safely():
    assert x_archive.build_query("@aimyon36", "マリーゴールド") == 'from:aimyon36 "マリーゴールド" -is:retweet'
    assert x_archive.build_query("a_b", ' say "hi"  “x” ') == 'from:a_b "say hi x" -is:retweet'
    assert x_archive.build_query("a", "t", retweets=True) == 'from:a "t"'
    for handle in ("", "a b", "toolonghandle1234", "from:x"):
        with pytest.raises(x_archive.QueryError):
            x_archive.build_query(handle, "t")
    for title in ("", '"""', "a\nb"):
        with pytest.raises(x_archive.QueryError):
            x_archive.build_query("a", title)
