"""3.12.0 段 3: 設定の口（CLI・MCP）・止めたのを戻す・動きの一覧（VM 側）・鍵の発行し直し。

見るのは:
  - `thth account set|resume|status`: 台帳を書き換え、変更ログに残す。`--by` 必須。
  - MCP の `thth_settings` は締める向きだけ（緩めるのは settings_loosen_requires_owner）。
    `thth_account_status` は読むだけ。止まった口座を戻す道具は MCP に無い。
  - 裁定 (a) 返信には最短間隔を掛けない（上限と burst には数える）。
  - 裁定 (b) scheduled: false の口座では予約・猶予を schedule_unavailable で断る。
  - 裁定 (c) 止まった口座は MCP の成功の答えにも理由が載る。
  - /activity の VM 側: 持ち主ごとの要約（本文は先頭 60 字）と、持ち主の操作（止める・戻す・予約の
    取り消し・設定・鍵の取り消し・発行し直し）。他人の口座の操作は not_owner。
  - 3.13.0: `approval` の設定は無い（CLI・MCP・/activity のどこからも変えられない）。台帳に残っていれば
    `account status` が「古い項目」と言う。招待の `--approval` も無い。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import secrets
import subprocess
import sys

import pytest

from thth import accounts, activity, admin_log, guard, jst, report_http, sent, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_guard import call, draft, ledger, publisher, refused, credential_id  # noqa: F401


def thth(*args, env_root=None):
    return subprocess.run([sys.executable, "-m", "thth", *args], capture_output=True, text=True)


def read_ledger(env):
    return json.loads((env["root"] / "accounts/alpha.json").read_text())


def mcp(env, monkeypatch):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(env["path"]))
    monkeypatch.setenv("THTH_REPORT_TOKEN", env["bearer"])
    return server


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_account_setは台帳を書き換え変更ログに残す_by必須(env):
    assert thth("account", "set", "alpha", "daily_max_posts", "4").returncode == 2
    assert "daily_max_posts" not in read_ledger(env)
    done = thth("account", "set", "alpha", "burst", "2", "20", "--by", "masaru")
    assert done.returncode == 0, done.stderr
    assert read_ledger(env)["burst"] == {"count": 2, "minutes": 20}
    done = thth("account", "set", "alpha", "daily_max_posts", "30", "--by", "masaru", "--json")
    assert json.loads(done.stdout) == {"account": "alpha", "key": "daily_max_posts", "before": 8, "after": 30,
                                       "changed": True}
    done = thth("account", "set", "alpha", "min_interval_hours", "1.5", "--by", "masaru")
    assert done.returncode == 0 and read_ledger(env)["min_interval_hours"] == 1.5
    rows, _ = admin_log.read(event="settings_set")
    assert [row["diff"] for row in rows] == [{"burst_count": [3, 2]},
                                             {"burst_minutes": [10, 20]}, {"daily_max_posts": [8, 30]},
                                             {"min_interval_hours": [0, 1.5]}]
    assert all(row["by"] == "masaru" and row["via"] == "cli" for row in rows)


@pytest.mark.parametrize("args", [("stopped", "0"), ("approval", "sometimes"), ("approval", "none"), ("approval", "all"),
                                  ("daily_max_posts", "-1"),
                                  ("hold_minutes", "abc"), ("burst_count", "0"), ("min_interval_hours", "999")])
def test_account_setは知らない名前や値を断る(env, args):
    before = read_ledger(env)
    done = thth("account", "set", "alpha", *args, "--by", "masaru")
    assert done.returncode == 2 and "invalid_setting" in done.stderr
    assert read_ledger(env) == before


def test_scheduledでない口座にhold_minutesを書くのは断る(env):
    ledger(env, scheduled=False)
    done = thth("account", "set", "alpha", "hold_minutes", "10", "--by", "masaru")
    assert done.returncode == 2 and "schedule_unavailable" in done.stderr
    assert "hold_minutes" not in read_ledger(env)
    assert thth("account", "set", "alpha", "hold_minutes", "0", "--by", "masaru").returncode == 0


def test_account_statusとresume(env):
    done = thth("account", "status", "alpha", "--json")
    row = json.loads(done.stdout)
    assert "approval" not in row["settings"] and row["ignored"] == [] and row["stopped"] is None and row["today"]["posts"] == 0
    assert row["settings"]["burst_count"] == 3 and row["scheduled"] is True
    guard.stop("alpha", "burst")
    row = json.loads(thth("account", "status", "alpha", "--json").stdout)
    assert row["stopped"]["reason"] == "burst"
    text = thth("account", "status", "alpha").stdout
    assert "止まっています" in text and "急な連投" in text
    assert thth("account", "resume", "alpha").returncode == 2  # --by 必須
    assert guard.stopped("alpha") is not None
    done = thth("account", "resume", "alpha", "--by", "masaru")
    assert done.returncode == 0 and guard.stopped("alpha") is None
    rows, _ = admin_log.read(event="guard_resumed")
    assert rows[-1]["by"] == "masaru" and rows[-1]["via"] == "cli"
    assert "止まっていません" in thth("account", "resume", "alpha", "--by", "masaru").stdout


def test_台帳に残ったapprovalはaccount_statusが古い項目と言う(env):
    assert "古い項目" not in thth("account", "status", "alpha").stdout
    ledger(env, approval="all")
    row = json.loads(thth("account", "status", "alpha", "--json").stdout)
    assert row["ignored"] == ["approval"] and "approval" not in row["settings"]
    text = thth("account", "status", "alpha").stdout
    assert "  古い項目 approval（無視）\n" in text and "承認（approval）" not in text


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------

def test_mcpのthth_settingsは締める向きだけ(env, monkeypatch):
    server = mcp(env, monkeypatch)
    names = {x["name"] for x in server._handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]}
    assert {"thth_settings", "thth_account_status"} <= names
    assert not any("resume" in name or "unstop" in name for name in names)
    read = json.loads(server.call_tool("thth_settings", {"account": "alpha"})["content"][0]["text"])
    assert "approval" not in read["settings"] and "approval" not in read["keys"] and "burst_count" in read["keys"]
    for key, value in (("daily_max_posts", "4"), ("burst_count", "2"),
                       ("burst_minutes", "30"), ("hold_minutes", "5"), ("min_interval_hours", "8")):
        ok = server.call_tool("thth_settings", {"account": "alpha", "key": key, "value": value})
        assert not ok.get("isError"), ok
    cfg = read_ledger(env)
    assert "approval" not in cfg and cfg["daily_max_posts"] == 4 and cfg["burst"] == {"count": 2, "minutes": 30}
    assert cfg["hold_minutes"] == 5 and cfg["min_interval_hours"] == 8
    for key, value in (("daily_max_posts", "5"), ("burst_count", "3"),
                       ("burst_minutes", "10"), ("hold_minutes", "0"), ("min_interval_hours", "1")):
        denied = server.call_tool("thth_settings", {"account": "alpha", "key": key, "value": value})
        assert denied["isError"] and denied["content"][0]["text"] == "settings_loosen_requires_owner", (key, denied)
    assert read_ledger(env) == cfg
    rows, _ = admin_log.read(event="settings_set")
    assert rows and all(row["via"] == "mcp" and row["by"] == "person" for row in rows)
    # 変えられるのは安全装置の数値だけ（3.13.0: approval も無い）。
    for key in ("approval", "stopped", "production", "scheduled", "quiet_hours"):
        denied = server.call_tool("thth_settings", {"account": "alpha", "key": key, "value": "0"})
        assert denied["content"][0]["text"] == "invalid_setting"
    assert server.call_tool("thth_settings", {"account": "beta"})["isError"]
    assert server.call_tool("thth_settings", {"account": "beta", "key": "approval", "value": "all"})["isError"]


def test_止まった口座はmcpから戻せず_成功の答えにも理由が載る(env, monkeypatch):
    server = mcp(env, monkeypatch)
    guard.stop("alpha", "burst")
    status = server.call_tool("thth_account_status", {"account": "alpha"})
    assert not status.get("isError")
    assert json.loads(status["content"][0]["text"])["stopped"]["reason"] == "burst"
    assert status["content"][1]["text"].startswith("account_stopped: burst: ")
    listed = server.call_tool("thth_draft_list", {"account": "alpha"})
    assert listed["content"][-1]["text"].startswith("account_stopped: burst")
    # 設定の口から止めた印は触れない（緩めても締めても止まったまま）。
    server.call_tool("thth_settings", {"account": "alpha", "key": "daily_max_posts", "value": "1"})
    assert guard.stopped("alpha")["reason"] == "burst"
    for name in [x["name"] for x in server._handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]]:
        assert "resume" not in name
    assert server.call_tool("thth_account_resume", {"account": "alpha"})["content"][0]["text"] == "unsupported_operation"
    assert guard.stopped("alpha") is not None
    assert env["bearer"] not in json.dumps(status)


# --------------------------------------------------------------------------
# 裁定 (a)・(b)
# --------------------------------------------------------------------------

def test_返信には最短間隔を掛けない_上限には数える(env, publisher, monkeypatch):
    from thth import adapters
    made = adapters.make_adapter

    def with_fetch(*args):
        adapter = made(*args)
        type(adapter).fetch_post = lambda self, post_id: {"id": post_id}
        return adapter
    monkeypatch.setattr(adapters, "make_adapter", with_fetch)
    ledger(env, min_interval_hours=6, daily_max_posts=2)
    call(env, operation="send_request", body="一本目")
    assert str(refused(env, operation="send_request", body="二本目")) == "too_soon"
    replied = call(env, operation="send_request", body="返信の本文", reply_to="123456")
    assert replied["status"] == "published"
    # 1 日の上限（2）には返信も数える。
    assert str(refused(env, operation="send_request", body="三本目", reply_to="123456")) == "daily_limit"


@pytest.mark.parametrize("mode", [None, "publish", "all"])
def test_scheduledでない口座の予約はschedule_unavailable(env, remote, mode):
    ledger(env, approval=mode, scheduled=False)
    one = draft(env)
    assert str(refused(env, operation="schedule_request", draft_id=one["draft_id"])) == "schedule_unavailable"
    assert str(refused(env, operation="approval_request", draft_id=one["draft_id"])) == "unsupported_operation"
    text = next((env["root"] / "repos/_server/alpha/queue").glob("*.md")).read_text()
    assert "status: draft" in text


def test_scheduledでない口座で猶予つきの今すぐも断る_下書きも作らない(env, publisher):
    ledger(env, scheduled=False, hold_minutes=10)
    assert str(refused(env, operation="send_request", body="待つ本文")) == "schedule_unavailable"
    assert publisher == []
    queue = env["root"] / "repos/_server/alpha/queue"
    assert not queue.exists() or not list(queue.glob("*.md"))


# --------------------------------------------------------------------------
# 動きの一覧（VM 側）
# --------------------------------------------------------------------------

class ActivityWorker:
    """Worker の person object の sync を真似る（押し上げた要約と、渡す操作）。"""

    def __init__(self):
        self.pushed = {}
        self.queue = {}
        self.completed = []

    def __call__(self, kind, subject, operation, body):
        assert (kind, operation) == ("activity", "sync")
        body = json.loads(json.dumps(body))
        self.pushed[subject] = body["accounts"]
        for row in body["completed"]:
            self.completed.append(row)
            self.queue.get(subject, {}).pop(row["id"], None)
        return {"status": "synced", "actions": list(self.queue.get(subject, {}).values())}

    def ask(self, person, **action):
        action_id = secrets.token_urlsafe(32)
        self.queue.setdefault(person, {})[action_id] = {"id": action_id, **action}
        return action_id

    def outcome(self, action_id):
        return next(row for row in self.completed if row["id"] == action_id)


@pytest.fixture
def worker(monkeypatch):
    from thth import approval_relay
    fake = ActivityWorker()
    monkeypatch.setattr(approval_relay, "signed_request", fake)
    activity._next_sync.clear()
    return fake


def test_持ち主は資格のactor_招待の口座は口座名(env):
    assert activity.owners(env["path"]) == {"person": {"alpha": "alpha"}}
    ledger(env, provenance={"created_at": jst.iso(), "created_by": "masaru", "created_via": "invite",
                            "created_host": "vm"})
    assert activity.owners(env["path"]) == {"person": {"alpha": "alpha"}, "alpha": {"alpha": "alpha"}}


def test_要約は本文の先頭60字だけ_秘密を含まない(env, worker, publisher):
    ledger(env, hold_minutes=15)
    body = "あ" * 70 + "末尾の語"
    sent.write(str(env["root"] / "state/alpha"), post_id="42", text=body, body_hash="x", sent_at=jst.iso())
    call(env, operation="send_request", body="猶予中の本文")
    activity.run_once(env["path"])
    (summary,) = worker.pushed["person"]
    assert summary["account"] == "alpha" and "approval" not in summary
    assert summary["limits"]["hold_minutes"] == 15 and summary["today"]["posts"] == 1
    kinds = [row["kind"] for row in summary["rows"]]
    assert "published" in kinds and "held" in kinds
    published = next(row for row in summary["rows"] if row["kind"] == "published")
    assert published["head"] == "あ" * 60 and published["post_id"] == "42"
    assert summary["credential"]["id"] == credential_id(env)
    text = json.dumps(worker.pushed)
    assert "末尾の語" not in text and env["bearer"] not in text
    assert hashlib.sha256(env["bearer"].encode()).hexdigest() not in text


def test_持ち主の操作_止める戻す設定と取り消し(env, worker, publisher):
    ledger(env, hold_minutes=15)
    held = call(env, operation="send_request", body="取り消す本文")
    stop = worker.ask("person", account="alpha", kind="stop")
    activity.run_once(env["path"])
    assert worker.outcome(stop)["outcome"] == "done" and guard.stopped("alpha")["reason"] == "owner"
    assert str(refused(env, operation="send_request", body="止まっている")) == "account_stopped"
    assert worker.pushed["person"][0]["stopped"]["reason"] == "owner"
    resume = worker.ask("person", account="alpha", kind="resume")
    activity._next_sync.clear()
    activity.run_once(env["path"])
    assert worker.outcome(resume)["outcome"] == "done" and guard.stopped("alpha") is None
    # /activity からは緩める向きも変えられる（secret で確かめたあと）。
    loosen = worker.ask("person", account="alpha", kind="settings", key="daily_max_posts", value="50")
    cancel = worker.ask("person", account="alpha", kind="cancel", draft_id=held["draft_id"])
    activity._next_sync.clear()
    activity.run_once(env["path"])
    assert worker.outcome(loosen)["outcome"] == "done" and read_ledger(env)["daily_max_posts"] == 50
    assert worker.outcome(cancel)["outcome"] == "done", worker.outcome(cancel)
    text = next((env["root"] / "repos/_server/alpha/queue").glob("*.md")).read_text()
    assert "status: draft" in text and "revoked_by: person" in text
    events = {row["event"]: row for row in admin_log.read()[0]}
    for name in ("guard_stopped", "guard_resumed", "settings_set", "schedule_cancelled"):
        assert events[name]["via"] == "http" and events[name]["by"] == "person", name


def test_他人の口座の操作はnot_owner(env, worker):
    other = worker.ask("person", account="beta", kind="stop")
    stranger = worker.ask("someone", account="alpha", kind="stop")
    activity.sync_person("person", ["alpha"], env["path"])
    activity.sync_person("someone", [], env["path"])
    assert worker.outcome(other) == {"id": other, "outcome": "failed", "reason": "not_owner"}
    assert worker.outcome(stranger) == {"id": stranger, "outcome": "failed", "reason": "not_owner"}
    assert guard.stopped("alpha") is None and guard.stopped("beta") is None
    bad = worker.ask("person", account="alpha", kind="rotate", sha256="not-a-hash")
    activity.sync_person("person", ["alpha"], env["path"])
    assert worker.outcome(bad)["reason"] == "invalid_action"


def test_鍵の取り消しと発行し直し(env, worker, monkeypatch):
    server = mcp(env, monkeypatch)
    assert not server.call_tool("thth_draft_list", {"account": "alpha"}).get("isError")
    revoke = worker.ask("person", account="alpha", kind="revoke")
    activity.sync_person("person", ["alpha"], env["path"])
    assert worker.outcome(revoke)["outcome"] == "done"
    assert server.call_tool("thth_draft_list", {"account": "alpha"})["isError"]
    bearer = secrets.token_urlsafe(32)
    digest = hashlib.sha256(bearer.encode()).hexdigest()
    rotate = worker.ask("person", account="alpha", kind="rotate", sha256=digest)
    activity.sync_person("person", ["alpha"], env["path"])
    assert worker.outcome(rotate)["outcome"] == "done", worker.outcome(rotate)
    monkeypatch.setenv("THTH_REPORT_TOKEN", bearer)
    assert not server.call_tool("thth_draft_list", {"account": "alpha"}).get("isError")
    monkeypatch.setenv("THTH_REPORT_TOKEN", env["bearer"])
    assert server.call_tool("thth_draft_list", {"account": "alpha"})["isError"]
    config = json.loads(env["path"].read_text())
    (row,) = config["credentials"]
    assert row["sha256"] == digest and row["revoked"] is False and row["actor"] == "person"
    expires = datetime.datetime.fromisoformat(row["expires_at"])
    assert datetime.timedelta(days=364) < expires - datetime.datetime.now(datetime.timezone.utc) <= datetime.timedelta(days=365)
    # 同じ操作がもう一度届いても変わらない（完了を返しそこねた場合）。
    again = worker.ask("person", account="alpha", kind="rotate", sha256=digest)
    activity.sync_person("person", ["alpha"], env["path"])
    assert worker.outcome(again)["outcome"] == "done" and json.loads(env["path"].read_text()) == config
    rows = admin_log.read()[0]
    assert [r["event"] for r in rows if r["event"].startswith("credential_")] == ["credential_revoked", "credential_rotated"]
    logged = json.dumps(rows)
    assert bearer not in logged and digest not in logged and env["bearer"] not in logged


# --------------------------------------------------------------------------
# 招待の --approval（3.13.0 で無くした）
# --------------------------------------------------------------------------

def test_招待にapprovalは無い_古い記録のapprovalは読まずに通す(monkeypatch, tmp_path):
    from thth import invites
    from tests.test_v3100_invite_create import FakeTTY, FakeWorker
    import socket
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("external network"))
    from thth import approval_relay
    monkeypatch.setattr(invites, "terminal", FakeTTY())
    monkeypatch.setattr(approval_relay, "signed_request", FakeWorker())
    with pytest.raises(TypeError):
        invites.create(media="threads", project="x", by="masaru", approval="all")
    row = invites.create(media="threads", project="meta-review", production=True, by="masaru")
    assert "approval" not in row
    record = invites.STORE.get(row["invite_id"])
    assert "approval" not in record and "approval" not in invites._ledger(record)
    # 3.12.0 の招待の記録（approval あり）も、読まずに通す。
    with invites.STORE.locked() as directory:
        invites.STORE.write(directory, {**record, "approval": "all"})
    rows, broken = invites.list_invites()
    assert broken == 0 and [r["invite_id"] for r in rows] == [row["invite_id"]] and "approval" not in rows[0]
    assert "approval" not in invites._ledger(invites.STORE.get(row["invite_id"]))
    created = admin_log.read(event="invite_created")[0]
    assert "approval" not in created[0]["diff"]


def test_招待の_approvalの引数は無い():
    done = thth("admin", "invite", "create", "--media", "threads", "--project", "x", "--approval", "all", "--by", "masaru")
    assert done.returncode == 2 and "--approval" in done.stderr
