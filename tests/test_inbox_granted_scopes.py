"""**権限が無いと分かっているトークンでは inbox を取りに行かない**（本番 P1・2026-09-14）。

何が起きていたか。v2.1.0 で `ThreadsAdapter.CAPABILITIES` に `inbox` が入り、
`collect` が毎 run `GET /{user_id}/mentions` を叩くようになった。**5 権限の
トークン**（`thth token set` の管理画面発行・`.token` の `scopes` は null）では
Meta が **HTTP 500** を返し（4xx ではない）、`_read()` は 500 を権限不足と
読まないので `AdapterError` → `collect` の `errors` に `inbox: 言及の取得:
HTTP 500` が毎 run 積まれ、`thth run` が 10 分ごとに「採取は完全ではありません」
を出していた。

ここで固定するもの:
  (a) `.token` の `scopes` に `threads_manage_mentions` が無い → `collect` は
      言及の口を **1 回も叩かず**・`errors` 0・`inbox_state: permission_missing`
  (b) `scopes` null ＋ `/debug_token` が 5 権限 → 同じく叩かず（**debug_token は
      1 回だけ**・同じ実体で 2 度採っても 1 回）
  (c) `scopes` null ＋ `/debug_token` も 500 → **不明は不明**。従来どおり叩き、
      500 なら `errors` 1 行（500 を無条件に権限不足にしない）
  (d) 11 権限なら従来どおり取れる（`/debug_token` は引かない）
  (e) `thth mentions` が scopes null ＋ debug_token 5 権限で rc=2 と `thth auth` の案内
  (f) `thth run` の exit code は投稿のもののまま（collect の失敗で変わらない）
  (g) `thth board` の account 行に `inbox=権限なし` の 1 語・`--json` に `inbox_state`

本物の Threads API には一切触れない（`http.server` の偽 API にだけ向ける）。
"""
from __future__ import annotations

import contextlib
import datetime
import http.server
import json
import os
import threading
import urllib.error
import urllib.parse

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_thth
from thth import collect as collect_mod
from thth import jst
from thth import scopes as scopes_mod
from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod

NOW = datetime.datetime(2026, 9, 14, 17, 3, tzinfo=jst.JST)

# 本番の 5 権限（asmon-kanto・kopicha・nigamilab の `.token` に乗っていたもの）。
FIVE_SCOPES = ["threads_basic", "threads_content_publish", "threads_manage_insights",
               "threads_manage_replies", "threads_read_replies"]
ELEVEN_SCOPES = list(scopes_mod.DEFAULT_SCOPES)
MENTIONS = "threads_manage_mentions"

MENTION_ROWS = [
    {"id": "M1", "text": "@nigamilab こんにちは", "username": "carol",
     "timestamp": "2026-09-10T09:00:00+0000", "permalink": "https://t/m1",
     "media_type": "TEXT", "is_reply": False, "has_replies": False},
]


class _Fake(http.server.BaseHTTPRequestHandler):
    """読み取り専用の偽 Threads API。

    `debug_token` は `list`（その一覧を返す）か `"500"`。`mentions` は `"ok"` か
    `"500"`（本番の Meta は 5 権限のトークンに 4xx ではなく **500** を返す）。
    GET 以外は 405 で記録する（テストの終わりで 1 つでもあれば fail）。
    """
    debug_token = "500"
    mentions = "500"
    requests: list = []

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.requests.append(("GET", parsed.path, params))
        p = parsed.path
        if p == "/v1.0/debug_token":
            if isinstance(self.debug_token, list):
                return self._json(200, {"data": {"scopes": list(self.debug_token)}})
            return self._json(500, {"error": {"message": "Internal error", "code": 2}})
        if p.endswith("/mentions"):
            if self.mentions == "ok":
                return self._json(200, {"data": MENTION_ROWS})
            return self._json(500, {"error": {"message": "Internal error", "code": 2}})
        if p == "/v1.0/me":
            return self._json(200, {"id": "999999", "username": "nigamilab"})
        if p.endswith("/insights"):
            return self._json(200, {"data": [{"name": "views", "values": [{"value": 7}]}]})
        if p.endswith("/conversation"):
            return self._json(200, {"data": []})
        return self._json(200, {"data": []})

    def _refuse(self):
        parsed = urllib.parse.urlparse(self.path)
        self.requests.append((self.command, parsed.path, {}))
        self._json(405, {"error": {"message": "read-only fake"}})

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _server(*, debug_token="500", mentions="500"):
    requests: list = []
    handler = type("H", (_Fake,), {"debug_token": debug_token, "mentions": mentions,
                                    "requests": requests})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert all(m == "GET" for m, _p, _q in requests), \
            f"読み取りの口が GET 以外を叩いた: {[r for r in requests if r[0] != 'GET']}"


