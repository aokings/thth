"""`thth gsc sites|query|compare|inspect` と Google の断りの写し（偽の Google・本物の網に出ない）。"""
from __future__ import annotations

import argparse
import datetime
import json
import urllib.parse

import pytest

from thth import cli, refusal_catalog
from thth.gsc import api, auth, wire
from thth.gsc import cli as gsc_cli
from thth.gsc.wire import GscRefused

CLIENT_SECRET = "FAKE-client-secret-0123456789"
REFRESH = "1//FAKE-gsc-refresh-token-abcdefghijklmnop"
ACCESS = "ya29.FAKE-gsc-access-token-qrstuvwxyz0123"
SECRETS = (CLIENT_SECRET, REFRESH, ACCESS)
SITE = "sc-domain:example.com"
QUERY_PATH = "sites/sc-domain%3Aexample.com/searchAnalytics/query"
TODAY = datetime.date(2026, 11, 8)


class FakeGoogle:
    """token の窓口と Search Console の偽物。`routes[(method, url)]` に (status, 値) か、その並び（呼ぶ順）。"""

    def __init__(self, routes=None, refresh=(200, {"access_token": ACCESS, "expires_in": 3599})):
        self.routes = dict(routes or {})
        self.refresh = refresh
        self.calls = []

    def __call__(self, method, url, headers, body):
        if url == "https://oauth2.googleapis.com/token":
            status, value = self.refresh
            return status, json.dumps(value).encode()
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body else None})
        key = (method, url[len(api.BASE_URL):] if url.startswith(api.BASE_URL) else url)
        answer = self.routes[key]
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
    monkeypatch.setenv(auth.TOKEN_ENV, str(tmp_path / "gsc-token.json"))
    auth.save_token({"refresh_token": REFRESH})
    fake = FakeGoogle()
    monkeypatch.setattr(api, "default_transport", fake)
    monkeypatch.setattr(auth, "default_transport", fake)
    monkeypatch.setattr(gsc_cli, "today_pacific", lambda: TODAY)
    return fake


def run(argv, capsys):
    rc = cli.main(argv)
    out = capsys.readouterr()
    for secret in SECRETS:
        assert secret not in out.out + out.err, "秘密が出た"
    return rc, out


def _row(keys, clicks, impressions, ctr=0.05, position=4.26):
    return {"keys": keys, "clicks": clicks, "impressions": impressions, "ctr": ctr, "position": position}


# ------------------------------------------------------------------ 形の検査

@pytest.mark.parametrize("value", ["sc-domain:example.com", "sc-domain:sub.example.co.jp", "https://example.com/",
                                   "http://example.com/blog/", "https://example.com:8443/"])
def test_プロパティの形(value):
    assert api.site_url(value) == value


@pytest.mark.parametrize("value", ["example.com", "https://example.com", "sc-domain:", "sc-domain:Example.com",
                                   "sc-domain:exa mple.com", "ftp://example.com/", "https://example.com/?q=1",
                                   "https://u:p@example.com/", "", None, "https://exa mple.com/"])
def test_プロパティの形が違えば断る(value):
    with pytest.raises(GscRefused) as caught:
        api.site_url(value)
    assert caught.value.code == "gsc_site_invalid"


@pytest.mark.parametrize("site,url,inside", [
    ("sc-domain:example.com", "https://example.com/a", True),
    ("sc-domain:example.com", "http://blog.example.com/a", True),
    ("sc-domain:example.com", "https://badexample.com/a", False),
    ("https://example.com/blog/", "https://example.com/blog/x", True),
    ("https://example.com/blog/", "https://example.com/shop/x", False),
])
def test_検査のURLはプロパティの中(site, url, inside):
    assert api.url_in_site(site, url) is inside


