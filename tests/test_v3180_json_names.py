"""3.18.0 A6 JSON の欄の名前の統一（構想 10/9 §3「`id`・`message_id`・`post_id` の混在」）。

**足すだけで、古い名前は消さない・変えない**（hub の observe・各レーン・MCP を壊さない）:

- 投稿・枝の 1 本の id は `post_id` にも載せる（`thth posts --json` の `id`・`thth thread` の
  `messages[].message_id`・`thth who` の `they_replied_to_me` の `message_id` は非推奨で残す）。
- 返信の台帳（`thth replies --json`）は返信自身の id を `reply_id` に（Threads の `id`・
  Bluesky／Mastodon の `message_id` を揃える）。台帳の `post_id` は「返信の付いた自分の投稿」の
  ままなので、返信の id には使わない。
"""
from __future__ import annotations

import json

from tests.test_thread_read import ROOT_URI, _cli, bsky_account  # noqa: F401 — fixture
from tests.test_who_cli import BOB_KEY, _seed
from thth import account_report, accounts, replies, who_cli


def test_threadのmessagesにpost_idを足しmessage_idも残す(bsky_account):  # noqa: F811
    account, service = bsky_account
    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    messages = json.loads(r.stdout)["messages"]
    assert messages and all(m["post_id"] == m["message_id"] for m in messages)


def test_whoのthey_replied_to_meにpost_idを足す(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)
    result = who_cli.answer(account_name=account["name"], author_key=BOB_KEY)
    reply = [t for t in result["threads"] if t["role"] == "they_replied_to_me"][0]
    assert reply["post_id"] == reply["message_id"] == "M-BOB-1"
    assert reply["root"] == "SELFPOST1"
    mine = [t for t in result["threads"] if t["role"] == "i_replied_to_them"]
    assert {t["post_id"] for t in mine} == {"P1", "P2"}


def test_repliesの行にreply_idを足しpost_idは根のまま(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)
    rows = replies.load(account["name"])["replies"]
    assert {r["reply_id"] for r in rows} == {"M-BOB-1", "M-CAROL-1"}
    assert {r["post_id"] for r in rows} == {"SELFPOST1"}
    assert all(r["message_id"] == r["reply_id"] for r in rows)


def test_repliesのThreadsの行はidからreply_id():
    assert replies._reply_id({"id": "179"}) == "179"
    assert replies._reply_id({"message_id": "m", "id": "x"}) == "m"
    assert replies._reply_id({}) is None


def test_postsのjsonにpost_idを足しidも残す(isolated_account_factory, monkeypatch):
    account = isolated_account_factory(media="threads")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    monkeypatch.setattr(account_report, "fetch_posts", lambda cfg, token, **kw: (
        [{"post_id": "111", "timestamp": "2026-10-12T00:00:00+09:00", "url": "https://x.example/1",
          "text": "本文", "topic": None}], None))
    result = account_report.recent_posts(account["name"])
    assert [(p["id"], p["post_id"]) for p in result["posts"]] == [("111", "111")]
    outside = account_report._remote_posts(accounts.load_account(account["name"]), None, set())
    assert [(p["id"], p["post_id"]) for p in outside["outside"]] == [("111", "111")]
