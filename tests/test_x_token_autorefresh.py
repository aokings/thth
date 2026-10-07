"""X のトークンは約 2 時間で切れる。読む前に期限の 5 分前を切っていたら更新する（不具合 r20261008-51544896）。"""
from __future__ import annotations

import datetime
import json

from thth import accounts, jst
from thth.adapters import auth_x


def _account(factory, tmp_path, minutes_left):
    a = factory(name="kx", repo_dir=str(tmp_path / "repos" / "_none"), media="x", handle="kx",
                token=str(tmp_path / "kx.token"))
    cfg = accounts.load_account(a["name"])
    obtained = jst.now_jst() - datetime.timedelta(seconds=7200) + datetime.timedelta(minutes=minutes_left)
    with open(cfg["token"], "w", encoding="utf-8") as f:
        json.dump({"access_token": "OLD", "refresh_token": "R", "expires_in": 7200,
                   "obtained_at": jst.iso(obtained)}, f)
    return a, cfg


def test_due_token_is_refreshed_before_it_is_read(isolated_account_factory, tmp_path, monkeypatch):
    a, cfg = _account(isolated_account_factory, tmp_path, minutes_left=2)
    calls = []

    def refresh(name, **kw):
        calls.append(name)
        with open(cfg["token"], "w", encoding="utf-8") as f:
            json.dump({"access_token": "NEW", "refresh_token": "R2", "expires_in": 7200,
                       "obtained_at": jst.iso(jst.now_jst())}, f)
        return 0
    monkeypatch.setattr(auth_x, "run_refresh", refresh)
    assert accounts.load_token(accounts.load_account(a["name"]))["access_token"] == "NEW"
    assert calls == ["kx"]


def test_fresh_token_is_not_refreshed(isolated_account_factory, tmp_path, monkeypatch):
    a, _cfg = _account(isolated_account_factory, tmp_path, minutes_left=60)
    monkeypatch.setattr(auth_x, "run_refresh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("更新した")))
    assert accounts.load_token(accounts.load_account(a["name"]))["access_token"] == "OLD"


def test_failed_refresh_returns_the_old_token(isolated_account_factory, tmp_path, monkeypatch):
    a, _cfg = _account(isolated_account_factory, tmp_path, minutes_left=-5)
    monkeypatch.setattr(auth_x, "run_refresh", lambda *a, **k: 2)
    assert accounts.load_token(accounts.load_account(a["name"]))["access_token"] == "OLD"
