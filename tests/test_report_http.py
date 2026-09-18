"""Real loopback HTTP requests with temporary, dummy administrator credentials."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import threading

import pytest

from thth import report_http, accounts
from thth.report_service import execute_report

TOKEN = "a" * 43


@pytest.fixture(autouse=True)
def dedicated_root(tmp_path, monkeypatch):
    root = tmp_path / "tenant"
    root.mkdir(mode=0o700)
    (root / "accounts").mkdir()
    for name, project in [("allowed", "project-a"), ("other", "project-b")]:
        cfg = {key: None for key in accounts.REQUIRED_FIELDS}
        cfg.update(account=name, project=project, repo_dir=str(root / "repos" / name),
                   queue_dir="docs/sns/queue", replies_dir="data/sns/replies",
                   env=str(root / "secrets" / "env"), token=str(root / "secrets" / "token"))
        (root / "accounts" / (name + ".json")).write_text(json.dumps(cfg))
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.delenv("THTH_ACCOUNTS_DIR", raising=False)



def config(path, **changes):
    item = {"sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "revoked": False, "accounts": {"allowed": "project-a"}}
    item.update(changes)
    value = {"schema_version": 1, "root": str(path.parent / "tenant"), "credentials": [item]}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return value


@contextmanager
def running(tmp_path, executor=None):
    path = tmp_path / "credentials.json"
    config(path)
    calls = []
    def record(context, request):
        calls.append((context, request))
        return {"ok": True}
    with report_http.PrivateReportServer(path, 0, executor=executor or record) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port, path, calls
        finally:
            server.shutdown()
            thread.join(5)


def request(port, body=None, headers=None, method="POST", target="/report"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    if body is None:
        body = json.dumps({"operation": "analytics_report", "account": "allowed"})
    fields = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
    fields.update(headers or {})
    conn.request(method, target, body, fields)
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), json.loads(response.read())
    conn.close()
    return result


def test_success_health_no_store_no_cors_and_no_logs(tmp_path, capsys):
    with running(tmp_path) as (port, _, calls):
        status, headers, data = request(port)
        assert status == 200 and data == {"ok": True}
        assert dict(calls[0][0].allowed_accounts) == {"allowed": "project-a"}
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert not any(key.lower().startswith("access-control") for key in headers)
        assert request(port, method="GET", target="/health")[2] == {"status": "serving", "scope": "http_process_only"}
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("auth", ["", "Bearer wrong", "Bearer " + "b" * 43, "Basic abc"])
def test_unauthorized_never_reads_core(tmp_path, auth):
    with running(tmp_path) as (port, _, calls):
        assert request(port, headers={"Authorization": auth})[0] == 401
        assert not calls


def test_expiry_revocation_scope_reloaded_next_request(tmp_path, monkeypatch):
    from thth import analytics_report
    calls = []
    monkeypatch.setattr(analytics_report, "answer", lambda name, **kw: calls.append(name) or {"name": name})
    with running(tmp_path, execute_report) as (port, path, _):
        assert request(port)[0] == 200
        assert calls == ["allowed"]
        config(path, revoked=True)
        assert request(port)[0] == 401
        config(path, expires_at="2000-01-01T00:00:00+00:00")
        assert request(port)[0] == 401
        config(path, accounts={"other": "project-b"})
        assert request(port)[2] == {"error": "scope_unavailable"}
        assert request(port, json.dumps({"operation": "analytics_report", "project": "project-a"}))[2] == {"error": "scope_unavailable"}
        assert calls == ["allowed"]


@pytest.mark.parametrize("body,headers,status", [
    ('{"operation":"analytics_report","operation":"operations_handoff"}', {}, 400),
    ('{"window_days":NaN}', {}, 400),
    ('[' * 2000, {}, 400),
    ('{}', {"Content-Type": "text/plain"}, 415),
    ('{}', {"Content-Encoding": "gzip"}, 415),
    ('{}', {"Origin": "https://evil.invalid"}, 403),
    ('{}', {"Host": "evil.invalid"}, 400),
    ('{}', {"Content-Length": "99999"}, 413),
    ('{}', {"Transfer-Encoding": "chunked"}, 400),
    ('{"window_days":3651}', {}, 400),
    ('{"min_n":true}', {}, 400),
])
def test_transport_rejections_before_executor(tmp_path, body, headers, status):
    with running(tmp_path) as (port, _, calls):
        assert request(port, body, headers)[0] == status
        assert not calls


@pytest.mark.parametrize("body", [
    {"operation": "approve", "account": "allowed"},
    {"operation": "analytics_report", "account": "outside"},
    {"operation": "analytics_report", "project": "outside"},
    {"operation": "analytics_report", "account": "allowed", "tenant": "outside"},
    {"operation": "analytics_report", "account": "allowed", "path": "/secret"},
])
def test_scope_operations_before_core(tmp_path, monkeypatch, body):
    from thth import analytics_report
    calls = []
    monkeypatch.setattr(analytics_report, "answer", lambda *a, **kw: calls.append(a))
    with running(tmp_path, execute_report) as (port, _, _):
        assert request(port, json.dumps(body))[0] == 400
        assert not calls


def test_bounded_unknown_exception(tmp_path, capsys):
    def failing(*args):
        raise RuntimeError("secret " + TOKEN)
    with running(tmp_path, failing) as (port, _, _):
        assert request(port)[2] == {"error": "report_unavailable"}
    assert capsys.readouterr() == ("", "")


def test_config_fail_closed_start_and_reload(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("bad secret")
    path.chmod(0o600)
    with pytest.raises(report_http.ConfigurationError, match="^configuration_unavailable$"):
        report_http.PrivateReportServer(path, 0)
    with running(tmp_path) as (port, path, calls):
        path.chmod(0o644)
        assert request(port)[0] == 503
        assert not calls
        path.unlink()
        assert request(port)[0] == 503


@pytest.mark.parametrize("changes", [{"expires_at": "2026-01-01"}, {"revoked": 1},
                                      {"accounts": {}}, {"sha256": "plain-text"}])
def test_bad_credential_rejected(tmp_path, changes):
    path = tmp_path / "credentials.json"
    config(path, **changes)
    with pytest.raises(report_http.ConfigurationError):
        report_http.load_credentials(path)


def test_symlink_fifo_rejected_without_blocking(tmp_path):
    path = tmp_path / "credentials.json"
    config(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for item in [link, fifo]:
        with pytest.raises(report_http.ConfigurationError):
            report_http.load_credentials(item)


@pytest.mark.parametrize("header", ["Authorization: Bearer " + TOKEN, "Content-Length: 2", "Host: localhost"])
def test_duplicate_security_headers_rejected(tmp_path, header):
    with running(tmp_path) as (port, _, calls):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
            conn.sendall((f"POST /report HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                          f"Authorization: Bearer {TOKEN}\r\nContent-Type: application/json\r\n"
                          f"Content-Length: 2\r\n{header}\r\n\r\n{{}}").encode())
            assert b" 200 " not in conn.recv(2048).split(b"\r\n")[0]
        assert not calls


def test_no_unauthenticated_preflight_or_account_reads(tmp_path, monkeypatch):
    with running(tmp_path) as (port, path, calls):
        def forbidden(*args, **kwargs):
            pytest.fail("unauthenticated request read account configuration")
        monkeypatch.setattr(accounts, "load_account", forbidden)
        assert request(port, headers={"Authorization": ""})[0] == 401
        config(path, revoked=True)
        assert request(port)[0] == 401
        config(path, expires_at="2000-01-01T00:00:00Z")
        assert request(port)[0] == 401
        assert not calls


def test_environment_rechecked_without_root_switching(tmp_path, monkeypatch):
    with running(tmp_path) as (port, path, calls):
        root_before = os.environ["THTH_ROOT"]
        (tmp_path / "tenant").chmod(0o755)
        assert request(port)[2] == {"error": "environment_unavailable"}
        assert not calls
        assert os.environ["THTH_ROOT"] == root_before
        (tmp_path / "tenant").chmod(0o700)
        value = json.loads(path.read_text())
        value["root"] = str(tmp_path)
        path.write_text(json.dumps(value))
        assert request(port)[2] == {"error": "environment_unavailable"}
        assert not calls
        assert os.environ["THTH_ROOT"] == root_before


@pytest.mark.parametrize("operation", ["analytics_report", "operations_handoff"])
def test_real_core_http_snapshot_does_not_write(tmp_path, operation):
    with running(tmp_path, execute_report) as (port, _, _):
        root = tmp_path / "tenant"
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        status, _, payload = request(port, json.dumps({"operation": operation, "account": "allowed"}))
        assert status == 200
        assert payload["report_type"] == "scoped_report_batch"
        assert set(payload["reports"]) == {"allowed"}
        assert payload["operation"] == operation
        assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("phase", ["request_line", "headers", "body"])
def test_total_read_deadline_releases_next_request(tmp_path, monkeypatch, phase):
    import time
    monkeypatch.setattr(report_http, "REQUEST_DEADLINE", 0.35)
    with running(tmp_path) as (port, _, calls):
        with socket.create_connection(("127.0.0.1", port), timeout=2) as slow:
            if phase == "request_line":
                prefix = b"P"
            elif phase == "headers":
                prefix = b"POST /report HTTP/1.1\r\nX-Slow: "
            else:
                prefix = (f"POST /report HTTP/1.1\r\nHost: localhost:{port}\r\n"
                          f"Authorization: Bearer {TOKEN}\r\nContent-Type: application/json\r\n"
                          "Content-Length: 1000\r\n\r\n{").encode()
            slow.sendall(prefix)
            stopped = threading.Event()
            def drip():
                while not stopped.wait(0.03):
                    try:
                        slow.sendall(b" ")
                    except OSError:
                        return
            thread = threading.Thread(target=drip, daemon=True)
            thread.start()
            started = time.monotonic()
            try:
                assert request(port, method="GET", target="/health")[0] == 200
                assert time.monotonic() - started < 1.2
            finally:
                stopped.set()
                thread.join(2)
            assert not calls
        # A completed request's watchdog must not affect a later request.
        time.sleep(0.4)
        assert request(port)[0] == 200


def test_unix_permissions_health_cleanup_and_existing_path(tmp_path, short_socket_dir):
    path = tmp_path / "credentials.json"
    config(path)
    private = short_socket_dir
    endpoint = private / "report.sock"
    with report_http.PrivateReportServer(path, socket_path=endpoint) as server:
        assert endpoint.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match="socket_path_in_use"):
            report_http.PrivateReportServer(path, socket_path=endpoint)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(endpoint))
            client.sendall(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            assert b" 200 " in client.recv(4096)
        thread.join(3)
    assert not endpoint.exists()
    endpoint.write_text("do not overwrite")
    with pytest.raises(ValueError, match="socket_path_in_use"):
        report_http.PrivateReportServer(path, socket_path=endpoint)
    assert endpoint.read_text() == "do not overwrite"
    endpoint.unlink()
    private.chmod(0o755)
    with pytest.raises(ValueError, match="private_socket_directory_required"):
        report_http.PrivateReportServer(path, socket_path=endpoint)


def test_unix_cleanup_does_not_remove_replacement(tmp_path, short_socket_dir):
    path = tmp_path / "credentials.json"
    config(path)
    private = short_socket_dir
    endpoint = private / "report.sock"
    with report_http.PrivateReportServer(path, socket_path=endpoint):
        endpoint.unlink()
        endpoint.write_text("replacement")
    assert endpoint.read_text() == "replacement"


def test_cli_requires_explicit_transport():
    from thth import cli
    with pytest.raises(SystemExit):
        cli.main(["serve-reports", "--credentials", "/unused"])


@pytest.fixture
def short_socket_dir():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="thth-sock-", dir="/tmp") as directory:
        yield Path(directory)


@pytest.mark.parametrize("content_type,status", [
    ("application/json;charset=utf-8", 200),
    ('Application/JSON ; CHARSET = "UTF-8"', 200),
    ("application/json; charset=shift_jis", 415),
    ("application/json; charset=utf-8; charset=utf-8", 415),
    ("application/json; other=utf-8", 415),
])
def test_content_type_media_and_charset(tmp_path, content_type, status):
    with running(tmp_path) as (port, _, _):
        assert request(port, headers={"Content-Type": content_type})[0] == status


@pytest.mark.parametrize("reason", ["root_mismatch", "account_scope_mismatch",
                                    "report_tree_too_large", "unsafe_report_tree"])
def test_startup_reason_bounded_stderr(tmp_path, monkeypatch, capsys, reason):
    from types import SimpleNamespace
    def bad(*args, **kwargs):
        raise report_http.IsolationError(reason)
    monkeypatch.setattr(report_http, "PrivateReportServer", bad)
    assert report_http.cmd_serve_reports(SimpleNamespace(credentials="/SECRET", tcp_port=8765, socket=None)) == 2
    assert capsys.readouterr() == ("", f"private_report_server_unavailable: {reason}\n")


def test_startup_address_in_use_reason(tmp_path, capsys):
    from types import SimpleNamespace
    path = tmp_path / "credentials.json"
    config(path)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        args = SimpleNamespace(credentials=path, tcp_port=occupied.getsockname()[1], socket=None)
        assert report_http.cmd_serve_reports(args) == 2
    assert capsys.readouterr() == ("", "private_report_server_unavailable: address_in_use\n")
