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
# 審査の招待（`--review-scopes`）で、口座の基の権限（各媒体の `SCOPES`）に足す分。今の申請の一覧
# （`appreview.names()`・11/28 に Meta の下書きと 53 件で一致）に合わせる（試験 `tests/test_invite_extra_matches_review.py`）。
# VM に構成（`META_LOGIN_CONFIG_ID`）があるときは、認可の画面が求める権限は Dashboard の構成の一覧で決まり、この一覧は
# `scope` として Meta に送られない（`auth_facebook.FacebookInviteAuthProfile.for_invite`）。それでも Worker の招待の
# ページにはこの一覧がそのまま載り、`thth doctor` が鍵の権限と突き合わせる相手になる。構成の無い所では `scope` として送る。
# 申請から外した `pages_manage_cta`・`read_audience_network_insights`・`commerce_account_read_settings`
# （`appreview.DEFERRED`）は 11/28 に外した。
META_INVITE_EXTRA = {
    "facebook": ("pages_manage_ads", "publish_video", "pages_messaging", "pages_utility_messaging"),
    "instagram": ("instagram_manage_engagement", "instagram_manage_messages", "pages_read_user_content",
                  "threads_business_basic", "instagram_manage_upcoming_events", "instagram_manage_events"),
    "ads": ("catalog_management", "leads_retrieval", "ads_mcp_management", "pages_manage_ads", "pages_show_list"),
}

# 申請から外したが、招待でまだ求める権限（名前 → 理由）。`appreview.DEFERRED` にある物だけ（試験が縛る）。
META_INVITE_BEYOND_REVIEW = {
    "pages_utility_messaging": "申請からは外した（口 `thth dm send --template` が無い・`appreview.DEFERRED`）が、"
                               "鍵に乗っていて害が無く、口を作れば申請に戻せるので pages_messaging と一緒に求め続ける"
                               "（docs/一覧_Meta申請の録画までに要る物_2026-11-23.md §2 B4 の推し）",
}

# 申請に入れたが、招待の一覧には足さない権限（名前 → 理由）。構成（`config_id`）のある VM では、
# 構成の一覧に入っていれば認可で求められる（ここに書くのは doctor の突き合わせと Worker の頁に出ないということ）。
_WORKER_SHAPE = ("Worker の招待の形の検査（`callback/src/invite-object.js` の META_SCOPE）が "
                 "`pages|instagram|ads|threads|leads|catalog|commerce|business|publish|read|public` の頭だけを通し、")
_WORKER_COUNT = ("Worker の招待は 1 件に権限 16 個まで（`callback/src/invite-object.js` の "
                 "`body.scopes.length>16`）。instagram の招待は基の 9 個と足し分 6 個で 15 個になり、残りは 1 個")
META_INVITE_LEFT_OUT = {
    "email": _WORKER_SHAPE + "頭の無い `email` は断られる。Worker を直して deploy したあとなら足せる",
    "pages_events": "Dashboard の綴りは `page_events`（11/28 の下書き）。" + _WORKER_SHAPE +
                    "`page_` の頭は断られる。`pages_events` の綴りなら Worker は通すが、構成の無い所で `scope` として"
                    "送ると Meta の認可の画面が名前を断り、招待がまるごと通らない見込み。THTH の鍵の読みは両方の綴りを"
                    "受ける（`adapters/facebook.EVENT_PERMISSION_ALIASES`）。Worker を直してから `page_events` で足す",
    "facebook_branded_content_ads_brand": _WORKER_SHAPE + "`facebook_` の頭は断られる",
    "facebook_creator_marketplace_discovery": _WORKER_SHAPE + "`facebook_` の頭は断られる",
    "instagram_branded_content_brand": _WORKER_COUNT,
    "instagram_branded_content_creator": _WORKER_COUNT,
    "instagram_branded_content_ads_brand": _WORKER_COUNT,
    "instagram_creator_marketplace_discovery": _WORKER_COUNT,
    "instagram_shopping_tag_products": _WORKER_COUNT,
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
