"""C-1（監査 P2-2）: 公開でない status を読み取りから落とす・出す側も拒む。

**破れていたもの**: `MastodonAdapter.recent_posts()`・`conversation()`（→
`_message()`）は status の `visibility` を見ずに本文を返していた。`visibility:
direct`（DM）も通常の投稿・返信として台帳へ流れ、README.en の「Never reads
direct messages. It only ever touches public posts and public replies」を
コードが保証していなかった。出す側（`__init__`）も `private`・`direct` を
そのまま許していた。

`tests/test_mastodon_adapter.py` の偽サーバ（`fake_mastodon`）をそのまま使う
——**新しいサーバは立てない**。`CONTEXT_FIXTURE`・`ACCOUNT_STATUSES_FIXTURE`
（同モジュールのグローバル）を `monkeypatch` で 5 行
（public・unlisted・private・direct・visibility 無し）に差し替えるだけで、
`_Handler` の実装は一切変えずに済む。
"""
from __future__ import annotations

import pytest

from thth.adapters import mastodon as mastodon_mod
from tests import test_mastodon_adapter as fixture_mod

ROOT_ID = fixture_mod.ROOT_ID
fake_mastodon = fixture_mod.fake_mastodon
_adapter = fixture_mod._adapter

# 5 件: public・unlisted・private・direct・visibility 無し（無いものも fail-closed
# の対象——「無ければ通す」ではなく「無ければ落とす」）。本文に「ひみつ」を混ぜ、
# 読み取りから落ちたことを**文字列で**確かめられるようにする。
FIVE_ROWS_CONTEXT = {
    "ancestors": [],
    "descendants": [
        {"id": "1", "created_at": "2026-09-13T01:00:00.000Z", "in_reply_to_id": ROOT_ID,
         "content": "<p>こうかい</p>", "account": {"acct": "pub@x"}, "visibility": "public"},
        {"id": "2", "created_at": "2026-09-13T01:01:00.000Z", "in_reply_to_id": ROOT_ID,
         "content": "<p>みしゅうさい</p>", "account": {"acct": "unl@x"}, "visibility": "unlisted"},
        {"id": "3", "created_at": "2026-09-13T01:02:00.000Z", "in_reply_to_id": ROOT_ID,
         "content": "<p>ひみつ(フォロワー限定)</p>", "account": {"acct": "priv@x"},
         "visibility": "private"},
        {"id": "4", "created_at": "2026-09-13T01:03:00.000Z", "in_reply_to_id": ROOT_ID,
         "content": "<p>ひみつ(DM)</p>", "account": {"acct": "dm@x"}, "visibility": "direct"},
        {"id": "5", "created_at": "2026-09-13T01:04:00.000Z", "in_reply_to_id": ROOT_ID,
         "content": "<p>ひみつ(visibility無し)</p>", "account": {"acct": "none@x"}},
    ],
}

FIVE_ROWS_STATUSES = [
    {"id": "1", "created_at": "2026-09-13T01:00:00.000Z", "content": "<p>こうかい</p>",
     "url": "https://example.invalid/1", "visibility": "public"},
    {"id": "2", "created_at": "2026-09-13T01:01:00.000Z", "content": "<p>みしゅうさい</p>",
     "url": "https://example.invalid/2", "visibility": "unlisted"},
    {"id": "3", "created_at": "2026-09-13T01:02:00.000Z", "content": "<p>ひみつ(フォロワー限定)</p>",
     "url": "https://example.invalid/3", "visibility": "private"},
    {"id": "4", "created_at": "2026-09-13T01:03:00.000Z", "content": "<p>ひみつ(DM)</p>",
     "url": "https://example.invalid/4", "visibility": "direct"},
    {"id": "5", "created_at": "2026-09-13T01:04:00.000Z", "content": "<p>ひみつ(visibility無し)</p>",
     "url": "https://example.invalid/5"},
]


FIVE_ROWS_SEARCH_STATUSES = [
    {"id": "1", "created_at": "2026-09-13T01:00:00.000Z", "content": "<p>こうかい</p>",
     "account": {"acct": "pub@x"}, "visibility": "public"},
    {"id": "2", "created_at": "2026-09-13T01:01:00.000Z", "content": "<p>みしゅうさい</p>",
     "account": {"acct": "unl@x"}, "visibility": "unlisted"},
    {"id": "3", "created_at": "2026-09-13T01:02:00.000Z", "content": "<p>ひみつ(フォロワー限定)</p>",
     "account": {"acct": "priv@x"}, "visibility": "private"},
    {"id": "4", "created_at": "2026-09-13T01:03:00.000Z", "content": "<p>ひみつ(DM)</p>",
     "account": {"acct": "dm@x"}, "visibility": "direct"},
    {"id": "5", "created_at": "2026-09-13T01:04:00.000Z", "content": "<p>ひみつ(visibility無し)</p>",
     "account": {"acct": "none@x"}},
]


def test_keyword_searchは公開とunlistedだけ返す(monkeypatch):
    """T2-1: `keyword_search()` も他の読む口と同じ C-1 の規律（fail-closed）。"""
    monkeypatch.setattr(fixture_mod, "SEARCH_STATUSES_FIXTURE", FIVE_ROWS_SEARCH_STATUSES)
    with fake_mastodon() as fake:
        rows = _adapter(fake).keyword_search("語")
    assert [r["message_id"] for r in rows] == ["1", "2"]
    assert "ひみつ" not in " ".join(r["text"] for r in rows)


def test_conversationは公開とunlistedだけ返す(monkeypatch):
    monkeypatch.setattr(fixture_mod, "CONTEXT_FIXTURE", FIVE_ROWS_CONTEXT)
    with fake_mastodon() as fake:
        messages = _adapter(fake).conversation(ROOT_ID)
    assert [m["message_id"] for m in messages] == ["1", "2"]
    # private・direct・visibility 無しの本文が出力に**含まれない**
    assert "ひみつ" not in " ".join(m["text"] for m in messages)


def test_recent_postsは公開とunlistedだけ返す(monkeypatch):
    monkeypatch.setattr(fixture_mod, "ACCOUNT_STATUSES_FIXTURE", FIVE_ROWS_STATUSES)
    with fake_mastodon() as fake:
        rows = _adapter(fake, account_id="9000").recent_posts()
    assert [r["post_id"] for r in rows] == ["1", "2"]
    assert "ひみつ" not in " ".join(r["text"] for r in rows)


def test_initのvisibility_directはValueError():
    with pytest.raises(ValueError) as e:
        mastodon_mod.MastodonAdapter(instance="https://example.invalid", visibility="direct")
    assert "direct" in str(e.value)
    # **「未知の値」ではなく「公開しか出さない」で名指しに断る**（監査 P2-2）。
    # 「未知です」の汎用文言に落ちていないことまで確かめる——`private`・`direct`
    # は「知らない値」ではなく「知っていて拒む値」。
    assert "公開の投稿しか出しません" in str(e.value)


def test_initのvisibility_privateはValueError():
    with pytest.raises(ValueError) as e:
        mastodon_mod.MastodonAdapter(instance="https://example.invalid", visibility="private")
    assert "private" in str(e.value)
    assert "公開の投稿しか出しません" in str(e.value)
