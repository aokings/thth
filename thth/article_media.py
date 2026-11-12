"""記事の出し先（主体を通す口座・配信の台帳の道）を問う 1 か所。

以前は core・cli・events_cli・dashboard・doctor などが `media == "hatenablog"` と媒体名で分岐していた
（30 か所ほど）。記事の出し先を足すたびにそれを全部触ることになるので、問いをここに寄せた:

- `is_article_account(cfg)`: 記事の口座か（配信原稿を承認して timer が出す道・SNS の送信や採取は通らない）。
- `is_article_media(media)`: 媒体名で同じことを問う（原稿や表の行が `medium` だけを持つとき）。
- `has(cfg_or_media, 能力)`: その出し先だけの機能があるか。はてなの反応の採取（`reactions`）・
  はてなブックマーク（`bookmarks`）は、はてなブログの口座だけ。

**出し先を足すのは `ARTICLE_MEDIA` に 1 行だけ**（能力の集合を添える）。依存を持たない（どこからでも import できる）。
"""
from __future__ import annotations

# 媒体名 → その出し先の能力。
ARTICLE_MEDIA = {
    "hatenablog": frozenset({"reactions", "bookmarks"}),
}


def is_article_media(media) -> bool:
    return isinstance(media, str) and media in ARTICLE_MEDIA


def is_article_account(cfg) -> bool:
    return isinstance(cfg, dict) and is_article_media(cfg.get("media"))


def has(cfg_or_media, capability: str) -> bool:
    media = cfg_or_media.get("media") if isinstance(cfg_or_media, dict) else cfg_or_media
    return is_article_media(media) and capability in ARTICLE_MEDIA[media]
