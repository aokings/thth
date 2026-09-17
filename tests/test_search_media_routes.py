"""Bluesky・Mastodon の検索 adapter を `thth topics --search` まで通す。

adapter 単体だけでなく、台帳・token・REGISTRY・CLI の経路を、loopback の fixture
だけで確かめる。本物の API には接続しない。
"""
from __future__ import annotations

import json

from tests.test_bluesky_adapter import (
    APP_PASSWORD,
    BOB_DID_SEARCH,
    BOB_HANDLE_SEARCH,
    HANDLE,
    _post_view,
    fake_bluesky,
)
from tests.test_mastodon_adapter import TOKEN, fake_mastodon
from thth import cli as cli_mod
from thth.adapters import base as adapter_base


def _write_token(path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_Bluesky検索がCLIのJSONまで届く(
        tmp_path, isolated_account_factory, capsys):
    uri = f"at://{BOB_DID_SEARCH}/app.bsky.feed.post/cli-search"
    rows = [_post_view(
        uri, "bafycli", handle=BOB_HANDLE_SEARCH, did=BOB_DID_SEARCH,
        text="CLI から見えるコーヒー", created_at="2026-09-16T01:00:00.000Z",
        counts={"replyCount": 2})]
    token = _write_token(tmp_path / "bluesky.token", {
        "identifier": HANDLE, "app_password": APP_PASSWORD, "no_expiry": True})

    with fake_bluesky(search=rows) as service:
        account = isolated_account_factory(
            "search-bluesky", media="bluesky", handle=HANDLE,
            service=str(service), token=token, production=False)
        rc = cli_mod.main([
            "topics", account["name"], "--search", "コーヒー", "--json"])

    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    result = json.loads(captured.out)
    assert result["provider_note"] is None
    post, = result["posts"]
    assert post == {
        "post_id": uri,
        "permalink": f"https://bsky.app/profile/{BOB_HANDLE_SEARCH}/post/cli-search",
        "timestamp": "2026-09-16T01:00:00.000Z",
        "author": BOB_HANDLE_SEARCH,
        "author_key": adapter_base.author_key("bluesky", BOB_DID_SEARCH),
        "replies": 2,
        "has_replies": True,
        "replied": None,
    }


def test_Mastodon検索がCLIまで届き全文検索の制約を示す(
        tmp_path, isolated_account_factory, capsys):
    token = _write_token(tmp_path / "mastodon.token", {
        "access_token": TOKEN, "no_expiry": True, "user_id": "9000"})

    with fake_mastodon() as service:
        account = isolated_account_factory(
            "search-mastodon", media="mastodon", handle="nigamilab",
            instance=service.instance, token=token, production=False)
        rc = cli_mod.main([
            "topics", account["name"], "--search", "コーヒー", "--json"])
        json_output = capsys.readouterr()
        assert rc == 0, json_output.out + json_output.err
        result = json.loads(json_output.out)
        assert [p["post_id"] for p in result["posts"]] == ["110000000000000020"]
        assert "インスタンスの検索設定" in result["provider_note"]
        assert "0 件でも" in result["provider_note"]

        rc = cli_mod.main(["topics", account["name"], "--search", "コーヒー"])
        text_output = capsys.readouterr()

    assert rc == 0, text_output.out + text_output.err
    assert "検索の制約" in text_output.out
    assert "インスタンスの検索設定" in text_output.out
