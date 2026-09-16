"""MastodonAdapter（設計 v2 §4.2・受け入れ T-B3）を偽サーバだけで確かめる。

**本物のインスタンスは 1 回も叩かない。** `instance` を `http://127.0.0.1:<port>` に
向けるので、ここで通ったことは「Mastodon の API 仕様と合っている」の証明ではなく
「**こちらが仕様どおりに投げ、返ってきた形を正しく写す**」の証明（仕様の側は
`thth/adapters/mastodon.py` の冒頭表のとおり **L2**＝一次資料の読解）。

確かめる 6 種＋α（T-B3）:
publish 成功／4xx／5xx／timeout／`before_publish` 拒否／dry_run、`Idempotency-Key`、
返信の `in_reply_to_id`、`/context` → `Message`（3 階層・HTML 除去）、
insights に views が無い、`whoami`、`char_limit`、秘密が例外文に出ない。
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
from thth.adapters import mastodon as mastodon_mod

TOKEN = "s3cr3t-mastodon-token-do-not-leak"

# 根の投稿（`/context` を引く先）と、その下にぶら下がる 3 階層。
ROOT_ID = "110000000000000001"

# `content` は HTML で返る（**L2**）。`<br />`・`<p>`・実体参照・リンクの span 分割まで
# 本物に似せておく（除去の限界は `strip_html()` の docstring にある）。
# 3 件とも `visibility: public`（本物の応答は必ず持つ・監査 P2-2）。
# `visibility` の絞り込み自体は `test_mastodon_visibility.py` が別に確かめる。
CONTEXT_FIXTURE = {
    "ancestors": [],
    "descendants": [
        {
            "id": "110000000000000002",
            "created_at": "2026-09-13T01:00:00.000Z",
            "in_reply_to_id": ROOT_ID,
            "content": "<p>おいしい&amp;にがい<br />ふたつ目の行</p>",
            "url": "https://example.invalid/@alice/110000000000000002",
            "account": {"id": "9001", "acct": "alice", "username": "alice"},
            "visibility": "public",
        },
        {
            "id": "110000000000000003",
            "created_at": "2026-09-13T01:10:00.000Z",
            "in_reply_to_id": "110000000000000002",
            "content": '<p>そう思う <a href="https://example.invalid/x">'
                       '<span class="invisible">https://</span>'
                       '<span class="">example.invalid/x</span></a></p>',
            "account": {"id": "9002", "acct": "bob@other.invalid", "username": "bob"},
            "visibility": "public",
        },
        {
            "id": "110000000000000004",
            "created_at": "2026-09-13T02:00:00.000Z",
            "in_reply_to_id": "110000000000000003",
            "content": "<p>三段目</p>",
            "account": {"id": "9001", "acct": "alice", "username": "alice"},
            "visibility": "public",
        },
    ],
}

STATUS_FIXTURE = {
    "id": ROOT_ID,
    "created_at": "2026-09-13T00:00:00.000Z",
    "url": f"https://example.invalid/@nigamilab/{ROOT_ID}",
    "content": "<p>根の投稿</p>",
    "favourites_count": 7,
    "replies_count": 3,
    "reblogs_count": 2,
    "account": {"id": "9000", "acct": "nigamilab"},
    # 本物の応答は必ず持つ（監査 P2-2）。`fetch_post()`（T1-1）の C-1 検査対象。
    "visibility": "public",
}

ACCOUNT_FIXTURE = {"id": "9000", "username": "nigamilab", "acct": "nigamilab",
                   "display_name": "にがみラボ"}

# `GET /api/v1/accounts/:id/statuses`（**L2**）。新しい順・`content` は HTML。
# 2 件とも `visibility: public`（本物の応答は必ず持つ・監査 P2-2）。
ACCOUNT_STATUSES_FIXTURE = [
    {"id": "110000000000000010", "created_at": "2026-09-13T05:00:00.000Z",
     "url": "https://example.invalid/@nigamilab/110000000000000010",
     "content": "<p>あたらしい&amp;ほう</p>", "visibility": "public"},
    {"id": ROOT_ID, "created_at": "2026-09-13T00:00:00.000Z",
     "url": f"https://example.invalid/@nigamilab/{ROOT_ID}",
     "content": "<p>根の投稿</p>", "visibility": "public"},
]

INSTANCE_FIXTURE = {
    "domain": "example.invalid",
    "configuration": {"statuses": {"max_characters": 1234,
                                   "max_media_attachments": 4,
                                   "characters_reserved_per_url": 23}},
}

# `GET /api/v2/search`（T2-1・**L2**）の `statuses`。1 件は `direct`（**C-1 の
# 規律で落ちる**べき）を混ぜる。
SEARCH_STATUSES_FIXTURE = [
    {"id": "110000000000000020", "created_at": "2026-09-16T00:00:00.000Z",
     "url": "https://example.invalid/@alice/110000000000000020",
     "content": "<p>苦いコーヒー</p>", "visibility": "public",
     "account": {"id": "9001", "acct": "alice"}, "replies_count": 2},
    {"id": "110000000000000021", "created_at": "2026-09-16T00:05:00.000Z",
     "content": "<p>内緒の話</p>", "visibility": "direct",
     "account": {"id": "9002", "acct": "bob"}},
]


class _Handler(http.server.BaseHTTPRequestHandler):
    behavior: dict
    requests: list

    # ----- 記録と応答 ------------------------------------------------------

    def _record(self, body: dict | None) -> None:
        self.__class__.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body or {},
        })

    def _json(self, status: int, payload, reason: str | None = None) -> None:
        raw = json.dumps(payload).encode("utf-8")
        if reason is None:
            self.send_response(status)
        else:
            self.send_response(status, reason)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        # rate limit ヘッダ（**L2**）。`quota()` が枠として返さないことも確かめる。
        self.send_header("X-RateLimit-Limit", "300")
        self.send_header("X-RateLimit-Remaining", "299")
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, mode: str) -> None:
        """トークンを**わざと**エラーの理由文に混ぜる（`echo_token`）。

        本物のサーバがこうする保証は無いが、**こちらが漏らさないこと**は
        こうしないと確かめられない（伏字が効いているかの検査）。
        """
        auth = self.headers.get("Authorization", "")
        if mode == "4xx":
            self._json(422, {"error": "Validation failed"})
        elif mode == "4xx_echo":
            self._json(401, {"error": f"bad token: {auth}"},
                       reason=f"Unauthorized ({auth})")
        elif mode == "5xx":
            self._json(503, {"error": "Service Unavailable"})

    # ----- 経路 ------------------------------------------------------------

    def do_POST(self):  # noqa: N802 (http.server の命名規則)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        parsed = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8")).items()}
        self._record(parsed)
        if not self.path.endswith("/api/v1/statuses"):
            self._json(404, {"error": "not found"})
            return
        delay = self.behavior.get("publish_delay", 0)
        if delay:
            time.sleep(delay)
        mode = self.behavior.get("publish", "ok")
        if mode == "ok":
            payload = dict(STATUS_FIXTURE)
            payload["id"] = "110000000000000099"
            payload["url"] = "https://example.invalid/@nigamilab/110000000000000099"
            payload["content"] = "<p>" + parsed.get("status", "") + "</p>"
            self._json(200, payload)
        elif mode == "no_id":
            self._json(200, {"url": "https://example.invalid/@nigamilab/x"})
        else:
            self._fail(mode)

    def do_GET(self):  # noqa: N802
        self._record(None)
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/v1/accounts/") and path.endswith("/statuses"):
            mode = self.behavior.get("account_statuses", "ok")
            if mode == "ok":
                self._json(200, ACCOUNT_STATUSES_FIXTURE)
            elif mode == "object":
                # **配列でない**（「取れて 0 件」と区別できない）。
                self._json(200, {"statuses": []})
            else:
                self._fail(mode)
            return
        if path == "/api/v2/instance":
            mode = self.behavior.get("instance", "ok")
            if mode == "ok":
                self._json(200, INSTANCE_FIXTURE)
            elif mode == "broken":
                self._json(200, {"domain": "example.invalid", "configuration": {}})
            else:
                self._fail(mode)
            return
        if path == "/api/v1/accounts/verify_credentials":
            mode = self.behavior.get("whoami", "ok")
            if mode == "ok":
                self._json(200, ACCOUNT_FIXTURE)
            elif mode == "no_id":
                self._json(200, {"acct": "nigamilab"})
            else:
                self._fail(mode)
            return
        if path.endswith("/context"):
            mode = self.behavior.get("context", "ok")
            if mode == "ok":
                self._json(200, CONTEXT_FIXTURE)
            elif mode == "missing":
                self._json(200, {"ancestors": []})
            elif mode == "null":
                self._json(200, {"ancestors": [], "descendants": None})
            else:
                self._fail(mode)
            return
        if path.startswith("/api/v1/statuses/"):
            mode = self.behavior.get("status", "ok")
            if mode == "ok":
                self._json(200, STATUS_FIXTURE)
            elif mode == "missing_likes":
                # **今回の応答に `favourites_count` が無い**（取れなかった回）。
                # 媒体にいいねが無いわけではない——`available` はこれで動かない。
                self._json(200, {k: v for k, v in STATUS_FIXTURE.items()
                                 if k != "favourites_count"})
            elif mode == "private":
                # **フォロワー限定の投稿**（`fetch_post()` の C-1 検査用）。
                self._json(200, {**STATUS_FIXTURE, "visibility": "private"})
            elif mode == "no_visibility":
                # **`visibility` が無い応答**（fail-closed の対象・監査 P2-2）。
                self._json(200, {k: v for k, v in STATUS_FIXTURE.items()
                                 if k != "visibility"})
            else:
                self._fail(mode)
            return
        if path == "/api/v2/search":
            mode = self.behavior.get("search", "ok")
            if mode == "ok":
                self._json(200, {"accounts": [], "hashtags": [],
                                 "statuses": SEARCH_STATUSES_FIXTURE})
            elif mode == "empty":
                self._json(200, {"accounts": [], "hashtags": [], "statuses": []})
            elif mode == "no_statuses":
                # **200 だが statuses が無い**（「取れて 0 件」と区別できない）。
                self._json(200, {"accounts": [], "hashtags": []})
            else:
                self._fail(mode)
            return
        self._json(404, {"error": "not found"})

    def log_message(self, format, *args):  # noqa: A002 - テスト出力を汚さない
        pass


@contextlib.contextmanager
def fake_mastodon(behavior: dict | None = None):
    handler_cls = type("Handler", (_Handler,),
                       {"behavior": dict(behavior or {}), "requests": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    # `serve_forever()` の既定の poll 間隔は 0.5 秒で、`shutdown()` は次の poll まで
    # 待つ。テスト 1 本ごとに偽サーバを立て直すので、既定のままだと**中身と関係なく
    # 全体が 0.5 秒 × 本数だけ遅くなる**（実測 35 本で 17 秒 → 1.4 秒）。
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                              daemon=True)
    thread.start()
    try:
        yield type("Fake", (), {
            "instance": f"http://127.0.0.1:{server.server_port}",
            "requests": handler_cls.requests,
        })
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _adapter(fake, **kwargs) -> mastodon_mod.MastodonAdapter:
    kwargs.setdefault("timeout", 2.0)
    return mastodon_mod.MastodonAdapter(instance=fake.instance, access_token=TOKEN, **kwargs)


def _posts(fake) -> list:
    return [r for r in fake.requests if r["method"] == "POST"]


# ---------------------------------------------------------------------------
# publish の 6 種
# ---------------------------------------------------------------------------

def test_publish_成功するとpost_idとurlが返る():
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        result = adapter.publish(adapter_base.Post(text="にがい"), dry_run=False)
    assert result.failure == "none"
    assert result.post_id == "110000000000000099"
    assert result.url == "https://example.invalid/@nigamilab/110000000000000099"
    assert result.error is None
    assert _posts(fake)[0]["body"]["status"] == "にがい"


def test_publish_4xxは出ていないと判る失敗():
    """設計 v1 §3.5 の表: HTTP 4xx は `publish_definite`（inflight を消してよい）。"""
    with fake_mastodon({"publish": "4xx"}) as fake:
        result = _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
    assert result.post_id is None
    assert result.failure == "publish_definite"
    assert "422" in result.error


def test_publish_5xxは分からない失敗():
    with fake_mastodon({"publish": "5xx"}) as fake:
        result = _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
    assert result.post_id is None
    assert result.failure == "publish_ambiguous"
    assert "503" in result.error


def test_publish_timeoutは分からない失敗():
    with fake_mastodon({"publish_delay": 3}) as fake:
        result = _adapter(fake, timeout=0.4).publish(
            adapter_base.Post(text="にがい"), dry_run=False)
    assert result.post_id is None
    assert result.failure == "publish_ambiguous"
    assert "公開失敗" in result.error


def test_publish_200だがidが無いのも分からない失敗():
    with fake_mastodon({"publish": "no_id"}) as fake:
        result = _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
    assert result.post_id is None
    assert result.failure == "publish_ambiguous"


def test_publish_before_publishが拒否したら一度も投げない():
    with fake_mastodon() as fake:
        result = _adapter(fake).publish(
            adapter_base.Post(text="にがい"), dry_run=False,
            before_publish=lambda: "継続期限を越えました")
    assert result.failure == "publish_vetoed"
    assert result.post_id is None
    assert result.error == "継続期限を越えました"
    assert _posts(fake) == []       # **サーバに届いていない**


def test_publish_dry_runは何も叩かない():
    with fake_mastodon() as fake:
        result = _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=True)
    assert result.failure == "none"
    assert result.post_id is None
    assert fake.requests == []


def test_publish_on_container_createdは呼ばれない():
    """Mastodon に container 段は無い（設計 v2 §4.2「投稿と返信の媒体差」）。"""
    called = []
    with fake_mastodon() as fake:
        _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False,
                               on_container_created=called.append)
    assert called == []


# ---------------------------------------------------------------------------
# Idempotency-Key・返信・公開範囲
# ---------------------------------------------------------------------------

def test_publish_にIdempotency_Keyが付き同じ本文なら同じ値():
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        adapter.publish(adapter_base.Post(text="にがい"), dry_run=False)
        adapter.publish(adapter_base.Post(text="にがい"), dry_run=False)
        adapter.publish(adapter_base.Post(text="あまい"), dry_run=False)
    keys = [r["headers"].get("Idempotency-Key") for r in _posts(fake)]
    assert all(k for k in keys), "Idempotency-Key が付いていない"
    assert keys[0] == keys[1]       # 同じ本文の再送はサーバ側で 1 本になる（**L2**）
    assert keys[0] != keys[2]
    assert TOKEN not in "".join(keys)   # 鍵にトークンを混ぜない


def test_publish_の鍵は返信先と公開範囲でも変わる():
    with fake_mastodon() as fake:
        a = _adapter(fake)
        b = _adapter(fake, visibility="unlisted")
        a.publish(adapter_base.Post(text="にがい"), dry_run=False)
        a.publish(adapter_base.Post(text="にがい", reply_to="42"), dry_run=False)
        b.publish(adapter_base.Post(text="にがい"), dry_run=False)
    keys = [r["headers"].get("Idempotency-Key") for r in _posts(fake)]
    assert len(set(keys)) == 3


def test_返信はin_reply_to_idとして送られる():
    with fake_mastodon() as fake:
        _adapter(fake).publish(
            adapter_base.Post(text="ありがとう", reply_to=ROOT_ID), dry_run=False)
    assert _posts(fake)[0]["body"]["in_reply_to_id"] == ROOT_ID


def test_返信でなければin_reply_to_idを送らない():
    with fake_mastodon() as fake:
        _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
    assert "in_reply_to_id" not in _posts(fake)[0]["body"]


def test_visibilityは既定public上書き可():
    with fake_mastodon() as fake:
        _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
        _adapter(fake, visibility="unlisted").publish(
            adapter_base.Post(text="にがい"), dry_run=False)
    bodies = [r["body"] for r in _posts(fake)]
    assert bodies[0]["visibility"] == "public"
    assert bodies[1]["visibility"] == "unlisted"


def test_未知のvisibilityは名指しで断る():
    with pytest.raises(ValueError) as e:
        mastodon_mod.MastodonAdapter(instance="https://example.invalid", visibility="secret")
    assert "secret" in str(e.value)


def test_topicはMastodonには無いので黙って無視される():
    """1 つの queue を Threads と Mastodon が拾う形を壊さない（設計 v2 §4.2）。"""
    with fake_mastodon() as fake:
        result = _adapter(fake).publish(
            adapter_base.Post(text="にがい", topic="苦味"), dry_run=False)
    assert result.failure == "none"
    body = _posts(fake)[0]["body"]
    assert "topic" not in body and "topic_tag" not in body


def test_認可はヘッダだけでURLにもフォームにも載らない():
    with fake_mastodon() as fake:
        _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
    req = _posts(fake)[0]
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in req["path"]
    assert TOKEN not in json.dumps(req["body"], ensure_ascii=False)


# ---------------------------------------------------------------------------
# conversation（/context → Message）
# ---------------------------------------------------------------------------

def test_contextの3階層がMessageに写る():
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        messages = adapter.conversation(ROOT_ID)

    assert [m["message_id"] for m in messages] == [
        "110000000000000002", "110000000000000003", "110000000000000004"]
    # 親子の鎖（3 階層）がそのまま残る
    assert messages[0]["replied_to"] == ROOT_ID
    assert messages[1]["replied_to"] == "110000000000000002"
    assert messages[2]["replied_to"] == "110000000000000003"
    # 根は引数の post_id（Mastodon の Status は根を持たない）
    assert {m["root_post"] for m in messages} == {ROOT_ID}
    assert {m["medium"] for m in messages} == {"mastodon"}
    assert {m["reply_deadline"] for m in messages} == {None}
    assert messages[0]["username"] == "alice"
    assert messages[1]["username"] == "bob@other.invalid"
    assert messages[0]["timestamp"] == "2026-09-13T01:00:00.000Z"


def test_contextのHTMLが本文に落とされる():
    with fake_mastodon() as fake:
        messages = _adapter(fake).conversation(ROOT_ID)
    # タグは消え、`<br>` は改行に、実体参照は文字に戻る
    assert messages[0]["text"] == "おいしい&にがい\nふたつ目の行"
    assert "<" not in messages[0]["text"] and "&amp;" not in messages[0]["text"]
    # リンクの span 分割は連結されて URL が戻る（限界は docstring のとおり **L3**）
    assert messages[1]["text"] == "そう思う https://example.invalid/x"
    assert "<a href" not in messages[1]["text"]


def test_author_keyはドメイン付きacctから決まる():
    """**式は境界のもの**（`base.author_key`・T3 の配線 2026-09-13）。

    以前はここが `sha256("mastodon:" + instance + ":" + account.id)` を自前で
    作っていた。数字の `id` はインスタンスの中でしか意味を持たない（引っ越すと
    変わる・よそのインスタンスの人はそのインスタンスの id を持たない）ので、
    身元は**ドメイン付きの `acct`** に寄せた。
    """
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        messages = adapter.conversation(ROOT_ID)
        host = urllib.parse.urlsplit(fake.instance).netloc
    # 同じ人（alice）は 2 件とも同じ鍵、別の人（bob@other.invalid）は別の鍵
    assert messages[0]["author_key"] == messages[2]["author_key"]
    assert messages[0]["author_key"] != messages[1]["author_key"]
    # 式は境界の `base.author_key(medium, identity)` そのもの。
    assert messages[0]["author_key"] == adapter_base.author_key(
        "mastodon", f"alice@{host}")
    # よそから来た人の `acct` は既にドメイン付き（**補わない**）。
    assert messages[1]["author_key"] == adapter_base.author_key(
        "mastodon", "bob@other.invalid")
    # 非可逆・16 桁の 16 進
    key = messages[0]["author_key"]
    assert len(key) == 16 and all(c in "0123456789abcdef" for c in key)
    assert "alice" not in key


def test_ドメインの無いacctはインスタンスのhostを補う():
    """**別インスタンスの同名を同一人物にしない**（T3 の配線 2026-09-13）。

    Mastodon の `acct` は**自分のインスタンスの利用者だけドメインが落ちる**。
    落ちたまま鍵にすると `mastodon.social` の `aoking` と `fedibird.com` の
    `aoking` が同じ鍵になる。
    """
    a = mastodon_mod.MastodonAdapter(instance="https://mastodon.social")
    b = mastodon_mod.MastodonAdapter(instance="https://fedibird.com")
    assert a.qualified_acct("aoking") == "aoking@mastodon.social"
    assert b.qualified_acct("aoking") == "aoking@fedibird.com"
    assert a.author_key("aoking") != b.author_key("aoking")
    # 既にドメインが付いていれば、どちらから見ても同じ人。
    assert a.author_key("who@example.invalid") == b.author_key("who@example.invalid")
    # `@` 始まりの綴りも同じ人（画面から写した形）。
    assert a.author_key("@aoking") == a.author_key("aoking")
    # 空は鍵にしない。
    assert a.author_key("") is None and a.qualified_acct(None) is None


def test_sinceより古い返信は落ちるが時刻が読めないものは残る():
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        messages = adapter.conversation(ROOT_ID, since="2026-09-13T01:05:00+00:00")
    assert [m["message_id"] for m in messages] == [
        "110000000000000003", "110000000000000004"]


@pytest.mark.parametrize("mode", ["missing", "null"])
def test_descendantsが無いnullなら取れて0件にしない(mode):
    """「取れて 0 件」と「取れていない」を区別する（Threads の `_rows` と同じ理由）。"""
    with fake_mastodon({"context": mode}) as fake:
        with pytest.raises(RuntimeError) as e:
            _adapter(fake).conversation(ROOT_ID)
    assert "descendants" in str(e.value)


# ----------------------------------------------------------- fetch_post（T1-1）
def test_fetch_postは根を1件Messageの形に写す():
    """`GET /api/v1/statuses/:id` の 1 件を根として返す（設計「自分の泉」§2.1）。"""
    with fake_mastodon() as fake:
        row = _adapter(fake).fetch_post(ROOT_ID)
    assert row["message_id"] == ROOT_ID
    assert row["username"] == "nigamilab"
    assert row["text"] == "根の投稿"
    assert row["timestamp"] == "2026-09-13T00:00:00.000Z"
    # **枝の根として使うので、自分自身への参照になる**（設計「自分の泉」§2.1）。
    assert row["replied_to"] is None
    assert row["root_post"] == ROOT_ID
    assert row["medium"] == "mastodon"
    assert row["reply_deadline"] is None
    assert row["permalink"] == f"https://example.invalid/@nigamilab/{ROOT_ID}"


def test_fetch_postはprivateをC1で断る():
    """**C-1 の規律**: public・unlisted 以外は本文を返さない（監査 P2-2 と同じ fail-closed）。"""
    with fake_mastodon({"status": "private"}) as fake:
        with pytest.raises(adapter_base.AdapterError) as e:
            _adapter(fake).fetch_post(ROOT_ID)
    assert "公開の投稿ではありません" in str(e.value)


def test_fetch_postはvisibilityが無い応答も断る():
    """**無い場合も拒む**（T1-1 発注書の文言そのまま）。"""
    with fake_mastodon({"status": "no_visibility"}) as fake:
        with pytest.raises(adapter_base.AdapterError) as e:
            _adapter(fake).fetch_post(ROOT_ID)
    assert "公開の投稿ではありません" in str(e.value)


# ---------------------------------------------------------------------------
# insights・whoami・probe・char_limit
# ---------------------------------------------------------------------------

def test_recent_postsは自分の投稿を新しい順に返す():
    """`GET /api/v1/accounts/:id/statuses`（**L2**・F2 の `recent_posts`）。"""
    with fake_mastodon() as fake:
        rows = _adapter(fake, account_id="9000").recent_posts(limit=25)
        取得 = [r for r in fake.requests if "/statuses" in r["path"]
                and "/accounts/" in r["path"]]
    assert [r["post_id"] for r in rows] == ["110000000000000010", ROOT_ID], rows
    # **HTML は落として人が読む本文にする**（実体参照も戻す）。
    assert rows[0]["text"] == "あたらしい&ほう", rows[0]
    assert rows[0]["timestamp"] == "2026-09-13T05:00:00.000Z"
    assert rows[0]["url"].endswith("/110000000000000010")
    assert rows[0]["topic"] is None
    # 数字の account id を使う（`acct` ではない）。ブーストは落とす。
    assert 取得[0]["path"].startswith("/api/v1/accounts/9000/statuses?"), 取得[0]["path"]
    assert "exclude_reblogs=true" in 取得[0]["path"], 取得[0]["path"]


def test_recent_postsのlimitは40を超えない():
    """**L2**: `limit` は既定 20・最大 40。"""
    with fake_mastodon() as fake:
        _adapter(fake, account_id="9000").recent_posts(limit=1000)
        取得 = [r for r in fake.requests if "/accounts/" in r["path"]]
    assert "limit=40" in 取得[0]["path"], 取得[0]["path"]


def test_recent_postsはaccount_idが無ければwhoamiで引く():
    with fake_mastodon() as fake:
        rows = _adapter(fake).recent_posts()
        引いた = [r["path"] for r in fake.requests]
    assert rows
    assert "/api/v1/accounts/verify_credentials" in 引いた, 引いた
    assert any(p.startswith("/api/v1/accounts/9000/statuses") for p in 引いた), 引いた


def test_recent_postsは配列でなければ0件と言わない():
    with fake_mastodon({"account_statuses": "object"}) as fake:
        with pytest.raises(mastodon_mod.AdapterError):
            _adapter(fake, account_id="9000").recent_posts()


def test_recent_postsの失敗にトークンが出ない():
    with fake_mastodon({"account_statuses": "4xx_echo"}) as fake:
        with pytest.raises(mastodon_mod.AdapterError) as e:
            _adapter(fake, account_id="9000").recent_posts()
    assert TOKEN not in str(e.value), str(e.value)


def test_insightsにviewsが無い():
    with fake_mastodon() as fake:
        got = _adapter(fake).insights(ROOT_ID)
    assert got["metrics"] == {"likes": 7, "replies": 3, "reposts": 2}
    assert sorted(got["available"]) == ["likes", "replies", "reposts"]
    assert "views" not in got["metrics"]
    assert "views" not in got["available"]


def test_insightsのavailableは媒体が持ちうる指標の定数():
    """**「今回取れた指標」ではない**（Bluesky・Threads と同じ・引継ぎ 2026-09-15 §3-D）。

    以前はここだけ `available` に「取れた指標」を積んでいたので、応答から
    `favourites_count` が落ちた回に `available` からも `likes` が消え、
    **「この媒体にいいねは無い」と読めた**。
    """
    with fake_mastodon({"status": "missing_likes"}) as fake:
        got = _adapter(fake).insights(ROOT_ID)
    assert "likes" not in got["metrics"], "取れなかった指標は metrics に入れない"
    assert got["metrics"] == {"replies": 3, "reposts": 2}
    assert got["available"] == list(mastodon_mod.AVAILABLE_METRICS)
    assert "likes" in got["available"], "媒体にいいねはある（今回取れなかっただけ）"
    assert "views" not in got["available"]


def test_availableの顔ぶれは実際に引く指標と同じ():
    """定数と `_METRICS` がずれたら落ちる（片方だけ足したときの黙った食い違い）。"""
    assert tuple(name for name, _field
                 in mastodon_mod.MastodonAdapter._METRICS) == mastodon_mod.AVAILABLE_METRICS


def test_whoamiはidとacctを返す():
    with fake_mastodon() as fake:
        assert _adapter(fake).whoami() == {"user_id": "9000", "username": "nigamilab"}


def test_whoamiはidが無ければ成功にしない():
    with fake_mastodon({"whoami": "no_id"}) as fake:
        with pytest.raises(RuntimeError):
            _adapter(fake).whoami()


def test_char_limitはinstanceのmax_charactersを読む():
    with fake_mastodon() as fake:
        assert mastodon_mod.char_limit(fake.instance, timeout=2.0) == 1234
        assert _adapter(fake).char_limit() == 1234
    # 既定（設計 v2 §4.2 の MEDIA_LIMITS）はあるが、読めた値がそれを上書きする
    assert mastodon_mod.DEFAULT_CHAR_LIMIT == 500


def test_char_limitは読めなければ既定に黙って落とさない():
    with fake_mastodon({"instance": "broken"}) as fake:
        with pytest.raises(RuntimeError) as e:
            mastodon_mod.char_limit(fake.instance, timeout=2.0)
    assert "max_characters" in str(e.value)


def test_probeはverify_credentialsとinstanceを見る():
    with fake_mastodon() as fake:
        probes = _adapter(fake).probe()
    assert [p["name"] for p in probes] == ["verify_credentials", "instance"]
    assert all(p["ok"] for p in probes)
    assert "nigamilab" in probes[0]["detail"]
    assert "1234" in probes[1]["detail"]


def test_probeは失敗しても落ちずに理由を返す():
    with fake_mastodon({"whoami": "4xx", "instance": "broken"}) as fake:
        probes = _adapter(fake).probe()
    assert [p["ok"] for p in probes] == [False, False]
    assert TOKEN not in json.dumps(probes, ensure_ascii=False)


def test_capabilitiesはrecent_postsだけでquotaはNone():
    """topic 無し・views 無し・inbox 無し・refresh 無し（設計 v2 §4.2）。

    `recent_posts` だけは在る（`GET /api/v1/accounts/:id/statuses`・F2・2026-09-13）。
    `thread_read` は T1-1（`fetch_post()`・2026-09-16）、`keyword_search` は
    T2-1（`GET /api/v2/search`・2026-09-16）で足した。
    """
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        assert adapter.capabilities() == {"recent_posts", "thread_read", "keyword_search"}
        assert adapter.quota() is None
        assert adapter.inbox() == []


def test_instanceはschemeを勝手に補わない():
    with pytest.raises(ValueError) as e:
        mastodon_mod.MastodonAdapter(instance="mastodon.social")
    assert "https://mastodon.social" in str(e.value)


# ---------------------------------------------------------------------------
# 秘密（発注 §0-3・設計 §3.6）
# ---------------------------------------------------------------------------

def test_秘密はpublishのエラー欄に出ない():
    """サーバが理由文にトークンを echo し返しても、こちらは伏字にしてから返す。"""
    with fake_mastodon({"publish": "4xx_echo"}) as fake:
        result = _adapter(fake).publish(adapter_base.Post(text="にがい"), dry_run=False)
    assert result.failure == "publish_definite"
    assert TOKEN not in result.error
    assert "***" in result.error


@pytest.mark.parametrize("call", ["whoami", "insights", "conversation"])
def test_秘密は例外文に出ない(call):
    behavior = {"whoami": "4xx_echo", "status": "4xx_echo", "context": "4xx_echo"}
    with fake_mastodon(behavior) as fake:
        adapter = _adapter(fake)
        with pytest.raises(RuntimeError) as e:
            if call == "whoami":
                adapter.whoami()
            elif call == "insights":
                adapter.insights(ROOT_ID)
            else:
                adapter.conversation(ROOT_ID)
    assert TOKEN not in str(e.value)
    assert TOKEN not in repr(e.value)


# ---------------------------------------------------------------------------
# T0（境界）が main に入れた形に合わせてある部分
# ---------------------------------------------------------------------------

def test_capabilitiesは実体を作らずに引ける():
    """`select` がトークンを読まずにトピック検査の要否を決められる（T0・受け入れ 6）。"""
    assert mastodon_mod.MastodonAdapter.capabilities() == {
        "recent_posts", "thread_read", "keyword_search"}
    assert mastodon_mod.MastodonAdapter.CAPABILITIES == frozenset(
        {"recent_posts", "thread_read", "keyword_search"})


def test_from_accountは台帳とトークンから組み立てる():
    adapter = mastodon_mod.MastodonAdapter.from_account(
        {"media": "mastodon", "instance": "https://example.invalid/"},
        {"access_token": TOKEN})
    assert adapter.instance == "https://example.invalid"   # 末尾の / は落ちる
    assert adapter.access_token == TOKEN
    assert adapter.visibility == "public"


def test_from_accountはvisibilityを台帳から読む():
    adapter = mastodon_mod.MastodonAdapter.from_account(
        {"instance": "https://example.invalid", "visibility": "unlisted"}, {})
    assert adapter.visibility == "unlisted"


def test_from_accountはinstanceが無ければ名指しで断る():
    """既定の mastodon.social に黙って落とすと、書き忘れた人が知らないサーバに投げる。"""
    with pytest.raises(ValueError) as e:
        mastodon_mod.MastodonAdapter.from_account({"media": "mastodon"}, {})
    assert "instance" in str(e.value)


def test_probeの1行はdoctorがそのまま描ける形():
    """`thth/doctor.py` は `label`・`permission`・`detail`・`ok` を読む（T-B5）。"""
    with fake_mastodon() as fake:
        probes = _adapter(fake).probe()
    for row in probes:
        assert {"name", "label", "permission", "key", "ok", "detail"} <= set(row)
        assert isinstance(row["label"], str) and row["label"]
        assert isinstance(row["permission"], str) and row["permission"]


def test_probeはgetを渡されても受け取る():
    """doctor は取得口を渡す（T0）。Mastodon 側は使わないが、**署名は合わせる**。"""
    with fake_mastodon() as fake:
        probes = _adapter(fake).probe(get=lambda *a, **k: {})
    assert [p["ok"] for p in probes] == [True, True]


def test_例外はAdapterErrorでRuntimeErrorの網にも入る():
    """採取側は「例外なら記録を書かない」で成功と失敗を分けている（T0 の base）。"""
    with fake_mastodon({"context": "5xx"}) as fake:
        with pytest.raises(mastodon_mod.AdapterError):
            _adapter(fake).conversation(ROOT_ID)
        with pytest.raises(RuntimeError):
            _adapter(fake).conversation(ROOT_ID)


# ---------------------------------------------------------------- keyword_search（T2-1）

def test_keyword_searchはMessageの形で返りvisibilityで絞る():
    """`direct` の 1 件は **C-1 の規律で落ちる**（本文が返らない）。"""
    with fake_mastodon() as fake:
        result = _adapter(fake).keyword_search("コーヒー", search_type="TOP", limit=10)
    assert len(result) == 1
    row = result[0]
    assert row["message_id"] == "110000000000000020"
    assert row["username"] == "alice"
    assert row["text"] == "苦いコーヒー"
    assert row["medium"] == "mastodon"
    assert row["author_key"] == mastodon_mod.base.author_key(
        "mastodon", _adapter(fake).qualified_acct("alice"))
    assert row["root_post"] is None
    assert row["replies_count"] == 2
    assert row["has_replies"] is True
    assert row["permalink"] == "https://example.invalid/@alice/110000000000000020"
    # 内緒の話（bob・direct）は出ない。
    assert all(r["message_id"] != "110000000000000021" for r in result)

    req = [r for r in fake.requests if r["path"].startswith("/api/v2/search")][0]
    assert "type=statuses" in req["path"]
    assert "limit=10" in req["path"]


def test_keyword_searchは検索結果が空でも0件():
    with fake_mastodon({"search": "empty"}) as fake:
        result = _adapter(fake).keyword_search("無風")
    assert result == []


def test_keyword_searchはstatusesが無いと取れて0件と区別する():
    with fake_mastodon({"search": "no_statuses"}) as fake:
        with pytest.raises(mastodon_mod.AdapterError):
            _adapter(fake).keyword_search("無風")


def test_keyword_searchはsearch_typeが違えば断る():
    with fake_mastodon() as fake:
        with pytest.raises(mastodon_mod.AdapterError):
            _adapter(fake).keyword_search("苦味", search_type="HOT")


def test_keyword_searchはlimitの上限を超えたら断る():
    with fake_mastodon() as fake:
        with pytest.raises(mastodon_mod.AdapterError):
            _adapter(fake).keyword_search("苦味", limit=41)


def test_keyword_searchのvisibilityの絞りを外すと非公開が漏れる_変異の対象():
    """**変異テストの対象**（発注 T2-1）: `keyword_search()` の
    `s.get("visibility") in READABLE_VISIBILITIES` を外すと、`direct` の
    投稿（bob の「内緒の話」）が結果に混ざる——このテストは**その状態でこそ
    落ちる**（今は通る）。変異の証拠は報告に貼る。
    """
    with fake_mastodon() as fake:
        result = _adapter(fake).keyword_search("コーヒー")
    assert all(r["message_id"] != "110000000000000021" for r in result), (
        "direct の投稿が混ざっています（visibility の絞りが外れています）")
