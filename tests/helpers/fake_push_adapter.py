"""**偽の push 型アダプタ**（設計 v2 §4.2 の受け入れ T-B1・**tests の中だけ**）。

WhatsApp・LINE のような「登録した人への push」の媒体を、本物の API を一切
叩かずに真似る。確かめたいのは 2 つだけ:

- `REGISTRY` に 1 行足すだけで `collect` が `inbox()` を呼び、
  `data/sns/inbox/<YYYY-MM>.ndjson` に書く（**core を変えずに動く**）。
- `reply_deadline`（24 時間の会話窓）が行に残る。

**views を持たない媒体**でもあるので、T-B4（`views: null` の実測を
「媒体に views が無い」の理由で数える）にも使う。**本物の媒体を待たずに、
境界の側だけを試せる**ことがこの偽物の目的。
"""
from __future__ import annotations

from thth.adapters import base

MEDIUM = "pushfake"


class FakePushAdapter(base.Adapter):
    """`inbox` を持ち `views` を持たない媒体。**投稿はしない**（この試験に要らない）。"""

    # **`views` が無い**（Bluesky・Mastodon と同じ形）。`topic` も無い。
    CAPABILITIES = frozenset({"inbox"})

    # クラスに積んでおくと `from_account()` 経由でも同じ一覧を返せる。
    messages: list = []

    def __init__(self, *, messages=None):
        self.messages = list(messages if messages is not None
                             else type(self).messages)
        self.inbox_calls = 0

    @classmethod
    def from_account(cls, account_cfg: dict, token: dict):
        return cls()

    def inbox(self, *, since: str | None = None) -> list:
        self.inbox_calls += 1
        return list(self.messages)

    def insights(self, post_id: str) -> dict:
        # **views を入れない。** `available` に無い＝そもそも媒体に無い。
        return {"metrics": {"likes": 2, "replies": 1},
                "available": ["likes", "replies"]}

    def conversation(self, post_id: str, *, since: str | None = None) -> list:
        return []

    def account_insights(self, user_id, *, since, until) -> dict:
        # アカウント単位の日次は持たない（`collect` は空なら 1 行も書かない）。
        return {}

    def whoami(self) -> dict:
        return {"user_id": "pushfake-1", "username": "pushfake"}


def message(message_id: str, *, text: str = "問い合わせです",
            timestamp: str = "2026-09-13T09:00:00+09:00",
            reply_deadline: str | None = "2026-09-14T09:00:00+09:00",
            username: str = "customer") -> base.Message:
    """`inbox()` が返す 1 件。**利用者から始まった会話なので `root_post` は無い。**"""
    return base.Message(
        message_id=message_id, username=username, text=text, timestamp=timestamp,
        replied_to=None, root_post=None, medium=MEDIUM,
        author_key=base.author_key(MEDIUM, username),
        reply_deadline=reply_deadline)
