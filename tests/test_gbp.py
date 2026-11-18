"""`thth gbp …`（Google ビジネス プロフィール）。偽の Google で回す（本物の網に出ない・認可も打たない）。

縛ること: 店の一覧・show は残さない・special は二段確認でその日だけ替わる・営業の時間の無い店と過去の日は先に断る・
post は二段確認で本文が送る値に入る・Google の断りの写し・秘密（client_secret・token）が出力に出ない。
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import urllib.parse
from pathlib import Path

import pytest

from thth import cli, refusal_catalog
from thth.gads import auth as gads_auth
from thth.gbp import api, auth, wire
from thth.gbp import cli as gbp_cli
from thth.gbp.wire import GbpRefused
from thth.gsc import auth as gsc_auth

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET = "FAKE-client-secret-0123456789"
REFRESH = "1//FAKE-gbp-refresh-token-abcdefghijklmnop"
ACCESS = "ya29.FAKE-gbp-access-token-qrstuvwxyz0123"
SECRETS = (CLIENT_SECRET, REFRESH, ACCESS)
TODAY = datetime.date(2026, 11, 19)
ACC = "accounts/111"
LOC = "locations/222"
REGULAR = {"periods": [{"openDay": "MONDAY", "openTime": {"hours": 9}, "closeDay": "MONDAY",
                        "closeTime": {"hours": 18}}]}
SPECIAL_NOW = [
    {"startDate": {"year": 2026, "month": 12, "day": 29}, "closed": True},
    {"startDate": {"year": 2026, "month": 12, "day": 24}, "openTime": {"hours": 10},
     "endDate": {"year": 2026, "month": 12, "day": 24}, "closeTime": {"hours": 15}, "closed": False},
]


def _location(**extra):
    base = {"name": LOC, "title": "山手日本語学校", "websiteUri": "https://example.invalid/",
            "regularHours": REGULAR, "specialHours": {"specialHourPeriods": json.loads(json.dumps(SPECIAL_NOW))},
            "openInfo": {"status": "OPEN"}, "metadata": {"mapsUri": "https://maps.google.com/?cid=1"}}
    base.update(extra)
    return base


class FakeGoogle:
    """token の窓口とビジネス プロフィールの偽物。`routes[(method, 道)]` に (status, 値)。道は query を除いた URL。"""

    def __init__(self, routes=None):
        self.routes = {
            ("GET", api.ACCOUNTS_URL): (200, {"accounts": [{"name": ACC, "accountName": "山手", "type": "PERSONAL"}]}),
            ("GET", api.INFO_BASE + f"{ACC}/locations"): (200, {"locations": [_location()]}),
            ("GET", api.INFO_BASE + LOC): (200, _location()),
            ("PATCH", api.INFO_BASE + LOC): (200, _location()),
            ("GET", api.V4_BASE + f"{ACC}/{LOC}/localPosts"): (200, {"localPosts": [
                {"name": f"{ACC}/{LOC}/localPosts/9", "topicType": "STANDARD", "state": "LIVE", "summary": "冬の講座",
                 "createTime": "2026-11-01T00:00:00Z", "searchUrl": "https://local.google.com/place?x"}]}),
            ("POST", api.V4_BASE + f"{ACC}/{LOC}/localPosts"): (200, {
                "name": f"{ACC}/{LOC}/localPosts/10", "state": "PROCESSING", "searchUrl": "https://local.google.com/p"}),
        }
        self.routes.update(routes or {})
        self.calls = []

    def __call__(self, method, url, headers, body):
        if url == "https://oauth2.googleapis.com/token":
            return 200, json.dumps({"access_token": ACCESS, "expires_in": 3599}).encode()
        parsed = urllib.parse.urlsplit(url)
        path = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        self.calls.append({"method": method, "path": path, "query": dict(urllib.parse.parse_qsl(parsed.query)),
                           "headers": headers, "body": json.loads(body) if body else None})
        answer = self.routes[(method, path)]
        if isinstance(answer, Exception):
            raise answer
        status, value = answer
        return status, (value if isinstance(value, bytes) else json.dumps(value).encode())

    def writes(self):
        return [c for c in self.calls if c["method"] in ("PATCH", "POST")]


@pytest.fixture
def google(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {
        "client_id": "123-fake.apps.googleusercontent.com", "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"}}), encoding="utf-8")
    monkeypatch.setenv(auth.CLIENT_ENV, str(client))
    monkeypatch.setenv(auth.TOKEN_ENV, str(tmp_path / "cfg" / "gbp-token.json"))
    monkeypatch.setenv(gads_auth.TOKEN_ENV, str(tmp_path / "gads-token.json"))
    monkeypatch.setenv(gsc_auth.TOKEN_ENV, str(tmp_path / "gsc-token.json"))
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    auth.save_token({"schema": auth.TOKEN_SCHEMA, "refresh_token": REFRESH, "scope": auth.SCOPE})
    fake = FakeGoogle()
    monkeypatch.setattr(api, "default_transport", fake)
    monkeypatch.setattr(auth, "default_transport", fake)
    monkeypatch.setattr(gbp_cli, "today_jst", lambda: TODAY)
    monkeypatch.chdir(tmp_path)
    return fake


def run(argv, capsys):
    rc = cli.main(argv)
    out = capsys.readouterr()
    for secret in SECRETS:
        assert secret not in out.out + out.err, "秘密が出た"
    return rc, out


def _files(root: Path) -> set:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


# ------------------------------------------------------------------ 認可の置き場

def test_scopeはbusiness_manageで鍵はgadsとgscと別のファイル(google, tmp_path):
    assert auth.SCOPE == "https://www.googleapis.com/auth/business.manage"
    assert auth.TOKEN_ENV == "THTH_GOOGLE_GBP_TOKEN"
    assert auth.DEFAULT_TOKEN.endswith("google-gbp-token.json")
    assert auth.CLIENT_ENV == gads_auth.CLIENT_ENV and auth.DEFAULT_CLIENT == gads_auth.DEFAULT_CLIENT
    assert auth.token_path() not in (gads_auth.token_path(), gsc_auth.token_path())
    client = auth.load_client()
    url = auth.authorization_url(client, "http://127.0.0.1:5", "st", auth.google_oauth.new_verifier())
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert params["scope"] == auth.SCOPE and params["code_challenge_method"] == "S256"
    assert CLIENT_SECRET not in url
    assert oct(os.stat(auth.token_path()).st_mode & 0o777) == "0o600"


def test_doctorは段ごとに済を出し秘密を出さない(google, capsys):
    rc, out = run(["gbp", "doctor"], capsys)
    assert rc == 0, out
    assert "済 client" in out.out and "済 token" in out.out and "済 scope" in out.out and "済 accounts: 1 件" in out.out


def test_doctorはscopeが無い認可を前向きに断る(google, capsys):
    auth.save_token({"refresh_token": REFRESH, "scope": "https://www.googleapis.com/auth/webmasters.readonly"})
    rc, out = run(["gbp", "doctor", "--json"], capsys)
    value = json.loads(out.out)
    assert rc == 2 and value["code"] == "gbp_scope_missing"
    assert value["checks"][-1] == {"check": "scope", "ok": False, "detail": "gbp_scope_missing"}
    assert google.calls == []


def test_doctorは認可が無ければtoken_missing(google, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv(auth.TOKEN_ENV, str(tmp_path / "none.json"))
    rc, out = run(["gbp", "doctor"], capsys)
    assert rc == 2 and "gbp_token_missing" in out.err and "thth gbp auth" in out.err


# ------------------------------------------------------------------ 読む

def test_locationsはaccountsから店のidと名前を出す(google, capsys):
    rc, out = run(["gbp", "locations", "--json"], capsys)
    assert rc == 0
    value = json.loads(out.out)
    assert value["storage"] == "display_only"
    row = value["locations"][0]
    assert row["location"] == LOC and row["title"] == "山手日本語学校" and row["account"] == ACC
    listed = [c for c in google.calls if c["path"].endswith("/locations")][0]
    assert listed["query"]["readMask"] == api.LIST_MASK and listed["query"]["pageSize"] == "100"
    assert all(c["headers"]["Authorization"] == f"Bearer {ACCESS}" for c in google.calls)


def test_locationsは次の頁も読む(google):
    pages = iter([(200, {"accounts": [{"name": ACC}], "nextPageToken": "p2"}),
                  (200, {"accounts": [{"name": "accounts/333"}]})])
    seen = []

    def call(method, url, headers, body):
        if url.startswith(api.ACCOUNTS_URL):
            seen.append(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)).get("pageToken"))
            status, value = next(pages)
            return status, json.dumps(value).encode()
        return google(method, url, headers, body)
    google.routes[("GET", api.INFO_BASE + "accounts/333/locations")] = (
        200, {"locations": [_location(name="locations/444")]})
    rows = api.Client(transport=call).all_locations()
    assert [r["location"] for r in rows] == [LOC, "locations/444"]
    assert seen == [None, "p2"]


def test_showは時間と臨時の時間を出しファイルに何も残さない(google, capsys, tmp_path):
    before = _files(tmp_path)
    rc, out = run(["gbp", "show", LOC], capsys)
    assert rc == 0, out
    assert "月 09:00〜18:00" in out.out and "2026-12-24 10:00〜15:00" in out.out and "2026-12-29 終日休み" in out.out
    assert "営業中" in out.out and "https://example.invalid/" in out.out and "保存しません" in out.out
    rc, out = run(["gbp", "show", LOC, "--json"], capsys)
    assert json.loads(out.out)["storage"] == "display_only"
    assert _files(tmp_path) == before, "show が何かを書いた（30 日の決まり）"
    assert google.calls[0]["query"]["readMask"] == api.SHOW_MASK


def test_postsはv4の一覧を出す(google, capsys):
    rc, out = run(["gbp", "posts", f"{ACC}/{LOC}", "--json"], capsys)
    assert rc == 0
    value = json.loads(out.out)
    assert value["posts"][0]["summary"] == "冬の講座" and value["posts"][0]["state"] == "LIVE"
    assert google.calls[-1]["query"]["pageSize"] == "20"


def test_postsはaccountを省くと見えるアカウントから探す(google, capsys):
    rc, out = run(["gbp", "posts", LOC], capsys)
    assert rc == 0 and "冬の講座" in out.out
    rc, out = run(["gbp", "posts", "locations/999"], capsys)
    assert rc == 2 and "gbp_location_unknown" in out.err


# ------------------------------------------------------------------ 読む口はファイルに書かない（柵）

WRITE_CALLS = {"open", "write_text", "write_bytes", "save_token", "dump", "mkdir", "makedirs"}
OS_WRITE_CALLS = {"replace", "rename", "remove", "unlink", "link", "symlink"}


def write_calls(path: Path) -> list[str]:
    """モジュールの中で、ファイルに書きうる呼び出しの名前（`auth.py` のほかで 0 のはず）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            owner = getattr(func.value, "id", None) if isinstance(func, ast.Attribute) else None
            if name in WRITE_CALLS or (owner in ("os", "shutil") and (name in OS_WRITE_CALLS or owner == "shutil")):
                found.append(name)
    return found


