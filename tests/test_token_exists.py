"""`thth.accounts.token_exists()` / `load_env()`（設計 §3.2・T3a 訂正 2026-09-09）。

`env_and_token_exist()` から名前と中身を変えた（env は任意になった）。ここでは
関数そのものを直接確かめる（`test_run_missing_env.py` は CLI 越しの確認）。
"""
from __future__ import annotations

from thth import accounts as accounts_mod


def test_tokenだけあれば真になる(tmp_path):
    token_path = tmp_path / "a.token"
    token_path.write_text('{"access_token": "x"}', encoding="utf-8")
    cfg = {"token": str(token_path), "env": str(tmp_path / "nonexistent.env")}
    assert accounts_mod.token_exists(cfg) is True


def test_tokenが無ければ偽(tmp_path):
    cfg = {"token": str(tmp_path / "nonexistent.token"), "env": str(tmp_path / "nonexistent.env")}
    assert accounts_mod.token_exists(cfg) is False


def test_envが無くてもtoken_existsは見ない(tmp_path):
    """`env_and_token_exist()` 時代は env も見ていたが、いまは token だけ。"""
    token_path = tmp_path / "a.token"
    token_path.write_text('{"access_token": "x"}', encoding="utf-8")
    cfg = {"token": str(token_path)}  # env キーすら無くても構わない
    assert accounts_mod.token_exists(cfg) is True


def test_load_envはファイルが無ければ空dict(tmp_path):
    cfg = {"env": str(tmp_path / "nonexistent.env")}
    assert accounts_mod.load_env(cfg) == {}


def test_load_envはkey_valueを読む(tmp_path):
    env_path = tmp_path / "a.env"
    env_path.write_text("HEALTHCHECK_URL=https://hc-ping.com/abc\n# comment\n\nX=1\n",
                         encoding="utf-8")
    cfg = {"env": str(env_path)}
    data = accounts_mod.load_env(cfg)
    assert data["HEALTHCHECK_URL"] == "https://hc-ping.com/abc"
    assert data["X"] == "1"
