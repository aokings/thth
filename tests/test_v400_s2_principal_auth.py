"""4.0 S2 §2.4: `thth principal auth <主体> --by`（OAuth 1.0a・PIN（oob））。T-S2-2a〜2d。

はてなには出ない（`httpsafe.urlopen` を fake に差し替える）。秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import json
import os
import stat
import urllib.parse

import pytest

from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import (FAKE_CONSUMER, FAKE_TOKEN, fake_http, feed_xml, entry_xml,  # noqa: F401
                                  markdown_feed, put_secrets)
from thth import admin_log, oauth1, principal_cli, principals

NAME = "hatena-main"
REQ_TOKEN, REQ_SECRET = "fake-request-token-0005", "fake-request-secret-0006"
ACC_TOKEN, ACC_SECRET = "fake-access-token-0007", "fake-access-secret-0008"
VERIFIER = "fake-verifier-0009"
SECRETS = (FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"], REQ_SECRET, ACC_TOKEN,
           ACC_SECRET, VERIFIER)
BLOGS = ("a.hatenablog.com", "b.hatenablog.com", "shared.hatenablog.com")


def hatena(*, url_name="lane-owner", missing_blog=None, confirmed=True, initiate_status=200):
    def handler(method, url, headers, body):
        if url == principal_cli.INITIATE_URL:
            if initiate_status != 200:
                return initiate_status, {}, b"oauth_problem=x"
            text = f"oauth_token={REQ_TOKEN}&oauth_token_secret={REQ_SECRET}"
            if confirmed:
                text += "&oauth_callback_confirmed=true"
            return 200, {}, text.encode()
        if url == principal_cli.TOKEN_URL:
            return 200, {}, (f"oauth_token={ACC_TOKEN}&oauth_token_secret={ACC_SECRET}"
                             f"&url_name={url_name}&display_name=%E3%81%82").encode()
        for blog in BLOGS:
            if method == "GET" and url == f"https://blog.hatena.ne.jp/lane-owner/{blog}/atom/entry":
                if blog == missing_blog:
                    return 404, {}, b"not found"
                return 200, {}, markdown_feed()
        raise AssertionError((method, url))
    return handler


class Prompts:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.labels = []

    def __call__(self, label):
        self.labels.append(label)
        return self.answers.pop(0)


def run(**kw):
    out, err = [], []
    kw.setdefault("isatty", lambda: True)
    rc = principal_cli.run_auth(NAME, by="tester", out=out.append, err=err.append, **kw)
    return rc, "\n".join(out), "\n".join(err)


def admin_rows(event=None):
    got = admin_log.read(account=NAME, event=event)
    return got[0] if isinstance(got, tuple) else got


def files_with(value, root):
    hits = []
    for base, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            try:
                if value in open(p, encoding="utf-8", errors="ignore").read():
                    hits.append(p)
            except OSError:
                pass
    return hits


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------------------------------------------------------- T-S2-2a

def test_T_S2_2a_端末でなければnot_a_ttyで何も書かない(world, fake_http):
    http = fake_http(hatena())
    prompts = Prompts()
    rc, out, err = run(isatty=lambda: False, prompts=prompts)
    assert rc == 2 and "not_a_tty" in err and "ssh -t" in err
    assert prompts.labels == [] and http.calls == []
    assert not os.path.exists(world.main_token) and not os.path.exists(str(world.main_token) + ".consumer")


def test_T_S2_2a_全部の段_consumerを2回受けinitiateにoobと4つのscope_verifierでtokenを0600で書く(world, fake_http):
    """柵: `url_name` と主体の `hatena_id` の比べを消すと、下の hatena_id_mismatch の試験が赤。"""
    http = fake_http(hatena())
    prompts = Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"], VERIFIER)
    rc, out, err = run(prompts=prompts)
    assert rc == 0, (out, err)
    assert len(prompts.labels) == 3
    assert "consumer key" in prompts.labels[0] and "consumer secret" in prompts.labels[1]
    assert "oauth_verifier" in prompts.labels[2] and all("表示されません" in l for l in prompts.labels)
    consumer_path = str(world.main_token) + ".consumer"
    assert json.load(open(consumer_path)) == FAKE_CONSUMER and mode(consumer_path) == 0o600
    # initiate: oob（oauth のパラメタ）・scope は form の body・署名は body を含む
    init = http.calls[0]
    assert (init["method"], init["url"]) == ("POST", principal_cli.INITIATE_URL)
    assert init["body"] == b"scope=read_public%2Cwrite_public%2Cread_private%2Cwrite_private"
    assert init["headers"]["content-type"] == "application/x-www-form-urlencoded"
    fields = oauth1.parse_header(init["headers"]["authorization"])
    assert fields["oauth_callback"] == "oob" and "oauth_token" not in fields
    params = [(k, v) for k, v in fields.items() if k not in ("realm", "oauth_signature")]
    base = oauth1.base_string("POST", principal_cli.INITIATE_URL,
                              params + [("scope", "read_public,write_public,read_private,write_private")])
    assert fields["oauth_signature"] == oauth1.sign_hmac_sha1(base, FAKE_CONSUMER["consumer_secret"], None)
    # 認可の URL（PC 用）
    assert f"https://www.hatena.ne.jp/oauth/authorize?oauth_token={REQ_TOKEN}" in out
    assert "/touch/" not in out and "はてな ID lane-owner" in out
    # token: request token と verifier で署名
    tok = http.calls[1]
    assert tok["url"] == principal_cli.TOKEN_URL
    tf = oauth1.parse_header(tok["headers"]["authorization"])
    assert tf["oauth_token"] == REQ_TOKEN and tf["oauth_verifier"] == VERIFIER
    params = [(k, v) for k, v in tf.items() if k not in ("realm", "oauth_signature")]
    assert tf["oauth_signature"] == oauth1.sign_hmac_sha1(
        oauth1.base_string("POST", principal_cli.TOKEN_URL, params), FAKE_CONSUMER["consumer_secret"], REQ_SECRET)
    # token のファイル（§2.3 の形・0600）
    data = json.load(open(world.main_token))
    assert data["schema"] == "thth.hatena-oauth/1" and data["hatena_id"] == "lane-owner"
    assert (data["oauth_token"], data["oauth_token_secret"]) == (ACC_TOKEN, ACC_SECRET)
    assert data["scopes"] == ["read_public", "write_public", "read_private", "write_private"]
    assert data["obtained_at"] and mode(world.main_token) == 0o600
    # 段 7: 3 つのブログを読んだ
    assert [c["url"] for c in http.calls[2:]] == [
        f"https://blog.hatena.ne.jp/lane-owner/{b}/atom/entry" for b in BLOGS]
    assert out.count("読めました") == 3 and "記法 text/x-markdown" in out
    # admin_log に値が無い（有無と scope と一致だけ）
    (row,) = admin_rows("principal_auth")
    assert row["diff"]["token"] == ["absent", "present"]
    assert row["diff"]["url_name_matches"] == [None, True]
    # 秘密の値は画面に出ず、token と consumer のファイルの外に無い
    for value in SECRETS:
        assert value not in out and value not in err
        hits = set(files_with(value, world.root)) | set(files_with(value, world.tmp / "secrets"))
        assert hits <= {str(world.main_token), consumer_path}, (value, hits)


def test_T_S2_2a_url_nameがhatena_idと違えばtokenを書かない(world, fake_http):
    fake_http(hatena(url_name="someone-else"))
    rc, out, err = run(prompts=Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"], VERIFIER))
    assert rc == 1 and "hatena_id_mismatch" in err
    assert not os.path.exists(world.main_token)
    assert admin_rows("principal_auth") == []


def test_T_S2_2a_本番の受け口はgetpass(world, fake_http, monkeypatch):
    fake_http(hatena())
    asked = []
    answers = [FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"], VERIFIER]
    monkeypatch.setattr("getpass.getpass", lambda label: (asked.append(label), answers.pop(0))[1])
    rc, _out, err = run()
    assert rc == 0, err
    assert len(asked) == 3


def test_T_S2_2a_consumerの空や空白は断って何も書かない(world, fake_http):
    http = fake_http(hatena())
    rc, _out, err = run(prompts=Prompts("fake key with space", "fake-consumer-secret-0004"))
    assert rc == 2 and "consumer_invalid" in err and http.calls == []
    assert not os.path.exists(str(world.main_token) + ".consumer")


def test_T_S2_2a_callback_confirmedが無ければinitiate_failed(world, fake_http):
    fake_http(hatena(confirmed=False))
    rc, _out, err = run(prompts=Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"]))
    assert rc == 1 and "initiate_failed" in err and not os.path.exists(world.main_token)


def test_T_S2_2a_initiateが401ならinitiate_failedで値を出さない(world, fake_http):
    fake_http(hatena(initiate_status=401))
    rc, out, err = run(prompts=Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"]))
    assert rc == 1 and "initiate_failed" in err and "401" in err
    assert FAKE_CONSUMER["consumer_secret"] not in out + err


# ---------------------------------------------------------------- T-S2-2b

def test_T_S2_2b_tokenがあってforce無しはrc2で何も変えない(world, fake_http):
    put_secrets(world)
    before = open(world.main_token).read()
    http = fake_http(hatena())
    prompts = Prompts()
    rc, _out, err = run(prompts=prompts)
    assert rc == 2 and "--force" in err and "--forget" in err
    assert http.calls == [] and prompts.labels == [] and open(world.main_token).read() == before


def test_T_S2_2b_forceは取り直し_consumerは聞かない(world, fake_http):
    put_secrets(world)
    fake_http(hatena())
    prompts = Prompts(VERIFIER)
    rc, _out, err = run(prompts=prompts, force=True)
    assert rc == 0, err
    assert len(prompts.labels) == 1 and json.load(open(world.main_token))["oauth_token"] == ACC_TOKEN
    (row,) = admin_rows("principal_auth")
    assert row["diff"]["token"] == ["present", "present"]


def test_T_S2_2b_forgetはtokenを消してadmin_logに残す(world):
    put_secrets(world)
    rc, out, _err = run(forget=True, isatty=lambda: False)   # 端末は要らない
    assert rc == 0 and not os.path.exists(world.main_token)
    assert os.path.exists(str(world.main_token) + ".consumer")
    (row,) = admin_rows("principal_auth_forgotten")
    assert row["diff"]["token"] == ["present", "absent"]
    rc, _out, _err = run(forget_consumer=True)
    assert rc == 0 and not os.path.exists(str(world.main_token) + ".consumer")


def test_T_S2_2b_consumerはconsumerだけ入れ直しtokenは触らない(world, fake_http):
    put_secrets(world)
    before = open(world.main_token).read()
    http = fake_http(hatena())
    rc, _out, err = run(prompts=Prompts("fake-consumer-key-new1", "fake-consumer-secret-new2"), consumer=True)
    assert rc == 0, err
    assert http.calls == [] and open(world.main_token).read() == before
    assert json.load(open(str(world.main_token) + ".consumer"))["consumer_key"] == "fake-consumer-key-new1"


def test_T_S2_2b_token_lockを持たれていればprincipal_token_busy(world, fake_http):
    fake_http(hatena())
    import fcntl
    principals.ensure_state_dir(NAME)
    fd = os.open(principals.token_lock_path_for(NAME), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        rc, _out, err = run(prompts=Prompts("x" * 10, "y" * 10))
    finally:
        os.close(fd)
    assert rc == 2 and "principal_token_busy" in err
    assert not os.path.exists(str(world.main_token) + ".consumer")


def test_T_S2_2b_byが無ければ断る(world):
    rc = principal_cli.run_auth(NAME, by="", out=lambda _l: None, err=lambda _l: None, isatty=lambda: True)
    assert rc == 2


# ---------------------------------------------------------------- T-S2-2c

def test_T_S2_2c_段7で1つが404ならrc1_tokenは残る(world, fake_http):
    fake_http(hatena(missing_blog="b.hatenablog.com"))
    rc, out, err = run(prompts=Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"], VERIFIER))
    assert rc == 1, (out, err)
    assert os.path.exists(world.main_token)
    assert "ブログ a.hatenablog.com: 読めました" in out
    assert "ブログ b.hatenablog.com: 読めません（404" in out
    assert "ブログ shared.hatenablog.com: 読めました" in out


def test_T_S2_2c_記事が無いブログの記法は不明(world, fake_http):
    def handler(method, url, headers, body):
        if "atom/entry" in url:
            return 200, {}, feed_xml([])
        return hatena()(method, url, headers, body)
    fake_http(handler)
    rc, out, _err = run(prompts=Prompts(FAKE_CONSUMER["consumer_key"], FAKE_CONSUMER["consumer_secret"], VERIFIER))
    assert rc == 0 and "記事 0 件・記法 不明" in out


# ---------------------------------------------------------------- T-S2-2d

def test_T_S2_2d_principal_authはMCPの道具とthth_meの操作に無い(monkeypatch):
    """柵: 道具の一覧か遠くの道の操作に `auth` の口（principal を含む）を足すと赤。"""
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    names = {tool["name"] for tool in server.TOOLS + server.ADMIN_TOOLS + server.SERVER_TOOLS}
    assert not [n for n in names if "principal" in n or "hatena_auth" in n]
    from thth import server_writes
    ops = set(server_writes.PUBLISHING_OPERATIONS) | set(server_writes.READ_OPERATIONS)
    assert not [o for o in ops if "principal" in o or "hatena_auth" in o]
