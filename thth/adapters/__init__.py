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
from .threads import ThreadsAdapter

# 媒体名 → アダプタのクラス。**Bluesky・Mastodon は別 Track（T1・T2）で足す。**
# 足すときはここに 1 行入れるだけで、`core`・`select`・`collect`・`doctor`・
# `token set` は触らなくてよい（それがこの境界の目的）。
REGISTRY = {
    "threads": ThreadsAdapter,
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


def make_adapter(account_cfg: dict, token: dict | None):
    """台帳とトークンからアダプタを 1 つ作る（**core の唯一の入口**）。"""
    media = (account_cfg or {}).get("media")
    cls = adapter_class(media)
    return cls.from_account(account_cfg or {}, token or {})


__all__ = ["REGISTRY", "UnknownMedium", "adapter_class", "base",
           "capabilities_for", "known_media", "make_adapter", "ThreadsAdapter"]
