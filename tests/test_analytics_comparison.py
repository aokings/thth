"""Comparison contract: actual elapsed time, account isolation, incomplete samples."""
import datetime
import json
from pathlib import Path

import pytest

from thth import analytics_report, after_cli, cli, jst
from tests.test_after_cli import _insight_path, _write_ndjson, _seed_engagement, _load_server_module

NOW = jst.parse("2026-09-18T12:00:00+09:00")


def seed(account, post_id, posted, *, age=25, value=10, marks=None, extra=None):
    posted = jst.parse(posted) if isinstance(posted, str) else posted
    row = {"account": account["name"], "post_id": post_id, "posted_at": jst.iso(posted),
           "collected_at": jst.iso(posted + datetime.timedelta(hours=age)),
           "age_hours": 999, "marks": [24] if marks is None else marks,
           "reply_to": None, "metrics": {"views": value, "likes": 0, "replies": 0}}
    row.update(extra or {})
    _write_ndjson(_insight_path(account, post_id), [row])
    return row


def report(account, **kwargs):
    return analytics_report.answer(account["name"], now=NOW, compare_previous=True, **kwargs)


def test_half_open_zero_delta_and_read_only(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "previous-start", NOW - datetime.timedelta(days=14), value=0)
    seed(a, "current-start", NOW - datetime.timedelta(days=7), value=5)
    seed(a, "end-excluded", NOW)
    seed(a, "too-old", NOW - datetime.timedelta(days=14, seconds=1))
    root = Path(a["repo_dir"])
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    r = report(a, min_n=1)
    p = r["by_account"][a["name"]]["posts"]
    assert p["previous"]["n_total"] == p["current"]["n_total"] == 1
    assert p["previous"]["metrics"]["views"]["median"] == 0
    assert p["comparison"]["views"]["absolute_median_change"] == 5
    assert p["previous"]["evidence"][0]["observation"]["actual_age_hours"] == 25
    assert r["data_updated_at"] == jst.iso(NOW - datetime.timedelta(days=7) + datetime.timedelta(hours=25))
    assert r["data_updated_at_scope"] == "selected_observations"
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not r["periods"]["previous"]["end_inclusive"]
    markdown = analytics_report.render_markdown(r)
    assert json.loads("\n".join(s[4:] for s in markdown.splitlines() if s.startswith("    "))) == r


@pytest.mark.parametrize("age,marks,extra,reason", [
    (23.99, [24], {}, "premature_observation"),
    (30, [24], {}, "late_observation"),
    (25, [1, 24], {}, "missing_or_collapsed_24h_mark"),
    (25, [], {}, "missing_or_collapsed_24h_mark"),
    (25, [24], {"collected_at": "bad"}, "invalid_collected_at"),
    (25, [24], {"collected_at": "2026-09-18T13:00:00+09:00"}, "future_observation"),
    (25, [24], {"collected_at": "2026-09-12T13:00:00"}, "invalid_collected_at"),
])
def test_rejected_observations(isolated_account_factory, age, marks, extra, reason):
    a = isolated_account_factory()
    seed(a, "p", NOW - datetime.timedelta(days=3), age=age, marks=marks, extra=extra)
    p = report(a, min_n=1)["by_account"][a["name"]]["posts"]["current"]
    assert (p["n_total"], p["n_eligible"], p["n_missing"], p["n_incomplete"]) == (1, 0, 1, 1)
    assert p["metrics"]["views"]["median"] is None
    assert p["evidence"][0]["rejected_observations"] == {reason: 1}
    assert p["data_updated_at"] is None


def test_immature_and_sparse_are_not_zero(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "new", NOW - datetime.timedelta(hours=1), age=0.5)
    seed(a, "old", NOW - datetime.timedelta(days=8), value=0)
    r = report(a, min_n=2)
    p = r["by_account"][a["name"]]["posts"]
    assert p["current"]["n_immature"] == 1
    assert p["previous"]["metrics"]["views"]["n_eligible"] == 1
    assert p["comparison"]["views"]["absolute_median_change"] is None
    assert not p["comparison"]["views"]["comparable"]


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), "3", 10**1000])
def test_invalid_metric_does_not_become_zero(isolated_account_factory, value):
    a = isolated_account_factory()
    seed(a, "p", NOW - datetime.timedelta(days=3), value=value)
    p = report(a, min_n=1)["by_account"][a["name"]]["posts"]["current"]
    assert p["n_eligible"] == 1  # time-eligible does not imply every metric exists
    assert p["metrics"]["views"] == {"n_total": 1, "n_eligible": 0, "n_missing": 1, "median": None}
    json.dumps(p, allow_nan=False)


def test_earliest_actual_time_and_one_per_post(isolated_account_factory):
    a = isolated_account_factory()
    posted = NOW - datetime.timedelta(days=3)
    later = seed(a, "p", posted, age=26, value=30)
    earlier = dict(later, collected_at=(posted + datetime.timedelta(hours=24)).astimezone(datetime.timezone.utc).isoformat(), metrics={"likes": 3})
    _write_ndjson(_insight_path(a, "p"), [later, earlier, earlier])
    p = report(a, min_n=1)["by_account"][a["name"]]["posts"]["current"]
    assert p["n_total"] == p["n_eligible"] == 1
    assert p["metrics"]["views"]["n_eligible"] == 0  # never substitute later metric
    assert p["metrics"]["likes"]["median"] == 3
    assert p["evidence"][0]["observation"]["actual_age_hours"] == 24


