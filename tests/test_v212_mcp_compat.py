"""Server MCP keeps legacy read shapes inside a trusted credential scope."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from thth import admin_report, jst, study_report
from thth.report_service import (ReportContext, ReportServiceError,
                                 execute_mcp_report, execute_report)
from tests.test_mcp import _load_server_module


NOW = jst.parse("2026-09-20T12:00:00+09:00")


@pytest.fixture
def scoped(isolated_account_factory, tmp_path, monkeypatch):
    repos = {name: tmp_path / ("repo-" + name) for name in ("one", "two", "foreign")}
    for name, repo in repos.items():
        repo.mkdir()
        isolated_account_factory(name=name, repo_dir=str(repo), project="shared")
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    return repos


@pytest.mark.parametrize("operation", ["analytics_report", "operations_handoff"])
@pytest.mark.parametrize("scope", ["user", "admin"])
def test_project_legacy_shape_uses_only_trusted_names(scoped, monkeypatch, operation, scope):
    from thth import accounts
    monkeypatch.setattr(accounts, "list_account_names",
                        lambda: pytest.fail("global registry enumerated"))
    context = ReportContext({"one": "shared", "two": "shared"}, scope=scope)
    request = {"operation": operation, "project": "shared"}
    if operation == "analytics_report":
        request.update(compare_previous=True, min_n=1)
    payload = execute_mcp_report(context, request)
    assert payload["report_type"] == ("period_comparison" if operation == "analytics_report"
                                       else "operations_handoff")
    assert set(payload["by_account"]) == {"one", "two"}
    assert "reports" not in payload and "foreign" not in json.dumps(payload)
    assert payload["filters"] == {"account": None, "project": "shared"}


@pytest.mark.parametrize("operation", ["analytics_report", "operations_handoff"])
def test_one_account_legacy_shape_and_http_batch_both_survive(scoped, operation):
    context = ReportContext({"one": "shared"})
    request = {"operation": operation, "account": "one"}
    direct = execute_mcp_report(context, request)
    batch = execute_report(context, request)
    assert direct["by_account"]["one"] == batch["reports"]["one"]["by_account"]["one"]
    assert batch["report_type"] == "scoped_report_batch"
    assert direct["report_type"] != batch["report_type"]


def test_nested_owner_lookup_reads_only_allowed_accounts_on_both_transports(
        isolated_account_factory, tmp_path, monkeypatch):
    """Changing an unpermitted ledger cannot change a permitted report."""
    from thth import accounts
    from thth import operations_handoff
    repo = tmp_path / "owned"
    repo.mkdir()
    foreign_repo = tmp_path / "outside-scope"
    foreign_repo.mkdir()
    isolated_account_factory("one", repo_dir=str(repo), project="p", handle="one")
    isolated_account_factory("two", repo_dir=str(repo), project="p", handle="two")
    isolated_account_factory("foreign", repo_dir=str(foreign_repo), project="p",
                             handle="unrelated")
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    posted = "2026-09-18T12:00:00+09:00"
    collected = "2026-09-19T12:00:00+09:00"
    state = Path(accounts.state_dir_for("one")) / "sent"
    _write(state / "101.json", {"post_id": "101", "sent_at": posted,
                                "reply_to": None, "text": "fixture"})
    reply_dir = Path(accounts.data_dirs(accounts.load_account("one"), "one")["replies"])
    reply_dir.mkdir(parents=True)
    reply_dir.joinpath("101.ndjson").write_text("\n".join(json.dumps(row) for row in (
        {"kind": "reply", "post_id": "101", "account": "one", "medium": "threads",
         "id": "201", "username": "reply-handle", "text": "fixture",
         "timestamp": collected, "collected_at": collected, "replied_to": "101"},
        {"kind": "fetch", "post_id": "101", "account": "one", "medium": "threads",
         "collected_at": collected})) + "\n", encoding="utf-8")
    cfg_path = Path(accounts.accounts_dir()) / "foreign.json"
    original = accounts.load_account
    reads = []

    def checked(name):
        if name == "foreign":
            reads.append(name)
            raise AssertionError("unpermitted account ledger read")
        return original(name)

    context = ReportContext({"one": "p", "two": "p"})
    results = []
    for handle in ("unrelated", "reply-handle"):
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["handle"] = handle
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        with monkeypatch.context() as local:
            local.setattr(accounts, "load_account", checked)
            for operation in ("operations_handoff", "analytics_report"):
                request = {"operation": operation, "account": "one"}
                direct = execute_mcp_report(context, request)
                batch = execute_report(context, request)
                assert direct["by_account"]["one"] == batch["reports"]["one"]["by_account"]["one"]
                results.append((operation, direct["by_account"]["one"].get("unanswered"),
                                direct["by_account"]["one"].get("engagements", {}).get("replies_back_24h")))
    assert reads == []
    assert results[:2] == results[2:]

    # A displayed alone still uses a second *permitted* account's owner handle.
    cfg_two = Path(accounts.accounts_dir()) / "two.json"
    value = json.loads(cfg_two.read_text(encoding="utf-8"))
    value["handle"] = "reply-handle"
    cfg_two.write_text(json.dumps(value), encoding="utf-8")
    allowed = execute_mcp_report(context, {"operation": "operations_handoff", "account": "one"})
    one_only = execute_mcp_report(ReportContext({"one": "p"}),
                                  {"operation": "operations_handoff", "account": "one"})
    assert allowed["by_account"]["one"]["unanswered"]["n"] == 0
    assert one_only["by_account"]["one"]["unanswered"]["n"] == 1

    # With every registry account permitted, the unchanged CLI calculation agrees.
    full = execute_mcp_report(ReportContext({"one": "p", "two": "p", "foreign": "p"}),
                              {"operation": "operations_handoff", "account": "one"})
    cli = operations_handoff.answer("one", now=jst.parse(full["generated_at"]))
    assert full == cli


def test_http_read_scope_is_context_local_nested_and_reset_on_error(scoped, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from thth import accounts, analytics_report, replies, unanswered
    assert replies.active_report_scope() is None
    original = accounts.load_account
    def no_foreign(name):
        if name == "foreign":
            pytest.fail("foreign ledger reached")
        return original(name)
    with monkeypatch.context() as local:
        local.setattr(accounts, "load_account", no_foreign)
        with replies.report_scope(("one",)):
            with pytest.raises(accounts.AccountError, match="scope_unavailable"):
                replies.load("foreign")
            with pytest.raises(accounts.AccountError, match="scope_unavailable"):
                unanswered.answer("foreign", now=NOW)
    assert replies.active_report_scope() is None
    with replies.report_scope(("one", "two")):
        assert replies.active_report_scope() == ("one", "two")
        with replies.report_scope(("one",)):
            assert replies.active_report_scope() == ("one",)
        assert replies.active_report_scope() == ("one", "two")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(replies.active_report_scope).result() is None
    assert replies.active_report_scope() is None

    def fails(*args, **kwargs):
        assert replies.active_report_scope() == ("one",)
        raise OSError("fixture private path")

    monkeypatch.setattr(analytics_report, "answer", fails)
    with pytest.raises(ReportServiceError, match="^report_unavailable$"):
        execute_report(ReportContext({"one": "shared"}),
                       {"operation": "analytics_report", "account": "one"})
    assert replies.active_report_scope() is None


def test_shared_repo_legacy_reply_without_owner_is_not_claimed_by_scoped_report(
        isolated_account_factory, tmp_path, monkeypatch):
    from thth import accounts, operations_handoff
    repo = tmp_path / "shared-ledger"
    repo.mkdir()
    isolated_account_factory("one", repo_dir=str(repo), project="p", handle="one")
    isolated_account_factory("foreign", repo_dir=str(repo), project="p", handle="foreign")
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    for name in ("one", "foreign"):
        _write(Path(accounts.state_dir_for(name)) / "sent" / "101.json",
               {"post_id": "101", "sent_at": "2026-09-18T12:00:00+09:00",
                "reply_to": None, "text": "fixture"})
    reply_dir = Path(accounts.data_dirs(accounts.load_account("one"), "one")["replies"])
    reply_dir.mkdir(parents=True)
    reply_dir.joinpath("101.ndjson").write_text(json.dumps({
        "kind": "reply", "post_id": "101", "id": "201", "username": "participant",
        "text": "fixture", "timestamp": "2026-09-19T12:00:00+09:00",
        "collected_at": "2026-09-19T12:00:00+09:00", "replied_to": "101"}) + "\n",
        encoding="utf-8")
    scoped = execute_mcp_report(ReportContext({"one": "p"}),
                                {"operation": "operations_handoff", "account": "one"})
    node = scoped["by_account"]["one"]
    assert node["unanswered"]["n"] == 0
    assert "reply_scope_ambiguous" in node["cannot_say"]
    complete = execute_mcp_report(ReportContext({"one": "p", "foreign": "p"}),
                                  {"operation": "operations_handoff", "account": "one"})
    cli = operations_handoff.answer("one", now=jst.parse(complete["generated_at"]))
    assert complete == cli
    row = json.loads(reply_dir.joinpath("101.ndjson").read_text(encoding="utf-8"))
    row["account"] = "one"  # Old medium omission still has a unique owner.
    reply_dir.joinpath("101.ndjson").write_text(json.dumps(row) + "\n", encoding="utf-8")
    owned = execute_mcp_report(ReportContext({"one": "p"}),
                               {"operation": "operations_handoff", "account": "one"})
    assert owned["by_account"]["one"]["unanswered"]["n"] == 1


def test_full_registry_scope_preserves_single_account_legacy_reply(
        isolated_account_factory, tmp_path, monkeypatch):
    from thth import accounts, operations_handoff
    repo = tmp_path / "only-repo"
    repo.mkdir()
    isolated_account_factory("one", repo_dir=str(repo), project="p", handle="one")
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    _write(Path(accounts.state_dir_for("one")) / "sent" / "101.json",
           {"post_id": "101", "sent_at": "2026-09-18T12:00:00+09:00",
            "reply_to": None, "text": "fixture"})
    reply_dir = Path(accounts.data_dirs(accounts.load_account("one"), "one")["replies"])
    reply_dir.mkdir(parents=True)
    reply_dir.joinpath("101.ndjson").write_text(json.dumps({
        "kind": "reply", "post_id": "101", "id": "201", "username": "participant",
        "text": "fixture", "timestamp": "2026-09-19T12:00:00+09:00",
        "collected_at": "2026-09-19T12:00:00+09:00", "replied_to": "101"}) + "\n",
        encoding="utf-8")
    report = execute_mcp_report(ReportContext({"one": "p"}),
                                {"operation": "operations_handoff", "account": "one"})
    cli = operations_handoff.answer("one", now=jst.parse(report["generated_at"]))
    assert report == cli
    assert report["by_account"]["one"]["unanswered"]["n"] == 1


def _declaration(account="one", identifier="fixture"):
    return {"schema_version": 1, "id": identifier, "account": account,
            "hypothesis": "h", "change": "c", "decision": {"status": "proposed"},
            "baseline_post_ids": [], "changed_post_ids": []}


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("scope", ["user", "admin"])
def test_study_same_bytes_as_cli_and_no_second_file_open(scoped, monkeypatch, scope):
    file = _write(scoped["one"] / "study.json", _declaration())
    expected = study_report.answer(file, min_n=1, now=NOW)
    monkeypatch.setattr(study_report, "load_declaration",
                        lambda *a, **k: pytest.fail("study reopened the file"))
    payload = execute_mcp_report(ReportContext({"one": "shared"}, scope=scope),
                                 {"operation": "study_report", "file": str(file), "min_n": 1})
    assert payload == expected
    assert payload["report_type"] == "study_review"


def test_study_path_swap_after_parse_uses_checked_bytes(scoped, monkeypatch):
    file = _write(scoped["one"] / "study.json", _declaration(identifier="before"))
    original = study_report.parse_declaration_bytes
    def swap_after_parse(data, now):
        value = original(data, now)
        _write(file, _declaration(account="foreign", identifier="after"))
        return value
    monkeypatch.setattr(study_report, "parse_declaration_bytes", swap_after_parse)
    payload = execute_mcp_report(ReportContext({"one": "shared"}),
                                 {"operation": "study_report", "file": str(file)})
    assert payload["declaration"]["id"] == "before"


def test_adopted_study_shape_uses_authorized_owner_universe(scoped, monkeypatch):
    """A real 24h reply shape must ignore C and still recognize permitted B."""
    import datetime
    from thth import accounts
    from tests.test_analytics_comparison import seed
    posted = NOW - datetime.timedelta(hours=48)
    seed({"name": "one", "repo_dir": str(scoped["one"])}, "101", posted,
         age=25, value=10)
    reply_dir = Path(accounts.data_dirs(accounts.load_account("one"), "one")["replies"])
    reply_dir.mkdir(parents=True)
    reply_dir.joinpath("101.ndjson").write_text("\n".join(json.dumps(row) for row in (
        {"kind": "reply", "post_id": "101", "account": "one", "medium": "threads",
         "id": "201", "username": "reply-handle", "text": "private fixture body",
         "timestamp": jst.iso(posted + datetime.timedelta(hours=1)),
         "collected_at": jst.iso(posted + datetime.timedelta(hours=23)),
         "replied_to": "101"},
        {"kind": "fetch", "post_id": "101", "account": "one", "medium": "threads",
         "collected_at": jst.iso(posted + datetime.timedelta(hours=23))})) + "\n",
        encoding="utf-8")
    study = _declaration()
    study["decision"] = {"status": "adopted", "by": "fixture",
                          "at": jst.iso(posted - datetime.timedelta(hours=1))}
    study["changed_post_ids"] = ["101"]
    file = _write(scoped["one"] / "study.json", study)
    for name, handle in (("one", "one"), ("two", "two"), ("foreign", "unrelated")):
        cfg = Path(accounts.accounts_dir()) / (name + ".json")
        value = json.loads(cfg.read_text(encoding="utf-8"))
        value["handle"] = handle
        cfg.write_text(json.dumps(value), encoding="utf-8")
    foreign = Path(accounts.accounts_dir()) / "foreign.json"
    original = accounts.load_account
    reads = []

    def checked(name):
        if name == "foreign":
            reads.append(name)
            pytest.fail("unpermitted account ledger read")
        return original(name)

    def shape(result):
        return result["observations"]["changed"]["marks_by_post"][0]["shape_at"]["24"]

    results = []
    for handle in ("unrelated", "reply-handle"):
        cfg = json.loads(foreign.read_text(encoding="utf-8"))
        cfg["handle"] = handle
        foreign.write_text(json.dumps(cfg), encoding="utf-8")
        with monkeypatch.context() as local:
            local.setattr(accounts, "load_account", checked)
            payload = execute_mcp_report(ReportContext({"one": "shared"}),
                                         {"operation": "study_report", "file": str(file),
                                          "min_n": 1})
        results.append(shape(payload))
        assert "private fixture body" not in json.dumps(payload)
    assert reads == []
    assert results[0] == results[1]
    assert results[0]["other_replies"] == 1 and results[0]["author_replies"] == 0

    cfg_two = Path(accounts.accounts_dir()) / "two.json"
    value = json.loads(cfg_two.read_text(encoding="utf-8"))
    value["handle"] = "reply-handle"
    cfg_two.write_text(json.dumps(value), encoding="utf-8")
    with monkeypatch.context() as local:
        local.setattr(accounts, "load_account", checked)
        allowed = execute_mcp_report(ReportContext({"one": "shared", "two": "shared"}),
                                     {"operation": "study_report", "file": str(file),
                                      "min_n": 1})
    assert shape(allowed)["author_replies"] == 1
    assert shape(allowed)["other_replies"] == 0
    assert reads == []
    full = execute_mcp_report(ReportContext({"one": "shared", "two": "shared",
                                             "foreign": "shared"}),
                              {"operation": "study_report", "file": str(file), "min_n": 1})
    assert full == study_report.answer(file, min_n=1, now=NOW)


def test_study_proposed_scope_check_precedes_all_observation_ledgers(scoped, monkeypatch):
    from thth import accounts, measured
    file = _write(scoped["one"] / "proposed.json", _declaration())
    monkeypatch.setattr(accounts, "load_account", lambda *a: pytest.fail("account ledger read"))
    monkeypatch.setattr(measured, "load", lambda *a, **k: pytest.fail("observation read"))
    with pytest.raises(study_report.StudyError, match="^scope_unavailable$"):
        study_report.answer(file, now=NOW, allowed_names=("two",))
    result = study_report.answer(file, now=NOW, allowed_names=("one",))
    assert result["observations"] is None and result["comparison"] is None


@pytest.mark.parametrize("kind", ["outside", "foreign_path", "foreign_declaration",
                                   "other_allowed_repo", "leaf_symlink", "ancestor_symlink",
                                   "repo_ancestor_symlink", "hardlink", "fifo", "malformed"])
def test_study_refuses_unsafe_or_foreign_inputs(scoped, tmp_path, kind):
    repo = scoped["one"]
    file = _write(repo / "study.json", _declaration())
    allowed = {"one": "shared", "two": "shared"} if kind == "other_allowed_repo" else {"one": "shared"}
    if kind == "outside":
        file = _write(tmp_path / "outside.json", _declaration())
    elif kind == "foreign_path":
        file = _write(scoped["foreign"] / "study.json", _declaration())
    elif kind == "foreign_declaration":
        file = _write(file, _declaration(account="foreign"))
    elif kind == "other_allowed_repo":
        file = _write(file, _declaration(account="two"))
    elif kind == "leaf_symlink":
        link = repo / "link.json"
        link.symlink_to(file)
        file = link
    elif kind == "ancestor_symlink":
        target = repo / "real"
        _write(target / "study.json", _declaration())
        link = repo / "alias"
        link.symlink_to(target, target_is_directory=True)
        file = link / "study.json"
    elif kind == "repo_ancestor_symlink":
        link = tmp_path / "repo-alias"
        link.symlink_to(repo, target_is_directory=True)
        from thth import accounts
        cfg_file = Path(accounts.accounts_dir()) / "one.json"
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        cfg["repo_dir"] = str(link)
        cfg_file.write_text(json.dumps(cfg), encoding="utf-8")
        file = link / "study.json"
    elif kind == "hardlink":
        link = repo / "hard.json"
        os.link(file, link)
        file = link
    elif kind == "fifo":
        file = repo / "pipe.json"
        os.mkfifo(file)
    elif kind == "malformed":
        file.write_text("{bad", encoding="utf-8")
    with pytest.raises(ReportServiceError) as exc:
        execute_mcp_report(ReportContext(allowed),
                           {"operation": "study_report", "file": str(file)})
    assert str(exc.value) in {"scope_unavailable", "invalid_options", "report_unavailable"}
    assert str(tmp_path) not in str(exc.value)


def test_admin_log_invalid_event_type_is_options_before_io(scoped, monkeypatch):
    monkeypatch.setattr(admin_report, "answer", lambda *a, **k: pytest.fail("log I/O"))
    context = ReportContext({"one": "shared"}, scope="admin")
    for value in ([], 42, "nonsense"):
        with pytest.raises(ReportServiceError, match="^invalid_options$"):
            execute_report(context, {"operation": "admin_log", "event": value})


def test_server_tools_keep_reads_and_admin_has_no_write_tool(scoped, monkeypatch):
    server = _load_server_module()
    user = ReportContext({"one": "shared"})
    admin = ReportContext({"one": "shared"}, scope="admin")
    for context in (user, admin):
        names = {tool["name"] for tool in server.server_tools(context)}
        assert {"analytics_report", "operations_handoff", "study_report"} <= names
    admin_names = {tool["name"] for tool in server.server_tools(admin)}
    assert "thth_admin_log" in admin_names
    assert not any(name in admin_names for name in ("thth_send_request", "thth_approve_request"))
    monkeypatch.setattr(server, "authenticated_context", lambda: admin)
    assert server.server_call("thth_send_request", {"account": "one"})["content"][0]["text"] == "unsupported_operation"


def test_partial_credential_environment_never_falls_back_to_cli(scoped, monkeypatch):
    server = _load_server_module()
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(scoped["one"] / "missing.json"))
    monkeypatch.delenv("THTH_REPORT_TOKEN", raising=False)
    monkeypatch.setattr(server, "run_cli", lambda *a, **k: pytest.fail("CLI fallback"))
    assert server.server_mode() is True
    response = server.call_tool("analytics_report", {"account": "one"})
    assert response["isError"] is True
    assert response["content"][0]["text"] == "unauthorized"
