"""括りの段 4 の準備: `thth maintain`（timer は引数無しで 1 日 1 回）が、main だけでなく全部の括りの口座の token を保つ。

- 括りが main だけなら `run_maintain()` そのもの（今の本番と同じ出力・終了コード）。
- 2 つ以上なら 1 つずつ入って回す。更新はその口座の括りの中で行う（garden の token を main で探さない）。
- JSON は 1 つにまとめ、各行に `workspace`。口座の無い括りは飛ばす。
"""
from __future__ import annotations

import json
import os

from tests.test_maintain import _write_token
from tests.test_ws_isolation import two_workspaces  # noqa: F401
from thth import accounts, jst
from thth import maintain as maintain_mod


def _tokens(root, garden, *, main_age, garden_age, now):
    from tests.conftest import make_account_json
    make_account_json(str(root / "accounts"), "alpha-main", repo_dir=str(root / "repo"), project="alphaproj",
                      token=_write_token(str(root / "secrets" / "alpha-main.token"), age_days=main_age, now=now))
    _write_token(str(garden / "secrets" / "gamma-garden.token"), age_days=garden_age, now=now)


def _refresh_spy(calls):
    def refresh(name, **kw):
        calls.append((name, accounts.current_workspace()))
        return 0
    return refresh


def test_every_workspace_is_maintained_inside_itself(two_workspaces, monkeypatch):  # noqa: F811
    root, garden = two_workspaces
    monkeypatch.delenv("THTH_WS", raising=False)
    now = jst.now_jst()
    _tokens(root, garden, main_age=55, garden_age=55, now=now)
    calls, lines = [], []
    maintain_mod.run_maintain_workspaces(log=lines.append, now=now, refresh=_refresh_spy(calls))
    assert sorted(calls) == [("alpha-main", "main"), ("gamma-garden", "garden")]
    assert "括り garden:" in lines and "括り main:" in lines


def test_json_is_one_document_with_workspace(two_workspaces, monkeypatch):  # noqa: F811
    root, garden = two_workspaces
    monkeypatch.delenv("THTH_WS", raising=False)
    now = jst.now_jst()
    _tokens(root, garden, main_age=10, garden_age=10, now=now)
    lines = []
    rc = maintain_mod.run_maintain_workspaces(check=True, as_json=True, log=lines.append, now=now)
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert {(r["account"], r["workspace"]) for r in data["accounts"]} == {("alpha-main", "main"),
                                                                         ("gamma-garden", "garden")}
    assert data["checked"] == 2 and data["workspaces"] == ["garden", "main"] and rc == 0


def test_main_only_is_the_same_as_before(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(accounts, "list_workspace_dirs", lambda: ["main"])
    monkeypatch.setattr(maintain_mod, "run_maintain", lambda *a, **k: calls.append((a, k)) or 7)
    assert maintain_mod.run_maintain_workspaces(check=True, log=print) == 7
    assert calls and calls[0][0] == (None,)


def test_empty_workspace_is_skipped(two_workspaces, monkeypatch):  # noqa: F811
    root, garden = two_workspaces
    monkeypatch.delenv("THTH_WS", raising=False)
    from thth import ws_admin
    ws_admin.add("empty", lead="nobody")
    (root / "ws" / "empty" / "accounts").mkdir(exist_ok=True)
    now = jst.now_jst()
    _tokens(root, garden, main_age=10, garden_age=10, now=now)
    lines = []
    assert maintain_mod.run_maintain_workspaces(check=True, log=lines.append, now=now) == 0
    assert "括り empty:" not in lines


def test_cli_without_account_walks_workspaces(two_workspaces, monkeypatch):  # noqa: F811
    from thth import cli
    monkeypatch.delenv("THTH_WS", raising=False)
    seen = []
    monkeypatch.setattr(maintain_mod, "run_maintain_workspaces", lambda **k: seen.append("all") or 0)
    monkeypatch.setattr(maintain_mod, "run_maintain", lambda *a, **k: seen.append(("one", a)) or 0)
    cli.main(["maintain", "--check"])
    monkeypatch.setenv("THTH_WS", "garden")
    cli.main(["maintain", "--check"])
    assert seen == ["all", ("one", (None,))]
