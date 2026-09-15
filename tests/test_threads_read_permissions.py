"""Threads の 11 権限を使う**読み取りの口 3 つ**（設計 v2 §4.3・v2.1-A・2026-09-14）。

  - `ThreadsAdapter.keyword_search()` / `mentions()` / `profile_lookup()`（全部 GET）
  - `thth topics <account> --search <語>`・`thth mentions`・`thth profile`
  - `collect` の `inbox` 経路に Threads の言及が落ちる（`message_id` で冪等）

ここで固定するもの:
  - 各口が偽サーバで通る（受け入れ (a)）
  - **権限不足は `PermissionMissing`**（doctor と同じ判定・黙って 0 件にしない）
  - CLI は権限不足を **rc=2** で「`thth auth <account>` をやり直してください」と断る（受け入れ (c)）
  - `--search` は本文を**どこにも書かない**（`THTH_ROOT`・利用者 repo・state を走査して 1 バイトも無い）
  - `collect` の inbox に言及が落ち、2 度走らせても増えない
  - 権限が無い collect は `errors` に積まず `inbox_state: permission_missing` を残して
    **続行する**（投稿の採取は止まらない・本番 P1 2026-09-14 で「errors に 1 行」から変更）
  - **偽サーバが GET 以外を受けたら即 fail**

本物の Threads API には一切触れない（`http.server` の偽 API にだけ向ける）。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import threading
import urllib.parse

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_thth
from thth import collect as collect_mod
from thth import scopes as scopes_mod
from thth import threads_read_cli
from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod

PERMISSION_ERROR = {"error": {"message": "(#10) Application does not have permission "
                                         "for this action",
                              "type": "OAuthException", "code": 10}}

# 偽サーバが返す検索結果。**本文はここにしか無い**——テストはこの綴りが
# ディスクに 1 バイトも落ちていないことを走査で確かめる。
SEARCH_TEXT_A = "偽サーバの検索本文その一・ZQXJ7A"
SEARCH_TEXT_B = "偽サーバの検索本文その二・ZQXJ7B"
SEARCH_ROWS = [
    {"id": "S1", "text": SEARCH_TEXT_A, "username": "alice",
     "timestamp": "2026-09-13T10:00:00+0000", "permalink": "https://t/s1",
     "media_type": "TEXT", "is_reply": False, "has_replies": True, "topic_tag": "お茶"},
    {"id": "S2", "text": SEARCH_TEXT_B, "username": "alice",
     "timestamp": "2026-09-14T01:00:00+0000", "permalink": "https://t/s2",
     "media_type": "TEXT", "is_reply": True, "has_replies": False, "topic_tag": None},
    {"id": "S3", "text": "三つ目", "username": "bob",
     "timestamp": "2026-09-12T00:00:00+0000", "permalink": "https://t/s3",
     "media_type": "TEXT", "is_reply": False, "has_replies": False, "topic_tag": "お茶"},
]
MENTION_ROWS = [
    {"id": "M1", "text": "@nigamilab こんにちは", "username": "carol",
     "timestamp": "2026-09-10T09:00:00+0000", "permalink": "https://t/m1",
     "media_type": "TEXT", "is_reply": False, "has_replies": False},
    {"id": "M2", "text": "@nigamilab 二つ目", "username": "dave",
     "timestamp": "2026-09-11T09:00:00+0000", "permalink": "https://t/m2",
     "media_type": "TEXT", "is_reply": True, "has_replies": False},
]
PROFILE = {"username": "threads", "name": "Threads", "biography": "Say more",
           "follower_count": 12345678, "is_verified": True,
           "profile_picture_url": "https://t/pic"}


class _ReadOnlyThreads(http.server.BaseHTTPRequestHandler):
    """読み取り専用の偽 Threads API。**GET 以外は全部記録して 405**（テストは
    `requests` を見て GET 以外が 1 つでもあれば fail する）。

    `behavior` は「path の末尾 → モード」。"ok" | "permission" | "5xx"。
    """
    behavior: dict = {}
    requests: list = []

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _mode(self, path):
        for suffix, mode in self.behavior.items():
            if path.endswith(suffix):
                return mode
        return "ok"

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.requests.append(("GET", parsed.path, params))
        mode = self._mode(parsed.path)
        if mode == "permission":
            return self._json(400, PERMISSION_ERROR)
        if mode == "5xx":
            return self._json(503, {"error": {"message": "temporarily unavailable",
                                              "code": 2}})
        p = parsed.path
        if p == "/v1.0/keyword_search":
            limit = int(params.get("limit", "25"))
            return self._json(200, {"data": SEARCH_ROWS[:limit]})
        if p.endswith("/mentions"):
            return self._json(200, {"data": MENTION_ROWS})
        if p == "/v1.0/profile_lookup":
            if params.get("username") != "threads":
                return self._json(400, {"error": {"message": "Unsupported request",
                                                  "code": 100}})
            return self._json(200, PROFILE)
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
def _server(behavior=None):
    requests: list = []
    handler = type("H", (_ReadOnlyThreads,), {"behavior": dict(behavior or {}),
                                               "requests": requests})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        # **GET 以外を 1 度でも受けていたら fail**（読み取りの口しか無い）。
        assert all(m == "GET" for m, _p, _q in requests), \
            f"読み取りの口が GET 以外を叩いた: {[r for r in requests if r[0] != 'GET']}"


def _adapter(base_url, **kw):
    return threads_mod.ThreadsAdapter(base_url=base_url, access_token="FAKE-SECRET",
                                      user_id="999999", timeout=2.0, **kw)


def _write_token(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "FAKE-SECRET", "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999", "username": "nigamilab",
                   "scopes": None}, f)


@pytest.fixture
def account(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)
    acc["token_path"] = token_path
    return acc


def _cli(args, base_url):
    return run_thth(args, env={"THTH_THREADS_BASE_URL": base_url})


# ======================================================== アダプタ: 成功

def test_keyword_searchはGETでMessageの形を返す():
    with _server() as (base_url, requests):
        rows = _adapter(base_url).keyword_search("お茶", search_type="RECENT", limit=3)
    assert [r["message_id"] for r in rows] == ["S1", "S2", "S3"]
    row = rows[0]
    assert row["medium"] == "threads"
    assert row["username"] == "alice"
    assert row["author_key"] == adapter_base.author_key("threads", "alice")
    assert row["text"] == SEARCH_TEXT_A
    assert row["timestamp"] == "2026-09-13T10:00:00+0000"
    assert row["topic_tag"] == "お茶" and row["is_reply"] is False
    assert row["reply_deadline"] is None
    # 叩いた口と引数（**L2** keyword-search）。
    (method, path, params), = [r for r in requests if r[1] == "/v1.0/keyword_search"]
    assert method == "GET"
    assert params["q"] == "お茶" and params["search_type"] == "RECENT"
    assert params["limit"] == "3"
    assert "text" in params["fields"] and "username" in params["fields"]


def test_keyword_searchは語とsearch_typeとlimitを検査する():
    with _server() as (base_url, requests):
        a = _adapter(base_url)
        with pytest.raises(adapter_base.AdapterError):
            a.keyword_search("   ")
        with pytest.raises(adapter_base.AdapterError):
            a.keyword_search("お茶", search_type="NEWEST")
        with pytest.raises(adapter_base.AdapterError):
            a.keyword_search("お茶", limit=101)
        assert requests == [], "検査で落ちる呼び出しが網に出ている"


def test_mentionsはGETで全頁をMessageの形で返す():
    with _server() as (base_url, requests):
        rows = _adapter(base_url).mentions()
    assert [r["message_id"] for r in rows] == ["M1", "M2"]
    assert rows[0]["medium"] == "threads"
    assert rows[0]["author_key"] == adapter_base.author_key("threads", "carol")
    assert rows[0]["reply_deadline"] is None
    (method, path, params), = [r for r in requests if r[1].endswith("/mentions")]
    assert method == "GET" and path == "/v1.0/999999/mentions"
    assert "since" not in params


def test_mentionsはsinceをそのまま渡す():
    with _server() as (base_url, requests):
        _adapter(base_url).mentions(since="1757800000")
    (_m, _p, params), = [r for r in requests if r[1].endswith("/mentions")]
    assert params["since"] == "1757800000"


def test_inboxは言及を返す():
    """`inbox()` は `mentions()` そのもの（設計 v2 §4.3「v2-3 の芽がそのまま受け皿」）。"""
    with _server() as (base_url, requests):
        rows = _adapter(base_url).inbox()
    assert [r["message_id"] for r in rows] == ["M1", "M2"]
    assert "inbox" in threads_mod.ThreadsAdapter.capabilities()


def test_profile_lookupはGETで資料のfieldを返す():
    with _server() as (base_url, requests):
        p = _adapter(base_url).profile_lookup("@threads")
    assert p["username"] == "threads" and p["name"] == "Threads"
    assert p["biography"] == "Say more" and p["follower_count"] == 12345678
    assert p["medium"] == "threads"
    assert p["author_key"] == adapter_base.author_key("threads", "threads")
    (method, path, params), = [r for r in requests if r[1] == "/v1.0/profile_lookup"]
    assert method == "GET"
    assert params["username"] == "threads", "@ を剥がしていない"
    assert "biography" in params["fields"] and "follower_count" in params["fields"]


def test_profile_lookupは標準アクセスの断りを権限不足と混ぜない():
    """公式 4 つ以外は API が 400（権限とは別の理由）→ `AdapterError`。"""
    with _server() as (base_url, requests):
        with pytest.raises(adapter_base.AdapterError) as e:
            _adapter(base_url).profile_lookup("nigamilab")
    assert not isinstance(e.value, adapter_base.PermissionMissing)
    assert "FAKE-SECRET" not in str(e.value)


# ======================================================== アダプタ: 権限不足

@pytest.mark.parametrize("suffix,call,permission", [
    ("/keyword_search", lambda a: a.keyword_search("お茶"), "threads_keyword_search"),
    ("/mentions", lambda a: a.mentions(), "threads_manage_mentions"),
    ("/mentions", lambda a: a.inbox(), "threads_manage_mentions"),
    ("/profile_lookup", lambda a: a.profile_lookup("threads"), "threads_profile_discovery"),
])
def test_権限不足はPermissionMissing(suffix, call, permission):
    """**黙って 0 件にしない**（受け入れ (c)）。doctor と同じ判定。"""
    with _server({suffix: "permission"}) as (base_url, requests):
        with pytest.raises(adapter_base.PermissionMissing) as e:
            call(_adapter(base_url))
    assert e.value.permission == permission
    assert isinstance(e.value, adapter_base.AdapterError)
    assert "乗っていません" in str(e.value)
    assert "FAKE-SECRET" not in str(e.value)


def test_5xxは権限不足ではなくAdapterError():
    """**「乗っていない」と「確かめられない」を混ぜない**（doctor と同じ）。"""
    with _server({"/keyword_search": "5xx"}) as (base_url, requests):
        with pytest.raises(adapter_base.AdapterError) as e:
            _adapter(base_url).keyword_search("お茶")
    assert not isinstance(e.value, adapter_base.PermissionMissing)
    assert "503" in str(e.value)


def test_200のerror本文でも権限の語があればPermissionMissing():
    class _H(_ReadOnlyThreads):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            self.requests.append(("GET", parsed.path, {}))
            self._json(200, {"error": {"message": "Missing permission threads_keyword_search",
                                       "code": 200}})
    requests: list = []
    handler = type("H2", (_H,), {"requests": requests})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(adapter_base.PermissionMissing):
            _adapter(f"http://127.0.0.1:{server.server_port}").keyword_search("お茶")
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ======================================================== CLI

def _scan_for(root: str, needle: str) -> list:
    """`root` 配下の全ファイルを読んで `needle` を含むものを返す（本文の保存を検出）。"""
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as f:
                    if needle.encode("utf-8") in f.read():
                        hits.append(path)
            except OSError:
                continue
    return hits


def test_topics_searchは材料を出し本文をどこにも書かない(account, thth_root):
    with _server() as (base_url, requests):
        r = _cli(["topics", account["name"], "--search", "お茶"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "投稿者の異なり数: 2" in out, out
    assert "件数            : 3" in out
    assert "上位 3 投稿者の占有率: 100%" in out
    assert "直近の投稿時刻  : 2026-09-14T01:00:00+0000" in out
    assert "タグ付きの割合  : 67%（2/3）" in out
    # 本文は**画面に**先頭 60 字で出る。
    assert SEARCH_TEXT_A in out
    # 末尾に「記録するのは人」の 1 行（指図の語は使わない）。
    assert "記録するのは人" in out
    assert "すべき" not in out and "してください" not in out.replace("thth auth", "")
    # **本文がディスクのどこにも無い**（§4.3「入れないもの」）。
    for root in (thth_root, account["repo_dir"], os.path.dirname(account["token_path"]),
                 account["accounts_dir"]):
        assert _scan_for(root, SEARCH_TEXT_A) == [], f"検索の本文が保存されている: {root}"
        assert _scan_for(root, SEARCH_TEXT_B) == []
    # TOP が既定。
    (_m, _p, params), = [x for x in requests if x[1] == "/v1.0/keyword_search"]
    assert params["search_type"] == "TOP"


def test_topics_search_jsonは件数と分母つきで本文を持たない(account, thth_root):
    with _server() as (base_url, requests):
        r = _cli(["topics", account["name"], "--search", "お茶", "--recent", "--json"],
                 base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["n"] == 3 and data["search_type"] == "RECENT"
    assert data["authors"] == {"distinct": 2, "with_username": 3, "top_share": 1.0,
                               "top_k": 3, "top_counts": [2, 1]}
    assert data["tagged"] == {"count": 2, "denominator": 3, "ratio": 2 / 3, "known": True}
    assert data["replies"] == {"count": 1, "denominator": 3}
    assert data["latest_timestamp"] == "2026-09-14T01:00:00+0000"
    assert SEARCH_TEXT_A not in r.stdout and SEARCH_TEXT_B not in r.stdout
    assert _scan_for(thth_root, SEARCH_TEXT_A) == []


def test_topics_searchは権限不足をrc2でthth_authを案内する(account):
    """**5 権限のトークン**（受け入れ (c)）。500 を黙って返さない・0 件にしない。"""
    with _server({"/keyword_search": "permission"}) as (base_url, requests):
        r = _cli(["topics", account["name"], "--search", "お茶"], base_url)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "threads_keyword_search がトークンに乗っていません" in r.stderr
    assert f"thth auth {account['name']}" in r.stderr
    assert "FAKE-SECRET" not in r.stdout + r.stderr
    assert "異なり数" not in r.stdout


def test_topics_searchは語が空ならrc2(account):
    with _server() as (base_url, requests):
        r = _cli(["topics", account["name"], "--search", "  "], base_url)
    assert r.returncode == 2
    assert requests == []


def test_search_materialはtopic_tagが無ければ割合を判らないにする():
    rows = [{"message_id": "X", "username": "u", "timestamp": "2026-09-01T00:00:00+0000"}]
    m = threads_read_cli.search_material(rows, q="q", search_type="TOP", limit=25)
    assert m["tagged"] == {"count": 0, "denominator": 0, "ratio": None, "known": False}
    assert m["authors"]["distinct"] == 1
    m0 = threads_read_cli.search_material([], q="q", search_type="TOP", limit=25)
    assert m0["n"] == 0 and m0["authors"]["top_share"] is None
    assert m0["latest_timestamp"] is None


def test_mentionsコマンドは一覧を出し台帳に書かない(account, thth_root):
    with _server() as (base_url, requests):
        r = _cli(["mentions", account["name"]], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "言及 2 件" in r.stdout
    assert "M1" in r.stdout and "@carol" in r.stdout
    assert "reply_to" in r.stdout
    assert not os.path.isdir(os.path.join(account["repo_dir"], "data", "sns", "inbox"))


def test_mentionsコマンドjson(account):
    with _server() as (base_url, requests):
        r = _cli(["mentions", account["name"], "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["n"] == 2
    assert [m["message_id"] for m in data["mentions"]] == ["M1", "M2"]
    assert data["mentions"][0]["medium"] == "threads"


def test_mentionsコマンドは権限不足をrc2で断る(account):
    with _server({"/mentions": "permission"}) as (base_url, requests):
        r = _cli(["mentions", account["name"]], base_url)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "threads_manage_mentions がトークンに乗っていません" in r.stderr
    assert f"thth auth {account['name']}" in r.stderr


def test_profileコマンド(account):
    with _server() as (base_url, requests):
        r = _cli(["profile", account["name"], "threads"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "@threads" in r.stdout and "Threads" in r.stdout
    assert "12345678" in r.stdout
    assert "標準アクセスでは Meta 公式の 4 つ" in r.stdout


def test_profileコマンドjson(account):
    with _server() as (base_url, requests):
        r = _cli(["profile", account["name"], "@threads", "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["profile"]["username"] == "threads"
    assert data["profile"]["follower_count"] == 12345678
    assert "標準アクセス" in data["standard_access_note"]


def test_profileコマンドは権限不足をrc2で断る(account):
    with _server({"/profile_lookup": "permission"}) as (base_url, requests):
        r = _cli(["profile", account["name"], "threads", "--json"], base_url)
    assert r.returncode == 2, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["permission"] == "threads_profile_discovery"
    assert f"thth auth {account['name']}" in data["error"]


# --- 「乗っていない」と「標準アクセスの範囲外」を言い分ける（引継ぎ 2026-09-15 §3-D）

def _write_token_with_scopes(path, scopes):
    """`thth auth` が書いた形の `.token`（`scopes` が一覧・`scopes_source: response`）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "FAKE-SECRET",
                   "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999", "username": "nigamilab",
                   "scopes": list(scopes), "scopes_source": "response"}, f)


