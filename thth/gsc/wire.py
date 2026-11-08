"""Search Console の口の網と断り（`auth.py`・`api.py` が使う）。

**網に出るのはここの `default_transport` の 1 か所**（中身は `thth.google_oauth.http_request`・`thth.httpsafe` の作法:
別ホストへのリダイレクトを追わない・https だけ）。試験は `transport` を差し替える（本物の網に出ない）。

`transport(method, url, headers, body) -> (status, body_bytes)`。HTTP の 4xx・5xx も (status, body) で返す。
届かない（DNS・接続・時間切れ）は `GscRefused("gsc_unavailable")`。

**秘密を出さない**: 断りの文は符丁と静的な語（Google の理由の名前・HTTP の数）だけ。応答の本文・URL（とその
クエリ）・トークン・client_secret は断りに入れない（`GscRefused.detail` は形を検査した語だけ）。
"""
from __future__ import annotations

import re

from .. import google_oauth

TIMEOUT = 60
# 断りの符丁（この口が出すもの全部・目録 `thth/refusal_catalog_part8.py` に 3 つがある）。
REASONS = frozenset((
    "gsc_client_missing", "gsc_client_invalid", "gsc_token_missing", "gsc_token_invalid",
    "gsc_token_revoked", "gsc_token_refresh_failed", "gsc_auth_denied", "gsc_auth_timeout",
    "gsc_auth_state_mismatch", "gsc_auth_exchange_failed", "gsc_no_refresh_token",
    "gsc_site_invalid", "gsc_site_unknown", "gsc_permission_denied", "gsc_date_invalid", "gsc_dim_invalid",
    "gsc_page_prefix_invalid", "gsc_query_contains_invalid", "gsc_url_invalid", "gsc_inspect_url_outside",
    "gsc_authentication_error", "gsc_api_not_enabled", "gsc_quota_exceeded", "gsc_request_rejected",
    "gsc_unavailable", "gsc_response_invalid",
))
# 詳細に入れてよい語の形（Google の理由の名前・HTTP の数・静的な語）。
_DETAIL = re.compile(r"[A-Za-z0-9_.: -]{1,80}\Z")


class GscRefused(Exception):
    """Search Console の口の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None):
        if code not in REASONS:
            code = "gsc_request_rejected"
        self.code = code
        self.detail = detail if isinstance(detail, str) and _DETAIL.fullmatch(detail) else None
        super().__init__(code)

    def __str__(self) -> str:
        return f"{self.code}（{self.detail}）" if self.detail else self.code


def default_transport(method: str, url: str, headers: dict, body: bytes | None):
    try:
        return google_oauth.http_request(method, url, headers, body, timeout=TIMEOUT)
    except google_oauth.Unreachable:
        # 例外の文には URL や本文が入りうるので、符丁だけにする（from None で連鎖も切る）。
        raise GscRefused("gsc_unavailable") from None
