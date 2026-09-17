"""Durable account incident outbox. SMTP acceptance is not end-user delivery.

No post bodies, exception strings, credentials or recipient addresses enter the
outbox or repository. A corrupt outbox freezes notifications for manual repair.
SMTP is at-least-once best effort: a crash after DATA acceptance can duplicate a
message; a stable Message-ID makes that ambiguity visible to the mail system.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import subprocess
import uuid
from email.message import EmailMessage

from . import accounts, healthcheck, inflight, jst, lock, queuefile, redact, writeback

STATE_FILE = "incident-outbox.json"
CONFIG_FILE = "notifications.json"
FIELDS = {"thth_run_state", "thth_run_at", "thth_run_reason", "thth_run_action", "thth_incident_id", "thth_run_detail", "thth_run_next"}


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("unsafe_state_path")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _load(path, default):
    if Path(path).is_symlink():
        raise ValueError("unsafe_state_path")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def address(value):
    # A single mailbox, no display names / lists / SMTP header control characters.
    return (isinstance(value, str) and len(value) <= 254 and
            bool(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", value)))


def settings(cfg):
    global_cfg = _load(Path(accounts.thth_root()) / CONFIG_FILE, {})
    if not isinstance(global_cfg, dict):
        raise ValueError("invalid_notification_config")
    users = global_cfg.get("users", {})
    if not isinstance(users, dict):
        raise ValueError("invalid_notification_config")
    user = users.get((cfg or {}).get("account")) or (cfg or {}).get("notification_email")
    admin = global_cfg.get("admin_email")  # never read admin recipient from account env
    smtp = global_cfg.get("smtp", {})
    if not isinstance(smtp, dict):
        raise ValueError("invalid_smtp_config")
    env = {"THTH_SMTP_" + {"sender": "FROM"}.get(k, k.upper()): v for k, v in smtp.items()}
    env.update(os.environ)
    if cfg:
        try:
            env.update(accounts.load_env(cfg))
        except (OSError, UnicodeError):
            pass  # global administrator transport must survive broken account env
    result = {"user": user, "admin": admin, "host": env.get("THTH_SMTP_HOST"),
              "port": env.get("THTH_SMTP_PORT", "465"),
              "tls": env.get("THTH_SMTP_TLS", "ssl"),
              "sender": env.get("THTH_SMTP_FROM"),
              "username": env.get("THTH_SMTP_USERNAME"),
              "password": env.get("THTH_SMTP_PASSWORD")}
    for key in ("user", "admin", "sender", "username", "password"):
        if result[key]:
            redact.register_secret(result[key])
    return result


def readiness(cfg):
    try:
        s = settings(cfg)
        smtp = (isinstance(s["host"], str) and bool(re.fullmatch(r"[A-Za-z0-9.-]+", s["host"]))
                and str(s["port"]).isdigit() and 1 <= int(s["port"]) <= 65535
                and s["tls"] in {"ssl", "starttls"} and address(s["sender"])
                and bool(s["username"]) == bool(s["password"]))
        return {"user_configured": bool(address(s["user"])),
                "admin_configured": bool(address(s["admin"])), "smtp_configured": bool(smtp)}
    except (OSError, ValueError, TypeError):
        return {"user_configured": False, "admin_configured": False, "smtp_configured": False}


def _send(s, recipient, event, account):
    message = EmailMessage()
    message["From"] = s["sender"]
    message["To"] = recipient
    message["Subject"] = f"THTH {account}: {'復旧' if event['state'] == 'recovered' else '停止・要確認' if event['state'] == 'blocked' else '通知テスト'}"
    message["Message-ID"] = f"<{event['id']}.{hashlib.sha256(recipient.lower().encode()).hexdigest()[:16]}@thth.local>"
    repo_status = "repo へ反映済み" if event.get("repo") == "written" else "repo 未反映（後続実行で再試行。原稿を特定できない場合は account 単位の通知のみ）"
    file_hint = healthcheck._safe_file(event.get("file")) or "特定できません（account 全体の状況）"
    message.set_content(f"原稿: {file_hint}\naccount: {account}\n状況: {event['state']}\n発生日時: {event['at']}\n理由: {healthcheck.reason_text(event['reason'])}\n次の対応: {healthcheck.next_action_text(healthcheck.next_action_for(event['reason']))}\n{repo_status}\n\nこの通知は定期実行から送信しました。VM 自体の停止は外部の死活監視で検知してください。\n")
    client = None
    accepted = False
    try:
        context = ssl.create_default_context()
        if s["tls"] == "ssl":
            client = smtplib.SMTP_SSL(s["host"], int(s["port"]), timeout=10, context=context)
        elif s["tls"] == "starttls":
            client = smtplib.SMTP(s["host"], int(s["port"]), timeout=10)
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        else:
            raise ValueError("tls_required")
        if s["username"]:
            client.login(s["username"], s["password"])
        accepted = client.send_message(message, from_addr=s["sender"], to_addrs=[recipient]) == {}
        return accepted
    finally:
        if client:
            try:
                client.quit()
            except Exception:
                # DATA was already accepted; a failed QUIT must not cause resend.
                with contextlib.suppress(Exception):
                    client.close()


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("repo_operation_failed")
    return result.stdout.strip()


def _safe_source(cfg, raw):
    if not isinstance(raw, str) or not raw or not cfg or accounts.repo_state(cfg) != accounts.REPO_OK:
        return None
    repo = Path(accounts.resolved_repo_dir(cfg))
    queue = repo / cfg["queue_dir"]
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    # raw result is normally a basename; resolve that only in configured queue.
    if len(Path(raw).parts) == 1:
        path = queue / raw
    try:
        rel = path.relative_to(repo)
        path.relative_to(queue)
    except ValueError:
        return None
    if ".." in rel.parts or path.suffix != ".md":
        return None
    cursor = repo
    if cursor.is_symlink():
        return None
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    return rel.as_posix()


def source(cfg, state_dir, result):
    try:
        row = inflight.read(state_dir)
    except (OSError, ValueError, TypeError):
        row = None
    if row is not None and not isinstance(row, dict):
        return None  # corrupt publication state: account notification, never rewrite it
    raw = (row or {}).get("file") or getattr(result, "file", None)
    if not raw:
        from . import threadrun
        try:
            unresolved = threadrun.has_unresolved(cfg["account"]) if cfg else []
        except (OSError, ValueError, TypeError):
            unresolved = []
        if len(unresolved) == 1:
            raw = unresolved[0].get("rel_path")
    return _safe_source(cfg, raw)


def _fingerprint(path):
    text = Path(path).read_text(encoding="utf-8")
    lines = text.split("\n")
    end = writeback._split_front_matter_lines(lines)
    stable = [line for line in lines[1:end] if line.split(":", 1)[0].strip() not in FIELDS]
    return hashlib.sha256(("\n".join(stable) + "\n---\n" + "\n".join(lines[end + 1:])).encode()).hexdigest()



def _publication_fingerprint(path, cfg):
    from . import core, bundle, approval
    text = Path(path).read_text(encoding="utf-8")
    if bundle.is_bundle_text(text):
        b = bundle.parse_text(text, str(path))
        segments, problems = bundle.load_segments(b, cfg["media"])
        if b.malformed or problems:
            raise ValueError("invalid_bundle")
        return approval.compute_bundle_sha(segments=segments, account=b.get("account"), topic=b.get("topic"),
                                           publish_at=b.get("publish_at"), continue_until=b.get("continue_until"))
    value = core._current_fingerprint(str(path), cfg["media"])
    if not value:
        raise ValueError("invalid_queue")
    return value

def _repo_write(cfg, account, event, persist):
    if event["reason"] in {"repo_sync_failed", "writeback_push_failed", "text_mismatch_before_writeback", "text_mismatch_after_rebase"}:
        raise ValueError("unsafe_writeback_context")
    if not event["file"]:
        event["repo"] = "no_source"
        return
    if _safe_source(cfg, event["file"]) != event["file"]:
        raise ValueError("unsafe_queue_path")
    repo = accounts.resolved_repo_dir(cfg)
    path = Path(repo) / event["file"]
    if _git(repo, "status", "--porcelain"):
        raise ValueError("repo_dirty")
    _git(repo, "fetch", "origin")
    head = _git(repo, "rev-parse", "HEAD")
    upstream = _git(repo, "rev-parse", "@{u}")
    # Retry only our exact known commit, never unrelated ahead commits.
    if event.get("commit"):
        if head != event["commit"] or _fingerprint(path) != event["fingerprint"]:
            raise ValueError("repo_changed")
        if upstream != head and _git(repo, "rev-parse", "HEAD^") != upstream:
            raise ValueError("repo_diverged")
        if upstream != head:
            _git(repo, "push", "origin", "HEAD")
        event["repo"] = "written"
        return
    if head != upstream:
        # Permit only remote fast-forward, never ahead/diverged state.
        _git(repo, "merge-base", "--is-ancestor", "HEAD", "@{u}")
        _git(repo, "merge", "--ff-only", "@{u}")
    if _git(repo, "status", "--porcelain"):
        raise ValueError("repo_dirty")
    if _safe_source(cfg, event["file"]) != event["file"]:
        raise ValueError("unsafe_queue_path")
    qf = queuefile.parse(str(path))
    if qf.get("thth") == "2":
        from . import bundle
        qf = bundle.parse(str(path))
    if qf.malformed or qf.get("account") != account:
        raise ValueError("wrong_queue_account")
    current = _fingerprint(path)
    if event.get("fingerprint") and event["fingerprint"] != current:
        raise ValueError("queue_changed")
    if not event.get("fingerprint"):
        raise ValueError("missing_original_fingerprint")
    persist()
    writeback.set_front_matter_fields(str(path), {
        "thth_run_state": event["state"], "thth_run_at": event["at"],
        "thth_run_reason": event["reason"], "thth_run_action": healthcheck.next_action_for(event["reason"]),
        "thth_incident_id": event["id"],
        "thth_run_detail": healthcheck.reason_text(event["reason"]),
        "thth_run_next": healthcheck.next_action_text(healthcheck.next_action_for(event["reason"]))})
    if _fingerprint(path) != current:
        raise ValueError("queue_changed")
    _git(repo, "add", "--", event["file"])
    _git(repo, "commit", "--only", "-m", "thth: record account incident state", "--", event["file"])
    event["commit"] = _git(repo, "rev-parse", "HEAD")
    persist()  # durable known commit before push
    _git(repo, "push", "origin", "HEAD")
    event["repo"] = "written"


def _validate(row):
    if not isinstance(row, dict) or row.get("version") != 1 or not isinstance(row.get("events"), list) or row.get("last") not in {None, "success", "fail"}:
        raise ValueError("invalid_outbox")
    if len(row["events"]) > 100:
        raise ValueError("outbox_full")
    for e in row["events"]:
        if (not isinstance(e, dict) or not re.fullmatch(r"[a-f0-9]{32}", e.get("id", ""))
                or e.get("state") not in {"blocked", "recovered"} or not jst.parse(e.get("at"))
                or not healthcheck._reason_code_is_safe(e.get("reason"))
                or not isinstance(e.get("accepted"), dict)
                or e.get("repo") not in {"pending", "written", "no_source"}
                or (e.get("file") is not None and not isinstance(e.get("file"), str))):
            raise ValueError("invalid_outbox")
        for role, value in e["accepted"].items():
            if role not in {"user", "admin"} or not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("invalid_outbox")
    if row["last"] == "fail" and (not row["events"] or row["events"][-1]["state"] != "blocked"):
        raise ValueError("invalid_outbox")


def notify(account, cfg, diag, *, state_dir, result=None):
    """Invoked after core locks release; lock order repo -> account -> outbox."""
    if not accounts.name_is_safe(account):
        return "invalid_account"
    with contextlib.ExitStack() as stack:
        if cfg and accounts.repo_state(cfg) == accounts.REPO_OK:
            stack.enter_context(lock.AccountLock(accounts.repo_lock_path_for(accounts.resolved_repo_dir(cfg))))
        stack.enter_context(lock.AccountLock(accounts.account_lock_path_for(account)))
        stack.enter_context(lock.AccountLock(str(Path(state_dir) / "incident.lock")))
        path = Path(state_dir) / STATE_FILE
        row = _load(path, {"version": 1, "last": None, "events": []})
        _validate(row)
        def persist():
            _save(path, row)
        if diag.state not in {"fail", "success"} or not healthcheck._reason_code_is_safe(diag.reason_code):
            raise ValueError("invalid_diagnostic")
        if cfg is not None and not cfg.get("production") and not healthcheck.has_blocking_state(account, state_dir):
            return "rehearsal"
        thread_identity = None
        try:
            from . import threadrun
            unresolved = threadrun.has_unresolved(account)
            if len(unresolved) == 1:
                thread_identity = [unresolved[0].get("run_id"), unresolved[0].get("started_at")]
        except (OSError, ValueError, TypeError):
            pass
        identity = hashlib.sha256(json.dumps([diag.since, source(cfg, state_dir, result), thread_identity], ensure_ascii=False).encode()).hexdigest()
        changed_incident = diag.state == "fail" and row["last"] == "fail" and row.get("identity") != identity
        if row["last"] != diag.state or changed_incident:
            if diag.state == "fail" or row["last"] == "fail":
                prior = row["events"][-1] if row["events"] else None
                file = source(cfg, state_dir, result) if diag.state == "fail" else prior["file"]
                event = {"id": uuid.uuid4().hex, "state": "blocked" if diag.state == "fail" else "recovered",
                         "at": jst.iso(jst.now_jst()), "reason": diag.reason_code,
                         "file": file, "repo": "pending", "accepted": {}}
                # Pin original content immediately, before later edits / git sync.
                if file:
                    try:
                        original_path = Path(accounts.resolved_repo_dir(cfg)) / file
                        event["fingerprint"] = _fingerprint(original_path)
                        event["publication"] = _publication_fingerprint(original_path, cfg)
                        if diag.state == "success" and event["publication"] != prior.get("publication"):
                            event["file"] = None
                        record = inflight.read(state_dir)
                        if diag.state == "fail" and record:
                            from . import core
                            if (record.get("post_id") or not record.get("approved_fingerprint") or
                                    not core._fingerprint_matches(str(Path(accounts.resolved_repo_dir(cfg)) / file), cfg["media"], record["approved_fingerprint"])):
                                event["file"] = None
                        elif diag.state == "fail" and not getattr(result, "file", None):
                            from . import threadrun
                            matching = threadrun.has_unresolved(account)
                            if len(matching) != 1 or matching[0].get("bundle_sha") != event["publication"]:
                                event["file"] = None
                    except (OSError, ValueError, TypeError):
                        event["file"] = None
                row["events"].append(event)
            row["last"] = diag.state
            row["identity"] = identity
            persist()
        ready = readiness(cfg)["smtp_configured"]
        s = settings(cfg) if ready else None
        blocked_roles = set()
        for event in row["events"]:
            if event["repo"] == "pending":
                try:
                    if cfg is not None and not cfg.get("production"):
                        raise ValueError("rehearsal_writeback_disabled")
                    _repo_write(cfg, account, event, persist)
                except Exception:
                    event["repo_error"] = "writeback_pending"  # finite code only
                persist()
            if not ready:
                continue
            for role in ("user", "admin"):
                if role in blocked_roles:
                    continue
                recipient = s[role]
                if not address(recipient):
                    event.setdefault("mail_errors", {})[role] = "not_configured"
                    blocked_roles.add(role)
                    continue
                key = hashlib.sha256(recipient.lower().encode()).hexdigest()
                if role in event["accepted"]:
                    continue
                if key in event["accepted"].values():
                    event["accepted"][role] = key
                    persist()
                    continue
                try:
                    if _send(s, recipient, event, account):
                        event["accepted"][role] = key
                        event.setdefault("mail_errors", {}).pop(role, None)
                        persist()
                    else:
                        event.setdefault("mail_errors", {})[role] = "smtp_failed"
                except Exception:
                    event.setdefault("mail_errors", {})[role] = "smtp_failed"
                if role not in event["accepted"]:
                    blocked_roles.add(role)
            persist()

        # Keep the last transition for recovery linkage, prune only fully handled history.
        while len(row["events"]) > 1 and len(row["events"][0]["accepted"]) == 2 and row["events"][0]["repo"] in {"written", "no_source"}:
            row["events"].pop(0)
        persist()
        return "processed" if ready else "not_configured"


def cmd_notifications(args):
    """Configuration from a private JSON file/stdin, never command-line secrets."""
    import sys
    try:
        cfg = accounts.load_account(args.account) if args.account else None
        if args.action == "config":
            if not args.input:
                raise ValueError("config_requires_input")
            if args.input == "-":
                data = json.load(sys.stdin)
            else:
                with open(args.input, encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, dict) or set(data) - {"user_email", "admin_email", "smtp"}:
                raise ValueError("invalid_config")
            if "user_email" in data and (cfg is None or not address(data["user_email"])):
                raise ValueError("invalid_user_email")
            if "admin_email" in data and not address(data["admin_email"]):
                raise ValueError("invalid_admin_email")
            if "smtp" in data:
                smtp = data["smtp"]
                if not isinstance(smtp, dict) or set(smtp) - {"host", "port", "tls", "sender", "username", "password"}:
                    raise ValueError("invalid_smtp_config")
                if any(not isinstance(v, str) for v in smtp.values()):
                    raise ValueError("invalid_smtp_config")
                if smtp.get("tls", "ssl") not in {"ssl", "starttls"}:
                    raise ValueError("tls_required")
            path = Path(accounts.thth_root()) / CONFIG_FILE
            with lock.AccountLock(str(path) + ".lock"):
                current = _load(path, {})
                if not isinstance(current, dict):
                    raise ValueError("invalid_config")
                if "user_email" in data:
                    current.setdefault("users", {})[args.account] = data["user_email"]
                if "admin_email" in data:
                    current["admin_email"] = data["admin_email"]
                if "smtp" in data:
                    current["smtp"] = data["smtp"]
                _save(path, current)
            print("通知設定を private file に保存しました。宛先・認証値は表示しません。")
            return 0
        if cfg is None:
            raise ValueError("account_required")
        status = readiness(cfg)
        if args.action == "status":
            status = summary(cfg, accounts.state_dir_for(args.account))
            print(json.dumps(status, ensure_ascii=False))
            return 0 if all(status.get(k) for k in ("user_configured", "admin_configured", "smtp_configured")) else 1
        if not all(status.values()):
            print("利用者・管理者・TLS SMTP の設定がすべて必要です", file=sys.stderr)
            return 2
        event = {"id": uuid.uuid4().hex, "state": "test", "reason": "healthy", "repo": "no_source", "at": jst.iso(jst.now_jst())}
        s = settings(cfg)
        result = {}
        seen = {}
        for role in ("user", "admin"):
            key = s[role].lower()
            if key not in seen:
                try:
                    seen[key] = _send(s, s[role], event, args.account)
                except Exception:
                    seen[key] = False
            result[role] = "accepted" if seen[key] else "failed"
        # Record configuration fingerprint, not addresses or credentials.
        record = {"at": event["at"], "roles": result, "configuration": config_fingerprint(s)}
        _save(Path(accounts.state_dir_for(args.account)) / "notification-test.json", record)
        print(json.dumps(result))
        print("accepted は SMTP サーバ受領です。両宛先の受信箱で到達を確認してください。")
        return 0 if all(v == "accepted" for v in result.values()) else 1
    except Exception:
        print("通知操作に失敗しました。設定・private state・接続を確認してください（秘密値は表示しません）。", file=sys.stderr)
        return 2


def config_fingerprint(s):
    return hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()


def register(sub):
    p = sub.add_parser("notifications", help="共通停止通知の設定・確認・両宛先メールテスト")
    p.add_argument("action", choices=("config", "status", "test"))
    p.add_argument("account", nargs="?")
    p.add_argument("--input", help="private JSON file または - (stdin)。config で使用")
    p.set_defaults(func=cmd_notifications)


def summary(cfg, state_dir):
    result = readiness(cfg)
    result["tested_current_configuration"] = False
    try:
        test = _load(Path(state_dir) / "notification-test.json", {})
        result["tested_current_configuration"] = (test.get("configuration") == config_fingerprint(settings(cfg)) and test.get("roles") == {"user": "accepted", "admin": "accepted"})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        row = _load(Path(state_dir) / STATE_FILE, {"version": 1, "last": None, "events": []})
        _validate(row)
        result.update({"outbox": "ok", "repo_pending": sum(e["repo"] == "pending" for e in row["events"]),
                       "mail_pending": sum(2 - len(e["accepted"]) for e in row["events"]),
                       "last_state": row["last"]})
    except (OSError, ValueError, TypeError, AttributeError):
        result.update({"outbox": "unreadable", "repo_pending": None, "mail_pending": None, "last_state": None})
    return result