@pytest.fixture
def account_11権限(tmp_path, isolated_account_factory):
    """11 権限で認可済みのアカウント（masaru の本番 3 本と同じ形・2026-09-15 実測）。"""
    token_path = str(tmp_path / "granted.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token_with_scopes(token_path, scopes_mod.DEFAULT_SCOPES)
    acc["token_path"] = token_path
    return acc


def test_profileは権限が乗っていれば標準アクセスの範囲外と言う(account_11権限):
    """**再認可をやり直せと言わない**（やり直しても直らないので）。rc は 1。"""
    acc = account_11権限
    with _server({"/profile_lookup": "permission"}) as (base_url, requests):
        r = _cli(["profile", acc["name"], "someone-else", "--json"], base_url)
    assert r.returncode == 1, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["permission"] == "threads_profile_discovery"
    assert data["granted"] is True and data["standard_access"] is True
    assert data["scopes_source"] == threads_mod.ThreadsAdapter.SCOPES_FROM_TOKEN
    assert "トークンに乗っています" in data["error"]
    assert "標準アクセス" in data["error"]
    assert "手順_AppReview_2026-09-14.md §0′" in data["error"]
    assert "がトークンに乗っていません" not in data["error"]
    # **再認可の案内をしない**（`thth auth <account>` をやり直せ、とは言わない）。
    assert "やり直してください" not in data["error"]


def test_権限が判らなければ従来どおり再認可を案内する(account):
    """`.token` の `scopes` が null（`thth token set` 発行）＝**判らない**。

    判らないことを「乗っている」にしない——従来の rc=2・再認可の案内のまま。
    """
    with _server({"/profile_lookup": "permission"}) as (base_url, requests):
        r = _cli(["profile", account["name"], "threads", "--json"], base_url)
    assert r.returncode == 2, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["granted"] is False and data["scopes_source"] is None
    assert f"thth auth {account['name']}" in data["error"]


def test_mentionsも乗っていれば標準アクセスの範囲外と言う(account_11権限):
    acc = account_11権限
    with _server({"/mentions": "permission"}) as (base_url, requests):
        r = _cli(["mentions", acc["name"]], base_url)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "トークンに乗っています" in r.stderr
    assert "テスターからの言及だけ" in r.stderr


def test_tokenが無ければrc1で網に出ない(tmp_path, isolated_account_factory):
    acc = isolated_account_factory(token=str(tmp_path / "無い.token"))
    with _server() as (base_url, requests):
        r = _cli(["mentions", acc["name"]], base_url)
    assert r.returncode == 1
    assert "token が無い" in r.stderr
    assert requests == []


def test_口を持たない媒体はrc1で網に出ない(tmp_path, isolated_account_factory):
    acc = isolated_account_factory(media="bluesky")
    with _server() as (base_url, requests):
        r = _cli(["profile", acc["name"], "threads"], base_url)
    assert r.returncode == 1
    assert "この口" in r.stderr
    assert requests == []


# ======================================================== collect の inbox

NOW_ISO = "2026-09-12T12:00:00+09:00"


def _inbox_rows(repo_dir, month="2026-09"):
    path = os.path.join(repo_dir, "data", "sns", "inbox", f"{month}.ndjson")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _posted_account(tmp_path, factory, token_path):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1", "posted_at": "2026-09-12T10:00:00+09:00"}))
    acc = factory(repo_dir=pair["work"], production=True, token=token_path)
    return pair, acc


def test_collectのinboxにThreadsの言及が落ちmessage_idで冪等(tmp_path, isolated_account_factory):
    import datetime
    from thth import jst
    now = datetime.datetime(2026, 9, 12, 12, 0, tzinfo=jst.JST)
    token_path = str(tmp_path / "a.token")
    _write_token(token_path)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    with _server() as (base_url, requests):
        adapter = _adapter(base_url)
        r1 = collect_mod.collect_once(acc["name"], adapter=adapter, now=now,
                                      log=lambda _l: None)
        r2 = collect_mod.collect_once(acc["name"], adapter=adapter, now=now,
                                      log=lambda _l: None)
    assert not any(e.startswith("inbox") for e in r1["errors"]), r1["errors"]
    rows = _inbox_rows(pair["work"])
    assert [r["message_id"] for r in rows] == ["M1", "M2"], rows
    assert rows[0]["kind"] == "inbox" and rows[0]["medium"] == "threads"
    assert rows[0]["reply_deadline"] is None
    assert rows[0]["author_key"] == adapter_base.author_key("threads", "carol")
    # 2 度目は増えない（`message_id` で冪等）。
    assert len(_inbox_rows(pair["work"])) == 2
    # 言及の口は 2 回叩いた（GET だけ）。
    assert sum(1 for m, p, _q in requests if p.endswith("/mentions")) == 2


def test_権限が無いcollectはerrorsに積まずinbox_stateを残して続行する(tmp_path, isolated_account_factory):
    """**投稿の採取は止めない**（設計 v2 §4.3・受け入れ (c) の collect 側）。

    **2026-09-14（本番 P1）に「`errors` に 1 行積む」から変えた。** 5 権限の
    トークンでは inbox が毎 run 権限なしで、それを失敗と呼ぶと `thth run` が
    10 分ごとに「採取は完全ではありません」を出し続ける。権限なしは状態
    （`inbox_state: permission_missing`・board の `inbox=権限なし`）。
    """
    import datetime
    from thth import jst
    now = datetime.datetime(2026, 9, 12, 12, 0, tzinfo=jst.JST)
    token_path = str(tmp_path / "a.token")
    _write_token(token_path)
    pair, acc = _posted_account(tmp_path, isolated_account_factory, token_path)

    with _server({"/mentions": "permission"}) as (base_url, requests):
        result = collect_mod.collect_once(acc["name"], adapter=_adapter(base_url), now=now,
                                          log=lambda _l: None)
    assert not any(e.startswith("inbox") for e in result["errors"]), result["errors"]
    assert result["inbox"]["state"] == "permission_missing"
    assert result["inbox"]["permission"] == "threads_manage_mentions"
    assert "FAKE-SECRET" not in json.dumps(result, ensure_ascii=False)
    assert _inbox_rows(pair["work"]) == []
    # **投稿の数は採れている**（inbox の失敗が投稿の採取を巻き込んでいない）。
    insight = os.path.join(pair["work"], "data", "sns", "insights", "posts", "POST1.ndjson")
    assert os.path.exists(insight), "権限不足の inbox が投稿の採取を止めている"
    assert result["posts"] == 1