def test_gbpの口はauthのほかにファイルに書く呼び出しを持たない():
    """Google の決まり（口の中身は 30 日まで）: 読んだ中身を貯める道を作らない。書くのは認可の置き場（auth.py）だけ。"""
    gbp_dir = ROOT / "thth" / "gbp"
    found = {p.name: write_calls(p) for p in gbp_dir.glob("*.py") if p.name != "auth.py"}
    assert {k: v for k, v in found.items() if v} == {}


def test_壊れた例_書く呼び出しの柵がopenを拾う(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def keep(v):\n    with open('cache.json', 'w') as f:\n        json.dump(v, f)\n    os.replace('a', 'b')\n    'x'.replace('a', 'b')\n",
                   encoding="utf-8")
    assert sorted(write_calls(bad)) == ["dump", "open", "replace"]


def test_gbpはMCPと遠くの道に出ない():
    for path in (ROOT / "mcp" / "server.py", ROOT / "thth" / "remote.py"):
        text = path.read_text(encoding="utf-8")
        assert "gbp" not in text, path


# ------------------------------------------------------------------ special（二段確認）

def _special(capsys, *extra):
    return run(["gbp", "special", LOC, "--date", "2026-12-24", *extra], capsys)


def test_specialの一段目は何も送らずdigestを出す(google, capsys):
    rc, out = _special(capsys, "--closed", "--by", "masaru")
    assert rc == 1, out
    assert google.writes() == []
    assert "変えません（確認の一段目です）" in out.out and "2026-12-24 終日休み" in out.out
    assert "外す行: 2026-12-24 10:00〜15:00" in out.out and "by  : masaru" in out.out
    assert "digest: " in out.out and "--confirm" in out.out


