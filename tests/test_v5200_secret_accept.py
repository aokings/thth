"""`thth secret accept <種類> <受付>`（monban の家の秘密の係から呼ばれる口・2026-11-27）。

柵:
- 値は標準入力だけ。欄ごとに 1 行、数が違う・`\\r`・0x00・空の欄・最後の改行が無いものは invalid_input。
- 中の入口の照合（口座と本人）をそのまま使う。断りの符丁を表の終了コードに直す。
- 標準出力・標準エラーに値を出さない（中の入口の出力も外に出さない）。
- 表の形は monban の係の表の決まり（種類・欄・符丁の字・16 個まで・`verified:handler_error` を使わない）に合う。

送る先はすべて偽物（偽の Threads・偽の WordPress・偽の BASE・偽のはてな）。値は試験の値。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import cli, secret_accept

TOKEN = "PASTED-SECRET-ACCEPT-TOKEN-0001"


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


# ------------------------------------------------------------------ 表の形（monban の係の表に書ける）

def test_種類と欄と終了コードはmonbanの係の表の形に合う():
    kind_re, field_re = re.compile(r"[a-z][a-z0-9-]{0,31}"), re.compile(r"[a-z][a-z0-9_]{0,31}")
    answer_re = re.compile(r"(verified|rejected):[a-z][a-z0-9_]{0,47}")
    codes = secret_accept.handler_codes()
    assert len(codes) <= 16
    assert all(re.fullmatch(r"(0|[1-9][0-9]{0,2})", rc) and int(rc) <= 255 for rc in codes)
    assert all(answer_re.fullmatch(a) and a != "verified:handler_error" for a in codes.values())
    assert codes["0"] == "verified:ok"
    assert str(secret_accept.UNEXPECTED_EXIT) not in codes and "2" not in codes   # 丸められる側
    for kind, spec in secret_accept.KINDS.items():
        assert kind_re.fullmatch(kind)
        assert 1 <= len(spec["fields"]) <= 4 and all(field_re.fullmatch(f) for f in spec["fields"])
    assert set(secret_accept.INNER.values()) <= set(secret_accept.CODES)


def test_本人の口だけの種類に印がある(capsys):
    assert cli.main(["secret", "kinds", "--json"]) == 0
    rows = {row["kind"]: row for row in json.loads(capsys.readouterr().out)}
    owner_only = {k for k, row in rows.items() if row["owner_only"]}
    assert owner_only == {"app-secret", "thth-login-key", "hatena-consumer"}
    assert rows["shop-client"]["fields"] == ["client_id", "client_secret"]


# ------------------------------------------------------------------ 標準入力の形

@pytest.mark.parametrize("data", [b"", b"ONE", b"ONE\nTWO\n", b"ONE\r\n", b"\n", b"  \n", b"ON\x00E\n",
                                  b"\xff\xfe\n", b"A" * 9000 + b"\n"])
def test_欄の数と形が合わなければinvalid_input(data):
    with pytest.raises(secret_accept.Refused) as caught:
        secret_accept.read_values(data, 1)
    assert caught.value.code == "invalid_input"


def test_改行を含む値は行の数が合わず断る():
    with pytest.raises(secret_accept.Refused):
        secret_accept.read_values(b"client-id\nsecret-with\nnewline\n", 2)
    assert secret_accept.read_values(b"client-id\nsecret value\n", 2) == ["client-id", "secret value"]


# ------------------------------------------------------------------ 口座の token

def _account(isolated_account_factory, tmp_path, **overrides):
    token_path = str(tmp_path / "account.token")
    overrides.setdefault("user_id", "")
    account = isolated_account_factory(token=token_path, **overrides)
    account["token_path"] = token_path
    return account


def test_口座のtokenを置いて値を出さない(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert rc == 0, (out, err)
    assert answer(out) == {"ok": True, "kind": "account-token", "target": account["name"], "state": "verified",
                           "code": "ok"}
    assert json.load(open(account["token_path"]))["access_token"] == TOKEN
    assert TOKEN not in out + err
    assert err == "", "中の入口の出力が外に出ている"


def test_台帳のhandleと違う口座のtokenはaccount_mismatchで置かない(capsys, tmp_path, monkeypatch,
                                                       isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, handle="kopi_chaba")
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (13, "account_mismatch")
    assert not os.path.exists(account["token_path"])
    assert TOKEN not in out + err


def test_台帳のuser_idと違えばaccount_mismatch(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, user_id="123")
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, _err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (13, "account_mismatch")


def test_台帳にhandleが無ければidentity_unknown(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, handle="")
    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, _err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (14, "identity_unknown")
    assert not os.path.exists(account["token_path"])


def test_使えないtokenはcredential_rejected(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)
    with fake_oauth_server({"me": "4xx"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        rc, out, _err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (15, "credential_rejected")


def test_無い口座と種類の違う口座は値を読む前に断る(capsys, tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, media="bluesky", handle="a.bsky.social")
    rc, out, _err = accept(capsys, "account-token", "no-such-account", (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (11, "target_unknown")
    rc, out, _err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert (rc, answer(out)["code"]) == (12, "wrong_kind")


def test_欄が2行ならinvalid_inputで何も書かない(capsys, tmp_path, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)
    rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\nEXTRA\n").encode())
    assert (rc, answer(out)["code"]) == (10, "invalid_input")
    assert not os.path.exists(account["token_path"]) and TOKEN not in out + err


# ------------------------------------------------------------------ Bluesky

def test_BlueskyのApp_Passwordはhandleが合えば置き違えばaccount_mismatch(capsys, tmp_path, monkeypatch,
                                                              isolated_account_factory):
    from thth.adapters import bluesky
    password = "abcd-efgh-ijkl-mnop"   # 試験の値（形だけ合わせた）
    account = _account(isolated_account_factory, tmp_path, media="bluesky", handle="kopicha.bsky.social")
    seen = {}

    def session(service, identifier, app_password, **_kw):
        seen["handle"] = identifier
        return {"accessJwt": "jwt", "did": "did:plc:fake", "handle": seen.get("as", identifier)}
    monkeypatch.setattr(bluesky, "create_session", session)
    rc, out, err = accept(capsys, "bluesky-app-password", account["name"], (password + "\n").encode())
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    before = open(account["token_path"], "rb").read()
    seen["as"] = "someone-else.bsky.social"
    rc, out, err = accept(capsys, "bluesky-app-password", account["name"], (password + "\n").encode())
    assert (rc, answer(out)["code"]) == (13, "account_mismatch")
    assert open(account["token_path"], "rb").read() == before
    assert password not in out + err


def test_思わぬ落ちは表に無い終了コードで値を出さない(capsys, tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path)

    def boom(*_a, **_kw):
        raise RuntimeError("leak " + TOKEN)
    monkeypatch.setattr(secret_accept, "_call", boom)
    rc, out, err = accept(capsys, "account-token", account["name"], (TOKEN + "\n").encode())
    assert rc == secret_accept.UNEXPECTED_EXIT
    assert TOKEN not in out + err


# ------------------------------------------------------------------ WordPress

def test_WordPressは利用者が合えば置き違えばaccount_mismatch(capsys, lane, monkeypatch):
    from tests.test_v5160_wordpress import PASSWORD, PRINCIPAL
    from thth.adapters import wordpress
    rc, out, err = accept(capsys, "wordpress-app-password", PRINCIPAL, (PASSWORD + "\n").encode())
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    before = lane.token.read_bytes()

    def other(self, url, *, op="get"):
        return {"id": 7, "username": "someone-else", "roles": ["editor"]}
    monkeypatch.setattr(wordpress.WordPressAdapter, "_get", other)
    rc, out, err = accept(capsys, "wordpress-app-password", PRINCIPAL, (PASSWORD + "\n").encode())
    assert (rc, answer(out)["code"]) == (13, "account_mismatch")
    assert lane.token.read_bytes() == before
    assert PASSWORD not in out + err


# ------------------------------------------------------------------ 店

def test_BASEの鍵は期限つきで置き別の店はaccount_mismatch(capsys, env, monkeypatch):
    from tests.test_store_base import STORE, run
    from thth.shop import cli as store_cli
    from thth.shop import ledger
    monkeypatch.setattr(store_cli, "READ_SECRET", None)   # 標準入力から読む本番の道
    assert run(capsys, "shop", "add", STORE, "--kind", "base", "--project", "nigamilab", "--by", "m")[0] == 0
    value = env["token"]
    rc, out, err = accept(capsys, "shop-token", STORE, (value + "\n").encode())
    assert (rc, answer(out)["code"]) == (19, "invalid_arguments")          # BASE は --expires が要る
    rc, out, err = accept(capsys, "shop-token", STORE, (value + "\n").encode(), "--expires", "2027-01-31")
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    before = open(ledger.load(STORE)["token"], "rb").read()
    env["shop_id"] = "another"
    rc, out, err = accept(capsys, "shop-token", STORE, (value + "\n").encode(), "--expires", "2027-01-31")
    assert (rc, answer(out)["code"]) == (13, "account_mismatch")
    assert open(ledger.load(STORE)["token"], "rb").read() == before
    assert value not in out + err
    rc, out, _err = accept(capsys, "shop-client", STORE, b"a\nb\n")
    assert (rc, answer(out)["code"]) == (12, "wrong_kind")                 # BASE は client の 2 行を受けない


# ------------------------------------------------------------------ はてな

def test_はてなの確認の文字列はbeginのあとに置きbeginが無ければnot_ready(capsys, world, fake_http):
    from tests.hatena_s2_http import put_secrets
    from tests.test_v400_s2_principal_auth import NAME, VERIFIER, hatena
    from thth import principal_cli
    put_secrets(world, token=False)
    fake_http(hatena())
    rc, out, _err = accept(capsys, "hatena-verifier", NAME, (VERIFIER + "\n").encode())
    assert (rc, answer(out)["code"]) == (16, "not_ready")
    assert principal_cli.run_auth_hatena_stdin(NAME, by="tester", begin=True, out=lambda *_: None,
                                               err=lambda *_: None) == 0
    rc, out, err = accept(capsys, "hatena-verifier", NAME, (VERIFIER + "\n").encode())
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    assert os.path.exists(world.main_token) and VERIFIER not in out + err


# ------------------------------------------------------------------ App Secret

def test_AppSecretはthreadsだけで置き値を出さない(capsys, tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "app.env"
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    secret = "fake-app-secret-value-0042"
    rc, out, err = accept(capsys, "app-secret", "x", ("123\n" + secret + "\n").encode())
    assert (rc, answer(out)["code"]) == (11, "target_unknown")
    rc, out, err = accept(capsys, "app-secret", "threads", ("123\n" + secret + "\n").encode())
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    assert secret in path.read_text() and secret not in out + err


# ------------------------------------------------------------------ 本物の別の命令として（家の係と同じ渡し方）

def test_別の命令として打っても値はargvと出力に出ず終了コードで答える(tmp_path, isolated_account_factory):
    """monban の `run_handler` と同じく、値は標準入力だけ・出力は捨てる前提でも、出力に値が無いことを見る。"""
    account = _account(isolated_account_factory, tmp_path, handle="kopi_chaba")
    with fake_oauth_server() as base_url:
        env = {k: v for k, v in os.environ.items() if k.startswith("THTH_") or k in ("PATH", "HOME", "PYTHONPATH")}
        env.update(THTH_THREADS_BASE_URL=base_url, LANG="C.UTF-8")
        argv = [sys.executable, "-m", "thth", "secret", "accept", "account-token", account["name"], "--by", "tester"]
        proc = subprocess.run(argv, input=(TOKEN + "\n").encode(), capture_output=True, env=env, timeout=60,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert proc.returncode == 13, proc.stderr.decode()
    assert TOKEN.encode() not in proc.stdout + proc.stderr
    assert TOKEN not in " ".join(argv)
    assert not os.path.exists(account["token_path"])


from tests.hatena_s1_helpers import world  # noqa: E402,F401
from tests.hatena_s2_http import fake_http  # noqa: E402,F401
from tests.test_store_base import env  # noqa: E402,F401
from tests.test_v5160_wordpress import lane, wp  # noqa: E402,F401
