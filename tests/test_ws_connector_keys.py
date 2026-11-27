"""段 K の VM の側の柵: コネクタ（リモート MCP）のレーンの鍵。

設計: docs/設計_THTHアカウント（括り）_共通の層_叩き台_2026-11-26.md §6.1。
- 同意の頁で「読むだけ」を選ぶと、Worker の頼みに `writes: false` が付き、VM は書けない鍵を作る（柵 2 の VM の層）。
- 取り消し（`lane_revoke`）は hash で頼まれ、VM はその持ち主の行だけを止める。ほかの持ち主の鍵は無い鍵と同じ。
- `thth logout --lane` は手元のファイルを消す前に、鍵そのものでサーバーに取り消しを頼む。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.test_ws_lane_login import LANE, OWNER, _key, _lane_rows, world  # noqa: F401  (fixture)

OPAQUE = "A" * 43


def _action(**extra):
    return {"id": OPAQUE, "kind": "lane_key", "workspace": None, "lane": "connector-claude-ai", "sha256": _key(70), **extra}


def test_read_only_action_makes_a_key_that_cannot_write(world):
    from thth import activity, lane_keys, server_passthrough
    from thth.report_service import ReportServiceError
    root, path = world
    got = activity.apply(OWNER, _action(writes=False), path)
    assert got == {"id": OPAQUE, "outcome": "done", "reason": None, "workspace": "garden", "account": "gamma-garden"}
    (row,) = _lane_rows(path, lane="connector-claude-ai")
    assert row["writes"] is False
    (context,) = [c for d, _e, _r, c in lane_keys.contexts(path) if d == _key(70)]
    assert context.writes is False and context.lane == "connector-claude-ai"
    with pytest.raises(ReportServiceError, match="writes_not_allowed"):
        server_passthrough.execute(context, {"operation": "ws_command", "account": "gamma-garden",
                                             "argv": ["withdraw", "--account", "gamma-garden", "queue/a.md"]})


def test_without_the_field_the_key_writes_as_in_stage_2(world):
    from thth import activity
    root, path = world
    assert activity.apply(OWNER, _action(), path)["outcome"] == "done"
    (row,) = _lane_rows(path, lane="connector-claude-ai")
    assert row["writes"] is True


@pytest.mark.parametrize("writes", [True, None, 0, "false"])
def test_writes_field_other_than_false_is_refused(world, writes):
    from thth import activity
    root, path = world
    got = activity.apply(OWNER, _action(writes=writes), path)
    assert got["outcome"] == "failed" and got["reason"] == "invalid_action"
    assert not _lane_rows(path, lane="connector-claude-ai")


def test_same_hash_with_other_writes_is_a_conflict(world):
    from thth import activity
    root, path = world
    activity.apply(OWNER, _action(writes=False), path)
    got = activity.apply(OWNER, _action(), path)
    assert got["outcome"] == "failed" and got["reason"] == "credential_conflict"


def _revoke(sha, workspace="garden"):
    return {"id": "B" * 43, "kind": "lane_revoke", "workspace": workspace, "sha256": sha}


def test_lane_revoke_stops_the_owner_key_and_is_idempotent(world):
    from thth import activity, api_requests
    root, path = world
    activity.issue_lane_key(path, OWNER, None, LANE, _key(71))
    assert [r["sha256"] for r in api_requests.workspace_key_table(OWNER, path)] == [_key(71)]
    assert activity.apply(OWNER, _revoke(_key(71)), path) == {"id": "B" * 43, "outcome": "done", "reason": None}
    assert [r["revoked"] for r in _lane_rows(path)] == [True]
    assert api_requests.workspace_key_table(OWNER, path) == []
    # 2 度来ても同じ（sync が落ちて同じ頼みが来る）。
    assert activity.apply(OWNER, _revoke(_key(71)), path)["outcome"] == "done"
    # 括りの名が無ければ全部の括りを探す。
    activity.issue_lane_key(path, OWNER, None, LANE, _key(72))
    assert activity.apply(OWNER, _revoke(_key(72), workspace=None), path)["outcome"] == "done"
    assert all(r["revoked"] for r in _lane_rows(path))


def test_lane_revoke_of_someone_elses_key_is_not_found(world):
    from thth import activity, lane_keys
    root, path = world
    # delta の括り orchard の鍵を、gamma が取り消そうとしても止まらない。
    got = activity.apply(OWNER, _revoke(_key("delta"), workspace="orchard"), path)
    assert got == {"id": "B" * 43, "outcome": "failed", "reason": "lane_key_not_found"}
    got = activity.apply(OWNER, _revoke(_key("delta"), workspace=None), path)
    assert got["reason"] == "lane_key_not_found"
    assert [r["revoked"] for r in lane_keys.read("orchard")] == [False]


@pytest.mark.parametrize("bad", [{"workspace": "Bad"}, {"sha256": "x"}, {"extra": 1}])
def test_lane_revoke_shape_is_closed(world, bad):
    from thth import activity
    root, path = world
    got = activity.apply(OWNER, {**_revoke(_key(73)), **bad}, path)
    assert got["outcome"] == "failed" and got["reason"] == "invalid_action"


# ---- Mac の側: thth logout --lane -------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("THTH_REMOTE_CONFIG", raising=False)
    monkeypatch.delenv("THTH_LANE", raising=False)
    lanes = tmp_path / ".config/thth/lanes"
    lanes.mkdir(parents=True)
    yield lanes
    os.environ.pop("THTH_LANE", None)


def _lane_file(lanes, **extra):
    path = lanes / "gamma-desk.json"
    path.write_text(json.dumps({"url": "https://thth.me", "key": "k" * 43, "lane": "gamma-desk", "workspace": "garden",
                                "account": "gamma-garden", **extra}))
    return path


def test_logout_lane_asks_the_server_with_the_key_itself(home, monkeypatch, capsys):
    from thth import cli, remote
    calls = []
    monkeypatch.setattr(remote, "_login_exchange", lambda *a: calls.append(a) or (202, {"status": "accepted"}))
    path = _lane_file(home)
    assert cli.main(["--lane", "gamma-desk", "logout"]) == 0
    assert not path.exists()
    ((method, target, headers, payload),) = calls
    assert (method, target, payload) == ("POST", "https://thth.me/api/v1/login/revoke", {"workspace": "garden"})
    assert headers == {"Authorization": "Bearer " + "k" * 43}
    out = capsys.readouterr()
    assert "取り消しを頼みました" in out.out and "k" * 43 not in out.out + out.err


@pytest.mark.parametrize("answer", [(None, None), (401, {"error": "invalid_key"}), (404, {"error": "not_found"})])
def test_logout_lane_removes_the_file_and_says_when_the_server_did_not_take_it(home, monkeypatch, capsys, answer):
    from thth import cli, remote
    monkeypatch.setattr(remote, "_login_exchange", lambda *a: answer)
    path = _lane_file(home)
    assert cli.main(["--lane", "gamma-desk", "logout"]) == 2
    assert not path.exists()
    assert "lane_revoke_unconfirmed" in capsys.readouterr().err


def test_logout_lane_without_workspace_does_not_ask(home, monkeypatch):
    from thth import cli, remote
    monkeypatch.setattr(remote, "_login_exchange", lambda *a: pytest.fail("must not ask"))
    path = home / "gamma-desk.json"
    path.write_text(json.dumps({"url": "https://thth.me", "key": "k" * 43, "lane": "gamma-desk"}))
    assert cli.main(["--lane", "gamma-desk", "logout"]) == 0
    assert not path.exists()
