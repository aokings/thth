"""Threads OAuth の既定 scope（設計 §2.2「権限は例外なく全部取る」・裁定 2026-09-09）。

masaru が編集しやすいよう、ハードコードはここ 1 か所にまとめる。個別のアカウントで
違う scope が要るときは `accounts/<account>.json` に `"scopes": [...]` を書けば
`thth auth` はそちらを優先する（`thth/auth.py` ではなく `thth/oauth.py` 側で
`account_cfg.get("scopes") or DEFAULT_SCOPES` として参照する）。
"""
from __future__ import annotations

DEFAULT_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_delete",
    "threads_keyword_search",
    "threads_location_tagging",
    "threads_manage_insights",
    "threads_manage_mentions",
    "threads_manage_replies",
    "threads_profile_discovery",
    "threads_read_replies",
    "threads_share_to_instagram",
]


MASTODON_SCOPES = ['read:accounts', 'read:statuses', 'read:search',
                   'read:notifications', 'write:statuses']


def mastodon_guidance(account):
    return ('Mastodon に必要な scope: ' + ' '.join(MASTODON_SCOPES) +
            f'。まず thth auth {account} --by … で再認可してください。手入力の逃げ道は thth token set {account} --stdin --by …（管理者が発行した access token）です')
