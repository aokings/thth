"""ビジネス プロフィールの OAuth（`thth gads` と同じ Desktop の client・scope は `business.manage`）。

流れの中身は `thth/google_oauth.py`。ここはビジネス プロフィールの `Kit` と薄い口だけ。

- client の JSON: gads と同じ。`$THTH_GOOGLE_ADS_CLIENT`、無ければ `~/.config/thth/google-ads-client.json`
  （Cloud の project は GBP API の許可リストに載ったもの。4 つの API を有効にしておく）。
- refresh token: gads・gsc と**別のファイル**。`$THTH_GOOGLE_GBP_TOKEN`、無ければ
  `~/.config/thth/google-gbp-token.json`（600）。scope が違う認可を 1 つのファイルに混ぜない。
- scope の `business.manage` は読むのも書くのも含む 1 つだけ（読むだけの scope は Google に無い）。書く命令は
  二段確認で縛る（`cli.py`）。
"""
from __future__ import annotations

import os

from .. import google_oauth
from ..gads import auth as gads_auth
from .wire import GbpRefused, default_transport

SCOPE = "https://www.googleapis.com/auth/business.manage"
CLIENT_ENV = gads_auth.CLIENT_ENV
DEFAULT_CLIENT = gads_auth.DEFAULT_CLIENT
TOKEN_ENV = "THTH_GOOGLE_GBP_TOKEN"
DEFAULT_TOKEN = os.path.join("~", ".config", "thth", "google-gbp-token.json")
TOKEN_SCHEMA = "thth.gbp.token/1"
AUTH_TIMEOUT = google_oauth.AUTH_TIMEOUT

KIT = google_oauth.Kit(prefix="gbp", label="Google ビジネス プロフィール", scope=SCOPE, client_env=CLIENT_ENV,
                       default_client=DEFAULT_CLIENT, token_env=TOKEN_ENV, default_token=DEFAULT_TOKEN,
                       token_schema=TOKEN_SCHEMA, refused=GbpRefused)


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


def token_scopes(token: dict) -> list[str]:
    """token のファイルに残した scope の語（認可のときに Google が返したもの・秘密でない）。"""
    scope = token.get("scope") if isinstance(token, dict) else None
    return scope.split() if isinstance(scope, str) else []


def run(*, open_browser=True, timeout=AUTH_TIMEOUT, transport=None, server=None, say=print, browser=None) -> dict:
    """`thth gbp auth` の中身。返す値に秘密は入れない（置き場のパス・scope・時刻だけ）。"""
    return google_oauth.run(KIT, transport=transport or default_transport, open_browser=open_browser,
                            timeout=timeout, server=server, say=say, browser=browser)