def test_ownership_reply_population_and_no_fallback(isolated_account_factory, monkeypatch):
    a = isolated_account_factory()
    posted = NOW - datetime.timedelta(days=3)
    seed(a, "foreign", posted, extra={"account": "elsewhere"})
    seed(a, "unknown", posted, extra={"account": None})
    seed(a, "ambiguous-root", posted, extra={"source": "sent"})
    seed(a, "reply", posted, extra={"metrics": {"views": 4}})
    _seed_engagement(a, "reply", posted_at=jst.iso(posted))
    _seed_engagement(a, "unmeasured", posted_at=jst.iso(posted))
    monkeypatch.setattr(after_cli, "_replies_back_from_ledger", lambda *a: pytest.fail("no fallback"))
    n = report(a, min_n=1)["by_account"][a["name"]]
    assert n["posts"]["current"]["n_total"] == 0
    assert n["engagements"]["current"]["n_total"] == 2
    assert n["engagements"]["current"]["metrics"]["replies"]["n_eligible"] == 0
    assert n["excluded_records"]["unknown_ownership"] == 1


def test_project_isolation_and_broken_ledger(isolated_account_factory):
    a = isolated_account_factory(name="one", project="p")
    b = isolated_account_factory(name="two", project="p")
    seed(a, "a", NOW - datetime.timedelta(days=3), value=0)
    seed(b, "b", NOW - datetime.timedelta(days=3), value=10)
    Path(_insight_path(a, "broken")).write_text("[broken")
    r = analytics_report.answer(project="p", now=NOW, min_n=1, compare_previous=True)
    assert r["by_account"]["one"]["posts"]["current"]["metrics"]["views"]["median"] == 0
    assert r["by_account"]["two"]["posts"]["current"]["metrics"]["views"]["median"] == 10
    assert r["by_account"]["one"]["incomplete_sources"]["measured_files"] == 1
    assert "posts" not in r


def test_cli_mcp_boolean_contract(isolated_account_factory, monkeypatch, capsys):
    a = isolated_account_factory()
    monkeypatch.setattr(analytics_report.jst, "now_jst", lambda: NOW)
    assert cli.main(["analytics-report", a["name"], "--compare-previous", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == report(a)
    server = _load_server_module()
    result = server.call_tool("analytics_report", {"account": a["name"], "compare_previous": True})
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["report_type"] == "period_comparison"
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("analytics_report", {"account": a["name"], "compare_previous": "true"})
    with pytest.raises(after_cli.AfterError):
        report(a, window_days=10**100)
    with pytest.raises(after_cli.AfterError):
        analytics_report.answer(a["name"], compare_previous=1)


@pytest.mark.parametrize("change", [{"reply_to": "parent"}, {"source": "sent"}, {"source": "unknown"}])
def test_conflicting_row_root_classification_excluded(isolated_account_factory, change):
    a = isolated_account_factory()
    row = seed(a, "p", NOW - datetime.timedelta(days=3), age=1, marks=[1])
    later = dict(row, collected_at=jst.iso(NOW - datetime.timedelta(days=2)), marks=[24], **change)
    _write_ndjson(_insight_path(a, "p"), [row, later])
    node = report(a, min_n=1)["by_account"][a["name"]]
    assert node["posts"]["current"]["n_total"] == 0
    assert node["excluded_records"]["conflicting_root_classification"] == 1


def test_conflicting_row_posted_time_rejected(isolated_account_factory):
    a = isolated_account_factory()
    row = seed(a, "p", NOW - datetime.timedelta(days=3), age=1, marks=[1])
    later = dict(row, collected_at=jst.iso(NOW - datetime.timedelta(days=2)), marks=[24],
                 posted_at=jst.iso(NOW - datetime.timedelta(days=4)))
    _write_ndjson(_insight_path(a, "p"), [row, later])
    p = report(a, min_n=1)["by_account"][a["name"]]["posts"]["current"]
    assert p["n_eligible"] == 0
    assert p["evidence"][0]["rejected_observations"]["invalid_or_conflicting_posted_at"] == 1


def test_duplicate_reply_and_conflicting_dates(isolated_account_factory):
    a = isolated_account_factory()
    posted = NOW - datetime.timedelta(days=3)
    for _ in range(2):
        _seed_engagement(a, "same", posted_at=jst.iso(posted))
    _seed_engagement(a, "conflict", posted_at=jst.iso(posted))
    _seed_engagement(a, "conflict", posted_at=jst.iso(posted - datetime.timedelta(days=1)))
    node = report(a)["by_account"][a["name"]]
    assert node["engagements"]["current"]["n_total"] == 1
    assert node["excluded_records"]["invalid_or_conflicting_engagement_posted_at"] == 1


def test_large_finite_metrics_stay_json_finite(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "p1", NOW - datetime.timedelta(days=3), value=1e308)
    seed(a, "p2", NOW - datetime.timedelta(days=4), value=1e308)
    r = report(a, min_n=2)
    assert r["by_account"][a["name"]]["posts"]["current"]["metrics"]["views"]["median"] == 1e308
    json.dumps(r, allow_nan=False)


def test_malformed_loader_data_is_error_not_empty_population(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "p", NOW - datetime.timedelta(days=3), extra={"metrics": [1]})
    with pytest.raises(after_cli.AfterError, match="台帳の形式"):
        report(a)


def test_conflicting_measured_reply_time_is_excluded_from_population(isolated_account_factory):
    a = isolated_account_factory()
    posted = NOW - datetime.timedelta(days=3)
    seed(a, "reply-conflict", posted, extra={"reply_to": "parent"})
    _seed_engagement(a, "reply-conflict", posted_at=jst.iso(posted - datetime.timedelta(days=1)))
    node = report(a)["by_account"][a["name"]]
    assert node["excluded_records"]["conflicting_measured_engagement_posted_at"] == 1
    for kind in ("posts", "engagements"):
        for period in ("previous", "current"):
            population = node[kind][period]
            assert population["n_total"] == population["n_missing"] == population["n_incomplete"] == 0
            assert population["evidence"] == []
