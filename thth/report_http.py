"""Private, Unix-socket or explicitly selected loopback read-only report transport; not a public HTTP server.

One OS-isolated user per process. Credentials are administrator-issued service
credentials, not human identity or publication approval. Never log requests.
"""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import stat
import socket
import socketserver
import threading
import sys

from .report_service import ReportContext, ReportServiceError, execute_report
from .report_isolation import IsolationError, validate_environment, read_registry_ledger, registry_project

MAX_BODY = 16384
MAX_CONFIG = 131072
READ_TIMEOUT = 5
REQUEST_DEADLINE = 10


class ConfigurationError(ValueError):
    pass


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate_key")
        obj[key] = value
    return obj


def _constant(value):
    raise ValueError("invalid_number")


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)


def _json_content_type(value):
    parts = value.split(";")
    if parts[0].strip().lower() != "application/json":
        return False
    if len(parts) == 1:
        return True
    if len(parts) != 2:
        return False
    key, separator, charset = parts[1].partition("=")
    return (bool(separator) and key.strip().lower() == "charset"
            and charset.strip().lower() in {"utf-8", '"utf-8"'})


def _expiry(value):
    if not isinstance(value, str):
        raise ValueError("invalid_expiry")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("invalid_expiry")
    return parsed


def load_credentials(path: Path):
    """Reload every request. Only owner-private regular files, never symlinks/FIFO.

    Atomic replacement in a trusted owner-private directory supports revocation.
    Filesystem ownership of all parent directories remains deployment policy.
    """
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > MAX_CONFIG):
                raise ValueError("invalid_file")
            raw = stream.read(MAX_CONFIG + 1)
        if len(raw) > MAX_CONFIG:
            raise ValueError("invalid_file")
        config = _json(raw)
        if (type(config) is not dict or set(config) != {"schema_version", "root", "credentials"}
                or type(config["schema_version"]) is not int or config["schema_version"] != 1
                or type(config["credentials"]) is not list or not 1 <= len(config["credentials"]) <= 100):
            raise ValueError("invalid_config")
        root = config["root"]
        if not isinstance(root, str) or not os.path.isabs(root):
            raise ValueError("invalid_root")
        credentials = []
        seen = set()
        for item in config["credentials"]:
            if type(item) is not dict or set(item) - {"sha256", "expires_at", "revoked", "accounts", "scope", "writes", "actor"} or not {"sha256", "expires_at", "revoked", "accounts"} <= set(item):
                raise ValueError("invalid_credential")
            digest = item["sha256"]
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or digest in seen or type(item["revoked"]) is not bool
                    or type(item["accounts"]) is not dict or (not item["accounts"] and item.get("scope") != "admin")):
                raise ValueError("invalid_credential")
            seen.add(digest)
            scope = item.get("scope", "user")
            allowed = item["accounts"]
            if scope == "admin":
                allowed = {}
                for ledger in sorted((Path(root) / "accounts").glob("*.json")):
                    if ledger.is_symlink() or not ledger.is_file():
                        raise ValueError("invalid_registry")
                    value = read_registry_ledger(ledger)
                    allowed[ledger.stem] = registry_project(value)
            context = ReportContext(allowed, scope=scope, writes=item.get('writes', False), actor=item.get('actor'),
                                    credential_digest=digest, credentials_path=str(Path(path).absolute()))
            from . import leave, leave_gate
            from dataclasses import replace
            if scope == 'admin':
                context=replace(context,allowed_accounts={**context.allowed_accounts,**{
                    name:context.allowed_accounts.get(name) for name in leave.names()}})
            else:
                excluded={name:project for name,project in context.allowed_accounts.items() if leave_gate.stopped(name)}
                context=replace(context,allowed_accounts={name:project for name,project in context.allowed_accounts.items()
                                                        if name not in excluded},excluded_accounts=excluded)
            credentials.append((digest, _expiry(item["expires_at"]), item["revoked"], context))
        return root, credentials
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ConfigurationError("configuration_unavailable") from None


