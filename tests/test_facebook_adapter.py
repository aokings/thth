"""FacebookAdapter（`thth/adapters/facebook.py`）を loopback の偽 Graph API だけで確かめる。

**本物の Graph API は 1 回も叩かない。** ここで通ったことは「Meta の API 仕様と合っている」
の証明ではなく、「こちらが（読んだ文書のとおりに）投げ、返ってきた形を正しく写す」の証明。
"""
from __future__ import annotations

import contextlib
import dataclasses
import http.server
import json
import threading
import urllib.parse

import pytest

from thth import adapters as adapters_mod
from thth import oauth
from thth.adapters import base
from thth.adapters import facebook as fb

TOKEN = "fb-page-token-do-not-leak-0123456789"
PAGE = "61596075596464"
POST = PAGE + "_1001"
V = "/" + fb.GRAPH_VERSION


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict
    calls: list

    def log_message(self, *a):
        pass

    def _answer(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        raw = b""
        if self.headers.get("Content-Length"):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
        form = {}
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            form = urllib.parse.parse_qs(raw.decode(), keep_blank_values=True)
        self.calls.append({"method": method, "path": parsed.path, "query": query, "form": form,
                           "raw": raw, "content_type": self.headers.get("Content-Type")})
        route = self.routes.get((method, parsed.path))
        status, body = route(query, form, raw) if callable(route) else route or (404, {"error": {"message": "nope", "code": 803}})
        payload = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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


def adapter(base_url):
    return fb.FacebookAdapter(base_url=base_url, access_token=TOKEN, page_id=PAGE)


def leaked(result):
    return TOKEN in json.dumps(dataclasses.asdict(result) if dataclasses.is_dataclass(result) else str(result),
                               ensure_ascii=False)


# --- 境界 -------------------------------------------------------------------

def test_REGISTRYにfacebookが載り境界の語彙に収まる():
    cls = adapters_mod.REGISTRY["facebook"]
    assert cls is fb.FacebookAdapter
    assert set(cls.CAPABILITIES) <= base.KNOWN_CAPABILITIES
    assert cls.capabilities() == {"recent_posts", "thread_read", "views", "link_preview",
                                  "account_insights", "carousel", "edit_post", "hide_reply"}
    assert cls.TOKEN_KEYS == ("access_token",) and cls.has_token({"access_token": "x"})
    assert cls.TOKEN_NO_EXPIRY is True and cls.AUTH_NEEDS_APP_ENV is True
    assert cls.DELETE_PERMISSION == "pages_manage_posts"
    assert cls.is_post_id(POST) and not cls.is_post_id("1001") and not cls.is_post_id("1_2/../x")


def test_期限を持たない鍵はremainingがNone():
    token = {"obtained_at": "2026-10-29T10:00:00+09:00", "no_expiry": True}
    import datetime
    now = datetime.datetime.fromisoformat("2027-10-29T10:00:00+09:00")
    assert oauth.token_age_and_remaining(token, now)[1] is None


def test_いいねとシェアはその媒体ではできないと断る():
    a = adapter("http://127.0.0.1:9")
    assert not a.reaction_supported("like") and not a.reaction_supported("repost")
    for call in (lambda: a.like(POST), lambda: a.repost(POST), lambda: a.unlike("r"), lambda: a.unrepost("r")):
        with pytest.raises(base.UnsupportedOnPlatform):
            call()


def test_from_accountはpage_idが無いと断り取り違えも断る(monkeypatch):
    monkeypatch.setenv(fb.BASE_URL_ENV, "http://127.0.0.1:9")
    with pytest.raises(ValueError, match="page_id"):
        fb.FacebookAdapter.from_account({"media": "facebook"}, {"access_token": "x"})
    with pytest.raises(ValueError, match="page_id"):
        fb.FacebookAdapter.from_account({"media": "facebook", "page_id": "../me"}, {"access_token": "x"})
    with pytest.raises(ValueError, match="facebook_page_mismatch"):
        fb.FacebookAdapter.from_account({"media": "facebook", "page_id": PAGE},
                                        {"access_token": "x", "user_id": "999"})
    made = fb.FacebookAdapter.from_account({"media": "facebook", "page_id": PAGE},
                                           {"access_token": "x", "user_id": PAGE,
                                            "scopes": ["pages_manage_posts"], "scopes_source": "response"})
    assert made.page_id == PAGE and made.granted_scopes == frozenset({"pages_manage_posts"})


@pytest.mark.parametrize("value", ["https://graph.facebook.com.evil.example", "https://evil.example",
                                   "http://graph.facebook.com", "https://graph.facebook.com/v1",
                                   "https://user@graph.facebook.com"])
def test_宛先はgraph_facebook_comとloopbackだけ(monkeypatch, value):
    monkeypatch.setenv(fb.BASE_URL_ENV, value)
    with pytest.raises(base.AdapterError, match="facebook_endpoint_invalid"):
        fb.graph_origin()
    monkeypatch.delenv(fb.BASE_URL_ENV)
    assert fb.graph_origin() == "https://graph.facebook.com"


# --- 投稿 -------------------------------------------------------------------

def test_文の投稿は本文に鍵を載せURLに載せない():
    routes = {("POST", f"{V}/{PAGE}/feed"): (200, {"id": POST})}
    with fake_graph(routes) as (url, calls):
        result = adapter(url).publish(base.Post(text="今日の一杯"), dry_run=False)
    assert result.failure == "none" and result.post_id == POST
    assert result.url == "https://www.facebook.com/" + POST
    call = calls[0]
    assert call["form"]["message"] == ["今日の一杯"] and call["form"]["access_token"] == [TOKEN]
    assert "link" not in call["form"] and "access_token" not in call["query"]


def test_リンクつきの文は最初のURLをlinkに渡す():
    routes = {("POST", f"{V}/{PAGE}/feed"): (200, {"id": POST})}
    with fake_graph(routes) as (url, calls):
        result = adapter(url).publish(
            base.Post(text="新しい記事です https://kopicha.org/a?b=1。ぜひ"), dry_run=False)
    assert result.failure == "none"
    assert calls[0]["form"]["link"] == ["https://kopicha.org/a?b=1"]


def test_返信はコメントの口に出す():
    routes = {("POST", f"{V}/{POST}/comments"): (200, {"id": POST + "_9"})}
    with fake_graph(routes) as (url, calls):
        result = adapter(url).publish(base.Post(text="ありがとう", reply_to=POST), dry_run=False)
    assert result.failure == "none" and result.post_id == POST + "_9"
    assert calls[0]["path"] == f"{V}/{POST}/comments"


@pytest.mark.parametrize("status,body,failure", [
    (400, {"error": {"message": "(#200) requires pages_manage_posts permission", "code": 200}}, "permission"),
    (400, {"error": {"message": "Error validating access token", "code": 190}}, "permission"),
    (400, {"error": {"message": "Invalid parameter", "code": 100}}, "publish_definite"),
    (500, {"error": {"message": "unknown", "code": 1}}, "publish_ambiguous"),
    (200, {}, "publish_ambiguous"),
    (200, b"not json", "publish_ambiguous"),
])
def test_投稿の失敗の種類を振り分ける(status, body, failure):
    routes = {("POST", f"{V}/{PAGE}/feed"): (status, body)}
    with fake_graph(routes) as (url, _):
        result = adapter(url).publish(base.Post(text="x"), dry_run=False)
    assert result.failure == failure and result.post_id is None and not leaked(result)


def test_投稿は接続できなければ結果不明():
    result = adapter("http://127.0.0.1:9").publish(base.Post(text="x"), dry_run=False)
    assert result.failure == "publish_ambiguous"


def test_直前の関門で止めたら投げないしdry_runも投げない():
    with fake_graph({}) as (url, calls):
        a = adapter(url)
        assert a.publish(base.Post(text="x"), dry_run=False,
                         before_publish=lambda: "quiet_hours").failure == "publish_vetoed"
        assert a.publish(base.Post(text="x"), dry_run=True).failure == "none"
    assert calls == []


# --- 画像 1 枚 ----------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"


@dataclasses.dataclass
class _Item:
    manifest: dict

    def verify(self):
        pass

    def chunks(self, chunk_size=1024 * 1024):
        yield PNG


def photo_row(**over):
    row = {"role": "media", "index": 1, "kind": "image", "format": "png", "public_size": len(PNG),
           "public_sha256": "ab" * 32, "alt": "白いカップのコーヒー", "file": "a.png",
           "width": 10, "height": 10, "duration": None}
    row.update(over)
    return row


def photo_post(rows=None, **over):
    rows = rows or [photo_row()]
    manifest = {"files": rows, "attachments": [], "post_options": {}, "captions": []}
    phases = []
    post = base.Post(text="新しい豆", media_manifest=manifest,
                     media_files=tuple(_Item(r) for r in rows),
                     media_progress=lambda phase, **d: phases.append((phase, d)), **over)
    return post, phases


def test_画像1枚はphotosにmultipartで出しalt_textとcaptionを付ける():
    routes = {("POST", f"{V}/{PAGE}/photos"): (200, {"id": "777", "post_id": POST})}
    post, phases = photo_post()
    with fake_graph(routes) as (url, calls):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == "none" and result.post_id == POST
    assert result.media == [{"sha256": "ab" * 32, "kind": "image", "alt_present": True, "remote_id": "777"}]
    raw = calls[0]["raw"]
    assert calls[0]["content_type"].startswith("multipart/form-data; boundary=")
    assert PNG in raw and "新しい豆".encode() in raw and "白いカップのコーヒー".encode() in raw
    assert b'name="alt_text_custom"' in raw and b'name="caption"' in raw and b'name="source"' in raw
    assert "access_token" not in calls[0]["query"]
    assert [p for p, _ in phases] == ["publishing", "published"]


@pytest.mark.parametrize("status,failure", [(400, "publish_definite"), (500, "media_ambiguous"), (200, "media_ambiguous")])
def test_画像の失敗の種類(status, failure):
    body = {"id": "777"} if status == 200 else {"error": {"message": "bad", "code": 1}}
    post, phases = photo_post()
    with fake_graph({("POST", f"{V}/{PAGE}/photos"): (status, body)}) as (url, _):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == failure and result.post_id is None
    assert phases[-1][0] in ("failed", "unknown")


def test_画像の権限不足はpermission():
    post, _ = photo_post()
    body = {"error": {"message": "(#200) permission", "code": 200}}
    with fake_graph({("POST", f"{V}/{PAGE}/photos"): (403, body)}) as (url, _):
        assert adapter(url).publish(post, dry_run=False).failure == "permission"


@pytest.mark.parametrize("rows,reason", [
    ([photo_row(index=i) for i in range(1, fb.CAROUSEL_MAX + 2)], "media_limit_exceeded: count"),
    ([photo_row(kind="video", format="mp4")], "unsupported_attachment: facebook/mp4"),
    ([photo_row(format="webp")], "unsupported_attachment: facebook/webp"),
    ([photo_row(public_size=fb.MAX_PHOTO_BYTES + 1)], "media_limit_exceeded: bytes"),
    ([photo_row(role="thumbnail")], "unsupported_attachment: facebook/thumbnail"),
])
def test_画像の口に載らない添付は要求の前に断る(rows, reason):
    manifest = {"files": rows, "attachments": [], "post_options": {}, "captions": []}
    assert fb.intent_error(manifest).startswith(reason)
    post, _ = photo_post(rows)
    with fake_graph({}) as (url, calls):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == "publish_definite" and calls == []


def test_画像の直前の関門はpublish_vetoed():
    post, _ = photo_post()
    with fake_graph({}) as (url, calls):
        result = adapter(url).publish(post, dry_run=False, before_publish=lambda: "account_stopped")
    assert result.failure == "publish_vetoed" and calls == []


def test_media_deliveryはfacebookの添付を受ける():
    from thth import media_delivery
    cfg = {"media": "facebook"}
    assert not media_delivery.unavailable(cfg)
    manifest = {"files": [photo_row()], "attachments": [], "post_options": {}, "captions": []}
    assert media_delivery.error_for(cfg, manifest) is None


# --- 取り下げ ---------------------------------------------------------------

def test_取り下げはsuccessが真のときだけ成功():
    with fake_graph({("DELETE", f"{V}/{POST}"): (200, {"success": True})}) as (url, calls):
        assert adapter(url).delete_post(POST) == {"deleted": True, "post_id": POST}
    assert calls[0]["method"] == "DELETE"
    with fake_graph({("DELETE", f"{V}/{POST}"): (200, {"success": False})}) as (url, _):
        with pytest.raises(base.AdapterError, match="facebook_retract_unconfirmed"):
            adapter(url).delete_post(POST)


@pytest.mark.parametrize("status,body,expected", [
    (400, {"error": {"message": "Unsupported delete request", "code": 100, "error_subcode": 33}}, "post_missing"),
    (403, {"error": {"message": "(#200) permission", "code": 200}}, "pages_manage_posts"),
    (500, {"error": {"message": "x", "code": 2}}, "facebook_retract_http_500"),
])
def test_取り下げの失敗(status, body, expected):
    with fake_graph({("DELETE", f"{V}/{POST}"): (status, body)}) as (url, _):
        with pytest.raises(base.AdapterError) as e:
            adapter(url).delete_post(POST)
    assert expected in str(e.value) and TOKEN not in str(e.value)
    if expected == "pages_manage_posts":
        assert isinstance(e.value, base.PermissionMissing)


def test_取り下げは形の違うidを投げずに断る():
    with fake_graph({}) as (url, calls):
        for bad in ("1001", "1_2/comments", "../me", ""):
            with pytest.raises(base.AdapterError, match="facebook_post_id_invalid"):
                adapter(url).delete_post(bad)
    assert calls == []


# --- 一覧・会話・数 ---------------------------------------------------------

def test_直近の投稿():
    body = {"data": [{"id": POST, "message": "あたらしい", "created_time": "2026-10-29T01:00:00+0000",
                      "permalink_url": "https://www.facebook.com/p/1"},
                     {"id": PAGE + "_1000", "created_time": "2026-10-28T01:00:00+0000"}]}
    with fake_graph({("GET", f"{V}/{PAGE}/posts"): (200, body)}) as (url, calls):
        rows = adapter(url).recent_posts(limit=500)
    assert [r["post_id"] for r in rows] == [POST, PAGE + "_1000"]
    assert rows[0]["text"] == "あたらしい" and rows[1]["text"] == "" and rows[0]["topic"] is None
    assert calls[0]["query"]["limit"] == ["100"] and calls[0]["query"]["access_token"] == [TOKEN]


def test_直近の投稿のdataが無ければ0件にしない():
    with fake_graph({("GET", f"{V}/{PAGE}/posts"): (200, {})}) as (url, _):
        with pytest.raises(base.AdapterError, match="0 件とは区別できません"):
            adapter(url).recent_posts()


def test_会話は頁を辿りsinceで古いものを落とす():
    first = {"data": [{"id": POST + "_1", "message": "おいしい", "created_time": "2026-10-28T00:00:00+0000",
                       "from": {"id": "111", "name": "Alice"}}],
             "paging": {"cursors": {"after": "CUR1"}, "next": "https://graph.facebook.com/next?access_token=" + TOKEN}}
    second = {"data": [{"id": POST + "_2", "message": "そう思う", "created_time": "2026-10-29T00:00:00+0000",
                        "from": {"id": "222"}, "parent": {"id": POST + "_1"}}]}

    def comments(query, form, raw):
        return (200, second) if query.get("after") == ["CUR1"] else (200, first)

    with fake_graph({("GET", f"{V}/{POST}/comments"): comments}) as (url, calls):
        rows = adapter(url).conversation(POST)
        later = adapter(url).conversation(POST, since="2026-10-28T12:00:00+00:00")
    assert [r["message_id"] for r in rows] == [POST + "_1", POST + "_2"]
    assert rows[0]["replied_to"] == POST and rows[1]["replied_to"] == POST + "_1"
    assert rows[0]["root_post"] == POST and rows[0]["medium"] == "facebook"
    assert rows[0]["username"] == "Alice" and rows[1]["username"] is None
    assert rows[0]["author_key"] == base.author_key("facebook", "111")
    assert [r["message_id"] for r in later] == [POST + "_2"]
    assert calls[0]["query"]["filter"] == ["stream"]
    # `paging.next` の絶対 URL は辿らない（cursor だけを使う）。
    assert all(c["path"] == f"{V}/{POST}/comments" for c in calls)


def test_会話の権限不足はPermissionMissing():
    body = {"error": {"message": "(#10) requires pages_read_user_content", "code": 10}}
    with fake_graph({("GET", f"{V}/{POST}/comments"): (403, body)}) as (url, _):
        with pytest.raises(base.PermissionMissing) as e:
            adapter(url).conversation(POST)
    assert e.value.permission == "pages_read_user_content"


def test_根を1件引く():
    body = {"id": POST, "message": "根", "created_time": "2026-10-29T00:00:00+0000",
            "permalink_url": "https://www.facebook.com/p/1", "from": {"id": PAGE, "name": "こぴちゃ"}}
    with fake_graph({("GET", f"{V}/{POST}"): (200, body)}) as (url, _):
        row = adapter(url).fetch_post(POST)
    assert row["message_id"] == POST and row["replied_to"] is None and row["permalink"].endswith("/p/1")
    other = dict(body, **{"from": {"id": "999"}})
    with fake_graph({("GET", f"{V}/{POST}"): (200, other)}) as (url, _):
        with pytest.raises(base.AdapterError, match="このページの投稿ではありません"):
            adapter(url).fetch_post(POST)


def test_数はavailableを定数で返し取れないものを0で埋めない():
    fields = {"id": POST, "comments": {"data": [], "summary": {"total_count": 3}},
              "reactions": {"data": [], "summary": {"total_count": 7}}}
    insight = {"data": [{"name": "post_media_view", "period": "lifetime", "values": [{"value": 120}]}]}
    with fake_graph({("GET", f"{V}/{POST}"): (200, fields),
                     ("GET", f"{V}/{POST}/insights"): (200, insight)}) as (url, calls):
        result = adapter(url).insights(POST)
    metrics, available = base.metrics_of(result)
    assert metrics == {"likes": 7, "replies": 3, "views": 120}      # shares が無い回は入れない
    assert available == ["views", "likes", "replies", "reposts"]
    assert calls[1]["query"]["metric"] == ["post_media_view"]


def test_表示の数が取れなくてもほかの数は返す():
    fields = {"id": POST, "shares": {"count": 2}}
    denied = {"error": {"message": "(#10) read_insights permission", "code": 10}}
    with fake_graph({("GET", f"{V}/{POST}"): (200, fields),
                     ("GET", f"{V}/{POST}/insights"): (403, denied)}) as (url, _):
        result = adapter(url).insights(POST)
    assert result["metrics"] == {"reposts": 2}
    assert "read_insights" in result["unavailable"]["views"]


# --- 素性・検査 -------------------------------------------------------------

def test_whoamiは台帳のページと違えば断る():
    with fake_graph({("GET", f"{V}/me"): (200, {"id": PAGE, "name": "こぴちゃ"})}) as (url, _):
        assert adapter(url).whoami() == {"user_id": PAGE, "username": PAGE, "name": "こぴちゃ"}
    with fake_graph({("GET", f"{V}/me"): (200, {"id": "999", "name": "別"})}) as (url, _):
        with pytest.raises(base.AdapterError, match="facebook_page_mismatch"):
            adapter(url).whoami()


def test_probeは読み取りだけで権限を測る():
    routes = {
        ("GET", f"{V}/me"): (200, {"id": PAGE, "name": "こぴちゃ"}),
        ("GET", f"{V}/debug_token"): (200, {"data": {"scopes": list(fb.SCOPES), "expires_at": 0}}),
        ("GET", f"{V}/{PAGE}/posts"): (200, {"data": [{"id": POST}]}),
        ("GET", f"{V}/{POST}/comments"): (200, {"data": []}),
        ("GET", f"{V}/{POST}"): (200, {"id": POST}),
        ("GET", f"{V}/{POST}/insights"): (200, {"data": []}),
    }
    with fake_graph(routes) as (url, calls):
        rows = adapter(url).probe(get=None)
    by = {r["key"]: r for r in rows}
    assert by["whoami"]["ok"] is True and by[fb.FacebookAdapter.DEBUG_TOKEN_KEY]["ok"] is True
    assert by["posts"]["ok"] is True and by["comments"]["ok"] is True and by["insights"]["ok"] is True
    assert by["publish"]["ok"] is True and by["reply"]["ok"] is True
    assert all({"name", "label", "permission", "key", "ok", "detail"} <= set(r) for r in rows)
    assert {c["method"] for c in calls} == {"GET"}
    assert TOKEN not in json.dumps(rows, ensure_ascii=False)


def test_probeは権限が足りなければ失敗で5xxは確かめられない():
    routes = {
        ("GET", f"{V}/me"): (200, {"id": PAGE}),
        ("GET", f"{V}/debug_token"): (200, {"data": {"scopes": ["pages_show_list"]}}),
        ("GET", f"{V}/{PAGE}/posts"): (500, {"error": {"message": "down", "code": 2}}),
    }
    with fake_graph(routes) as (url, _):
        rows = adapter(url).probe()
    by = {r["key"]: r for r in rows}
    assert by[fb.FacebookAdapter.DEBUG_TOKEN_KEY]["ok"] is False
    assert "pages_manage_posts" in by[fb.FacebookAdapter.DEBUG_TOKEN_KEY]["detail"]
    assert by["posts"]["ok"] is None and by["comments"]["ok"] is None
    assert by["publish"]["ok"] is False


def test_秘密は例外文に出ない():
    body = {"error": {"message": f"bad token {TOKEN}", "code": 1}}
    with fake_graph({("GET", f"{V}/me"): (400, body)}) as (url, _):
        with pytest.raises(base.AdapterError) as e:
            adapter(url).whoami()
    assert TOKEN not in str(e.value)
