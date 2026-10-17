"""はてなブックマークの REST API の口（設計 4.0 S4 §10）。

S2 の `hatenablog.py` と同じ作法:

- `base.Adapter` は継がない・`adapters.REGISTRY` に載せない。`hatena_bookmark.execute_locked` が
  `hatena_bookmark.ADAPTER_FACTORY or factory` を呼ぶ直前に解決する。
- 認証は主体の OAuth 1.0a（`hatena_common.load_credentials`・ブログと同じ token）。
- HTTP は `httpsafe.urlopen`（同一 origin のリダイレクトだけ・平文不可）。**再試行しない・sleep しない。**
- 断り（4xx）は「付いていない」、5xx・時間切れ・読めない応答は「分からない」（§7 の表）。
- `url` は**クエリ**に、`comment`・`tags` は **form の body**（`application/x-www-form-urlencoded`）に入れ、
  form の鍵と値は OAuth の base string に含める（RFC 5849 §3.4.1.3.1）。`tags` は `tags` を繰り返す
  （公式に形の記述が無い・推測。実機の §15 の 24 で確かめる）。`private` は送らない（公開）。
- 公開のエントリー情報（`entry_info`・柵 2）は**認証を付けない**（別ホストに token を送らない）。

秘密の値はどこにも出さない（例外の文は `scrub` を通す）。
"""
from __future__ import annotations

import dataclasses
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe
from . import hatena_common

BASE = "https://bookmark.hatenaapis.com"
PUBLIC_BASE = "https://b.hatena.ne.jp"
READ_TIMEOUT = 30
WRITE_TIMEOUT = 60
MAX_RESPONSE_BYTES = 1024 * 1024

ResponseTooLarge = hatena_common.ResponseTooLarge


class BookmarkHTTPError(Exception):
    """GET の失敗（404 以外の状態・つながらない・読めない）。`status` は HTTP の番号か None。"""

    def __init__(self, code, status=None):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclasses.dataclass
class BookmarkResult:
    ok: bool
    ambiguous: bool = False
    error: str | None = None
    status: int | None = None
    bookmark: dict | None = None
    missing: bool = False


def factory(account_cfg, principal_cfg):
    """`hatena_bookmark.execute_locked` の既定。作れなければ `AdapterUnavailable(code)`（打たない側）。"""
    token, consumer = hatena_common.load_credentials(account_cfg, principal_cfg)
    return HatenaBookmarkAdapter(account_cfg, principal_cfg, token=token, consumer=consumer)


def _read_limited(response) -> bytes:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge("response_too_large")
    return raw


def entry_url(url: str) -> str:
    """公開のエントリー情報（軽い版）の URL。対象の URL はクエリの `url` に入れる（実装で決めた・§18）。"""
    return f"{PUBLIC_BASE}/entry/jsonlite/?" + urllib.parse.urlencode([("url", url)])


def entry_info(url: str, *, contact=None, timeout=READ_TIMEOUT) -> dict | None:
    """公開のエントリー情報を 1 回だけ読む（柵 2・設計 4.0 S4 §4.2）。**認証は付けない。**

    返り: `{"exists", "count", "title", "eid", "users"}`（`users` はブックマークした ID の一覧）。
    はてなにまだエントリーが無いページは `null` が返る → `exists: False`・`count: 0`。
    失敗（4xx・5xx・時間切れ・読めない・大きすぎる）は None（「確かめられなかった」）。
    """
    headers = {"Accept": "application/json", "User-Agent": hatena_common.user_agent(contact)}
    req = urllib.request.Request(entry_url(url), method="GET", headers=headers)
    try:
        with httpsafe.urlopen(req, timeout=min(timeout, READ_TIMEOUT)) as response:
            if response.status != 200:
                return None
            raw = _read_limited(response)
    except (httpsafe.EndpointRejected, httpsafe.RedirectBlocked, urllib.error.URLError, TimeoutError, OSError,
            ValueError, http.client.HTTPException, ResponseTooLarge):
        return None
    try:
        data = json.loads(raw.decode("utf-8")) if raw.strip() else None
    except (UnicodeDecodeError, ValueError):
        return None
    if data is None:
        return {"exists": False, "count": 0, "title": None, "eid": None, "users": []}
    if not isinstance(data, dict):
        return None
    marks = data.get("bookmarks")
    users = sorted({str(row.get("user")) for row in (marks if isinstance(marks, list) else [])
                    if isinstance(row, dict) and isinstance(row.get("user"), str)})
    count = data.get("count")
    return {"exists": True, "count": count if isinstance(count, int) and not isinstance(count, bool) else len(users),
            "title": data.get("title") if isinstance(data.get("title"), str) else None,
            "eid": data.get("eid") if isinstance(data.get("eid"), (str, int)) else None,
            "users": users}


