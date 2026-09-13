"""`thth send`（同席の様態）を Bluesky・Mastodon で端から端まで通す（v2-3・2026-09-13）。

**本物の API は 1 つも叩かない。** 台帳の `service`／`instance` を偽サーバに向け、
`tests/test_bluesky_adapter.py`・`tests/test_mastodon_adapter.py` の偽サーバを
そのまま使う（`tests/test_media_roundtrip.py` の型）。

**なぜ `throw` とは別に要るか。** masaru の最初の実投稿は queue でなく
`thth send` で出す（`masaru-threads` の疎通確認と同じ・引継ぎ 2026-09-13 §3）。
`throw` の経路が通っても `send` が通る保証は無い——`send` は queue も書き戻しも
通らない別の関数（`core._send_locked()`）で、以前はここが `ThreadsAdapter` を
直に組み立て、上限を 500 字で固定していた。

確かめるのは 4 つ:

  1. `--production` を付けない実行は **dry-run**（媒体を 1 度も叩かない）で、
     digest を表示する。
  2. `--production --confirm <digest>` で **1 件だけ**出て、`post_id` が返る。
  3. 出した本文が `state/<account>/sent/<post_id>.json` に残る（**正本**）。
     `thth send` には書き戻す front-matter が無いので、ここが唯一の記録。
  4. **台帳が `production: false` なら `--production` を付けても出ない**
     （fail-closed・設計 §0）。repo の `accounts/masaru-bluesky.json`・
     `masaru-mastodon.json` はこの状態で配布されている。
"""
from __future__ import annotations

import contextlib
import json
import os

from tests.conftest import run_thth
from tests.test_bluesky_adapter import APP_PASSWORD, DID, HANDLE, fake_bluesky
from tests.test_mastodon_adapter import TOKEN as MASTODON_TOKEN
from tests.test_mastodon_adapter import fake_mastodon
from thth import postid as postid_mod

本文 = "THTH という道具の疎通確認です。承認は人が、送信は道具が。\n"
MASTODON_POST_ID = "110000000000000099"


