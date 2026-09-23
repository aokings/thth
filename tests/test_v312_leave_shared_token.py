"""3.1.2 件 6: 退出で token が他の生きている account と共有なら、遠隔で失効させずに終える。

実測（2026-09-23 12:50）: 同じ app で同じ利用者が認可すると Mastodon は既存の access token を
返すので、kopicha-server-mastodon と kopicha-mastodon の token は値が同じだった。失効すると
他方も死ぬので投げない（判定は正しい）——が、**退出が `worker_revoked` で止まるのは誤り**。

masaru 裁定（09-23）: **値だけの共有（別ファイル・別 inode）なら、自分の token file と env は
消して完了する**（退出した account の秘密を VM に残さない・消しても失効にはならず、相手は
自分のファイルを持つ）。**同じファイル・同じ inode の共有**（path 一致・dev/ino 一致・app
directory 配下）のときだけ従前どおり残す（`preserved: ['token_shared']`）。

見るのは（fake Mastodon・loopback）:
  (a) 値だけ共有 → completed・`remote: unconfirmed_shared`・`/oauth/revoke` は呼ばれない・
      自分の token file は無い・他方の token file は byte 一致・他方は停止しない。
  (a') ファイル共有（同じ path）→ 自分の token file は残る（相手のファイルでもあるので）。
  (b) 値が違う 2 台帳 → 3.1.1 どおり自分の token だけ失効して completed。
  (c) 3.1.1 の形で `worker_revoked` に止まった row（token を消す対象から外していた）からの
      再実行が (a) で完了し、自分の token file も消える。
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


def test_a_値だけ共有なら失効を投げず自分のtoken_fileは消して完了する(env, provider, monkeypatch, capsys):
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
    assert "token_shared" not in row["preserved"] and row["deleted"]["token"] == 1
    assert provider["calls"] == []            # /oauth/revoke は呼ばれない
    assert not tokenpath.exists()              # 退出した account の秘密は残さない
    assert not alpha_ledger.exists() and not alpha_state.exists()
    _assert_other_untouched(beta_token, beta_ledger, token_bytes, ledger_bytes)


def test_a2_ファイルごとの共有なら自分のtoken_fileも残す(env, provider, monkeypatch):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    beta_token, beta_ledger = same_user_beta(env, provider, tokenpath, "mastodon", same_bytes=True)
    # beta の台帳が alpha と同じ token file を指す（path 一致）。
    ledger = json.loads(beta_ledger.read_text())
    ledger["token"] = str(tokenpath)
    beta_ledger.write_text(json.dumps(ledger))
    token_bytes, ledger_bytes = tokenpath.read_bytes(), beta_ledger.read_bytes()
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed" and result["remote"] == "unconfirmed_shared"
    assert result["preserved"] == ["token_shared"] and "token" not in result["deleted"]
    assert provider["calls"] == []
    _assert_other_untouched(tokenpath, beta_ledger, token_bytes, ledger_bytes)


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


def _stuck_at_worker_revoked(monkeypatch):
    """停止と worker の失効までは進み、遠隔の段の手前で落ちた row を作る。"""
    original = leave_gate.credentials
    monkeypatch.setattr(leave_gate, "credentials",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("stop_here")))
    with pytest.raises(ValueError):
        leave.run("alpha", by="operator")
    monkeypatch.setattr(leave_gate, "credentials", original)
    row = leave.read("alpha")
    assert row["phase"] == "worker_revoked" and leave_gate.stopped("alpha")
    return row


def test_c_31_1の形でworker_revokedに止まったrowから再実行して完了する(env, provider, monkeypatch):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    beta_token, beta_ledger = same_user_beta(env, provider, tokenpath, "mastodon", same_bytes=True)
    token_bytes, ledger_bytes = beta_token.read_bytes(), beta_ledger.read_bytes()
    row = _stuck_at_worker_revoked(monkeypatch)
    # 3.1.1 は値だけの共有でも token を消す対象から外し preserved に入れていた。その形に戻す。
    row["targets"] = [target for target in row["targets"] if target["category"] != "token"]
    row["inventory_sha256"] = leave._hash(row["targets"])
    row["preserved"] = ["token_shared"]
    assert row["token_shared"] is True
    leave._save(row)
    result = leave.run("alpha", by="operator")
    assert result["phase"] == "completed" and result["remote"] == "unconfirmed_shared"
    assert provider["calls"] == [] and not tokenpath.exists()
    assert "token_shared" not in result["preserved"]
    _assert_other_untouched(beta_token, beta_ledger, token_bytes, ledger_bytes)


def test_ファイル共有なのに消す対象にtokenが残るrowは矛盾として止める(env, provider, monkeypatch):
    _, tokenpath = configure(env, provider, monkeypatch, "mastodon")
    row = _stuck_at_worker_revoked(monkeypatch)
    assert any(target["category"] == "token" for target in row["targets"])
    # ファイルごと共有と記録したのに token を消す対象に残した row。
    row["token_shared"] = True
    row["preserved"] = sorted(set(row["preserved"]) | {"token_shared"})
    leave._save(row)
    with pytest.raises(ValueError, match="^shared_credential_revoke_refused$"):
        leave.run("alpha", by="operator")
    assert provider["calls"] == [] and tokenpath.exists() and leave_gate.stopped("alpha")
    assert leave.read("alpha")["phase"] == "worker_revoked"