def test_specialの二段目はその日だけ替えて全体をPATCHする(google, capsys):
    rc, out = _special(capsys, "--open", "11:00", "--close", "14:30", "--by", "masaru", "--json")
    assert rc == 1
    digest = json.loads(out.out)["digest"]
    rc, out = _special(capsys, "--open", "11:00", "--close", "14:30", "--by", "masaru", "--confirm", digest)
    assert rc == 0, out
    (patch,) = google.writes()
    assert patch["method"] == "PATCH" and patch["path"] == api.INFO_BASE + LOC
    assert patch["query"] == {"updateMask": "specialHours"}
    periods = patch["body"]["specialHours"]["specialHourPeriods"]
    assert periods == [
        {"startDate": {"year": 2026, "month": 12, "day": 24}, "openTime": {"hours": 11, "minutes": 0},
         "closeTime": {"hours": 14, "minutes": 30}, "closed": False, "endDate": {"year": 2026, "month": 12, "day": 24}},
        SPECIAL_NOW[0],  # 12/29 の休みはそのまま
    ]
    assert "送りました" in out.out


def test_specialは新しい日を足すときほかの日を残す(google, capsys):
    argv = ["gbp", "special", LOC, "--date", "2026-12-31", "--open", "18:00", "--close", "02:00", "--by", "m", "--json"]
    rc, out = run(argv, capsys)
    value = json.loads(out.out)
    assert value["replaces"] == [] and len(value["special_hours_after"]) == 3
    assert value["period"]["endDate"] == {"year": 2027, "month": 1, "day": 1}  # 日をまたぐ
    rc, out = run(argv[:-1] + ["--confirm", value["digest"]], capsys)
    assert rc == 0, out
    assert len(google.writes()[0]["body"]["specialHours"]["specialHourPeriods"]) == 3


