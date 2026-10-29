"""公開 URL の置き場（設計 MetaAPI 計画 B-1・B-2・段 2）。

Instagram の画像・動画は Meta が公開の URL を取りに来る（**L2**・計画 B-2）。Threads が使う relay
（`media_relay.MediaRelay`・Worker の `/media/`）を Meta 向けに貸す。**どの媒体に貸すかは
`media_registry` の `media_fetch: pull` で決める**（媒体名の表を増やさない）。

**この段では配管に繋がない。** `media_delivery` は今も Threads にだけ relay を渡す
（`tests/test_media_registry.py` の「取りに来るのは relay を渡す媒体だけ」が縛る）。段 3 で
Instagram を `media_fetch: pull` で registry に足すとき、`media_delivery` の分岐をここの
`relay_for()` に置き換える。Facebook は multipart で直に送る（`push`・今のまま）。
"""
from __future__ import annotations

from .. import media_registry

# relay の操作者の名（`media_delivery` が Threads に渡しているのと同じ）。
OPERATOR = "operator"


def pulls(medium) -> bool:
    """媒体が公開の URL を取りに来るか（registry の `media_fetch`）。知らない媒体は False。"""
    row = media_registry.MEDIA.get(medium) if isinstance(medium, str) else None
    return row is not None and row.media_fetch == "pull"


def relay_for(cfg: dict, *, has_items: bool):
    """取りに来る媒体で添付があるときだけ relay を貸す（無ければ None）。"""
    if not has_items or not pulls((cfg or {}).get("media")):
        return None
    from ..media_relay import MediaRelay
    return MediaRelay(cfg["account"], OPERATOR)
