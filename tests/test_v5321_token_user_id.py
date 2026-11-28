"""監査 5.32.0 の軽い 1・2（5.32.1）: 口座の token と台帳の user_id。

- 1: 断りの文が勧める命令（`thth account set <口座> user_id …`・`handle …`・`thth token revoke …`）が実際に通る。
  `account set` は handle と同じく、鍵が無いときだけ user_id を受ける。
- 2: 台帳の user_id が空なら、今ある鍵の user_id と比べる（店の「前の鍵が照合の元」と同じ）。置いたとき台帳の
  user_id が空なら書く。既存の口座（今の鍵と同じ口座の鍵）は新しく断られない。

本物の媒体には触れない（偽の adapter と偽の me だけ）。
"""
from __future__ import annotations

import json
import os
import re

import pytest

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import adapters, cli
from thth import oauth as oauth_mod

TOKEN = "V5321-PASTED-TOKEN-0001"


def _account(factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    overrides.setdefault("user_id", "")
    account = factory(token=token_path, **overrides)
    account["token_path"] = token_path
    account["ledger"] = os.path.join(account["accounts_dir"], account["name"] + ".json")
    return account


def _ledger(account):
    with open(account["ledger"], encoding="utf-8") as f:
        return json.load(f)


def _fake_me(monkeypatch, user_id, username):
    class Fake:
        def whoami(self):
            return {"user_id": user_id, "username": username}
    monkeypatch.setattr(adapters, "make_adapter", lambda cfg, tok: Fake())


def _set(account, *, force=True, value=TOKEN):
    lines = []
    rc = oauth_mod.run_token_set(account["name"], force=force, input_func=lambda: value,
                                 log=lines.append, by="test-operator")
    return rc, "\n".join(lines)


def _put_old_token(account, user_id, username="nigamilab"):
    with open(account["token_path"], "w", encoding="utf-8") as f:
        json.dump({"access_token": "OLD-TOKEN-V5321", "user_id": user_id, "username": username,
                   "obtained_at": "2026-11-01T00:00:00+09:00", "expires_in": 5184000}, f)
    os.chmod(account["token_path"], 0o600)


def _commands(text):
    """断りの文の（…）の中の `thth …` の命令を、打てる argv に（<名前> は試しの名前に）。"""
    found = []
    for m in re.finditer(r"thth ((?:token revoke|account set) [^（）。、]+?--by <名前>)", text):
        found.append(m.group(1).replace("<名前>", "test-operator").split())
    return found


# ---------------------------------------------------------------- 1: account set が user_id を受ける

def test_account_setはuser_idを鍵が無いときだけ受ける(capsys, tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, user_id="123")
    assert cli.main(["account", "set", account["name"], "user_id", "999999", "--by", "t"]) == 0
    assert _ledger(account)["user_id"] == "999999"
    _put_old_token(account, "999999")
    capsys.readouterr()
    rc = cli.main(["account", "set", account["name"], "user_id", "555", "--by", "t"])
    out, err = capsys.readouterr()
    assert rc != 0 and "user_id_locked_by_token" in out + err and "token revoke" in out + err
    assert _ledger(account)["user_id"] == "999999"
    assert cli.main(["account", "set", account["name"], "user_id", "bad id!", "--by", "t"]) != 0


def test_account_setのuser_idはハイフンで外せる(tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, user_id="123")
    assert cli.main(["account", "set", account["name"], "user_id", "-", "--by", "t"]) == 0
    assert _ledger(account)["user_id"] == ""


@pytest.mark.parametrize("with_token", [False, True])
def test_user_idの食い違いの断りが勧める命令はそのまま通る(capsys, tmp_path, monkeypatch, isolated_account_factory,
                                              with_token):
    account = _account(isolated_account_factory, tmp_path, user_id="123")
    if with_token:
        _put_old_token(account, "123")
    _fake_me(monkeypatch, "999999", "nigamilab")
    rc, out = _set(account)
    assert rc == 1 and out.startswith("token_account_mismatch") and TOKEN not in out
    commands = _commands(out)
    assert commands, out
    assert any(c[:2] == ["account", "set"] and "user_id" in c for c in commands)
    assert any(c[:2] == ["token", "revoke"] for c in commands) == with_token
    for argv in commands:
        assert cli.main(argv) == 0, (argv, capsys.readouterr())
    rc, out = _set(account)
    assert rc == 0, out
    assert _ledger(account)["user_id"] == "999999"


@pytest.mark.parametrize("with_token", [False, True])
def test_handleの食い違いの断りが勧める命令もそのまま通る(capsys, tmp_path, monkeypatch, isolated_account_factory,
                                            with_token):
    account = _account(isolated_account_factory, tmp_path, handle="nigamilab")
    if with_token:
        _put_old_token(account, "999999", "kopichaba")
    _fake_me(monkeypatch, "999999", "kopichaba")
    rc, out = _set(account)
    assert rc == 1 and out.startswith("token_account_mismatch")
    commands = _commands(out)
    assert any(c[:2] == ["account", "set"] and "handle" in c for c in commands), out
    assert any(c[:2] == ["token", "revoke"] for c in commands) == with_token
    for argv in commands:
        assert cli.main(argv) == 0, (argv, capsys.readouterr())
    assert _set(account)[0] == 0


def test_handleが空の断りが勧める命令も鍵があれば外す1手を添える(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, handle="")
    _put_old_token(account, "999999")
    _fake_me(monkeypatch, "999999", "nigamilab")
    rc, out = _set(account)
    assert rc == 1 and out.startswith("token_handle_missing")
    assert f"thth token revoke {account['name']}" in out


# ---------------------------------------------------------------- 2: 台帳が空なら今の鍵の番号と比べる・置いたら台帳に書く

def test_台帳のuser_idが空なら今の鍵の番号と比べ違えば置かない(tmp_path, monkeypatch, isolated_account_factory):
    """同じ handle を別の人が取り直した形: 名前は合うが番号が違う。--force でも置かない。"""
    account = _account(isolated_account_factory, tmp_path, handle="nigamilab")
    _put_old_token(account, "111111")
    before = open(account["token_path"], "rb").read()
    _fake_me(monkeypatch, "999999", "nigamilab")
    rc, out = _set(account)
    assert rc == 1 and out.startswith("token_account_mismatch")
    assert "111111" in out and "999999" in out and TOKEN not in out and "OLD-TOKEN-V5321" not in out
    assert open(account["token_path"], "rb").read() == before
    assert _ledger(account)["user_id"] == ""


def test_今の鍵と同じ口座の鍵は通り台帳に番号を書く(tmp_path, monkeypatch, isolated_account_factory):
    """既存の口座（台帳の user_id が空・今の鍵と同じ口座）は新しく断られない。"""
    account = _account(isolated_account_factory, tmp_path, handle="nigamilab")
    _put_old_token(account, "999999")
    _fake_me(monkeypatch, "999999", "nigamilab")
    rc, out = _set(account)
    assert rc == 0, out
    assert json.load(open(account["token_path"]))["access_token"] == TOKEN
    assert _ledger(account)["user_id"] == "999999"


@pytest.mark.parametrize("old", ["{broken", json.dumps({"access_token": "OLD-TOKEN-V5321"})])
def test_今の鍵が番号を持たなければhandleだけで通る(tmp_path, monkeypatch, isolated_account_factory, old):
    account = _account(isolated_account_factory, tmp_path, handle="nigamilab")
    with open(account["token_path"], "w") as f:
        f.write(old)
    os.chmod(account["token_path"], 0o600)
    _fake_me(monkeypatch, "999999", "nigamilab")
    assert _set(account)[0] == 0
    assert _ledger(account)["user_id"] == "999999"


@pytest.mark.parametrize("media,handle", [("facebook", "1469823909537300"), ("instagram", "17841437318552220")])
def test_本番の形のfbとigは番号の鍵で通り2回目も通る(tmp_path, monkeypatch, isolated_account_factory, media, handle):
    """fb・ig は whoami の username が番号（本番 11 本の形）。初めても入れ替えも通る。"""
    try:
        account = _account(isolated_account_factory, tmp_path, media=media, handle=handle)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"台帳を作れない: {exc}")
    _fake_me(monkeypatch, handle, handle)
    assert _set(account, force=False)[0] == 0
    assert _ledger(account)["user_id"] == handle
    assert _set(account, value=TOKEN + "-2")[0] == 0


def test_初めての鍵で台帳に番号を書き以後は別の番号を断る(tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, handle="nigamilab")
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        assert _set(account, force=False)[0] == 0
    assert _ledger(account)["user_id"] == "999999"
    _fake_me(monkeypatch, "222", "nigamilab")
    rc, out = _set(account)
    assert rc == 1 and "token_account_mismatch" in out


def test_台帳に番号を書くのは鍵と同じ取引で落ちれば鍵も戻す(tmp_path, monkeypatch, isolated_account_factory):
    from thth import admin_log
    account = _account(isolated_account_factory, tmp_path, handle="nigamilab")
    _put_old_token(account, "999999")
    before = open(account["token_path"], "rb").read()
    _fake_me(monkeypatch, "999999", "nigamilab")
    real = admin_log.append

    def boom(event, *a, **kw):
        if event == "token_set":
            raise admin_log.AdminLogError("down")
        return real(event, *a, **kw)
    monkeypatch.setattr(admin_log, "append", boom)
    try:
        rc = _set(account)[0]
    except Exception:  # noqa: BLE001 — 落ち方は問わない。置き場が戻っていること
        rc = "raised"
    assert rc != 0
    assert open(account["token_path"], "rb").read() == before
    assert _ledger(account)["user_id"] == ""
