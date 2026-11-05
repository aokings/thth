"""Page Mentioning（計画 Meta 全部盛り M4・機能 Page Mentioning）。

Facebook のページの投稿・コメントの本文に `@[ページの id]` と書くと、Meta がそのページへの言及に変える
（人への言及は返事の本文の `@[PSID]`）【一次 10/29・features-reference「Lets your app mention Facebook Pages in
posts on Pages it manages」】。THTH は本文を書き換えず、形だけを見る: `@[` のあとが数字の id と `]` でなければ
断る（打ち違いがそのまま文字として出るのを防ぐ）。数字の id がそのページに実在するかは Meta が決める
【本物でしか確かめられない】。

`thth send`（`core._send_locked`）が Facebook の口座のときに呼ぶ。断りの符丁は下の `REASONS`
（`thth/refusal_catalog_part5.py` の M4 の分）。
"""
from __future__ import annotations

import re

# `thth send` の断り（`ThrowResult.error`）。`share_to_instagram_unavailable` は `--share-to-instagram` の門。
REASONS = frozenset(("page_mention_invalid", "share_to_instagram_unavailable"))
MENTION_ID_MAX = 32
_OPEN = re.compile(r"@\[")
_GOOD = re.compile(r"@\[([0-9]{1,%d})\]" % MENTION_ID_MAX)


def ids(text: str) -> list:
    """本文の中の言及の id（出てくる順・重ねない）。"""
    return list(dict.fromkeys(_GOOD.findall(text or "")))


def problem(text: str) -> str | None:
    """形の違う `@[…]` があれば人向けの 1 行、無ければ None。"""
    value = text or ""
    for found in _OPEN.finditer(value):
        if not _GOOD.match(value, found.start()):
            tail = value[found.start():found.start() + 24]
            return (f"page_mention_invalid: ページへの言及は @[数字の id] の形です（{tail!r}）。"
                    "言及でなければ @ と [ の間に空白を入れてください")
    return None