def _hits(requests, suffix):
    return [r for r in requests if r[1].endswith(suffix)]


def _write_token(path, scopes):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "FAKE-SECRET", "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999", "username": "nigamilab",
                   "scopes": scopes}, f)


def _read_token(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _adapter_from_token(base_url, token_path):
    """本番と同じ経路（`from_account`）で `.token` の `scopes` をアダプタに渡す。"""
    os.environ["THTH_THREADS_BASE_URL"] = base_url
    try:
        adapter = threads_mod.ThreadsAdapter.from_account({}, _read_token(token_path))
    finally:
        os.environ.pop("THTH_THREADS_BASE_URL", None)
    adapter.timeout = 2.0
    return adapter


def _posted_account(tmp_path, factory, token_path):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1", "posted_at": "2026-09-14T10:00:00+09:00"}))
    acc = factory(repo_dir=pair["work"], production=True, token=token_path)
    return pair, acc


def _inbox_rows(repo_dir, month="2026-09"):
    path = os.path.join(repo_dir, "data", "sns", "inbox", f"{month}.ndjson")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _collect(acc, adapter):
    return collect_mod.collect_once(acc["name"], adapter=adapter, now=NOW,
                                    log=lambda _l: None)


def _assert_posts_collected(pair, result):
    """**投稿の採取は止まっていない**（inbox の状態が投稿の採取を巻き込まない）。"""
    insight = os.path.join(pair["work"], "data", "sns", "insights", "posts", "POST1.ndjson")
    assert os.path.exists(insight), "inbox が投稿の採取を止めている"
    assert result["posts"] == 1


# ======================================================== (a) .token の scopes に無い

def test_a_scopesに無ければcollectは言及の口を叩かずerrors0(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, FIVE_SCOPES)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    with _server(debug_token="500", mentions="500") as (base_url, requests):
        adapter = _adapter_from_token(base_url, token_path)
        result = _collect(acc, adapter)

    assert result["errors"] == [], result["errors"]
    assert result["inbox"]["state"] == "permission_missing"
    assert result["inbox"]["permission"] == MENTIONS
    assert result["inbox"]["skipped"] == "permission_missing"
    # **HTTP を 1 回も叩いていない**（言及も debug_token も）。
    assert _hits(requests, "/mentions") == [], requests
    assert _hits(requests, "/debug_token") == [], requests
    assert _inbox_rows(pair["work"]) == []
    _assert_posts_collected(pair, result)
    # board が読む記録。
    state = collect_mod.read_inbox_state(acc["name"])
    assert state["state"] == "permission_missing" and state["permission"] == MENTIONS
    assert state["at"] == jst.iso(NOW)
    # `.token` には書いていない（読むだけ）。
    assert _read_token(token_path)["scopes"] == FIVE_SCOPES


def test_a_アダプタ単体でもscopesに無ければ叩かずPermissionMissing():
    with _server() as (base_url, requests):
        a = threads_mod.ThreadsAdapter(base_url=base_url, access_token="FAKE-SECRET",
                                       user_id="999999", timeout=2.0, scopes=FIVE_SCOPES)
        for call in (a.mentions, a.inbox, lambda: a.keyword_search("お茶"),
                     lambda: a.profile_lookup("threads"),
                     lambda: a.location_search("Menlo Park")):
            with pytest.raises(adapter_base.PermissionMissing) as e:
                call()
            assert "叩いていません" in e.value.detail
            assert "FAKE-SECRET" not in str(e.value)
    assert requests == [], "無いと分かっている権限の口を叩いた"
    assert a.granted_scopes() == FIVE_SCOPES


# ======================================================== (b) scopes null → /debug_token

def test_b_scopes_nullはdebug_tokenを1回だけ引き5権限なら叩かない(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, None)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    with _server(debug_token=FIVE_SCOPES, mentions="500") as (base_url, requests):
        adapter = _adapter_from_token(base_url, token_path)
        r1 = _collect(acc, adapter)
        r2 = _collect(acc, adapter)

    assert r1["errors"] == [] and r2["errors"] == [], (r1["errors"], r2["errors"])
    assert r1["inbox"]["state"] == r2["inbox"]["state"] == "permission_missing"
    assert _hits(requests, "/mentions") == [], requests
    # **debug_token は 1 回だけ**（同じ実体で 2 度採っても）。
    assert len(_hits(requests, "/debug_token")) == 1, _hits(requests, "/debug_token")
    (_m, _p, params), = _hits(requests, "/debug_token")
    assert params.get("input_token") == "FAKE-SECRET"
    _assert_posts_collected(pair, r1)
    # `.token` の scopes は null のまま（書かない）。
    assert _read_token(token_path)["scopes"] is None
    assert adapter.granted_scopes() == FIVE_SCOPES


# ======================================================== (c) 不明は不明

def test_c_debug_tokenも500なら従来どおり叩いて500はerrors1行(tmp_path, isolated_account_factory):
    """**500 を無条件に権限不足にしない**——一覧が取れなければ本物の失敗として残す。"""
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, None)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    with _server(debug_token="500", mentions="500") as (base_url, requests):
        adapter = _adapter_from_token(base_url, token_path)
        result = _collect(acc, adapter)

    inbox_errors = [e for e in result["errors"] if e.startswith("inbox")]
    assert len(inbox_errors) == 1, result["errors"]
    assert "HTTP 500" in inbox_errors[0]
    assert "乗っていません" not in inbox_errors[0], "不明を「無い」にしている"
    assert "FAKE-SECRET" not in inbox_errors[0]
    assert result["inbox"]["state"] == "failed"
    assert len(_hits(requests, "/mentions")) == 1
    assert len(_hits(requests, "/debug_token")) == 1
    assert adapter.granted_scopes() is None
    _assert_posts_collected(pair, result)
    assert collect_mod.read_inbox_state(acc["name"])["state"] == "failed"


