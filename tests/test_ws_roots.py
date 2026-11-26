"""括りの段 0 の柵: 置き場の根は「括りの根」と「共通の根」の 2 つの口からだけ引く。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §2.1・§3 の段 0・§6.2（根の引き方の柵）。

- `accounts.thth_root()` を accounts.py の外で呼ばない（どちらの根か決めずに引くのを止める）。
- `common_root()` を使ってよいのは、下の表の物だけ。共通の根は括りをまたぐので、
  新しく使う所は、この表に足す（＝括りをまたいでよい理由を書く）まで赤になる。
- 何も書かなければ `ws_root()`（括りの中）を使う。それが漏れない側。
"""
from __future__ import annotations

import ast
import pathlib

from thth import accounts

PKG = pathlib.Path(__file__).resolve().parent.parent / "thth"

# 共通の根を使ってよい物と、その理由（設計 §2.1 の「共通」の並び）。
COMMON_ALLOWED = {
    "accounts": "state/_app.lock（版の入れ替えの印）・ws/ の置き場と、根の外の括りかの見分け",
    "account_cli": "根の外の括りかの見分けだけ（その括りの台帳にだけ書く）",
    "ws_admin": "括りの台帳 workspaces.json と ws/ を作る管理者の口（括りをまたぐのは管理者だけ）",
    "admin_log": "管理者の記録の置き場を作るだけ",
    "admin_notifications": "state/_admin（管理者への知らせ）",
    "admin_report": "app の env がサーバの置き場の中かの検査",
    "adapters/auth_mastodon": "置き場の根を作るだけ（app の登録は共通）",
    "appconfig": "app の設定は共通。置き場の中に置かない検査",
    "authclients": "app の秘密を置き場の中に置かない検査",
    "authflow": "置き場の根を作るだけ",
    "authpassword": "置き場の根を作るだけ",
    "budget_x": "state/_admin（X の読みの予算はアプリに 1 つ）",
    "budget_x_posts": "state/_admin（X の投稿の予算）",
    "incident": "全体の止めの設定",
    "media_observations": "state/_media（媒体ごとの観測。口座でなく媒体の物）",
    "oauth": "置き場の根を作るだけ",
    "permission_watch": "state/_meta（Meta アプリの権限の台帳）",
    "register/cli": "置き場の根を作るだけ（CI の新しい箱）",
    "report_isolation": "管理者の資格（private/）の根の照合",
    "report_service": "管理者の資格（private/）の根の照合",
    "server_ads": "出力から隠す道（広いほうの根）",
    "server_files": "サーバの置き場の外へ出ない検査（括りの根は共通の根の中）",
    "webhook": "state/_webhook（Meta の Webhook はアプリに 1 つ）",
    "worker_version": "worker の控え（版の印）",
}


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(PKG))[:-3]
        yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Attribute) and fn.attr == name) or (isinstance(fn, ast.Name) and fn.id == name):
                yield node.lineno


def test_thth_root_is_called_only_inside_accounts():
    bad = [f"{rel}:{line}" for rel, tree in _modules() if rel != "accounts"
           for line in _calls(tree, "thth_root")]
    assert not bad, f"thth_root() を直に呼んでいる（ws_root か common_root に）: {bad}"


def test_common_root_users_are_listed():
    users = {rel for rel, tree in _modules() if any(_calls(tree, "common_root"))}
    unlisted = sorted(users - set(COMMON_ALLOWED))
    assert not unlisted, f"共通の根を使う理由が表に無い（括りをまたぐ）: {unlisted}"
    stale = sorted(set(COMMON_ALLOWED) - users)
    assert not stale, f"もう共通の根を使っていないのに表に残っている: {stale}"


def test_ws_root_is_used_for_account_data():
    for rel in ("principals", "approval_ledger", "leave", "register/ledger", "shop/ledger", "ads/ledger", "wa/ledger"):
        tree = ast.parse((PKG / f"{rel}.py").read_text(encoding="utf-8"))
        assert any(_calls(tree, "ws_root")), rel
        assert not any(_calls(tree, "common_root")), rel


def test_stage0_both_roots_are_the_old_root(tmp_path, monkeypatch):
    monkeypatch.setenv("THTH_ROOT", str(tmp_path))
    assert accounts.ws_root() == accounts.common_root() == accounts.thth_root() == str(tmp_path)
    assert accounts.accounts_dir() == str(tmp_path / "accounts")
