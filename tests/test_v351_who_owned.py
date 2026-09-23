"""3.5.1 件 2: `thth who` は、この account の投稿の返信だけを「相手→自分」に数える。

原因（依頼書）: `who_cli` が返信の台帳を `owned_only` なしで読んでいた。台帳は同じ repo の
3 口座で共有なので他の媒体の返信行も読まれ、`author_key(media, username)` を**この account
の媒体で**計算するため、同じ username の人が他の媒体で返信していると混ざる。

ここでは 3.1.1 の共有 repo（threads／bluesky／mastodon）で、username `someone` の返信を
threads の投稿にだけ置く:
- `who kopicha-bluesky @someone` の「相手→自分」は 0（前は 1）。
- `who kopicha-threads @someone` は 1（自分の投稿への返信は従前どおり数える）。
- 分母に `replies_other_account_files`（読まなかった他 account のファイルの本数）。
"""
from __future__ import annotations

from thth import who_cli
from tests.test_v311_replies_owned import shared  # noqa: F401  (fixture)


def _from_them(node):
    return [t for t in node["threads"] if t["role"] == "they_replied_to_me"]


def test_他媒体の同じusernameの返信を混ぜない(shared):
    node = who_cli.answer(account_name="kopicha-bluesky", username="@someone")
    assert _from_them(node) == []
    assert node["met"] == 0
    # 置き場の 4 本のうち自分の投稿は 1 本。残り 3 本は読まずに数だけ残す。
    assert node["provenance"]["replies_other_account_files"] == 3
    assert node["provenance"]["replies_population_errors"] == 0


def test_自分の投稿への返信は従前どおり数える(shared):
    node = who_cli.answer(account_name="kopicha-threads", username="@someone")
    rows = _from_them(node)
    assert [row["message_id"] for row in rows] == ["T1"]
    assert node["provenance"]["replies_other_account_files"] == 3


def test_人向けの画面に分母が出る(shared, capsys):
    import argparse
    rc = who_cli.cmd_who(argparse.Namespace(targets=["kopicha-bluesky", "@someone"], project=None,
                                           profile=False, json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "（→ 0・← 0）" in out
    assert "他 account の投稿のファイル 3 本は読んでいません" in out
