"""監査 5.32.0 の軽い 3・4・5 (b)・7・11（5.32.1）: `thth secret accept` の答えの符丁と終了コード。

- 3 (a): コロンの無い符丁だけの行も拾う（Bluesky の台帳に handle が無い → identity_unknown 14）。
- 3 (b): fb・ig の「別のページ・別の口座」は token_unusable に包まず token_account_mismatch（13）。
- 4: 鍵を書いたあとの控えが落ちたら、rejected でなく stored_with_warning（3）。置いていなければ 3 にしない。
- 5 (b): 家の係の表で verified になるのは ok と stored_with_warning だけ。
- 7: rc 3 は断りではないので、受け口の案内（report_channel）の行を足さない。
- 11: 中の入口の符丁の表（INNER）の符丁は、どれも断りの目録にある。

送る先は偽物だけ。値は試験の値。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import adapters, cli, secret_accept

TOKEN = "V5321-SECRET-ACCEPT-TOKEN-0001"


def accept(capsys, kind, target, data: bytes, *extra):
    old = sys.stdin

    class _In:
        buffer = __import__("io").BytesIO(data)

        def read(self, n=-1):
            return ""
    sys.stdin = _In()
    try:
        rc = cli.main(["secret", "accept", kind, target, "--by", "tester", *extra])
    finally:
        sys.stdin = old
    out, err = capsys.readouterr()
    return rc, out, err


def answer(out):
    return json.loads(out.strip().splitlines()[-1])


def _account(factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    overrides.setdefault("user_id", "")
    account = factory(token=token_path, **overrides)
    account["token_path"] = token_path
    return account


# ---------------------------------------------------------------- 3 (a)

def test_Blueskyの台帳にhandleが無ければidentity_unknown(capsys, tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, media="bluesky", handle="")
    rc, out, err = accept(capsys, "bluesky-app-password", account["name"], b"abcd-efgh-ijkl-mnop\n")
    assert (rc, answer(out)["code"]) == (14, "identity_unknown"), (out, err)
    assert not os.path.exists(account["token_path"])


@pytest.mark.parametrize("line,code", [
    ("bluesky_ledger_identifier_required", "bluesky_ledger_identifier_required"),
    ("auth_account_changed", "auth_account_changed"),
    ("app_client_changed: 保存しません", "app_client_changed"),
    ("token: 何かの語\nbluesky_account_required", "bluesky_account_required"),
])
def test_符丁だけの行も符丁として拾い表に載る符丁を先にする(line, code):
    sink = secret_accept._Sink()
    for text in line.split("\n"):
        sink.err_line(text)
    assert sink.code() == code


# ---------------------------------------------------------------- 3 (b)

@pytest.mark.parametrize("said", [
    "facebook_page_mismatch: この鍵のページは 別のページ（1361492233719922）で、台帳の page_id 1469823909537300 と別のページです",
    "instagram_account_mismatch: この鍵の口座は 1784 で、台帳の ig_user_id 1785 と別の口座です",
])
def test_fbとigの別の口座の鍵はaccount_mismatch(capsys, tmp_path, monkeypatch, isolated_account_factory, said):
    account = _account(isolated_account_factory, tmp_path, handle="1469823909537300")

    class Fake:
        def whoami(self):
            raise adapters.base.AdapterError(said)
    monkeypatch.setattr(adapters, "make_adapter", lambda cfg, tok: Fake())
    rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (13, "account_mismatch"), (out, err)
    assert not os.path.exists(account["token_path"]) and TOKEN not in out + err


def test_ほかの使えない鍵はcredential_rejectedのまま(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)

    class Fake:
        def whoami(self):
            raise adapters.base.AdapterError("user_id の取得に失敗しました: HTTP 401 Unauthorized")
    monkeypatch.setattr(adapters, "make_adapter", lambda cfg, tok: Fake())
    rc, out, _err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (15, "credential_rejected")


# ---------------------------------------------------------------- 4

@pytest.mark.parametrize("fault", ["unexpected", "durability"])
def test_置いたあとの控えが落ちたらstored_with_warning(capsys, tmp_path, monkeypatch, isolated_account_factory, fault):
    from thth import admin_log, doctor
    account = _account(isolated_account_factory, tmp_path)
    if fault == "unexpected":
        def boom(*_a, **_kw):
            raise RuntimeError("doctor down " + TOKEN)
        monkeypatch.setattr(doctor, "record_auth", boom)
    else:
        def boom(*_a, **_kw):
            exc = admin_log.AdminLogError("durability")
            exc.complete, exc.appended = True, True
            raise exc
        monkeypatch.setattr(doctor, "record_auth", boom)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert os.path.exists(account["token_path"])
    assert (rc, answer(out)["code"]) == (3, "stored_with_warning"), (rc, out, err)
    assert answer(out)["state"] == "verified" and TOKEN not in out + err


def test_置く前に落ちたら3にせず表に無い終了コード(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)

    class Fake:
        def whoami(self):
            raise KeyError("boom")
    monkeypatch.setattr(adapters, "make_adapter", lambda cfg, tok: Fake())
    rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert rc == secret_accept.UNEXPECTED_EXIT and not os.path.exists(account["token_path"])


def test_断って前の鍵を書き戻した入口は3にしない(capsys, tmp_path, monkeypatch, isolated_account_factory):
    """鍵を書いたあと取引が落ちて前の鍵へ戻したとき（中身は前のまま）は、置いていないので rejected。"""
    from thth import admin_log
    account = _account(isolated_account_factory, tmp_path)
    with open(account["token_path"], "w") as f:
        json.dump({"access_token": "OLD-V5321", "user_id": "999999", "username": "nigamilab"}, f)
    os.chmod(account["token_path"], 0o600)
    before = open(account["token_path"], "rb").read()
    real = admin_log.append

    def refuse(event, *a, **kw):
        if event == "token_set":
            raise admin_log.AdminLogError("refused")
        return real(event, *a, **kw)
    monkeypatch.setattr(admin_log, "append", refuse)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert open(account["token_path"], "rb").read() == before
    assert rc not in (0, 3) and answer(out)["state"] == "rejected", (out, err)


# ---------------------------------------------------------------- 5 (b)

def test_verifiedはokとstored_with_warningだけ(capsys):
    verified = {code for code, (_rc, state) in secret_accept.CODES.items() if state == "verified"}
    assert verified == {"ok", "stored_with_warning"}
    assert all(state in ("verified", "rejected") for _rc, state in secret_accept.CODES.values())
    assert cli.main(["secret", "kinds", "--json"]) == 0
    for row in json.loads(capsys.readouterr().out):
        states = {answer_.split(":")[1]: answer_.split(":")[0] for answer_ in row["codes"].values()}
        assert {c for c, s in states.items() if s == "verified"} == {"ok", "stored_with_warning"}, row["kind"]


# ---------------------------------------------------------------- 7

def test_rc3は断りの案内を足さず断りには足す(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)
    monkeypatch.setattr(secret_accept, "accept", lambda *a, **k: "stored_with_warning")
    rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert rc == 3 and "report_channel" not in out + err and "exit_3" not in out + err
    monkeypatch.setattr(secret_accept, "accept", lambda *a, **k: "credential_rejected")
    rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert rc == 15 and "report_channel" in out + err


# ---------------------------------------------------------------- 11

def test_中の入口の符丁はどれも断りの目録にある():
    from thth import refusal_catalog as rc
    missing = sorted(code for code in secret_accept.INNER if code not in rc.CATALOG)
    assert missing == []
    for code in ("token_handle_missing", "token_account_mismatch", "token_unusable", "wp_user_mismatch",
                 "verifier_pending_missing", "invalid_user_id", "user_id_locked_by_token"):
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code
