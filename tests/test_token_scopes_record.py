"""`.token` に**認可の範囲（scope）とその出どころ**を残す（2026-09-14）。

運用の観測: VM の `.token` は Threads 4 本・Mastodon 1 本とも `scopes` が null
——`thth auth` / `token set` が認可の範囲を記録していなかった。

一次資料（**L2**）:
  - 長期トークン交換（`GET /access_token`）の応答は `access_token`・`token_type`・
    `expires_in` だけで **scope は入っていない**
    （https://developers.facebook.com/docs/threads/get-started/long-lived-tokens）。
  - `GET /v1.0/debug_token?access_token=<tester のユーザートークン>&input_token=<同じ>`
    の応答 `data.scopes` がトークンに乗っている権限の一覧
    （https://developers.facebook.com/docs/threads/troubleshooting/debug-access-token）。

固定するもの:
  - `thth auth` は `/debug_token` が言った一覧を `scopes_source: "response"` で書く
  - 訊けなければ要求した一覧（`DEFAULT_SCOPES`）を `"requested"` で書く——推測で埋めない
  - `thth token set` は `scopes: null`・`scopes_source: "unknown"`
  - `thth refresh` は `scopes`／`scopes_source` を壊さない
  - 既存の `.token`（`scopes_source` 無し・`scopes` null）は「不明」と読む

本物の API には一切触れない。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import doctor as doctor_mod
from thth import oauth as oauth_mod
from thth import scopes as scopes_mod

REDIRECT_URI = "https://nigamilab.example.test/"
FIXED_STATE = "FIXED-STATE-FOR-SCOPE-TESTS"


@pytest.fixture(autouse=True)
def 固定のstate(monkeypatch):
    monkeypatch.setattr(oauth_mod, "_new_state", lambda: FIXED_STATE)


def _戻り(code="ABC123"):
    return f"{REDIRECT_URI}?code={code}&state={FIXED_STATE}#_"


def _app_env(tmp_path, monkeypatch):
    path = tmp_path / "app.env"
    path.write_text("THREADS_APP_ID=1234567890\nTHREADS_APP_SECRET=APP-SECRET-01234\n",
                    encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))


def _account(isolated_account_factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    overrides.setdefault("redirect_uri", REDIRECT_URI)
    acc = isolated_account_factory(token=token_path, **overrides)
    acc["token_path"] = token_path
    return acc


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_authはdebug_tokenの一覧をresponseとして書く(tmp_path, monkeypatch,
                                             isolated_account_factory):
    _app_env(tmp_path, monkeypatch)
    acc = _account(isolated_account_factory, tmp_path)
    with fake_oauth_server({"debug": "ok"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines: list = []
        rc = oauth_mod.run_auth(acc["name"], code=_戻り(), log=lines.append)
    assert rc == 0, lines
    token = _read(acc["token_path"])
    assert token["scopes"] == ["threads_basic", "threads_content_publish"]
    assert token["scopes_source"] == oauth_mod.SCOPES_SOURCE_RESPONSE
    assert "LONG-SECRET-TOKEN" not in "\n".join(lines)


def test_authはdebug_tokenが無ければ要求した一覧をrequestedとして書く(
        tmp_path, monkeypatch, isolated_account_factory):
    _app_env(tmp_path, monkeypatch)
    acc = _account(isolated_account_factory, tmp_path)
    with fake_oauth_server() as base_url:               # debug は既定で 404
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(acc["name"], code=_戻り(), log=lambda _l: None)
    assert rc == 0
    token = _read(acc["token_path"])
    assert token["scopes"] == list(scopes_mod.DEFAULT_SCOPES)
    assert len(token["scopes"]) == 11
    assert token["scopes_source"] == oauth_mod.SCOPES_SOURCE_REQUESTED


def test_authはdebug_tokenが5xxでも認可を落とさずrequestedにする(
        tmp_path, monkeypatch, isolated_account_factory):
    _app_env(tmp_path, monkeypatch)
    acc = _account(isolated_account_factory, tmp_path)
    with fake_oauth_server({"debug": "5xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(acc["name"], code=_戻り(), log=lambda _l: None)
    assert rc == 0
    token = _read(acc["token_path"])
    assert token["scopes_source"] == oauth_mod.SCOPES_SOURCE_REQUESTED


def test_debug_tokenの応答にscopesが無ければNone(monkeypatch):
    """嘘の一覧を作らない（形が違う応答は None）。"""
    monkeypatch.setattr(oauth_mod, "_get_json",
                        lambda url, params, timeout: {"data": {"is_valid": True}})
    assert oauth_mod.fetch_token_scopes("T") is None
    monkeypatch.setattr(oauth_mod, "_get_json",
                        lambda url, params, timeout: {"data": {"scopes": "threads_basic"}})
    assert oauth_mod.fetch_token_scopes("T") is None


def test_token_setはnullとunknownを書く(tmp_path, monkeypatch, isolated_account_factory):
    acc = _account(isolated_account_factory, tmp_path)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_token_set(acc["name"], input_func=lambda: "PASTED-TOKEN\n",
                                     log=lambda _l: None)
    assert rc == 0
    token = _read(acc["token_path"])
    assert token["scopes"] is None
    assert token["scopes_source"] == oauth_mod.SCOPES_SOURCE_UNKNOWN


def test_refreshはscopesとscopes_sourceを壊さない(tmp_path, monkeypatch,
                                          isolated_account_factory):
    acc = _account(isolated_account_factory, tmp_path)
    with open(acc["token_path"], "w", encoding="utf-8") as f:
        json.dump({"access_token": "OLD-SECRET", "obtained_at": "2026-07-01T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999", "username": "nigamilab",
                   "scopes": ["threads_basic"], "scopes_source": "response"}, f)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_refresh(acc["name"], force=True, log=lambda _l: None)
    assert rc == 0
    token = _read(acc["token_path"])
    assert token["access_token"] == "REFRESHED-SECRET-TOKEN"
    assert token["scopes"] == ["threads_basic"]
    assert token["scopes_source"] == "response"


def test_既存のtokenはnullを不明と読む():
    """`scopes_source` が無い（いまの VM の 5 本）を壊さず「不明」と扱う。"""
    rec = doctor_mod.recorded_scopes({"access_token": "x", "scopes": None})
    assert rec == {"count": None, "scopes": None, "source": "unknown"}
    assert "不明" in doctor_mod.recorded_scopes_line(rec)
    rec = doctor_mod.recorded_scopes({"scopes": list(scopes_mod.DEFAULT_SCOPES),
                                      "scopes_source": "requested"})
    assert doctor_mod.recorded_scopes_line(rec) == "記録上の scope: 11 個（source=requested）"
