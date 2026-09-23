"""3.1.1 件 5: `thth replies <account>` は、その account の投稿の返信だけを返す。

実測（依頼書）: kopicha の 3 口座（threads／bluesky／mastodon）は同じ repo を持ち、
返信の台帳 `data/sns/replies/<post_id>.ndjson` を共有する。`thth replies
kopicha-bluesky --json` が Threads の返信 32 行を返した。ここでは 1 つの repo を
3 台帳で共有し、各媒体の投稿の返信ファイルを置いて:
- 各 account の `replies` が自分の投稿の分だけを返す（queue・`sent/` の両方の母集団）。
- 年齢の窓は掛けない（古い投稿の返信も読める）。
- `--json` の各行に `account` と `medium`（行に無ければ account 側から補う）。
- 他 account のファイルは読まずに `counts.other_account_files` に数だけ残す。
- `--post` の指定は従前どおり。
"""
from __future__ import annotations

import argparse
import json
import os

import pytest

from thth import accounts, approval, cli, postid, sent

NAMES = {"kopicha-threads": "threads", "kopicha-bluesky": "bluesky", "kopicha-mastodon": "mastodon"}
POSTS = {"kopicha-threads": "18000000000000001",
         "kopicha-bluesky": "at://did:plc:synthetic/app.bsky.feed.post/3kabc",
         "kopicha-mastodon": "113000000000000001"}


def write_queue(queue_dir, name, account, post_id, posted_at):
    os.makedirs(queue_dir, exist_ok=True)
    with open(os.path.join(queue_dir, name), "w", encoding="utf-8") as stream:
        stream.write("---\nthth: 1\naccount: " + account + "\nstatus: posted\n"
                     "publish_at: " + posted_at + "\npost_id: " + post_id + "\n"
                     "posted_at: " + posted_at + "\n---\n\n本文\n")


def write_replies(replies_dir, post_id, rows):
    os.makedirs(replies_dir, exist_ok=True)
    with open(os.path.join(replies_dir, postid.to_filename(post_id) + ".ndjson"), "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture
def shared(isolated_account_factory):
    infos = {name: isolated_account_factory(name, project="kopicha", media=medium, handle="kopi_" + medium,
                                            instance="https://mastodon.social")
             for name, medium in NAMES.items()}
    repo = {info["repo_dir"] for info in infos.values()}
    assert len(repo) == 1, "3 台帳が同じ repo を持つ前提"
    info = next(iter(infos.values()))
    replies_dir = os.path.join(info["repo_dir"], "data", "sns", "replies")
    # Threads と Mastodon は queue（古い 2025 年の投稿＝窓の外）、Bluesky は sent/。
    write_queue(info["queue_dir"], "t.md", "kopicha-threads", POSTS["kopicha-threads"], "2025-01-01T09:00:00+09:00")
    write_queue(info["queue_dir"], "m.md", "kopicha-mastodon", POSTS["kopicha-mastodon"], "2025-01-02T09:00:00+09:00")
    sent.write(accounts.state_dir_for("kopicha-bluesky"), post_id=POSTS["kopicha-bluesky"], text="本文",
               body_hash=approval.compute_body_hash("本文"), sent_at="2026-09-20T09:00:00+09:00")
    # Threads の行は medium を持たない（採取の実際の形）。Mastodon の行は持つ。
    write_replies(replies_dir, POSTS["kopicha-threads"], [
        {"kind": "reply", "id": "T1", "username": "someone", "permalink": "https://www.threads.com/@someone/post/T1",
         "post_id": POSTS["kopicha-threads"], "collected_at": "2025-01-01T10:00:00+09:00"},
        {"kind": "reply", "id": "T2", "username": "other", "post_id": POSTS["kopicha-threads"],
         "collected_at": "2025-01-01T10:00:00+09:00"}])
    write_replies(replies_dir, POSTS["kopicha-bluesky"], [
        {"kind": "reply", "message_id": "B1", "username": "bsky.person", "medium": "bluesky",
         "post_id": POSTS["kopicha-bluesky"], "collected_at": "2026-09-20T10:00:00+09:00"}])
    write_replies(replies_dir, POSTS["kopicha-mastodon"], [
        {"kind": "reply", "message_id": "M1", "username": "alice", "medium": "mastodon",
         "post_id": POSTS["kopicha-mastodon"], "collected_at": "2025-01-02T10:00:00+09:00"}])
    # 誰の投稿の記録も無いファイル（どの account にも返さない）。
    write_replies(replies_dir, "19000000000000009", [
        {"kind": "reply", "id": "X9", "username": "stranger", "post_id": "19000000000000009"}])
    return infos


def run(name, capsys, post=None):
    rc = cli.cmd_replies(argparse.Namespace(account=name, post=post, json=True))
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def ids(result):
    return sorted(row.get("message_id") or row.get("id") for row in result["replies"])


@pytest.mark.parametrize("name,expected", [("kopicha-threads", ["T1", "T2"]),
                                           ("kopicha-bluesky", ["B1"]),
                                           ("kopicha-mastodon", ["M1"])])
def test_each_account_gets_only_its_own_posts_replies(shared, capsys, name, expected):
    result = run(name, capsys)
    assert ids(result) == expected
    assert {row["post_id"] for row in result["replies"]} == {POSTS[name]}
    assert all(row["account"] == name and row["medium"] == NAMES[name] for row in result["replies"])
    # 置き場の 4 本のうち、自分の 1 本だけを読み、残り 3 本は数だけ。
    assert result["counts"]["other_account_files"] == 3 and result["counts"]["replies"] == len(expected)
    assert result["population_errors"] == 0


def test_post_option_is_unchanged(shared, capsys):
    result = run("kopicha-bluesky", capsys, post=POSTS["kopicha-threads"])
    assert ids(result) == ["T1", "T2"] and result["counts"]["other_account_files"] == 0
    # 名指しされたのは他 account の投稿なので、account／medium を自分の値で補わない。
    assert all("account" not in row and "medium" not in row for row in result["replies"])
