"""Threads の返信の管理（監査 2026-11-06 #1・#2・`thth reply`）。

**本物の Threads は叩かない。** loopback の偽サーバ（`tests/test_v315_reactions.py` の `fake`）が受けた
要求の形と回数を数える。資料は https://developers.facebook.com/docs/threads/reply-management（11/06 に読んだ）。

1. 隠す・戻す `POST /v1.0/{reply-id}/manage_reply`（`hide=true|false`）が FB・IG と同じ `thth reply hide|unhide` で通る。
2. 返信への返信・投稿は隠さない。Threads に返信を消す口は無いので `delete` は要求を出さずに断る。
3. 承認待ち `GET /v1.0/{media-id}/pending_replies`（`approval_status`）は読むだけで本文を残さない。
4. 承認・無視 `POST /v1.0/{reply-id}/manage_pending_reply`（`approve=true|false`）は 1 段で記録に 1 行。
5. 権限の使用が runs に残り、権限の不使用の見張りが読む。遠くの道は hide・unhide だけ。
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import urllib.parse
from pathlib import Path

from thth import accounts, cli, edit_cli, jst, leave_gate, permission_watch, remote
from thth.adapters import base
from thth.adapters import threads as threads_mod
from tests.test_v315_reactions import fake, make  # noqa: F401 — fixture

REPLY = "17900000000000001"
ROOT = "17800000000000001"
SCOPES = ["threads_basic", "threads_content_publish", "threads_manage_replies", "threads_read_replies"]
SECRET = "THREADS-SECRET-TOKEN"


def _token(scopes=SCOPES):
    return {"access_token": SECRET, "user_id": "123", "scopes": list(scopes), "scopes_source": "debug_token"}


def _args(action, target=REPLY, **kw):
    values = dict(action=action, account="alpha", comment_id=target, reason=None, by="operator",
                  confirm=None, json=True, wait=0, status="pending")
    values.update(kw)
    return argparse.Namespace(**values)


def _run(capsys, action, target=REPLY, **kw):
    rc = edit_cli.cmd_reply(_args(action, target, **kw))
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def _reply_route(fake, *, status="NOT_HUSHED", parent=ROOT, is_reply=True):
    fake.on("GET", f"/v1.0/{REPLY}", body={
        "id": REPLY, "text": "荒れた返信", "username": "someone", "timestamp": "2026-11-06T01:00:00+0000",
        "is_reply": is_reply, "replied_to": {"id": parent}, "root_post": {"id": ROOT},
        "hide_status": status, "is_reply_owned_by_me": False})
    fake.on("POST", f"/v1.0/{REPLY}/manage_reply", body={"success": True})
    fake.on("POST", f"/v1.0/{REPLY}/manage_pending_reply", body={"success": True})


def _writes(fake, before=0):
    return [c for c in fake.calls[before:] if c["method"] != "GET"]


def _form(call):
    return urllib.parse.parse_qs(call["body"])


def _state_text():
    root = Path(accounts.state_dir_for("alpha"))
    return "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())


def _runs(action):
    rows = []
    for p in Path(accounts.state_dir_for("alpha")).glob("runs-*.ndjson"):
        rows += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("action") == action]


# ================================================================== 1. 隠す・戻す

def test_threadsの返信もreply_hideとunhideで隠し戻せ_記録に本文を残さない(make, fake, capsys):
    make("threads", token=_token())
    _reply_route(fake)
    rc, first = _run(capsys, "hide")
    assert rc == 0 and first["changed"] is True
    _reply_route(fake, status="HIDDEN")
    rc, _ = _run(capsys, "unhide")
    assert rc == 0
    writes = _writes(fake)
    assert [(c["path"], _form(c)["hide"]) for c in writes] == [
        (f"/v1.0/{REPLY}/manage_reply", ["true"]), (f"/v1.0/{REPLY}/manage_reply", ["false"])]
    assert all(_form(c)["access_token"] == [SECRET] for c in writes)        # 鍵は本文で送る（URL に載せない）
    rows = [json.loads(line) for line in Path(first["record"]).read_text().splitlines()]
    assert [(r["action"], r["result"], r["medium"]) for r in rows] == [
        ("hide", "confirmed", "threads"), ("unhide", "confirmed", "threads")]
    assert "荒れた返信" not in _state_text() and "someone" not in _state_text()
    used = _runs("reply_moderate")
    assert [(r["status"], r["permission"]) for r in used] == [("ok", "threads_manage_replies")] * 2


def test_既に隠れている返信は隠し直さない(make, fake, capsys):
    make("threads", token=_token())
    _reply_route(fake, status="HIDDEN")
    rc, payload = _run(capsys, "hide")
    assert rc == 0 and payload["changed"] is False and _writes(fake) == []


# ================================================================== 2. 隠せないもの・消せないもの

def test_返信への返信と投稿は隠さず断る(make, fake, capsys):
    make("threads", token=_token())
    _reply_route(fake, parent="17900000000000999")
    rc, payload = _run(capsys, "hide")
    assert rc == 1 and "返信への返信" in payload["error"]
    _reply_route(fake, is_reply=False)
    rc, payload = _run(capsys, "hide")
    assert rc == 1 and "投稿" in payload["error"]
    assert _writes(fake) == []


def test_threadsのdeleteは要求を1本も出さずに前向きに断る(make, fake, capsys):
    make("threads", token=_token())
    _reply_route(fake)
    rc, payload = _run(capsys, "delete", reason="荒らし")
    assert rc == 2 and payload["failure"] == "unsupported_on_platform"
    assert "thth reply hide alpha" in payload["error"]                 # 通る直し方を言う
    assert fake.calls == []


def test_productionが無い_権限が無いなら書かない(make, fake, capsys):
    make("threads", token=_token(), production=False)
    _reply_route(fake)
    rc, payload = _run(capsys, "hide")
    assert rc == 1 and "production: true" in payload["error"]
    rc, payload = _run(capsys, "approve")
    assert rc == 1 and "production: true" in payload["error"]
    assert _writes(fake) == []


def test_manage_repliesが乗っていなければ書かずにrc2(make, fake, capsys):
    make("threads", token=_token(["threads_basic", "threads_read_replies"]))
    _reply_route(fake)
    for action in ("hide", "approve", "ignore"):
        rc, payload = _run(capsys, action)
        assert rc == 2 and "threads_manage_replies" in payload["error"]
    assert _writes(fake) == []


def test_書く口は形の違うidを投げずに断る():
    a = threads_mod.ThreadsAdapter(base_url="http://127.0.0.1:9", access_token="x", user_id="1")
    for call in (lambda: a.hide_reply("../me"), lambda: a.hide_reply("1?x=1"),
                 lambda: a.manage_pending_reply("1/../2", approve=True),
                 lambda: a.pending_replies("abc"), lambda: a.pending_replies("1", status="PENDING")):
        try:
            call()
        except base.AdapterError:
            continue
        raise AssertionError("断らなかった")


# ================================================================== 3. 承認待ちの一覧

def _pending_route(fake):
    fake.on("GET", f"/v1.0/{ROOT}/pending_replies", body={"data": [
        {"id": "17900000000000002", "text": "承認を待つ返信", "username": "fan", "timestamp": "2026-11-06T02:00:00+0000",
         "reply_approval_status": "pending", "hide_status": "NOT_HUSHED", "media_type": "TEXT_POST"}]})


def test_pendingは承認待ちを読むだけで本文を残さない(make, fake, capsys):
    make("threads", token=_token())
    _pending_route(fake)
    rc, payload = _run(capsys, "pending", ROOT, by=None)
    assert rc == 0 and payload["count"] == 1
    assert payload["replies"][0]["reply_id"] == "17900000000000002"
    assert payload["replies"][0]["approval_status"] == "pending"
    assert payload["replies"][0]["text"] == "承認を待つ返信"            # 画面と --json には出す
    (call,) = fake.calls
    assert call["method"] == "GET" and call["query"]["approval_status"] == ["pending"]
    assert call["query"]["reverse"] == ["true"]
    assert "reply_approval_status" in call["query"]["fields"][0]
    assert "承認を待つ返信" not in _state_text() and "fan" not in _state_text()   # どこにも残さない
    assert [(r["status"], r["permission"], r["mode"]) for r in _runs("reply_pending")] == [
        ("ok", "threads_read_replies", "read")]


def test_pendingのallは絞りを付けない_productionが無くても読める(make, fake, capsys):
    make("threads", token=_token(), production=False)
    _pending_route(fake)
    rc, payload = _run(capsys, "pending", ROOT, status="all")
    assert rc == 0 and payload["status"] == "all"
    assert "approval_status" not in fake.calls[0]["query"]


def test_pendingは数字でない投稿のidを断る(make, fake, capsys):
    make("threads", token=_token())
    rc, payload = _run(capsys, "pending", "https://threads.net/x")
    assert rc == 1 and "post_id" in payload["error"] and fake.calls == []


# ================================================================== 4. 承認・無視

def test_approveとignoreは1段で送り記録に1行ずつ残す(make, fake, capsys):
    make("threads", token=_token())
    _reply_route(fake)
    rc, first = _run(capsys, "approve")
    assert rc == 0 and first["changed"] is True
    rc, _ = _run(capsys, "ignore")
    assert rc == 0
    writes = _writes(fake)
    assert [(c["path"], _form(c)["approve"]) for c in writes] == [
        (f"/v1.0/{REPLY}/manage_pending_reply", ["true"]), (f"/v1.0/{REPLY}/manage_pending_reply", ["false"])]
    rows = [json.loads(line) for line in Path(first["record"]).read_text().splitlines()]
    assert [(r["action"], r["reply_id"], r["by"], r["result"]) for r in rows] == [
        ("approve", REPLY, "operator", "confirmed"), ("ignore", REPLY, "operator", "confirmed")]
    assert [r["permission"] for r in _runs("reply_approval")] == ["threads_manage_replies"] * 2


def test_approveは名乗りが無ければ送らない(make, fake, capsys, monkeypatch):
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    make("threads", token=_token())
    rc, payload = _run(capsys, "approve", by=None)
    assert rc == 1 and "--by" in payload["error"] and fake.calls == []


def test_successが無い応答は済んだと言わずunknownを残す(make, fake, capsys):
    make("threads", token=_token())
    fake.on("POST", f"/v1.0/{REPLY}/manage_pending_reply", body={})
    rc, payload = _run(capsys, "approve")
    assert rc == 1 and payload["result"] == "unknown"
    text = _state_text()
    assert '"result": "unknown"' in text and _runs("reply_approval") == []


def test_facebookやinstagramではpendingは未対応と言う(make, fake, capsys, isolated_account_factory, tmp_path):
    path = tmp_path / "fb.token"
    path.write_text(json.dumps({"access_token": "x", "page_id": "1"}))
    os.chmod(path, 0o600)
    isolated_account_factory("alpha", media="facebook", token=str(path), production=True)
    rc, payload = _run(capsys, "pending", ROOT)
    assert rc == 2 and "reply_approvals" in payload["error"]


# ================================================================== 5. 境界・見張り・遠くの道

def test_capabilityと権限の表と止めの柵():
    caps = threads_mod.ThreadsAdapter.capabilities()
    assert {"hide_reply", "reply_approvals"} <= caps and caps <= base.KNOWN_CAPABILITIES
    assert threads_mod.ThreadsAdapter.MODERATE_PERMISSIONS[0] == "threads_manage_replies"
    src = inspect.getsource(leave_gate.bind)
    for name in ("fetch_reply", "hide_reply", "pending_replies", "manage_pending_reply"):
        assert f"'{name}'" in src
    assert inspect.getsource(edit_cli).count(".manage_pending_reply(") == 1


def test_runsの行を権限の不使用の見張りが読む(make, fake, capsys):
    cfg = make("threads", token=_token())
    _reply_route(fake)
    _pending_route(fake)
    assert _run(capsys, "hide")[0] == 0
    assert _run(capsys, "pending", ROOT)[0] == 0
    row = permission_watch._account_row("alpha", cfg, jst.now_jst())
    assert row["found"].get("reply_moderate") is not None and row["found"].get("reply_pending") is not None
    assert "reply_moderate" in permission_watch.PERMISSIONS["threads_manage_replies"]["evidence"]
    assert "reply_pending" in permission_watch.PERMISSIONS["threads_read_replies"]["evidence"]


def test_CLIの形と遠くの道():
    args = cli.build_parser().parse_args(["reply", "pending", "alpha", ROOT, "--status", "ignored", "--json"])
    assert (args.action, args.comment_id, args.status) == ("pending", ROOT, "ignored")
    for action in ("pending", "approve", "ignore"):
        key = remote.command_key("reply", argparse.Namespace(action=action))
        assert key in remote.UNSUPPORTED and key not in remote.COMMANDS
    for action in ("hide", "unhide"):
        assert remote.command_key("reply", argparse.Namespace(action=action)) in remote.COMMANDS
