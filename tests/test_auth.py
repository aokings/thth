"""`thth auth`（発注 T2a）。偽 OAuth API に向けて、正常系・貼り付けの揺れ・
app.env 無し・交換の失敗・秘密が出力に出ないことを確認する。

本物の Threads API（graph.threads.net・threads.net）には一切触れない。
"""
from __future__ import annotations

import json
import os
import stat

from tests.conftest import run_thth
from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import accounts as accounts_mod
from thth import oauth as oauth_mod

REDIRECT_URI = "https://nigamilab.example.invalid/"
APP_SECRET_VALUE = "APP-SECRET-super-value-01234"
APP_ID_VALUE = "1234567890"


def _write_app_env(tmp_path, monkeypatch, *, app_id=APP_ID_VALUE, app_secret=APP_SECRET_VALUE):
    path = tmp_path / "app.env"
    path.write_text(f"THREADS_APP_ID={app_id}\nTHREADS_APP_SECRET={app_secret}\n", encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    return str(path)


def _account_with_token_path(isolated_account_factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    overrides.setdefault("redirect_uri", REDIRECT_URI)
    account = isolated_account_factory(token=token_path, **overrides)
    account["token_path"] = token_path
    return account


def test_1_正常系は短期長期めをたどってtokenを書く(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lines.append)

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "LONG-SECRET-TOKEN"
    assert token["user_id"] == "999999"
    assert token["username"] == "nigamilab"
    assert token["expires_in"] == 5184000
    assert "obtained_at" in token
    assert token["scopes"]  # 既定 scope が入っている

    mode = stat.S_IMODE(os.stat(account["token_path"]).st_mode)
    assert mode == 0o600

    out = "\n".join(lines)
    assert "999999" in out and "nigamilab" in out
    assert "LONG-SECRET-TOKEN" not in out
    assert "SHORT-SECRET-TOKEN" not in out
    assert APP_SECRET_VALUE not in out


def test_2_hash_underscore付きの貼り付けも通る(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code="ABC123#_", log=lambda _l: None)

    assert rc == 0
    assert os.path.exists(account["token_path"])


def test_3_url全体の貼り付けも通る(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)
    pasted = f"{REDIRECT_URI}?code=ABC123#_"

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code=pasted, log=lambda _l: None)

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "LONG-SECRET-TOKEN"


def test_4_app_env無しはexit2(tmp_path, monkeypatch, isolated_account_factory):
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    lines = []
    rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lines.append)

    assert rc == 2
    assert not os.path.exists(account["token_path"])
    assert any("app.env" in line for line in lines)


def test_5_交換が4xxならexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"short": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lines.append)

    assert rc == 1
    assert not os.path.exists(account["token_path"])


def test_6_長期交換が4xxならexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"long": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lambda _l: None)

    assert rc == 1
    assert not os.path.exists(account["token_path"])


def test_7_meが失敗してもexit1でtokenを作らない(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server({"me": "5xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lambda _l: None)

    assert rc == 1
    assert not os.path.exists(account["token_path"])


def test_8_redirect_uri無しはexit2(tmp_path, monkeypatch, isolated_account_factory):
    _write_app_env(tmp_path, monkeypatch)
    token_path = str(tmp_path / "account.token")
    account = isolated_account_factory(token=token_path)  # redirect_uri を渡さない

    rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lambda _l: None)

    assert rc == 2
    assert not os.path.exists(token_path)


def test_9_CLI経由でも動く_codeフラグ(tmp_path, monkeypatch, isolated_account_factory):
    """`bin/thth auth <account> --code ...` を subprocess で呼ぶ（対話無し）。"""
    _write_app_env(tmp_path, monkeypatch)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        env = {"THTH_THREADS_BASE_URL": base_url, "THTH_APP_ENV_PATH": os.environ["THTH_APP_ENV_PATH"]}
        result = run_thth(["auth", account["name"], "--code", "ABC123"], env=env)

    assert result.returncode == 0, result.stderr
    assert "LONG-SECRET-TOKEN" not in result.stdout
    assert "LONG-SECRET-TOKEN" not in result.stderr
    assert APP_SECRET_VALUE not in result.stdout
    assert os.path.exists(account["token_path"])


def test_10_app_envのパーミッションが600でなければ直す(tmp_path, monkeypatch, isolated_account_factory):
    path = tmp_path / "app.env"
    path.write_text(f"THREADS_APP_ID={APP_ID_VALUE}\nTHREADS_APP_SECRET={APP_SECRET_VALUE}\n",
                     encoding="utf-8")
    os.chmod(path, 0o644)  # わざと緩くしておく
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code="ABC123", log=lines.append)

    assert rc == 0
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert any("600" in line for line in lines)  # 警告が出ている


def test_20260909_authが標準出力に秘密を一切出さない(tmp_path, monkeypatch, isolated_account_factory, capsys):
    """事故防止テスト: token・app secret・code のいずれも標準出力・ログに出ないことを、
    実際に仕込んだ秘密文字列で確認する（設計 §3.6・masaru の指示）。"""
    secret_code = "SUPER-SECRET-CODE-VALUE-9999"
    _write_app_env(tmp_path, monkeypatch, app_secret=APP_SECRET_VALUE)
    account = _account_with_token_path(isolated_account_factory, tmp_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_auth(account["name"], code=secret_code, log=lines.append)

    assert rc == 0
    out = "\n".join(lines)
    for secret in (secret_code, APP_SECRET_VALUE, "LONG-SECRET-TOKEN", "SHORT-SECRET-TOKEN"):
        assert secret not in out, f"秘密が出力に出た: {secret}"

    # print() へも一切出していないこと（run_auth の既定 log=print 経路も確認する）。
    capsys.readouterr()  # 直前の出力を捨てる
    lines2 = []
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        token_path2 = str(tmp_path / "account2.token")
        account2 = isolated_account_factory("nigamilab-threads-2", token=token_path2,
                                             redirect_uri=REDIRECT_URI, repo_dir=account["repo_dir"])
        oauth_mod.run_auth(account2["name"], code=secret_code)  # log=print(既定)
    captured = capsys.readouterr()
    for secret in (secret_code, APP_SECRET_VALUE, "LONG-SECRET-TOKEN", "SHORT-SECRET-TOKEN"):
        assert secret not in captured.out
        assert secret not in captured.err