# ---------------------------------------------------------------------------
# 反応を読む口（設計 4.0 S6 §3・§9 の 2）。どれも**認証なし**（公開の情報）・1 回だけ・再試行しない。
# 失敗は `hatena_common.PublicHTTPError(符丁)`。
# ---------------------------------------------------------------------------

COUNT_BATCH = 50   # 件数取得 API `count/entries` は 1 回 50 URL まで（超えると 414）


def count_url(urls) -> str:
    return f"{BASE}/count/entries?" + urllib.parse.urlencode([("url", u) for u in urls])


def bookmark_counts(urls, *, contact=None, timeout=READ_TIMEOUT) -> dict:
    """件数取得 API（`count/entries`）を 1 回。`{url: 件数か None}`。50 件を超える束は呼び手が分ける。

    返りは URL を鍵にした JSON（推測・実機の 45 で確かめる）。**鍵の無い URL は None**（0 と混ぜない・
    監査 中-6。0 かどうかは呼び手がエントリー情報と合わせて決める）。値が整数でなければ `response_invalid`。
    """
    urls = list(urls)
    if not urls or len(urls) > COUNT_BATCH:
        raise ValueError("count_batch_invalid")
    data = hatena_common.public_json(count_url(urls), contact=contact, timeout=timeout)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise hatena_common.PublicHTTPError("response_invalid")
    out = {}
    for u in urls:
        value = data.get(u)
        if value is None:
            out[u] = None
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise hatena_common.PublicHTTPError("response_invalid")
        out[u] = value
    return out


