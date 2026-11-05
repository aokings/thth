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


# `write:favourites` は 3.15.0 で足した（いいね・`thth like`）。それより前に認可した
# トークンには無いので、いいねは `scope_missing` で断り `thth auth` を案内する
# （再投稿は `write:statuses` で足りる）。client の世代は scope 集合ごとに別ファイル
# （`auth_mastodon.PREVIOUS_SCOPES` が 1 つ前の世代を読む）。
MASTODON_SCOPES = ['read:accounts', 'read:statuses', 'read:search',
                   'read:notifications', 'write:statuses', 'write:media',
                   'write:favourites']


# 未承認の権限（計画 Meta 全部盛り申請 §1 の表・判断 ①②③⑤ は入れる）。承認までは旗で閉じる（masaru 11/05）:
# 既定の招待には足さず、録画と審査の招待（`thth admin invite create --review-scopes`）だけが求める。
# 未承認の権限を外の人に求めると、認可そのものが通らないことがある。
# 構成（`config_id`）があるときは構成の側の一覧が効く。
THREADS_REVIEW_SCOPES = ("threads_share_to_instagram",)
META_INVITE_EXTRA = {
    "facebook": ("pages_manage_ads", "pages_manage_cta", "publish_video", "pages_messaging",
                 "pages_utility_messaging"),
    "instagram": ("instagram_manage_engagement", "instagram_manage_messages", "pages_read_user_content",
                  "threads_business_basic", "instagram_manage_upcoming_events"),
    "ads": ("catalog_management", "leads_retrieval", "ads_mcp_management", "pages_manage_ads", "pages_show_list",
            "read_audience_network_insights", "commerce_account_read_settings"),
}


def meta_invite_scopes(media, review=False):
    """招待の口座が持つはずの権限（`facebook`・`instagram`・`ads`）。doctor の突き合わせの相手もこれ。"""
    from .adapters import facebook, instagram
    from .ads import auth as ads_auth
    base = {"facebook": facebook.SCOPES, "instagram": instagram.SCOPES, "ads": ads_auth.SCOPES}[media]
    return [*base, *META_INVITE_EXTRA[media]] if review else list(base)


def mastodon_guidance(account):
    return ('Mastodon に必要な scope: ' + ' '.join(MASTODON_SCOPES) +
            f'。まず thth auth {account} --by … で再認可してください。手入力の逃げ道は thth token set {account} --stdin --by …（管理者が発行した access token）です')
