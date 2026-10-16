"""OAuth 1.0a の署名（RFC 5849・HMAC-SHA1）。**標準ライブラリだけ**（設計 4.0 S2 §2.2）。

はてなの AtomPub と OAuth の口（`initiate`・`token`）に付ける `Authorization: OAuth …` を作る。
`pyproject` の `dependencies = []` を守るため、requests_oauthlib は使わない。

- `percent_encode`: §3.6（英数と `-._~` 以外を `%XX`・大文字）。
- `base_string`: §3.4.1（URL の query・form の body・`oauth_*` を全部集め、鍵と値を encode →
  並べ替え → `&` で連結）。XML の body は入れない（呼び手が `body_params` に渡さない）。
- `sign_hmac_sha1`: §3.4.2（鍵は `encode(consumer_secret)&encode(token_secret)`・base64）。
- `authorization_header`: §3.5.1（`OAuth realm="", …`）。

秘密の値（consumer secret・token secret）は返り値の署名にしか効かない。ヘッダに入るのは
consumer key・token・署名だけ（ヘッダそのものはログに出さない・`redact` が `Authorization:` を消す）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse

SIGNATURE_METHOD = "HMAC-SHA1"
VERSION = "1.0"
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def percent_encode(value) -> str:
    """RFC 5849 §3.6。UTF-8 にしてから、英数と `-._~` 以外を `%XX`（大文字の 16 進）に。"""
    raw = str(value).encode("utf-8")
    return "".join(chr(b) if chr(b) in _UNRESERVED else "%{:02X}".format(b) for b in raw)


def base_string_uri(url: str) -> str:
    """§3.4.1.2: scheme と host を小文字に・既定のポートを落とし・query と fragment を外す。"""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return f"{scheme}://{host}{parts.path or '/'}"


def _pairs(params):
    if params is None:
        return []
    items = params.items() if isinstance(params, dict) else params
    return [(str(k), "" if v is None else str(v)) for k, v in items]


def normalized_parameters(url: str, params=None) -> str:
    """§3.4.1.3: URL の query（form として decode）と `params`（decode 済みの鍵と値）を集めて
    encode → 鍵の順・同じ鍵は値の順に並べ → `k=v` を `&` で連結。`oauth_signature` は除く。"""
    query = urllib.parse.urlsplit(url).query
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True) if query else []
    pairs = list(pairs) + _pairs(params)
    encoded = sorted((percent_encode(k), percent_encode(v)) for k, v in pairs if k != "oauth_signature")
    return "&".join(f"{k}={v}" for k, v in encoded)


def base_string(method: str, url: str, params=None) -> str:
    """§3.4.1.1。`params` は form の body と `oauth_*`（decode 済み）。query は `url` から取る。"""
    return "&".join((percent_encode(method.upper()), percent_encode(base_string_uri(url)),
                     percent_encode(normalized_parameters(url, params))))


def sign_hmac_sha1(base: str, consumer_secret: str, token_secret: str | None = None) -> str:
    """§3.4.2。鍵は `encode(consumer_secret)&encode(token_secret)`（token secret が無ければ空）。"""
    key = percent_encode(consumer_secret or "") + "&" + percent_encode(token_secret or "")
    digest = hmac.new(key.encode("utf-8"), base.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def oauth_params(*, consumer_key, token=None, nonce=None, timestamp=None, extra_oauth=None) -> dict:
    """署名の前の `oauth_*`（`oauth_signature` は入らない）。"""
    out = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce if nonce is not None else secrets.token_hex(16),
        "oauth_signature_method": SIGNATURE_METHOD,
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_version": VERSION,
    }
    if token:
        out["oauth_token"] = token
    for key, value in (extra_oauth or {}).items():
        if not str(key).startswith("oauth_"):
            raise ValueError("extra_oauth_key_invalid")
        out[key] = value
    return out


def authorization_header(*, consumer_key, consumer_secret, token=None, token_secret=None,
                         method, url, body_params=None, extra_oauth=None,
                         nonce=None, timestamp=None, realm="") -> str:
    """§3.5.1 の `Authorization` の値（`OAuth realm="", oauth_…="…", …`）。

    `body_params` は `application/x-www-form-urlencoded` の body の鍵と値（decode 済み）だけ。
    XML の body は渡さない（base に入れない・§3.4.1.3.1）。
    """
    params = oauth_params(consumer_key=consumer_key, token=token, nonce=nonce, timestamp=timestamp,
                          extra_oauth=extra_oauth)
    base = base_string(method, url, list(params.items()) + _pairs(body_params))
    params["oauth_signature"] = sign_hmac_sha1(base, consumer_secret, token_secret)
    fields = [f'realm="{percent_encode(realm)}"'] + [
        f'{percent_encode(k)}="{percent_encode(v)}"' for k, v in sorted(params.items())]
    return "OAuth " + ", ".join(fields)


def parse_header(value: str) -> dict:
    """`OAuth k="v", …` を decode した dict に（試験と診断のため）。"""
    if not isinstance(value, str) or not value.startswith("OAuth "):
        raise ValueError("not_oauth_header")
    out = {}
    for part in value[len("OAuth "):].split(","):
        key, _, raw = part.strip().partition("=")
        out[urllib.parse.unquote(key)] = urllib.parse.unquote(raw.strip('"'))
    return out
