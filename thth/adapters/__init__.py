"""媒体の台帳（`REGISTRY`）と組み立て（`make_adapter`）。設計 v2 §4.2。

**core はここだけを呼ぶ。** 媒体名で分岐するコードを 1 か所に集める——以前は
`core._default_adapter_factory()` が `media != "threads"` を弾いて
`ThreadsAdapter` を直に組み立てていたので、媒体を足すたびに core を触ることに
なっていた（設計 v2 §4.2「Threads 固有になっている 6 箇所」の 1 つめ）。

**知らない `media` は、知っている媒体の一覧を添えて loud に断る**（T-B0）。
黙って Threads として扱うと、別媒体のつもりで書いた queue が Threads に出る。
"""
from __future__ import annotations

from . import base
from .bluesky import BlueskyAdapter
from .mastodon import MastodonAdapter
from .threads import ThreadsAdapter

# 媒体名 → アダプタのクラス。**足すのはここに 1 行だけ**——`core`・`select`・
# `collect`・`doctor`・`token set` は触らなくてよい（それがこの境界の目的）。
# Bluesky・Mastodon を足したときに core 側で変わったのは、境界の語彙で言い換えた
# 4 か所だけ（`account_insights` の capability・`doctor` の `TOKEN_KEYS`・
# `thth auth` の媒体分岐・`token set` の `TOKEN_NO_EXPIRY`）で、**媒体名で分岐する
# コードは 1 行も増えていない。**
REGISTRY = {
    "threads": ThreadsAdapter,
    "bluesky": BlueskyAdapter,
    "mastodon": MastodonAdapter,
}

UnknownMedium = base.UnknownMedium


def known_media() -> list:
    """知っている媒体の名前（断り文にそのまま出す）。"""
    return sorted(REGISTRY)


def adapter_class(media):
    """`media` に対応するクラス。**知らなければ loud に断る**（T-B0）。"""
    cls = REGISTRY.get(media) if isinstance(media, str) else None
    if cls is None:
        raise UnknownMedium(
            f"media={media!r} を知りません。"
            f"知っている媒体: {'・'.join(known_media())}。"
            f"台帳の media を直すか、その媒体のアダプタを thth/adapters/ に足して "
            f"thth/adapters/__init__.py の REGISTRY に登録してください")
    return cls


def capabilities_for(media) -> set:
    """台帳の `media` から能力を引く。**実体（トークン）は要らない。**

    `select` が「トピック検査をするか」を決めるのに使う（設計 v2 §4.2・受け入れ 6）。
    **知らない媒体は空集合**——ここで断ると、`thth board` のような読むだけの口が
    台帳 1 本の誤字で丸ごと止まる。**公開の経路（`make_adapter`）は loud に断る**
    ので、知らない媒体で投稿が出ることはない。
    """
    cls = REGISTRY.get(media) if isinstance(media, str) else None
    return set(cls.CAPABILITIES) if cls is not None else set()


def count_text_for(media, text: str) -> int:
    """台帳の `media` の数え方で本文を数える。**実体（トークン）は要らない。**

    `capabilities_for()` と同じ筋。**知らない媒体は既定の数え方**（`base.Adapter`
    ＝ Threads の数え方）——`queuefile.limit_for()` が知らない媒体に
    `DEFAULT_MEDIA_LIMIT` を返すのと揃える。読むだけの口をここで断ると、
    台帳 1 本の誤字で `thth lint` が丸ごと止まる。
    """
    cls = REGISTRY.get(media) if isinstance(media, str) else None
    return (cls or base.Adapter).count_text(text)


def make_adapter(account_cfg: dict, token: dict | None):
    """台帳とトークンからアダプタを 1 つ作る（**core の唯一の入口**）。"""
    media = (account_cfg or {}).get("media")
    cls = adapter_class(media)
    return cls.from_account(account_cfg or {}, token or {})


__all__ = ["REGISTRY", "UnknownMedium", "adapter_class", "base",
           "capabilities_for", "count_text_for", "known_media", "make_adapter",
           "BlueskyAdapter", "MastodonAdapter", "ThreadsAdapter"]