def _clean_tags(raw) -> list:
    return [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []


def entry_info_full(url, *, contact=None, timeout=READ_TIMEOUT) -> dict:
    """公開のエントリー情報（`entry/jsonlite/?url=`・キャッシュ）を 1 回。既存の `entry_info` の返りは変えない。

    返り: `{"exists", "count", "eid", "bookmarks": [{"user", "timestamp", "comment", "tags"}]}`。エントリーが
    無い（`null`）なら `exists: False`・`bookmarks: []`。`timestamp` は API の文字列のまま。
    """
    data = hatena_common.public_json(entry_url(url), contact=contact, timeout=timeout)
    if data is None:
        return {"exists": False, "count": 0, "eid": None, "bookmarks": []}
    if not isinstance(data, dict):
        raise hatena_common.PublicHTTPError("response_invalid")
    marks = data.get("bookmarks")
    rows = []
    for row in marks if isinstance(marks, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("user"), str) or not row["user"]:
            continue
        rows.append({"user": row["user"],
                     "timestamp": row.get("timestamp") if isinstance(row.get("timestamp"), str) else None,
                     "comment": row.get("comment") if isinstance(row.get("comment"), str) else "",
                     "tags": _clean_tags(row.get("tags"))})
    count = data.get("count")
    return {"exists": True, "count": count if isinstance(count, int) and not isinstance(count, bool) else len(rows),
            "eid": data.get("eid") if isinstance(data.get("eid"), (str, int)) else None, "bookmarks": rows}


class HatenaBookmarkAdapter:
    """主体（はてな ID）のブックマークへの口。"""

    def __init__(self, account_cfg, principal_cfg, *, token, consumer):
        self.hatena_id = principal_cfg["hatena_id"]
        self.contact = principal_cfg.get("user_agent_contact") or hatena_common.DEFAULT_CONTACT
        timeout = (account_cfg or {}).get("timeout")
        self.timeout = timeout if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0 else 300
        self._consumer = consumer
        self._token = token
        from .. import redact as redact_mod
        for value in (consumer["consumer_key"], consumer["consumer_secret"], token["oauth_token"],
                      token["oauth_token_secret"]):
            redact_mod.register_secret(value)

    @property
    def bookmark_endpoint(self) -> str:
        return f"{BASE}/rest/1/my/bookmark"

    def _scrub(self, text) -> str:
        return hatena_common.scrub(text, self._token["oauth_token_secret"], self._consumer["consumer_secret"],
                                   self._token["oauth_token"], self._consumer["consumer_key"])

    def _request(self, method, url, *, form=None):
        """署名して 1 回だけ投げる。`(status, bytes)`。urllib の例外はそのまま上げる。"""
        from .. import oauth1
        headers = {"Accept": "application/json", "User-Agent": hatena_common.user_agent(self.contact)}
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        headers["Authorization"] = oauth1.authorization_header(
            consumer_key=self._consumer["consumer_key"], consumer_secret=self._consumer["consumer_secret"],
            token=self._token["oauth_token"], token_secret=self._token["oauth_token_secret"],
            method=method, url=url, body_params=form)
        limit = READ_TIMEOUT if method == "GET" else WRITE_TIMEOUT
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        with httpsafe.urlopen(req, timeout=min(self.timeout, limit)) as response:
            status = response.status
            raw = _read_limited(response)
        return status, raw

    def _with_url(self, url) -> str:
        return self.bookmark_endpoint + "?" + urllib.parse.urlencode([("url", url)])

    @staticmethod
    def _http_error(code) -> str:
        if code in (401, 403):
            return "hatena_auth_rejected"
        if code == 429:
            return "provider_rate_limited"
        return f"hatena_http_{code}"

    def _parse_bookmark(self, raw):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, AttributeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("user"), str):
            return None
        if not isinstance(data.get("tags"), list):
            data["tags"] = []
        return data

    # ----- 口 -----------------------------------------------------------------

    def get_bookmark(self, *, url) -> dict | None:
        """`GET /rest/1/my/bookmark?url=`。200 → ブックマークオブジェクト・404 → None・他は `BookmarkHTTPError`。"""
        try:
            status, raw = self._request("GET", self._with_url(url))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise BookmarkHTTPError(self._http_error(exc.code), exc.code) from None
        except ResponseTooLarge:
            raise BookmarkHTTPError("response_too_large") from None
        except (httpsafe.EndpointRejected, httpsafe.RedirectBlocked) as exc:
            raise BookmarkHTTPError(self._scrub(exc.__class__.__name__)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            raise BookmarkHTTPError("hatena_transport_failed") from None
        if status != 200:
            raise BookmarkHTTPError(f"hatena_http_{status}", status)
        data = self._parse_bookmark(raw)
        if data is None:
            raise BookmarkHTTPError("response_invalid", status)
        return data

    def _write(self, method, url, *, form=None):
        try:
            status, raw = self._request(method, url, form=form)
        except httpsafe.RedirectBlocked:
            # 3xx を返した＝要求は届いている。書き込みは「分からない」（S2 監査 中-1 と同じ）。
            return BookmarkResult(ok=False, ambiguous=True, error="redirect_blocked")
        except urllib.error.HTTPError as exc:
            return BookmarkResult(ok=False, ambiguous=exc.code >= 500, error=self._http_error(exc.code),
                                  status=exc.code)
        except httpsafe.EndpointRejected:
            return BookmarkResult(ok=False, ambiguous=False, error="endpoint_rejected")
        except ResponseTooLarge:
            return BookmarkResult(ok=False, ambiguous=True, error="response_too_large")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            return BookmarkResult(ok=False, ambiguous=True, error="hatena_transport_failed")
        return status, raw

    def add_bookmark(self, *, url, comment, tags) -> BookmarkResult:
        """`POST /rest/1/my/bookmark`（url はクエリ・comment と tags は form の body）。1 回だけ。"""
        form = [("comment", comment or "")] + [("tags", t) for t in (tags or [])]
        out = self._write("POST", self._with_url(url), form=form)
        if isinstance(out, BookmarkResult):
            return out
        status, raw = out
        if not 200 <= status < 300:
            return BookmarkResult(ok=False, ambiguous=True, error=f"hatena_http_{status}", status=status)
        data = self._parse_bookmark(raw)
        if data is None:
            return BookmarkResult(ok=False, ambiguous=True, error="response_invalid", status=status)
        if data["user"] != self.hatena_id:
            return BookmarkResult(ok=False, ambiguous=True, error="hatena_user_mismatch", status=status,
                                  bookmark=data)
        return BookmarkResult(ok=True, status=status, bookmark=data)

    def delete_bookmark(self, *, url) -> BookmarkResult:
        """`DELETE /rest/1/my/bookmark?url=`。204 → ok・404 → ok（`missing=True`）。"""
        out = self._write("DELETE", self._with_url(url))
        if isinstance(out, BookmarkResult):
            if out.status == 404:
                return BookmarkResult(ok=True, status=404, missing=True)
            return out
        status, _raw = out
        if status in (200, 204):
            return BookmarkResult(ok=True, status=status)
        return BookmarkResult(ok=False, ambiguous=True, error=f"hatena_http_{status}", status=status)

    def entry_info(self, *, url) -> dict | None:
        """柵 2（公開 API・認証なし）。モジュールの `entry_info` と同じ。"""
        return entry_info(url, contact=self.contact, timeout=self.timeout)

    def probe(self) -> dict:
        """`GET /rest/1/my`（doctor・ID が読めるか）。例外は上げない。`{"ok", "status", "name", "error"}`。"""
        try:
            status, raw = self._request("GET", f"{BASE}/rest/1/my")
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "name": None, "error": self._http_error(exc.code)}
        except Exception as exc:  # noqa: BLE001 — 診断は落とさない
            return {"ok": False, "status": None, "name": None, "error": self._scrub(exc.__class__.__name__)}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            data = None
        name = data.get("name") if isinstance(data, dict) and isinstance(data.get("name"), str) else None
        if status != 200 or name is None:
            return {"ok": False, "status": status, "name": None, "error": "response_invalid"}
        return {"ok": name == self.hatena_id, "status": status, "name": name,
                "error": None if name == self.hatena_id else "hatena_user_mismatch"}
