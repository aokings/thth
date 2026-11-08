"""`thth gsc auth` と認可の置き場（Search Console・読むだけの口の土台）。本物の網には出ない（偽の token の窓口）。

流れの中身は `thth/google_oauth.py`（gads と共通）。ここは Search Console の違い（scope・置き場・符丁の頭）を縛る。
"""
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
from thth.gads import auth as gads_auth
from thth.gsc import api, auth, wire
from thth.gsc.wire import GscRefused

CLIENT_SECRET = "FAKE-client-secret-0123456789"
REFRESH = "1//FAKE-gsc-refresh-token-abcdefghijklmnop"
ACCESS = "ya29.FAKE-gsc-access-token-qrstuvwxyz0123"
AUTH_CODE = "4/FAKE-gsc-auth-code-ZYXWVUT98765"
SECRETS = (CLIENT_SECRET, REFRESH, ACCESS, AUTH_CODE)


@pytest.fixture
def gsc_env(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {
        "client_id": "123-fake.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}), encoding="utf-8")
    token = tmp_path / "newdir" / "gsc-token.json"
    gads_token = tmp_path / "gads-token.json"
    monkeypatch.setenv(auth.CLIENT_ENV, str(client))
    monkeypatch.setenv(auth.TOKEN_ENV, str(token))
    monkeypatch.setenv(gads_auth.TOKEN_ENV, str(gads_token))
    return {"client": client, "token": token, "gads_token": gads_token}


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
    seen = {}

    def open_(url):
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        seen.update(params, url=url)
        threading.Thread(target=_hit, args=(params["redirect_uri"], reply(params)), daemon=True).start()
    return open_, seen


def test_scopeはwebmasters_readonlyだけ_PKCEのS256(gsc_env):
    client = auth.load_client()
    verifier = auth.google_oauth.new_verifier()
    url = auth.authorization_url(client, "http://127.0.0.1:54321", "st-1", verifier)
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert params["scope"] == "https://www.googleapis.com/auth/webmasters.readonly"
    assert params["code_challenge_method"] == "S256" and params["redirect_uri"] == "http://127.0.0.1:54321"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == expected
    assert CLIENT_SECRET not in url and verifier not in url


def test_通しの認可はgscの置き場に600で残しgadsの鍵に触らない(gsc_env, capsys):
    endpoint = FakeTokenEndpoint()
    open_, seen = _browser(lambda p: {"code": AUTH_CODE, "state": p["state"], "scope": auth.SCOPE})
    said = []
    value = auth.run(timeout=10, transport=endpoint, browser=open_, say=said.append)
    path = gsc_env["token"]
    assert value == {"ok": True, "token_path": str(path), "scope": auth.SCOPE, "mode": "0600"}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    stored = json.loads(path.read_text())
    assert stored["schema"] == "thth.gsc.token/1" and stored["refresh_token"] == REFRESH
    assert "access_token" not in stored
    assert not gsc_env["gads_token"].exists()
    assert "Search Console" in said[0]
    form = endpoint.calls[0]["form"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == seen["code_challenge"]
    out = "\n".join(said) + json.dumps(value) + "".join(capsys.readouterr())
    for secret in SECRETS + (form["code_verifier"],):
        assert secret not in out


def test_CLIのauthも秘密を出さない(gsc_env, monkeypatch, capsys):
    endpoint = FakeTokenEndpoint()
    monkeypatch.setattr(auth, "default_transport", endpoint)
    open_, _ = _browser(lambda p: {"code": AUTH_CODE, "state": p["state"]})
    monkeypatch.setattr("webbrowser.open", open_)
    rc = cli.main(["gsc", "auth", "--timeout", "10"])
    out = capsys.readouterr()
    assert rc == 0, out
    assert "600" in out.out and "thth gsc sites" in out.out
    for secret in SECRETS:
        assert secret not in out.out + out.err


@pytest.mark.parametrize("reply,endpoint,code", [
    (lambda p: {"code": AUTH_CODE, "state": "other"}, FakeTokenEndpoint(), "gsc_auth_state_mismatch"),
    (lambda p: {"error": "access_denied", "state": p["state"]}, FakeTokenEndpoint(), "gsc_auth_denied"),
    (lambda p: {"code": AUTH_CODE, "state": p["state"]},
     FakeTokenEndpoint(exchange=(200, {"access_token": ACCESS})), "gsc_no_refresh_token"),
    (lambda p: {"code": AUTH_CODE, "state": p["state"]},
     FakeTokenEndpoint(exchange=(400, {"error": "invalid_grant", "error_description": AUTH_CODE})),
     "gsc_auth_exchange_failed"),
])
def test_認可の流れの断りはgscの符丁(gsc_env, reply, endpoint, code):
    open_, _ = _browser(reply)
    with pytest.raises(GscRefused) as caught:
        auth.run(timeout=10, transport=endpoint, browser=open_, say=lambda _: None)
    assert caught.value.code == code and AUTH_CODE not in str(caught.value)
    assert not gsc_env["token"].exists()


def test_戻りが来なければ時間切れ(gsc_env):
    with pytest.raises(GscRefused) as caught:
        auth.run(timeout=0.3, transport=FakeTokenEndpoint(), browser=lambda url: None, say=lambda _: None)
    assert caught.value.code == "gsc_auth_timeout"


def test_CLIの認可の断りは3つの行を出す(gsc_env, monkeypatch, capsys):
    monkeypatch.setattr(auth, "default_transport", FakeTokenEndpoint())
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    rc = cli.main(["gsc", "auth", "--timeout", "0"])
    err = capsys.readouterr().err
    assert rc == 2 and err.startswith("gsc_auth_timeout: ")
    assert "通すには: " in err and "もっと良く: " in err


def test_既定の置き場_clientはgadsと同じ_鍵は別(monkeypatch, tmp_path):
    monkeypatch.delenv(auth.CLIENT_ENV, raising=False)
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert auth.client_path() == str(tmp_path / ".config" / "thth" / "google-ads-client.json")
    assert auth.token_path() == str(tmp_path / ".config" / "thth" / "google-gsc-token.json")
    assert auth.client_path() == gads_auth.client_path()
    assert auth.token_path() != gads_auth.token_path()


def test_鍵の置き場は環境変数で差し替える(monkeypatch, tmp_path):
    monkeypatch.setenv("THTH_GOOGLE_GSC_TOKEN", str(tmp_path / "x.json"))
    assert auth.token_path() == str(tmp_path / "x.json")


def test_refreshは命令ごとに1回(gsc_env):
    auth.save_token({"refresh_token": REFRESH})
    calls = []

    def transport(method, url, headers, body):
        if url.startswith("https://oauth2.googleapis.com/"):
            calls.append(("token", dict(urllib.parse.parse_qsl(body.decode()))))
            return 200, json.dumps({"access_token": ACCESS}).encode()
        calls.append(("api", method, url, headers))
        return 200, json.dumps({"siteEntry": []}).encode()

    client = api.Client(transport=transport)
    client.sites()
    client.sites()
    token_calls = [c for c in calls if c[0] == "token"]
    assert len(token_calls) == 1
    assert token_calls[0][1]["refresh_token"] == REFRESH and token_calls[0][1]["grant_type"] == "refresh_token"
    _, method, url, headers = [c for c in calls if c[0] == "api"][0]
    assert method == "GET" and url == "https://www.googleapis.com/webmasters/v3/sites"
    assert headers["Authorization"] == f"Bearer {ACCESS}"


def test_鍵が無ければ断る(gsc_env):
    with pytest.raises(GscRefused) as caught:
        api.Client(transport=FakeTokenEndpoint()).sites()
    assert caught.value.code == "gsc_token_missing"


def test_client_jsonが無ければ断る(gsc_env):
    gsc_env["client"].unlink()
    with pytest.raises(GscRefused) as caught:
        auth.load_client()
    assert caught.value.code == "gsc_client_missing"


def test_網に届かなければ符丁だけ(monkeypatch):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.URLError(f"cannot reach {req.full_url}")
    monkeypatch.setattr(wire.google_oauth.httpsafe, "urlopen", boom)
    with pytest.raises(GscRefused) as caught:
        wire.default_transport("GET", "https://www.googleapis.com/webmasters/v3/sites?x=secret-query", {}, None)
    assert str(caught.value) == "gsc_unavailable" and caught.value.__cause__ is None


def test_網の口はhttpsafeのurlopenの1か所():
    """`thth/google_oauth.py` の外の gsc の口は urlopen を呼ばない（網は `http_request` の 1 か所）。"""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "thth"
    for path in sorted((root / "gsc").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "urlopen" not in text and "http.client" not in text and "socket" not in text, path.name
    shared = (root / "google_oauth.py").read_text(encoding="utf-8")
    assert shared.count("urlopen(") == 1 and "httpsafe.urlopen(" in shared