def test_specialは一段目のあとにGoogleの臨時の時間が変わればdigestで止める(google, capsys):
    rc, out = _special(capsys, "--closed", "--by", "m", "--json")
    digest = json.loads(out.out)["digest"]
    changed = _location(specialHours={"specialHourPeriods": [SPECIAL_NOW[0]]})
    google.routes[("GET", api.INFO_BASE + LOC)] = (200, changed)
    rc, out = _special(capsys, "--closed", "--by", "m", "--confirm", digest)
    assert rc == 1 and "gbp_digest_mismatch" in out.err
    assert google.writes() == []


def test_specialは営業の時間の無い店を先に断る(google, capsys):
    google.routes[("GET", api.INFO_BASE + LOC)] = (200, _location(regularHours=None))
    rc, out = _special(capsys, "--closed", "--by", "m")
    assert rc == 2 and "gbp_regular_hours_missing" in out.err
    assert "通すには" in out.err or "営業の時間を入れてから" in out.err
    assert google.writes() == []


def test_specialは過去の日を網に出る前に断る(google, capsys):
    rc, out = run(["gbp", "special", LOC, "--date", "2026-11-18", "--closed", "--by", "m"], capsys)
    assert rc == 2 and "gbp_date_past" in out.err
    assert google.calls == []


