"""Google Ads API の REST（読むだけ・urllib だけ）。

**版は `API_VERSION` の 1 か所**（約 12 か月で廃止・年に 1 回上げる。v26 の廃止は 2027-11）。
欄の名前は v26 の公式の discovery（`https://googleads.googleapis.com/$discovery/rest?version=v26`・2026-11-08 に読んだ）
で確かめた:

- `GET  v26/customers:listAccessibleCustomers` → `resourceNames`（`customers/<数字>` の並び）。
- `POST v26/customers/{id}:generateKeywordIdeas`（KeywordPlanIdeaService）← `language`・`geoTargetConstants`・
  `keywordPlanNetwork`・`includeAdultKeywords`・`pageSize`・`keywordSeed{keywords}` か `keywordAndUrlSeed{keywords,url}`
  → `results[]{text, keywordIdeaMetrics{avgMonthlySearches, competition, lowTopOfPageBidMicros,
  highTopOfPageBidMicros}}`・`nextPageToken`（int64 は JSON の文字列）。
- `POST v26/customers/{id}/googleAds:search` ← `query`（GAQL）・`pageToken`（`pageSize` は v26 で非推奨なので送らない）
  → `results[]{campaign{id,name,status}, metrics{impressions, clicks, costMicros, conversions}}`・`nextPageToken`。
  `segments.date` は WHERE だけに書いてよい（https://developers.google.com/google-ads/api/docs/query/structure）。

**developer-token の header は送らない**（2026-09-09 に廃止・今は無視される）。段は Cloud の project に付く。
言語と地域の id は公式の表で確かめた（`LANGUAGES`・`GEO_TARGETS` の説明）。
"""
from __future__ import annotations

import json
import re
import urllib.parse

from . import auth
from .wire import GadsRefused, default_transport

API_VERSION = "v26"
BASE_URL = f"https://googleads.googleapis.com/{API_VERSION}/"

# 言語（https://developers.google.com/static/google-ads/api/data/tables/languagecodes.csv・2026-11-08）。
LANGUAGES = {"ja": ("1005", "日本語"), "en": ("1000", "英語")}
# 地域（https://developers.google.com/static/google-ads/api/data/geo/geotargets-2026-10-06.csv.zip の Country の行）。
GEO_TARGETS = {"JP": ("2392", "日本"), "US": ("2840", "アメリカ合衆国")}
# 1 回に渡せる種の語の数（KeywordSeed.keywords: 1〜20）。
MAX_SEEDS = 20
MICROS = 1_000_000
MAX_PAGES = 20

_CUSTOMER = re.compile(r"(\d{3})-?(\d{3})-?(\d{4})\Z")
_ENUM = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def customer_id(value) -> str:
    """`226-006-0755` も `2260060755` も 10 桁の数字に。"""
    match = _CUSTOMER.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        raise GadsRefused("gads_customer_invalid")
    return "".join(match.groups())


def language(code) -> str:
    entry = LANGUAGES.get(str(code or "").lower())
    if entry is None:
        raise GadsRefused("gads_lang_unknown")
    return f"languageConstants/{entry[0]}"


def geo_target(code) -> str:
    entry = GEO_TARGETS.get(str(code or "").upper())
    if entry is None:
        raise GadsRefused("gads_geo_unknown")
    return f"geoTargetConstants/{entry[0]}"


def yen(micros):
    """micros（口座の通貨の 100 万分の 1）を円に（JPY の口座の前提・1 円未満は四捨五入）。"""
    if micros is None:
        return None
    try:
        return round(int(micros) / MICROS)
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Google の断り → THTH の符丁

_AUTHORIZATION_SPECIAL = {
    "CUSTOMER_NOT_ENABLED": "gads_customer_not_enabled",
    "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION": "gads_project_not_approved",
    "DEVELOPER_TOKEN_NOT_APPROVED": "gads_project_not_approved",
    "DEVELOPER_TOKEN_NOT_ON_ALLOWLIST": "gads_project_not_approved",
    "PROJECT_DISABLED": "gads_project_not_approved",
}