def test_頁の頭はRE2の式に逃がして渡す():
    found = api.filters(page_prefix="https://example.com/a.b/(x)?", query_contains=" 布 団 ")
    assert found == [
        {"dimension": "page", "operator": "includingRegex", "expression": r"^https://example\.com/a\.b/\(x\)\?"},
        {"dimension": "query", "operator": "contains", "expression": "布 団"}]


def test_期間の既定は3日前までの28日_太平洋時間():
    args = argparse.Namespace(days=None, since=None, until=None)
    assert gsc_cli.date_range(args, today=TODAY) == ("2026-10-09", "2026-11-05")
    args = argparse.Namespace(days=None, since="2026-10-01", until=None)
    assert gsc_cli.date_range(args, today=TODAY) == ("2026-10-01", "2026-11-05")
    assert gsc_cli.compare_ranges(7, today=TODAY) == (("2026-10-30", "2026-11-05"), ("2026-10-23", "2026-10-29"))


# ------------------------------------------------------------------ 正常

def test_sitesは見えるプロパティと権限(google, capsys):
    google.routes[("GET", "sites")] = (200, {"siteEntry": [
        {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://a.example.org/", "permissionLevel": "siteRestrictedUser"},
        {"siteUrl": "https://b.example.org/", "permissionLevel": "somethingNew"}]})
    rc, out = run(["gsc", "sites", "--json"], capsys)
    assert rc == 0
    assert json.loads(out.out) == {"sites": [
        {"site": "https://a.example.org/", "permission": "siteRestrictedUser"},
        {"site": "https://b.example.org/", "permission": None},
        {"site": "sc-domain:example.com", "permission": "siteOwner"}]}
    rc, out = run(["gsc", "sites"], capsys)
    assert rc == 0 and "sc-domain:example.com  権限 所有者" in out.out and "制限付き" in out.out
    assert google.calls[0]["headers"]["Authorization"] == f"Bearer {ACCESS}"


def test_sitesが0件なら次の一手を言う(google, capsys):
    google.routes[("GET", "sites")] = (200, {})
    rc, out = run(["gsc", "sites"], capsys)
    assert rc == 0 and "0 件" in out.out


def test_queryは依頼の形と行(google, capsys):
    google.routes[("POST", QUERY_PATH)] = (200, {"rows": [
        _row(["布団 洗い方", "https://example.com/a"], 12.0, 340.0, 0.0352941, 3.456),
        _row(["布団 干し方", "https://example.com/b"], 3.0, 99.0, 0.0303, 8.04)],
        "responseAggregationType": "byPage"})
    rc, out = run(["gsc", "query", "--site", SITE, "--dim", "query", "page", "--page-prefix", "https://example.com/",
                   "--query-contains", "布団", "--limit", "500", "--json"], capsys)
    assert rc == 0, out.err
    call = google.calls[0]
    assert call["method"] == "POST" and call["url"] == api.BASE_URL + QUERY_PATH
    assert call["body"] == {"startDate": "2026-10-09", "endDate": "2026-11-05", "dimensions": ["query", "page"],
                            "type": "web", "rowLimit": 500, "startRow": 0, "dimensionFilterGroups": [
                                {"groupType": "and", "filters": [
                                    {"dimension": "page", "operator": "includingRegex",
                                     "expression": r"^https://example\.com/"},
                                    {"dimension": "query", "operator": "contains", "expression": "布団"}]}]}
    assert "dataState" not in call["body"]  # 確定した数だけ
    value = json.loads(out.out)
    assert value["rows"][0] == {"query": "布団 洗い方", "page": "https://example.com/a", "clicks": 12,
                                "impressions": 340, "ctr": 0.0353, "position": 3.5}
    assert value["since"] == "2026-10-09" and value["until"] == "2026-11-05"


def test_queryの文の出力(google, capsys):
    google.routes[("POST", "sites/https%3A%2F%2Fexample.com%2F/searchAnalytics/query")] = (200, {"rows": [
        _row(["2026-11-01"], 1234, 56789, 0.0217, 7.3)]})
    rc, out = run(["gsc", "query", "--site", "https://example.com/", "--dim", "date", "--days", "7"], capsys)
    assert rc == 0, out.err
    assert "2026-10-30〜2026-11-05" in out.out
    assert "2026-11-01  クリック 1,234  表示 56,789  CTR 2.2%  平均掲載順位 7.3" in out.out
    assert google.calls[0]["body"]["dimensions"] == ["date"] and google.calls[0]["body"]["rowLimit"] == 100


def test_queryが0行なら広げ方を言う(google, capsys):
    google.routes[("POST", QUERY_PATH)] = (200, {"responseAggregationType": "byProperty"})
    rc, out = run(["gsc", "query", "--site", SITE], capsys)
    assert rc == 0 and "0 件" in out.out


def test_compareは前の期間との差を上から(google, capsys):
    google.routes[("POST", QUERY_PATH)] = [
        (200, {"rows": [_row(["a"], 30, 300, position=3.0), _row(["b"], 5, 100), _row(["new"], 4, 10)]}),
        (200, {"rows": [_row(["a"], 10, 200, position=5.0), _row(["b"], 9, 120), _row(["gone"], 2, 50)]}),
    ]
    rc, out = run(["gsc", "compare", "--site", SITE, "--days", "7", "--json"], capsys)
    assert rc == 0, out.err
    bodies = [c["body"] for c in google.calls]
    assert [(b["startDate"], b["endDate"]) for b in bodies] == [("2026-10-30", "2026-11-05"),
                                                                 ("2026-10-23", "2026-10-29")]
    assert all(b["dimensions"] == ["query"] and b["rowLimit"] == gsc_cli.COMPARE_FETCH for b in bodies)
    value = json.loads(out.out)
    assert [r["query"] for r in value["rising"]] == ["a", "new"]
    assert [r["query"] for r in value["falling"]] == ["b", "gone"]
    a = value["rising"][0]
    assert (a["clicks_before"], a["clicks"], a["clicks_delta"], a["position_before"], a["position"]) == (10, 30, 20,
                                                                                                         5.0, 3.0)
    assert value["falling"][1]["position"] is None
    assert value["totals"] == {"clicks": 39, "clicks_before": 21, "impressions": 410, "impressions_before": 370}


def test_compareの文の出力(google, capsys):
    google.routes[("POST", QUERY_PATH)] = [
        (200, {"rows": [_row(["https://example.com/a"], 3, 30)]}), (200, {"rows": []})]
    rc, out = run(["gsc", "compare", "--site", SITE, "--dim", "page"], capsys)
    assert rc == 0, out.err
    assert "伸びた頁（1 件）" in out.out and "落ちた頁（0 件）" in out.out and "（+3）" in out.out


def test_inspectは索引の様子(google, capsys):
    google.routes[("POST", api.INSPECT_URL)] = (200, {"inspectionResult": {
        "inspectionResultLink": "https://search.google.com/search-console/inspect?resource_id=x",
        "indexStatusResult": {"verdict": "PASS", "coverageState": "送信して登録されました",
                              "robotsTxtState": "ALLOWED", "indexingState": "INDEXING_ALLOWED",
                              "lastCrawlTime": "2026-11-01T01:02:03Z", "pageFetchState": "SUCCESSFUL",
                              "googleCanonical": "https://example.com/a", "userCanonical": "https://example.com/a",
                              "crawledAs": "MOBILE"}}})
    rc, out = run(["gsc", "inspect", "--site", SITE, "--url", "https://example.com/a", "--json"], capsys)
    assert rc == 0, out.err
    assert google.calls[0]["body"] == {"inspectionUrl": "https://example.com/a", "siteUrl": SITE, "languageCode": "ja"}
    value = json.loads(out.out)
    assert value["verdict"] == "PASS" and value["page_fetch_state"] == "SUCCESSFUL" and value["crawled_as"] == "MOBILE"
    assert value["link"].startswith("https://search.google.com/")
    rc, out = run(["gsc", "inspect", "--site", SITE, "--url", "https://example.com/a"], capsys)
    assert rc == 0 and "判定 PASS" in out.out and "最後の巡回 2026-11-01T01:02:03Z" in out.out


# ------------------------------------------------------------------ Google の断り → 符丁

def _error(status, word=None, reason=None, info=None, message="secret https://example.com/?token=SECRETQUERY"):
    error = {"code": status, "message": message}
    if word:
        error["status"] = word
    if reason:
        error["errors"] = [{"reason": reason, "message": message}]
    if info:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": info}]
    return {"error": error}


@pytest.mark.parametrize("status,payload,code,detail", [
    (403, _error(403, "PERMISSION_DENIED", "accessNotConfigured", "SERVICE_DISABLED"), "gsc_api_not_enabled",
     "SERVICE_DISABLED"),
    (403, _error(403, "PERMISSION_DENIED", "insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"),
     "gsc_authentication_error", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"),
    (403, _error(403, "PERMISSION_DENIED", "forbidden"), "gsc_permission_denied", "forbidden"),
    (401, _error(401, "UNAUTHENTICATED"), "gsc_authentication_error", "UNAUTHENTICATED"),
    (429, _error(429, "RESOURCE_EXHAUSTED", "rateLimitExceeded"), "gsc_quota_exceeded", "rateLimitExceeded"),
    (404, _error(404, "NOT_FOUND", "notFound"), "gsc_site_unknown", "notFound"),
    (503, b"<html>down</html>", "gsc_unavailable", "HTTP 503"),
    (400, _error(400, "INVALID_ARGUMENT", "badRequest"), "gsc_request_rejected", "badRequest"),
])
def test_Googleの断りの写し(status, payload, code, detail):
    value = json.loads(payload) if isinstance(payload, bytes) and payload.startswith(b"{") else payload
    refused = api.refusal_for(status, value if isinstance(value, dict) else None)
    assert (refused.code, refused.detail) == (code, detail)
    assert "SECRETQUERY" not in str(refused)


def _arrange(name, google, monkeypatch):
    """符丁ごとに、その断りが出る場面を作って argv を返す。"""
    sites = ["gsc", "sites"]
    query = ["gsc", "query", "--site", SITE]
    if name == "gsc_client_missing":
        monkeypatch.setenv(auth.CLIENT_ENV, "/nonexistent/thth-test-client.json")
        return sites
    if name == "gsc_client_invalid":
        path = auth.client_path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"web": {"client_id": "x"}}))
        return sites
    if name == "gsc_token_missing":
        monkeypatch.setenv(auth.TOKEN_ENV, "/nonexistent/thth-test-gsc-token.json")
        return sites
    if name == "gsc_token_invalid":
        with open(auth.token_path(), "w", encoding="utf-8") as handle:
            handle.write("{")
        return sites
    if name == "gsc_token_revoked":
        google.refresh = (400, {"error": "invalid_grant"})
        return sites
    if name == "gsc_token_refresh_failed":
        google.refresh = (503, {"error": "temporarily_unavailable"})
        return sites
    if name == "gsc_site_invalid":
        return ["gsc", "query", "--site", "example.com"]
    if name == "gsc_date_invalid":
        return [*query, "--days", "7", "--since", "2026-10-01"]
    if name == "gsc_dim_invalid":
        return [*query, "--dim", "query", "query"]
    if name == "gsc_page_prefix_invalid":
        return [*query, "--page-prefix", "/blog/"]
    if name == "gsc_query_contains_invalid":
        return [*query, "--query-contains", "   "]
    if name == "gsc_url_invalid":
        return ["gsc", "inspect", "--site", SITE, "--url", "not a url"]
    if name == "gsc_inspect_url_outside":
        return ["gsc", "inspect", "--site", SITE, "--url", "https://other.example.net/a"]
    if name == "gsc_response_invalid":
        google.routes[("GET", "sites")] = (200, b"<html>not json</html>")
        return sites
    if name == "gsc_unavailable":
        def down(method, url, headers, body):
            raise GscRefused("gsc_unavailable")
        monkeypatch.setattr(api, "default_transport", down)
        monkeypatch.setattr(auth, "default_transport", down)
        return sites
    google_side = {
        "gsc_site_unknown": (404, _error(404, "NOT_FOUND", "notFound")),
        "gsc_permission_denied": (403, _error(403, "PERMISSION_DENIED", "forbidden")),
        "gsc_authentication_error": (401, _error(401, "UNAUTHENTICATED")),
        "gsc_api_not_enabled": (403, _error(403, "PERMISSION_DENIED", None, "SERVICE_DISABLED")),
        "gsc_quota_exceeded": (429, _error(429, "RESOURCE_EXHAUSTED")),
        "gsc_request_rejected": (400, _error(400, "INVALID_ARGUMENT", "badRequest")),
    }
    if name in google_side:
        google.routes[("POST", QUERY_PATH)] = google_side[name]
        return query
    return None


