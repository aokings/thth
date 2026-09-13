"""BlueskyAdapter を偽の XRPC サーバ（`http.server`）だけで確かめる（受け入れ T-B2）。

**本物の `bsky.social` には一切触らない。** `service` を差し替えて自前の偽サーバに
だけ向ける（`tests/test_fake_api.py` の Threads と同じ流儀）。

確かめるのは設計 v2 §4.2「境界の拡張」の受け入れ T-B2:
publish（成功・4xx・5xx・timeout・`before_publish` の拒否・dry_run・200 だが uri 無し）、
facets のバイト位置（日本語＋URL 2 本）、返信の parent/root（**親が返信のとき root が
引き継がれる**）、`getPostThread` → `Message`（3 階層）、`insights` に views が無い、
`whoami`、`count`（絵文字・結合文字）、**秘密が例外文に出ない**。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading
import time
import urllib.parse

import pytest

from thth.adapters import base as adapter_base
from thth.adapters import bluesky as bsky

DID = "did:plc:testtesttesttest"
HANDLE = "nigamilab.bsky.social"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"
ACCESS_JWT = "ACCESS-SECRET-JWT"
REFRESH_JWT = "REFRESH-SECRET-JWT"

ROOT_URI = f"at://{DID}/app.bsky.feed.post/rootrootroot"
ROOT_CID = "bafyroot"
MID_URI = f"at://{DID}/app.bsky.feed.post/midmidmidmid"
MID_CID = "bafymid"


def _post_view(uri, cid, *, handle, did, text, created_at, reply=None, counts=None):
    """`app.bsky.feed.defs#postView` 1 件（lexicon の形・L2）。"""
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at}
    if reply:
        record["reply"] = reply
    view = {
        "$type": "app.bsky.feed.defs#postView",
        "uri": uri, "cid": cid,
        "author": {"did": did, "handle": handle},
        "record": record,
        "indexedAt": created_at,
    }
    view.update(counts or {})
    return view


def _strong(uri, cid):
    return {"uri": uri, "cid": cid}


class _Handler(http.server.BaseHTTPRequestHandler):
    behavior: dict = {}
    posts: dict = {}
    thread: dict = {}
    feed: list = []
    created: list = []
    seen: list = []

    def _respond_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _nsid(self) -> str:
        return urllib.parse.urlparse(self.path).path.rsplit("/", 1)[-1]

    def _bearer(self) -> str:
        raw = self.headers.get("Authorization") or ""
        return raw[len("Bearer "):] if raw.startswith("Bearer ") else ""

    # --- POST（procedure） -------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 (http.server の命名規則)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw or b"{}")
        nsid = self._nsid()
        self.__class__.seen.append(nsid)
        mode = self.behavior.get(nsid, "ok")
        delay = self.behavior.get(f"{nsid}_delay", 0)
        if delay:
            time.sleep(delay)

        if nsid == "com.atproto.server.createSession":
            if mode == "ok":
                self._respond_json(200, {"accessJwt": ACCESS_JWT, "refreshJwt": REFRESH_JWT,
                                          "handle": HANDLE, "did": DID, "active": True})
            elif mode == "4xx":
                self._respond_json(401, {"error": "AuthenticationRequired"})
            elif mode == "echo_secret":
                # **秘密をそのまま返してくる意地の悪いサーバ。** 200 のまま error を
                # 入れてくる形（Threads 側の監査 2026-09-11 と同じ穴）も兼ねる。
                self._respond_json(200, {
                    "error": "AuthenticationRequired",
                    "message": f"rejected the secret {payload.get('password')}"})
            else:
                self._respond_json(500, {"error": "InternalServerError"})
            return

        if nsid == "com.atproto.repo.createRecord":
            self.__class__.created.append({"payload": payload, "bearer": self._bearer()})
            if mode == "ok":
                uri = f"at://{DID}/app.bsky.feed.post/new{len(self.created)}"
                self._respond_json(200, {"uri": uri, "cid": "bafynew",
                                          "validationStatus": "valid"})
            elif mode == "4xx":
                self._respond_json(400, {"error": "InvalidRequest"})
            elif mode == "5xx":
                self._respond_json(502, {"error": "UpstreamFailure"})
            elif mode == "no_uri":
                self._respond_json(200, {"cid": "bafynew"})
            elif mode == "echo_secret":
                self._respond_json(200, {"error": "ExpiredToken",
                                          "message": f"bad token {self._bearer()}"})
            else:
                self._respond_json(500, {"error": "InternalServerError"})
            return

        self._respond_json(404, {"error": "MethodNotImplemented", "message": nsid})

    # --- GET（query） -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        nsid = self._nsid()
        self.__class__.seen.append(nsid)
        mode = self.behavior.get(nsid, "ok")
        delay = self.behavior.get(f"{nsid}_delay", 0)
        if delay:
            time.sleep(delay)

        if nsid == "app.bsky.feed.getPosts":
            if mode == "4xx":
                self._respond_json(400, {"error": "InvalidRequest"})
                return
            uris = params.get("uris") or []
            found = [self.posts[u] for u in uris if u in self.posts]
            self._respond_json(200, {"posts": found})
            return

        if nsid == "app.bsky.feed.getPostThread":
            self._respond_json(200, {"thread": self.thread})
            return

        if nsid == "app.bsky.feed.getAuthorFeed":
            if mode == "4xx":
                self._respond_json(400, {"error": "InvalidRequest"})
                return
            if mode == "no_feed":
                # **200 だが feed が無い**（「取れて 0 件」と区別できない）。
                self._respond_json(200, {"cursor": "c1"})
                return
            self.__class__.seen.append(
                "getAuthorFeed:" + ",".join(params.get("actor", []))
                + "/limit=" + ",".join(params.get("limit", [])))
            self._respond_json(200, {"feed": list(self.feed), "cursor": "c1"})
            return

        if nsid == "app.bsky.actor.getProfile":
            if mode != "ok":
                self._respond_json(400, {"error": "InvalidRequest"})
                return
            self._respond_json(200, {"did": DID, "handle": HANDLE, "postsCount": 7})
            return

        self._respond_json(404, {"error": "MethodNotImplemented", "message": nsid})

    def log_message(self, format, *args):  # noqa: A002 - テスト出力を汚さない
        pass


class _ServerURL(str):
    """`service` としてそのまま使える文字列に、偽サーバの状態を覗ける
    `handler_cls` を添えたもの。"""


@contextlib.contextmanager
def fake_bluesky(behavior=None, *, posts=None, thread=None, feed=None):
    handler_cls = type("Handler", (_Handler,), {
        "behavior": dict(behavior or {}),
        "posts": dict(posts or {}),
        "thread": dict(thread or {}),
        "feed": list(feed or []),
        "created": [],
        "seen": [],
    })
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = _ServerURL(f"http://127.0.0.1:{server.server_port}")
        url.handler_cls = handler_cls
        yield url
    finally:
        server.shutdown()
        t.join(timeout=5)


def _adapter(service, **kwargs) -> bsky.BlueskyAdapter:
    kwargs.setdefault("timeout", 2.0)
    return bsky.BlueskyAdapter(service=service, identifier=HANDLE,
                                app_password=APP_PASSWORD, **kwargs)


# ---------------------------------------------------------------- publish
def test_publishが成功するとuriとbskyappのURLを返す():
    with fake_bluesky() as service:
        adapter = _adapter(service)
        result = adapter.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert result.failure == "none"
    assert result.error is None
    assert result.post_id.startswith(f"at://{DID}/app.bsky.feed.post/")
    rkey = result.post_id.rsplit("/", 1)[-1]
    assert result.url == f"https://bsky.app/profile/{HANDLE}/post/{rkey}"

    record = service.handler_cls.created[0]["payload"]["record"]
    assert record["$type"] == "app.bsky.feed.post"
    assert record["text"] == "こんにちは"
    # createdAt は UTC（末尾 Z）で入る（**L2**: `createdAt` は必須）。
    assert record["createdAt"].endswith("Z")
    assert service.handler_cls.created[0]["payload"]["collection"] == "app.bsky.feed.post"
    assert service.handler_cls.created[0]["payload"]["repo"] == DID
    # 認可は Bearer（accessJwt）で通る。
    assert service.handler_cls.created[0]["bearer"] == ACCESS_JWT


def test_publishが4xxならpublish_definite():
    with fake_bluesky({"com.atproto.repo.createRecord": "4xx"}) as service:
        result = _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=False)
    assert result.failure == "publish_definite"
    assert result.post_id is None


def test_publishが5xxならpublish_ambiguous():
    with fake_bluesky({"com.atproto.repo.createRecord": "5xx"}) as service:
        result = _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=False)
    assert result.failure == "publish_ambiguous"
    assert result.post_id is None


def test_publishがtimeoutならpublish_ambiguous():
    with fake_bluesky({"com.atproto.repo.createRecord_delay": 1.5}) as service:
        adapter = _adapter(service, timeout=0.4)
        result = adapter.publish(adapter_base.Post(text="本文"), dry_run=False)
    assert result.failure == "publish_ambiguous"
    assert result.post_id is None


def test_publishが200でもuriが無ければpublish_ambiguous():
    with fake_bluesky({"com.atproto.repo.createRecord": "no_uri"}) as service:
        result = _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=False)
    assert result.failure == "publish_ambiguous"
    assert result.error == "公開失敗: uri無し"


def test_before_publishが文字列を返すと出さない():
    with fake_bluesky() as service:
        adapter = _adapter(service)
        result = adapter.publish(adapter_base.Post(text="本文"), dry_run=False,
                                  before_publish=lambda: "継続期限を越えた")
    assert result.failure == "publish_vetoed"
    assert result.post_id is None
    # **要求そのものを出していない。**
    assert service.handler_cls.created == []


def test_before_publishはcreateRecordの直前に呼ばれる():
    calls = []
    with fake_bluesky() as service:
        adapter = _adapter(service)
        adapter.publish(adapter_base.Post(text="本文"), dry_run=False,
                         before_publish=lambda: calls.append(len(service.handler_cls.created)))
    # 呼ばれた時点で createRecord はまだ 0 回。
    assert calls == [0]
    assert len(service.handler_cls.created) == 1


def test_dry_runは何も叩かない():
    with fake_bluesky() as service:
        result = _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=True)
        seen = list(service.handler_cls.seen)
    assert result.failure == "none"
    assert result.post_id is None and result.url is None
    assert seen == []


def test_コンテナは無いのでon_container_createdを呼ばない():
    called = []
    with fake_bluesky() as service:
        _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=False,
                                   on_container_created=called.append)
    assert called == []


def test_topicは黙って無視する():
    """1 つの queue ファイルを Threads と Bluesky が拾う形を壊さない（設計 v2 §4.2）。"""
    with fake_bluesky() as service:
        result = _adapter(service).publish(
            adapter_base.Post(text="本文", topic="苦味"), dry_run=False)
        record = service.handler_cls.created[0]["payload"]["record"]
    assert result.failure == "none"
    assert "topic" not in record and "topic_tag" not in record


# ---------------------------------------------------------------- facets
def test_facetsは日本語混じりでもUTF8のバイト位置を指す():
    text = "コーヒーの話 https://example.com/a を書いた。続きは https://例.jp/b です"
    with fake_bluesky() as service:
        _adapter(service).publish(adapter_base.Post(text=text), dry_run=False)
        record = service.handler_cls.created[0]["payload"]["record"]

    facets = record["facets"]
    assert len(facets) == 2
    data = text.encode("utf-8")
    for facet, expected in zip(facets, ["https://example.com/a", "https://例.jp/b"]):
        index = facet["index"]
        # **バイトで切り出したものが URL そのもの**であること（文字位置で書いて
        # いれば日本語のぶんだけずれるので、ここで必ず落ちる）。
        assert data[index["byteStart"]:index["byteEnd"]].decode("utf-8") == expected
        assert facet["features"][0]["$type"] == "app.bsky.richtext.facet#link"
        assert facet["features"][0]["uri"] == expected
    # 文字位置とバイト位置は**実際にずれている**（ずれないなら試験になっていない）。
    assert facets[0]["index"]["byteStart"] != text.index("https://example.com/a")


def test_URLの後ろの句点はリンクに含めない():
    text = "こちら https://example.com/a。"
    facets = bsky.build_facets(text)
    assert len(facets) == 1
    data = text.encode("utf-8")
    index = facets[0]["index"]
    assert data[index["byteStart"]:index["byteEnd"]].decode("utf-8") == "https://example.com/a"


def test_URLが無ければfacetsを付けない():
    with fake_bluesky() as service:
        _adapter(service).publish(adapter_base.Post(text="ただの本文"), dry_run=False)
        record = service.handler_cls.created[0]["payload"]["record"]
    assert "facets" not in record


# ---------------------------------------------------------------- 返信
def test_親が根なら_rootとparentは同じ():
    posts = {ROOT_URI: _post_view(ROOT_URI, ROOT_CID, handle="a.bsky.social", did="did:plc:a",
                                   text="根の投稿", created_at="2026-09-13T00:00:00.000Z")}
    with fake_bluesky(posts=posts) as service:
        result = _adapter(service).publish(
            adapter_base.Post(text="返信です", reply_to=ROOT_URI), dry_run=False)
        record = service.handler_cls.created[0]["payload"]["record"]
    assert result.failure == "none"
    assert record["reply"]["parent"] == _strong(ROOT_URI, ROOT_CID)
    assert record["reply"]["root"] == _strong(ROOT_URI, ROOT_CID)


def test_親が返信なら根を引き継ぐ():
    """**親を根にすると、同じ話の続きが別スレッドに生える。**"""
    mid = _post_view(MID_URI, MID_CID, handle="b.bsky.social", did="did:plc:b",
                      text="1 段目の返信", created_at="2026-09-13T01:00:00.000Z",
                      reply={"root": _strong(ROOT_URI, ROOT_CID),
                             "parent": _strong(ROOT_URI, ROOT_CID)})
    with fake_bluesky(posts={MID_URI: mid}) as service:
        _adapter(service).publish(
            adapter_base.Post(text="2 段目", reply_to=MID_URI), dry_run=False)
        record = service.handler_cls.created[0]["payload"]["record"]
    assert record["reply"]["parent"] == _strong(MID_URI, MID_CID)
    assert record["reply"]["root"] == _strong(ROOT_URI, ROOT_CID)


def test_返信先が引けなければ出ていない扱い():
    with fake_bluesky(posts={}) as service:
        result = _adapter(service).publish(
            adapter_base.Post(text="返信", reply_to=ROOT_URI), dry_run=False)
    assert result.failure == "publish_definite"
    # createRecord まで到達していない。
    assert service.handler_cls.created == []


# ---------------------------------------------------------------- 会話
def _thread_fixture():
    """3 階層（A → A1 → A2）と兄弟 B をぶら下げた `#threadViewPost`。"""
    def node(uri, *, did, handle, text, created, parent_uri, replies=None):
        reply = None
        if parent_uri:
            reply = {"root": _strong(ROOT_URI, ROOT_CID),
                     "parent": _strong(parent_uri, "bafy" + uri[-3:])}
        return {
            "$type": "app.bsky.feed.defs#threadViewPost",
            "post": _post_view(uri, "bafy" + uri[-3:], handle=handle, did=did,
                                text=text, created_at=created, reply=reply),
            "replies": replies or [],
        }

    a2 = node(f"at://{DID}/app.bsky.feed.post/aaa2", did="did:plc:c", handle="c.bsky.social",
              text="孫の返信", created="2026-09-13T03:00:00.000Z",
              parent_uri=f"at://{DID}/app.bsky.feed.post/aaa1")
    a1 = node(f"at://{DID}/app.bsky.feed.post/aaa1", did=DID, handle=HANDLE,
              text="こちらの返し", created="2026-09-13T02:00:00.000Z",
              parent_uri=f"at://{DID}/app.bsky.feed.post/aaa0", replies=[a2])
    a0 = node(f"at://{DID}/app.bsky.feed.post/aaa0", did="did:plc:c", handle="c.bsky.social",
              text="最初の返信", created="2026-09-13T01:00:00.000Z", parent_uri=ROOT_URI,
              replies=[a1])
    b0 = node(f"at://{DID}/app.bsky.feed.post/bbb0", did="did:plc:d", handle="d.bsky.social",
              text="別の枝", created="2026-09-13T01:30:00.000Z", parent_uri=ROOT_URI)
    root = {
        "$type": "app.bsky.feed.defs#threadViewPost",
        "post": _post_view(ROOT_URI, ROOT_CID, handle=HANDLE, did=DID, text="うちの投稿",
                            created_at="2026-09-13T00:00:00.000Z"),
        "replies": [a0, b0],
    }
    return root


def test_getPostThreadを全階層のMessageに写す():
    with fake_bluesky(thread=_thread_fixture()) as service:
        messages = _adapter(service).conversation(ROOT_URI)

    ids = [m["message_id"] for m in messages]
    assert ids == [f"at://{DID}/app.bsky.feed.post/aaa0",
                   f"at://{DID}/app.bsky.feed.post/aaa1",
                   f"at://{DID}/app.bsky.feed.post/aaa2",
                   f"at://{DID}/app.bsky.feed.post/bbb0"]
    # **投稿そのものは入らない**（Threads の `/conversation` と同じ形）。
    assert ROOT_URI not in ids
    # 3 階層ぶんの親子がそのまま残る。
    by_id = {m["message_id"]: m for m in messages}
    assert by_id[ids[0]]["replied_to"] == ROOT_URI
    assert by_id[ids[1]]["replied_to"] == ids[0]
    assert by_id[ids[2]]["replied_to"] == ids[1]
    assert all(m["root_post"] == ROOT_URI for m in messages)
    # Message の形（設計 v2 §4.2）。
    first = by_id[ids[0]]
    assert first["username"] == "c.bsky.social"
    assert first["text"] == "最初の返信"
    assert first["timestamp"] == "2026-09-13T01:00:00.000Z"
    assert first["medium"] == "bluesky"
    assert first["reply_deadline"] is None
    # **式は境界のもの**（T3 の配線 2026-09-13）。以前は Bluesky だけが
    # `sha256("bluesky:" + did)` を自前で作っていた——同じ意味の欄に媒体ごとの
    # 別の式が入っていると、泉に出たあとで突き合わせる根拠が実装の履歴になる。
    assert first["author_key"] == bsky.author_key("did:plc:c")
    assert first["author_key"] == adapter_base.author_key("bluesky", "did:plc:c")
    # 身元は **did**（handle は改名できる）。
    assert first["author_key"] != adapter_base.author_key("bluesky", "c.bsky.social")
    # **author_key は非可逆**（did がそのまま残らない）。
    assert len(first["author_key"]) == 16
    assert "did:plc:c" not in first["author_key"]
    # 媒体をまたいで同じ鍵にならない（設計 v2 §2.1）。
    assert first["author_key"] != adapter_base.author_key("threads", "did:plc:c")


def test_author_keyはdidが無ければNone():
    """**空文字を鍵にしない**（誰も彼もが同じ鍵になる・`base.author_key`）。"""
    assert bsky.author_key("") is None
    assert bsky.author_key(None) is None


def test_sinceより古い返信は返さない():
    with fake_bluesky(thread=_thread_fixture()) as service:
        messages = _adapter(service).conversation(ROOT_URI, since="2026-09-13T02:00:00.000Z")
    assert [m["text"] for m in messages] == ["こちらの返し", "孫の返信"]


def test_見つからない投稿の会話は失敗として上げる():
    """**取れて 0 件と区別できないものを、0 件にしない。**"""
    thread = {"$type": "app.bsky.feed.defs#notFoundPost", "uri": ROOT_URI, "notFound": True}
    with fake_bluesky(thread=thread) as service:
        with pytest.raises(RuntimeError):
            _adapter(service).conversation(ROOT_URI)


def test_遮断された枝は飛ばすがほかの枝は取れる():
    root = _thread_fixture()
    root["replies"].insert(0, {"$type": "app.bsky.feed.defs#blockedPost",
                                "uri": f"at://{DID}/app.bsky.feed.post/zzz0",
                                "blocked": True,
                                "author": {"did": "did:plc:z"}})
    with fake_bluesky(thread=root) as service:
        messages = _adapter(service).conversation(ROOT_URI)
    assert len(messages) == 4


# ------------------------------------------------------------- recent_posts
def _feed_item(uri, *, text, created_at, reason=None):
    item = {"post": _post_view(uri, "bafy" + uri[-4:], handle=HANDLE, did=DID,
                                text=text, created_at=created_at)}
    if reason:
        item["reason"] = reason
    return item


def test_recent_postsは自分の投稿を新しい順に返す():
    """`app.bsky.feed.getAuthorFeed`（F2・境界の `recent_posts`）。"""
    feed = [
        _feed_item(ROOT_URI, text="あたらしいほう", created_at="2026-09-13T05:00:00Z"),
        _feed_item(MID_URI, text="ふるいほう", created_at="2026-09-13T00:00:00Z"),
    ]
    with fake_bluesky(feed=feed) as service:
        rows = _adapter(service).recent_posts(limit=25)
    assert [r["post_id"] for r in rows] == [ROOT_URI, MID_URI]
    assert rows[0]["text"] == "あたらしいほう"
    assert rows[0]["timestamp"] == "2026-09-13T05:00:00Z"
    assert rows[0]["url"] == f"https://bsky.app/profile/{HANDLE}/post/rootrootroot"
    # **語（Threads の topic_tag）に当たるものが無い媒体。**
    assert rows[0]["topic"] is None


def test_recent_postsは再投稿を自分の投稿に数えない():
    """`reason`（`#reasonRepost`）の付いた行は本人が書いたものではない（**L2**）。"""
    feed = [
        _feed_item(ROOT_URI, text="自分の", created_at="2026-09-13T05:00:00Z"),
        _feed_item(MID_URI, text="よそのを再投稿", created_at="2026-09-13T04:00:00Z",
                   reason={"$type": "app.bsky.feed.defs#reasonRepost",
                           "by": {"did": DID, "handle": HANDLE},
                           "indexedAt": "2026-09-13T04:00:00Z"}),
    ]
    with fake_bluesky(feed=feed) as service:
        rows = _adapter(service).recent_posts()
    assert [r["post_id"] for r in rows] == [ROOT_URI], rows


def test_recent_postsのlimitは1から100に収まる():
    with fake_bluesky(feed=[]) as service:
        _adapter(service).recent_posts(limit=5000)
        呼び = [x for x in service.handler_cls.seen if x.startswith("getAuthorFeed:")]
    assert 呼び == [f"getAuthorFeed:{DID}/limit=100"], 呼び


def test_recent_postsはfeedが無ければ0件と言わない():
    """**「取れなかった」を「取れて 0 件」にしない**（`_post_view` と同じ規律）。"""
    with fake_bluesky({"app.bsky.feed.getAuthorFeed": "no_feed"}) as service:
        with pytest.raises(adapter_base.AdapterError):
            _adapter(service).recent_posts()


# ---------------------------------------------------------------- insights
def test_insightsにviewsは無い():
    view = _post_view(ROOT_URI, ROOT_CID, handle=HANDLE, did=DID, text="うちの投稿",
                       created_at="2026-09-13T00:00:00.000Z",
                       counts={"likeCount": 5, "replyCount": 2, "repostCount": 1,
                               "quoteCount": 3})
    with fake_bluesky(posts={ROOT_URI: view}) as service:
        got = _adapter(service).insights(ROOT_URI)
    assert got["metrics"] == {"likes": 5, "replies": 2, "reposts": 1, "quotes": 3}
    assert "views" not in got["metrics"]
    assert "views" not in got["available"]
    assert got["available"] == ["likes", "replies", "reposts", "quotes"]


def test_取れなかった指標は0にしない():
    view = _post_view(ROOT_URI, ROOT_CID, handle=HANDLE, did=DID, text="うちの投稿",
                       created_at="2026-09-13T00:00:00.000Z", counts={"likeCount": 5})
    with fake_bluesky(posts={ROOT_URI: view}) as service:
        got = _adapter(service).insights(ROOT_URI)
    assert got["metrics"] == {"likes": 5}
    # **持ちうる指標の一覧は変わらない**（「取れなかった」と「持っていない」を分ける）。
    assert got["available"] == ["likes", "replies", "reposts", "quotes"]


def test_投稿が見つからないinsightsは失敗として上げる():
    with fake_bluesky(posts={}) as service:
        with pytest.raises(RuntimeError):
            _adapter(service).insights(ROOT_URI)


# ---------------------------------------------------------------- 身元と検査
def test_whoamiはdidとhandleを返す():
    with fake_bluesky() as service:
        assert _adapter(service).whoami() == {"user_id": DID, "username": HANDLE}


def test_probeはgetProfileまで見る():
    with fake_bluesky() as service:
        results = _adapter(service).probe()
    assert [r["name"] for r in results] == ["createSession", "getProfile"]
    assert all(r["ok"] for r in results)


def test_probeはgetProfileの失敗を隠さない():
    with fake_bluesky({"app.bsky.actor.getProfile": "4xx"}) as service:
        results = _adapter(service).probe()
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False


def test_capabilitiesにviewsもtopicも入らない():
    adapter = bsky.BlueskyAdapter(identifier=HANDLE, app_password=APP_PASSWORD)
    # `recent_posts` は在る（`getAuthorFeed`・F2・2026-09-13）。views・topic・
    # quota・inbox・refresh は無いまま。
    assert adapter.capabilities() == {"link_preview", "recent_posts"}
    assert adapter.quota() is None


# ---------------------------------------------------------------- 文字数
@pytest.mark.parametrize("text,expected", [
    ("こんにちは", 5),
    ("a" * 300, 300),
    ("が", 1),              # か + 濁点（結合文字）
    ("é", 1),               # e + アキュート
    ("\U0001F1EF\U0001F1F5", 1),  # 🇯🇵（地域指示記号 2 個で 1）
    ("\U0001F44D\U0001F3FD", 1),  # 👍🏽（肌の色の修飾子）
    ("\U0001F468‍\U0001F469‍\U0001F467", 1),  # 👨‍👩‍👧（ZWJ の連結）
    ("❤️", 1),          # ❤️（異体字セレクタ）
    ("あ\U0001F44D", 2),
])
def test_countは結合したものを1と数える(text, expected):
    assert bsky.count(text) == expected
    assert bsky.BlueskyAdapter(identifier=HANDLE).count(text) == expected


def test_上限は300grapheme():
    assert bsky.CHAR_LIMIT == 300
    assert bsky.BlueskyAdapter.char_limit == 300


# ---------------------------------------------------------------- 秘密
def test_App_Passwordが例外文にも結果にも出ない():
    """サーバがこちらの秘密をそのまま返してきても、外へは出さない。"""
    with fake_bluesky({"com.atproto.server.createSession": "echo_secret"}) as service:
        result = _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=False)
    assert result.failure == "publish_definite"
    assert APP_PASSWORD not in (result.error or "")
    assert "***" in result.error


def test_accessJwtが例外文に出ない():
    with fake_bluesky({"com.atproto.repo.createRecord": "echo_secret"}) as service:
        result = _adapter(service).publish(adapter_base.Post(text="本文"), dry_run=False)
    assert result.failure == "publish_ambiguous"
    assert ACCESS_JWT not in (result.error or "")


def test_conversationの失敗にも秘密が出ない():
    with fake_bluesky({"com.atproto.server.createSession": "4xx"}) as service:
        adapter = _adapter(service)
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 - 種類ではなく中身を見る
            adapter.conversation(ROOT_URI)
    assert APP_PASSWORD not in str(excinfo.value)


def test_scrubは名前でも値でも伏せる():
    assert bsky.scrub('{"password": "abcd-efgh"}') == '{"password": "***"}'
    assert bsky.scrub("Authorization: Bearer xyz") == "Authorization: ***"
    assert bsky.scrub("秘密は s3cret です", "s3cret") == "秘密は *** です"


# ---------------------------------------------------------------- auth
def test_auth_interactiveはトークンの中身を返す():
    with fake_bluesky() as service:
        token = bsky.auth_interactive(lambda: f"@{HANDLE}", lambda: APP_PASSWORD,
                                       service=service)
    assert token["identifier"] == HANDLE          # 先頭の @ は落ちる
    assert token["app_password"] == APP_PASSWORD
    assert token["did"] == DID
    assert token["handle"] == HANDLE
    # **「判らない」ではなく「期限を持たない」**（設計 v2 §4.2）。
    assert token["no_expiry"] is True
    assert token["obtained_at"].endswith("+09:00")


def test_auth_interactiveはアカウントのパスワードを断る():
    with fake_bluesky() as service:
        with pytest.raises(ValueError) as excinfo:
            bsky.auth_interactive(lambda: HANDLE, lambda: "MyRealPassword123",
                                   service=service)
        # **断ったのだから、サーバには送っていない。**
        assert service.handler_cls.seen == []
    assert "App Password" in str(excinfo.value)
    assert "MyRealPassword123" not in str(excinfo.value)


def test_auth_interactiveは空を断る():
    with fake_bluesky() as service:
        with pytest.raises(ValueError):
            bsky.auth_interactive(lambda: "", lambda: APP_PASSWORD, service=service)
        with pytest.raises(ValueError):
            bsky.auth_interactive(lambda: HANDLE, lambda: "", service=service)


def test_auth_interactiveの失敗文に秘密が出ない():
    with fake_bluesky({"com.atproto.server.createSession": "echo_secret"}) as service:
        with pytest.raises(RuntimeError) as excinfo:
            bsky.auth_interactive(lambda: HANDLE, lambda: APP_PASSWORD, service=service)
    assert APP_PASSWORD not in str(excinfo.value)


def test_トークンが無ければ_loudに断る():
    adapter = bsky.BlueskyAdapter(service="http://127.0.0.1:1", identifier="", app_password="")
    with pytest.raises(RuntimeError) as excinfo:
        adapter.whoami()
    assert "thth auth" in str(excinfo.value)
