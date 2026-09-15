"""同席の送信（`thth send`）が board と posts に現れる（運用の報告 2026-09-13）。

**本番で起きたこと。** masaru が VM から Bluesky と Mastodon へ 1 本ずつ出し、
両方成功した（`state/<account>/sent/<post_id>.json` も 1 件ずつ残った）。それなのに

  (a) `thth board` の 2 本が `last_post=(なし)` のまま、
  (b) `thth posts <account>` がその 1 本を「**外で出したもの**」と表示し、
      「うち THTH を通していないもの 1 件」と数え、
  (c) `thth send` の成功時の出力に URL が無く、実物を見に行けない。

**原因はどちらも「queue の front-matter しか見ていない」こと**（`last.json` を
書かないから、ではない——`last.json` はどの経路も書かないし、どの読み手も見て
いない）。同席の様態は queue も書き戻しも通らないので、front-matter だけを見る
読み手からは**出した事実そのものが無かったことになる**。

ここで確かめるのは 3 媒体（Bluesky・Mastodon・Threads）で共通に:

  1. `send --production` のあと、board のその account の行に `last_post` が出る
     （`last_post_source: "sent"`）。
  2. `thth posts` がその 1 本を **THTH を通したもの**に数える
     （「通していないもの 0 件」）。
  3. 成功時の出力に URL が出る（`PublishResult.url` を返さない Threads は
     `post_id` だけ——**推測した URL を出さない**）。

**本物の API は 1 つも叩かない**（`tests/test_send_media.py` と同じ型）。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import threading
import urllib.parse

import pytest

from tests.conftest import run_thth
from tests import test_bluesky_adapter as bsky_fake
from tests import test_mastodon_adapter as mstdn_fake
from tests.test_bluesky_adapter import APP_PASSWORD, DID, HANDLE, fake_bluesky
from tests.test_mastodon_adapter import TOKEN as MASTODON_TOKEN
from tests.test_mastodon_adapter import fake_mastodon
from thth import account_report as account_report_mod
from thth import report as report_mod

本文 = "同席の送信が board と posts に現れることを確かめます。\n"

BLUESKY_POST_ID = f"at://{DID}/app.bsky.feed.post/new1"
BLUESKY_URL = f"https://bsky.app/profile/{HANDLE}/post/new1"
MASTODON_POST_ID = "110000000000000099"
MASTODON_URL = f"https://example.invalid/@nigamilab/{MASTODON_POST_ID}"
THREADS_USER_ID = "12345"
THREADS_POST_ID = "17900000000000001"
THREADS_PERMALINK = "https://www.threads.net/@masaru/post/ABCDEF"


# --------------------------------------------------------------- 助け手
def _write_token(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def _本文ファイル(tmp_path, text=本文):
    path = tmp_path / "honbun.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _digest(stdout: str) -> str:
    行 = [l for l in stdout.splitlines() if l.startswith("digest: ")]
    assert 行, f"dry-run が digest を出していない: {stdout}"
    return 行[-1].split(": ", 1)[1].strip()


def _出す(account_name, text_file):
    """dry-run で digest を採り、`--production --confirm` で 1 本出す。"""
    dry = run_thth(["send", account_name, "--text-file", text_file])
    assert dry.returncode == 0, dry.stdout + dry.stderr
    real = run_thth(["send", account_name, "--text-file", text_file,
                      "--production", "--confirm", _digest(dry.stdout)])
    assert real.returncode == 0, f"{real.returncode}: {real.stdout}{real.stderr}"
    assert "mode: production" in real.stdout, real.stdout
    return real


def _board_row(account_name):
    summary = report_mod.board_summary()
    return next(r for r in summary["accounts"] if r["account"] == account_name)


def _sent記録(thth_root, account_name):
    from thth import sent as sent_mod
    at, row = sent_mod.latest_sent(os.path.join(thth_root, "state", account_name))
    assert row is not None, "sent/ に記録が無い"
    return at, row


def _確かめる(thth_root, account_name, *, post_id, url):
    """(a) board に last_post が出る／(b) posts が通したものに数える。"""
    # ---- (a) board -------------------------------------------------------
    at, 記録 = _sent記録(thth_root, account_name)
    row = _board_row(account_name)
    assert row["last_post_at"] is not None, f"board の last_post が空のまま: {row}"
    assert row["last_post_at"] == at.isoformat(), row
    assert row["last_post_source"] == "sent", row
    assert row["last_sent_post_id"] == post_id, row
    assert row["last_sent_at"] == at.isoformat(), row
    assert 記録["post_id"] == post_id

    # ---- (b) posts -------------------------------------------------------
    result = account_report_mod.recent_posts(account_name)
    assert result["error"] is None, result
    通した = [p for p in result["posts"] if p["via_thth"]]
    外 = [p for p in result["posts"] if not p["via_thth"]]
    assert [p["id"] for p in 通した] == [post_id], result["posts"]
    assert 外 == [], 外
    assert 通した[0]["source"] == "sent", 通した[0]
    assert 通した[0]["file"] is None, 通した[0]

    # 人が読む画面（運用が見たのはこちら）。
    表示 = run_thth(["posts", account_name])
    assert 表示.returncode == 0, 表示.stdout + 表示.stderr
    assert "THTH を通していないもの 0 件" in 表示.stdout, 表示.stdout
    assert "外で出したもの" not in 表示.stdout, 表示.stdout
    assert "THTH（同席の送信）" in 表示.stdout, 表示.stdout

    # `thth account` の「うち THTH を通していないもの」も同じ突合を使う。
    detail = account_report_mod.account_detail(account_name)
    assert detail["remote"]["known"] is True, detail["remote"]
    assert detail["remote"]["outside"] == [], detail["remote"]

    if url is None:
        return
    assert url  # 呼び出し側が URL を渡したなら (c) は別途確かめる


# --------------------------------------------------------------- Bluesky
@contextlib.contextmanager
def _bluesky(tmp_path, factory):
    """出した 1 本がそのまま `getAuthorFeed` にも現れる偽 PDS。"""
    feed = [{"post": bsky_fake._post_view(
        BLUESKY_POST_ID, "bafynew1", handle=HANDLE, did=DID,
        text=本文.strip(), created_at="2026-09-13T10:27:00Z")}]
    with fake_bluesky(feed=feed) as service:
        token_path = str(tmp_path / "bsky.token")
        _write_token(token_path, {
            "identifier": HANDLE, "app_password": APP_PASSWORD,
            "did": DID, "handle": HANDLE, "no_expiry": True,
            "user_id": DID, "username": HANDLE,
            "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = factory("masaru-bluesky-test", media="bluesky", handle=HANDLE,
                           service=str(service), token=token_path,
                           scheduled=False, production=True)
        yield account, service


def test_同席の送信はblueskyのboardとpostsに現れる(tmp_path, thth_root, isolated_account_factory):
    with _bluesky(tmp_path, isolated_account_factory) as (account, service):
        real = _出す(account["name"], _本文ファイル(tmp_path))
        assert f"post_id={BLUESKY_POST_ID}" in real.stdout, real.stdout
        # (c) 出たものを見に行ける（媒体が返した URL をそのまま出す）。
        assert f"URL: {BLUESKY_URL}" in real.stdout, real.stdout
        _確かめる(thth_root, account["name"], post_id=BLUESKY_POST_ID, url=BLUESKY_URL)


# -------------------------------------------------------------- Mastodon
@contextlib.contextmanager
def _mastodon(tmp_path, factory, monkeypatch):
    with fake_mastodon() as fake:
        # 出した 1 本がそのまま `GET /api/v1/accounts/9000/statuses` にも現れる。
        # **本物の応答は必ず `visibility` を持つ**（監査 P2-2・fail-closed）。
        monkeypatch.setattr(mstdn_fake, "ACCOUNT_STATUSES_FIXTURE", [
            {"id": MASTODON_POST_ID, "created_at": "2026-09-13T10:27:00.000Z",
             "url": MASTODON_URL, "content": "<p>" + 本文.strip() + "</p>",
             "visibility": "public"}])
        token_path = str(tmp_path / "mstdn.token")
        _write_token(token_path, {
            "access_token": MASTODON_TOKEN, "no_expiry": True,
            "user_id": "9000", "username": "nigamilab",
            "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = factory("masaru-mastodon-test", media="mastodon", handle="nigamilab",
                           instance=fake.instance, token=token_path,
                           scheduled=False, production=True)
        yield account, fake


def test_同席の送信はmastodonのboardとpostsに現れる(tmp_path, thth_root,
                                                    isolated_account_factory, monkeypatch):
    with _mastodon(tmp_path, isolated_account_factory, monkeypatch) as (account, fake):
        real = _出す(account["name"], _本文ファイル(tmp_path))
        assert f"post_id={MASTODON_POST_ID}" in real.stdout, real.stdout
        assert f"URL: {MASTODON_URL}" in real.stdout, real.stdout
        _確かめる(thth_root, account["name"], post_id=MASTODON_POST_ID, url=MASTODON_URL)


# --------------------------------------------------------------- Threads
class _ThreadsHandler(http.server.BaseHTTPRequestHandler):
    """公開（POST）と直近の投稿（GET）の両方を返す偽 Threads。

    `masaru-threads` は**同席専用**（queue も timer も持たない・設計 §3.7）なので、
    (a)(b) の穴がいちばん強く出る媒体——queue を見るだけの突合では、出した投稿が
    **1 本残らず**「THTH を通していないもの」になる。
    """

    created: list = []

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        params = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8")).items()}
        self.__class__.created.append({"path": self.path, "params": params})
        if self.path.endswith("/threads_publish"):
            self._json(200, {"id": THREADS_POST_ID})
        else:
            self._json(200, {"id": "container-1"})

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == f"/v1.0/{THREADS_USER_ID}/threads":
            self._json(200, {"data": [{
                "id": THREADS_POST_ID, "permalink": THREADS_PERMALINK,
                "timestamp": "2026-09-13T10:27:00+0000", "text": 本文.strip(),
                "topic_tag": None}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _threads(tmp_path, factory, monkeypatch):
    handler_cls = type("Handler", (_ThreadsHandler,), {"created": []})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        monkeypatch.setenv("THTH_THREADS_WAIT_SECONDS", "0")
        token_path = str(tmp_path / "threads.token")
        _write_token(token_path, {
            "access_token": "fake-long-lived-token", "user_id": THREADS_USER_ID,
            "username": "masaru", "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = factory("masaru-threads-test", media="threads", handle="masaru",
                           user_id=THREADS_USER_ID, token=token_path,
                           scheduled=False, production=True)
        yield account, handler_cls
    finally:
        server.shutdown()
        t.join(timeout=5)


def test_同席の送信はthreadsのboardとpostsに現れる(tmp_path, thth_root,
                                                   isolated_account_factory, monkeypatch):
    with _threads(tmp_path, isolated_account_factory, monkeypatch) as (account, handler_cls):
        real = _出す(account["name"], _本文ファイル(tmp_path))
        assert f"post_id={THREADS_POST_ID}" in real.stdout, real.stdout
        # **URL を返さない媒体では黙る**（推測した URL を出さない）。
        assert "URL: " not in real.stdout, real.stdout
        _確かめる(thth_root, account["name"], post_id=THREADS_POST_ID, url=None)


# ------------------------------------------------ 記録の素性を混ぜない（board）
def test_boardは新しいほうの記録から最後に出したものを言う(tmp_path, thth_root,
                                                          isolated_account_factory):
    """queue の書き戻し（不在の様態）と `sent/`（同席の様態）の**新しいほう**。

    `sent/` を足したせいで、timer が出した新しい投稿が古い同席の記録に隠れる、
    では直したことにならない。素性は `last_post_source` で言い分ける。
    """
    from thth import sent as sent_mod
    from tests.conftest import write_queue_file

    account = isolated_account_factory()
    state_dir = os.path.join(thth_root, "state", account["name"])
    sent_mod.write(state_dir, post_id="ふるいほう", text="ふるい本文",
                    body_hash="x", sent_at="2026-09-01T10:00:00+09:00")
    write_queue_file(account["queue_dir"], "posted.md", fm_overrides={
        "status": "posted", "post_id": "あたらしいほう",
        "posted_at": "2026-09-05T10:00:00+09:00", "approved_sha": "dummy"})

    row = _board_row(account["name"])
    assert row["last_post_at"] == "2026-09-05T10:00:00+09:00", row
    assert row["last_post_source"] == "queue", row
    # **同席の記録も消さずに出す**（鍵の追加だけ）。
    assert row["last_sent_at"] == "2026-09-01T10:00:00+09:00", row
    assert row["last_sent_post_id"] == "ふるいほう", row


def test_boardは記録が1件も無ければ判らないと言う(isolated_account):
    row = _board_row(isolated_account["name"])
    assert row["last_post_at"] is None, row
    assert row["last_post_source"] is None, row
    assert row["last_sent_at"] is None, row


@pytest.mark.parametrize("壊れかた", ["json でない", "辞書でない"])
def test_sentの壊れた記録1本で読み手が止まらない(tmp_path, thth_root, isolated_account, 壊れかた):
    from thth import sent as sent_mod

    state_dir = os.path.join(thth_root, "state", isolated_account["name"])
    sent_mod.write(state_dir, post_id="よいほう", text="本文", body_hash="x",
                    sent_at="2026-09-01T10:00:00+09:00")
    壊れ = os.path.join(state_dir, "sent", "こわれ.json")
    with open(壊れ, "w", encoding="utf-8") as f:
        f.write("{" if 壊れかた == "json でない" else "[1, 2]")

    assert sent_mod.post_ids(state_dir) == {"よいほう"}
    at, row = sent_mod.latest_sent(state_dir)
    assert row["post_id"] == "よいほう"
    assert at.isoformat() == "2026-09-01T10:00:00+09:00"
