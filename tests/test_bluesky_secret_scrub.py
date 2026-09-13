"""Bluesky の秘密は**全経路で**伏字にする（独立監査 1・v2-3・**P1-2**）。

伏字（`self._scrub()`）を通していたのは `publish` と `probe` だけだった。
`conversation`・`insights`・`whoami` の例外文は素通しで、しかも伏字の本体は
**鍵の名前**（`password:`・`accessJwt:`・`Bearer `）でしか消していなかった。

相手のサーバが値を鸚鵡返しにしてくると（饒舌な proxy・素朴な実装・`message`
に入力を echo する API）、名前が付かないので regex は素通りする:

    {"error": "InvalidRequest", "message": "rejected credential abcd-efgh-ijkl-mnop"}

その文字列は `collect` の `errors` に積まれ、**ログと `runs` の道**へ流れる。
直し方は `_xrpc()` に**判っている秘密の値**を渡し、`scrub()` に値ごと置き換え
させること（`_request()` はもう一枚の網）。
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import threading

import pytest

from tests.conftest import write_queue_file
from thth import collect as collect_mod
from thth import redact as redact_mod
from thth.adapters import base as adapter_base
from thth.adapters import bluesky as bsky

APP_PASSWORD = "abcd-efgh-ijkl-mnop"
ACCESS_JWT = "ACCESS-SECRET-JWT-0123456789"
HANDLE = "aoking.bsky.social"
DID = "did:plc:aoking"
POST_URI = f"at://{DID}/app.bsky.feed.post/one"
出したとき = "2026-09-13T09:00:00+09:00"
採取のとき = datetime.datetime.fromisoformat("2026-09-13T10:00:00+09:00")


class _鸚鵡返し(http.server.BaseHTTPRequestHandler):
    """**受け取った資格情報をそのまま `message` に返す**サーバ。

    `createSession` だけは通し（`accessJwt` を渡す）、その後の呼びでは
    `Authorization` の bearer を鸚鵡返しにする——`app_password` と `accessJwt`
    の**両方**の道を 1 つのサーバで試すため。
    """

    status = 200
    session_ok = False

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        if type(self).session_ok and self.path.endswith("createSession"):
            return self._json(200, {"accessJwt": ACCESS_JWT, "refreshJwt": "R",
                                    "did": DID, "handle": HANDLE})
        送られてきた秘密 = ""
        try:
            送られてきた秘密 = json.loads(raw).get("password") or ""
        except Exception:       # noqa: BLE001 — GET には本文が無い
            pass
        送られてきた秘密 = (送られてきた秘密
                            or (self.headers.get("Authorization") or "")[len("Bearer "):])
        self._json(type(self).status,
                   {"error": "InvalidRequest",
                    "message": f"rejected credential {送られてきた秘密} for {self.path}"})

    def _json(self, status, payload):
        out = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _go

    def log_message(self, *a):
        pass


def _立てる(*, status=200, session_ok=False):
    _鸚鵡返し.status = status
    _鸚鵡返し.session_ok = session_ok
    srv = http.server.HTTPServer(("127.0.0.1", 0), _鸚鵡返し)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def 鸚鵡返しのサーバ():
    srv = _立てる()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def 途中から鸚鵡返しのサーバ():
    """`createSession` は通る（＝`accessJwt` を握ったあと）の経路。"""
    srv = _立てる(session_ok=True)
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def _adapter(service):
    return bsky.BlueskyAdapter(service=service, identifier=HANDLE,
                               app_password=APP_PASSWORD)


呼び口 = [
    ("whoami", lambda a: a.whoami()),
    ("conversation", lambda a: a.conversation(POST_URI)),
    ("insights", lambda a: a.insights(POST_URI)),
]


@pytest.mark.parametrize("名前,呼ぶ", 呼び口, ids=[n for n, _ in 呼び口])
def test_App_Passwordが例外文に出ない(鸚鵡返しのサーバ, 名前, 呼ぶ):
    a = _adapter(鸚鵡返しのサーバ)
    with pytest.raises(Exception) as e:
        呼ぶ(a)
    文 = str(e.value)
    assert APP_PASSWORD not in 文, f"{名前} の例外文に App Password が出た: {文}"
    # **伏せたことが判る**（黙って全部消して「理由不明」にしない）。
    assert "***" in 文, 文


@pytest.mark.parametrize("名前,呼ぶ", 呼び口, ids=[n for n, _ in 呼び口])
def test_accessJwtが例外文に出ない(途中から鸚鵡返しのサーバ, 名前, 呼ぶ):
    """`createSession` が通ったあとの秘密（`accessJwt`）も同じ扱い。"""
    a = _adapter(途中から鸚鵡返しのサーバ)
    if 名前 == "whoami":
        pytest.skip("whoami は createSession だけで終わる（この道では失敗しない）")
    with pytest.raises(Exception) as e:
        呼ぶ(a)
    文 = str(e.value)
    assert ACCESS_JWT not in 文, f"{名前} の例外文に accessJwt が出た: {文}"


def test_publishとprobeも従来どおり伏せる(鸚鵡返しのサーバ):
    """既に通っていた 2 経路が、値渡しにしても崩れていないこと。"""
    a = _adapter(鸚鵡返しのサーバ)
    r = a.publish(adapter_base.Post(text="こんにちは"), dry_run=False)
    assert APP_PASSWORD not in (r.error or ""), r.error
    assert r.failure == "publish_definite", r

    b = _adapter(鸚鵡返しのサーバ)
    rows = json.dumps(b.probe(get=None), ensure_ascii=False)
    assert APP_PASSWORD not in rows, rows


def test_collectのerrorsとログに値が出ない(tmp_path, isolated_account_factory,
                                        鸚鵡返しのサーバ):
    """**ディスクとログまで**（`collect` の `errors` → 採取の失敗行 → journal）。

    `collect_once()` は `conversation` の例外を
    `errors.append(f"{post_id}: conversation: {redact(str(e))}")` で拾う。
    `redact()` は Threads の鍵しか知らないので、**アダプタが伏せそこねたら
    そこで漏れる**（`redact` に媒体を足して回る形にはしない・境界の内側で塞ぐ）。
    """
    token_path = tmp_path / "bsky.token"
    token_path.write_text(json.dumps(
        {"identifier": HANDLE, "app_password": APP_PASSWORD, "did": DID,
         "handle": HANDLE, "no_expiry": True, "user_id": DID, "username": HANDLE,
         "obtained_at": "2026-09-13T09:00:00+09:00"}), encoding="utf-8")
    os.chmod(token_path, 0o600)
    account = isolated_account_factory(
        "masaru-bluesky-scrub", media="bluesky", handle=HANDLE,
        service=鸚鵡返しのサーバ, token=str(token_path))
    # **出したことになっている原稿**（`post_id` と `posted_at` があると採りに行く）。
    write_queue_file(
        account["queue_dir"], "a.md", media="bluesky",
        fm_overrides={"account": account["name"], "status": "posted",
                      "post_id": POST_URI,
                      "posted_at": 出したとき},
        body="## bluesky\n\n出した本文です。\n")

    行: list = []
    adapter = bsky.BlueskyAdapter.from_account(
        {"service": 鸚鵡返しのサーバ},
        json.loads(token_path.read_text(encoding="utf-8")))
    # 出してから 1 時間後（`marks` の 1h に当たる＝返信と実測を採りに行く刻み）。
    out = collect_mod.collect_once(account["name"], adapter=adapter,
                                   now=採取のとき, log=行.append)

    まとめ = json.dumps(out["errors"], ensure_ascii=False) + "\n" + "\n".join(行)
    assert out["errors"], "**失敗が errors に積まれていない**（この試験の前提が崩れている）"
    assert APP_PASSWORD not in まとめ, f"**errors／ログに App Password が乗った**: {まとめ}"
    # `collect` 側が通す `redact()` の後でも消えていること（＝境界で消えている）。
    assert APP_PASSWORD not in redact_mod.redact(まとめ)
    # 採取したものを置くディレクトリにも値が残っていない。
    for root, _dirs, names in os.walk(os.path.join(account["repo_dir"], "data")):
        for name in names:
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                assert APP_PASSWORD not in f.read(), os.path.join(root, name)


def test_scrubは名前が付いていない値も消す():
    """伏字の本体（module 関数）。**値を渡せば名前が無くても消える。**"""
    文 = f"rejected credential {APP_PASSWORD} for /xrpc/foo"
    assert APP_PASSWORD in bsky.scrub(文)          # 値を渡さなければ素通り（従来）
    assert APP_PASSWORD not in bsky.scrub(文, APP_PASSWORD)
    # 短すぎる値は置き換えない（"a" で全文が伏字になるのを防ぐ既存の規律）。
    assert bsky.scrub("abc の話", "abc") == "abc の話"
