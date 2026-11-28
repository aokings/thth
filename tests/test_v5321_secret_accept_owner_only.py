"""監査 5.32.0 の軽い 8・9（5.32.1）: 本人の口だけの種類の確かめの深さと、App ID の形。

- 8: 家の係の表（`thth secret kinds --json`）の説明に、何を確かめて置くかを出す。App Secret と thth.me の鍵は
  「確かめたのは形だけ」。はてなの consumer は、token があるとき置き換えの前に標準エラーへ知らせる（値は出さない）。
- 9: App Secret の App ID は数字だけ（欄を取り違えて App Secret が App ID に入ると、認可の URL に載る）。

送る先は偽物だけ。値は試験の値。
"""
from __future__ import annotations

import json
import sys

from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import fake_http, put_secrets  # noqa: F401
from tests.test_v400_s2_principal_auth import NAME, hatena
from thth import cli, secret_accept

NEW_KEY, NEW_SECRET = "fake-new-consumer-key-5321", "fake-new-consumer-secret-5321"


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


# ---------------------------------------------------------------- 8

def test_表の説明に確かめの深さが出て本人の口だけは形だけと言う(capsys):
    assert cli.main(["secret", "kinds", "--json"]) == 0
    rows = {row["kind"]: row for row in json.loads(capsys.readouterr().out)}
    assert all(row["checked"] for row in rows.values())
    for kind in ("app-secret", "thth-login-key"):
        assert "確かめたのは形だけ" in rows[kind]["checked"], kind
    assert "request token" in rows["hatena-consumer"]["checked"]
    assert "知らせ" in rows["hatena-consumer"]["checked"]
    for kind in ("account-token", "shop-token", "hatena-verifier"):
        assert "形だけ" not in rows[kind]["checked"], kind
    assert cli.main(["secret", "kinds"]) == 0
    assert "確かめたのは形だけ" in capsys.readouterr().out


def test_consumerはtokenがあれば置き換えの前に標準エラーで知らせ値は出さない(capsys, world, fake_http):
    put_secrets(world)
    consumer_path = str(world.main_token) + ".consumer"
    before = open(consumer_path).read()
    fake_http(hatena())
    rc, out, err = accept(capsys, "hatena-consumer", NAME, f"{NEW_KEY}\n{NEW_SECRET}\n".encode())
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    assert open(consumer_path).read() != before
    assert "token があります" in err and "consumer を置き換えます" in err
    assert NEW_KEY not in out + err and NEW_SECRET not in out + err


def test_consumerはtokenが無ければ知らせない(capsys, world, fake_http):
    put_secrets(world, token=False)
    fake_http(hatena())
    rc, out, err = accept(capsys, "hatena-consumer", NAME, f"{NEW_KEY}\n{NEW_SECRET}\n".encode())
    assert rc == 0 and "token があります" not in err


def test_principal_authのconsumerもtokenがあれば知らせる(world, fake_http):
    from thth import principal_cli
    put_secrets(world)
    fake_http(hatena())
    lines = [NEW_KEY, NEW_SECRET]
    said = []
    rc = principal_cli.run_auth_hatena_stdin(NAME, by="t", force=True, consumer=True,
                                             read_stdin=lambda: lines.pop(0) if lines else "",
                                             out=said.append, err=said.append)
    assert rc == 0
    text = "\n".join(said)
    assert "token があります" in text and NEW_SECRET not in text


# ---------------------------------------------------------------- 9

def test_AppSecretのApp_IDは数字だけで取り違えは置かない(capsys, tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "app.env"
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(path))
    secret = "fake-app-secret-value-5321"
    rc, out, err = accept(capsys, "app-secret", "threads", (secret + "\n123\n").encode())   # 欄を取り違えた
    assert (rc, answer(out)["code"]) == (10, "invalid_input")
    assert not path.exists() and secret not in out + err
    for bad in ("12a3", "１２３", "-1"):
        rc, out, _err = accept(capsys, "app-secret", "threads", (bad + "\n" + secret + "\n").encode())
        assert (rc, answer(out)["code"]) == (10, "invalid_input"), bad
    rc, out, err = accept(capsys, "app-secret", "threads", ("1234567890123456\n" + secret + "\n").encode())
    assert (rc, answer(out)["code"]) == (0, "ok"), (out, err)
    assert secret in path.read_text()
    assert secret_accept.KINDS["app-secret"]["fields"] == ("app_id", "app_secret")
