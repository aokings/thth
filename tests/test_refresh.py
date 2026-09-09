"""`thth refresh`（発注 T2a）。50 日未満／50 日超／24 時間未満の 3 種 ＋ `--force`／
`--check` ＋ 秘密が出力に出ないこと。

本物の Threads API には触れない（`tests/helpers/fake_oauth_server.py`）。
"""
from __future__ import annotations

import datetime
import json
import os
import stat

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import jst
from thth import oauth as oauth_mod

OLD_TOKEN_VALUE = "OLD-SECRET-TOKEN-value"


def _write_token(path: str, *, obtained_at: str, expires_in: int = 5184000,
                  access_token: str = OLD_TOKEN_VALUE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "access_token": access_token,
            "obtained_at": obtained_at,
            "expires_in": expires_in,
            "user_id": "999999",
            "username": "nigamilab",
            "scopes": ["threads_basic"],
        }, f)
    os.chmod(path, 0o600)


def _account(isolated_account_factory, tmp_path, *, age_days=0.0, age_hours=None,
             expires_in=5184000):
    token_path = str(tmp_path / "account.token")
    now = jst.now_jst()
    if age_hours is not None:
        obtained_at_dt = now - datetime.timedelta(hours=age_hours)
    else:
        obtained_at_dt = now - datetime.timedelta(days=age_days)
    _write_token(token_path, obtained_at=jst.iso(obtained_at_dt), expires_in=expires_in)
    account = isolated_account_factory(token=token_path)
    account["token_path"] = token_path
    account["now"] = now
    return account


def test_1_50日未満なら何もしない(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=10)
    lines = []
    rc = oauth_mod.run_refresh(account["name"], log=lines.append, now=account["now"])

    assert rc == 0
    assert any("必要がありません" in line for line in lines)
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == OLD_TOKEN_VALUE  # 書き換わっていない


def test_2_50日超なら更新する(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_refresh(account["name"], log=lines.append, now=account["now"])

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "REFRESHED-SECRET-TOKEN"
    assert token["obtained_at"] == jst.iso(account["now"])
    mode = stat.S_IMODE(os.stat(account["token_path"]).st_mode)
    assert mode == 0o600


def test_3_24時間未満なら拒否する(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_hours=2)
    lines = []
    rc = oauth_mod.run_refresh(account["name"], log=lines.append, now=account["now"])

    assert rc == 0
    assert any("24 時間" in line or "24時間" in line for line in lines)
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == OLD_TOKEN_VALUE


def test_4_forceでも24時間未満は覆せない(tmp_path, isolated_account_factory):
    """--force は 50 日の運用しきい値を無視するだけで、Meta 側の 24 時間の
    ハード制約は覆さない（設計 §2.2 の「公式の条件」）。"""
    account = _account(isolated_account_factory, tmp_path, age_hours=2)
    lines = []
    rc = oauth_mod.run_refresh(account["name"], force=True, log=lines.append, now=account["now"])

    assert rc == 0
    assert any("24" in line for line in lines)
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == OLD_TOKEN_VALUE


def test_5_forceなら50日未満でも更新する(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=5)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_refresh(account["name"], force=True, log=lambda _l: None, now=account["now"])

    assert rc == 0
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == "REFRESHED-SECRET-TOKEN"


def test_6_checkはJSONを返し更新しない(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    lines = []
    rc = oauth_mod.run_refresh(account["name"], check=True, log=lines.append, now=account["now"])

    assert rc == 0
    payload = json.loads(lines[-1])
    assert payload["account"] == account["name"]
    assert payload["needs_refresh"] is True
    assert payload["can_refresh"] is True
    assert "remaining_days" in payload
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == OLD_TOKEN_VALUE  # --check は書き換えない


def test_7_更新が4xxならexit1でtokenを書き換えない(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    with fake_oauth_server({"refresh": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc = oauth_mod.run_refresh(account["name"], log=lambda _l: None, now=account["now"])

    assert rc == 1
    with open(account["token_path"], encoding="utf-8") as f:
        token = json.load(f)
    assert token["access_token"] == OLD_TOKEN_VALUE


def test_8_token無しはexit2(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "does-not-exist.token")
    account = isolated_account_factory(token=token_path)
    lines = []
    rc = oauth_mod.run_refresh(account["name"], log=lines.append)

    assert rc == 2
    assert not os.path.exists(token_path)


def test_20260909_refreshが標準出力に秘密を一切出さない(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, age_days=55)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        rc = oauth_mod.run_refresh(account["name"], log=lines.append, now=account["now"])

    assert rc == 0
    out = "\n".join(lines)
    assert OLD_TOKEN_VALUE not in out
    assert "REFRESHED-SECRET-TOKEN" not in out
