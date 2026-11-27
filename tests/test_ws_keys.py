"""括りの段 2 の柵（VM の側）: 鍵は 1 つの括りにだけ結び付き、頼みは口座の括りの置き場で行う。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §4.5（括りの食い違いを断る所・権威は VM）。
- 括りに結び付いた鍵（レーンの鍵）は資格のファイルの外の括りの表にあり（§8 R1）、口座の括りと違えば
  「無い鍵」と同じ `invalid_key`。
- 資格のファイルの鍵（括りの欄は無い）は、口座の台帳のある括りで動く（今の本番と同じ）。
- 頼みの印（state/<口座>/requests）は口座の括りの置き場に書かれ、ほかの括りを開かない。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import make_account_json
from tests.test_ws_isolation import _ON, _SEEN, _under  # 監査の口を同じ形で使う

PERSON = "masaru"  # 対応表の main と garden の持ち主（ws_admin の既定）


def _key(n):
    return hashlib.sha256(f"key-{n}".encode()).hexdigest()


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.delenv("THTH_WS", raising=False)
    monkeypatch.delenv("THTH_ACCOUNTS_DIR", raising=False)
    import thth.accounts as accounts_mod
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("THTH_APP_DIR", str(tmp_path / "app"))
    (tmp_path / "repo-a").mkdir()
    (tmp_path / "repo-b").mkdir()
    (root / "accounts").mkdir()
    make_account_json(str(root / "accounts"), "alpha-main", repo_dir=str(tmp_path / "repo-a"), project="alphaproj")
    from thth import ws_admin
    ws_admin.add("garden", lead="gamma")
    garden = root / "ws" / "garden"
    (garden / "accounts").mkdir()
    make_account_json(str(garden / "accounts"), "gamma-garden", repo_dir=str(tmp_path / "repo-b"), project="gammaproj",
                      env="$THTH_ROOT/secrets/gamma-garden.env", token="$THTH_ROOT/secrets/gamma-garden.token")
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rows = [
        # 資格のファイルの鍵（括りの欄なし）
        {"sha256": _key(1), "expires_at": expires, "revoked": False, "accounts": {"gamma-garden": "gammaproj"},
         "scope": "user", "writes": True, "actor": PERSON},
        {"sha256": _key(4), "expires_at": expires, "revoked": False, "accounts": {"alpha-main": "alphaproj"},
         "scope": "user", "writes": True, "actor": PERSON},
    ]
    private = root / "private"
    private.mkdir(mode=0o700)
    path = private / "report-credentials.json"
    path.write_text(json.dumps({"schema_version": 1, "root": str(root), "credentials": rows}))
    path.chmod(0o600)
    # 括りに結び付いた鍵は括りの表に: 2 は garden、3 は main（garden の口座を名指しできない）。
    from thth import lane_keys
    for n, ws in ((2, "garden"), (3, "main")):
        lane_keys.change(ws, lambda keys, n=n, ws=ws: keys.append(
            {"sha256": _key(n), "expires_at": expires, "revoked": False, "actor": PERSON, "workspace": ws,
             "lane": "desk", "writes": True}) or True)
    return root, garden, str(path)


def _item(account, n, workspace=None):
    item = {"request_id": ("r" * 42) + str(n) + "." + account, "account": account, "operation": "doctor",
            "key_sha256": _key(n), "body": {"account": account}}
    if workspace is not None:
        item["workspace"] = workspace  # 括りの束から届いた頼み
    return item


def test_credentials_and_workspace_tables(world):
    from thth import report_http
    _, creds = report_http.load_credentials(__import__("pathlib").Path(world[2]))
    assert [c[3].workspace for c in creds] == [None, None, "garden", "main"]
    assert dict(creds[2][3].allowed_accounts) == {"gamma-garden": "gammaproj"}  # 括りの口座を引く


@pytest.mark.parametrize("n,account,ok", [(1, "gamma-garden", True), (2, "gamma-garden", True),
                                          (3, "gamma-garden", False), (3, "alpha-main", True),
                                          (4, "alpha-main", True)])
def test_context_checks_workspace(world, n, account, ok):
    from thth import accounts, api_requests
    with accounts.account_workspace(account):
        if ok:
            assert api_requests._context(PERSON, account, _key(n), world[2]).allowed_accounts
        else:
            with pytest.raises(api_requests.Refused, match="invalid_key"):
                api_requests._context(PERSON, account, _key(n), world[2])


def test_handle_runs_in_the_account_workspace(world, monkeypatch):
    root, garden, path = world
    from thth import accounts, api_requests
    seen_ws = []

    def fake_run(person, item, credentials_path):
        seen_ws.append(accounts.current_workspace())
        return {"ok": True}

    monkeypatch.setattr(api_requests, "_run", fake_run)
    _SEEN.clear()
    _ON[0] = True
    try:
        entry = api_requests.handle(PERSON, _item("gamma-garden", 2, "garden"), path)
    finally:
        _ON[0] = False
    assert entry["result"] == {"ok": True}
    assert seen_ws == ["garden"]
    assert list((garden / "state" / "gamma-garden" / "requests").glob("*.json"))
    assert not (root / "state" / "gamma-garden").exists()
    leaked = [p for p in _SEEN if _under(p, root / "accounts") or _under(p, root / "state")]
    assert not leaked, leaked[:5]
    # 送り直しの一覧も同じ括りの印を読む
    assert api_requests.undelivered(["gamma-garden"])[0]["request_id"].endswith(".gamma-garden")


def test_mismatched_key_is_refused_like_unknown(world):
    from thth import api_requests
    wrong = api_requests.handle(PERSON, _item("gamma-garden", 3, "main"), world[2])["result"]
    unknown = api_requests.handle(PERSON, {**_item("gamma-garden", 5), "key_sha256": "0" * 64}, world[2])["result"]
    right = api_requests.handle(PERSON, _item("gamma-garden", 2, "garden"), world[2])["result"]
    assert wrong == unknown == {"error": "invalid_key"}
    assert right != {"error": "invalid_key"}  # 括りの合う鍵は鍵の検めを通る（柵の空回りでない）
