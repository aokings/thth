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
        },
        {
            "id": "110000000000000003",
            "created_at": "2026-09-13T01:10:00.000Z",
            "in_reply_to_id": "110000000000000002",
            "content": '<p>そう思う <a href="https://example.invalid/x">'
                       '<span class="invisible">https://</span>'
                       '<span class="">example.invalid/x</span></a></p>',
            "account": {"id": "9002", "acct": "bob@other.invalid", "username": "bob"},
        },
        {
            "id": "110000000000000004",
            "created_at": "2026-09-13T02:00:00.000Z",
            "in_reply_to_id": "110000000000000003",
            "content": "<p>三段目</p>",
            "account": {"id": "9001", "acct": "alice", "username": "alice"},
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
}

ACCOUNT_FIXTURE = {"id": "9000", "username": "nigamilab", "acct": "nigamilab",
                   "display_name": "にがみラボ"}

INSTANCE_FIXTURE = {
    "domain": "example.invalid",
    "configuration": {"statuses": {"max_characters": 1234,
                                   "max_media_attachments": 4,
                                   "characters_reserved_per_url": 23}},
}


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
        path = self.path
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


def test_author_keyはinstanceとaccount_idから決まる():
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        messages = adapter.conversation(ROOT_ID)
    # 同じ人（9001）は 2 件とも同じ鍵、別の人（9002）は別の鍵
    assert messages[0]["author_key"] == messages[2]["author_key"]
    assert messages[0]["author_key"] != messages[1]["author_key"]
    # 非可逆・16 桁の 16 進
    key = messages[0]["author_key"]
    assert len(key) == 16 and all(c in "0123456789abcdef" for c in key)
    assert "9001" not in key
    # **インスタンスを混ぜる**ので、別インスタンスの同じ番号は別人
    other = mastodon_mod.MastodonAdapter(instance="https://other.invalid")
    assert other.author_key("9001") != adapter.author_key("9001")


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


# ---------------------------------------------------------------------------
# insights・whoami・probe・char_limit
# ---------------------------------------------------------------------------

def test_insightsにviewsが無い():
    with fake_mastodon() as fake:
        got = _adapter(fake).insights(ROOT_ID)
    assert got["metrics"] == {"likes": 7, "replies": 3, "reposts": 2}
    assert sorted(got["available"]) == ["likes", "replies", "reposts"]
    assert "views" not in got["metrics"]
    assert "views" not in got["available"]


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


def test_capabilitiesは空でquotaはNone():
    """topic 無し・views 無し・inbox 無し・refresh 無し（設計 v2 §4.2）。"""
    with fake_mastodon() as fake:
        adapter = _adapter(fake)
        assert adapter.capabilities() == set()
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
    assert mastodon_mod.MastodonAdapter.capabilities() == set()
    assert mastodon_mod.MastodonAdapter.CAPABILITIES == frozenset()


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
