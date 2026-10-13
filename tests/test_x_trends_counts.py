"""3.19.0: X のトレンド（`thth trends`）と語ごとの投稿数の推移（`thth counts`）。

**本物の api.x.com は叩かない**——`thth.adapters.x.request` を偽物に差し替える。確かめること:
- 要求は 1 回・path と query の形（trend.fields・max_trends・granularity）。
- 費用は種類ごとの台帳（Trends 0.010・Counts: Recent 0.005／request）で月の予算に入り、
  1 日の本数には数えない。断りの応答は 0 で確定・予算が無ければ叩かない。
- X 以外は unsupported_on_platform・範囲外の引数は叩かずに断る・保存しない。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import urllib.error

import pytest

from thth import accounts as accounts_mod
from thth import budget_x as b
from thth import trends_cli
from tests.test_x_community import ACCOUNT, _account, fake, x_account  # noqa: F401


def _trends(n):
    return {"data": [{"trend_name": f"#話題{i}", "tweet_count": 1000 - i} for i in range(n - 1)]
            + [{"trend_name": "数なし"}]}


def run_trends(capsys, *, account=ACCOUNT, woeid=23424856, max_trends=None, as_json=True):
    args = argparse.Namespace(account=account, woeid=woeid, max_trends=max_trends, json=as_json)
    rc = trends_cli.cmd_trends(args)
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if as_json and out.out.strip() else None), out


def run_counts(capsys, word, *, account=ACCOUNT, granularity="day", as_json=True):
    args = argparse.Namespace(account=account, word=word, granularity=granularity, json=as_json)
    rc = trends_cli.cmd_counts(args)
    out = capsys.readouterr()
    return rc, (json.loads(out.out) if as_json and out.out.strip() else None), out


def rows(kind):
    return list(b.read_posts(kind)["reservations"].values())


def _no_text_saved(*needles):
    root = accounts_mod.state_dir_for(ACCOUNT)
    for folder, _dirs, files in os.walk(root):
        for name in files:
            with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
            assert not any(n in body for n in needles)


# ----- trends -------------------------------------------------------------------

def test_trends_calls_once_and_charges_one_request(x_account, fake, capsys):
    fake["routes"][("GET", "/2/trends/by/woeid/23424856")] = _trends(3)
    rc, payload, _ = run_trends(capsys, max_trends=5)
    assert rc == 0
    [call] = fake["calls"]
    assert call["query"] == {"max_trends": 5, "trend.fields": "trend_name,tweet_count"}
    assert payload["trends"][0] == {"trend_name": "#話題0", "tweet_count": 1000}
    assert payload["trends"][-1] == {"trend_name": "数なし", "tweet_count": None}
    assert any("0 ではありません" in line for line in payload["cannot_say"])
    [ledger] = rows(b.TRENDS)
    assert ledger["state"] == "settled" and ledger["count"] == 1 and ledger["usd"] == "0.010"
    cost = payload["x_read_cost"]
    assert cost["requests"] == 1 and cost["usd_estimate"] == "0.010" and cost["daily_read_cap"] == "not_counted"
    report = b.report()
    assert report["spent_estimate_usd"] == "0.010" and report["trends_read"]["month"]["requests"] == 1
    assert any(line.startswith("  トレンド（Trends・1 回 $0.010）") for line in b.cost_lines(report))
    assert b.posts_status(ACCOUNT)["today"] == 0
    _no_text_saved("#話題0")


def test_trends_default_is_japan_and_twenty(x_account, fake, capsys):
    fake["routes"][("GET", "/2/trends/by/woeid/23424856")] = _trends(1)
    from thth import cli
    parsed = cli.build_parser().parse_args(["trends", ACCOUNT])
    assert parsed.woeid == 23424856 and parsed.max_trends is None
    rc, _payload, out = run_trends(capsys, as_json=False)
    assert rc == 0 and fake["calls"][0]["query"]["max_trends"] == 20
    assert "woeid=23424856" in out.out and "Trends" in out.out


@pytest.mark.parametrize("kw", [{"max_trends": 0}, {"max_trends": 51}, {"woeid": 0}])
def test_trends_out_of_range_never_calls(x_account, fake, capsys, kw):
    rc, payload, _ = run_trends(capsys, **kw)
    assert rc == 2 and payload["error"] == "invalid_request" and fake["calls"] == []


def test_trends_refuses_other_media(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory, name="kanto-bsky", media="bluesky")
    rc, payload, _ = run_trends(capsys, account="kanto-bsky")
    assert rc == 2 and payload["error"] == "unsupported_on_platform" and payload["cannot_say"]
    assert fake["calls"] == []


def test_trends_without_budget_never_calls(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory)
    rc, payload, _ = run_trends(capsys)
    assert rc == 1 and payload["error"] == "budget_exhausted" and fake["calls"] == []


def test_trends_refusal_charges_nothing_and_keeps_a_short_reason(x_account, fake, capsys):
    body = b'{"title":"Forbidden","detail":"something new","status":403}'
    fake["routes"][("GET", "/2/trends/by/woeid/23424856")] = urllib.error.HTTPError(
        "u", 403, "Forbidden", {}, io.BytesIO(body))
    rc, payload, _ = run_trends(capsys)
    assert rc == 1 and payload["error"] == "upstream_refused"
    assert "x_trends_http_403: x_unknown_refusal（title=Forbidden / detail=something new）" in payload["message"]
    [ledger] = rows(b.TRENDS)
    assert ledger["count"] == 0 and ledger["usd"] == "0.000"


# ----- counts -------------------------------------------------------------------

def _counts():
    return {"data": [{"start": "2026-10-11T00:00:00.000Z", "end": "2026-10-12T00:00:00.000Z", "post_count": 12},
                     {"start": "2026-10-12T00:00:00.000Z", "end": "2026-10-13T00:00:00.000Z", "post_count": 30}],
            "meta": {"total_post_count": 42}}


def test_counts_calls_once_and_charges_one_request(x_account, fake, capsys):
    fake["routes"][("GET", "/2/tweets/counts/recent")] = _counts()
    rc, payload, _ = run_counts(capsys, " 喫茶店 lang:ja ")
    assert rc == 0
    [call] = fake["calls"]
    assert call["query"] == {"query": "喫茶店 lang:ja", "granularity": "day"}
    assert payload["total_post_count"] == 42 and payload["n"] == 2 and payload["window_days"] == 7
    assert payload["buckets"][1] == {"start": "2026-10-12T00:00:00.000Z", "end": "2026-10-13T00:00:00.000Z",
                                     "post_count": 30}
    [ledger] = rows(b.COUNTS)
    assert ledger["state"] == "settled" and ledger["usd"] == "0.005"
    assert payload["x_read_cost"]["usd_estimate"] == "0.005"
    report = b.report()
    assert report["counts_read"]["month"]["requests"] == 1
    assert any("語ごとの投稿数（Counts: Recent・1 回 $0.005）" in line for line in b.cost_lines(report))
    assert b.posts_status(ACCOUNT)["today"] == 0
    _no_text_saved("喫茶店")


def test_counts_hour_granularity_and_human_output(x_account, fake, capsys):
    fake["routes"][("GET", "/2/tweets/counts/recent")] = _counts()
    rc, _payload, out = run_counts(capsys, "coffee", granularity="hour", as_json=False)
    assert rc == 0 and fake["calls"][0]["query"]["granularity"] == "hour"
    assert "合計=42" in out.out and "Counts: Recent" in out.out


@pytest.mark.parametrize("word", ["", "   ", "a" * 513, "改\n行"])
def test_counts_bad_word_never_calls(x_account, fake, capsys, word):
    rc, payload, _ = run_counts(capsys, word)
    assert rc == 2 and payload["error"] == "invalid_request" and fake["calls"] == []


def test_counts_refuses_other_media(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory, name="kanto-threads", media="threads")
    rc, payload, _ = run_counts(capsys, "coffee", account="kanto-threads")
    assert rc == 2 and payload["error"] == "unsupported_on_platform" and fake["calls"] == []


def test_counts_parser_rejects_minute():
    from thth import cli
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["counts", ACCOUNT, "x", "--granularity", "minute"])
    parsed = cli.build_parser().parse_args(["counts", ACCOUNT, "x"])
    assert parsed.granularity == "day" and parsed.func is trends_cli.cmd_counts


def test_failures_leave_only_static_codes_in_runs(x_account, fake, capsys):
    """10/13: 失敗の runs に原因の符丁だけを残す（語・detail の文は残さない）。"""
    body = b'{"title":"Forbidden","detail":"something new","status":403}'
    fake["routes"][("GET", "/2/trends/by/woeid/23424856")] = urllib.error.HTTPError(
        "u", 403, "Forbidden", {}, io.BytesIO(body))
    fake["routes"][("GET", "/2/tweets/counts/recent")] = TimeoutError("timed out 珈琲")
    run_trends(capsys)
    run_counts(capsys, "珈琲")
    root = accounts_mod.state_dir_for(ACCOUNT)
    errors = []
    for name in sorted(os.listdir(root)):
        if name.startswith("runs-"):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                errors += [json.loads(line).get("error") for line in fh if line.strip()]
    assert sorted(errors) == ["counts_failed: x_read_failed TimeoutError",
                              "trends_failed: x_trends_http_403 x_unknown_refusal"]