def _google_errors(payload):
    """`error.details[].errors[].errorCode` の (分類, 値) を順に（GoogleAdsFailure の形）。"""
    error = payload.get("error") if isinstance(payload, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    for detail in details if isinstance(details, list) else ():
        errors = detail.get("errors") if isinstance(detail, dict) else None
        for item in errors if isinstance(errors, list) else ():
            code = item.get("errorCode") if isinstance(item, dict) else None
            if isinstance(code, dict):
                for kind, value in code.items():
                    if isinstance(kind, str) and isinstance(value, str) and _ENUM.fullmatch(value):
                        yield kind, value


def refusal_for(status: int, payload) -> GadsRefused:
    """Google の答え（HTTP の数と本文）から THTH の断り。詳細は Google の enum の名前だけ（本文の文は入れない）。"""
    for kind, value in _google_errors(payload):
        if kind == "authenticationError":
            if value == "CUSTOMER_NOT_FOUND":
                return GadsRefused("gads_customer_unknown", value)
            return GadsRefused("gads_authentication_error", value)
        if kind == "authorizationError":
            return GadsRefused(_AUTHORIZATION_SPECIAL.get(value, "gads_authorization_error"), value)
        if kind == "quotaError":
            return GadsRefused("gads_quota_exceeded", value)
        if kind == "requestError" and value == "INVALID_CUSTOMER_ID":
            return GadsRefused("gads_customer_invalid", value)
        return GadsRefused("gads_request_rejected", value if kind.endswith("Error") else None)
    status_word = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("status"), str) and _ENUM.fullmatch(error["status"]):
        status_word = error["status"]
    detail = status_word or f"HTTP {status}"
    if status == 401:
        return GadsRefused("gads_authentication_error", detail)
    if status == 403:
        return GadsRefused("gads_authorization_error", detail)
    if status == 429 or status_word == "RESOURCE_EXHAUSTED":
        return GadsRefused("gads_quota_exceeded", detail)
    if status >= 500:
        return GadsRefused("gads_unavailable", detail)
    return GadsRefused("gads_request_rejected", detail)


# ------------------------------------------------------------------ 呼ぶ口

class Client:
    """1 つの命令で 1 つ。初めて呼ぶときに refresh token から access token を取る（残さない）。"""

    def __init__(self, *, transport=None, base_url=BASE_URL):
        self.transport = transport or default_transport
        self.base_url = base_url
        self.client = auth.load_client()
        self.token = auth.load_token()
        self._access = None

    def _access_token(self) -> str:
        if self._access is None:
            self._access = auth.refresh(self.client, self.token, transport=self.transport)
        return self._access

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, raw = self.transport(method, self.base_url + path, headers, data)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = None
        if status != 200:
            raise refusal_for(status, payload)
        if not isinstance(payload, dict):
            raise GadsRefused("gads_response_invalid")
        return payload

    # ---- 結べる口座

    def accessible_customers(self) -> list[str]:
        payload = self.call("GET", "customers:listAccessibleCustomers")
        names = payload.get("resourceNames", [])
        if not isinstance(names, list):
            raise GadsRefused("gads_response_invalid")
        out = []
        for name in names:
            if isinstance(name, str) and name.startswith("customers/") and name[10:].isdigit():
                out.append(name[10:])
        return out

    # ---- キーワードの案

    def keyword_ideas(self, customer: str, seeds: list[str], *, url: str | None = None, lang: str = "ja",
                      geo: str = "JP", limit: int = 50) -> dict:
        body = keyword_ideas_body(seeds, url=url, lang=lang, geo=geo, limit=limit)
        payload = self.call("POST", f"customers/{customer_id(customer)}:generateKeywordIdeas", body)
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise GadsRefused("gads_response_invalid")
        rows = [idea_row(item) for item in results if isinstance(item, dict)][:limit]
        return {"customer": customer_id(customer), "seeds": body_seeds(body), "url": url,
                "lang": lang.lower(), "geo": geo.upper(), "network": body["keywordPlanNetwork"],
                "currency_note": "入札の額は口座の通貨（JPY の口座なら円）", "ideas": rows,
                "total": _int(payload.get("totalSize"))}

    # ---- キャンペーンごとの成績

    def campaign_report(self, customer: str, since: str, until: str) -> dict:
        cid = customer_id(customer)
        query = report_query(since, until)
        rows, token = [], None
        for _ in range(MAX_PAGES):
            body = {"query": query}
            if token:
                body["pageToken"] = token
            payload = self.call("POST", f"customers/{cid}/googleAds:search", body)
            results = payload.get("results", [])
            if not isinstance(results, list):
                raise GadsRefused("gads_response_invalid")
            rows.extend(report_row(item) for item in results if isinstance(item, dict))
            token = payload.get("nextPageToken")
            if not token:
                break
        totals = {"impressions": sum(r["impressions"] or 0 for r in rows),
                  "clicks": sum(r["clicks"] or 0 for r in rows),
                  "cost_yen": yen(sum(r["cost_micros"] or 0 for r in rows)),
                  "conversions": round(sum(r["conversions"] or 0 for r in rows), 2)}
        return {"customer": cid, "since": since, "until": until, "campaigns": rows, "totals": totals,
                "currency_note": "費用は口座の通貨（JPY の口座なら円）"}


