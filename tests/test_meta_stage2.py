"""Meta の計画の段 2（Meta の共通層 `thth/meta/`・10/29）の柵。

正本は `docs/設計_MetaAPIを使い倒す計画_2026-10-29.md` の「B-1」と「段 2」。偽の Graph API（loopback・
応答のヘッダも返せる）と試験の中で作った値だけを使う。本物の Meta には触らない。

抜き出しの正しさの証拠は、段 0・段 1 の 4 つの試験（`test_facebook_adapter.py`・`test_facebook_auth.py`・
`test_facebook_stage1.py`・`test_meta_stage0.py`）を **1 行も変えずに**通すこと。ここは共通層が新しく
持つものを縛る:

1. `appsecret_proof`（HMAC-SHA256・既知の値）が全部の呼び出しに付く・app secret が無ければ付かない
2. `X-Business-Use-Case-Usage` などのヘッダを `state/<口座>/meta_usage.json` に残す
3. 80001 などの量の制限は `rate_limited` に振り分け、再試行せず、待ちの間は網に出ない・timer は出さない
4. 「待たない」印（`urgent`・広告を止める操作）は待ちを見ず、80004 ですぐ失敗として返る
5. `thth quota <口座>` が facebook で使用量を返す
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import hmac
import http.server
import json
import stat
import threading
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from thth import accounts, core, jst, leave_gate
from thth.adapters import base
from thth.adapters import facebook as fb
from thth.meta import errors as meta_errors
from thth.meta import graph as meta_graph
from thth.meta import media as meta_media
from thth.meta import tokens as meta_tokens
from thth.meta import usage as meta_usage
from tests.test_facebook_adapter import photo_post, photo_row
from tests.test_facebook_auth import PAGE, V, env, run, write_token  # noqa: F401 — env は fixture

TOKEN = "fb-page-token-stage2-0123456789"
SECRET = "app-secret-stage2-abcdef"
POST = PAGE + "_1001"
BUC = {PAGE: [{"type": "pages", "call_count": 12, "total_cputime": 3, "total_time": 4,
               "estimated_time_to_regain_access": 0}]}


# ------------------------------------------------------------------ 偽の Graph API（ヘッダつき）

class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict
    calls: list

    def log_message(self, *a):
        pass

    def _answer(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        raw = self.rfile.read(int(self.headers["Content-Length"])) if self.headers.get("Content-Length") else b""
        form = {}
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            form = urllib.parse.parse_qs(raw.decode(), keep_blank_values=True)
        self.calls.append({"method": method, "path": parsed.path, "query": query, "form": form, "raw": raw})
        route = self.routes.get((method, parsed.path))
        answer = route(query, form) if callable(route) else route or (404, {"error": {"message": "nope", "code": 803}})
        status, body = answer[0], answer[1]
        headers = answer[2] if len(answer) > 2 else {}
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in headers.items():
            self.send_header(name, value if isinstance(value, str) else json.dumps(value))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._answer("GET")

    def do_POST(self):
        self._answer("POST")

    def do_DELETE(self):
        self._answer("DELETE")


@contextlib.contextmanager
def fake_graph(routes):
    calls = []
    handler = type("H", (_Handler,), {"routes": routes, "calls": calls})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(autouse=True)
def _isolated_root(thth_root):
    """state（`meta_usage.json`）を試験ごとの置き場に書く（env の fixture はこの上に置き直す）。"""
    return thth_root


def proof_of(token, secret):
    """試験の側の計算（実装を呼ばない）。"""
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def make(url, *, secret=SECRET, account=None):
    a = fb.FacebookAdapter(base_url=url, access_token=TOKEN, page_id=PAGE, app_secret=secret)
    a.usage_account = account
    return a


# ================================================================== 1. appsecret_proof

def test_appsecret_proofはHMAC_SHA256で既知の値と一致する():
    # RFC 4231 の試験の組 2（key="Jefe"・data="what do ya want for nothing?"）。
    assert meta_graph.appsecret_proof("what do ya want for nothing?", "Jefe") == (
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843")
    assert meta_graph.appsecret_proof(TOKEN, SECRET) == proof_of(TOKEN, SECRET)


def test_appsecret_proofはGET_POST_DELETE_写真のすべてに付く():
    expected = proof_of(TOKEN, SECRET)
    routes = {("GET", f"{V}/me"): (200, {"id": PAGE, "name": "こぴちゃ"}),
              ("POST", f"{V}/{PAGE}/feed"): (200, {"id": POST}),
              ("DELETE", f"{V}/{POST}"): (200, {"success": True}),
              ("POST", f"{V}/{PAGE}/photos"): (200, {"id": "9", "post_id": POST})}
    with fake_graph(routes) as (url, calls):
        a = make(url)
        a.whoami()
        assert a.publish(base.Post(text="今日の一杯"), dry_run=False).failure == "none"
        a.delete_post(POST)
        post, _ = photo_post([photo_row()])
        assert a.publish(post, dry_run=False).failure == "none"
    get, feed, delete, photo = calls
    assert get["query"]["appsecret_proof"] == [expected]
    assert feed["form"]["appsecret_proof"] == [expected] and "appsecret_proof" not in feed["query"]
    assert delete["query"]["appsecret_proof"] == [expected]
    assert b'name="appsecret_proof"\r\n\r\n' + expected.encode() in photo["raw"]
    assert all(SECRET not in json.dumps(c, default=str) for c in calls)


def test_app_secretが無ければappsecret_proofを付けない():
    routes = {("GET", f"{V}/me"): (200, {"id": PAGE}), ("POST", f"{V}/{PAGE}/feed"): (200, {"id": POST})}
    with fake_graph(routes) as (url, calls):
        a = make(url, secret=None)
        a.whoami()
        a.publish(base.Post(text="x"), dry_run=False)
    assert all("appsecret_proof" not in c["query"] and "appsecret_proof" not in c["form"] for c in calls)


def test_from_accountはapp_envのMETA_APP_SECRETでproofを付けdoctorがそう言う(env):
    from thth import doctor
    env["routes"][("GET", V + "/debug_token")] = (200, {"data": {"scopes": list(fb.SCOPES), "expires_at": 0}})
    env["routes"][("GET", V + f"/{PAGE}/posts")] = (200, {"data": []})
    write_token(env)
    before = len(env["calls"])
    report = doctor.diagnose("alpha")
    calls = env["calls"][before:]
    assert calls and all(c["query"]["appsecret_proof"] == [proof_of(env["page_token"], env["app_secret"])]
                         for c in calls)
    row = next(p for p in report["probes"] if p["key"] == fb.FacebookAdapter.APPSECRET_PROOF_KEY)
    assert row["ok"] is True
    text = json.dumps(report, ensure_ascii=False)
    assert env["app_secret"] not in text and env["page_token"] not in text


def test_app_envが無ければproofを付けずdoctorは確かめられないと言う(env, monkeypatch):
    from thth import doctor
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(Path(env["app"]).with_name("absent.env")))
    env["routes"][("GET", V + "/debug_token")] = (200, {"data": {"scopes": list(fb.SCOPES)}})
    env["routes"][("GET", V + f"/{PAGE}/posts")] = (200, {"data": []})
    write_token(env)
    before = len(env["calls"])
    report = doctor.diagnose("alpha")
    assert all("appsecret_proof" not in c["query"] for c in env["calls"][before:])
    row = next(p for p in report["probes"] if p["key"] == fb.FacebookAdapter.APPSECRET_PROOF_KEY)
    assert row["ok"] is None and "META_APP_SECRET" in row["detail"]


def test_認可は鍵を持って呼ぶ3つにproofを付け鍵の交換には付けない(env):
    assert run(env) == 0
    by_path = {}
    for call in env["calls"]:
        by_path.setdefault(call["path"], []).append(call["query"])
    secret = env["app_secret"]
    assert all(q["appsecret_proof"] == [proof_of(env["long"], secret)] for q in by_path[V + "/me/accounts"])
    assert by_path[V + "/me"][0]["appsecret_proof"] == [proof_of(env["page_token"], secret)]
    app_token = env["app_id"] + "|" + secret
    assert by_path[V + "/debug_token"][0]["appsecret_proof"] == [proof_of(app_token, secret)]
    assert all("appsecret_proof" not in q for q in by_path[V + "/oauth/access_token"])


# ================================================================== 2. 使用量のヘッダ

def test_使用量のヘッダを口座のstateに0600で残す():
    headers = {"X-App-Usage": {"call_count": 5, "total_cputime": 1, "total_time": 2},
               "X-Business-Use-Case-Usage": BUC,
               "x-fb-ads-insights-throttle": {"app_id_util_pct": 1.5, "acc_id_util_pct": 0}}
    with fake_graph({("GET", f"{V}/me"): (200, {"id": PAGE}, headers)}) as (url, _):
        make(url, account="alpha").whoami()
    path = Path(meta_usage.path_for("alpha"))
    row = json.loads(path.read_text())
    assert row["headers"]["app_usage"] == {"call_count": 5, "total_cputime": 1, "total_time": 2}
    assert row["headers"]["business_use_case_usage"] == BUC
    assert row["headers"]["ads_insights_throttle"]["app_id_util_pct"] == 1.5
    assert "wait" not in row
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    assert TOKEN not in path.read_text() and SECRET not in path.read_text()


def test_口座が分からなければ残さない_ヘッダが無ければ書かない():
    with fake_graph({("GET", f"{V}/me"): (200, {"id": PAGE}, {"X-Business-Use-Case-Usage": BUC})}) as (url, _):
        make(url, account=None).whoami()
    assert not Path(meta_usage.path_for("alpha")).exists()
    with fake_graph({("GET", f"{V}/me"): (200, {"id": PAGE})}) as (url, _):
        make(url, account="alpha").whoami()
    assert not Path(meta_usage.path_for("alpha")).exists()


def test_壊れたヘッダは読まない():
    assert meta_usage.parse({"x-app-usage": "not json", "x-business-use-case-usage": "x" * 20000}) == {}
    assert meta_usage.parse(None) == {}


# ================================================================== 3. 量の制限

def _limited(code, minutes=19):
    buc = {PAGE: [{"type": "pages", "call_count": 100, "estimated_time_to_regain_access": minutes}]}
    return (400, {"error": {"message": f"(#{code}) There have been too many calls", "code": code}},
            {"X-Business-Use-Case-Usage": buc})


@pytest.mark.parametrize("code", sorted(meta_errors.RATE_LIMIT_CODES))
def test_量の制限のcodeはrate_limitedに振り分ける(code):
    assert meta_errors.classify(400, {"code": code}) == "rate_limited"
    # 文面に permission が混ざっても量の制限として待つ（権限より先に見る）。
    assert meta_errors.classify(403, {"code": code, "message": "permission"}) == "rate_limited"


def test_振り分けの残りの3つ():
    assert meta_errors.classify(403, {"code": 10}) == "permission"
    assert meta_errors.classify(400, {"code": 190}) == "permission"
    assert meta_errors.classify(400, {"code": 100}) == "definite"
    assert meta_errors.classify(500, {"code": 2}) == "ambiguous"
    assert meta_errors.classify(None, {}) == "ambiguous"


def test_80001はrate_limitedで再試行せず_待ちの間は網に出ない(frozen_now_jst):
    with fake_graph({("GET", f"{V}/me"): _limited(80001)}) as (url, calls):
        a = make(url, account="alpha")
        with pytest.raises(meta_errors.RateLimited) as e:
            a.whoami()
        assert len(calls) == 1                                   # 再試行しない
        assert e.value.failure == "rate_limited" and e.value.code == 80001 and e.value.wait_minutes == 19
        assert isinstance(e.value, base.AdapterError)
        wait = meta_usage.waiting("alpha")
        assert wait["code"] == 80001 and wait["minutes"] == 19
        until = datetime.datetime.fromisoformat(wait["until"])
        assert until - jst.now_jst() == datetime.timedelta(minutes=19)
        with pytest.raises(meta_errors.RateLimited) as again:
            a.whoami()
        assert len(calls) == 1 and again.value.sent is False       # 待ちの間は投げない
    # 待ちが明けたら投げる（戻るまでの分の後）。
    frozen_now_jst(jst.now_jst() + datetime.timedelta(minutes=20))
    assert meta_usage.waiting("alpha") is None
    with fake_graph({("GET", f"{V}/me"): (200, {"id": PAGE})}) as (url, calls):
        make(url, account="alpha").whoami()
    assert len(calls) == 1


def test_戻るまでの分が無ければTHTHの線だけ待つ():
    with fake_graph({("GET", f"{V}/me"): (400, {"error": {"message": "(#4) limit", "code": 4}})}) as (url, _):
        with pytest.raises(meta_errors.RateLimited) as e:
            make(url, account="alpha").whoami()
    assert e.value.wait_minutes == meta_usage.DEFAULT_WAIT_MINUTES
    assert meta_usage.waiting("alpha")["minutes"] == meta_usage.DEFAULT_WAIT_MINUTES


def test_投稿が量の制限で断られたら出ていないとして返る():
    with fake_graph({("POST", f"{V}/{PAGE}/feed"): _limited(80001)}) as (url, calls):
        a = make(url, account="alpha")
        result = a.publish(base.Post(text="x"), dry_run=False)
        assert result.failure == "publish_definite" and "meta_rate_limited" in result.error
        again = a.publish(base.Post(text="x"), dry_run=False)
    assert again.failure == "publish_definite" and len(calls) == 1
    assert TOKEN not in result.error


def test_写真の公開が量の制限で断られても出ていない():
    with fake_graph({("POST", f"{V}/{PAGE}/photos"): _limited(80001)}) as (url, calls):
        post, phases = photo_post([photo_row()])
        result = make(url, account="alpha").publish(post, dry_run=False)
    assert result.failure == "publish_definite" and len(calls) == 1
    assert phases[-1][0] == "failed"


def test_ページの日次は量の制限を指標が無いと取り違えない():
    with fake_graph({("GET", f"{V}/{PAGE}/insights"): _limited(80001)}) as (url, calls):
        with pytest.raises(meta_errors.RateLimited):
            make(url, account="alpha").account_insights(PAGE, since="1", until="2")
    assert len(calls) == 1                                         # 1 つずつの訊き直しをしない


def test_timerは量の制限の待ちの間は出さない(isolated_account_factory):
    isolated_account_factory("fbacct", media="facebook", page_id=PAGE, handle=PAGE, production=True)
    meta_usage.note_rate_limited("fbacct", {"x-business-use-case-usage": json.dumps(
        {PAGE: [{"estimated_time_to_regain_access": 7}]})}, 80001)

    def no_adapter(*a, **k):
        pytest.fail("待ちの間に adapter を作った")
    lines = []
    result = core.throw_once("fbacct", production_flag=True, adapter_factory=no_adapter, log=lines.append)
    assert result.action == "rate_limited" and result.exit_code == 0
    assert any("Meta の量の制限で待っています" in line and "7 分" in line for line in lines)


def test_timerの待ちはMetaの層に乗る媒体だけ(isolated_account_factory):
    isolated_account_factory("thacct")
    meta_usage.note_rate_limited("thacct", None, 80001)
    assert core._meta_rate_limit_wait("thacct", accounts.load_account("thacct")) is None


# ================================================================== 4. 待たない印（urgent）

def test_待たない印の呼び出しは待ちを見ず80004ですぐ失敗として返る():
    meta_usage.note_rate_limited("alpha", None, 80001)              # 待ちの間
    with fake_graph({("POST", f"{V}/act_1/ads"): _limited(80004, minutes=55)}) as (url, calls):
        a = make(url, account="alpha")
        started = time.monotonic()
        with pytest.raises(meta_errors.RateLimited) as e:
            a._request("POST", "/act_1/ads", form={"status": "PAUSED"}, urgent=True)
        elapsed = time.monotonic() - started
    assert len(calls) == 1                                          # 待ちの間でも投げた
    assert e.value.urgent is True and e.value.code == 80004 and e.value.sent is True
    assert "待たずに" in str(e.value) and elapsed < 5
    # 待たない印の無い呼び出しは、待ちの間は網に出ない。
    with fake_graph({("POST", f"{V}/act_1/ads"): (200, {"success": True})}) as (url, calls):
        with pytest.raises(meta_errors.RateLimited):
            make(url, account="alpha")._request("POST", "/act_1/ads", form={"status": "PAUSED"})
    assert calls == []


# ================================================================== 5. thth quota

def _quota_args(account, json_out=True):
    return SimpleNamespace(account=account, json=json_out)


def test_thth_quotaはfacebookで使用量を返す(env, capsys, monkeypatch):
    from thth import quota_cli
    write_token(env)
    headers = {"X-Business-Use-Case-Usage": BUC, "X-App-Usage": {"call_count": 3}}
    with fake_graph({("GET", f"{V}/{PAGE}"): (200, {"id": PAGE}, headers)}) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        assert quota_cli.cmd_quota(_quota_args("alpha")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["medium"] == "facebook" and out["fresh"] is True
    assert out["business_use_case_usage"] == BUC and out["app_usage"] == {"call_count": 3}
    assert out["waiting"] is None and out["appsecret_proof"] is True
    assert [(c["method"], c["path"]) for c in calls] == [("GET", f"{V}/{PAGE}")]   # 読むだけ
    assert env["page_token"] not in json.dumps(out) and env["app_secret"] not in json.dumps(out)


def test_thth_quotaは待ちの間は投げずに残っている値と待ちを返す(env, capsys, monkeypatch):
    from thth import quota_cli
    write_token(env)
    meta_usage.note_rate_limited("alpha", {"x-business-use-case-usage": json.dumps(
        {PAGE: [{"estimated_time_to_regain_access": 9}]})}, 80001)
    with fake_graph({}) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        assert quota_cli.cmd_quota(_quota_args("alpha", json_out=False)) == 0
    text = capsys.readouterr().out
    assert calls == [] and "待ち" in text and "9 分" in text


def test_thth_quotaは使用量を返さない媒体を断る(isolated_account_factory, capsys, monkeypatch):
    from thth import quota_cli
    isolated_account_factory("thacct")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "t", "user_id": "1"})
    assert quota_cli.cmd_quota(_quota_args("thacct")) == 2
    assert "使用量を返しません" in json.loads(capsys.readouterr().out)["error"]


def test_thth_quotaはhelpに出る():
    from thth import cli as cli_mod, quota_cli
    assert cli_mod.build_parser().parse_args(["quota", "alpha"]).func is quota_cli.cmd_quota


def test_quotaはleave_gateに包まれ止めた口座では1本も出ない(env):
    write_token(env)
    assert "'quota'" in __import__("inspect").getsource(leave_gate.bind)
    cfg = accounts.load_account("alpha")
    made = fb.FacebookAdapter.from_account(cfg, accounts.load_token(cfg))
    before = len(env["calls"])
    from tests.test_v212_leave_gate import stop
    stop("alpha")
    with pytest.raises(accounts.AccountStopped):
        made.quota()
    assert len(env["calls"]) == before


# ================================================================== 鍵と公開 URL の置き場

def test_データの期限の見立ては共通層にあり_adapterは同じ答えを返す():
    now = jst.now_jst()
    soon = int(now.timestamp() + 5 * 86400)
    ok, detail = meta_tokens.data_access_verdict(soon, hint="thth auth a --by <名前>", now=now)
    assert ok is True and "14 日を切りました" in detail
    a = fb.FacebookAdapter(base_url="http://127.0.0.1:9", access_token="x", page_id=PAGE)
    a.auth_account = "a"
    assert a._data_access_verdict(soon, now=now) == (ok, detail)
    assert meta_tokens.data_access_verdict(0, hint="h")[0] is None


def test_再認可の引数はauth_type_reauthorizeを足す():
    params = {"client_id": "1"}
    assert meta_tokens.dialog_params(params, reauthorize=True) == {"client_id": "1", "auth_type": "reauthorize"}
    assert meta_tokens.dialog_params(params) == params and params == {"client_id": "1"}


def test_relayを貸すのはregistryで取りに来る媒体だけ():
    assert meta_media.pulls("threads") and not meta_media.pulls("facebook") and not meta_media.pulls("nope")
    assert meta_media.relay_for({"media": "facebook", "account": "a"}, has_items=True) is None
    assert meta_media.relay_for({"media": "threads", "account": "a"}, has_items=False) is None
    relay = meta_media.relay_for({"media": "threads", "account": "a"}, has_items=True)
    assert type(relay).__name__ == "MediaRelay" and relay.account == "a"


def test_共通層はthreadsの宛先を持たない():
    root = Path(meta_graph.__file__).resolve().parent
    for path in root.glob("*.py"):
        assert "graph.threads.net" not in path.read_text(encoding="utf-8").replace(
            "（`graph.threads.net`）は扱わない", ""), path.name


def test_facebookの別名は共通層と同じもの(monkeypatch):
    assert fb.GRAPH_VERSION == meta_graph.GRAPH_VERSION and fb.BASE_URL_ENV == meta_graph.BASE_URL_ENV
    assert fb.graph_error is meta_errors.graph_error and fb.is_permission_error is meta_errors.is_permission_error
    assert fb.DATA_ACCESS_WARN_DAYS == meta_tokens.DATA_ACCESS_WARN_DAYS
    monkeypatch.delenv(fb.BASE_URL_ENV, raising=False)
    assert fb.graph_origin() == meta_graph.origin() == "https://graph.facebook.com"
