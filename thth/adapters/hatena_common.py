"""はてなの口（ブログ・フォトライフ）が共有する認証の材料と、署名した 1 回の HTTP（設計 4.0 S3 §6.1）。

`hatenablog`（S2）から切り出した。ブログとフォトライフは入口のホストと XML の名前空間が違うので
クラスは別にし、ここには関数だけを置く:

- `load_credentials(account_cfg, principal_cfg) -> (token, consumer)`: 主体の token と consumer を読んで
  形を確かめ、値を `redact` に登録する。作れなければ `hatena_publish.AdapterUnavailable(code)`。
- `request_signed(method, url, *, headers, body, timeout, consumer, token)`: OAuth 1.0a の
  `Authorization` を付けて `httpsafe.urlopen` で 1 回だけ投げる（再試行しない・sleep しない）。
  `(status, headers, bytes)`。urllib の例外はそのまま上げる。応答が上限を超えれば `ResponseTooLarge`。

秘密の値はどこにも出さない（例外の文は呼び手が `scrub` を通す）。
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request

from .. import httpsafe
from .. import redact as redact_mod

TOKEN_SCHEMA = "thth.hatena-oauth/1"
CONSUMER_SCHEMA = "thth.hatena-consumer/1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_CONTACT = "https://thth.me/"


class ResponseTooLarge(Exception):
    """応答が上限（4 MiB）を超えた。"""


class ResponseInvalid(Exception):
    """応答の XML が読めない・形が違う。"""


def unavailable(code):
    from ..hatena_publish import AdapterUnavailable
    return AdapterUnavailable(code)


def _good(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(
        ord(c) < 32 or ord(c) == 127 or c.isspace() for c in value)


def read_consumer(path) -> dict:
    """consumer のファイルを読む。無い → `consumer_missing`・形が違う → `consumer_invalid`。"""
    if not isinstance(path, str) or not path or not os.path.exists(path):
        raise unavailable("consumer_missing")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise unavailable("consumer_invalid") from None
    if (not isinstance(data, dict) or data.get("schema") != CONSUMER_SCHEMA
            or not _good(data.get("consumer_key")) or not _good(data.get("consumer_secret"))):
        raise unavailable("consumer_invalid")
    return data


def check_token(token, hatena_id) -> dict:
    if (not isinstance(token, dict) or token.get("schema") != TOKEN_SCHEMA
            or not _good(token.get("oauth_token")) or not _good(token.get("oauth_token_secret"))
            or token.get("hatena_id") != hatena_id):
        raise unavailable("token_invalid")
    return token


def load_credentials(account_cfg, principal_cfg) -> tuple:
    """`(token, consumer)`。`auth` が oauth でない・token や consumer が無い／形が違う → `AdapterUnavailable`。"""
    from .. import accounts
    if (principal_cfg or {}).get("auth", "oauth") != "oauth":
        raise unavailable("auth_unsupported")
    try:
        token = accounts.load_token(account_cfg)
    except (ValueError, OSError):
        # 読めない（権限・壊れたファイル・ディレクトリ）も「使えない」。timer の run を落とさない。
        raise unavailable("token_invalid") from None
    if token is None:
        raise unavailable("token_missing")
    check_token(token, principal_cfg.get("hatena_id"))
    consumer = read_consumer(principal_cfg.get("consumer"))
    for value in (consumer["consumer_key"], consumer["consumer_secret"],
                  token["oauth_token"], token["oauth_token_secret"]):
        redact_mod.register_secret(value)
    return token, consumer


def user_agent(contact) -> str:
    from .. import __version__
    return f"thth/{__version__} (+{contact or DEFAULT_CONTACT})"


def scrub(text, *secrets) -> str:
    out = str(text)
    for value in secrets:
        if value:
            out = out.replace(value, "***")
    return redact_mod.redact(out) or ""


class PublicHTTPError(Exception):
    """認証なしの公開の読む口（件数・エントリー情報・スター・4.0 S6）の失敗。`code` は静的な符丁。"""

    def __init__(self, code, status=None):
        super().__init__(code)
        self.code = code
        self.status = status


PUBLIC_READ_TIMEOUT = 30
PUBLIC_MAX_BYTES = 1024 * 1024


def public_http_error(status) -> str:
    if status == 429:
        return "provider_rate_limited"
    return f"hatena_http_{status}"


def public_get(url, *, contact=None, timeout=PUBLIC_READ_TIMEOUT) -> bytes:
    """認証なしの GET を 1 回だけ（設計 4.0 S6 §4.2）。**`Authorization` は付けない**（別ホストに token を
    送らない）。`User-Agent` に連絡先。`httpsafe.urlopen`（平文不可・同一 origin のリダイレクトだけ）。
    再試行しない・sleep しない。200 以外・つながらない・大きすぎる → `PublicHTTPError(符丁)`。"""
    headers = {"Accept": "application/json", "User-Agent": user_agent(contact)}
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with httpsafe.urlopen(req, timeout=min(timeout, PUBLIC_READ_TIMEOUT)) as response:
            status = response.status
            raw = response.read(PUBLIC_MAX_BYTES + 1)
    except httpsafe.RedirectBlocked:
        raise PublicHTTPError("redirect_blocked") from None
    except urllib.error.HTTPError as exc:
        raise PublicHTTPError(public_http_error(exc.code), exc.code) from None
    except httpsafe.EndpointRejected:
        raise PublicHTTPError("endpoint_rejected") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
        raise PublicHTTPError("hatena_transport_failed") from None
    if len(raw) > PUBLIC_MAX_BYTES:
        raise PublicHTTPError("response_too_large")
    if status != 200:
        raise PublicHTTPError(public_http_error(status), status)
    return raw


def public_json(url, *, contact=None, timeout=PUBLIC_READ_TIMEOUT):
    """`public_get` の JSON。読めなければ `PublicHTTPError("response_invalid")`。空の body は None。"""
    raw = public_get(url, contact=contact, timeout=timeout)
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PublicHTTPError("response_invalid") from None


def request_signed(method, url, *, headers, body, timeout, consumer, token):
    """署名して 1 回だけ投げる。`(status, headers, bytes)`。

    `consumer`・`token` は `load_credentials` の返す dict。`headers` は `Authorization` 以外
    （`Accept`・`User-Agent`・`Content-Type`）。XML の body は OAuth の base string に入れない。
    """
    from .. import oauth1
    out = dict(headers or {})
    out["Authorization"] = oauth1.authorization_header(
        consumer_key=consumer["consumer_key"], consumer_secret=consumer["consumer_secret"],
        token=token["oauth_token"], token_secret=token["oauth_token_secret"], method=method, url=url)
    req = urllib.request.Request(url, data=body, method=method, headers=out)
    with httpsafe.urlopen(req, timeout=timeout) as response:
        status = response.status
        response_headers = dict(response.headers.items()) if response.headers is not None else {}
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge("response_too_large")
    return status, response_headers, raw
