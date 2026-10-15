"""X の自分の投稿の数字（Owned Reads）と `thth thread --limit`（masaru 10/12 決定 1・5）。

**本物の api.x.com は叩かない**——`thth.adapters.x.request` を偽物に差し替える
（`tests/test_x_search_thread.py` の `fake`・`x_account` をそのまま使う）。
時刻は conftest の固定（2026-09-09 10:00 JST）。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import urllib.error

import pytest

from thth import accounts as accounts_mod
from thth import budget_x as b
from thth import cli, collect as collect_mod, jst, sent as sent_mod, server_files
from thth.adapters import base
from tests.test_x_search_thread import (  # noqa: F401  （fixture をこのファイルでも使う）
    ACCOUNT, ROOT, USERS, adapter_for, conversation_routes, fake, x_account)

ME = "123"   # `x_account` の token の user_id
NOW = datetime.datetime(2026, 9, 9, 10, 0, tzinfo=jst.JST)


def owned_row(identifier, *, author=ME, created="2026-09-09T00:00:00.000Z", **metrics):
    public = {"impression_count": 120, "like_count": 4, "reply_count": 1,
              "retweet_count": 2, "quote_count": 0, "bookmark_count": 3}
    public.update(metrics)
    return {"id": identifier, "author_id": author, "created_at": created,
            "public_metrics": public}


def owned_ledger():
    return list(b.read_posts(b.OWNED)["reservations"].values())


# ----- 予算（別の台帳・別の段・月の合計には入る） ----------------------------------

def test_owned_read_is_its_own_ledger_and_price(x_account):
    with b.owned_read(ACCOUNT, 3) as slot:
        assert b.report()["held_usd"] == "0.003"
        slot.dispatch()
        slot.settle(2)
    whole = b.report()
    assert whole["spent_estimate_usd"] == "0.002" and whole["held_usd"] == "0"
    assert whole["owned_read"]["price"]["owned_read_usd"] == "0.001"
    assert whole["owned_read"]["month"] == {"month_utc": "2026-09", "posts": 2,
                                            "spent_estimate_usd": "0.002", "held_usd": "0",
                                            "uncertain": 0}
    # 他人の投稿の台帳（と 1 日 60 本）には数えない。
    assert b.read_posts()["reservations"] == {} and b.posts_status(ACCOUNT)["today"] == 0
    assert whole["posts_read"]["month"]["spent_estimate_usd"] == "0"
    with server_files.directory(b.folder(), private=True) as fd:
        assert b.OWNED_FILE in set(os.listdir(fd))
    assert whole["months"]["2026-09"] == {"spent_estimate_usd": "0.002", "held_usd": "0"}


def test_owned_reads_share_the_monthly_cap_with_the_other_two(x_account):
    b.configure("0.015", by="operator")
    with b.posts_read(ACCOUNT, 2) as slot:   # 0.010
        slot.dispatch(); slot.settle(2)
    with b.owned_read(ACCOUNT, 5) as slot:   # 0.005 → ちょうど上限
        slot.dispatch(); slot.settle(5)
    with pytest.raises(b.BudgetError, match="budget_exhausted"):
        with b.owned_read(ACCOUNT, 1):
            pytest.fail("月の上限を超えた")
    with pytest.raises(b.BudgetError, match="budget_exhausted"):
        with b.posts_read(ACCOUNT, 1):
            pytest.fail("他人の投稿の読み取りが自分の投稿の数字を数えていない")
    with pytest.raises(b.BudgetError, match="budget_exhausted"):
        with b.user_read(ACCOUNT):
            pytest.fail("本人確認が自分の投稿の数字を数えていない")


def test_owned_read_is_not_limited_by_the_daily_posts_cap(x_account):
    assert cli.main(["account", "set", ACCOUNT, "x_daily_reads", "0", "--by", "operator"]) == 0
    with b.owned_read(ACCOUNT, 100) as slot:
        slot.dispatch(); slot.settle(100)
    assert b.report()["owned_read"]["month"]["spent_estimate_usd"] == "0.100"


def test_owned_ledger_is_strictly_validated(x_account):
    with b.owned_read(ACCOUNT, 2) as slot:
        slot.dispatch(); slot.settle(2)
    with b.locked() as fd:
        value = b._read_posts(fd, b.OWNED)
        next(iter(value["reservations"].values()))["usd"] = "0.010"   # 他人の投稿の単価
        server_files.replace_at(fd, b.OWNED_FILE, server_files.encode(value), private=True)
    with pytest.raises(b.BudgetError, match="budget_unreadable"):
        b.report()


# ----- adapter の口 -------------------------------------------------------------

def test_owned_metrics_query_mapping_and_charge(x_account, fake):
    fake["routes"]["/2/tweets"] = {"data": [
        owned_row("1900000000000000001"),
        owned_row("1900000000000000002", author="999"),   # 自分でない（落とす）
        owned_row("1900000000000000003", impression_count=-1),
    ]}
    adapter = adapter_for()
    result = adapter.owned_metrics(["1900000000000000001", "1900000000000000002",
                                    "1900000000000000003", "1900000000000000001"], now=NOW)
    method, path, query = fake["calls"][0]
    assert (method, path) == ("GET", "/2/tweets") and len(fake["calls"]) == 1
    assert query == {"ids": "1900000000000000001,1900000000000000002,1900000000000000003",
                     "tweet.fields": "public_metrics,created_at,author_id"}
    # 本文・username を頼まない（text も expansions も無い）。
    assert "expansions" not in query and "text" not in query["tweet.fields"]
    assert result["by_post"]["1900000000000000001"] == {
        "metrics": {"views": 120, "likes": 4, "replies": 1, "reposts": 2, "quotes": 0, "bookmarks": 3},
        "available": ["views", "likes", "replies", "reposts", "quotes", "bookmarks"]}
    assert "1900000000000000002" not in result["by_post"] and result["rejected"] == 1
    # 読めない指標は入れない（0 と混ぜない）。
    assert "views" not in result["by_post"]["1900000000000000003"]["metrics"]
    assert result["not_returned"] == ["1900000000000000002"]
    # 課金は返ってきた本数（3 本・0.001 ずつ）。他人の投稿の台帳には何も無い。
    assert result["posts"] == 3 and result["usd_estimate"] == "0.003"
    assert [(row["state"], row["count"], row["usd"]) for row in owned_ledger()] == [("settled", 3, "0.003")]
    assert b.read_posts()["reservations"] == {}


def test_owned_metrics_batches_by_100(x_account, fake):
    def answer(query):
        return {"data": [owned_row(i) for i in query["ids"].split(",")]}
    fake["routes"]["/2/tweets"] = answer
    ids = [str(1900000000000000000 + i) for i in range(150)]
    result = adapter_for().owned_metrics(ids, now=NOW)
    assert [len(c[2]["ids"].split(",")) for c in fake["calls"]] == [100, 50]
    assert len(result["by_post"]) == 150 and result["requests"] == 2
    assert b.report()["owned_read"]["month"]["spent_estimate_usd"] == "0.150"


def test_owned_metrics_failure_keeps_the_hold_and_refusal_settles_zero(x_account, fake):
    fake["routes"]["/2/tweets"] = urllib.error.URLError("boom")
    with pytest.raises(base.AdapterError, match="x_read_failed"):
        adapter_for().owned_metrics(["1900000000000000001", "1900000000000000002"], now=NOW)
    assert [(r["state"], r["usd"]) for r in owned_ledger()] == [("uncertain", "0.002")]
    assert b.report()["owned_read"]["month"]["uncertain"] == 1
    fake["routes"]["/2/tweets"] = urllib.error.HTTPError("u", 503, "x", {}, None)
    with pytest.raises(base.AdapterError, match="x_owned_read_http_503"):
        adapter_for().owned_metrics(["1900000000000000001"], now=NOW)
    assert sorted((r["state"], r["count"]) for r in owned_ledger() if r["state"] == "settled") == [("settled", 0)]


def test_owned_metrics_all_missing_settles_zero(x_account, fake):
    fake["routes"]["/2/tweets"] = {"errors": [{"resource_id": "1900000000000000001", "title": "Not Found"}]}
    result = adapter_for().owned_metrics(["1900000000000000001"], now=NOW)
    assert result["by_post"] == {} and result["not_returned"] == ["1900000000000000001"]
    assert result["api_errors"] == 1
    assert [(r["state"], r["count"]) for r in owned_ledger()] == [("settled", 0)]


def test_owned_metrics_refuses_bad_ids_before_any_request(x_account, fake):
    with pytest.raises(base.AdapterError, match="x_post_id_invalid"):
        adapter_for().owned_metrics(["abc"], now=NOW)
    assert fake["calls"] == [] and owned_ledger() == []


# ----- collect（timer）から：自分が THTH から出した投稿だけ ---------------------------

def _sent(post_id, at, **extra):
    sent_mod.write(accounts_mod.state_dir_for(ACCOUNT), post_id=post_id, text="本文 https://e.example",
                   body_hash="h", sent_at=at, **extra)


def _insight_rows(post_id):
    cfg = accounts_mod.load_account(ACCOUNT)
    path = os.path.join(accounts_mod.data_dirs(cfg, ACCOUNT)["insights_posts"], f"{post_id}.ndjson")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_collect_reads_only_own_sent_posts_at_the_threads_marks(x_account, fake):
    _sent("1900000000000000001", "2026-09-09T08:30:00+09:00")   # 1.5h → 1h の刻み
    _sent("1900000000000000002", "2026-09-08T09:00:00+09:00")   # 25h → 1・6・24h
    _sent("1900000000000000003", "2026-09-09T09:30:00+09:00")   # 0.5h → まだ刻みが無い
    _sent("1900000000000000004", "2026-09-09T07:00:00+09:00")
    sent_mod.mark_retracted(accounts_mod.state_dir_for(ACCOUNT), "1900000000000000004",
                            retracted_at="2026-09-09T08:00:00+09:00", retracted_by="operator",
                            retract_reason="test")
    fake["routes"]["/2/tweets"] = lambda q: {"data": [
        owned_row(i, created="2026-09-08T00:00:00.000Z") for i in q["ids"].split(",")]}

    result = collect_mod.collect_once(ACCOUNT, adapter=adapter_for(), now=NOW, log=lambda _l: None)
    assert result["errors"] == []
    # 1 回の要求に、刻みを跨いだ自分の投稿だけ（取り下げた投稿・刻み前は入れない）。
    assert [c[1] for c in fake["calls"]] == ["/2/tweets"]
    assert fake["calls"][0][2]["ids"] == "1900000000000000001,1900000000000000002"
    row = _insight_rows("1900000000000000001")[0]
    assert row["marks"] == [1] and row["source"] == "sent" and row["medium"] == "x"
    assert row["account"] == ACCOUNT and row["has_link"] is True and row["file"] is None
    assert row["metrics"] == {"views": 120, "likes": 4, "replies": 1, "reposts": 2, "quotes": 0,
                              "bookmarks": 3, "shares": None}
    assert "text" not in row and "username" not in row
    assert _insight_rows("1900000000000000002")[0]["marks"] == [1, 6, 24]
    assert _insight_rows("1900000000000000003") == [] and _insight_rows("1900000000000000004") == []
    # 返信（他人の投稿）は読まない。
    cfg = accounts_mod.load_account(ACCOUNT)
    assert not os.listdir(accounts_mod.data_dirs(cfg, ACCOUNT)["replies"]) if os.path.isdir(
        accounts_mod.data_dirs(cfg, ACCOUNT)["replies"]) else True
    assert b.report()["owned_read"]["month"]["spent_estimate_usd"] == "0.002"

    # 同じ時刻にもう一度: 済んだ刻みは読まない（要求も予約もしない）。
    collect_mod.collect_once(ACCOUNT, adapter=adapter_for(), now=NOW, log=lambda _l: None)
    assert len(fake["calls"]) == 1 and len(owned_ledger()) == 1


def test_collect_does_not_read_ids_outside_sent(x_account, fake, monkeypatch):
    # queue（書き戻された front-matter）にしか無い id は読まない（`sent/` が正）。
    from thth import core, queuefile
    qf = queuefile.QueueFile(path="/nowhere/q.md", malformed=False, verified=True,
                             front_matter={"thth": "1", "account": ACCOUNT, "status": "posted",
                                           "post_id": "1900000000000000009",
                                           "posted_at": "2026-09-09T08:00:00+09:00"},
                             body="## x\n\n本文\n")
    monkeypatch.setattr(core, "list_queue_files", lambda cfg, tree_sha=None: [qf])
    fake["routes"]["/2/tweets"] = {"data": []}
    result = collect_mod.collect_once(ACCOUNT, adapter=adapter_for(), now=NOW, log=lambda _l: None)
    assert fake["calls"] == [] and result["posts"] == 1 and result["errors"] == []


def test_collect_budget_refusal_is_an_error_and_writes_nothing(x_account, fake):
    b.configure("0", by="operator")
    _sent("1900000000000000001", "2026-09-09T08:30:00+09:00")
    fake["routes"]["/2/tweets"] = {"data": [owned_row("1900000000000000001")]}
    result = collect_mod.collect_once(ACCOUNT, adapter=adapter_for(), now=NOW, log=lambda _l: None)
    assert fake["calls"] == [] and _insight_rows("1900000000000000001") == []
    assert len(result["errors"]) == 1 and "budget_exhausted" in result["errors"][0]


def test_collect_says_which_posts_were_not_returned(x_account, fake):
    _sent("1900000000000000001", "2026-09-09T08:30:00+09:00")
    _sent("1900000000000000002", "2026-09-09T08:30:00+09:00")
    fake["routes"]["/2/tweets"] = {"data": [owned_row("1900000000000000001")],
                                   "errors": [{"resource_id": "1900000000000000002"}]}
    result = collect_mod.collect_once(ACCOUNT, adapter=adapter_for(), now=NOW, log=lambda _l: None)
    assert len(_insight_rows("1900000000000000001")) == 1 and _insight_rows("1900000000000000002") == []
    assert len(result["errors"]) == 1 and "1900000000000000002" in result["errors"][0]
    assert f"thth retract {ACCOUNT} <post_id> --already-gone" in result["errors"][0]


def test_collect_stops_asking_once_marked_already_gone(x_account, fake):
    """`retract --already-gone` の記録（`retracted_at`）が付いた投稿は読みに行かない。"""
    _sent("1900000000000000001", "2026-09-09T08:30:00+09:00")
    _sent("1900000000000000002", "2026-09-09T08:30:00+09:00")
    from thth import accounts as accounts_mod, sent as sent_mod
    sent_mod.mark_retracted(accounts_mod.state_dir_for(ACCOUNT), "1900000000000000002",
                            retracted_at="2026-09-10T08:00:00+09:00", retracted_by="test",
                            retract_reason="X の画面で消した")
    fake["routes"]["/2/tweets"] = {"data": [owned_row("1900000000000000001")]}
    result = collect_mod.collect_once(ACCOUNT, adapter=adapter_for(), now=NOW, log=lambda _l: None)
    assert result["errors"] == [] and len(_insight_rows("1900000000000000001")) == 1


# ----- doctor／admin の費用の表示 --------------------------------------------------

def test_admin_and_doctor_show_the_owned_reads_line(x_account, capsys):
    with b.owned_read(ACCOUNT, 4) as slot:
        slot.dispatch(); slot.settle(4)
    assert cli.main(["admin", "budget", "x"]) == 0
    out = capsys.readouterr().out
    assert "自分の投稿の数字（Owned Reads・1 本 $0.001）: 4 本・$0.004" in out
    assert "他人の投稿（Posts: Read・1 本 $0.005）" in out
    assert cli.main(["doctor", ACCOUNT]) == 2   # X は probe を叩かない（既存どおり rc=2）
    out = capsys.readouterr().out
    assert "自分の投稿の数字（Owned Reads・1 本 $0.001）: 4 本・$0.004" in out
    assert cli.main(["doctor", ACCOUNT, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["x_costs"]["owned_read"]["month"]["posts"] == 4


# ----- thth thread --limit ----------------------------------------------------------

def test_thread_limit_sets_max_results_and_the_hold(x_account, fake, capsys):
    conversation_routes(fake, 12)
    assert cli.main(["thread", ACCOUNT, ROOT, "--limit", "10", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    search = [c for c in fake["calls"] if c[1] == "/2/tweets/search/recent"][0]
    assert search[2]["max_results"] == 10
    assert result["provenance"]["read_limit"] == {"requested": 10, "applied": True, "reason": None}
    assert result["counts"]["truncated"] is True and len(result["messages"]) <= 10
    # 予約は根 1 本＋枝 10 本（返ってきた本数で確定・偽物は 12 本返すので確定できず押さえたまま）。
    assert sorted(row["limit"] for row in b.read_posts()["reservations"].values()) == [1, 10]


def test_thread_limit_default_is_still_50(x_account, fake, capsys):
    conversation_routes(fake, 3, next_token=False)
    assert cli.main(["thread", ACCOUNT, ROOT, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [c for c in fake["calls"] if c[1] == "/2/tweets/search/recent"][0][2]["max_results"] == 50
    assert "read_limit" not in result["provenance"]


@pytest.mark.parametrize("n", ["9", "51", "0"])
def test_thread_limit_out_of_range_is_refused_before_any_read(x_account, fake, capsys, n):
    assert cli.main(["thread", ACCOUNT, ROOT, "--limit", n]) == 2
    assert "10〜50" in capsys.readouterr().err
    assert fake["calls"] == [] and b.read_posts()["reservations"] == {}


def test_thread_limit_is_accepted_but_not_applied_on_free_media(isolated_account_factory):
    from thth import thread_read

    class Free:
        PAID_READS = False
        def __init__(self): self.kwargs = None
        def fetch_post(self, post_id): return {"message_id": post_id, "username": "carol", "text": "根"}
        def conversation(self, post_id, *, since=None, **kwargs):
            self.kwargs = kwargs
            return [{"message_id": "r1", "replied_to": post_id, "username": "bob", "text": "返信",
                     "timestamp": "2026-09-09T00:00:00Z"}]

    account = isolated_account_factory("free-thread", media="bluesky", handle="me.bsky.social",
                                       token=None)
    free = Free()
    import thth.adapters as adapters_mod
    from unittest import mock
    with mock.patch.object(adapters_mod, "make_adapter", lambda cfg, token: free), \
            mock.patch.object(adapters_mod.adapter_class("bluesky"), "has_token", classmethod(lambda cls, t: True)), \
            mock.patch.object(accounts_mod, "load_token", lambda cfg: {}):
        result = thread_read.answer(account["name"], "at://did:plc:carol/app.bsky.feed.post/root",
                                    limit=10)
    assert free.kwargs == {}   # adapter の枝の読み方は変えない
    assert result["provenance"]["read_limit"] == {"requested": 10, "applied": False,
                                                  "reason": "free_read_medium"}
    assert result["counts"]["messages"] == 1


def test_mcp_thread_read_passes_limit(monkeypatch):
    import importlib.util
    from tests.test_mcp import MCP_SERVER
    spec = importlib.util.spec_from_file_location("thth_mcp_server_limit", MCP_SERVER)
    server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)
    tool = next(t for t in server.TOOLS if t["name"] == "thread_read")
    assert tool["inputSchema"]["properties"]["limit"]["type"] == "integer"
    seen = []
    monkeypatch.setattr(server, "run_cli", lambda args, **kw: (seen.append(args), subprocess.CompletedProcess(args, 0, "{}", ""))[1])
    server.call_tool("thread_read", {"account": ACCOUNT, "post_id": ROOT, "limit": 20})
    assert seen and seen[0][-3:] == ["--limit", "20", "--json"]
