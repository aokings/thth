"""Versioned snapshot: calculation parity, provenance and read-only boundaries."""
import datetime
import json
from pathlib import Path

import pytest

from thth import after_cli, analytics_report, cli, jst
from tests.test_after_cli import (_load_server_module, _seed_measured, _seed_engagement,
                            _insight_path)

NOW = jst.parse("2026-09-17T08:00:00+09:00")


def test_boundaries_evidence_missing_and_no_writes(isolated_account_factory):
    account = isolated_account_factory()
    for name, time, marks in [
        ("start", "2026-09-10T08:00:00+09:00", (24,)),
        ("end", "2026-09-17T08:00:00+09:00", ()),
        ("old", "2026-09-10T07:59:59+09:00", (24,)),
        ("future", "2026-09-17T08:00:01+09:00", (24,)),
    ]:
        _seed_measured(account, name, posted_at=time, marks=marks)
    _seed_engagement(account, "reply", posted_at="2026-09-12T08:00:00+09:00")
    queue = Path(account["repo_dir"]) / "docs/sns/queue/protected.md"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("---\napproved: true\n---\nPRIVATE BODY")
    before = {p: p.read_bytes() for p in Path(account["repo_dir"]).rglob("*") if p.is_file()}
    result = analytics_report.answer(account["name"], now=NOW)
    after = {p: p.read_bytes() for p in Path(account["repo_dir"]).rglob("*") if p.is_file()}
    assert before == after
    node = result["by_account"][account["name"]]
    original = after_cli.answer(account["name"], now=NOW, window_days=7)
    for key in ("posts", "engagements", "comparable", "cannot_say"):
        def legacy_projection(actual, original):
            if isinstance(original, dict):
                return {k: legacy_projection(actual[k], v) for k, v in original.items()}
            if isinstance(original, list):
                assert len(actual) == len(original)
                return [legacy_projection(a, b) for a, b in zip(actual, original)]
            return actual
        assert legacy_projection(node[key], original[key]) == original[key]
    assert {p["post_id"] for p in node["posts"]["by_post"]} == {"start", "end"}
    assert node["posts"]["views_24h"] == {"median": None, "n": 1, "iqr": None, "min": None, "max": None, "spread_reason": "below_min_n"}
    end = next(p for p in node["posts"]["by_post"] if p["post_id"] == "end")
    assert end["views_24h"] is None and end["covered"] is False
    assert node["engagements"]["by_branch"][0]["post_id"] == "reply"
    assert "one_thing_to_change" not in node
    assert "updated" not in node["provenance"]
    assert result["data_updated_at"] is None and result["data_updated_at_reason"]
    assert result["period"]["start_inclusive"] and result["period"]["end_inclusive"]
    assert result["period"]["start"] == "2026-09-10T08:00:00+09:00"
    assert result["generated_at"] == "2026-09-17T08:00:00+09:00"
    text = json.dumps(result)
    assert "PRIVATE BODY" not in text
    assert '"username"' not in text
    def assert_text_only_in_details(value, details=False):
        if isinstance(value, dict):
            assert "text" not in value or details
            for key, child in value.items():
                assert_text_only_in_details(child, key == "cannot_say_details")
        elif isinstance(value, list):
            for child in value:
                assert_text_only_in_details(child, details)
    assert_text_only_in_details(result)
    markdown = analytics_report.render_markdown(result)
    recovered = "\n".join(line[4:] for line in markdown.splitlines() if line.startswith("    "))
    assert json.loads(recovered) == result


def test_project_separation_and_unknown_project(isolated_account_factory):
    one = isolated_account_factory(name="one", project="same")
    two = isolated_account_factory(name="two", project="same")
    isolated_account_factory(name="outside", project="other")
    _seed_measured(one, "P1", metrics={"views": 0})
    _seed_measured(two, "P2", metrics={"views": 100})
    result = analytics_report.answer(project="same", min_n=1, now=NOW)
    assert set(result["by_account"]) == {"one", "two"}
    assert result["by_account"]["one"]["posts"]["views_24h"]["median"] == 0
    assert result["by_account"]["two"]["posts"]["views_24h"]["median"] == 100
    assert "posts" not in result
    with pytest.raises(after_cli.AfterError, match="account"):
        analytics_report.answer(project="missing", now=NOW)


def test_broken_ledger_is_not_silently_healthy(isolated_account_factory):
    account = isolated_account_factory()
    _seed_measured(account, "good")
    path = Path(_insight_path(account, "bad"))
    path.write_text("{broken json\n")
    node = analytics_report.answer(account["name"], now=NOW)["by_account"][account["name"]]
    assert any("読めない実測台帳" in line for line in node["cannot_say"])


@pytest.mark.parametrize("kwargs", [{}, {"account_name": "a", "project": "p"},
    {"account_name": ""}, {"project": " "}, {"account_name": "a", "window_days": True},
    {"account_name": "a", "min_n": False}, {"account_name": "a", "window_days": 0},
    {"account_name": "a", "min_n": 1.5}, {"account_name": "a", "window_days": 10**100},
    {"account_name": "a", "now": datetime.datetime(2026, 9, 17)}])
def test_invalid_input(kwargs):
    with pytest.raises(after_cli.AfterError):
        analytics_report.answer(**kwargs)


def test_cli_and_mcp_same_payload(isolated_account_factory, monkeypatch, capsys):
    account = isolated_account_factory()
    _seed_measured(account, "root")
    monkeypatch.setattr(analytics_report.jst, "now_jst", lambda: NOW)
    assert cli.main(["analytics-report", account["name"], "--json"]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli.main(["analytics-report", account["name"]]) == 0
    assert capsys.readouterr().out == analytics_report.render_markdown(cli_result) + "\n"
    server = _load_server_module()
    result = server.call_tool("analytics_report", {"account": account["name"]})
    assert not result["isError"]
    payload = json.loads(result["content"][0]["text"])
    expected = analytics_report.answer(account["name"], now=jst.parse(payload["generated_at"]))
    assert payload == expected
    assert cli.main(["analytics-report", "--project", "missing", "--json"]) == 2
    assert capsys.readouterr().out == ""
    assert server.call_tool("analytics_report", {"project": "missing"})["isError"]
    assert server.call_tool("analytics_report", {"account": "missing"})["isError"]


@pytest.mark.parametrize("args", [{}, {"account": "a", "project": "p"},
    {"account": "a", "window_days": True}, {"account": "a", "min_n": False},
    {"account": "a", "secret": "not-valid"}])
def test_mcp_input_boundary(args):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("analytics_report", args)
