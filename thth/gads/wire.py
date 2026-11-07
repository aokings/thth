"""Google 広告の口の網と断り（`auth.py`・`api.py` が使う）。

**網に出るのはここの `default_transport` の 1 か所**（`thth.httpsafe` の作法: 別ホストへのリダイレクトを追わない・
https だけ）。試験は `transport` を差し替える（本物の網に出ない）。

`transport(method, url, headers, body) -> (status, body_bytes)`。HTTP の 4xx・5xx も (status, body) で返す
（読み手が Google の断りの JSON を読む）。届かない（DNS・接続・時間切れ）は `GadsRefused("gads_unavailable")`。

**秘密を出さない**: 断りの文は符丁と静的な語（Google の enum の名前・HTTP の数）だけ。応答の本文・URL・
トークン・client_secret は断りに入れない（`GadsRefused.detail` は形を検査した語だけ）。
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request

from .. import httpsafe

TIMEOUT = 60
# 断りの符丁（この口が出すもの全部・目録 `thth/refusal_catalog_part7.py` に 3 つがある）。
REASONS = frozenset((
    "gads_client_missing", "gads_client_invalid", "gads_token_missing", "gads_token_invalid",
    "gads_token_revoked", "gads_token_refresh_failed", "gads_auth_denied", "gads_auth_timeout",
    "gads_auth_state_mismatch", "gads_auth_exchange_failed", "gads_no_refresh_token",
    "gads_customer_invalid", "gads_customer_unknown", "gads_customer_not_enabled",
    "gads_seed_empty", "gads_seed_too_many", "gads_url_invalid", "gads_lang_unknown", "gads_geo_unknown",
    "gads_date_invalid", "gads_authentication_error", "gads_authorization_error", "gads_project_not_approved",
    "gads_quota_exceeded", "gads_request_rejected", "gads_unavailable", "gads_response_invalid",
))
# 詳細に入れてよい語の形（Google の enum の名前・HTTP の数・静的な語）。
_DETAIL = re.compile(r"[A-Za-z0-9_.: -]{1,80}\Z")


class GadsRefused(Exception):
    """Google 広告の口の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None):
        if code not in REASONS:
            code = "gads_request_rejected"
        self.code = code
        self.detail = detail if isinstance(detail, str) and _DETAIL.fullmatch(detail) else None
        super().__init__(code)

    def __str__(self) -> str:
        return f"{self.code}（{self.detail}）" if self.detail else self.code


def default_transport(method: str, url: str, headers: dict, body: bytes | None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with httpsafe.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(1_000_000)
        except Exception:  # 本文が読めなくても数だけで判断する
            raw = b""
        finally:
            exc.close()
        return exc.code, raw
    except (urllib.error.URLError, OSError, ValueError):
        # 例外の文には URL や本文が入りうるので、符丁だけにする（from None で連鎖も切る）。
        raise GadsRefused("gads_unavailable") from None
