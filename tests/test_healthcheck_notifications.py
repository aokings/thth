"""timer run の Healthchecks 通知。外部送信はすべて fake transport。"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
import urllib.error

import pytest

from thth import accounts, cli, core, healthcheck, inflight, report, runs


@pytest.fixture(autouse=True)
def no_real_healthcheck(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)


class Response:
    def __init__(self, body=b"OK"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


def configured_account(tmp_path, isolated_account_factory, *, url=True, token=True):
    env_path = tmp_path / "account.env"
    if url:
        env_path.write_text(
            "HEALTHCHECK_URL=https://hc-ping.com/secret-check-id\n", encoding="utf-8")
    token_path = tmp_path / "account.token"
    if token:
        token_path.write_text("{}\n", encoding="utf-8")
    account = isolated_account_factory(
        env=str(env_path), token=str(token_path), production=True)
    return account, env_path


def prepare_run(monkeypatch):
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.collect_mod, "run_collect", lambda *_a, **_k: 0)


def test_cmd_run_first_fail_repeat_inflight_then_recovery_success(
        tmp_path, isolated_account_factory, monkeypatch):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    prepare_run(monkeypatch)
    state_dir = accounts.state_dir_for(account["name"])
    post_file = os.path.join(account["queue_dir"], "柚子.md")
    requests = []

    def fake_open(req, *, timeout):
        requests.append((req.full_url, json.loads(req.data), timeout))
        return Response()

    monkeypatch.setattr(healthcheck.httpsafe, "urlopen", fake_open)
    call = {"n": 0}

    def fake_throw(*_a, **_k):
        call["n"] += 1
        if call["n"] == 1:
            inflight.write(state_dir, file=post_file,
                           started="2026-09-16T20:08:13+09:00", container_id="123")
            return core.ThrowResult(1, "production", "inflight", "停止", file=post_file,
                                    error="公開失敗: HTTP 500 remote supplied detail")
        if call["n"] == 2:
            return core.ThrowResult(1, "rehearsal", "inflight", "停止", file=post_file)
        inflight.clear(state_dir)
        return core.ThrowResult(0, "production", "none", "出すものなし")

    monkeypatch.setattr(cli.core, "throw_once", fake_throw)
    args = SimpleNamespace(account=account["name"])
    assert [cli.cmd_run(args), cli.cmd_run(args), cli.cmd_run(args)] == [1, 1, 0]

    assert requests[0][0].endswith("/secret-check-id/fail")
    assert requests[1][0].endswith("/secret-check-id/fail")
    assert requests[2][0].endswith("/secret-check-id")
    assert requests[0][1]["reason"].endswith("[publish_http_500]")
    assert requests[1][1]["reason"].endswith("[publish_http_500]")
    assert set(requests[0][1]) == {
        "account", "state", "since", "file", "reason", "next_action"}
    assert requests[0][1]["file"] == "柚子.md"
    assert requests[2][1]["state"] == "success"
    assert healthcheck.read_status(state_dir)["reason_code"] == "healthy"


def test_transport_failure_is_loud_and_does_not_retry_publication(
        tmp_path, isolated_account_factory, monkeypatch, capsys):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    prepare_run(monkeypatch)
    calls = []

    def fake_throw(*_a, **_k):
        calls.append("publish")
        return core.ThrowResult(1, "production", "post", "失敗", error="HTTP 400")

    monkeypatch.setattr(cli.core, "throw_once", fake_throw)
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("secret response")))
    assert cli.cmd_run(SimpleNamespace(account=account["name"])) == 1
    assert calls == ["publish"]
    assert "死活通知に失敗しました: transport_error" in capsys.readouterr().err


def test_rc0_with_remaining_inflight_sends_fail_not_recovery(
        tmp_path, isolated_account_factory, monkeypatch):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    prepare_run(monkeypatch)
    state_dir = accounts.state_dir_for(account["name"])
    requests = []

    def stopped(*_a, **_k):
        inflight.write(state_dir, file="bundle.md",
                       started="2026-09-16T20:08:13+09:00")
        return core.ThrowResult(0, "production", "stopped", "連投停止")

    monkeypatch.setattr(cli.core, "throw_once", stopped)
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda req, **_k: requests.append((req.full_url, json.loads(req.data))) or Response())
    assert cli.cmd_run(SimpleNamespace(account=account["name"])) == 0
    assert requests[0][0].endswith("/fail")
    assert requests[0][1]["state"] == "fail"


def test_blocking_probe_exception_preserves_rc_and_does_not_send_success(
        tmp_path, isolated_account_factory, monkeypatch, capsys):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    prepare_run(monkeypatch)
    requests = []
    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: core.ThrowResult(0, "production", "none", "なし"))
    monkeypatch.setattr(
        healthcheck, "has_blocking_state",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("raw secret")))
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda req, **_k: requests.append((req.full_url, json.loads(req.data))) or Response())
    assert cli.cmd_run(SimpleNamespace(account=account["name"])) == 0
    assert requests[0][0].endswith("/fail")
    assert requests[0][1]["state"] == "fail"
    assert "raw secret" not in capsys.readouterr().err


def test_process_exception_survives_notification_and_diagnostic_failure(
        tmp_path, isolated_account_factory, monkeypatch):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    prepare_run(monkeypatch)

    class Original(RuntimeError):
        pass

    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: (_ for _ in ()).throw(Original("original secret")))
    monkeypatch.setattr(
        healthcheck, "diagnostic",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("notification secret")))
    with pytest.raises(Original, match="original secret"):
        cli.cmd_run(SimpleNamespace(account=account["name"]))


def test_process_exception_sends_fail_then_reraises_original(
        tmp_path, isolated_account_factory, monkeypatch):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    prepare_run(monkeypatch)
    requests = []
    original = RuntimeError("raw exception detail")
    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: (_ for _ in ()).throw(original))
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda req, **_k: requests.append((req.full_url, json.loads(req.data))) or Response())
    with pytest.raises(RuntimeError) as caught:
        cli.cmd_run(SimpleNamespace(account=account["name"]))
    assert caught.value is original
    assert requests[0][0].endswith("/fail")
    assert requests[0][1]["state"] == "fail"
    assert requests[0][1]["reason"].endswith("[exception_runtime_error]")
    assert "raw exception detail" not in json.dumps(requests[0][1], ensure_ascii=False)


def test_missing_token_sends_fail_without_calling_core(
        tmp_path, isolated_account_factory, monkeypatch):
    account, _ = configured_account(
        tmp_path, isolated_account_factory, token=False)
    prepare_run(monkeypatch)
    requests = []
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda req, **_k: requests.append(json.loads(req.data)) or Response())
    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: pytest.fail("token 無しで公開経路を呼んだ"))
    assert cli.cmd_run(SimpleNamespace(account=account["name"])) == 2
    assert requests[0]["state"] == "fail"
    assert requests[0]["reason"].endswith("[missing_token]")


def test_unconfigured_records_truth_and_never_opens_network(
        tmp_path, isolated_account_factory, monkeypatch, capsys):
    account, _ = configured_account(
        tmp_path, isolated_account_factory, url=False)
    prepare_run(monkeypatch)
    monkeypatch.setattr(
        cli.core, "throw_once",
        lambda *_a, **_k: core.ThrowResult(0, "production", "none", "なし"))
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda *_a, **_k: pytest.fail("未設定なのに network を開いた"))
    assert cli.cmd_run(SimpleNamespace(account=account["name"])) == 0
    status = healthcheck.read_status(accounts.state_dir_for(account["name"]))
    assert status["delivery"] == "not_configured"
    assert status["endpoint_configured"] is False
    assert "通知は送っていません" in capsys.readouterr().err


def test_invalid_account_name_does_not_create_notification_state(
        tmp_path, monkeypatch):
    monkeypatch.setenv("THTH_ROOT", str(tmp_path / "root"))
    prepare_run(monkeypatch)
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/secret")
    monkeypatch.setattr(
        healthcheck.httpsafe, "urlopen",
        lambda *_a, **_k: pytest.fail("不正 account で通知した"))
    assert cli.cmd_run(SimpleNamespace(account="../outside")) == 2
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("body", [b"OK (not found)", b"OK (rate limited)", b"ok"])
def test_http_200_body_must_be_exact_ok(body, monkeypatch):
    diag = healthcheck.Diagnostic(
        "a", "success", None, None, "正常 [healthy]", "不要 [none]",
        "healthy", "none")
    monkeypatch.setattr(healthcheck.httpsafe, "urlopen", lambda *_a, **_k: Response(body))
    assert healthcheck._post("https://hc-ping.com/secret", diag) == (
        False, "unexpected_response")


def test_endpoint_is_registered_but_never_written_or_sent_in_body(
        tmp_path, isolated_account_factory, monkeypatch):
    account, _ = configured_account(tmp_path, isolated_account_factory)
    cfg = accounts.load_account(account["name"])
    state_dir = accounts.state_dir_for(account["name"])
    seen = {}

    def fake_open(req, **_k):
        seen["body"] = req.data.decode()
        return Response()

    monkeypatch.setattr(healthcheck.httpsafe, "urlopen", fake_open)
    diag = healthcheck.diagnostic(account["name"], "success", state_dir=state_dir)
    healthcheck.notify(account["name"], cfg, diag, state_dir=state_dir)
    state_text = open(healthcheck.status_path(state_dir), encoding="utf-8").read()
    assert "secret-check-id" not in state_text
    assert "secret-check-id" not in seen["body"]


def test_definite_failure_uses_result_file_without_inflight(tmp_path):
    state_dir = str(tmp_path / "state")
    result = core.ThrowResult(
        1, "production", "post", "失敗", file="/private/raw/原稿.md", error="HTTP 400")
    diag = healthcheck.diagnostic("a", "fail", state_dir=state_dir, result=result)
    assert diag.file == "原稿.md"
    assert diag.reason_code == "publish_http_400"


def test_malformed_status_types_are_treated_as_unknown(tmp_path):
    state_dir = str(tmp_path / "state")
    os.makedirs(state_dir)
    with open(healthcheck.status_path(state_dir), "w", encoding="utf-8") as f:
        json.dump({"endpoint_configured": [], "delivery": [], "last_state": {},
                   "reason_code": [], "category": {}}, f)
    status = healthcheck.read_status(state_dir)
    assert status["delivery"] is None
    assert status["last_state"] is None
    assert status["reason_code"] is None


def test_status_rejects_arbitrary_reason_that_looks_like_a_code(tmp_path):
    state_dir = str(tmp_path / "state")
    os.makedirs(state_dir)
    with open(healthcheck.status_path(state_dir), "w", encoding="utf-8") as f:
        json.dump({"endpoint_configured": True, "delivery": "failed",
                   "last_state": "fail", "reason_code": "exception_remote_secret",
                   "last_attempt_at": "2026-09-16T20:08:13+09:00"}, f)
    assert healthcheck.read_status(state_dir)["reason_code"] is None


def test_malformed_endpoint_records_failed_instead_of_leaving_old_success(
        tmp_path, isolated_account_factory, monkeypatch):
    account, env_path = configured_account(tmp_path, isolated_account_factory)
    cfg = accounts.load_account(account["name"])
    state_dir = accounts.state_dir_for(account["name"])
    monkeypatch.setattr(healthcheck.httpsafe, "urlopen", lambda *_a, **_k: Response())
    ok = healthcheck.diagnostic(account["name"], "success", state_dir=state_dir)
    assert healthcheck.notify(account["name"], cfg, ok, state_dir=state_dir).delivery == "delivered"
    env_path.write_text("HEALTHCHECK_URL=https://[\n", encoding="utf-8")
    failed = healthcheck.diagnostic(
        account["name"], "fail", state_dir=state_dir, reason="publish_failed")
    attempt = healthcheck.notify(account["name"], cfg, failed, state_dir=state_dir)
    assert attempt.delivery == "failed" and attempt.category == "invalid_endpoint"
    status = healthcheck.read_status(state_dir)
    assert status["delivery"] == "failed"
    assert status["category"] == "invalid_endpoint"


def test_legacy_cause_matches_same_file_only_and_labels_uncertainty(
        isolated_account, thth_root):
    state_dir = accounts.state_dir_for(isolated_account["name"])
    inflight.write(state_dir, file="same.md", started="2026-09-16T20:08:13+09:00")
    base = {"account": isolated_account["name"], "run_id": "r", "mode": "production",
            "action": "post", "file": "same.md", "post_id": None, "collected": None,
            "refreshed": False, "quota": None, "status": "error"}
    runs.append_run(state_dir, {**base, "error": "公開失敗 HTTP 500 detail"}, "2026-09")
    runs.append_run(state_dir, {**base, "file": "other.md", "error": "HTTP 400"}, "2026-09")
    diag = healthcheck.diagnostic(
        isolated_account["name"], "fail", state_dir=state_dir,
        result=core.ThrowResult(1, "rehearsal", "inflight", "停止"))
    assert diag.reason_code == "legacy_candidate_publish_http_500"
    assert "対応は未確認" in diag.reason


def test_board_exposes_safe_cause_and_notification_truth(isolated_account):
    state_dir = accounts.state_dir_for(isolated_account["name"])
    inflight.write(state_dir, file="a.md", started="2026-09-16T20:08:13+09:00")
    diag = healthcheck.diagnostic(
        isolated_account["name"], "fail", state_dir=state_dir,
        reason="publish_result_unknown")
    healthcheck.notify(isolated_account["name"], None, diag, state_dir=state_dir)
    row = next(r for r in report.board_summary()["accounts"]
               if r["account"] == isolated_account["name"])
    assert row["notification_configured"] is False
    assert row["notification_delivery"] == "not_configured"
    assert row["inflight_started"] == "2026-09-16T20:08:13+09:00"
    assert row["inflight_reason_code"] == "publish_result_unknown"
    assert "媒体上の投稿有無" in row["inflight_next_action"]