def keyword_ideas_body(seeds, *, url=None, lang="ja", geo="JP", limit=50) -> dict:
    words = [s.strip() for s in (seeds or []) if isinstance(s, str) and s.strip()]
    if not words:
        raise GadsRefused("gads_seed_empty")
    if len(words) > MAX_SEEDS:
        raise GadsRefused("gads_seed_too_many")
    body = {"language": language(lang), "geoTargetConstants": [geo_target(geo)],
            "keywordPlanNetwork": "GOOGLE_SEARCH", "includeAdultKeywords": False, "pageSize": int(limit)}
    if url is not None:
        parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname or any(
                ord(c) <= 32 for c in url):
            raise GadsRefused("gads_url_invalid")
        body["keywordAndUrlSeed"] = {"keywords": words, "url": url}
    else:
        body["keywordSeed"] = {"keywords": words}
    return body


def body_seeds(body: dict) -> list[str]:
    return list((body.get("keywordAndUrlSeed") or body.get("keywordSeed") or {}).get("keywords") or [])


def idea_row(item: dict) -> dict:
    metrics = item.get("keywordIdeaMetrics") if isinstance(item.get("keywordIdeaMetrics"), dict) else {}
    competition = metrics.get("competition")
    return {"text": item.get("text") if isinstance(item.get("text"), str) else "",
            "avg_monthly_searches": _int(metrics.get("avgMonthlySearches")),
            "competition": competition if isinstance(competition, str) and _ENUM.fullmatch(competition) else None,
            "competition_index": _int(metrics.get("competitionIndex")),
            "low_top_of_page_bid_yen": yen(metrics.get("lowTopOfPageBidMicros")),
            "high_top_of_page_bid_yen": yen(metrics.get("highTopOfPageBidMicros"))}


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def report_query(since: str, until: str) -> str:
    if not (_DATE.fullmatch(since or "") and _DATE.fullmatch(until or "")):
        raise GadsRefused("gads_date_invalid")
    return ("SELECT campaign.id, campaign.name, campaign.status, metrics.impressions, metrics.clicks, "
            "metrics.cost_micros, metrics.conversions FROM campaign "
            f"WHERE segments.date BETWEEN '{since}' AND '{until}' ORDER BY metrics.cost_micros DESC")


def report_row(item: dict) -> dict:
    campaign = item.get("campaign") if isinstance(item.get("campaign"), dict) else {}
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    status = campaign.get("status")
    try:
        conversions = round(float(metrics.get("conversions") or 0), 2)
    except (TypeError, ValueError):
        conversions = None
    return {"campaign_id": str(campaign.get("id") or ""),
            "name": campaign.get("name") if isinstance(campaign.get("name"), str) else "",
            "status": status if isinstance(status, str) and _ENUM.fullmatch(status) else None,
            "impressions": _int(metrics.get("impressions")) or 0,
            "clicks": _int(metrics.get("clicks")) or 0,
            "cost_micros": _int(metrics.get("costMicros")) or 0,
            "cost_yen": yen(metrics.get("costMicros")) or 0,
            "conversions": conversions}