def test_specialは今日を通す(google, capsys):
    rc, out = run(["gbp", "special", LOC, "--date", TODAY.isoformat(), "--closed", "--by", "m"], capsys)
    assert rc == 1, out


@pytest.mark.parametrize("extra,code", [
    ([], "gbp_hours_args_invalid"),
    (["--closed", "--open", "10:00", "--close", "11:00"], "gbp_hours_args_invalid"),
    (["--open", "10:00"], "gbp_hours_args_invalid"),
    (["--open", "25:00", "--close", "11:00"], "gbp_time_invalid"),
    (["--open", "10:00", "--close", "10:00"], "gbp_time_invalid"),
    (["--open", "10:00", "--close", "24:30"], "gbp_time_invalid"),
])
def test_specialの時刻の形(google, capsys, extra, code):
    rc, out = _special(capsys, *extra, "--by", "m")
    assert rc == 2 and code in out.err
    assert google.calls == []


def test_specialはbyが無ければ断る(google, capsys):
    rc, out = _special(capsys, "--closed")
    assert rc == 1 and "by_required" in out.err and google.calls == []


def test_日付と店の形():
    with pytest.raises(GbpRefused) as caught:
        api.parse_date("2026-02-30")
    assert caught.value.code == "gbp_date_invalid"
    assert api.parse_location("locations/12") == (None, "12")
    assert api.parse_location("accounts/3/locations/12") == ("3", "12")
    for bad in ("locations/x", "accounts/3/locations/", "../locations/1", "locations/1?x=1"):
        with pytest.raises(GbpRefused):
            api.parse_location(bad)
    assert api.parse_time("24:00") == {"hours": 24, "minutes": 0}


# ------------------------------------------------------------------ post（二段確認）

TEXT = "冬の短期講座の受付を始めました。\n12 月 1 日から。"


def test_postの一段目は送らずに本文を見せる(google, capsys):
    rc, out = run(["gbp", "post", LOC, "--text", TEXT, "--url", "https://example.invalid/winter", "--by", "m"], capsys)
    assert rc == 1, out
    assert google.writes() == []
    assert "出しません（確認の一段目です）" in out.out and "冬の短期講座の受付を始めました。" in out.out
    assert "LEARN_MORE → https://example.invalid/winter" in out.out


def test_postの二段目は本文を送る値に入れる(google, capsys):
    base = ["gbp", "post", LOC, "--text", TEXT, "--url", "https://example.invalid/winter", "--action", "BOOK",
            "--by", "m"]
    rc, out = run(base + ["--json"], capsys)
    digest = json.loads(out.out)["digest"]
    rc, out = run(base + ["--confirm", digest], capsys)
    assert rc == 0, out
    (post,) = google.writes()
    assert post["method"] == "POST" and post["path"] == api.V4_BASE + f"{ACC}/{LOC}/localPosts"
    assert post["body"] == {"languageCode": "ja", "summary": TEXT, "topicType": "STANDARD",
                            "callToAction": {"actionType": "BOOK", "url": "https://example.invalid/winter"}}
    assert "出しました" in out.out and "処理中" in out.out


