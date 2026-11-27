"""括りの段 2 の柵: 括りを選ぶ login（レーンの鍵）と、括りの束・`workspace_mismatch`。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §4.3・§4.5・§7.1・§8 R1・R3。
- VM は対応表の `owner_of(括り) == 持ち主` を確かめてから資格の行を作る。違えば作らない。
  名指しが無ければ、持ち主の括りが 1 つならそれ・2 つ以上なら作らない。
- レーンの鍵は口座ごとの鍵の表に載らず、括りの鍵の表に載る。括りの束からだけ通る。
- A の括りの鍵で B の括りの束に頼むと「無い鍵」と同じ断り。
- 通り道の答えには括りの名が付き、CLI は鍵のファイルの控えと違えば `workspace_mismatch`。
- 古い Worker（括りの形を断る）には 3.14.0 の形で話し、口座の鍵は止めない。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import make_account_json

OWNER = "gamma"
LANE = "gamma-desk"


def _key(n):
    return hashlib.sha256(f"lane-key-{n}".encode()).hexdigest()


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    for name in ("THTH_WS", "THTH_ACCOUNTS_DIR", "THTH_WS_MAP"):
        monkeypatch.delenv(name, raising=False)
    import thth.accounts as accounts_mod
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("THTH_APP_DIR", str(tmp_path / "app"))
    for repo in ("repo-a", "repo-b", "repo-c"):
        (tmp_path / repo).mkdir()
    (root / "accounts").mkdir()
    make_account_json(str(root / "accounts"), "alpha-main", repo_dir=str(tmp_path / "repo-a"), project="alphaproj")
    from thth import ws_admin
    ws_admin.add("garden", lead="gamma", owner=OWNER)
    ws_admin.add("orchard", lead="delta", owner="delta")
    for ws, account, repo, project in (("garden", "gamma-garden", "repo-b", "gammaproj"),
                                       ("orchard", "delta-orchard", "repo-c", "deltaproj")):
        base = root / "ws" / ws
        (base / "accounts").mkdir()
        make_account_json(str(base / "accounts"), account, repo_dir=str(tmp_path / repo), project=project,
                          env=f"$THTH_ROOT/secrets/{account}.env", token=f"$THTH_ROOT/secrets/{account}.token")
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rows = [
        # 口座の鍵（今の形）
        {"sha256": _key("acct"), "expires_at": expires, "revoked": False, "accounts": {"gamma-garden": "gammaproj"},
         "scope": "user", "writes": True, "actor": OWNER},
        # 持ち主 delta の括り orchard のレーンの鍵
        {"sha256": _key("delta"), "expires_at": expires, "revoked": False, "accounts": {"delta-orchard": "deltaproj"},
         "scope": "user", "writes": True, "actor": "delta", "workspace": "orchard", "lane": "delta-desk"},
    ]
    private = root / "private"
    private.mkdir(mode=0o700)
    path = private / "report-credentials.json"
    path.write_text(json.dumps({"schema_version": 1, "root": str(root), "credentials": rows}))
    path.chmod(0o600)
    from thth import activity
    activity._next_sync.clear()
    activity._legacy.clear()
    return root, str(path)


def _rows(path):
    return json.loads(open(path).read())["credentials"]


def _lane_rows(path, lane=LANE):
    return [row for row in _rows(path) if row.get("lane") == lane]


# ---- VM: 鍵を作る所（権威は対応表） -------------------------------------------------------------------

def test_owner_gets_a_lane_key_bound_to_the_workspace(world):
    from thth import activity
    root, path = world
    assert activity.issue_lane_key(path, OWNER, None, LANE, _key(1)) == ("garden", "gamma-garden")
    (row,) = _lane_rows(path)
    assert row == {**row, "sha256": _key(1), "actor": OWNER, "workspace": "garden", "lane": LANE, "scope": "user",
                   "writes": True, "revoked": False, "accounts": {"gamma-garden": "gammaproj"}}
    days = (datetime.fromisoformat(row["expires_at"]) - datetime.now(timezone.utc)).days
    assert 363 <= days <= 365
    # 同じ操作が 2 度来ても 1 本・同じレーンの次の鍵は前の鍵を取り消す。
    activity.issue_lane_key(path, OWNER, "garden", LANE, _key(1))
    assert len(_lane_rows(path)) == 1
    activity.issue_lane_key(path, OWNER, "garden", LANE, _key(2))
    assert [(r["sha256"], r["revoked"]) for r in _lane_rows(path)] == [(_key(1), True), (_key(2), False)]


@pytest.mark.parametrize("workspace", ["orchard", "nowhere", "main"])
def test_someone_elses_or_unknown_workspace_creates_nothing(world, workspace):
    from thth import activity
    root, path = world
    before = _rows(path)
    with pytest.raises(ValueError, match="workspace_not_owned"):
        activity.issue_lane_key(path, OWNER, workspace, LANE, _key(3))
    assert _rows(path) == before


def test_owner_of_two_workspaces_must_name_one(world):
    from thth import activity, ws_admin
    root, path = world
    ws_admin.add("meadow", lead="gamma2", owner=OWNER)
    base = root / "ws" / "meadow"
    (base / "accounts").mkdir()
    make_account_json(str(base / "accounts"), "gamma-meadow", repo_dir=str(root.parent / "repo-b"),
                      project="meadowproj", env="$THTH_ROOT/secrets/m.env", token="$THTH_ROOT/secrets/m.token")
    before = _rows(path)
    with pytest.raises(ValueError, match="workspace_required"):
        activity.issue_lane_key(path, OWNER, None, LANE, _key(4))
    assert _rows(path) == before
    assert activity.issue_lane_key(path, OWNER, "meadow", LANE, _key(4)) == ("meadow", "gamma-meadow")


def test_person_without_workspace_creates_nothing(world):
    from thth import activity
    root, path = world
    with pytest.raises(ValueError, match="workspace_not_owned"):
        activity.issue_lane_key(path, "stranger", None, LANE, _key(5))


def test_lane_key_action_reports_the_chosen_workspace(world):
    from thth import activity
    root, path = world
    action = {"id": "a" * 43, "kind": "lane_key", "workspace": None, "lane": LANE, "sha256": _key(6)}
    assert activity.apply(OWNER, action, path) == {"id": "a" * 43, "outcome": "done", "reason": None,
                                                   "workspace": "garden", "account": "gamma-garden"}
    refused = activity.apply("delta", {**action, "id": "b" * 43, "workspace": "garden", "sha256": _key(7)}, path)
    assert refused == {"id": "b" * 43, "outcome": "failed", "reason": "workspace_not_owned", "workspace": None,
                       "account": None}
    bad = activity.apply(OWNER, {**action, "id": "c" * 43, "lane": "../x"}, path)
    assert bad["reason"] == "invalid_action"


# ---- 鍵の表: レーンの鍵は括りの表だけ ------------------------------------------------------------------

def test_lane_keys_go_to_the_workspace_table_only(world, monkeypatch):
    from thth import activity, api_requests
    root, path = world
    activity.issue_lane_key(path, OWNER, None, LANE, _key(8))
    assert {row["sha256"] for row in api_requests.key_table(OWNER, path)} == {_key("acct")}
    table = api_requests.workspace_key_table(OWNER, path)
    assert [(row["sha256"], row["workspace"]) for row in table] == [(_key(8), "garden")]
    assert set(table[0]) == {"sha256", "workspace", "expires_at"}
    assert api_requests.lane_accounts(OWNER, path) == ["gamma-garden"]
    # 括りの持ち主が替われば、表に載せない（Worker の束から消える）。
    from thth import ws_admin, workspaces_map
    data = json.loads(open(ws_admin.map_path()).read())
    data["workspaces"]["garden"]["owner"] = "delta"
    workspaces_map.write(data, ws_admin.map_path())
    assert api_requests.workspace_key_table(OWNER, path) == []
    # レーンの鍵は口座の持ち主を決める鍵ではない（/activity の要約を広げない）。
    assert "delta-orchard" not in activity.owners(path).get("delta", {})


def _item(account, key, n, workspace=None):
    item = {"request_id": ("q" * 42) + str(n) + "." + account, "account": account, "operation": "lane_status",
            "key_sha256": key, "body": {"account": account}}
    if workspace is not None:
        item["workspace"] = workspace
    return item


def test_bundle_and_key_must_match(world):
    from thth import activity, api_requests
    root, path = world
    activity.issue_lane_key(path, OWNER, None, LANE, _key(9))
    ok = api_requests.handle(OWNER, _item("gamma-garden", _key(9), 1, "garden"), path)["result"]
    assert ok == {"workspace": "garden", "lane": LANE, "owner": OWNER, "writes": True, "accounts": 1}
    unknown = api_requests.handle(OWNER, _item("gamma-garden", "0" * 64, 2, "garden"), path)["result"]
    cases = [
        (OWNER, _item("gamma-garden", _key(9), 3)),                     # レーンの鍵を口座の束から
        (OWNER, _item("gamma-garden", _key(9), 4, "orchard")),          # A の括りの鍵で B の括りの束
        (OWNER, _item("gamma-garden", _key("acct"), 5, "garden")),      # 口座の鍵を括りの束から
        ("delta", _item("delta-orchard", _key("delta"), 6, "garden")),  # B の鍵で A の束
        ("delta", _item("gamma-garden", _key("delta"), 7, "orchard")),  # B の鍵で A の括りの口座
    ]
    for person, item in cases:
        assert api_requests.handle(person, item, path)["result"] == unknown == {"error": "invalid_key"}, item
    # 印は括りの束の名を持ち、送り直しも括りの束へ。
    assert api_requests.undelivered(["gamma-garden"])[0]["workspace"] == "garden"


def test_lane_key_stops_when_the_workspace_changes_owner(world):
    from thth import activity, api_requests, ws_admin, workspaces_map
    root, path = world
    activity.issue_lane_key(path, OWNER, None, LANE, _key(10))
    data = json.loads(open(ws_admin.map_path()).read())
    data["workspaces"]["garden"]["owner"] = "delta"
    workspaces_map.write(data, ws_admin.map_path())
    got = api_requests.handle(OWNER, _item("gamma-garden", _key(10), 8, "garden"), path)["result"]
    assert got == {"error": "invalid_key"}


# ---- sync の形（版ずれ） -------------------------------------------------------------------------------

class Worker:
    """Worker の sync の偽物。`accept` は受ける形の数（3 = 括りの形まで・2 = 3.14.0・1 = 3.13.0）。"""

    def __init__(self, accept=3, actions=()):
        self.accept, self.bodies, self.actions = accept, [], list(actions)

    def __call__(self, kind, subject, operation, body):
        body = json.loads(json.dumps(body))
        level = 3 if "workspace_keys" in body else 2 if "keys" in body else 1
        if level > self.accept or any(set(c) != {"id", "outcome", "reason"} for c in body["completed"]) \
                and self.accept < 3:
            from thth import relay
            raise relay.RelayError("relay_outcome_unknown", status=400)
        self.bodies.append(body)
        actions, self.actions = self.actions, []
        return {"status": "synced", "actions": actions, "requests": []}


def test_sync_carries_workspace_keys_and_the_new_key_in_the_same_round(world, monkeypatch):
    from thth import activity, relay
    root, path = world
    fake = Worker(actions=[{"id": "d" * 43, "kind": "lane_key", "workspace": None, "lane": LANE,
                            "sha256": _key(11)}])
    monkeypatch.setattr(relay, "signed_request", fake)
    completed, _ = activity._sync(OWNER, [], path)
    assert completed[0]["outcome"] == "done" and completed[0]["workspace"] == "garden"
    first, second = fake.bodies
    assert first["workspace_keys"] == [] and first["workspace_results"] == []
    assert [k["sha256"] for k in second["workspace_keys"]] == [_key(11)]
    assert second["completed"] == completed
    assert all(row["sha256"] != _key(11) for row in second["keys"])


def test_old_worker_gets_the_3_14_shape_and_account_keys_keep_working(world, monkeypatch):
    from thth import activity, relay
    root, path = world
    fake = Worker(accept=2, actions=[{"id": "e" * 43, "kind": "lane_key", "workspace": None, "lane": LANE,
                                      "sha256": _key(12)}])
    monkeypatch.setattr(relay, "signed_request", fake)
    activity._sync(OWNER, [], path)
    assert fake.bodies and all(set(body) == {"accounts", "completed", "keys", "results"} for body in fake.bodies)
    assert {k["sha256"] for k in fake.bodies[0]["keys"]} == {_key("acct")}
    assert all(set(c) == {"id", "outcome", "reason"} for body in fake.bodies for c in body["completed"])
    count = len(fake.bodies)
    activity._sync(OWNER, [], path)  # しばらくは 3.14.0 の形のまま（毎回 400 を踏まない）
    assert len(fake.bodies) == count + 1


def test_run_once_also_visits_workspace_owners(world, monkeypatch):
    from thth import activity, relay
    root, path = world
    seen = []
    monkeypatch.setattr(relay, "signed_request", lambda kind, subject, op, body: seen.append(subject) or
                        {"status": "synced", "actions": [], "requests": []})
    activity.run_once(path)
    assert {"gamma", "delta", "masaru"} <= set(seen)


# ---- 通り道の答えと CLI ----------------------------------------------------------------------------------

def test_passthrough_answer_names_the_workspace(world, monkeypatch):
    from thth import server_passthrough as sp
    from thth.report_service import ReportContext

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: Done())
    ctx = ReportContext({"gamma-garden": "gammaproj"}, workspace="garden", lane=LANE)
    out = sp.execute(ctx, {"operation": "ws_command", "account": "gamma-garden", "argv": ["board"]})
    assert out["workspace"] == "garden"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("THTH_REMOTE_CONFIG", raising=False)
    monkeypatch.delenv("THTH_LANE", raising=False)
    yield tmp_path / "home"
    os.environ.pop("THTH_LANE", None)


def _lane_file(home, workspace="garden"):
    lanes = home / ".config/thth/lanes"
    lanes.mkdir(parents=True, exist_ok=True)
    (lanes / (LANE + ".json")).write_text(json.dumps({"url": "https://thth.me", "key": "k" * 24,
                                                      "account": "gamma-garden", "lane": LANE,
                                                      "workspace": workspace}))


def test_cli_sends_lane_requests_to_the_workspace_bundle(home, monkeypatch):
    from thth import remote
    _lane_file(home)
    monkeypatch.setenv("THTH_LANE", LANE)
    sent = []

    def fake_exchange(cfg, method, path, payload=None):
        sent.append(payload)
        return 200, {}, {"ok": True}

    monkeypatch.setattr(remote, "_exchange", fake_exchange)
    remote.call("posts", "gamma-garden")
    assert sent == [{"account": "gamma-garden", "workspace": "garden"}]
    assert remote.REQUEST_ID.fullmatch("A" * 43 + "~garden") and remote.REQUEST_ID.fullmatch("A" * 43 + ".acct")


@pytest.mark.parametrize("answered", ["orchard", None])
def test_cli_refuses_an_answer_from_another_workspace(home, monkeypatch, capsys, answered):
    from thth import cli, remote
    _lane_file(home)
    value = {"command": "board", "rc": 0, "stdout": "SECRET BOARD\n", "stderr": "", "truncated": False}
    if answered:
        value["workspace"] = answered
    monkeypatch.setattr(remote, "call", lambda *a, **k: value)
    assert cli.main(["--lane", LANE, "board"]) == 2
    out = capsys.readouterr()
    assert "workspace_mismatch" in out.err and "SECRET BOARD" not in out.out
    value["workspace"] = "garden"
    assert cli.main(["--lane", LANE, "board"]) == 0
    assert capsys.readouterr().out == "SECRET BOARD\n"


class Login:
    """ブラウザ式 login の Worker の偽物（start・poll）。"""

    def __init__(self, outcome):
        self.starts, self.outcome, self.taken = [], outcome, False

    def __call__(self, method, target, headers, payload=None):
        if target.endswith("/api/v1/login/start"):
            self.starts.append(payload)
            return 202, {"url": "https://thth.me/login/", "expires_at": "x"}
        if not self.taken:
            self.taken = True
            return 200, {"key": "L" * 43, "owner": OWNER, "lane": LANE, "workspace": None}
        return 200, self.outcome


def _login(monkeypatch, home, outcome, answer=None, argv=("login", "--lane", LANE, "--no-browser")):
    from thth import cli, remote
    fake = Login(outcome)
    monkeypatch.setattr(remote, "_login_exchange", fake)
    monkeypatch.setattr(remote, "_sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(remote, "call", lambda op, account, **kw: calls.append((op, account)) or answer)
    return cli.main(list(argv)), fake, calls


def test_lane_login_saves_the_workspace_and_confirms(home, monkeypatch, capsys):
    done = {"outcome": "done", "reason": None, "workspace": "garden", "account": "gamma-garden"}
    rc, fake, calls = _login(monkeypatch, home, done, {"workspace": "garden", "lane": LANE, "owner": OWNER,
                                                       "writes": True, "accounts": 1})
    assert rc == 0, capsys.readouterr()
    assert fake.starts[0]["lane"] == LANE and "workspace" not in fake.starts[0]
    data = json.loads((home / ".config/thth/lanes" / (LANE + ".json")).read_text())
    assert (data["workspace"], data["account"], data["lane"], data["owner"]) == ("garden", "gamma-garden", LANE, OWNER)
    assert calls == [("lane_status", "gamma-garden")]
    assert "括り garden" in capsys.readouterr().out


@pytest.mark.parametrize("reason", ["workspace_not_owned", "workspace_required"])
def test_lane_login_says_why_the_key_was_not_made(home, monkeypatch, capsys, reason):
    rc, fake, calls = _login(monkeypatch, home, {"outcome": "failed", "reason": reason, "workspace": None,
                                                 "account": None})
    assert rc == 2 and calls == []
    err = capsys.readouterr().err
    assert err.startswith(reason) and "通すには:" in err and "もっと良く:" in err
    assert not (home / ".config/thth/lanes" / (LANE + ".json")).exists()


def test_lane_login_with_workspace_passes_it_and_catches_a_mismatch(home, monkeypatch, capsys):
    done = {"outcome": "done", "reason": None, "workspace": "garden", "account": "gamma-garden"}
    rc, fake, _ = _login(monkeypatch, home, done, {"workspace": "orchard"},
                         argv=("login", "--lane", LANE, "--workspace", "garden", "--no-browser"))
    assert fake.starts[0]["workspace"] == "garden"
    assert rc == 2 and "workspace_mismatch" in capsys.readouterr().err
    rc, *_ = _login(monkeypatch, home, done, None, argv=("login", "--lane", LANE, "--workspace", "meadow",
                                                         "--no-browser"))
    assert rc == 2 and "workspace_mismatch" in capsys.readouterr().err


def test_workspace_flag_needs_a_lane(home, monkeypatch, capsys):
    from thth import cli
    assert cli.main(["login", "--workspace", "garden", "--no-browser"]) == 2
    assert "workspace_name_invalid" in capsys.readouterr().err


def test_account_login_is_unchanged(home, monkeypatch, capsys):
    from thth import cli, remote
    seen = []

    def fake(method, target, headers, payload=None):
        if target.endswith("/start"):
            seen.append(payload)
            return 202, {"url": "u", "expires_at": "x"}
        return 200, {"key": "K" * 43, "account": "gamma-garden"}

    monkeypatch.setattr(remote, "_login_exchange", fake)
    monkeypatch.setattr(remote, "_sleep", lambda s: None)
    assert cli.main(["login", "--no-browser"]) == 0
    assert set(seen[0]) == {"code_hash", "poll_token_hash"}
    data = json.loads((home / ".config/thth/remote.json").read_text())
    assert data == {"url": "https://thth.me", "key": "K" * 43, "account": "gamma-garden"}