# 認可の流れの符丁は test_gsc_auth.py が場面を作る（ブラウザの戻り）。
AUTH_FLOW = {"gsc_auth_denied", "gsc_auth_timeout", "gsc_auth_state_mismatch", "gsc_auth_exchange_failed",
             "gsc_no_refresh_token"}


@pytest.mark.parametrize("name", sorted(wire.REASONS - AUTH_FLOW))
def test_符丁ごとにCLIが3つの行を出し秘密を出さない(name, google, monkeypatch, capsys):
    argv = _arrange(name, google, monkeypatch)
    assert argv is not None, f"{name} の場面が無い"
    rc, out = run(argv, capsys)
    assert rc == 2 and out.out == ""
    lines = out.err.splitlines()
    assert lines[0].startswith(f"{name}: "), out.err
    assert any(line.startswith("通すには: ") for line in lines) and any(line.startswith("もっと良く: ") for line in lines)
    assert "SECRETQUERY" not in out.err
    rc, out = run([*argv, "--json"], capsys)
    value = json.loads(out.out)
    assert value["ok"] is False and value["code"] == name and value["why"] and value["fix"] and value["better"]
    assert "SECRETQUERY" not in out.out


def test_引数の断りは網に出る前(google, capsys):
    for argv in (["gsc", "query", "--site", "bad"], ["gsc", "compare", "--site", SITE, "--days", "999"],
                 ["gsc", "inspect", "--site", SITE, "--url", "https://other.example.net/"]):
        rc, _ = run(argv, capsys)
        assert rc == 2
    assert google.calls == []


@pytest.mark.parametrize("name", sorted(wire.REASONS))
def test_符丁は目録に3つがそろう(name):
    entry = refusal_catalog.CATALOG[name]
    assert entry["why"] and entry["fix"]["text"] and entry["better"] and entry["no_better"] is None
    assert entry["doors"] == ("cli",) and entry["since"] == "5.12.6"


def test_GscRefusedは知らない符丁と形の違う詳細を通さない():
    refused = GscRefused("gsc_nope", "has\nnewline " + ACCESS * 3)
    assert refused.code == "gsc_request_rejected" and refused.detail is None


def test_リダイレクト先の符丁はurlのクエリを詳細に入れない():
    refused = api.refusal_for(400, {"error": {"status": "https://x/?q=SECRETQUERY"}})
    assert refused.detail == "HTTP 400"


def test_URLのquery_stringは断りに出ない(google, capsys):
    rc, out = run(["gsc", "inspect", "--site", SITE, "--url", "https://other.example.net/a?token=SECRETQUERY"], capsys)
    assert rc == 2 and "SECRETQUERY" not in out.err
    parsed = urllib.parse.urlsplit(api.BASE_URL)
    assert parsed.scheme == "https"