def test_postは本文を変えればdigestが変わる(google, capsys):
    rc, out = run(["gbp", "post", LOC, "--text", TEXT, "--by", "m", "--json"], capsys)
    digest = json.loads(out.out)["digest"]
    rc, out = run(["gbp", "post", LOC, "--text", TEXT + "!", "--by", "m", "--confirm", digest], capsys)
    assert rc == 1 and "gbp_digest_mismatch" in out.err and google.writes() == []


def test_postの打ち直しの命令は改行を含む本文でも同じdigestになる(google, capsys):
    import shlex
    rc, out = run(["gbp", "post", LOC, "--text", TEXT, "--by", "山手 事務", ], capsys)
    argv = shlex.split(out.out.split("送るなら: ", 1)[1])
    assert argv[0] == "thth"
    rc, out = run(argv[1:], capsys)
    assert rc == 0, out
    assert google.writes()[0]["body"]["summary"] == TEXT


@pytest.mark.parametrize("text,detail", [("   ", "empty"), ("あ" * 1501, "length 1501 over 1500"),
                                         ("a\x07b", "control_character")])
def test_postの本文の形(google, capsys, text, detail):
    rc, out = run(["gbp", "post", LOC, "--text", text, "--by", "m"], capsys)
    assert rc == 2 and "gbp_post_text_invalid" in out.err and detail in out.err
    assert google.calls == []


def test_postの本文は1500字まで通る():
    assert api.post_body("あ" * 1500)["summary"] == "あ" * 1500


@pytest.mark.parametrize("url", ["ftp://x.example/", "https://u:p@x.example/", "https://exa mple/", "javascript:x"])
def test_postのURLの形(google, capsys, url):
    rc, out = run(["gbp", "post", LOC, "--text", "x", "--url", url, "--by", "m"], capsys)
    assert rc == 2 and "gbp_post_url_invalid" in out.err


def test_postはURLなしのactionを断る(google, capsys):
    rc, out = run(["gbp", "post", LOC, "--text", "x", "--action", "BOOK", "--by", "m"], capsys)
    assert rc == 2 and "gbp_post_url_invalid" in out.err


def test_書いたあとに届かなければ結果不明として断る(google, capsys):
    rc, out = run(["gbp", "post", f"{ACC}/{LOC}", "--text", "x", "--by", "m", "--json"], capsys)
    digest = json.loads(out.out)["digest"]
    google.routes[("POST", api.V4_BASE + f"{ACC}/{LOC}/localPosts")] = GbpRefused("gbp_unavailable")
    rc, out = run(["gbp", "post", f"{ACC}/{LOC}", "--text", "x", "--by", "m", "--confirm", digest], capsys)
    assert rc == 2 and "gbp_write_unknown" in out.err and "thth gbp show" in out.err


# ------------------------------------------------------------------ Google の断りの写し

def _err(status, reason=None, status_word=None, metadata=None, **extra):
    detail = {"@type": "type.googleapis.com/google.rpc.ErrorInfo"}
    if reason:
        detail["reason"] = reason
    if metadata:
        detail["metadata"] = metadata
    error = {"code": status, "message": "secret-looking message https://x/?token=abc", "details": [detail]}
    if status_word:
        error["status"] = status_word
    error.update(extra)
    return {"error": error}


