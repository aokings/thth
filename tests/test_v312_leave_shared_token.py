"""3.1.2 件 6: 退出で token の値が他の生きている account と同じなら、遠隔で失効させずに終える。

実測（2026-09-23 12:50）: 同じ app で同じ利用者が認可すると Mastodon は既存の access token を
返すので、kopicha-server-mastodon と kopicha-mastodon の token は値が同じだった。失効すると
他方も死ぬので投げない（判定は正しい）——が、**退出が `worker_revoked` で止まるのは誤り**。
遠隔は `unconfirmed_shared` のまま、自分の台帳・state・env の削除まで完了する。

見るのは（fake Mastodon・loopback）:
  (a) 同じ値の 2 台帳 → completed・`remote: unconfirmed_shared`・`/oauth/revoke` は呼ばれない・
      他方の token file は byte 一致・他方は停止しない。
  (b) 値が違う 2 台帳 → 3.1.1 どおり自分の token だけ失効して completed。
  (c) 既に `worker_revoked` で止まった row（`token_shared: True`）からの再実行が (a) で完了。
"""
import json

import pytest

from thth import accounts, leave, leave_gate
from tests.test_v212_server_writes import env  # noqa: F401（fixture）
from tests.test_v212_leave_providers import provider, configure  # noqa: F401（fixture）
from tests.test_v311_leave_same_user import same_user_beta


def _assert_other_untouched(beta_token, beta_ledger, token_bytes, ledger_bytes):
    assert beta_token.read_bytes() == token_bytes and beta_ledger.read_bytes() == ledger_bytes
    assert not leave_gate.stopped("beta") and leave.read("beta") is None
    accounts.load_account("beta")


def test_a_同じ値の2台帳は失効を投げずに完了する(env, provider, monkeypatch, capsys):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    beta_token, beta_ledger = same_user_beta(env, provider, tokenpath, "mastodon", same_bytes=True)
    token_bytes, ledger_bytes = beta_token.read_bytes(), beta_ledger.read_bytes()
    alpha_ledger = env["root"] / "accounts/alpha.json"
    alpha_state = env["root"] / "state/alpha"

    class Args:
        name, by, json = "alpha", "operator", False
    assert leave.command(Args()) == 0
    out = capsys.readouterr().out
    assert "remote_unconfirmed: 他 account と共有の接続です。" in out
    row = leave.read("alpha")
    assert row["phase"] == "completed" and row["remote"] == "unconfirmed_shared"
    assert "token_shared" in row["preserved"]
    assert provider["calls"] == []            # /oauth/revoke は呼ばれない
    assert not alpha_ledger.exists() and not alpha_state.exists()
    _assert_other_untouched(beta_token, beta_ledger, token_bytes, ledger_bytes)


def test_b_値が違う2台帳は自分のtokenだけ失効して完了する(env, provider, monkeypatch):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    beta_token, beta_ledger = same_user_beta(env, provider, tokenpath, "mastodon")
    token_bytes, ledger_bytes = beta_token.read_bytes(), beta_ledger.read_bytes()
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed" and result["remote"] == "confirmed"
    sent = [form["token"][0] for path, form, _ in provider["calls"]]
    assert [path for path, _, _ in provider["calls"]] == ["/oauth/revoke"]
    assert sent == [provider["access"]] and not tokenpath.exists()
    _assert_other_untouched(beta_token, beta_ledger, token_bytes, ledger_bytes)


def test_c_worker_revokedで止まったrowから再実行して完了する(env, provider, monkeypatch):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    beta_token, beta_ledger = same_user_beta(env, provider, tokenpath, "mastodon", same_bytes=True)
    token_bytes, ledger_bytes = beta_token.read_bytes(), beta_ledger.read_bytes()
    # 3.1.1 の形で止まった row を作る: 停止と worker の失効までは進み、遠隔の段の手前で落ちる。
    original = leave_gate.credentials

    def broken(*args, **kwargs):
        raise ValueError("shared_credential_revoke_refused")
    monkeypatch.setattr(leave_gate, "credentials", broken)
    with pytest.raises(ValueError):
        leave.run("alpha", by="operator")
    stuck = leave.read("alpha")
    assert stuck["phase"] == "worker_revoked" and stuck["token_shared"] is True
    assert stuck["preserved"] == ["token_shared"] and leave_gate.stopped("alpha")
    monkeypatch.setattr(leave_gate, "credentials", original)
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed" and result["remote"] == "unconfirmed_shared"
    assert provider["calls"] == []
    _assert_other_untouched(beta_token, beta_ledger, token_bytes, ledger_bytes)


def test_共有なのに消す対象にtokenが残るrowは矛盾として止める(env, provider, monkeypatch):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    original = leave_gate.credentials
    monkeypatch.setattr(leave_gate, "credentials",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("stop_here")))
    with pytest.raises(ValueError):
        leave.run("alpha", by="operator")
    monkeypatch.setattr(leave_gate, "credentials", original)
    row = leave.read("alpha")
    assert any(target["category"] == "token" for target in row["targets"])
    row["token_shared"] = True                 # 値が同じと記録したのに token を消す対象に残した row
    leave._save(row)
    with pytest.raises(ValueError, match="^shared_credential_revoke_refused$"):
        leave.run("alpha", by="operator")
    assert provider["calls"] == [] and tokenpath.exists() and leave_gate.stopped("alpha")
    assert leave.read("alpha")["phase"] == "worker_revoked"
