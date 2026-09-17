"""秘密の**値そのもの**が伏字になる（発注 A-1・セキュリティ監査 2026-09-16・P1-1）。

`thth/redact.py::redact()` はもともと `access_token=` のような**綴り**にしか
反応しない。サーバが `rejected credential <値>` のように**キー名なしで値を
反射**すると、綴りが無いのでどの正規表現も当たらず、値がそのまま
`thth/adapters/threads.py::_read`（HTTPError の `error.message`）・
`thth/oauth.py::_error_message` を素通りして、`thth topics <acct> --search
--json` の標準出力・`thth doctor` の出力・例外文に残っていた。

`redact.register_secret()` はプロセス内の登録簿に値を控え、`redact()` は
正規表現のあとにその値そのものを（綴りに関わらず）消す。ここではその登録簿と、
Threads adapter・OAuth・CLI（`thth topics --search`・`thth doctor`）の各経路が
登録済みの `redact()` を通ることを確かめる。

本物の Threads API・OAuth API には一切触れない（`http.server` の偽サーバにだけ
向ける）。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading

import pytest

from tests.conftest import run_thth
from thth import oauth as oauth_mod
from thth import redact as redact_mod
from thth.adapters import base as adapter_base
from thth.adapters import bluesky as bluesky_mod
from thth.adapters import mastodon as mastodon_mod
from thth.adapters import threads as threads_mod

FAKE_TOKEN_VALUE = "FAKE_TOKEN_VALUE_1234"
FAKE_SECRET_VALUE = "FAKE_SECRET_VALUE_5678"
REFLECT_BODY = {"error": {"message":
                f"rejected credential {FAKE_TOKEN_VALUE} and {FAKE_SECRET_VALUE}"}}


@pytest.fixture(autouse=True)
def _clean_registry():
    """登録簿はプロセス内で共有される（モジュール変数）。テスト間で漏れないよう
    毎回空にする——前のテストが登録した値が、別のテストの平文を巻き込んで
    「たまたま消えた」を作らないため。"""
    redact_mod._clear_registered()
    yield
    redact_mod._clear_registered()


class _ReflectingHandler(http.server.BaseHTTPRequestHandler):
    """どの path・method に対しても 400 を返し、本文に**キー名なしで**秘密の
    値を反射する偽サーバ（監査の再現 `http-repro.py` と同じ形の応答）。"""

    def _reflect(self):
        body = json.dumps(REFLECT_BODY).encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._reflect()

    def do_POST(self):  # noqa: N802
        self._reflect()

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _reflecting_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _ReflectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _write_token(path, *, access_token=FAKE_TOKEN_VALUE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": access_token, "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999", "username": "nigamilab",
                   "scopes": None}, f)


# ==================================================== (a) 登録簿そのもの

def test_a_登録した値はredactで伏字になる():
    redact_mod.register_secret(FAKE_TOKEN_VALUE)
    out = redact_mod.redact(f"rejected credential {FAKE_TOKEN_VALUE}")
    assert FAKE_TOKEN_VALUE not in out
    assert "***" in out


def test_a_7文字は登録されない():
    short = "SHORT12"
    assert len(short) == 7
    redact_mod.register_secret(short)
    out = redact_mod.redact(f"value={short} の続き")
    assert short in out, "7 文字の語まで消してしまっている"


def test_a_8文字ちょうどは登録される():
    eight = "EIGHTCH1"
    assert len(eight) == 8
    redact_mod.register_secret(eight)
    out = redact_mod.redact(f"value={eight}")
    assert eight not in out


def test_a_noneと空文字は無視される():
    redact_mod.register_secret(None)
    redact_mod.register_secret("")
    out = redact_mod.redact("何も変わらない文です")
    assert out == "何も変わらない文です"


# ==================================================== (b) ThreadsAdapter._read 経路

def test_b_偽サーバがキー名なしで値を反射してもAdapterErrorの文字列に両方無い():
    # **process 内で app_secret 相当の値も既に登録されている想定**（同じプロセスで
    # `thth auth` を先に実行していれば `oauth.exchange_long_lived_token()` が
    # 登録している）。ここでは直接登録して同じ状況を作る。
    redact_mod.register_secret(FAKE_SECRET_VALUE)
    with _reflecting_server() as base_url:
        # **ThreadsAdapter.__init__ が access_token を登録する**（本体の直し）。
        adapter = threads_mod.ThreadsAdapter(
            base_url=base_url, access_token=FAKE_TOKEN_VALUE, user_id="999999", timeout=2.0)
        with pytest.raises(adapter_base.AdapterError) as e:
            adapter.keyword_search("お茶")
    text = str(e.value)
    assert FAKE_TOKEN_VALUE not in text, f"トークンの値が残っている: {text}"
    assert FAKE_SECRET_VALUE not in text, f"secret の値が残っている: {text}"


def test_b_delete_postの例外文にも値が残らない():
    with _reflecting_server() as base_url:
        adapter = threads_mod.ThreadsAdapter(
            base_url=base_url, access_token=FAKE_TOKEN_VALUE, user_id="999999", timeout=2.0)
        with pytest.raises(adapter_base.AdapterError) as e:
            adapter.delete_post("123")
    assert FAKE_TOKEN_VALUE not in str(e.value)


# =============================== (b2) Bluesky / Mastodon adapter の共通登録簿

def test_b2_BlueskyAdapterの全secretが共通redactを通る(monkeypatch):
    app_password = "BLUESKY_APP_PASSWORD_1234"
    access_jwt = "BLUESKY_ACCESS_JWT_5678"
    refresh_jwt = "BLUESKY_REFRESH_JWT_9012"
    adapter = bluesky_mod.BlueskyAdapter(
        service="http://127.0.0.1:1", identifier="name.example",
        app_password=app_password,
    )
    monkeypatch.setattr(
        bluesky_mod, "create_session",
        lambda *_a, **_k: {"did": "did:plc:test", "handle": "name.example",
                           "accessJwt": access_jwt, "refreshJwt": refresh_jwt},
    )
    adapter.session()

    error = redact_mod.redact(
        f"adapter error reflected {app_password} {access_jwt} {refresh_jwt}")
    for secret in (app_password, access_jwt, refresh_jwt):
        assert secret not in error
    assert error.count("***") == 3


def test_b2_Blueskyの異常session応答でも受け取ったJWTを直ちに登録する(monkeypatch):
    access_jwt = "BLUESKY_MALFORMED_ACCESS_JWT_1234"
    refresh_jwt = "BLUESKY_MALFORMED_REFRESH_JWT_5678"
    monkeypatch.setattr(
        bluesky_mod, "_xrpc",
        lambda *_a, **_k: {"accessJwt": access_jwt, "refreshJwt": refresh_jwt},
    )

    with pytest.raises(RuntimeError, match="did がありません"):
        bluesky_mod.create_session(
            "http://127.0.0.1:1", "name.example", "APP_PASSWORD_FOR_MALFORMED_1234")

    error = redact_mod.redact(f"adapter error reflected {access_jwt} {refresh_jwt}")
    assert access_jwt not in error
    assert refresh_jwt not in error
    assert error.count("***") == 2


def test_b2_MastodonAdapterのtokenが共通redactを通る():
    token = "MASTODON_ACCESS_TOKEN_1234"
    mastodon_mod.MastodonAdapter(
        instance="http://127.0.0.1:1", access_token=token,
    )

    error = redact_mod.redact(f"adapter error reflected {token}")
    assert token not in error
    assert "***" in error


# ==================================================== (c) oauth の各経路

def test_c_長期トークン交換の反射がOAuthErrorに残らない():
    with _reflecting_server() as base_url:
        import os as _os
        _os.environ["THTH_THREADS_BASE_URL"] = base_url
        try:
            with pytest.raises(oauth_mod.OAuthError) as e:
                oauth_mod.exchange_long_lived_token(FAKE_SECRET_VALUE, FAKE_TOKEN_VALUE)
        finally:
            _os.environ.pop("THTH_THREADS_BASE_URL", None)
    text = str(e.value)
    assert FAKE_SECRET_VALUE not in text, f"app secret の値が残っている: {text}"
    assert FAKE_TOKEN_VALUE not in text, f"短期トークンの値が残っている: {text}"


def test_c_短期トークン交換の反射も残らない():
    with _reflecting_server() as base_url:
        import os as _os
        _os.environ["THTH_THREADS_BASE_URL"] = base_url
        try:
            with pytest.raises(oauth_mod.OAuthError) as e:
                oauth_mod.exchange_short_lived_token(
                    "APP-ID", FAKE_SECRET_VALUE, "https://x.example.test/", FAKE_TOKEN_VALUE)
        finally:
            _os.environ.pop("THTH_THREADS_BASE_URL", None)
    text = str(e.value)
    assert FAKE_SECRET_VALUE not in text
    assert FAKE_TOKEN_VALUE not in text


def test_c_fetch_meの反射も残らない():
    with _reflecting_server() as base_url:
        import os as _os
        _os.environ["THTH_THREADS_BASE_URL"] = base_url
        try:
            with pytest.raises(oauth_mod.OAuthError) as e:
                oauth_mod.fetch_me(FAKE_TOKEN_VALUE)
        finally:
            _os.environ.pop("THTH_THREADS_BASE_URL", None)
    assert FAKE_TOKEN_VALUE not in str(e.value)


# ==================================================== (d) CLI: thth topics --search --json

@pytest.fixture
def account(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)
    acc["token_path"] = token_path
    return acc


def test_d_topics_search_jsonのstdoutに値が無い(account):
    with _reflecting_server() as base_url:
        r = run_thth(["topics", account["name"], "--search", "お茶", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert FAKE_TOKEN_VALUE not in r.stdout, r.stdout
    assert FAKE_TOKEN_VALUE not in r.stderr, r.stderr


def test_d_topics_search_画面表示にも値が無い(account):
    with _reflecting_server() as base_url:
        r = run_thth(["topics", account["name"], "--search", "お茶"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert FAKE_TOKEN_VALUE not in r.stdout, r.stdout
    assert FAKE_TOKEN_VALUE not in r.stderr, r.stderr


# ==================================================== (e) CLI: thth doctor --json

def test_e_doctor_jsonの出力に値が無い(account):
    with _reflecting_server() as base_url:
        r = run_thth(["doctor", account["name"], "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert FAKE_TOKEN_VALUE not in r.stdout, r.stdout
    assert FAKE_TOKEN_VALUE not in r.stderr, r.stderr


def test_e_doctor_画面表示にも値が無い(account):
    with _reflecting_server() as base_url:
        r = run_thth(["doctor", account["name"]],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert FAKE_TOKEN_VALUE not in r.stdout, r.stdout
    assert FAKE_TOKEN_VALUE not in r.stderr, r.stderr
