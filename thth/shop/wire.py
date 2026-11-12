"""店を読む口の網と断り。

**網に出るのはここの `default_transport` の 1 か所**で、打つのは **GET だけ**、送り先は **`BASE_HOST` の定数だけ**
（利用者の入力・台帳の値で変わらない）。別ホストへのリダイレクトは `thth.httpsafe` が追わない。再試行しない。
試験は `transport` を差し替える（本物の網に出ない）。

`transport(method, url, headers) -> (status, body_bytes)`。4xx・5xx も (status, body) で返す。
届かないときは `StoreRefused("store_unavailable")`。

**秘密を出さない**: 断りの文は符丁と静的な語（BASE の `type` の形を検査したもの・HTTP の数）だけ。応答の本文・
鍵は断りに入れない。
"""
from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe

BASE_HOST = "apiv2.thebase.com"
BASE_ORIGIN = "https://" + BASE_HOST
# 呼んでよい道（注文 `/api/orders` などは入れない・設計 §2）。
BASE_PATHS = frozenset(("/api/user", "/api/items"))
TIMEOUT = 30
MAX_BODY = 5_000_000

# 断りの符丁（この口が出すもの全部・目録 `thth/refusal_catalog_part9.py` に 3 つがある）。
REASONS = frozenset((
    "store_args_invalid", "store_name_taken", "store_missing", "store_ledger_invalid",
    "store_token_missing", "store_token_input_unsafe", "store_token_empty", "store_token_expired",
    "store_token_rejected", "store_scope_missing", "store_shop_mismatch",
    "store_api_changed", "store_unavailable", "store_request_rejected",
    "store_read_incomplete", "store_draft_nothing", "store_draft_exists", "store_draft_account",
    "store_draft_rejected",
    # 監査 5.15.0 の直し（11/12）。
    "store_token_expires_past", "store_draft_no_baseline", "store_not_open", "store_item_changed",
    "store_remove_unconfirmed", "store_remove_unsafe", "store_log_unavailable",
))
_DETAIL = re.compile(r"[A-Za-z0-9_./: -]{1,80}\Z")


class StoreRefused(Exception):
    """店を読む口の断り。`code` は `REASONS` のどれか、`detail` は形を検査した静的な語（無ければ None）。"""

    def __init__(self, code: str, detail: str | None = None, **extra):
        if code not in REASONS:
            code = "store_request_rejected"
        self.code = code
        self.detail = detail if isinstance(detail, str) and _DETAIL.fullmatch(detail) else None
        self.extra = extra
        super().__init__(code)

    def __str__(self) -> str:
        return f"{self.code}（{self.detail}）" if self.detail else self.code


def check_url(method: str, url: str) -> None:
    """GET・https・定数のホスト・許した道だけ。外れたら打たない（柵）。"""
    parts = urllib.parse.urlsplit(url)
    if (method != "GET" or parts.scheme != "https" or parts.hostname != BASE_HOST or parts.port is not None
            or parts.username or parts.password or parts.path not in BASE_PATHS or parts.fragment):
        raise StoreRefused("store_request_rejected", "blocked_request")


def default_transport(method: str, url: str, headers: dict):
    check_url(method, url)
    request = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with httpsafe.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(100_000)
        except Exception:  # noqa: BLE001 — 本文が読めなくても数で決める
            raw = b""
        finally:
            exc.close()
        return exc.code, raw
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        # `http.client.IncompleteRead`（応答の途中で切れた）も `HTTPException` の仲間（監査 5.15.0 気づき 6）。
        raise StoreRefused("store_unavailable") from None