def test_c_アダプタ単体でも不明なら500はAdapterError():
    with _server(debug_token="500", mentions="500") as (base_url, requests):
        a = threads_mod.ThreadsAdapter(base_url=base_url, access_token="FAKE-SECRET",
                                       user_id="999999", timeout=2.0, scopes=None)
        with pytest.raises(adapter_base.AdapterError) as e:
            a.mentions()
        assert not isinstance(e.value, adapter_base.PermissionMissing)
        assert "500" in str(e.value)
        # 2 度目も debug_token は引き直さない（不明を覚えている）。
        with pytest.raises(adapter_base.AdapterError):
            a.mentions()
    assert len(_hits(requests, "/debug_token")) == 1
    assert len(_hits(requests, "/mentions")) == 2


def test_500かつ無いと分かっているときだけPermissionMissing():
    """`_read()` の 500 の読み分け（前置きの判定を通らない呼び手のため）。"""
    def boom():
        raise urllib.error.HTTPError("http://x/v1.0/999999/mentions", 500, "Internal",
                                     {}, None)
    known_missing = threads_mod.ThreadsAdapter(access_token="FAKE-SECRET", scopes=FIVE_SCOPES)
    # 前置きの判定を外して 500 だけを試す。
    known_missing._require_scope = lambda permission: None
    with pytest.raises(adapter_base.PermissionMissing) as e:
        known_missing._read(boom, permission=MENTIONS, what="言及の取得")
    assert "500" in e.value.detail and "FAKE-SECRET" not in str(e.value)

    known_granted = threads_mod.ThreadsAdapter(access_token="FAKE-SECRET", scopes=ELEVEN_SCOPES)
    with pytest.raises(adapter_base.AdapterError) as e:
        known_granted._read(boom, permission=MENTIONS, what="言及の取得")
    assert not isinstance(e.value, adapter_base.PermissionMissing)


# ======================================================== (d) 11 権限

def test_d_11権限なら従来どおり取れdebug_tokenは引かない(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, ELEVEN_SCOPES)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    with _server(debug_token="500", mentions="ok") as (base_url, requests):
        adapter = _adapter_from_token(base_url, token_path)
        result = _collect(acc, adapter)

    assert result["errors"] == [], result["errors"]
    assert result["inbox"] == {"state": "ok", "count": 1}
    assert [r["message_id"] for r in _inbox_rows(pair["work"])] == ["M1"]
    assert len(_hits(requests, "/mentions")) == 1
    assert _hits(requests, "/debug_token") == []
    assert collect_mod.read_inbox_state(acc["name"])["state"] == "ok"


def test_d_scopes_nullでdebug_tokenが11権限なら取れる(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, None)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)
    with _server(debug_token=ELEVEN_SCOPES, mentions="ok") as (base_url, requests):
        result = _collect(acc, _adapter_from_token(base_url, token_path))
    assert result["errors"] == [] and result["inbox"]["state"] == "ok"
    assert [r["message_id"] for r in _inbox_rows(pair["work"])] == ["M1"]
    assert len(_hits(requests, "/debug_token")) == 1


# ======================================================== (e) thth mentions

@pytest.fixture
def account_null_scopes(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path, None)
    acc["token_path"] = token_path
    return acc


def _cli(args, base_url):
    return run_thth(args, env={"THTH_THREADS_BASE_URL": base_url})


