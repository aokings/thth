"""Search Console API の REST（読むだけ・urllib だけ）。

欄の名前は公式の参照（2026-11-08 に読んだ）で確かめた:

- `GET  https://www.googleapis.com/webmasters/v3/sites` → `siteEntry[]{siteUrl, permissionLevel}`
  （`permissionLevel` は siteOwner・siteFullUser・siteRestrictedUser・siteUnverifiedUser）。
- `POST https://www.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query` ← `startDate`・`endDate`
  （YYYY-MM-DD・太平洋時間・両端を含む）・`dimensions`（query・page・country・device・date ほか）・`type`（既定 web）・
  `dimensionFilterGroups[]{groupType: "and", filters[]{dimension, operator, expression}}`（operator は
  contains・equals・notContains・notEquals・includingRegex・excludingRegex・式は 4096 字まで）・`rowLimit`（1〜25,000・
  既定 1,000）・`startRow`・`dataState`（省くと確定した数だけ）
  → `rows[]{keys[], clicks, impressions, ctr, position}`（クリックの多い順・date で分けると日付の順）。
  数は「上の行」だけで、全部の行は返らない（1 日 1 種類 5 万行まで: how-tos/all-your-data）。
- `POST https://searchconsole.googleapis.com/v1/urlInspection/index:inspect` ← `inspectionUrl`・`siteUrl`・`languageCode`
  → `inspectionResult{inspectionResultLink, indexStatusResult{verdict, coverageState, robotsTxtState, indexingState,
  lastCrawlTime, pageFetchState, googleCanonical, userCanonical, crawledAs}}`。Google の索引にある版の様子だけ。
  1 プロパティ 1 日 2,000 回・1 分 600 回まで（https://developers.google.com/webmaster-tools/limits）。

scope は `webmasters.readonly` で 3 つとも呼べる（各頁の Authorization scopes）。
"""
from __future__ import annotations

import json
import re
import urllib.parse

from . import auth
from .wire import GscRefused, default_transport

BASE_URL = "https://www.googleapis.com/webmasters/v3/"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
DIMENSIONS = ("query", "page", "country", "device", "date")
ROW_LIMIT_MAX = 25_000
EXPRESSION_MAX = 4096
URL_MAX = 2048
PERMISSIONS = ("siteOwner", "siteFullUser", "siteRestrictedUser", "siteUnverifiedUser")

_ENUM = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_DOMAIN = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
# RE2 で意味を持つ字（--page-prefix を「この字の並びで始まる」の式にするときに逃がす）。
_RE2_SPECIAL = set("\\.+*?()|[]{}^$")


def _clean_text(value) -> bool:
    return isinstance(value, str) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _http_url(value) -> urllib.parse.SplitResult | None:
    if not _clean_text(value) or not value or len(value) > URL_MAX or " " in value:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port  # noqa: B018 （port の数が壊れていれば ValueError）
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return parsed


def site_url(value) -> str:
    """`sc-domain:example.com`（ドメインのプロパティ）か `https://example.com/`（URL の頭のプロパティ・終わりは /）。"""
    if isinstance(value, str) and value.startswith("sc-domain:"):
        domain = value[len("sc-domain:"):]
        if _DOMAIN.fullmatch(domain):
            return value
        raise GscRefused("gsc_site_invalid")
    parsed = _http_url(value)
    if parsed is None or not parsed.path.endswith("/") or parsed.query or parsed.fragment or parsed.username:
        raise GscRefused("gsc_site_invalid")
    return value


def url_in_site(site: str, url: str) -> bool:
    parsed = _http_url(url)
    if parsed is None:
        return False
    if site.startswith("sc-domain:"):
        domain = site[len("sc-domain:"):]
        host = (parsed.hostname or "").lower()
        return host == domain or host.endswith("." + domain)
    return url.startswith(site)


def dimensions(values) -> list[str]:
    dims = [str(v).strip().lower() for v in (values or [])]
    if any(d not in DIMENSIONS for d in dims) or len(set(dims)) != len(dims):
        raise GscRefused("gsc_dim_invalid")
    return dims


def filters(*, page_prefix=None, query_contains=None) -> list[dict]:
    out = []
    if page_prefix is not None:
        if _http_url(page_prefix) is None:
            raise GscRefused("gsc_page_prefix_invalid")
        pattern = "^" + "".join("\\" + c if c in _RE2_SPECIAL else c for c in page_prefix)
        if len(pattern) > EXPRESSION_MAX:
            raise GscRefused("gsc_page_prefix_invalid")
        out.append({"dimension": "page", "operator": "includingRegex", "expression": pattern})
    if query_contains is not None:
        if not _clean_text(query_contains) or not query_contains.strip() or len(query_contains) > EXPRESSION_MAX:
            raise GscRefused("gsc_query_contains_invalid")
        out.append({"dimension": "query", "operator": "contains", "expression": query_contains.strip()})
    return out