class PrivateReportServer(HTTPServer):
    """Serialized requests; never switches process environment per request."""
    allow_reuse_address = True

    def __init__(self, credentials_path, port=None, *, socket_path=None, executor=execute_report):
        if (port is None) == (socket_path is None):
            raise ValueError("transport_required")
        self.unix_path = None
        self.socket_identity = None
        self.credentials_path = Path(credentials_path).absolute()
        root, credentials = load_credentials(self.credentials_path)
        for _, _, _, context in credentials:
            validate_environment(root, context.allowed_accounts, allow_unreadable=context.scope == "admin", allow_empty=True)  # Fail before binding.
        self.executor = executor
        if socket_path is not None:
            path = Path(socket_path).absolute()
            parent = path.parent
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise ValueError("private_socket_directory_required")
            if path.exists() or path.is_symlink():
                raise ValueError("socket_path_in_use")
            self.unix_path = path
            self.address_family = socket.AF_UNIX
            address = str(path)
        else:
            if type(port) is not int or not 0 <= port <= 65535:
                raise ValueError("invalid_port")
            address = ("127.0.0.1", port)
        super().__init__(address, ReportHandler)

    def server_bind(self):
        if self.unix_path is None:
            return super().server_bind()
        socketserver.TCPServer.server_bind(self)
        info = self.unix_path.lstat()
        self.socket_identity = (info.st_dev, info.st_ino)
        self.unix_path.chmod(0o600)  # Before listen; parent is owner-private.
        self.server_name, self.server_port = "localhost", None

    def server_close(self):
        super().server_close()
        if self.unix_path is not None and self.socket_identity is not None:
            try:
                info = self.unix_path.lstat()
                if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.socket_identity:
                    self.unix_path.unlink()
            except FileNotFoundError:
                pass

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(READ_TIMEOUT)
        return connection, address

    def handle_error(self, request, client_address):
        # BaseServer would print an exception traceback with implementation data.
        pass


class ReportHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "THTH-Private"
    sys_version = ""

    def setup(self):
        super().setup()
        # A recv timeout resets after every byte; the watchdog does not.
        self._deadline = threading.Timer(REQUEST_DEADLINE, self._expire_read)
        self._deadline.daemon = True
        self._deadline.start()

    def _expire_read(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _read_complete(self):
        self._deadline.cancel()

    def finish(self):
        self._read_complete()
        super().finish()

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._reply(code, {"error": "invalid_http_request"})

    def _reply(self, status, payload):
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, OverflowError, RecursionError):
            status, body = 503, b'{"error":"report_unavailable"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="thth-private-report"')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def _valid_host(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        allowed = {"localhost"} if self.server.unix_path is not None else {f"127.0.0.1:{port}", f"localhost:{port}"}
        if len(hosts) != 1 or hosts[0] not in allowed:
            self._reply(400, {"error": "invalid_host"})
            return False
        return True

    def _credentials(self):
        try:
            return load_credentials(self.server.credentials_path)
        except ConfigurationError:
            self._reply(503, {"error": "configuration_unavailable"})
            return None

    def do_GET(self):
        self._read_complete()
        if not self._valid_host():
            return
        if self.path != "/health":
            self._reply(404, {"error": "not_found"})
        elif self._credentials() is not None:
            self._reply(200, {"status": "serving", "scope": "http_process_only"})

    def do_POST(self):
        if not self._valid_host():
            return
        if self.path not in ("/report", "/write"):
            return self._reply(404, {"error": "not_found"})
        loaded = self._credentials()
        if loaded is None:
            return
        root, credentials = loaded
        auth = self.headers.get_all("Authorization", [])
        match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43,128})", auth[0]) if len(auth) == 1 else None
        digest = hashlib.sha256(match[1].encode("ascii")).hexdigest() if match else ""
        now = datetime.now(timezone.utc)
        context = None
        for expected, expires, revoked, allowed in credentials:
            if hmac.compare_digest(digest, expected) and not revoked and now < expires:
                context = allowed
        if context is None:
            return self._reply(401, {"error": "unauthorized"})
        # Browser requests never need CORS here. Reject Origin, including null.
        if self.headers.get_all("Origin"):
            return self._reply(403, {"error": "origin_not_allowed"})
        if self.headers.get_all("Content-Encoding"):
            return self._reply(415, {"error": "json_required"})
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or not _json_content_type(types[0]):
            return self._reply(415, {"error": "json_required"})
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,8}", lengths[0]):
            return self._reply(400, {"error": "invalid_length"})
        length = int(lengths[0])
        if length > MAX_BODY:
            return self._reply(413, {"error": "request_too_large"})
        try:
            raw = self.rfile.read(length)
            self._read_complete()
            if len(raw) != length:
                raise ValueError("short_body")
            request = _json(raw)
            if type(request) is not dict:
                raise ValueError("invalid_request")
            # Bound calculations before ledger access as well as payload size.
            for key, limit in (("window_days", 3650), ("min_n", 100000)):
                if key in request and (type(request[key]) is not int or not 1 <= request[key] <= limit):
                    raise ValueError("invalid_options")
        except (ValueError, OSError, RecursionError, OverflowError):
            return self._reply(400, {"error": "invalid_request"})
        try:
            validate_environment(root, context.allowed_accounts, allow_unreadable=context.scope == "admin", allow_empty=True)
        except IsolationError:
            return self._reply(503, {"error": "environment_unavailable"})
        try:
            if self.path == '/write':
                from .server_writes import execute
                payload = execute(context, request, via='http')
            else:
                payload = self.server.executor(context, request)
        except ReportServiceError as error:
            # Fixed allowlist prevents future exception text exposing core details.
            reason = str(error)
            if reason == 'account_leaving':
                return self._reply(503, {'error':reason, 'cannot_say':[reason]})
            public = {"invalid_request", "unsupported_operation", "invalid_scope", "invalid_options", "scope_unavailable", "writes_not_allowed", "invalid_draft", "draft_changed", "draft_not_editable", "managed_repo_required", "production_disabled"}
            fallback = "report_unavailable"
            detail = {}
            from .server_reads import OPERATIONS as READ_OPERATIONS
            if self.path != '/write' and request.get('operation') in READ_OPERATIONS:
                # 読む口（設計 3.14.0 §3.2）: 静的な符丁と、次に採れる時刻・媒体の短い理由。
                from .server_writes import SAFE_ERRORS
                fallback = reason if reason in SAFE_ERRORS else 'read_unavailable'
                if isinstance(getattr(error, "reason", None), str):
                    detail = {"reason": error.reason}
                if isinstance(getattr(error, "next_at", None), str):
                    detail["next_at"] = error.next_at
            if self.path == '/write':
                from .server_writes import SAFE_ERRORS, DRAFT_REASONS
                fallback = reason if reason in SAFE_ERRORS else 'write_unavailable'
                # The refusal carries its static reason; never the lint text or a path.
                if getattr(error, "reason", None) in DRAFT_REASONS:
                    detail = {"reason": error.reason}
                # 安全装置の断り（設計 3.12.0 §3.3）: 止まった理由と次に出せる時刻。
                from .server_writes import GUARD_DETAILS
                if getattr(error, "reason", None) in GUARD_DETAILS:
                    detail = {"reason": error.reason}
                if isinstance(getattr(error, "next_at", None), str):
                    detail["next_at"] = error.next_at
            return self._reply(400 if reason in public else 503,
                               {"error": reason if reason in public else fallback, **detail})
        except Exception:
            return self._reply(503, {"error": "report_unavailable"})
        self._reply(200, payload)


def cmd_serve_reports(args):
    try:
        if args.tcp_port is not None and not 1 <= args.tcp_port <= 65535:
            raise ValueError("invalid_port")
        with PrivateReportServer(args.credentials, args.tcp_port, socket_path=args.socket) as server:
            transport = "Unix socket" if args.socket is not None else "explicit loopback TCP"
            print(f"Private read-only reports: {transport}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        reasons = {"root_mismatch", "account_scope_mismatch", "private_root_required",
                   "account_registry_mismatch", "invalid_report_scope", "resource_outside_root",
                   "unsafe_report_tree", "report_tree_too_large", "unreadable_report_tree",
                   "invalid_report_environment", "configuration_unavailable", "invalid_port",
                   "private_socket_directory_required", "socket_path_in_use", "transport_required"}
        reason = str(error) if isinstance(error, (IsolationError, ConfigurationError, ValueError)) and str(error) in reasons else "server_unavailable"
        if isinstance(error, OSError) and error.errno == errno.EADDRINUSE:
            reason = "address_in_use"
        print(f"private_report_server_unavailable: {reason}", file=sys.stderr)
        return 2
    return 0
