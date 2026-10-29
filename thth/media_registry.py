"""媒体の宣言の 1 か所（10/29 段 0・設計 MetaAPI 計画 B-3 の (c)）。

**なぜ要るか。** `adapters.REGISTRY` の注は「足すのはここに 1 行だけ」と言うが、それは投稿と採取の配管の
話で、実際には柵・添付・画面・シナリオの表を 15 か所ほど手で足す。facebook を足したとき（5.1.0）は
そのうち 7 か所が漏れ、漏れても試験が赤にならない所があった（scenario・dashboard・thread_read）。

**この模組は表を移さない。** 各表は今の場所に残し（運用と同じ repo を触るので大移動はしない）、ここには
「どの媒体があるか」と、その媒体の性質（表示名・family・添付の取り方・性質の台帳の有無・雛形の handle）
だけを書く。`tests/test_media_registry.py` が、**ここの媒体の集合と各表の媒体の集合が一致すること**
（載せない表は `NOT_LISTED` に理由を書く）を縛る。媒体を足すときは `MEDIA` に 1 行足し、試験が赤を
指した表を順に埋める。表を移すのは赤を指した所から、段ごとに少しずつ。

ここに無い表（媒体名でなく種類で分かれる・Threads だけの行）は試験の外: `core.py` の返信の見積もり・
topic の行（Threads だけ）・`approval.py` の任意項目の指紋（媒体ごとの項目は指紋の版の決まりで縛る）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Medium:
    # 画面の言葉（`dashboard_cli.MEDIUM_JA`・`scenario.MEDIUM_JA` と同じ）。
    display: str
    # 柵の表の道（`limits_policy.FAMILIES`）。SNS の adapter はどれも `sns`。
    family: str
    # 添付の取り方: `push`（THTH が媒体へ送る・multipart や upload）／`pull`（媒体が公開の URL を
    # 取りに来る・Threads は relay `media_relay.py` を使う）。
    media_fetch: str
    # 媒体の性質の台帳（`thth/media_traits/<媒体>.json`）を持つか。持てば柵の表の根拠に結ぶ。
    traits: bool
    # 口座の雛形（`accounts.example/<媒体>.json`）の handle。雛形のまま認可に進ませない印。
    # None は雛形の handle を「ダミー」と呼ばない（`NOT_LISTED` に理由）。
    default_handle: str | None


MEDIA: dict[str, Medium] = {
    "threads": Medium("Threads", "sns", "pull", True, "demo"),
    "bluesky": Medium("Bluesky", "sns", "push", True, "demo.bsky.social"),
    "mastodon": Medium("Mastodon", "sns", "push", True, "demo"),
    "x": Medium("X", "sns", "push", True, None),
    "facebook": Medium("Facebook", "sns", "push", True, "0"),
    # 段 3（10/29）: Meta が公開の URL を取りに来る（入れ物の 2 段・Threads の relay を貸す）。
    "instagram": Medium("Instagram", "sns", "pull", True, "0"),
}

# 投稿先の adapter を持たない、表に並ぶ名前（はてな・サイト）。媒体の集合の比較から外す。
NON_ADAPTER_NAMES = frozenset({"hatenablog", "hatena_bookmark", "hatenabookmark", "fotolife", "site"})

# (表の名前, 媒体) → 載せない理由。**理由の無い欠けは試験が赤にする。** 載せたら、ここから消す
# （消し忘れも試験が赤にする）。表の名前は `tests/test_media_registry.py` の `TABLES` の鍵。
NOT_LISTED: dict[tuple[str, str], str] = {
    ("accounts.DUMMY_HANDLES", "x"):
        "X の口座は `thth account add` が --handle を必須にするので、雛形の handle が残らない",
    **{("thread_read.PERMISSION_BY_MEDIUM", medium):
       "枝の読み取りに権限の名前が無い（公開の読み取り・provenance.permission は None）"
       for medium in ("bluesky", "mastodon", "x")},
}


def names() -> frozenset:
    return frozenset(MEDIA)


def not_listed(table: str) -> frozenset:
    """その表に載せない媒体（理由は `NOT_LISTED`）。"""
    return frozenset(medium for (name, medium) in NOT_LISTED if name == table)