def test_e_mentionsコマンドはscopes_null_debug_token5権限でrc2と案内(account_null_scopes):
    acc = account_null_scopes
    with _server(debug_token=FIVE_SCOPES, mentions="500") as (base_url, requests):
        r = _cli(["mentions", acc["name"]], base_url)
    assert r.returncode == 2, r.stdout + r.stderr
    assert f"{MENTIONS} がトークンに乗っていません" in r.stderr
    assert f"thth auth {acc['name']}" in r.stderr
    assert "HTTP 500" not in r.stderr
    assert "FAKE-SECRET" not in r.stdout + r.stderr
    assert _hits(requests, "/mentions") == [], "無いと分かっている口を叩いた"
    assert len(_hits(requests, "/debug_token")) == 1


def test_e_mentionsコマンドはscopes_null_debug_token500ならrc1でHTTP500(account_null_scopes):
    """不明なら従来どおり叩く——rc=1 の本物の失敗（rc=2 の案内にしない）。"""
    acc = account_null_scopes
    with _server(debug_token="500", mentions="500") as (base_url, requests):
        r = _cli(["mentions", acc["name"]], base_url)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "HTTP 500" in r.stderr
    assert "乗っていません" not in r.stderr
    assert len(_hits(requests, "/mentions")) == 1


# ======================================================== (f) thth run の exit code

def _run_account(tmp_path, factory, token_path):
    """`thth run` 用（approved の queue は無い → 投稿は「出すものが無い」exit 0）。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1", "posted_at": "2026-09-14T10:00:00+09:00"}))
    return pair, factory(repo_dir=pair["work"], production=True, token=token_path)


def test_f_runのexitは権限なしのinboxで変わらず完全ではありませんも出ない(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, None)
    pair, acc = _run_account(tmp_path, isolated_account_factory, token_path)
    with _server(debug_token=FIVE_SCOPES, mentions="500") as (base_url, requests):
        r = _cli(["run", acc["name"]], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "採取は完全ではありません" not in r.stdout + r.stderr
    assert "採取に失敗しました" not in r.stdout + r.stderr
    assert "inbox: 権限なし" in r.stdout
    assert _hits(requests, "/mentions") == []
    assert len(_hits(requests, "/debug_token")) == 1
    assert "FAKE-SECRET" not in r.stdout + r.stderr


def test_f_runのexitは本物のcollect失敗でも投稿のもののまま(tmp_path, isolated_account_factory):
    """collect が exit=1 でも `thth run` の終了コードは投稿のもの（0）——変えない。"""
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, None)
    pair, acc = _run_account(tmp_path, isolated_account_factory, token_path)
    with _server(debug_token="500", mentions="500") as (base_url, requests):
        r = _cli(["run", acc["name"]], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "採取は完全ではありません: exit=1" in r.stdout
    assert "HTTP 500" in r.stdout
    assert len(_hits(requests, "/mentions")) == 1


# ======================================================== (g) thth board

def test_g_boardは権限なしを1語で出しjsonにinbox_state(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, FIVE_SCOPES)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)
    with _server() as (base_url, requests):
        _collect(acc, _adapter_from_token(base_url, token_path))

    r = run_thth(["board"])
    assert r.returncode == 0, r.stdout + r.stderr
    line, = [l for l in r.stdout.splitlines() if l.startswith(f"{acc['name']}: ")]
    assert " inbox=権限なし" in line, line
    assert line.count("inbox=") == 1

    j = run_thth(["board", "--json"])
    assert j.returncode == 0, j.stdout + j.stderr
    row, = [x for x in json.loads(j.stdout)["accounts"] if x["account"] == acc["name"]]
    assert row["inbox_state"] == "permission_missing"
    assert row["inbox_permission"] == MENTIONS


def test_g_boardは採れていれば語を出さず記録が無ければnull(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    _write_token(token_path, ELEVEN_SCOPES)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    # まだ 1 度も採っていない → 判らない（null）・語は出ない。
    j0 = run_thth(["board", "--json"])
    row, = [x for x in json.loads(j0.stdout)["accounts"] if x["account"] == acc["name"]]
    assert row["inbox_state"] is None and row["inbox_permission"] is None

    with _server(mentions="ok") as (base_url, requests):
        _collect(acc, _adapter_from_token(base_url, token_path))
    r = run_thth(["board"])
    line, = [l for l in r.stdout.splitlines() if l.startswith(f"{acc['name']}: ")]
    assert "inbox=" not in line, line
    j = run_thth(["board", "--json"])
    row, = [x for x in json.loads(j.stdout)["accounts"] if x["account"] == acc["name"]]
    assert row["inbox_state"] == "ok"


def test_read_inbox_stateは壊れた記録を判らないにする(thth_root, isolated_account):
    path = collect_mod.inbox_state_path(isolated_account["name"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert collect_mod.read_inbox_state(isolated_account["name"]) is None
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"at": "x"}, f)
    assert collect_mod.read_inbox_state(isolated_account["name"]) is None