def query_body(since: str, until: str, dims, *, page_prefix=None, query_contains=None, limit=1000) -> dict:
    if not (_DATE.fullmatch(since or "") and _DATE.fullmatch(until or "")) or until < since:
        raise GscRefused("gsc_date_invalid")
    body = {"startDate": since, "endDate": until, "dimensions": dimensions(dims), "type": "web",
            "rowLimit": max(1, min(int(limit), ROW_LIMIT_MAX)), "startRow": 0}
    found = filters(page_prefix=page_prefix, query_contains=query_contains)
    if found:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": found}]
    return body


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metric_row(item: dict, dims: list[str]) -> dict:
    keys = item.get("keys") if isinstance(item.get("keys"), list) else []
    row = {dim: (keys[i] if i < len(keys) and isinstance(keys[i], str) else None) for i, dim in enumerate(dims)}
    clicks, impressions = _num(item.get("clicks")), _num(item.get("impressions"))
    ctr, position = _num(item.get("ctr")), _num(item.get("position"))
    row.update({"clicks": int(clicks) if clicks is not None else 0,
                "impressions": int(impressions) if impressions is not None else 0,
                "ctr": round(ctr, 4) if ctr is not None else None,
                "position": round(position, 1) if position is not None else None})
    return row


# ------------------------------------------------------------------ Google の断り → THTH の符丁

def _reasons(payload):
    """`error.details[].reason`（ErrorInfo）と `error.errors[].reason` の語を順に（形を検査したものだけ）。"""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return
    for key in ("details", "errors"):
        items = error.get(key)
        for item in items if isinstance(items, list) else ():
            reason = item.get("reason") if isinstance(item, dict) else None
            if isinstance(reason, str) and _ENUM.fullmatch(reason):
                yield reason


_DISABLED = {"SERVICE_DISABLED", "accessNotConfigured", "API_DISABLED"}
_SCOPE = {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}
_QUOTA = {"RATE_LIMIT_EXCEEDED", "rateLimitExceeded", "quotaExceeded", "userRateLimitExceeded",
          "dailyLimitExceeded", "RESOURCE_EXHAUSTED"}


def refusal_for(status: int, payload) -> GscRefused:
    """Google の答え（HTTP の数と本文）から THTH の断り。詳細は Google の理由の名前だけ（本文の文は入れない）。"""
    reasons = list(_reasons(payload))
    error = payload.get("error") if isinstance(payload, dict) else None
    status_word = error.get("status") if isinstance(error, dict) and isinstance(error.get("status"), str) else None
    if status_word is not None and not _ENUM.fullmatch(status_word):
        status_word = None
    words = set(reasons) | ({status_word} if status_word else set())
    detail = (reasons[0] if reasons else None) or status_word or f"HTTP {status}"
    if words & _DISABLED:
        return GscRefused("gsc_api_not_enabled", next(iter(sorted(words & _DISABLED))))
    if words & _SCOPE:
        return GscRefused("gsc_authentication_error", next(iter(sorted(words & _SCOPE))))
    if status == 429 or words & _QUOTA:
        return GscRefused("gsc_quota_exceeded", detail)
    if status == 401:
        return GscRefused("gsc_authentication_error", detail)
    if status == 403:
        return GscRefused("gsc_permission_denied", detail)
    if status == 404:
        return GscRefused("gsc_site_unknown", detail)
    if status >= 500:
        return GscRefused("gsc_unavailable", detail)
    return GscRefused("gsc_request_rejected", detail)


# ------------------------------------------------------------------ 呼ぶ口

