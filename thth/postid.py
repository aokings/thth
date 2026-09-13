"""`post_id` をファイル名にする（媒体で `post_id` の形が違う・T3・2026-09-13）。

**見つかり方**: Bluesky を配線して端から端を通したら、`state/<account>/sent/` の
書き込みが `FileNotFoundError` で落ちた。Bluesky の `post_id` は AT URI
（`at://did:plc:…/app.bsky.feed.post/<rkey>`）で、**`/` と `:` を含む**。
`sent.path_for()` も `collect` の台帳のパスも `f"{post_id}.ndjson"` と素朴に
繋いでいたので、途中の `/` がディレクトリの区切りとして効いてしまう。
`collect._safe_post_id()` は区切りを含む `post_id` を弾いていたので、
**Bluesky の投稿は 1 本も採取されない**（弾いたことは errors に出るが、
「そういう媒体だ」という理由は誰も知らない）。

**直し方**: パスに使うときだけ percent-encoding を通す（`quote(safe="")`）。

- **Threads の既存のファイル名は 1 文字も変わらない。** Threads の `post_id` は
  数字だけで、`quote()` が触らない文字（`A-Za-z0-9_.-~`）に収まっている。
  追記専用の台帳（`data/sns/insights/posts/*.ndjson`・`data/sns/replies/*.ndjson`）を
  改名せずに済むのが、この綴りを選んだ理由。
- **戻せる。** ファイル名から `post_id` を復元する側（`measured`・
  `account_report`）は `unquote()` を通す。数字だけの古い名前は素通りする。

**ここで弾くのは「ファイル名にできない値」だけ**で、媒体ごとの `post_id` の形は
知らない（`at://` も数字も同じ扱い）。
"""
from __future__ import annotations

import urllib.parse


def is_usable(post_id) -> bool:
    """`post_id` を台帳の鍵として使えるか。**外へ出る値を弾く。**

    区切り文字（`/`）はもう弾かない——**encode すれば安全に書ける**し、
    弾くと Bluesky の投稿が丸ごと採取から落ちる。残っているのは
    「空」「`.`／`..`」「NUL」だけ。
    """
    if not isinstance(post_id, str) or not post_id.strip():
        return False
    if post_id.strip() in (".", ".."):
        return False
    return "\x00" not in post_id


def to_filename(post_id: str) -> str:
    """`post_id` 1 つぶんのファイル名（拡張子を除く）。

    `safe=""` なので `/` も `:` も `%2F`・`%3A` になる。`~` は `quote()` が
    触らないが、ファイル名として問題は無い。
    """
    return urllib.parse.quote(post_id, safe="")


def from_filename(name: str) -> str:
    """ファイル名（拡張子を除いたもの）から `post_id` を戻す。

    古い（encode 前の）名前は素通りする——`unquote()` は `%` を含まない文字列を
    そのまま返す。
    """
    return urllib.parse.unquote(name)
