import copy
import dataclasses
import json

import pytest

from thth import analytics_report, operations_handoff, report_service as service, jst

NOW = jst.parse("2026-09-18T12:00:00+09:00")


def test_context_copies_and_freezes_host_mapping():
    original = {"one":"shared"}
    context = service.ReportContext(original)
    original["outside"] = "shared"
    assert dict(context.allowed_accounts) == {"one":"shared"}
    with pytest.raises(TypeError):
        context.allowed_accounts["outside"] = "shared"
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.allowed_accounts = {}
    for value in ({"../outside":"p"}, {"a":True}, {"a":""}, [], {True:"p"}):
        with pytest.raises(service.ReportServiceError, match="invalid_context"):
            service.ReportContext(value)


@pytest.mark.parametrize("input_request", [
    {}, [], {"operation":"send", "account":"one"},
    {"operation":True, "account":"one"},
    {"operation":"analytics_report", "account":"one", "project":"p"},
    {"operation":"analytics_report", "account":None},
    {"operation":"analytics_report", "account":True},
    {"operation":"analytics_report", "account":" "},
    {"operation":"analytics_report", "account":"outside"},
    {"operation":"analytics_report", "project":"outside"},
    {"operation":"analytics_report", "account":"one", "window_days":True},
    {"operation":"analytics_report", "account":"one", "min_n":0},
    {"operation":"analytics_report", "account":"one", "compare":True},
    {"operation":"operations_handoff", "account":"one", "window_days":7},
    *[{"operation":"analytics_report", "account":"one", key:"secret"}
      for key in ("tenant", "root", "env", "path", "auth", "output")],
])
def test_reject_before_any_core_read(input_request, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("core must not be called")
    monkeypatch.setattr(analytics_report, "answer", fail)
    monkeypatch.setattr(operations_handoff, "answer", fail)
    monkeypatch.setattr(service.accounts, "load_account", fail)
    with pytest.raises(service.ReportServiceError):
        service.execute_report(service.ReportContext({"one":"p"}), input_request)


def test_project_expands_only_trusted_accounts_request_unchanged(monkeypatch):
    calls = []
    def answer(name, **kwargs):
        calls.append((name, kwargs))
        return {"name":name}
    monkeypatch.setattr(analytics_report, "answer", answer)
    monkeypatch.setattr(service.accounts, "list_account_names", lambda: pytest.fail("registry enumerated"))
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    context = service.ReportContext({"two":"p", "one":"p", "another":"q"})
    request = {"operation":"analytics_report", "project":"p", "window_days":14, "min_n":3}
    before = copy.deepcopy(request)
    result = service.execute_report(context, request)
    assert request == before
    assert [call[0] for call in calls] == ["one", "two"]
    assert all(call[1] == {"now":NOW,"window_days":14,"min_n":3} for call in calls)
    assert result["reports"] == {"one":{"name":"one"}, "two":{"name":"two"}}
    markdown = service.render_markdown(result)
    assert json.loads("\n".join(line[4:] for line in markdown.splitlines() if line.startswith("    "))) == result


@pytest.mark.parametrize("operation, module", [("analytics_report",analytics_report), ("operations_handoff",operations_handoff)])
def test_existing_result_preserved(operation, module, isolated_account_factory, monkeypatch):
    cfg = isolated_account_factory(name="one", project="p")
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    context = service.ReportContext({"one":"p"})
    result = service.execute_report(context, {"operation":operation,"account":"one"})
    assert result["reports"]["one"] == module.answer("one", now=NOW)


def test_failure_hides_core_exception_and_resource_existence(monkeypatch):
    context = service.ReportContext({"one":"p"})
    def failed(*args, **kwargs):
        raise OSError("/private/tenant/secret token")
    monkeypatch.setattr(operations_handoff, "answer", failed)
    with pytest.raises(service.ReportServiceError, match="^report_unavailable$"):
        service.execute_report(context, {"operation":"operations_handoff","account":"one"})
    for name in ("nonexistent", "existing_other_tenant"):
        with pytest.raises(service.ReportServiceError, match="^scope_unavailable$"):
            service.execute_report(context, {"operation":"operations_handoff","account":name})