def _write_token(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def _本文ファイル(tmp_path, text=本文):
    path = tmp_path / "honbun.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _digest(stdout: str) -> str:
    """dry-run が出した digest（`--confirm` に渡すもの）。"""
    行 = [l for l in stdout.splitlines() if l.startswith("digest: ")]
    assert 行, f"dry-run が digest を出していない: {stdout}"
    return 行[-1].split(": ", 1)[1].strip()


def _sent(thth_root, account_name, post_id):
    path = os.path.join(thth_root, "state", account_name, "sent",
                        f"{postid_mod.to_filename(post_id)}.json")
    assert os.path.exists(path), f"送った本文の記録が無い: {path}"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------- Bluesky
@contextlib.contextmanager
def _bluesky(tmp_path, factory, *, production):
    """偽 PDS に向けた同席用の台帳（`production` は呼び出し側が決める）。"""
    with fake_bluesky() as service:
        token_path = str(tmp_path / "bsky.token")
        _write_token(token_path, {
            "identifier": HANDLE, "app_password": APP_PASSWORD,
            "did": DID, "handle": HANDLE, "no_expiry": True,
            "user_id": DID, "username": HANDLE,
            "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = factory(
            "masaru-bluesky-test", media="bluesky", handle=HANDLE,
            service=str(service), token=token_path,
            scheduled=False, production=production)
        yield account, service


def test_send_bluesky_dry_runは媒体を叩かない(tmp_path, thth_root, isolated_account_factory):
    with _bluesky(tmp_path, isolated_account_factory, production=True) as (account, service):
        r = run_thth(["send", account["name"], "--text-file", _本文ファイル(tmp_path)])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "mode: rehearsal" in r.stdout, r.stdout
        assert 本文.strip() in r.stdout, r.stdout
        # **1 度も網に出ていない**（createSession すら呼ばない）。
        assert service.handler_cls.seen == [], service.handler_cls.seen
        assert service.handler_cls.created == []


def test_send_bluesky_productionで1件出てsentに残る(tmp_path, thth_root, isolated_account_factory):
    with _bluesky(tmp_path, isolated_account_factory, production=True) as (account, service):
        text_file = _本文ファイル(tmp_path)
        dry = run_thth(["send", account["name"], "--text-file", text_file])
        assert dry.returncode == 0, dry.stdout + dry.stderr

        real = run_thth(["send", account["name"], "--text-file", text_file,
                          "--production", "--confirm", _digest(dry.stdout)])
        assert real.returncode == 0, f"{real.returncode}: {real.stdout}{real.stderr}"
        assert "mode: production" in real.stdout, real.stdout

        # **1 件だけ**（`max_per_run` の話ではない——`send` は 1 回 1 本）。
        created = service.handler_cls.created
        assert len(created) == 1, created
        record = created[0]["payload"]["record"]
        # **渡された本文がそのまま出る**（整形も切り詰めもしない・§3.7）。
        assert record["text"] == 本文.strip()
        assert created[0]["payload"]["collection"] == "app.bsky.feed.post"

        post_id = f"at://{DID}/app.bsky.feed.post/new1"
        assert f"post_id={post_id}" in real.stdout, real.stdout
        # **`post_id` は `/` と `:` を含む**ので、記録のパスは encode を通る。
        記録 = _sent(thth_root, account["name"], post_id)
        assert 記録["text"] == 本文.strip()
        assert 記録["post_id"] == post_id


def test_send_blueskyは台帳がproduction_falseなら出さない(tmp_path, thth_root, isolated_account_factory):
    """**fail-closed。** 配布されている `accounts/masaru-bluesky.json` はこの状態。"""
    with _bluesky(tmp_path, isolated_account_factory, production=False) as (account, service):
        text_file = _本文ファイル(tmp_path)
        dry = run_thth(["send", account["name"], "--text-file", text_file])
        r = run_thth(["send", account["name"], "--text-file", text_file,
                       "--production", "--confirm", _digest(dry.stdout)])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "mode: rehearsal" in r.stdout, r.stdout
        assert service.handler_cls.created == [], "台帳が false なのに出た"
        assert service.handler_cls.seen == [], service.handler_cls.seen


# -------------------------------------------------------------- Mastodon
@contextlib.contextmanager
def _mastodon(tmp_path, factory, *, production):
    with fake_mastodon() as fake:
        token_path = str(tmp_path / "mstdn.token")
        _write_token(token_path, {
            "access_token": MASTODON_TOKEN, "no_expiry": True,
            "user_id": "9000", "username": "nigamilab",
            "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = factory(
            "masaru-mastodon-test", media="mastodon", handle="nigamilab",
            instance=fake.instance, token=token_path,
            scheduled=False, production=production)
        yield account, fake


def _mastodon_posts(fake):
    return [r for r in fake.requests if r["method"] == "POST"]


def test_send_mastodon_dry_runは媒体を叩かない(tmp_path, thth_root, isolated_account_factory):
    with _mastodon(tmp_path, isolated_account_factory, production=True) as (account, fake):
        r = run_thth(["send", account["name"], "--text-file", _本文ファイル(tmp_path)])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "mode: rehearsal" in r.stdout, r.stdout
        assert fake.requests == [], fake.requests


def test_send_mastodon_productionで1件出てsentに残る(tmp_path, thth_root, isolated_account_factory):
    with _mastodon(tmp_path, isolated_account_factory, production=True) as (account, fake):
        text_file = _本文ファイル(tmp_path)
        dry = run_thth(["send", account["name"], "--text-file", text_file])
        assert dry.returncode == 0, dry.stdout + dry.stderr

        real = run_thth(["send", account["name"], "--text-file", text_file,
                          "--production", "--confirm", _digest(dry.stdout)])
        assert real.returncode == 0, f"{real.returncode}: {real.stdout}{real.stderr}"

        posts = _mastodon_posts(fake)
        assert len(posts) == 1, posts
        assert posts[0]["body"]["status"] == 本文.strip()
        assert f"post_id={MASTODON_POST_ID}" in real.stdout, real.stdout

        記録 = _sent(thth_root, account["name"], MASTODON_POST_ID)
        assert 記録["text"] == 本文.strip()


def test_send_mastodonは台帳がproduction_falseなら出さない(tmp_path, thth_root,
                                                            isolated_account_factory):
    with _mastodon(tmp_path, isolated_account_factory, production=False) as (account, fake):
        text_file = _本文ファイル(tmp_path)
        dry = run_thth(["send", account["name"], "--text-file", text_file])
        r = run_thth(["send", account["name"], "--text-file", text_file,
                       "--production", "--confirm", _digest(dry.stdout)])
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "mode: rehearsal" in r.stdout, r.stdout
        assert _mastodon_posts(fake) == [], "台帳が false なのに出た"


# ------------------------------------------------------------ 上限は媒体ごと
def test_send_blueskyの上限は300字(tmp_path, thth_root, isolated_account_factory):
    """**500 字固定ではない**（`queuefile.limit_for()` が媒体の既定を引く）。

    301 字は Threads なら通る長さ。**媒体を見ずに 500 で通すと、Bluesky 側で
    公開要求が拒まれる**（出ないので害は小さいが、理由が媒体のエラーになる）。
    """
    with _bluesky(tmp_path, isolated_account_factory, production=True) as (account, service):
        text_file = _本文ファイル(tmp_path, "あ" * 301)
        r = run_thth(["send", account["name"], "--text-file", text_file])
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "上限 300 字" in r.stdout, r.stdout
        assert service.handler_cls.seen == []
