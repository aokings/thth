import builtins
import io
import json
from pathlib import Path
import socket
import subprocess

import pytest

from thth import accounts, cli, jst, operations_handoff as handoff
from tests.test_after_cli import _load_server_module

NOW = jst.parse("2026-09-18T12:00:00+09:00")


def seed(cfg, filename, status, **extra):
    queue = Path(cfg["repo_dir"]) / cfg["queue_dir"]
    queue.mkdir(parents=True, exist_ok=True)
    fm = {"thth": "1", "account": cfg["name"], "status": status,
          "publish_at": "2026-09-18T09:00:00+09:00", **extra}
    (queue / filename).write_text("---\n" + "\n".join(f"{k}: {v}" for k,v in fm.items()) + "\n---\nPRIVATE BODY\n")


def test_local_only_scoped_counts_and_evidence(isolated_account_factory, monkeypatch):
    one = isolated_account_factory(name="one", project="same")
    other = isolated_account_factory(name="other", project="outside")
    seed(one, "one.md", "approved")
    seed(one, "draft.md", "draft")
    seed(other, "other.md", "draft")
    state = Path(accounts.state_dir_for("one"))
    state.mkdir(parents=True, exist_ok=True)
    (state / "inflight.json").write_text(json.dumps({"file": "one.md", "started": "2026-09-18T09:00:00+09:00"}))
    (state / "healthcheck-status.json").write_text(json.dumps({"last_state": "success", "last_attempt_at": "2026-01-01T09:00:00+09:00"}))
    before = {p: p.read_bytes() for p in Path(accounts.thth_root()).rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError("network/subprocess/secret read forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(accounts, "load_env", forbidden)
    real_open, real_io_open = builtins.open, io.open
    def read_only_open(original):
        def guarded(file, mode="r", *args, **kwargs):
            assert not any(flag in mode for flag in "wax+"), "filesystem write forbidden"
            return original(file, mode, *args, **kwargs)
        return guarded
    monkeypatch.setattr(builtins, "open", read_only_open(real_open))
    monkeypatch.setattr(io, "open", read_only_open(real_io_open))
    payload = handoff.answer("one", now=NOW)
    after = {p: p.read_bytes() for p in Path(accounts.thth_root()).rglob("*") if p.is_file()}
    assert before == after
    row = payload["by_account"]["one"]
    assert row["state"] == "blocked"
    assert row["queue"]["counts"]["approval_needed"] == 1
    assert row["queue"]["counts"]["overdue"] == 1
    assert row["inflight"]["next_action_code"]
    assert row["last_run_notification"]["timer_health"] == "unknown"
    assert row["last_run_notification"]["recorded_at"].startswith("2026-01-01")
    assert row["evidence"]["run_notification"]["freshness"] == "unknown"
    text = json.dumps(payload)
    assert "PRIVATE BODY" not in text and "other.md" not in text and str(state) not in text
    markdown = handoff.render_markdown(payload)
    assert json.loads("\n".join(line[4:] for line in markdown.splitlines() if line.startswith("    "))) == payload


def test_missing_corrupt_and_waiting(isolated_account_factory):
    cfg = isolated_account_factory()
    seed(cfg, "later.md", "approved", publish_at="2026-10-01T09:00:00+09:00")
    name = cfg["name"]
    node = handoff.answer(name, now=NOW)["by_account"][name]
    assert node["state"] == "waiting"
    assert node["notifications"]["mail_pending"] is None
    state = Path(accounts.state_dir_for(name))
    state.mkdir(parents=True, exist_ok=True)
    (state / "inflight.json").write_text("{broken")
    node = handoff.answer(name, now=NOW)["by_account"][name]
    assert node["state"] == "unknown"
    assert "inflight_unreadable" in node["cannot_say"]
    (state / "inflight.json").unlink()
    (state / "incident-outbox.json").write_text("{broken")
    assert handoff.answer(name, now=NOW)["by_account"][name]["notifications"]["availability"] == "unreadable"


def test_project_cli_mcp_and_errors(isolated_account_factory, monkeypatch, capsys):
    one = isolated_account_factory(name="one", project="same")
    isolated_account_factory(name="two", project="same")
    isolated_account_factory(name="other", project="outside")
    assert set(handoff.answer(project="same")["by_account"]) == {"one", "two"}
    monkeypatch.setattr(handoff.jst, "now_jst", lambda: NOW)
    assert cli.main(["handoff-report", "one", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == handoff.answer("one", now=NOW)
    assert cli.main(["handoff-report", "--project", "missing", "--json"]) == 2
    assert capsys.readouterr().out == ""
    server = _load_server_module()
    result = server.call_tool("operations_handoff", {"account": "one"})
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["report_type"] == "operations_handoff"
    for args in ({}, {"account":"one", "project":"same"}, {"account":True}, {"account":None}, {"account":"one", "extra":1}):
        with pytest.raises(server.ToolInputError):
            server.validate_arguments("operations_handoff", args)
    for args in ({}, {"account_name":True}, {"account_name":"one", "project":"same"}, {"project":"missing"}):
        with pytest.raises(handoff.HandoffError):
            handoff.answer(**args)


def test_recorded_failure_future_post_and_unattributed_file(isolated_account_factory):
    cfg = isolated_account_factory()
    seed(cfg, "waiting.md", "approved", publish_at="2026-10-01T09:00:00+09:00")
    seed(cfg, "future.md", "posted", posted_at="2027-10-01T09:00:00+09:00")
    queue = Path(cfg["repo_dir"]) / cfg["queue_dir"]
    (queue / "private-foreign.md").write_text("broken secret body")
    state = Path(accounts.state_dir_for(cfg["name"]))
    state.mkdir(parents=True, exist_ok=True)
    (state / "healthcheck-status.json").write_text(json.dumps({
        "last_state":"fail", "reason_code":"missing_token", "delivery":"failed",
        "last_attempt_at":"2026-09-17T09:00:00+09:00"}))
    node = handoff.answer(cfg["name"], now=NOW)["by_account"][cfg["name"]]
    assert node["state"] == "review_required"
    assert node["last_post"]["observed_at"] is None
    assert node["last_run_notification"]["reason_code"] == "missing_token"
    assert node["last_run_notification"]["next_action_code"]
    assert node["queue"]["counts"]["unattributed_malformed"] == 1
    assert "private-foreign" not in json.dumps(node)
    (queue / "private-foreign.md").unlink()
    (state / "healthcheck-status.json").write_text("broken")
    node = handoff.answer(cfg["name"], now=NOW)["by_account"][cfg["name"]]
    assert node["state"] == "unknown"
    assert "run_notification_unreadable" in node["cannot_say"]


def test_delivered_incident_failure_still_requires_review(isolated_account_factory):
    cfg = isolated_account_factory()
    seed(cfg, "waiting.md", "approved", publish_at="2026-10-01T09:00:00+09:00")
    state = Path(accounts.state_dir_for(cfg["name"]))
    state.mkdir(parents=True, exist_ok=True)
    (state / "incident-outbox.json").write_text(json.dumps({"version":1,"last":"fail","events":[{
        "id":"a"*32,"state":"blocked","at":"2026-09-17T09:00:00+09:00",
        "reason":"missing_token","accepted":{"user":"b"*64,"admin":"c"*64},
        "repo":"written","file":None}]}))
    node = handoff.answer(cfg["name"], now=NOW)["by_account"][cfg["name"]]
    assert node["state"] == "review_required"
    assert node["notifications"]["mail_pending"] == 0
    assert node["notifications"]["reason_code"] == "missing_token"


def test_queue_evidence_keys_stable_without_repo(isolated_account_factory, monkeypatch):
    item = isolated_account_factory()
    cfg = accounts.load_account(item["name"])
    configured = handoff._account(item["name"], cfg, NOW)["evidence"]["queue"]
    cfg["repo_dir"] = None
    unconfigured = handoff._account(item["name"], cfg, NOW)["evidence"]["queue"]
    assert set(configured) == set(unconfigured) == {
        "availability", "freshness", "local_modified_at", "observed_at", "remote_current_verified"}
    assert unconfigured == {"availability": "not_configured", "freshness": "not_applicable",
                            "local_modified_at": None, "observed_at": jst.iso(NOW),
                            "remote_current_verified": False}
