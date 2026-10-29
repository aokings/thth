"""Graph API のエラーの振り分け（設計 MetaAPI 計画 B-1・段 2）。

`adapters/facebook.py` の `graph_error`・`is_permission_error`・権限系の code の表を抜き出し、
量の制限の code を足した。振り分けは 4 つ:

| 語 | 意味 | 例 |
|---|---|---|
| `rate_limited` | Meta が量の制限で断った（**出ていない**）。待ってから取り直す | code 4・17・32・80000・80001・80002・80004 |
| `permission` | 権限が無い・鍵が無効（出ていない）。`thth auth` のやり直し | code 10・200〜299・190 |
| `definite` | ほかの 4xx（Meta が断った＝変わっていない） | code 100 など |
| `ambiguous` | 5xx・時間切れ（変わったか分からない） | |

量の制限の code（**L2**: developers.facebook.com/docs/graph-api/overview/rate-limiting の
「Error Codes」: 4 アプリ・17 利用者・32 ページ（ページの鍵）・80000 広告の成績・80001 ページ・
80002 Instagram・80004 広告の管理）。613 などほかの code は**未確認**なので入れない（入れるときは
出典を書いてここに足す）。
"""
from __future__ import annotations

import json
import urllib.error

from ..adapters import base

# 権限系の code（L3・Graph API 共通。`threads.py` と同じ一覧）。
PERMISSION_ERROR_CODES = frozenset({10} | set(range(200, 300)))
# 鍵が無効・失効（L3・Graph API 共通の `OAuthException` 190）。
INVALID_TOKEN_CODE = 190
# 量の制限（L2・上の注）。広告を止める操作は、この code でも**待たない**（`urgent`）。
RATE_LIMIT_CODES = frozenset({4, 17, 32, 80000, 80001, 80002, 80004})

RATE_LIMITED = "rate_limited"
PERMISSION = "permission"
DEFINITE = "definite"
AMBIGUOUS = "ambiguous"

# 本文を読んだ結果を例外に覚えさせる名前（同じ例外を 2 度読むと 2 度目は空になるため）。
_CACHE = "_thth_graph_error"


def graph_error(error: urllib.error.HTTPError) -> dict:
    """HTTPError の本文から Graph の `error`（`code`・`error_subcode`・`message`）を読む。

    読めなければ空の dict。**本文はここで読み切って閉じる。** 読んだ結果は例外に覚えさせ、
    2 度目からはそれを返す（共通層が量の制限を見るために先に読み、adapter がもう一度読む）。
    """
    cached = getattr(error, _CACHE, None)
    if isinstance(cached, dict):
        return cached
    try:
        raw = error.read(65537)
    except Exception:   # noqa: BLE001 — 本文が読めないのは「分からない」
        raw = b""
    finally:
        try:
            error.close()
        except Exception:   # noqa: BLE001
            pass
    try:
        err = json.loads(raw or b"{}").get("error")
    except (ValueError, AttributeError):
        err = None
    out = err if isinstance(err, dict) else {}
    try:
        setattr(error, _CACHE, out)
    except Exception:   # noqa: BLE001 — 覚えられなくても読んだ値は返す
        pass
    return out


def _code(err: dict):
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def is_permission_error(err: dict) -> bool:
    message = str(err.get("message") or "").lower()
    code = _code(err)
    return "permission" in message or (code is not None and code in PERMISSION_ERROR_CODES)


def is_rate_limited(err: dict) -> bool:
    code = _code(err)
    return code is not None and code in RATE_LIMIT_CODES


def classify(http_status: int | None, err: dict) -> str:
    """4 つの語のどれか。**量の制限を権限より先に見る**（code 4・17・32 は権限の表に無いが、
    文面に permission が混ざっても量の制限として待つ）。"""
    if is_rate_limited(err):
        return RATE_LIMITED
    status = http_status or 0
    if 400 <= status < 500 and (is_permission_error(err) or _code(err) == INVALID_TOKEN_CODE):
        return PERMISSION
    if 400 <= status < 500:
        return DEFINITE
    return AMBIGUOUS


class RateLimited(base.AdapterError):
    """Meta が量の制限で断った、または前に断られて待っている間に呼ぼうとした。

    **要求は出ていない**（Meta が受けなかった・こちらが投げなかった）。`failure` は `rate_limited`。
    `urgent` は「待たない」印の呼び出し（広告を止める操作）で、待ちの間でも投げ、断られたら
    すぐこの例外で返る（呼び手が hub への知らせに落とす）。
    """

    def __init__(self, message, *, code=None, http_status=None, wait_minutes=None,
                 until=None, urgent=False, sent=True):
        super().__init__(message)
        self.failure = RATE_LIMITED
        self.code = code
        self.http_status = http_status
        self.wait_minutes = wait_minutes
        self.until = until
        self.urgent = urgent
        # False は「待ちの間なので投げなかった」（網に出ていない）。
        self.sent = sent
