"""Search Console の OAuth（`thth gads` と同じ Desktop の client・scope は `webmasters.readonly` だけ）。

流れの中身は `thth/google_oauth.py`。ここは Search Console の `Kit` と薄い口だけ。

- client の JSON: gads と同じ。`$THTH_GOOGLE_ADS_CLIENT`、無ければ `~/.config/thth/google-ads-client.json`
  （Desktop の OAuth クライアント 1 つを 2 つの口で使う。Cloud の project で Search Console API を有効にしておく）。
- refresh token: gads と**別のファイル**。`$THTH_GOOGLE_GSC_TOKEN`、無ければ `~/.config/thth/google-gsc-token.json`（600）。
  scope が違う認可を 1 つのファイルに混ぜない（片方をやり直しても、もう片方が切れない）。
"""
from __future__ import annotations

import os

from .. import google_oauth
from ..gads import auth as gads_auth
from .wire import GscRefused, default_transport

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
CLIENT_ENV = gads_auth.CLIENT_ENV
DEFAULT_CLIENT = gads_auth.DEFAULT_CLIENT
TOKEN_ENV = "THTH_GOOGLE_GSC_TOKEN"
DEFAULT_TOKEN = os.path.join("~", ".config", "thth", "google-gsc-token.json")
TOKEN_SCHEMA = "thth.gsc.token/1"
AUTH_TIMEOUT = google_oauth.AUTH_TIMEOUT

KIT = google_oauth.Kit(prefix="gsc", label="Search Console", scope=SCOPE, client_env=CLIENT_ENV,
                       default_client=DEFAULT_CLIENT, token_env=TOKEN_ENV, default_token=DEFAULT_TOKEN,
                       token_schema=TOKEN_SCHEMA, refused=GscRefused)


def client_path() -> str:
    return google_oauth.client_path(KIT)


def token_path() -> str:
    return google_oauth.token_path(KIT)


def load_client() -> dict:
    return google_oauth.load_client(KIT)


def authorization_url(client: dict, redirect_uri: str, state: str, verifier: str) -> str:
    return google_oauth.authorization_url(KIT, client, redirect_uri, state, verifier)


# `default_transport` は呼ぶ時に引く（試験が差し替える）。
def refresh(client: dict, token: dict, *, transport=None) -> str:
    return google_oauth.refresh(KIT, client, token, transport=transport or default_transport)


def save_token(value: dict, path: str | None = None) -> str:
    return google_oauth.save_token(KIT, value, path)


def load_token() -> dict:
    return google_oauth.load_token(KIT)


def run(*, open_browser=True, timeout=AUTH_TIMEOUT, transport=None, server=None, say=print, browser=None) -> dict:
    """`thth gsc auth` の中身。返す値に秘密は入れない（置き場のパス・scope・時刻だけ）。"""
    return google_oauth.run(KIT, transport=transport or default_transport, open_browser=open_browser,
                            timeout=timeout, server=server, say=say, browser=browser)
