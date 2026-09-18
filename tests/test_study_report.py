"""Study declarations stay distinct from observed values and posting approval."""
import datetime
import json
from pathlib import Path

import pytest

from thth import cli, jst, study_report
from tests.test_after_cli import _insight_path, _write_ndjson, _seed_engagement, _load_server_module
from tests.test_analytics_comparison import NOW, seed


def declaration(a):
    return {"schema_version": 1, "id": "study", "account": a["name"],
            "hypothesis": "question <script> is useful?", "change": "opening ``` words",
            "decision": {"status": "adopted", "by": "person", "at": jst.iso(NOW - datetime.timedelta(days=7))},
            "baseline_post_ids": ["before"], "changed_post_ids": ["after"]}


def write(a, data):
    path = Path(a["repo_dir"]) / "study.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def test_observation_linkage_old_ids_zero_and_read_only(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "before", NOW - datetime.timedelta(days=100), value=0)
    seed(a, "after", NOW - datetime.timedelta(days=7), value=8)
    path = write(a, declaration(a))
    root = Path(a["repo_dir"])
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    r = study_report.answer(path, min_n=1, now=NOW)
    assert r["decision_provenance"] == "user_declared_unverified"
    assert r["interpretation"] == "observational_difference"
    assert r["observations"]["baseline"]["metrics"]["views"]["median"] == 0
    assert r["comparison"]["views"]["absolute_median_change"] == 8
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    markdown = study_report.render_markdown(r)
    assert "<script>" not in markdown.split("## 宣言と観測の構造化データ")[0]
    assert json.loads("\n".join(s[4:] for s in markdown.splitlines() if s.startswith("    "))) == r
    assert '"username"' not in json.dumps(r) and '"text"' not in json.dumps(r)


def test_proposed_never_loads_ledgers(isolated_account_factory, monkeypatch):
    a = isolated_account_factory()
    d = declaration(a)
    d["decision"] = {"status": "proposed"}
    monkeypatch.setattr(study_report.accounts, "load_account", lambda *a: pytest.fail("no ledger access"))
    r = study_report.answer(write(a, d), now=NOW)
    assert r["observations"] is r["comparison"] is r["data_updated_at"] is None
    assert r["cannot_say"]


def test_explicit_only_decision_boundary_missing_and_classification(isolated_account_factory):
    a = isolated_account_factory()
    at = NOW - datetime.timedelta(days=7)
    for pid, when in [("wrongbefore", at), ("wrongafter", at-datetime.timedelta(seconds=1)), ("unlisted", at), ("reply", at), ("collapsed", at), ("conflict", at), ("foreign", at), ("future", NOW)]:
        seed(a, pid, when)
    seed(a, "foreign", at, extra={"account": "other"})
    seed(a, "collapsed", at, marks=[1, 24])
    _seed_engagement(a, "reply", posted_at=jst.iso(at))
    row = seed(a, "conflict", at, age=1, marks=[1])
    later = dict(row, collected_at=jst.iso(at+datetime.timedelta(hours=25)), reply_to="parent", marks=[24])
    _write_ndjson(_insight_path(a, "conflict"), [row, later])
    d = declaration(a)
    d["baseline_post_ids"] = ["wrongbefore"]
    d["changed_post_ids"] = ["wrongafter", "reply", "conflict", "foreign", "missing", "collapsed", "future"]
    r = study_report.answer(write(a, d), now=NOW, min_n=1)
    base, changed = r["observations"].values()
    assert base["excluded"] == [{"post_id": "wrongbefore", "reason": "wrong_side_of_decision_time"}]
    excluded = {x["post_id"]: x["reason"] for x in changed["excluded"]}
    assert excluded == {"wrongafter": "wrong_side_of_decision_time", "reply": "reply_not_root", "conflict": "conflicting_root_classification", "foreign": "unknown_or_unowned_id", "missing": "unknown_or_unowned_id", "future": "at_or_after_report_time"}
    assert changed["n_total"] == changed["n_missing"] == 1
    assert changed["n_requested"] == 7
    assert all(v["absolute_median_change"] is None for v in r["comparison"].values())
    assert r["data_updated_at"] is None


