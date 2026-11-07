"""`thth gads auth` と認可の置き場（Google 広告・読むだけの口の土台）。本物の網には出ない（偽の token の窓口）。"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import stat
import threading
import urllib.parse

import pytest

from thth import cli
from thth.gads import api, auth, wire
from thth.gads.wire import GadsRefused

CLIENT_SECRET = "FAKE-client-secret-0123456789"
REFRESH = "1//FAKE-refresh-token-abcdefghijklmnop"
ACCESS = "ya29.FAKE-access-token-qrstuvwxyz0123"
AUTH_CODE = "4/FAKE-auth-code-ZYXWVUT98765"
SECRETS = (CLIENT_SECRET, REFRESH, ACCESS, AUTH_CODE)


@pytest.fixture
def gads_env(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {
        "client_id": "123-fake.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}), encoding="utf-8")
    token = tmp_path / "newdir" / "token.json"
    monkeypatch.setenv(auth.CLIENT_ENV, str(client))
    monkeypatch.setenv(auth.TOKEN_ENV, str(token))
    return {"client": client, "token": token}


class FakeTokenEndpoint:
    def __init__(self, *, exchange=None, refresh=None):
        self.calls = []
        self.exchange = exchange or (200, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3599,
                                           "scope": auth.SCOPE, "token_type": "Bearer"})
        self.refresh = refresh or (200, {"access_token": ACCESS, "expires_in": 3599, "token_type": "Bearer"})

    def __call__(self, method, url, headers, body):
        form = dict(urllib.parse.parse_qsl(body.decode("ascii"))) if body else {}
        self.calls.append({"method": method, "url": url, "headers": headers, "form": form})
        assert url == "https://oauth2.googleapis.com/token"
        status, value = self.exchange if form.get("grant_type") == "authorization_code" else self.refresh
        return status, json.dumps(value).encode()


def _hit(url, query):
    parsed = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.request("GET", "/?" + urllib.parse.urlencode(query))
    conn.getresponse().read()
    conn.close()


def _browser(reply):
    """ブラウザの代わり: 認可の URL を読み、戻り先に `reply(params)` の query を投げる（別の糸で）。"""
    seen = {}

    def open_(url):
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        seen.update(params, url=url)
        threading.Thread(target=_hit, args=(params["redirect_uri"], reply(params)), daemon=True).start()
    return open_, seen


def test_認可のURLはPKCEのS256とloopbackの戻り先を持つ(gads_env):
    client = auth.load_client()
    verifier = auth.new_verifier()
    url = auth.authorization_url(client, "http://127.0.0.1:54321", "st-123", verifier)
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://accounts.google.com/o/oauth2/auth?")
    assert params["redirect_uri"] == "http://127.0.0.1:54321"
    assert params["scope"] == "https://www.googleapis.com/auth/adwords"
    assert params["response_type"] == "code" and params["state"] == "st-123"
    assert params["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == expected
    assert 43 <= len(verifier) <= 128 and all(c.isalnum() or c in "-._~" for c in verifier)
    assert CLIENT_SECRET not in url and verifier not in url


def test_通しの認可はtokenを600で残し秘密を出さない(gads_env, capsys):
    endpoint = FakeTokenEndpoint()
    open_, seen = _browser(lambda p: {"code": AUTH_CODE, "state": p["state"], "scope": auth.SCOPE})
    said = []
    value = auth.run(timeout=10, transport=endpoint, browser=open_, say=said.append)
    path = gads_env["token"]
    assert value == {"ok": True, "token_path": str(path), "scope": auth.SCOPE, "mode": "0600"}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    stored = json.loads(path.read_text())
    assert stored["refresh_token"] == REFRESH and "access_token" not in stored
    # 交換の依頼は PKCE の verifier と同じ戻り先を送る（challenge は verifier から）。
    form = endpoint.calls[0]["form"]
    assert form["grant_type"] == "authorization_code" and form["code"] == AUTH_CODE
    assert form["redirect_uri"] == seen["redirect_uri"].rstrip("/")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == seen["code_challenge"]
    out = "\n".join(said) + json.dumps(value) + "".join(capsys.readouterr())
    for secret in SECRETS + (form["code_verifier"],):
        assert secret not in out


def test_CLIのauthも秘密を出さない(gads_env, monkeypatch, capsys):
    endpoint = FakeTokenEndpoint()
    monkeypatch.setattr(auth, "default_transport", endpoint)
    open_, _ = _browser(lambda p: {"code": AUTH_CODE, "state": p["state"]})
    monkeypatch.setattr("webbrowser.open", open_)
    rc = cli.main(["gads", "auth", "--timeout", "10"])
    out = capsys.readouterr()
    assert rc == 0, out
    assert "600" in out.out and "thth gads accounts" in out.out
    for secret in SECRETS:
        assert secret not in out.out + out.err


def test_戻りのstateが違えば断る(gads_env):
    open_, _ = _browser(lambda p: {"code": AUTH_CODE, "state": "not-the-state"})
    with pytest.raises(GadsRefused) as caught:
        auth.run(timeout=10, transport=FakeTokenEndpoint(), browser=open_, say=lambda _: None)
    assert caught.value.code == "gads_auth_state_mismatch"
    assert not gads_env["token"].exists()


def test_許可されなければ断る(gads_env):
    open_, _ = _browser(lambda p: {"error": "access_denied", "state": p["state"]})
    with pytest.raises(GadsRefused) as caught:
        auth.run(timeout=10, transport=FakeTokenEndpoint(), browser=open_, say=lambda _: None)
    assert caught.value.code == "gads_auth_denied" and caught.value.detail == "access_denied"


def test_戻りが来なければ時間切れで断る(gads_env):
    with pytest.raises(GadsRefused) as caught:
        auth.run(timeout=0.3, transport=FakeTokenEndpoint(), browser=lambda url: None, say=lambda _: None)
    assert caught.value.code == "gads_auth_timeout"


def test_refresh_tokenが無い答えは断る(gads_env):
    endpoint = FakeTokenEndpoint(exchange=(200, {"access_token": ACCESS, "expires_in": 3599}))
    open_, _ = _browser(lambda p: {"code": AUTH_CODE, "state": p["state"]})
    with pytest.raises(GadsRefused) as caught:
        auth.run(timeout=10, transport=endpoint, browser=open_, say=lambda _: None)
    assert caught.value.code == "gads_no_refresh_token"
    assert not gads_env["token"].exists()


def test_交換の失敗は符丁と語だけ(gads_env):
    endpoint = FakeTokenEndpoint(exchange=(400, {"error": "invalid_grant", "error_description": AUTH_CODE}))
    open_, _ = _browser(lambda p: {"code": AUTH_CODE, "state": p["state"]})
    with pytest.raises(GadsRefused) as caught:
        auth.run(timeout=10, transport=endpoint, browser=open_, say=lambda _: None)
    assert caught.value.code == "gads_auth_exchange_failed" and AUTH_CODE not in str(caught.value)


def test_置き場は600で作る_既にあるファイルも置き換えて600(gads_env):
    path = gads_env["token"]
    path.parent.mkdir(mode=0o755)
    path.write_text("{}")
    os.chmod(path, 0o644)
    auth.save_token({"refresh_token": REFRESH})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["token.json"]  # 一時ファイルを残さない


def test_refreshはrefresh_tokenで毎回の命令ごとにaccess_tokenを取る(gads_env):
    auth.save_token({"refresh_token": REFRESH})
    calls = []

    def transport(method, url, headers, body):
        if url.startswith("https://oauth2.googleapis.com/"):
            form = dict(urllib.parse.parse_qsl(body.decode()))
            calls.append(("token", form))
            return 200, json.dumps({"access_token": ACCESS, "expires_in": 3599}).encode()
        calls.append(("api", method, url, headers))
        return 200, json.dumps({"resourceNames": ["customers/2260060755"]}).encode()

    client = api.Client(transport=transport)
    assert client.accessible_customers() == ["2260060755"]
    assert client.accessible_customers() == ["2260060755"]
    token_calls = [c for c in calls if c[0] == "token"]
    assert len(token_calls) == 1  # 1 つの命令（Client）で 1 回
    form = token_calls[0][1]
    assert form == {"client_id": "123-fake.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
                    "grant_type": "refresh_token", "refresh_token": REFRESH}
    _, method, url, headers = [c for c in calls if c[0] == "api"][0]
    assert method == "GET" and url == f"https://googleads.googleapis.com/{api.API_VERSION}/customers:listAccessibleCustomers"
    assert headers["Authorization"] == f"Bearer {ACCESS}"
    assert not any(k.lower() == "developer-token" for k in headers)  # 2026-09-09 に廃止


@pytest.mark.parametrize("status,value,code", [
    (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, "gads_token_revoked"),
    (500, {"error": "server_error"}, "gads_token_refresh_failed"),
    (200, {"expires_in": 3599}, "gads_token_refresh_failed"),
])
def test_refreshの失敗(gads_env, status, value, code):
    auth.save_token({"refresh_token": REFRESH})
    endpoint = FakeTokenEndpoint(refresh=(status, value))
    with pytest.raises(GadsRefused) as caught:
        api.Client(transport=endpoint).accessible_customers()
    assert caught.value.code == code
    assert REFRESH not in str(caught.value) and CLIENT_SECRET not in str(caught.value)


@pytest.mark.parametrize("content,detail", [
    ("not json", "unreadable"),
    (json.dumps({"web": {"client_id": "x"}}), "not_installed"),
    (json.dumps({"installed": {"auth_uri": "https://a.example/", "token_uri": "https://b.example/"}}), "client_id"),
    (json.dumps({"installed": {"client_id": "x", "auth_uri": "http://a.example/", "token_uri": "https://b.example/"}}),
     "auth_uri"),
])
def test_client_jsonが違えば断る(gads_env, content, detail):
    gads_env["client"].write_text(content)
    with pytest.raises(GadsRefused) as caught:
        auth.load_client()
    assert (caught.value.code, caught.value.detail) == ("gads_client_invalid", detail)


def test_client_jsonが無ければ断る(gads_env):
    gads_env["client"].unlink()
    with pytest.raises(GadsRefused) as caught:
        auth.load_client()
    assert caught.value.code == "gads_client_missing"


def test_既定の置き場はconfigのthth(monkeypatch, tmp_path):
    monkeypatch.delenv(auth.CLIENT_ENV, raising=False)
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert auth.client_path() == str(tmp_path / ".config" / "thth" / "google-ads-client.json")
    assert auth.token_path() == str(tmp_path / ".config" / "thth" / "google-ads-token.json")


def test_網に届かなければ符丁だけ(monkeypatch):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.URLError(f"cannot reach {req.full_url}")
    monkeypatch.setattr(wire.httpsafe, "urlopen", boom)
    with pytest.raises(GadsRefused) as caught:
        wire.default_transport("GET", "https://googleads.googleapis.com/v26/customers:listAccessibleCustomers", {}, None)
    assert str(caught.value) == "gads_unavailable" and caught.value.__cause__ is None
