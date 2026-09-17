"""Account incident tests: local bare git remote and fake SMTP, no external mail."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from thth import incident, healthcheck, accounts, approval, inflight


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    for key in list(os.environ):
        if key.startswith("THTH_SMTP_"):
            monkeypatch.delenv(key)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    repo = tmp_path / "repo"
    git(tmp_path, "clone", str(remote), str(repo))
    git(repo, "config", "user.email", "test@example.org")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "commit.gpgsign", "false")
    queue = repo / "docs/sns/queue"
    queue.mkdir(parents=True)
    original = "---\nthth: 1\naccount: demo\nstatus: approved\npublish_at: 2026-09-18T10:00:00+09:00\napproved_sha: pinned\n---\n\n## threads\n本文です。\n"
    path = queue / "one.md"
    path.write_text(original)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    git(repo, "push", "-u", "origin", "HEAD")
    cfg = {"account": "demo", "repo_dir": str(repo), "queue_dir": "docs/sns/queue", "media": "threads", "production": True,
           "env": str(root / "missing.env"), "notification_email": "user@example.org"}
    incident._save(root / incident.CONFIG_FILE, {"admin_email": "admin@example.org", "smtp": {"host": "smtp.example.org", "sender": "sender@example.org", "tls": "ssl"}})
    state = accounts.state_dir_for("demo")
    captured = []
    monkeypatch.setattr(incident, "_send", lambda s, recipient, event, account: captured.append((recipient, event["state"], event["id"])) or True)
    return SimpleNamespace(root=root, repo=repo, path=path, cfg=cfg, state=state, captured=captured, original=original)


def diag(s, state="fail", reason="publish_timeout", since=None):
    return healthcheck.Diagnostic("demo", state, since, "one.md", "ignored external secret", "ignored external secret", reason if state == "fail" else "healthy", "none")


def notify(s, state="fail", result=True):
    return incident.notify("demo", s.cfg, diag(s, state), state_dir=s.state,
                           result=SimpleNamespace(file=str(s.path), mode="production") if result else None)


def row(s):
    return json.loads((Path(s.state) / incident.STATE_FILE).read_text())


def test_git_roundtrip_preserves_original_and_deduplicates(setup):
    s = setup
    before = incident._fingerprint(s.path)
    notify(s)
    assert incident._fingerprint(s.path) == before
    assert "status: approved" in s.path.read_text()
    assert "thth_run_state: blocked" in s.path.read_text()
    assert "公開 API が時間切れ" in s.path.read_text()
    assert git(s.repo, "rev-parse", "HEAD") == git(s.repo, "rev-parse", "@{u}")
    head = git(s.repo, "rev-parse", "HEAD")
    notify(s)
    assert git(s.repo, "rev-parse", "HEAD") == head
    assert len(s.captured) == 2
    notify(s, "success", result=False)
    assert "thth_run_state: recovered" in s.path.read_text()
    assert len(s.captured) == 4
    assert incident._fingerprint(s.path) == before
    assert len(row(s)["events"]) == 1


def test_initial_success_is_quiet(setup):
    s = setup
    notify(s, "success", result=False)
    assert not s.captured
    assert s.path.read_text() == s.original


def test_role_failure_retries_only_unfinished_and_other_role_gets_recovery(setup, monkeypatch):
    s = setup
    calls = []
    def send(config, recipient, event, account):
        calls.append((recipient, event["state"]))
        return recipient == "admin@example.org"
    monkeypatch.setattr(incident, "_send", send)
    notify(s)
    notify(s, "success", result=False)
    assert calls == [("user@example.org", "blocked"), ("admin@example.org", "blocked"), ("user@example.org", "blocked"), ("admin@example.org", "recovered")]
    monkeypatch.setattr(incident, "_send", lambda c, r, e, a: calls.append((r, e["state"])) or True)
    notify(s, "success", result=False)
    assert calls[-2:] == [("user@example.org", "blocked"), ("user@example.org", "recovered")]
    assert len(row(s)["events"]) == 1


def test_same_address_single_message(setup):
    s = setup
    s.cfg["notification_email"] = "admin@example.org"
    notify(s)
    assert len(s.captured) == 1
    assert len(row(s)["events"][0]["accepted"]) == 2


def test_dirty_outbox_retry_and_changed_content_is_not_overwritten(setup):
    s = setup
    (s.repo / "untracked").write_text("user work")
    notify(s)
    assert s.path.read_text() == s.original
    assert row(s)["events"][0]["repo"] == "pending"
    (s.repo / "untracked").unlink()
    s.path.write_text(s.original.replace("本文", "変更"))
    git(s.repo, "add", ".")
    git(s.repo, "commit", "-m", "change")
    git(s.repo, "push")
    notify(s)
    assert "thth_run_state" not in s.path.read_text()
    assert row(s)["events"][0]["repo"] == "pending"


def test_push_failure_persists_known_commit_and_retries(setup):
    s = setup
    hook = s.repo.parent / "remote.git/hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    notify(s)
    event = row(s)["events"][0]
    assert event["repo"] == "pending" and event["commit"]
    head = git(s.repo, "rev-parse", "HEAD")
    hook.unlink()
    notify(s)
    assert row(s)["events"][0]["repo"] == "written"
    assert git(s.repo, "rev-parse", "HEAD") == head
    assert len(s.captured) == 2


def test_symlink_wrong_account_and_queue_escape_fail_closed(setup):
    s = setup
    for candidate in ("../outside.md", "docs/other.md", "/etc/a.md"):
        assert incident._safe_source(s.cfg, candidate) is None
    s.path.unlink()
    target = s.repo.parent / "outside.md"
    target.write_text(s.original)
    s.path.symlink_to(target)
    notify(s)
    assert target.read_text() == s.original
    assert row(s)["events"][0]["repo"] == "no_source"


def test_corrupt_state_sends_nothing(setup):
    s = setup
    incident._save(Path(s.state) / incident.STATE_FILE, {"version": 1, "last": "fail", "events": []})
    with pytest.raises(ValueError):
        notify(s, "success", result=False)
    assert not s.captured


def test_admin_works_without_user_or_readable_account_env(setup, monkeypatch):
    s = setup
    s.cfg.pop("notification_email")
    monkeypatch.setattr(accounts, "load_env", lambda cfg: (_ for _ in ()).throw(OSError("secret")))
    notify(s)
    assert [r for r, _, _ in s.captured] == ["admin@example.org"]
    assert incident.summary(s.cfg, s.state)["mail_pending"] == 1


def test_no_recipient_or_secret_in_state_or_repo(setup):
    s = setup
    notify(s)
    all_text = (Path(s.state) / incident.STATE_FILE).read_text() + s.path.read_text()
    assert "@example" not in all_text and "ignored external secret" not in all_text


def test_rehearsal_is_quiet(setup):
    s = setup
    s.cfg["production"] = False
    notify(s)
    assert s.path.read_text() == s.original
    assert not s.captured


def test_existing_inflight_mode_rehearsal_still_notifies(setup):
    s = setup
    inflight.write(s.state, file=str(s.path), started="2026-09-18T10:00:00+09:00")
    incident.notify("demo", s.cfg, diag(s), state_dir=s.state, result=SimpleNamespace(file=None, mode="rehearsal"))
    assert len(s.captured) == 2
    assert s.path.read_text() == s.original  # legacy missing expected fingerprint: no repo mutation
    assert inflight.read(s.state)


def test_smtp_tls_recipient_privacy_and_quit_failure(monkeypatch):
    sent = []
    steps = []
    class SMTP:
        def __init__(self, *args, **kwargs):
            steps.append("connect")
        def ehlo(self): steps.append("ehlo")
        def starttls(self, **kwargs): steps.append("tls")
        def login(self, *args): steps.append("login")
        def send_message(self, message, **kwargs):
            steps.append("send")
            sent.append((message, kwargs))
            return {}
        def quit(self): raise OSError("secret quit")
        def close(self): pass
    monkeypatch.setattr(incident.smtplib, "SMTP", SMTP)
    s = {"host": "mail.example.org", "port": "587", "tls": "starttls", "sender": "sender@example.org", "username": "secretuser", "password": "secretpass"}
    e = {"id": "a"*32, "state": "blocked", "reason": "publish_result_unknown", "at": "2026-09-18T10:00:00+09:00", "repo": "pending", "file": "docs/sns/queue/one.md"}
    assert incident._send(s, "user@example.org", e, "demo")
    assert steps == ["connect", "ehlo", "tls", "ehlo", "login", "send"]
    message, kwargs = sent[0]
    assert kwargs["to_addrs"] == ["user@example.org"]
    assert "admin@example.org" not in str(message)
    assert "secretpass" not in str(message)
    assert "repo 未反映" in message.get_content()
    assert "one.md" in message.get_content()
    first_id = message["Message-ID"]
    assert incident._send(s, "user@example.org", e, "demo")
    assert sent[1][0]["Message-ID"] == first_id


def test_tls_failure_never_sends_or_authenticates(monkeypatch):
    class SMTP:
        def __init__(self, *a, **k): pass
        def ehlo(self): pass
        def starttls(self, **kwargs): raise OSError("TLS unavailable")
        def login(self, *a): pytest.fail("plaintext auth")
        def send_message(self, *a, **k): pytest.fail("plaintext send")
        def quit(self): pass
    monkeypatch.setattr(incident.smtplib, "SMTP", SMTP)
    s = {"host": "mail.example.org", "port": "587", "tls": "starttls", "sender": "sender@example.org", "username": "u", "password": "p"}
    with pytest.raises(OSError):
        incident._send(s, "user@example.org", {"id": "a"*32, "state": "blocked", "reason": "unknown", "at": "now", "repo": "pending"}, "demo")


def test_configuration_change_invalidates_test_receipt(setup):
    s = setup
    record = {"configuration": incident.config_fingerprint(incident.settings(s.cfg)), "roles": {"user": "accepted", "admin": "accepted"}}
    incident._save(Path(s.state) / "notification-test.json", record)
    assert incident.summary(s.cfg, s.state)["tested_current_configuration"]
    s.cfg["notification_email"] = "different@example.org"
    assert not incident.summary(s.cfg, s.state)["tested_current_configuration"]


def test_config_invalid_type_does_not_crash_status(setup):
    s = setup
    incident._save(s.root / incident.CONFIG_FILE, {"users": None})
    assert not any(incident.readiness(s.cfg).values())


def test_valid_inflight_pins_original_approval_before_writeback(setup):
    s = setup
    from thth import core
    expected = core._current_fingerprint(str(s.path), "threads")
    inflight.write(s.state, file=str(s.path), started="2026-09-18T10:00:00+09:00", approved_fingerprint=expected)
    before = s.path.read_text().split("---", 2)[2]
    notify(s)
    assert "thth_run_state: blocked" in s.path.read_text()
    assert s.path.read_text().split("---", 2)[2] == before
    assert core._current_fingerprint(str(s.path), "threads") == expected
    assert inflight.read(s.state)["approved_fingerprint"] == expected


def test_v2_unresolved_run_source_and_recovery_file_none(setup, monkeypatch):
    s = setup
    s.path.write_text("---\nthth: 2\naccount: demo\nstatus: approved\npublish_at: 2026-09-18T10:00:00+09:00\ncontinue_until: 2026-09-19T10:00:00+09:00\napproved_sha: preserved\nposts:\n  - index: 1\n---\n\n## threads\n一段目です。\n")
    git(s.repo, "add", ".")
    git(s.repo, "commit", "-m", "bundle")
    git(s.repo, "push")
    from thth import threadrun
    expected = incident._publication_fingerprint(s.path, s.cfg)
    monkeypatch.setattr(threadrun, "has_unresolved", lambda account: [{"rel_path": "docs/sns/queue/one.md", "bundle_sha": expected, "started_at": "2026-09-18T10:00:00+09:00"}])
    notify(s, result=False)
    assert "thth_run_state: blocked" in s.path.read_text()
    assert incident._publication_fingerprint(s.path, s.cfg) == expected
    assert "approved_sha: preserved" in s.path.read_text()
    monkeypatch.setattr(threadrun, "has_unresolved", lambda account: [])
    notify(s, "success", result=False)
    assert "thth_run_state: recovered" in s.path.read_text()
    assert incident._publication_fingerprint(s.path, s.cfg) == expected


def test_recovery_does_not_mark_rewritten_publication(setup):
    s = setup
    notify(s)
    s.path.write_text(s.path.read_text().replace("本文", "別原稿"))
    git(s.repo, "add", ".")
    git(s.repo, "commit", "-m", "new content")
    git(s.repo, "push")
    notify(s, "success", result=False)
    assert "thth_run_state: recovered" not in s.path.read_text()
    assert row(s)["events"][-1]["file"] is None


def test_remote_withdrawal_is_not_overwritten(setup):
    s = setup
    (s.repo / "user-work").write_text("dirty")
    notify(s)
    (s.repo / "user-work").unlink()
    clone = s.repo.parent / "editor"
    git(s.repo.parent, "clone", str(s.repo.parent / "remote.git"), str(clone))
    git(clone, "config", "user.email", "editor@example.org")
    git(clone, "config", "user.name", "editor")
    git(clone, "config", "commit.gpgsign", "false")
    other = clone / "docs/sns/queue/one.md"
    other.write_text(other.read_text().replace("status: approved", "status: withdrawn"))
    git(clone, "add", ".")
    git(clone, "commit", "-m", "withdraw")
    git(clone, "push")
    notify(s)
    assert "status: withdrawn" in s.path.read_text()
    assert "thth_run_state" not in s.path.read_text()
    assert row(s)["events"][0]["repo"] == "pending"


def test_new_thread_run_identity_produces_new_incident(setup, monkeypatch):
    s = setup
    from thth import threadrun
    run = {"run_id": "first", "started_at": "2026-09-18T10:00:00+09:00", "rel_path": "docs/sns/queue/one.md", "bundle_sha": "unknown"}
    monkeypatch.setattr(threadrun, "has_unresolved", lambda account: [run])
    notify(s, result=False)
    notify(s, result=False)
    assert len(s.captured) == 2
    run["run_id"] = "second"
    run["started_at"] = "2026-09-18T12:00:00+09:00"
    notify(s, result=False)
    assert len(s.captured) == 4


@pytest.mark.parametrize("broken", [["non-dict"], "non-dict"])
def test_broken_inflight_type_sends_account_notice_without_changing_it(setup, broken):
    s = setup
    original = json.dumps(broken)
    p = Path(s.state) / "inflight.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(original)
    notify(s)
    assert len(s.captured) == 2
    assert p.read_text() == original
    assert row(s)["events"][0]["file"] is None
    assert s.path.read_text() == s.original
