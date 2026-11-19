"""Google ビジネス プロフィールの口の網と断り（`auth.py`・`api.py` が使う）。

**網に出るのはここの `default_transport` の 1 か所**（中身は `thth.google_oauth.http_request`・`thth.httpsafe` の作法:
別ホストへのリダイレクトを追わない・https だけ）。試験は `transport` を差し替える（本物の網に出ない）。

`transport(method, url, headers, body) -> (status, body_bytes)`。HTTP の 4xx・5xx も (status, body) で返す。
届かない（DNS・接続・時間切れ）は `GbpRefused("gbp_unavailable")`。

**秘密を出さない**: 断りの文は符丁と静的な語（Google の理由の名前・API の名前・HTTP の数）だけ。応答の本文・URL・
トークン・client_secret は断りに入れない（`GbpRefused.detail` は形を検査した語だけ）。
"""
from __future__ import annotations

import re

from .. import google_oauth

TIMEOUT = 60
# 断りの符丁（この口が出すもの全部・目録 `thth/refusal_catalog_part_gbp.py` に 3 つがある）。
REASONS = frozenset((
    # 認可と置き場（`thth/google_oauth.py` の `Kit.deny` が頭 gbp_ を付けて出す）
    "gbp_client_missing", "gbp_client_invalid", "gbp_token_missing", "gbp_token_invalid",
    "gbp_token_revoked", "gbp_token_refresh_failed", "gbp_auth_denied", "gbp_auth_timeout",
    "gbp_auth_state_mismatch", "gbp_auth_exchange_failed", "gbp_no_refresh_token", "gbp_scope_missing",
    # 引数の形
    "gbp_location_invalid", "gbp_date_invalid", "gbp_date_past", "gbp_time_invalid", "gbp_hours_args_invalid",
    "gbp_post_text_invalid", "gbp_post_url_invalid", "gbp_event_invalid",
    # 店の状態・二段確認
    "gbp_regular_hours_missing", "gbp_digest_mismatch", "gbp_write_unknown",
    # Google の断り
    "gbp_location_unknown", "gbp_permission_denied", "gbp_not_allowlisted", "gbp_api_not_enabled",
    "gbp_authentication_error", "gbp_quota_exceeded", "gbp_request_rejected", "gbp_unavailable",
    "gbp_response_invalid",
))
# 詳細に入れてよい語の形（Google の理由の名前・API の名前・HTTP の数・静的な語）。
_DETAIL = re.compile(r"[A-Za-z0-9_.: -]{1,80}\Z")


class GbpRefused(Exception):
    """ビジネス プロフィールの口の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None):
        if code not in REASONS:
            code = "gbp_request_rejected"
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
        raise GbpRefused("gbp_unavailable") from None
