"""`thth gads accounts|keywords|report` と Google の断りの写し（偽の Google・本物の網に出ない）。"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import urllib.parse
from pathlib import Path

import pytest

from thth import cli, refusal_catalog
from thth.gads import api, auth, wire
from thth.gads import cli as gads_cli
from thth.gads.wire import GadsRefused

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET = "FAKE-client-secret-0123456789"
REFRESH = "1//FAKE-refresh-token-abcdefghijklmnop"
ACCESS = "ya29.FAKE-access-token-qrstuvwxyz0123"
SECRETS = (CLIENT_SECRET, REFRESH, ACCESS)
BASE = f"https://googleads.googleapis.com/{api.API_VERSION}/"


class FakeGoogle:
    """token の窓口と Google Ads API の偽物。`routes[(method, path)]` に (status, 値) か、その並び（頁ごと）。"""

    def __init__(self, routes=None, refresh=(200, {"access_token": ACCESS, "expires_in": 3599})):
        self.routes = dict(routes or {})
        self.refresh = refresh
        self.calls = []

    def __call__(self, method, url, headers, body):
        if url == "https://oauth2.googleapis.com/token":
            status, value = self.refresh
            return status, json.dumps(value).encode()
        assert url.startswith(BASE), url
        path = url[len(BASE):]
        self.calls.append({"method": method, "path": path, "headers": headers,
                           "body": json.loads(body) if body else None})
        answer = self.routes[(method, path)]
        if isinstance(answer, list):
            answer = answer.pop(0)
        status, value = answer
        return status, (value if isinstance(value, bytes) else json.dumps(value).encode())


@pytest.fixture
def google(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {
        "client_id": "123-fake.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}}), encoding="utf-8")
    monkeypatch.setenv(auth.CLIENT_ENV, str(client))
    monkeypatch.setenv(auth.TOKEN_ENV, str(tmp_path / "token.json"))
    auth.save_token({"refresh_token": REFRESH})
    fake = FakeGoogle()
    monkeypatch.setattr(api, "default_transport", fake)
    monkeypatch.setattr(auth, "default_transport", fake)
    return fake


def run(argv, capsys):
    rc = cli.main(argv)
    out = capsys.readouterr()
    for secret in SECRETS:
        assert secret not in out.out + out.err, "秘密が出た"
    return rc, out


# ------------------------------------------------------------------ 口座の id と表

@pytest.mark.parametrize("value", ["226-006-0755", "2260060755", " 226-006-0755 "])
def test_口座のidはハイフンありもなしも10桁に(value):
    assert api.customer_id(value) == "2260060755"


@pytest.mark.parametrize("value", ["226-006-075", "22600607555", "abc-def-ghij", "226 006 0755", "", None,
                                   "226--006-0755", "2260060755x"])
def test_口座のidの形が違えば断る(value):
    with pytest.raises(GadsRefused) as caught:
        api.customer_id(value)
    assert caught.value.code == "gads_customer_invalid"


def test_言語と地域のidは公式の表の値():
    # languagecodes.csv（ja=1005・en=1000）と geotargets-2026-10-06.csv の Country（JP=2392・US=2840）。
    assert api.language("ja") == "languageConstants/1005" and api.language("en") == "languageConstants/1000"
    assert api.geo_target("JP") == "geoTargetConstants/2392" and api.geo_target("us") == "geoTargetConstants/2840"


def test_版は1か所の定数():
    assert api.BASE_URL == f"https://googleads.googleapis.com/{api.API_VERSION}/"
    hits = []
    for path in sorted((ROOT / "thth").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"googleads\.googleapis\.com/v\d", line):
                hits.append(f"{path.name}: {line.strip()}")
    assert all("$discovery" in hit for hit in hits), hits


# ------------------------------------------------------------------ accounts

def test_accountsは結べる口座の一覧(google, capsys):
    google.routes[("GET", "customers:listAccessibleCustomers")] = (
        200, {"resourceNames": ["customers/2260060755", "customers/1234567890"]})
    rc, out = run(["gads", "accounts"], capsys)
    assert rc == 0 and "226-006-0755" in out.out and "123-456-7890" in out.out
    google.routes[("GET", "customers:listAccessibleCustomers")] = (200, {"resourceNames": ["customers/2260060755"]})
    rc, out = run(["gads", "accounts", "--json"], capsys)
    assert rc == 0 and json.loads(out.out) == {"customers": ["2260060755"]}


# ------------------------------------------------------------------ keywords

IDEAS = {"results": [
    {"text": "中学受験 算数", "keywordIdeaMetrics": {
        "avgMonthlySearches": "1900", "competition": "MEDIUM", "competitionIndex": "45",
        "lowTopOfPageBidMicros": "120000000", "highTopOfPageBidMicros": "350400000"}},
    {"text": "算数 問題集", "keywordIdeaMetrics": {"competition": "LOW"}},
], "totalSize": "2"}


def test_keywordsは種を重ねて書いても全部送る(google, capsys):
    """11/08 本物で `--seed A --seed B` が B だけになった（argparse の nargs="+" は後ろが上書き）。"""
    path = "customers/2260060755:generateKeywordIdeas"
    google.routes[("POST", path)] = (200, IDEAS)
    rc, out = run(["gads", "keywords", "--customer", "226-006-0755", "--seed", "中学受験",
                   "--seed", "算数", "国語", "--json"], capsys)
    assert rc == 0, out.err
    assert google.calls[-1]["body"]["keywordSeed"] == {"keywords": ["中学受験", "算数", "国語"]}


def test_keywordsの依頼の本文(google, capsys):
    path = "customers/2260060755:generateKeywordIdeas"
    google.routes[("POST", path)] = (200, IDEAS)
    rc, out = run(["gads", "keywords", "--customer", "226-006-0755", "--seed", "中学受験", "算数",
                   "--limit", "10", "--json"], capsys)
    assert rc == 0, out.err
    body = google.calls[-1]["body"]
    assert google.calls[-1]["method"] == "POST" and google.calls[-1]["path"] == path
    assert body == {"language": "languageConstants/1005", "geoTargetConstants": ["geoTargetConstants/2392"],
                    "keywordPlanNetwork": "GOOGLE_SEARCH", "includeAdultKeywords": False, "pageSize": 10,
                    "keywordSeed": {"keywords": ["中学受験", "算数"]}}
    rc, out = run(["gads", "keywords", "--customer", "2260060755", "--seed", "math", "--lang", "en", "--geo", "US",
                   "--url", "https://example.com/page", "--json"], capsys)
    assert rc == 0, out.err
    body = google.calls[-1]["body"]
    assert body["language"] == "languageConstants/1000" and body["geoTargetConstants"] == ["geoTargetConstants/2840"]
    assert body["keywordAndUrlSeed"] == {"keywords": ["math"], "url": "https://example.com/page"}
    assert "keywordSeed" not in body and body["pageSize"] == 50


def test_keywordsの表示はmicrosを円に(google, capsys):
    google.routes[("POST", "customers/2260060755:generateKeywordIdeas")] = (200, IDEAS)
    rc, out = run(["gads", "keywords", "--customer", "2260060755", "--seed", "中学受験"], capsys)
    assert rc == 0, out.err
    assert "中学受験 算数  月平均 1,900  競合 MEDIUM  ページ上部の入札 120〜350 円" in out.out
    assert "算数 問題集  月平均 —  競合 LOW  ページ上部の入札 —" in out.out
    google.routes[("POST", "customers/2260060755:generateKeywordIdeas")] = (200, IDEAS)
    rc, out = run(["gads", "keywords", "--customer", "2260060755", "--seed", "中学受験", "--json"], capsys)
    value = json.loads(out.out)
    assert value["ideas"][0] == {"text": "中学受験 算数", "avg_monthly_searches": 1900, "competition": "MEDIUM",
                                 "competition_index": 45, "low_top_of_page_bid_yen": 120,
                                 "high_top_of_page_bid_yen": 350}
    assert value["ideas"][1]["avg_monthly_searches"] is None


def test_keywordsのlimitは手元でも切る(google, capsys):
    many = {"results": [{"text": f"語{i}", "keywordIdeaMetrics": {}} for i in range(5)]}
    google.routes[("POST", "customers/2260060755:generateKeywordIdeas")] = (200, many)
    rc, out = run(["gads", "keywords", "--customer", "2260060755", "--seed", "x", "--limit", "3", "--json"], capsys)
    assert [row["text"] for row in json.loads(out.out)["ideas"]] == ["語0", "語1", "語2"]


def test_micros():
    assert api.yen("120000000") == 120 and api.yen("1500000") == 2 and api.yen(None) is None and api.yen("x") is None


# ------------------------------------------------------------------ report

ROWS = [{"campaign": {"resourceName": "customers/2260060755/campaigns/11", "id": "11", "name": "秋の講習",
                      "status": "ENABLED"},
         "metrics": {"impressions": "12000", "clicks": "340", "costMicros": "15800500000", "conversions": 12.5}},
        {"campaign": {"id": "12", "name": "体験", "status": "PAUSED"},
         "metrics": {"impressions": "800", "clicks": "20", "costMicros": "900000000", "conversions": 0.0}}]
SEARCH = ("POST", "customers/2260060755/googleAds:search")


def test_reportはキャンペーンごとの数とGAQL(google, capsys):
    google.routes[SEARCH] = (200, {"results": ROWS, "fieldMask": "campaign.id"})
    rc, out = run(["gads", "report", "--customer", "226-006-0755", "--since", "2026-10-01", "--until",
                   "2026-10-31"], capsys)
    assert rc == 0, out.err
    query = google.calls[-1]["body"]["query"]
    assert google.calls[-1]["body"] == {"query": query}  # pageSize（v26 で非推奨）は送らない
    assert query == ("SELECT campaign.id, campaign.name, campaign.status, metrics.impressions, metrics.clicks, "
                     "metrics.cost_micros, metrics.conversions FROM campaign "
                     "WHERE segments.date BETWEEN '2026-10-01' AND '2026-10-31' ORDER BY metrics.cost_micros DESC")
    assert "秋の講習（ENABLED）  表示 12,000  クリック 340  費用 15,800 円  コンバージョン 12.5" in out.out
    assert "合計  表示 12,800  クリック 360  費用 16,700 円" in out.out


def test_reportはキャンペーンが0件でも断らない(google, capsys):
    google.routes[SEARCH] = (200, {"fieldMask": "campaign.id"})  # 0 件のとき results の欄は無い
    rc, out = run(["gads", "report", "--customer", "2260060755", "--days", "7"], capsys)
    assert rc == 0 and out.err == ""
    assert "キャンペーンがありません" in out.out
    google.routes[SEARCH] = (200, {"results": []})
    rc, out = run(["gads", "report", "--customer", "2260060755", "--json"], capsys)
    value = json.loads(out.out)
    assert rc == 0 and value["campaigns"] == [] and value["totals"]["cost_yen"] == 0


def test_reportは次の頁を辿る(google, capsys):
    google.routes[SEARCH] = [(200, {"results": ROWS[:1], "nextPageToken": "p2"}), (200, {"results": ROWS[1:]})]
    rc, out = run(["gads", "report", "--customer", "2260060755", "--days", "3", "--json"], capsys)
    assert rc == 0 and len(json.loads(out.out)["campaigns"]) == 2
    assert google.calls[-1]["body"]["pageToken"] == "p2"


def _ns(**kw):
    base = {"days": None, "since": None, "until": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_期間の決め方():
    today = datetime.date(2026, 11, 8)
    assert gads_cli.date_range(_ns(), today=today) == ("2026-10-09", "2026-11-07")
    assert gads_cli.date_range(_ns(days=7), today=today) == ("2026-11-01", "2026-11-07")
    assert gads_cli.date_range(_ns(since="2026-10-01"), today=today) == ("2026-10-01", "2026-11-08")
    for bad in (_ns(days=7, since="2026-10-01"), _ns(until="2026-10-01"), _ns(since="2026-10-31", until="2026-10-01"),
                _ns(since="2026/10/01"), _ns(days=0)):
        with pytest.raises(GadsRefused) as caught:
            gads_cli.date_range(bad, today=today)
        assert caught.value.code == "gads_date_invalid"


# ------------------------------------------------------------------ Google の断りの写し

def _failure(kind, value, status=403):
    return status, {"error": {"code": status, "message": "Secret-ish message " + ACCESS, "status": "PERMISSION_DENIED",
                              "details": [{"@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure",
                                           "errors": [{"errorCode": {kind: value}, "message": "x"}],
                                           "requestId": "abc"}]}}


@pytest.mark.parametrize("kind,value,code", [
    ("authenticationError", "OAUTH_TOKEN_EXPIRED", "gads_authentication_error"),
    ("authenticationError", "CUSTOMER_NOT_FOUND", "gads_customer_unknown"),
    ("authorizationError", "USER_PERMISSION_DENIED", "gads_authorization_error"),
    ("authorizationError", "AUTHORIZATION_ERROR", "gads_authorization_error"),
    ("authorizationError", "CUSTOMER_NOT_ENABLED", "gads_customer_not_enabled"),
    ("authorizationError", "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION", "gads_project_not_approved"),
    ("quotaError", "RESOURCE_EXHAUSTED", "gads_quota_exceeded"),
    ("requestError", "INVALID_CUSTOMER_ID", "gads_customer_invalid"),
    ("queryError", "BAD_FIELD_NAME", "gads_request_rejected"),
])
def test_Googleの断りはTHTHの符丁に(kind, value, code):
    refused = api.refusal_for(*_failure(kind, value))
    assert (refused.code, refused.detail) == (code, value)
    assert ACCESS not in str(refused)


@pytest.mark.parametrize("status,payload,code", [
    (401, {"error": {"status": "UNAUTHENTICATED"}}, "gads_authentication_error"),
    (403, None, "gads_authorization_error"),
    (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, "gads_quota_exceeded"),
    (503, None, "gads_unavailable"),
    (400, None, "gads_request_rejected"),
])
def test_詳細が無いときはHTTPの数で(status, payload, code):
    assert api.refusal_for(status, payload).code == code


# ------------------------------------------------------------------ 符丁ごとの断り（CLI の 3 つの行）

CID = ["--customer", "2260060755"]


def _arrange(name, google, tmp_path, monkeypatch):
    """断りの場面を作り、打つ命令を返す。"""
    kw = ["gads", "keywords", *CID, "--seed", "x"]
    accounts = ["gads", "accounts"]
    rep = ["gads", "report", *CID]
    listing = ("GET", "customers:listAccessibleCustomers")
    if name == "gads_client_missing":
        Path(auth.client_path()).unlink()
        return accounts
    if name == "gads_client_invalid":
        Path(auth.client_path()).write_text(json.dumps({"web": {}}))
        return accounts
    if name == "gads_token_missing":
        Path(auth.token_path()).unlink()
        return accounts
    if name == "gads_token_invalid":
        Path(auth.token_path()).write_text("{")
        return accounts
    if name == "gads_token_revoked":
        google.refresh = (400, {"error": "invalid_grant"})
        return accounts
    if name == "gads_token_refresh_failed":
        google.refresh = (503, {"error": "temporarily_unavailable"})
        return accounts
    if name == "gads_customer_invalid":
        return ["gads", "report", "--customer", "12-34"]
    if name == "gads_seed_empty":
        return ["gads", "keywords", *CID, "--seed", " "]
    if name == "gads_seed_too_many":
        return ["gads", "keywords", *CID, "--seed", *[f"w{i}" for i in range(21)]]
    if name == "gads_url_invalid":
        return [*kw, "--url", "ftp://example.com/x"]
    if name == "gads_lang_unknown":
        return [*kw, "--lang", "xx"]
    if name == "gads_geo_unknown":
        return [*kw, "--geo", "ZZ"]
    if name == "gads_date_invalid":
        return [*rep, "--days", "7", "--since", "2026-10-01"]
    if name == "gads_response_invalid":
        google.routes[listing] = (200, b"<html>not json</html>")
        return accounts
    if name == "gads_unavailable":
        def down(method, url, headers, body):
            raise GadsRefused("gads_unavailable")
        monkeypatch.setattr(api, "default_transport", down)
        monkeypatch.setattr(auth, "default_transport", down)
        return accounts
    google_side = {
        "gads_customer_unknown": ("authenticationError", "CUSTOMER_NOT_FOUND"),
        "gads_customer_not_enabled": ("authorizationError", "CUSTOMER_NOT_ENABLED"),
        "gads_authentication_error": ("authenticationError", "OAUTH_TOKEN_INVALID"),
        "gads_authorization_error": ("authorizationError", "USER_PERMISSION_DENIED"),
        "gads_project_not_approved": ("authorizationError", "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"),
        "gads_quota_exceeded": ("quotaError", "RESOURCE_EXHAUSTED"),
        "gads_request_rejected": ("queryError", "BAD_FIELD_NAME"),
    }
    if name in google_side:
        google.routes[("POST", "customers/2260060755/googleAds:search")] = _failure(*google_side[name])
        return rep
    return None


# 認可の流れの符丁は test_gads_auth.py が場面を作る（ブラウザの戻り）。
AUTH_FLOW = {"gads_auth_denied", "gads_auth_timeout", "gads_auth_state_mismatch", "gads_auth_exchange_failed",
             "gads_no_refresh_token"}


@pytest.mark.parametrize("name", sorted(wire.REASONS - AUTH_FLOW))
def test_符丁ごとにCLIが3つの行を出し秘密を出さない(name, google, tmp_path, monkeypatch, capsys):
    argv = _arrange(name, google, tmp_path, monkeypatch)
    assert argv is not None, f"{name} の場面が無い"
    rc, out = run(argv, capsys)
    assert rc == 2 and out.out == ""
    lines = out.err.splitlines()
    assert lines[0].startswith(f"{name}: ")
    assert any(line.startswith("通すには: ") for line in lines) and any(line.startswith("もっと良く: ") for line in lines)
    rc, out = run([*argv, "--json"], capsys)
    value = json.loads(out.out)
    assert value["ok"] is False and value["code"] == name and value["why"] and value["fix"] and value["better"]


@pytest.mark.parametrize("name", sorted(wire.REASONS))
def test_符丁は目録に3つがそろう(name):
    entry = refusal_catalog.CATALOG[name]
    assert entry["why"] and entry["fix"]["text"] and entry["better"] and entry["no_better"] is None
    assert entry["doors"] == ("cli",) and entry["since"] == "5.13.0"


def test_GadsRefusedは知らない符丁と形の違う詳細を通さない():
    refused = GadsRefused("gads_nope", "has\nnewline " + ACCESS * 3)
    assert refused.code == "gads_request_rejected" and refused.detail is None
