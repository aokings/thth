import dataclasses
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from thth import api_diagnostic as d, core, healthcheck, incident, jst, runs
from thth.adapters import base, threads
from tests.test_incident_notifications import setup, diag, row

DETAIL = {"http_status": 400, "code": 100, "error_subcode": 2207030,
          "is_transient": False, "type": "OAuthException"}


def error(status=400, body=None):
    body = body if body is not None else {"error": {**DETAIL, "message": "private body text"}}
    return urllib.error.HTTPError("https://example.org/private", status, "private reason", {},
                                  io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize("body", [[], None, {"error": None}, {"error": []}, {"error": "private"}])
def test_unusable_bodies(body):
    e = error(body=body if body is not None else {"error": None})
    assert d.read_http_error(e)[0] == {"http_status": 400}


def test_strict_field_allowlist():
    assert d.clean({"code": True, "error_subcode": "1", "is_transient": 1,
                    "type": "private", "message": "private", "http_status": 900}) == {}
    assert d.clean({"code": -1, "error_subcode": 2**80}) == {}
    assert d.clean(DETAIL) == DETAIL


@pytest.mark.parametrize("raw", [b"not json", b"\xff", b"x" * (d.MAX_ERROR_BYTES + 1)])
def test_read_bound_and_malformed(raw):
    class Response:
        code = 400
        def read(self, size):
            assert size == d.MAX_ERROR_BYTES + 1
            return raw[:size]
    assert d.read_http_error(Response())[0] == {"http_status": 400}


@pytest.mark.parametrize("stage,status,failure", [("create",400,"container"), ("publish",400,"publish_definite"), ("publish",500,"publish_ambiguous")])
def test_publish_classification_and_safe_detail(monkeypatch, stage, status, failure):
    adapter = threads.ThreadsAdapter(access_token="private token", user_id="1", wait_seconds=0)
    def post(path, params):
        if stage == "create" or path.endswith("threads_publish"):
            raise error(status)
        return {"id": "123"}
    monkeypatch.setattr(adapter, "_post", post)
    result = adapter.publish(base.Post("private text"), dry_run=False)
    assert result.failure == failure
    assert result.api_diagnostic == {**DETAIL, "http_status": status}
    assert "private" not in result.error
    assert "error_subcode=2207030" in result.error


def test_permission_classification_kept(monkeypatch):
    adapter = threads.ThreadsAdapter(access_token="private", user_id="1", wait_seconds=0)
    def post(*args):
        raise error(body={"error": {"code": 10, "message": "permission private"}})
    monkeypatch.setattr(adapter, "_post", post)
    result = adapter.publish(base.Post("private", location_id="123"), dry_run=False)
    assert result.failure == "permission"
    assert "private" not in result.error


def test_diagnostic_and_run_record(tmp_path):
    result = core.ThrowResult(1, "production", "post", "failed", api_diagnostic=DETAIL)
    diagnosis = healthcheck.diagnostic("demo", "fail", state_dir=str(tmp_path), result=result)
    assert diagnosis.api_diagnostic == DETAIL
    assert healthcheck.diagnostic("demo", "success", state_dir=str(tmp_path), result=result).api_diagnostic == {}
    core._append_run(str(tmp_path), "demo", "one", "production", "post", "one.md", None,
                     jst.now_jst(), status="error", error="failed", api_diagnostic={**DETAIL,"message":"private"})
    assert runs.read_runs(str(tmp_path))[-1]["api_diagnostic"] == DETAIL


def test_queue_mail_outbox_and_recovery(setup, monkeypatch):
    s = setup
    messages = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def send_message(self, message, **kwargs):
            messages.append(message.get_content())
            return {}
        def quit(self): pass
    monkeypatch.setattr(incident, "_send", ORIGINAL_SEND)
    monkeypatch.setattr(incident.smtplib, "SMTP_SSL", SMTP)
    diagnosis = dataclasses.replace(diag(s), api_diagnostic={**DETAIL,"message":"private"})
    result = SimpleNamespace(file=str(s.path))
    before = incident._fingerprint(s.path)
    incident.notify("demo",s.cfg,diagnosis,state_dir=s.state,result=result)
    assert row(s)["events"][-1]["api_diagnostic"] == DETAIL
    assert len(messages) == 2
    assert all("error_subcode=2207030" in text and "private" not in text for text in messages)
    assert "error_subcode=2207030" in s.path.read_text()
    assert incident._fingerprint(s.path) == before
    incident.notify("demo",s.cfg,diag(s,"success"),state_dir=s.state,result=None)
    assert "error_subcode=2207030" not in s.path.read_text()
    assert all("error_subcode" not in text for text in messages[2:])


ORIGINAL_SEND = incident._send


@pytest.mark.parametrize("status,action,kept", [(400,"post",False),(500,"inflight",True)])
def test_core_actual_adapter_to_runs(isolated_account_factory, monkeypatch, status, action, kept):
    from tests.test_fake_api import write_queue_file, NOW
    from thth import accounts, inflight
    account = isolated_account_factory(production=True)
    write_queue_file(account["queue_dir"], "a.md")
    adapter = threads.ThreadsAdapter(access_token="private", user_id="1", wait_seconds=0)
    def post(path, params):
        if path.endswith("threads_publish"):
            raise error(status)
        return {"id":"123"}
    monkeypatch.setattr(adapter,"_post",post)
    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: adapter, now=NOW)
    state = accounts.state_dir_for(account["name"])
    assert result.action == action
    assert (inflight.read(state) is not None) is kept
    assert result.api_diagnostic == {**DETAIL,"http_status":status}
    assert runs.read_runs(state)[-1]["api_diagnostic"] == result.api_diagnostic


from tests.test_thread_publish import thread_account


@pytest.mark.parametrize("status,action,kept", [(400,"failed",False),(500,"unresolved",True)])
def test_bundle_actual_adapter_to_notification(thread_account, monkeypatch, status, action, kept):
    from tests.test_thread_publish import NOW
    from thth import accounts, inflight, threadrun
    account = thread_account["account"]
    adapter = threads.ThreadsAdapter(access_token="private", user_id="1", wait_seconds=0)
    def post(path, params):
        if path.endswith("threads_publish"):
            raise error(status)
        return {"id":"123"}
    monkeypatch.setattr(adapter,"_post",post)
    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: adapter, now=NOW)
    state = accounts.state_dir_for(account["name"])
    assert result.action == action
    assert (inflight.read(state) is not None) is kept
    assert result.file == thread_account["path"]
    assert result.api_diagnostic == {**DETAIL,"http_status":status}
    assert runs.read_runs(state)[-1]["api_diagnostic"] == result.api_diagnostic
    diagnosis = healthcheck.diagnostic(account["name"], "fail", state_dir=state, result=result)
    assert diagnosis.api_diagnostic == result.api_diagnostic
    assert incident.source(accounts.load_account(account["name"]),state,result) == "docs/sns/queue/thread.md"
    assert threadrun.open_runs(account["name"])[0]["posts"][0]["api_diagnostic"] == result.api_diagnostic
