"""`/me`（`whoami()`）が本人確認できる `id`・`username` を返さなければ保存しない
（発注 A-2・セキュリティ監査 2026-09-16・P2-1）。

**破れていたもの**: `thth/oauth.py::run_auth()` は `user_id = me.get("id","")`・
`username = me.get("username","")` で空を許していた。取り違え防止の照合は
`if handle and username and handle.lower() != username.lower():` なので、
`username` が空だと**照合そのものを飛ばして保存**する——`user_id` も
`username` も空のトークンが 600 で書かれる。`run_token_set()` 側も `user_id`
だけを見ていて、同じ穴があった。

ここで固定するもの:
  - `/me` が `{}`（id も username も無い）→ rc=1・**既存の `.token` に 1 バイトも
    触らない**
  - `/me` が `{"id": "1"}` だけ（username 無し）→ 同じく rc=1・既存の `.token`
    は不変
  - `/me` が `{"id": "1", "username": "x"}` → 従来どおり保存する

本物の Threads API・Mastodon インスタンスには一切触れない。
"""
from __future__ import annotations

import json
import os

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import oauth as oauth_mod
from thth.adapters import threads as threads_mod

REDIRECT_URI = "https://nigamilab.example.test/"
FIXED_STATE = "FIXED-STATE-FOR-IDENTITY-TESTS"
EXISTING_TOKEN_CONTENT = '{"access_token": "EXISTING-DO-NOT-TOUCH", "user_id": "1"}'


def _write_app_env(tmp_path, monkeypatch):
    path = tmp_path / "app.env"
    path.write_text("THREADS_APP_ID=1234567890\nTHREADS_APP_SECRET=APP-SECRET-value\n",
                     encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))


def _account_with_existing_token(isolated_account_factory, tmp_path, **overrides):
    token_path = tmp_path / "account.token"
    token_path.write_text(EXISTING_TOKEN_CONTENT, encoding="utf-8")
    overrides.setdefault("redirect_uri", REDIRECT_URI)
    account = isolated_account_factory(token=str(token_path), **overrides)
    account["token_path"] = str(token_path)
    return account


def _戻り(code: str = "ABC123", *, state: str = FIXED_STATE, suffix: str = "#_") -> str:
    return f"{REDIRECT_URI}?code={code}&state={state}{suffix}"


# ==================================================== run_auth（本体）

def test_meが空dictならrc1で既存tokenは不変(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_existing_token(isolated_account_factory, tmp_path)
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: FIXED_STATE)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        monkeypatch.setattr(oauth_mod, "fetch_me", lambda *a, **k: {})
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=_戻り(), log=lines.append)

    assert rc == 1
    out = "\n".join(lines)
    assert "本人確認ができない" in out
    assert "id・username を返しませんでした" in out or "id・username" in out
    with open(account["token_path"], encoding="utf-8") as f:
        assert f.read() == EXISTING_TOKEN_CONTENT, "既存の .token が 1 バイトでも変わった"


def test_usernameだけ無ければrc1で既存tokenは不変(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_existing_token(isolated_account_factory, tmp_path)
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: FIXED_STATE)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        monkeypatch.setattr(oauth_mod, "fetch_me", lambda *a, **k: {"id": "1"})
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=_戻り(), log=lines.append)

    assert rc == 1
    assert "本人確認ができない" in "\n".join(lines)
    with open(account["token_path"], encoding="utf-8") as f:
        assert f.read() == EXISTING_TOKEN_CONTENT


def test_idもusernameも有れば従来どおり保存する(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    # 台帳の既定 handle（"nigamilab"）と食い違うと**別の検査**（取り違え防止）で
    # rc=1 になる。ここで見たいのは本人確認の検査だけなので handle を合わせる。
    account = _account_with_existing_token(isolated_account_factory, tmp_path, handle="x")
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: FIXED_STATE)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        monkeypatch.setattr(oauth_mod, "fetch_me", lambda *a, **k: {"id": "1", "username": "x"})
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=_戻り(), log=lines.append)

    assert rc == 0, "\n".join(lines)
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["user_id"] == "1" and token["username"] == "x"


# ==================================================== run_token_set（同じ穴）

def test_token_setもusernameが無ければrc1で既存tokenは不変(tmp_path, isolated_account_factory):
    token_path = tmp_path / "account.token"
    token_path.write_text(EXISTING_TOKEN_CONTENT, encoding="utf-8")
    account = isolated_account_factory(token=str(token_path))

    orig_whoami = threads_mod.ThreadsAdapter.whoami
    threads_mod.ThreadsAdapter.whoami = lambda self: {"user_id": "1", "username": ""}
    try:
        lines = []
        # **`--force` を付ける**——既存 `.token` がある口は `force` 無しだと
        # 「既に token があります」で先に rc=1 になり、本人確認の検査を通らない。
        # ここで見たいのは本人確認の検査そのもの。
        rc = oauth_mod.run_token_set(account["name"], force=True,
                                     input_func=lambda: "PASTED-TOKEN", log=lines.append)
    finally:
        threads_mod.ThreadsAdapter.whoami = orig_whoami

    assert rc == 1
    with open(str(token_path), encoding="utf-8") as f:
        assert f.read() == EXISTING_TOKEN_CONTENT, "既存の .token が 1 バイトでも変わった"


def test_token_setはidとusernameが有れば従来どおり保存する(tmp_path, isolated_account_factory):
    token_path = tmp_path / "account.token"
    account = isolated_account_factory(token=str(token_path))

    orig_whoami = threads_mod.ThreadsAdapter.whoami
    threads_mod.ThreadsAdapter.whoami = lambda self: {"user_id": "1", "username": "nigamilab"}
    try:
        lines = []
        rc = oauth_mod.run_token_set(account["name"], input_func=lambda: "PASTED-TOKEN",
                                     log=lines.append)
    finally:
        threads_mod.ThreadsAdapter.whoami = orig_whoami

    assert rc == 0, "\n".join(lines)
    with open(str(token_path), encoding="utf-8") as f:
        token = json.load(f)
    assert token["user_id"] == "1" and token["username"] == "nigamilab"