@pytest.mark.parametrize("edit", [
    lambda d: d.update(schema_version=True), lambda d: d.update(extra="SECRET"),
    lambda d: d.pop("id"), lambda d: d.update(id=""),
    lambda d: d.update(baseline_post_ids=[1]), lambda d: d.update(baseline_post_ids=[True]),
    lambda d: d.update(baseline_post_ids=["x", "x"]), lambda d: d.update(changed_post_ids=["before"]),
    lambda d: d.update(changed_post_ids=[str(i) for i in range(1001)]),
    lambda d: d["decision"].pop("by"), lambda d: d["decision"].update(by=""),
    lambda d: d["decision"].update(at="2026-09-19T00:00:00+09:00"),
    lambda d: d["decision"].update(at="2026-09-17T00:00:00"),
    lambda d: d["decision"].update(status=True), lambda d: d["decision"].update(extra="SECRET"),
])
def test_strict_schema_bounded_errors(isolated_account_factory, edit):
    a = isolated_account_factory()
    d = declaration(a)
    d["hypothesis"] = "SECRET"
    edit(d)
    with pytest.raises(study_report.StudyError) as exc:
        study_report.answer(write(a, d), now=NOW)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("raw", ['{"id":"a","id":"b"}', '{"x":NaN}', '{broken SECRET', '[]'])
def test_invalid_json(isolated_account_factory, raw):
    a = isolated_account_factory()
    path = write(a, declaration(a))
    path.write_text(raw)
    with pytest.raises(study_report.StudyError):
        study_report.answer(path, now=NOW)


def test_file_limits_regular_input_and_min_n(isolated_account_factory):
    a = isolated_account_factory()
    path = write(a, declaration(a))
    path.write_bytes(b"x"*(study_report.MAX_BYTES+1))
    with pytest.raises(study_report.StudyError, match="1MiB"):
        study_report.answer(path, now=NOW)
    with pytest.raises(study_report.StudyError):
        study_report.answer(path.parent, now=NOW)
    with pytest.raises(study_report.StudyError):
        study_report.answer(path, min_n=True, now=NOW)


def test_cli_mcp_and_file_boundary(isolated_account_factory, monkeypatch, capsys, tmp_path):
    a = isolated_account_factory()
    path = write(a, declaration(a))
    monkeypatch.setattr(study_report.jst, "now_jst", lambda: NOW)
    assert cli.main(["study-report", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == study_report.answer(path, now=NOW)
    assert cli.main(["study-report", str(path)]) == 0
    assert "# Study review" in capsys.readouterr().out
    server = _load_server_module()
    reply = server.call_tool("study_report", {"file": str(path)})
    assert not reply["isError"]
    result = json.loads(reply["content"][0]["text"])
    assert result == study_report.answer(path, now=jst.parse(result["generated_at"]))
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("study_report", {"file": str(tmp_path/"outside.json")})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("study_report", {"file": str(path), "min_n": True})


def test_broken_source_immature_and_sparse_remain_unknown(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "before", NOW-datetime.timedelta(days=8), value=0)
    seed(a, "after", NOW-datetime.timedelta(hours=1), age=0.5)
    Path(_insight_path(a, "broken")).write_text("{broken")
    r = study_report.answer(write(a, declaration(a)), now=NOW)
    assert r["incomplete_sources"]["measured_files"] == 1
    assert r["observations"]["changed"]["n_immature"] == 1
    assert r["observations"]["baseline"]["metrics"]["views"]["n_eligible"] == 1
    assert all(v["absolute_median_change"] is None for v in r["comparison"].values())
    assert len(r["cannot_say"]) == 3
    assert r["cannot_say"][-1] == "forecast_assumes_collection_runs"


def test_invalid_cli_is_error_without_input_disclosure(isolated_account_factory, capsys):
    a = isolated_account_factory()
    d = declaration(a)
    d["SECRET"] = "secret contents"
    path = write(a, d)
    assert cli.main(["study-report", str(path), "--json"]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "SECRET" not in captured.err and "secret contents" not in captured.err


def test_extreme_invalid_ledger_time_is_excluded(isolated_account_factory):
    a = isolated_account_factory()
    seed(a, "before", NOW-datetime.timedelta(days=8), extra={"posted_at": "0001-01-01T00:00:00+23:00"})
    r = study_report.answer(write(a, declaration(a)), now=NOW)
    assert r["observations"]["baseline"]["excluded"] == [{"post_id": "before", "reason": "invalid_root_posted_at"}]
