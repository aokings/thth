"""道ごとの意味の差の残り（10/15・docs/調べ_道ごとの意味の差_2026-10-15.md・調べ_JSONの欄 §5・§7）。

- 手元の `thth retract` にも安全装置（`guard.check(kind='retract', cli=True)`）。--already-gone は掛けない。
- help に thth.me の道での動きを 1 句ずつ。
- `account status` の答えに `publish_hold`（thth.me の道・MCP でも「止まっている」と言う）。
- thth.me の道の schedule: 過去の `--at` を断る・サーバは過去の時刻の 1 日の上限を今日で数える。
- 答えの別名（medium・permalink・usd_estimate・generated_at）。
- 小さな不具合: MCP の thth_settings を key だけで呼ぶ・Bluesky の since の比べ方・morning の未回答の permalink。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import pytest

from thth import accounts, guard, jst, retract_cli, sent as sent_mod
from thth.adapters import x as x_mod

POST_ID = "2107936529020805252"
NOW = datetime.datetime.now(jst.JST)


# --------------------------------------------------------------------------
# 1. 手元の retract の安全装置
# --------------------------------------------------------------------------

def _args(account, *, confirm=None, gone=False, post_id=POST_ID):
    return argparse.Namespace(account=account, post_id=post_id, reason="書き間違い",
                              by="masaru", confirm=confirm, json=True, wait=0, already_gone=gone)


def _setup(tmp_path, factory, monkeypatch, **cfg):
    a = factory(name="kx", repo_dir=str(tmp_path / "repos" / "_none"), media="x", handle="kx",
                production=True, **cfg)
    state = accounts.state_dir_for(a["name"])
    for i in range(4):
        sent_mod.write(state, post_id=f"{POST_ID}{i}", text=f"本文{i}", body_hash="h",
                       sent_at=jst.iso(NOW - datetime.timedelta(days=2)))
    monkeypatch.setattr(retract_cli, "_lookup_url", lambda *a, **k: None)
    deleted = []
    monkeypatch.setattr(x_mod.XAdapter, "has_token", classmethod(lambda cls, token: True))
    monkeypatch.setattr(x_mod.XAdapter, "missing_permissions", classmethod(lambda cls, token, need: []))
    monkeypatch.setattr(x_mod.XAdapter, "delete_post",
                        lambda self, pid: deleted.append(pid) or {"deleted_id": pid})
    return a, deleted


def _retract(name, post_id, capsys, *, gone=False):
    assert retract_cli.cmd_retract(_args(name, gone=gone, post_id=post_id)) == 1
    digest = json.loads(capsys.readouterr().out)["digest"]
    rc = retract_cli.cmd_retract(_args(name, confirm=digest, gone=gone, post_id=post_id))
    return rc, json.loads(capsys.readouterr().out)


def test_手元のretractも1日の削除の上限で断る(tmp_path, isolated_account_factory, monkeypatch, capsys):
    a, deleted = _setup(tmp_path, isolated_account_factory, monkeypatch, daily_max_retracts=2,
                        burst={"count": 10, "minutes": 10})
    assert _retract(a["name"], f"{POST_ID}0", capsys)[0] == 0
    assert _retract(a["name"], f"{POST_ID}1", capsys)[0] == 0
    rc, out = _retract(a["name"], f"{POST_ID}2", capsys)
    assert rc == 1 and out["ok"] is False and out["guard"] == "retract_limit"
    assert "取り下げません（安全装置）" in out["error"] and "daily_max_retracts" in out["error"]
    assert "次に消せるのは" in out["error"] and "thth account set" in out["error"]
    assert deleted == [f"{POST_ID}0", f"{POST_ID}1"]
    # 記録だけの道（DELETE を呼ばない）には掛けない。
    rc, out = _retract(a["name"], f"{POST_ID}3", capsys, gone=True)
    assert rc == 0 and out["deleted_id"] is None


def test_止まった口座の手元のretractは断る(tmp_path, isolated_account_factory, monkeypatch, capsys):
    a, deleted = _setup(tmp_path, isolated_account_factory, monkeypatch)
    state = accounts.state_dir_for(a["name"])
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o700)
    guard.stop(a["name"], "owner")
    rc, out = _retract(a["name"], f"{POST_ID}0", capsys)
    assert rc == 1 and out["guard"] == "account_stopped" and "resume" in out["error"]
    assert deleted == []


def test_止めの印を置けない置き場ではretractも止めずに1件断る(tmp_path, isolated_account_factory,
                                                     monkeypatch, capsys):
    a, deleted = _setup(tmp_path, isolated_account_factory, monkeypatch, daily_max_retracts=10,
                        burst={"count": 1, "minutes": 10})
    state = accounts.state_dir_for(a["name"])
    os.chmod(state, 0o775)
    assert not guard.stop_place_usable(a["name"])
    assert _retract(a["name"], f"{POST_ID}0", capsys)[0] == 0
    rc, out = _retract(a["name"], f"{POST_ID}1", capsys)
    assert rc == 1 and out["guard"] == "burst_limit" and "止めずに" in out["error"]
    assert not os.path.exists(os.path.join(state, "guard", "stopped.json"))
    assert deleted == [f"{POST_ID}0"]


def test_retractの答えにpermalinkも(tmp_path, isolated_account_factory, monkeypatch, capsys):
    a, _ = _setup(tmp_path, isolated_account_factory, monkeypatch)
    monkeypatch.setattr(retract_cli, "_lookup_url", lambda *a, **k: "https://x.com/kx/status/1")
    assert retract_cli.cmd_retract(_args(a["name"], post_id=f"{POST_ID}0")) == 1
    first = json.loads(capsys.readouterr().out)
    assert first["permalink"] == first["url"] == "https://x.com/kx/status/1"
    rc, done = _retract(a["name"], f"{POST_ID}0", capsys)
    assert rc == 0 and done["permalink"] == done["url"] == "https://x.com/kx/status/1"


def test_account_statusの掛かる所にretractも(isolated_account_factory, capsys):
    from thth import account_settings
    a = isolated_account_factory(production=True)
    account_settings.show_status(account_settings.status(a["name"]))
    out = capsys.readouterr().out
    assert "安全装置の掛かる所: thth send と thth retract（VM の CLI・thth.me・MCP）" in out
    assert "retract --already-gone" in out


# --------------------------------------------------------------------------
# 3. account status の publish_hold
# --------------------------------------------------------------------------

def test_account_statusの答えにpublish_hold(isolated_account_factory, capsys):
    from thth import account_settings, publish_hold
    a = isolated_account_factory(media="x", production=True)
    row = account_settings.status(a["name"])
    assert row["publish_hold"] is None
    state = accounts.state_dir_for(a["name"])
    os.makedirs(state, exist_ok=True)
    os.chmod(state, 0o700)
    publish_hold.hold(a["name"], "x_not_permitted", file="draft.md")
    row = account_settings.status(a["name"])
    assert row["publish_hold"]["reason"] == "x_not_permitted"
    assert "resume" in row["publish_hold"]["message"]
    # thth.me の道（`--json` の形を受けて表示する）でも「動いています」と言わない。
    account_settings.show_status(json.loads(json.dumps(row)))
    out = capsys.readouterr().out
    assert "自動の投稿を止めています" in out and "動いています" not in out


# --------------------------------------------------------------------------
# 2・4. help と thth.me の道の schedule
# --------------------------------------------------------------------------

def _help(*argv):
    from thth import cli
    parser = cli.build_parser()
    if not argv:
        return parser.format_help()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices[argv[0]].format_help()


def test_helpはthth_meの道の動きを言う():
    send = " ".join(_help("send").split())
    assert "thth.me の道: 台帳が production: true でなければ production_disabled" in send
    assert "thth.me の道: 使えない（digest はサーバがその場で突き合わせる" in send
    assert "thth.me の道: 手元の機械のファイル" in send
    retract = " ".join(_help("retract").split())
    assert "thth.me の道では使えない" in retract and "鍵の持ち主が記録される" in retract
    top = " ".join(_help().split())
    assert "thth.me の道: --text と --at か --draft に --production を付けると予約する" in top
    assert "thth.me の道も二段" in top


def _remote_home(tmp_path, monkeypatch):
    from tests.test_v3140_remote_cli import FakeWorker, KEY, _echo
    from thth import remote
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    config = tmp_path / "cfg" / "thth" / "remote.json"
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote, "_sleep", lambda seconds: None)
    fake = FakeWorker()
    fake.default = _echo
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"url": fake.url, "key": KEY}))
    os.chmod(config, 0o600)
    return fake


def test_thth_meの道のscheduleは過去のatを断る(tmp_path, monkeypatch, capsys):
    from thth import cli
    fake = _remote_home(tmp_path, monkeypatch)
    try:
        past = jst.iso(jst.now_jst() - datetime.timedelta(days=365))
        rc = cli.main(["schedule", "inv-demo-abc", "--text", "後で出す", "--at", past])
        out, err = capsys.readouterr()
        assert rc == 2 and "過去" in (out + err)
        assert fake.calls == []
        future = jst.iso(jst.now_jst() + datetime.timedelta(days=1))
        fake.push(200, {"draft_id": "d" * 64, "account": "inv-demo-abc", "status": "draft"})
        fake.push(200, {"account": "inv-demo-abc", "draft_id": "d" * 64, "status": "approved",
                        "publish_at": future, "via": "api"})
        assert cli.main(["schedule", "inv-demo-abc", "--text", "明日", "--at", future, "--production"]) == 0
        assert [c["path"] for c in fake.calls] == ["/api/v1/draft_put", "/api/v1/schedule_request"]
    finally:
        fake.close()


def test_サーバの予約は猶予の内の過去の時刻を今日として数える(monkeypatch):
    """猶予（数分）の内の過去の publish_at は timer の次の run で出る。1 日の上限は今日の分で数える。"""
    from thth import server_writes
    seen = {}

    def fake_guarded(context, account, kind, origin, **extra):
        seen.update(extra)
        raise RuntimeError("stop-here")
    monkeypatch.setattr(server_writes, "_guarded", fake_guarded)
    past = jst.iso(jst.now_jst() - datetime.timedelta(minutes=2))
    _drive_schedule(monkeypatch, server_writes, past)
    assert jst.to_jst(seen["publish_at"]).date() == jst.now_jst().date()


def test_サーバの予約は猶予より古い過去の時刻を断る(monkeypatch):
    """「後で出すつもりが今出る」を防ぐ（手元の CLI の --at と同じ線・MCP の --draft の道もここで止まる・10/16）。"""
    from thth import server_writes
    from thth.report_service import ReportServiceError
    monkeypatch.setattr(server_writes, "_guarded", lambda *a, **k: pytest.fail("ここまで来ない"))
    past = jst.iso(jst.now_jst() - datetime.timedelta(days=30))
    with pytest.raises(ReportServiceError) as caught:
        _drive_schedule(monkeypatch, server_writes, past)
    assert str(caught.value) == "invalid_draft" and caught.value.reason == "publish_at_past"
    assert "publish_at_past" in server_writes.DRAFT_REASONS


def test_サーバの予約は読めない時刻を今に倒さない(monkeypatch):
    from thth import server_writes
    from thth.report_service import ReportServiceError
    monkeypatch.setattr(server_writes, "_guarded", lambda *a, **k: pytest.fail("ここまで来ない"))
    with pytest.raises(ReportServiceError) as caught:
        _drive_schedule(monkeypatch, server_writes, "読めない")
    assert str(caught.value) == "invalid_draft" and caught.value.reason == "invalid_publish_at"


def _drive_schedule(monkeypatch, server_writes, publish_at):
    """`server_writes.schedule` を、時刻の扱いの所まで動かす（repo・資格は偽物）。"""
    import contextlib
    import types
    from thth import cli
    cfg = {"production": True, "scheduled": True, "media": "x", "repo_dir": "/nonexistent"}
    monkeypatch.setattr(server_writes, "current", lambda *a, **k: cfg)
    monkeypatch.setattr(server_writes, "_refuse_if_stopped", lambda account: None)
    monkeypatch.setattr(server_writes.server_files, "account_locks",
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(server_writes, "_sync", lambda cfg: None)
    q = types.SimpleNamespace(front_matter={"status": "draft"})
    monkeypatch.setattr(server_writes, "_draft", lambda *a: ("a.md", b"", q))
    monkeypatch.setattr(server_writes, "_queue", lambda cfg: (None, Path("/nonexistent")))
    monkeypatch.setattr(server_writes, "_rows", lambda *a: [])
    monkeypatch.setattr(cli, "_prepare_one", lambda path: ({"publish_at": publish_at}, None))
    context = types.SimpleNamespace(actor="tester", credential_digest="c" * 64)
    with contextlib.suppress(RuntimeError):
        server_writes.schedule(context, {"operation": "schedule_request", "account": "kx",
                                         "draft_id": "d" * 32}, "api")


# --------------------------------------------------------------------------
# 5. 答えの別名
# --------------------------------------------------------------------------

def test_費用の目安にusd_estimateも():
    from thth import list_cli, reaction_cli
    cost = list_cli._cost(1)
    assert cost["usd_estimate"] == cost["usd"]
    cost = reaction_cli._x_cost("x", x_mod.XAdapter, False)
    assert cost["usd_estimate"] == cost["usd"]


def test_budgetの答えにmediumも(isolated_account_factory):
    from thth import budget_x, budget_x_posts
    assert budget_x.report()["medium"] == budget_x.report()["media"] == "x"
    assert budget_x_posts.report()["medium"] == "x"


def test_account_jsonにmediumも(isolated_account_factory):
    from thth import account_report
    a = isolated_account_factory(media="mastodon")
    detail = account_report.account_detail(a["name"], remote=False)
    assert detail["medium"] == detail["media"] == "mastodon"


@pytest.mark.parametrize("module, needle", [
    ("community_cli", '"generated_at": jst.iso(now), "provenance"'),
    ("list_cli", '"generated_at": jst.iso(now), "provenance"'),
    ("trends_cli", '"generated_at": jst.iso(now), "provenance"'),
    ("where_cli", '"generated_at": jst.iso(now), "provenance"'),
    ("who_cli", '"generated_at": jst.iso(now)'),
    ("after_cli", '"generated_at": jst.iso(now)'),
    ("thread_read", '"generated_at": jst.iso(now)'),
])
def test_最上位にgenerated_at(module, needle):
    import importlib
    import inspect
    source = inspect.getsource(importlib.import_module("thth." + module))
    assert needle in source


def test_afterの答えにgenerated_at(isolated_account_factory):
    from thth import after_cli
    a = isolated_account_factory("one", handle="owner")
    value = after_cli.answer(a["name"])
    assert value["generated_at"] == value["provenance"]["updated"]


# --------------------------------------------------------------------------
# 7. 小さな不具合
# --------------------------------------------------------------------------

def _mcp():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "mcp"))
    try:
        import server
    finally:
        sys.path.pop(0)
    return server


def test_mcpのthth_settingsをkeyだけで呼んでも落ちない(monkeypatch):
    server = _mcp()
    monkeypatch.setattr(server, "run_cli", lambda *a, **k: pytest.fail("CLI を呼ばない"))
    for arguments in ({"account": "inv-demo-abc", "key": "daily_max_posts"},
                      {"account": "inv-demo-abc", "value": "3"}):
        result = server.remote_call("thth_settings", arguments)
        assert result["isError"] is True and result["content"][0]["text"] == "invalid_request"


def test_blueskyのsinceは時刻として比べる():
    from thth.adapters import bluesky
    # 2026-10-15T00:30Z は 09:30 JST。since 09:00 JST より後なので残る（文字列なら "00" < "09" で落ちた）。
    assert bluesky._not_before("2026-10-15T00:30:00.000Z", "2026-10-15T09:00:00+09:00")
    assert not bluesky._not_before("2026-10-14T23:30:00.000Z", "2026-10-15T09:00:00+09:00")
    # 読めなければ前と同じ文字列の比べ方。
    assert bluesky._not_before("b", "a")


def test_morningの未回答にpermalinkが入る(isolated_account_factory):
    from thth import morning
    now = jst.parse("2026-09-23T07:00:00+09:00")
    isolated_account_factory("one", handle="owner", collect_days=14)
    sent_mod.write(accounts.state_dir_for("one"), post_id="ROOT", text="本文", body_hash="hash",
                   sent_at=jst.iso(now - datetime.timedelta(days=3)))
    directory = Path(accounts.data_dirs(accounts.load_account("one"), "one")["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    rows = [{"id": "R1", "username": "outside", "text": "返信", "post_id": "ROOT",
             "timestamp": jst.iso(now - datetime.timedelta(days=1)),
             "permalink": "https://www.threads.net/@outside/post/R1"},
            {"kind": "fetch", "post_id": "ROOT", "collected_at": jst.iso(now)}]
    (directory / "ROOT.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))
    value = morning._unanswered_rows("one", now, None, accounts.load_account("one"))
    assert [row["permalink"] for row in value["items"]] == ["https://www.threads.net/@outside/post/R1"]
