"""monban の秘密の入力口の前提（2026-11-27）: はてなの主体の鍵を端末の対話でなく標準入力で受ける 2 つの段。

- `--begin`: 置いてある consumer で request token を頼み、認可の URL を出す（秘密は読まない）。
- `--consumer --stdin`: consumer の 2 行を受け、request token が取れたときだけ consumer を書く。
- `--stdin`: 確認の文字列の 1 行で access token に替える。はてな ID が違えば書かない。

はてなには出ない（`httpsafe.urlopen` を fake に差し替える）。秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import json
import os

from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import FAKE_CONSUMER, fake_http, put_secrets  # noqa: F401
from tests.test_v400_s2_principal_auth import (ACC_SECRET, ACC_TOKEN, NAME, REQ_SECRET, REQ_TOKEN, VERIFIER,
                                               hatena)
from thth import principal_cli

NEW_KEY, NEW_SECRET = "fake-new-consumer-key-0101", "fake-new-consumer-secret-0102"


class Lines:
    def __init__(self, *lines):
        self.lines = list(lines)

    def __call__(self):
        return self.lines.pop(0) + "\n" if self.lines else ""


def run(*, now=None, **kw):
    out, err = [], []
    rc = principal_cli.run_auth_hatena_stdin(NAME, by="tester", out=out.append, err=err.append,
                                             now=now, **kw)
    return rc, "\n".join(out), "\n".join(err)


def pending_path():
    return principal_cli._pending_path(NAME)


def test_beginで認可のURLを出しverifierの1行でtokenを書く(world, fake_http):
    put_secrets(world, token=False)
    http = fake_http(hatena())
    rc, out, err = run(begin=True)
    assert rc == 0, (out, err)
    assert f"https://www.hatena.ne.jp/oauth/authorize?oauth_token={REQ_TOKEN}" in out
    assert os.stat(pending_path()).st_mode & 0o777 == 0o600
    rc, out2, err2 = run(read_stdin=Lines(VERIFIER))
    assert rc == 0, (out2, err2)
    data = json.load(open(world.main_token))
    assert (data["oauth_token"], data["oauth_token_secret"]) == (ACC_TOKEN, ACC_SECRET)
    assert not os.path.exists(pending_path()), "request token の預けを捨てていない"
    said = out + err + out2 + err2
    for value in (REQ_SECRET, VERIFIER, ACC_TOKEN, ACC_SECRET, FAKE_CONSUMER["consumer_secret"]):
        assert value not in said
    assert [c["url"] for c in http.calls[:2]] == [principal_cli.INITIATE_URL, principal_cli.TOKEN_URL]


def test_はてなIDが違えばtokenを書かず預けも捨てる(world, fake_http):
    put_secrets(world, token=False)
    fake_http(hatena(url_name="someone-else"))
    assert run(begin=True)[0] == 0
    rc, _out, err = run(read_stdin=Lines(VERIFIER))
    assert rc == 1 and "hatena_id_mismatch" in err
    assert not os.path.exists(world.main_token)
    assert not os.path.exists(pending_path())


def test_beginが無ければverifierを受けない(world, fake_http):
    put_secrets(world, token=False)
    http = fake_http(hatena())
    rc, _out, err = run(read_stdin=Lines(VERIFIER))
    assert rc == 2 and "verifier_pending_missing" in err and http.calls == []
    assert not os.path.exists(world.main_token)


def test_預けが30分を過ぎたら受けない(world, fake_http):
    put_secrets(world, token=False)
    fake_http(hatena())
    assert run(begin=True, now=lambda: 1000.0)[0] == 0
    rc, _out, err = run(read_stdin=Lines(VERIFIER), now=lambda: 1000.0 + principal_cli.PENDING_SECONDS + 1)
    assert rc == 2 and "verifier_pending_missing" in err
    assert not os.path.exists(world.main_token)


def test_tokenがあればforce無しでは置き換えない(world, fake_http):
    put_secrets(world)
    before = open(world.main_token).read()
    fake_http(hatena())
    assert run(begin=True)[0] == 0
    rc, _out, err = run(read_stdin=Lines(VERIFIER))
    assert rc == 2 and "token_present" in err
    assert open(world.main_token).read() == before
    rc, _out, err = run(read_stdin=Lines(VERIFIER), force=True)
    assert rc == 0, err


def test_consumerはrequest_tokenが取れたときだけ書く(world, fake_http):
    put_secrets(world, token=False)
    consumer_path = str(world.main_token) + ".consumer"
    before = open(consumer_path).read()
    fake_http(hatena(initiate_status=401))
    rc, out, err = run(consumer=True, read_stdin=Lines(NEW_KEY, NEW_SECRET))
    assert rc == 1 and "initiate_failed" in err and "consumer は書いていません" in err
    assert open(consumer_path).read() == before, "確かめる前に consumer を書いた"
    assert not os.path.exists(pending_path())
    assert NEW_SECRET not in out + err


def test_consumerの2行が通れば書いて認可のURLを出す(world, fake_http):
    put_secrets(world, token=False, consumer=False)
    fake_http(hatena())
    rc, out, err = run(consumer=True, read_stdin=Lines(NEW_KEY, NEW_SECRET))
    assert rc == 0, (out, err)
    consumer = json.load(open(str(world.main_token) + ".consumer"))
    assert (consumer["consumer_key"], consumer["consumer_secret"]) == (NEW_KEY, NEW_SECRET)
    assert "oauth/authorize" in out and NEW_SECRET not in out + err
    assert os.path.exists(pending_path())


def test_consumerの行が足りなければ何も書かない(world, fake_http):
    put_secrets(world, token=False, consumer=False)
    http = fake_http(hatena())
    rc, _out, err = run(consumer=True, read_stdin=Lines(NEW_KEY))
    assert rc == 2 and "consumer_invalid" in err and http.calls == []
    assert not os.path.exists(str(world.main_token) + ".consumer")
