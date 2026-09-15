"""**偽の検索できる媒体**（設計 v2 §4.4 の受け入れ・**tests の中だけ**）。

本物の API を 1 つも叩かずに `thth topics <account> --search <語>` の一覧を試す。
確かめたいのは 3 つ:

- 一覧が **post_id・permalink・投稿ごとの返信・「もう返した」印**を指せる。
- **返信の数を返さない媒体**（`has_replies` すら無い）で `—` / `null` になる
  （**`n/a` を 0 と混ぜない**）。
- 本文は画面の先頭 60 字だけで、**ディスクに 1 バイトも落ちない**。

`REGISTRY` に 1 行足すだけで通る（境界の証明でもある・§4.2）。
"""
from __future__ import annotations

from thth.adapters import base

MEDIUM = "searchfake"

# **本文はここにしか無い**——テストはこの綴りがディスクに無いことを走査で確かめる。
TEXT_A = "偽の検索本文その一・WKPL3A"
TEXT_B = "偽の検索本文その二・WKPL3B"
TEXT_LONG = "長" * 80 + "・WKPL3C"

# `has_replies` を返す媒体（Threads と同じ形。**数は返らない**）。
ROWS_HAS_REPLIES = [
    {"message_id": "POST7QXA1", "username": "alice", "text": TEXT_A,
     "timestamp": "2026-09-13T10:00:00+0000", "permalink": "https://fake/POST7QXA1",
     "medium": MEDIUM, "author_key": base.author_key(MEDIUM, "alice"),
     "is_reply": False, "has_replies": True, "topic_tag": "お茶"},
    {"message_id": "POST7QXA2", "username": "bob", "text": TEXT_B,
     "timestamp": "2026-09-14T01:00:00+0000", "permalink": "https://fake/POST7QXA2",
     "medium": MEDIUM, "author_key": base.author_key(MEDIUM, "bob"),
     "is_reply": False, "has_replies": False, "topic_tag": None},
    {"message_id": "POST7QXA3", "username": "carol", "text": TEXT_LONG,
     "timestamp": "2026-09-12T00:00:00+0000", "permalink": "https://fake/POST7QXA3",
     "medium": MEDIUM, "author_key": base.author_key(MEDIUM, "carol"),
     "is_reply": False, "has_replies": True, "topic_tag": "お茶"},
]

# **返信のことを何も返さない媒体**（数も真偽も無い）。`—` / `null` になる。
ROWS_SILENT = [
    {"message_id": "POST7QXB1", "username": "alice", "text": TEXT_A,
     "timestamp": "2026-09-13T10:00:00+0000", "permalink": "https://fake/POST7QXB1",
     "medium": MEDIUM, "author_key": base.author_key(MEDIUM, "alice")},
]

# **数を返す媒体**（`replies` に整数）。数があればその数を出す。
ROWS_WITH_COUNT = [
    {"message_id": "POST7QXC1", "username": "alice", "text": TEXT_A,
     "timestamp": "2026-09-13T10:00:00+0000", "permalink": "https://fake/POST7QXC1",
     "medium": MEDIUM, "author_key": base.author_key(MEDIUM, "alice"),
     "replies": 12, "has_replies": True},
]


class FakeSearchAdapter(base.Adapter):
    """`keyword_search` を持つ媒体。**投稿はしない**（この試験に要らない）。"""

    CAPABILITIES = frozenset({"keyword_search"})
    # `.token` を置かずに試せるようにする（在るかどうかだけを見る口なので）。
    TOKEN_KEYS: tuple = ()

    rows: list = ROWS_HAS_REPLIES
    calls: list = []

    def __init__(self, *, rows=None):
        self.rows = list(rows if rows is not None else type(self).rows)

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        return cls()

    def keyword_search(self, q: str, *, search_type: str = "TOP", limit: int = 25) -> list:
        type(self).calls.append((q, search_type, limit))
        return [dict(r) for r in self.rows]

    def whoami(self) -> dict:
        return {"user_id": "searchfake-1", "username": "searchfake"}
