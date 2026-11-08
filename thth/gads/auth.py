"""Google 広告の OAuth（端末のアプリの形・loopback の転送先・PKCE）と refresh token の置き場。

流れの中身は `thth/google_oauth.py`（`thth gsc` と共通）。ここは Google 広告の `Kit`（scope・置き場・符丁の頭）と、
今までの名前のままの薄い口だけ。

- client の JSON（Google の「installed」の形・Cloud Console で落としたもの）: `$THTH_GOOGLE_ADS_CLIENT`、
  無ければ `~/.config/thth/google-ads-client.json`。中身は表示しない。
- refresh token: `$THTH_GOOGLE_ADS_TOKEN`、無ければ `~/.config/thth/google-ads-token.json`。ファイルは 600・
  置き場のディレクトリは作るとき 700。access token は残さない（呼ぶたびに refresh で取る）。
- client_secret・refresh token・access token・認可の code・code_verifier はどこにも出さない。
"""
from __future__ import annotations

import os

from .. import google_oauth
from ..google_oauth import LOOPBACK_HOST, challenge, new_verifier  # noqa: F401 （今までの名前）
from .wire import GadsRefused, default_transport

SCOPE = "https://www.googleapis.com/auth/adwords"
CLIENT_ENV = "THTH_GOOGLE_ADS_CLIENT"
TOKEN_ENV = "THTH_GOOGLE_ADS_TOKEN"
DEFAULT_CLIENT = os.path.join("~", ".config", "thth", "google-ads-client.json")
DEFAULT_TOKEN = os.path.join("~", ".config", "thth", "google-ads-token.json")
TOKEN_SCHEMA = "thth.gads.token/1"
AUTH_TIMEOUT = google_oauth.AUTH_TIMEOUT

KIT = google_oauth.Kit(prefix="gads", label="Google 広告", scope=SCOPE, client_env=CLIENT_ENV,
                       default_client=DEFAULT_CLIENT, token_env=TOKEN_ENV, default_token=DEFAULT_TOKEN,
                       token_schema=TOKEN_SCHEMA, refused=GadsRefused)


def client_path() -> str:
    return google_oauth.client_path(KIT)


def token_path() -> str:
    return google_oauth.token_path(KIT)


def load_client() -> dict:
    return google_oauth.load_client(KIT)


def authorization_url(client: dict, redirect_uri: str, state: str, verifier: str) -> str:
    return google_oauth.authorization_url(KIT, client, redirect_uri, state, verifier)


def loopback_server():
    return google_oauth.loopback_server(KIT)


def wait_for_code(server, state: str, timeout: float) -> str:
    return google_oauth.wait_for_code(KIT, server, state, timeout)


# `default_transport` は呼ぶ時に引く（試験が差し替える）。
def exchange(client: dict, code: str, verifier: str, redirect_uri: str, *, transport=None) -> dict:
    return google_oauth.exchange(KIT, client, code, verifier, redirect_uri, transport=transport or default_transport)


def refresh(client: dict, token: dict, *, transport=None) -> str:
    return google_oauth.refresh(KIT, client, token, transport=transport or default_transport)


def save_token(value: dict, path: str | None = None) -> str:
    return google_oauth.save_token(KIT, value, path)


def load_token() -> dict:
    return google_oauth.load_token(KIT)


def run(*, open_browser=True, timeout=AUTH_TIMEOUT, transport=None, server=None, say=print, browser=None) -> dict:
    """`thth gads auth` の中身。返す値に秘密は入れない（置き場のパス・scope・時刻だけ）。"""
    return google_oauth.run(KIT, transport=transport or default_transport, open_browser=open_browser,
                            timeout=timeout, server=server, say=say, browser=browser)