@pytest.mark.parametrize("status,payload,code,write", [
    (403, _err(403, status_word="PERMISSION_DENIED"), "gbp_permission_denied", False),
    (429, _err(429, "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED"), "gbp_quota_exceeded", False),
    (429, _err(429, "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED", {"quota_limit_value": "0"}), "gbp_not_allowlisted", False),
    (403, _err(403, "RATE_LIMIT_EXCEEDED", "PERMISSION_DENIED", {"quota_limit_value": "0"}), "gbp_not_allowlisted", False),
    (403, _err(403, "SERVICE_DISABLED", "PERMISSION_DENIED",
               {"service": "mybusinessaccountmanagement.googleapis.com"}), "gbp_api_not_enabled", False),
    (403, _err(403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT"), "gbp_authentication_error", False),
    (401, _err(401, status_word="UNAUTHENTICATED"), "gbp_authentication_error", False),
    (404, _err(404, status_word="NOT_FOUND"), "gbp_location_unknown", False),
    (400, _err(400, status_word="INVALID_ARGUMENT"), "gbp_request_rejected", False),
    (503, _err(503, status_word="UNAVAILABLE"), "gbp_unavailable", False),
    (503, _err(503, status_word="UNAVAILABLE"), "gbp_write_unknown", True),
])
def test_Googleの断りを口の符丁に写す(status, payload, code, write):
    found = api.refusal_for(status, payload, write=write)
    assert found.code == code
    assert "token=abc" not in str(found) and "message" not in str(found)


def test_無効なAPIの名前を詳細に添える():
    found = api.refusal_for(403, _err(403, "SERVICE_DISABLED", metadata={"service": "mybusiness.googleapis.com"}))
    assert found.detail == "SERVICE_DISABLED mybusiness.googleapis.com"
    bad = api.refusal_for(403, _err(403, "SERVICE_DISABLED", metadata={"service": "evil.example/x?y"}))
    assert bad.detail == "SERVICE_DISABLED"


def test_CLIの断りは前向きな3つの行を出しGoogleの文を出さない(google, capsys):
    google.routes[("GET", api.ACCOUNTS_URL)] = (
        429, _err(429, "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED", {"quota_limit_value": "0"}))
    rc, out = run(["gbp", "locations"], capsys)
    assert rc == 2
    assert out.err.startswith("gbp_not_allowlisted: ") and "（Google: RATE_LIMIT_EXCEEDED）" in out.err
    assert "token=abc" not in out.err
    entry = refusal_catalog.CATALOG["gbp_not_allowlisted"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]


def test_符丁はすべて目録にある():
    missing = [code for code in wire.REASONS if code not in refusal_catalog.CATALOG]
    assert missing == []


def test_access_tokenとrefresh_tokenはどの出力にも出ない(google, capsys):
    for argv in (["gbp", "doctor", "--json"], ["gbp", "locations"], ["gbp", "show", LOC, "--json"],
                 ["gbp", "posts", LOC], ["gbp", "special", LOC, "--date", "2026-12-24", "--closed", "--by", "m"]):
        run(argv, capsys)  # run が秘密の有無を見る
    google.routes[("GET", api.INFO_BASE + LOC)] = (401, {"error": {"message": f"bad {ACCESS}", "status": "UNAUTHENTICATED"}})
    rc, out = run(["gbp", "show", LOC], capsys)
    assert rc == 2 and "gbp_authentication_error" in out.err


def test_通しの認可はgbpの置き場に600で残しgadsとgscの鍵に触らない(google, tmp_path, capsys):
    from tests.test_gsc_auth import FakeTokenEndpoint, _browser
    import stat
    endpoint = FakeTokenEndpoint(exchange=(200, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3599,
                                                 "scope": auth.SCOPE, "token_type": "Bearer"}))
    open_, seen = _browser(lambda p: {"code": "4/FAKE-gbp-code", "state": p["state"]})
    said = []
    value = auth.run(timeout=10, transport=endpoint, browser=open_, say=said.append)
    path = Path(auth.token_path())
    assert value["token_path"] == str(path) and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    stored = json.loads(path.read_text())
    assert stored["schema"] == "thth.gbp.token/1" and stored["scope"] == auth.SCOPE and "access_token" not in stored
    assert seen["scope"] == auth.SCOPE
    assert not (tmp_path / "gads-token.json").exists() and not (tmp_path / "gsc-token.json").exists()
    assert "ビジネス プロフィール" in said[0]
    out = "\n".join(said) + json.dumps(value) + "".join(capsys.readouterr())
    for secret in SECRETS + ("4/FAKE-gbp-code",):
        assert secret not in out