class Client:
    """1 つの命令で 1 つ。初めて呼ぶときに refresh token から access token を取る（残さない）。"""

    def __init__(self, *, transport=None):
        self.transport = transport or default_transport
        self.client = auth.load_client()
        self.token = auth.load_token()
        self._access = None

    def _access_token(self) -> str:
        if self._access is None:
            self._access = auth.refresh(self.client, self.token, transport=self.transport)
        return self._access

    def call(self, method: str, url: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, raw = self.transport(method, url, headers, data)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = None
        if status != 200:
            raise refusal_for(status, payload)
        if not isinstance(payload, dict):
            raise GscRefused("gsc_response_invalid")
        return payload

    # ---- 見えるプロパティ

    def sites(self) -> list[dict]:
        payload = self.call("GET", BASE_URL + "sites")
        entries = payload.get("siteEntry", [])
        if not isinstance(entries, list):
            raise GscRefused("gsc_response_invalid")
        out = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("siteUrl"), str):
                continue
            level = entry.get("permissionLevel")
            out.append({"site": entry["siteUrl"], "permission": level if level in PERMISSIONS else None})
        return sorted(out, key=lambda e: e["site"])

    # ---- 検索の成績

    def query(self, site: str, since: str, until: str, dims, *, page_prefix=None, query_contains=None,
              limit=1000) -> dict:
        site = site_url(site)
        body = query_body(since, until, dims, page_prefix=page_prefix, query_contains=query_contains, limit=limit)
        path = "sites/" + urllib.parse.quote(site, safe="") + "/searchAnalytics/query"
        payload = self.call("POST", BASE_URL + path, body)
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise GscRefused("gsc_response_invalid")
        return {"site": site, "since": since, "until": until, "dimensions": body["dimensions"],
                "page_prefix": page_prefix, "query_contains": query_contains, "type": "web",
                "rows": [metric_row(item, body["dimensions"]) for item in rows if isinstance(item, dict)][:limit]}

    # ---- URL の検査

    def inspect(self, site: str, url: str, *, language: str = "ja") -> dict:
        site = site_url(site)
        if _http_url(url) is None:
            raise GscRefused("gsc_url_invalid")
        if not url_in_site(site, url):
            raise GscRefused("gsc_inspect_url_outside")
        payload = self.call("POST", INSPECT_URL, {"inspectionUrl": url, "siteUrl": site, "languageCode": language})
        result = payload.get("inspectionResult")
        if not isinstance(result, dict):
            raise GscRefused("gsc_response_invalid")
        index = result.get("indexStatusResult") if isinstance(result.get("indexStatusResult"), dict) else {}

        def text(key):
            value = index.get(key)
            return value if isinstance(value, str) else None

        def enum(key):
            value = index.get(key)
            return value if isinstance(value, str) and _ENUM.fullmatch(value) else None

        link = result.get("inspectionResultLink")
        return {"site": site, "url": url,
                "verdict": enum("verdict"), "coverage_state": text("coverageState"),
                "robots_txt_state": enum("robotsTxtState"), "indexing_state": enum("indexingState"),
                "page_fetch_state": enum("pageFetchState"), "last_crawl_time": text("lastCrawlTime"),
                "crawled_as": enum("crawledAs"), "google_canonical": text("googleCanonical"),
                "user_canonical": text("userCanonical"),
                "link": link if isinstance(link, str) and link.startswith("https://search.google.com/") else None}


# ------------------------------------------------------------------ 前の期間との差

def compare_rows(now: list[dict], before: list[dict], dim: str) -> list[dict]:
    """同じ鍵（語か頁）で 2 つの期間を並べる。片方に無い鍵は 0（掲載順位は None）。"""
    a = {row[dim]: row for row in now if row.get(dim) is not None}
    b = {row[dim]: row for row in before if row.get(dim) is not None}
    out = []
    for key in set(a) | set(b):
        x, y = a.get(key, {}), b.get(key, {})
        out.append({dim: key,
                    "clicks": x.get("clicks", 0), "clicks_before": y.get("clicks", 0),
                    "clicks_delta": x.get("clicks", 0) - y.get("clicks", 0),
                    "impressions": x.get("impressions", 0), "impressions_before": y.get("impressions", 0),
                    "impressions_delta": x.get("impressions", 0) - y.get("impressions", 0),
                    "position": x.get("position"), "position_before": y.get("position")})
    return out


def split_moves(rows: list[dict], dim: str, limit: int) -> tuple[list[dict], list[dict]]:
    """伸びた（クリックの差・次に表示の差が大きい順）と落ちた（小さい順）を上から `limit` 件ずつ。"""
    def score(r):
        return (r["clicks_delta"], r["impressions_delta"])
    rising = sorted((r for r in rows if score(r) > (0, 0)), key=lambda r: (-r["clicks_delta"],
                                                                          -r["impressions_delta"], r[dim]))
    falling = sorted((r for r in rows if score(r) < (0, 0)), key=lambda r: (r["clicks_delta"],
                                                                           r["impressions_delta"], r[dim]))
    return rising[:limit], falling[:limit]
