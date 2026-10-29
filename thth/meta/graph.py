"""`graph.facebook.com` を呼ぶ 1 か所（設計 MetaAPI 計画 B-1・段 2）。

`adapters/facebook.py` の宛先の検査（`graph_origin`）・`_request`・版の定数を抜き出し、次を足した:

- **`appsecret_proof`**（`access_token` を app secret で HMAC-SHA256 した 16 進・**L2**:
  developers.facebook.com/docs/graph-api/securing-requests）を、app secret が分かるときは
  全部の呼び出しに付ける。必須なのはシステムユーザーの鍵を作る呼び出しだけで、普通の呼び出しで
  必須になるのはアプリの設定「Require App Secret」を入れたとき（L2・監査 B-5）。全部に付けて
  おけば、その設定をいつでも入れられる。app secret は `app.env` の `META_APP_SECRET`
  （`load_app_secret()`・値は `redact` に登録し、どこにも出さない）。
- **量の制限**: 応答のヘッダを `usage.observe()` に渡し、量の制限の code で断られたら
  `usage.note_rate_limited()` で待ちを残して `errors.RateLimited` を上げる。待ちの間は網に出る
  前に断る。**`urgent=True`（「待たない」印・広告を止める操作が使う）はこの待ちを見ず、断られたら
  すぐ返す。**

**秘密の扱い**は今の作法のまま: 鍵は GET・DELETE はクエリ、POST は本文。例外文に URL を入れない
（呼び手の `_scrub()`）。`urllib` の例外は量の制限のときを除いてそのまま上げる（4xx と 5xx を
呼び手が区別する）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .. import appenv, httpsafe
from .. import redact as redact_mod
from ..adapters import base
from . import errors, usage

# **版は 1 か所**（L2: changelog の最新 v26.0・2026-07-29）。上げるときはここだけを直す。
GRAPH_VERSION = "v26.0"
DEFAULT_BASE_URL = "https://graph.facebook.com"
# 試験だけが loopback へ向ける（本番は既定のまま）。
BASE_URL_ENV = "THTH_FACEBOOK_BASE_URL"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def origin() -> str:
    """宛先の origin。**公式の HTTPS か、明示した loopback の試験先だけ。**

    `oauth._auth_graph_base_url()`（Threads）と同じ作り。環境変数の値がほかのホストを
    指していたら、鍵を持って出ていく前に断る。
    """
    raw = os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        valid = (not any(ord(c) < 32 or ord(c) == 127 for c in raw)
                 and not parsed.username and not parsed.password and not parsed.query
                 and not parsed.fragment and parsed.path in ("", "/")
                 and ((parsed.scheme == "https" and parsed.hostname == "graph.facebook.com"
                       and parsed.port in (None, 443))
                      or (parsed.scheme == "http"
                          and parsed.hostname in ("127.0.0.1", "localhost", "::1"))))
    except ValueError:
        valid = False
    if not valid:
        raise base.AdapterError(
            "facebook_endpoint_invalid: 宛先は https://graph.facebook.com か"
            " loopback の試験先だけを使えます")
    return raw


def endpoint(base_url: str, path: str) -> str:
    """`<origin>/<版><path>`（クエリは付けない）。"""
    return f"{base_url}/{GRAPH_VERSION}{path}"


def appsecret_proof(access_token: str, app_secret: str) -> str:
    """`HMAC-SHA256(key=app_secret, msg=access_token)` の 16 進（L2: securing-requests）。"""
    return hmac.new(app_secret.encode("utf-8"), access_token.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def load_app_secret() -> str | None:
    """`app.env` の `META_APP_SECRET`（無い・読めなければ None）。値は `redact` に登録する。

    None のときは `appsecret_proof` を付けずに呼ぶ（今までと同じ）。doctor がそのことを言う。
    """
    try:
        _app_id, secret, _config = appenv.load_meta_app_env(log=lambda _line: None)
    except appenv.AppEnvError:
        return None
    except OSError:
        return None
    if not isinstance(secret, str) or not secret:
        return None
    redact_mod.register_secret(secret)
    return secret


class Client:
    """1 本の鍵で `graph.facebook.com` を呼ぶ。口座が分かれば量の制限を state に残す。"""

    def __init__(self, base_url: str, access_token: str, *, app_secret: str | None = None,
                 account: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 usage_path: str | None = None):
        self.base_url = base_url
        self.access_token = access_token or ""
        self.app_secret = app_secret or None
        self.account = account
        # 量の写しの置き場を直に渡す口（広告の口座は `adaccount_state/<名前>/` に置く・段 4）。
        self.usage_path = usage_path
        self.timeout = timeout
        if self.access_token:
            redact_mod.register_secret(self.access_token)

    @property
    def proof(self) -> str | None:
        if not (self.app_secret and self.access_token):
            return None
        value = appsecret_proof(self.access_token, self.app_secret)
        redact_mod.register_secret(value)
        return value

    def auth_fields(self) -> dict:
        """鍵（と `appsecret_proof`）。multipart を自分で組む呼び手（写真）が本文に入れる。"""
        fields = {"access_token": self.access_token}
        proof = self.proof
        if proof:
            fields["appsecret_proof"] = proof
        return fields

    def _observe(self, headers) -> None:
        if self.account or self.usage_path:
            usage.observe(self.account, headers, path=self.usage_path)

    def request(self, method: str, path: str, *, params: dict | None = None,
                form: dict | None = None, body: bytes | None = None,
                headers: dict | None = None, timeout: float | None = None,
                urgent: bool = False):
        """1 回だけ投げて JSON を返す。鍵は GET・DELETE はクエリ、POST は本文。

        `body` を渡す（multipart）ときは、呼び手が `auth_fields()` を本文に入れる。
        """
        if not urgent and (self.account or self.usage_path):
            wait = usage.waiting(self.account, path=self.usage_path)
            if wait is not None:
                raise errors.RateLimited(
                    f"meta_rate_limited: Meta の量の制限で待っています（あと {wait['remaining_minutes']} 分・"
                    f"{wait.get('until')} まで）。この間は呼びません",
                    code=wait.get("code"), wait_minutes=wait["remaining_minutes"],
                    until=wait.get("until"), sent=False)
        query = dict(params or {})
        data = None
        hdrs = {"Accept": "application/json"}
        if method == "POST":
            if body is not None:
                data = body
            else:
                fields = dict(form or {})
                fields.update(self.auth_fields())
                data = urllib.parse.urlencode(fields).encode("utf-8")
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            query.update(self.auth_fields())
        hdrs.update(headers or {})
        url = endpoint(self.base_url, path)
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with httpsafe.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                response_headers = getattr(resp, "headers", None)
        except urllib.error.HTTPError as exc:
            err = errors.graph_error(exc)
            self._observe(exc.headers)
            if errors.is_rate_limited(err):
                code = err.get("code")
                minutes, until = usage.note_rate_limited(self.account, exc.headers, code,
                                                         path=self.usage_path)
                if urgent:
                    message = (f"meta_rate_limited: Meta の量の制限で断られました（code {code}）。"
                               "待たずに失敗として返します（止める操作は待たない）")
                else:
                    message = (f"meta_rate_limited: Meta の量の制限で断られました（code {code}）。"
                               f"{minutes} 分待ってから次の timer で取り直します（{until} まで）")
                raise errors.RateLimited(message, code=code, http_status=exc.code,
                                         wait_minutes=minutes, until=until, urgent=urgent) from None
            raise
        self._observe(response_headers)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        return json.loads(raw) if raw else {}
