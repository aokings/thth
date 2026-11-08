"""審査の台本と提出の前の点検（計画 Meta 全部盛り §4 の M5 の (d)(e)）。

- `thth admin appreview script`: 権限・機能 → THTH の口 → 審査員が打つ命令 → 録画の区間、の対応表（計画 §1 の形）。
  `--tsv` は `tools/appreview_rec.sh --segments` が読む区間の並び（`tools/appreview_segments.tsv` に置く・
  手で直さない・`tests/test_m5_appreview.py` が一致を見る）。
- `thth admin appreview check`: 提出の前に「30 日以内に成功の呼び出しが無い権限」「口の無い権限」「privacy に語の
  無い権限」を赤で出す（前向きな断りの 3 行）。全部通れば黙って rc 0。

**権限の正本は 1 か所**: 権限の名前・媒体・読む／書くは `thth/permission_watch.py` の `PERMISSIONS`（THTH の認可が
求める権限は全部そこに並ぶ・`thth/scopes.py` の一覧が入っていることは `tests/test_m5_permission_watch.py` が縛る）。
ここに書くのは、その表に無いものだけ:

- `SEGMENTS`: 録画の区間（撮る順）。区間が扱う権限・機能と、審査員が打つ命令。**口があるとは、どこかの区間に
  載っていること**（THTH の口は区間の命令から引く）。
- `PLANNED`: 計画 §1.10 の判断で申請に入れるが、THTH の認可がまだ求めない権限（口が無い・M0 で Dashboard を見て
  直す）。`PERMISSIONS` と重ねない（試験が縛る）。
- `FEATURES`: 機能（Feature・権限ではない）。
- `PRIVACY_WORDS`: privacy の頁にあるはずの語（英語の文・大文字小文字は見ない・全部の語が要る）。見立ての粗い柵で、
  M6 で privacy を直す者が語をそろえる。

命令の型の穴は `{TH}` のような 1 語だけ（`appreview_rec.sh` が撮る前に値を聞いて 1 つの引数として入れる）。
本物で確かめていない口（計画 §4 の「M4 で分かったこと」の【推】）の区間には `real_first` の印を付け、台本は
「本物で 1 回打ってから」撮る。読むだけ。網に出ない（check は `permission_watch.build()` の記録を読むだけ）。
"""
from __future__ import annotations

import datetime
import html
import importlib.util
import os
import re

from . import jst
from . import permission_watch as watch

CALL_DAYS = 30            # 「Calls must be made within 30 days of submitting for App Review」（計画 §3.2）
MEDIA_ORDER = ("threads", "facebook", "instagram", "ads", "whatsapp")
MEDIA_LABEL = {"threads": "Threads", "facebook": "Facebook ページ", "instagram": "Instagram", "ads": "広告",
               "whatsapp": "WhatsApp", "feature": "機能"}
# check の断り（`thth/refusal_catalog_part5.py` の末尾）。並びは出す順。
CHECK_REFUSALS = ("appreview_no_recent_call", "appreview_no_door", "appreview_privacy_missing")
PRIVACY_REFUSALS = ("appreview_privacy_unreadable",)
STEP_KINDS = ("prep", "hand", "off", "login", "cmd", "two", "twooff")
SLOT = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")

# 型の穴 → 撮る前に聞く文（`appreview_rec.sh` が tty に出す・既定のあるものは聞かない）。
SLOTS = {
    "TH": "Threads の口座名", "FB": "Facebook ページの口座名", "IG": "Instagram の口座名",
    "AD": "広告の台帳の名前", "BY": "記録に残す名前", "BODY": "投稿の本文",
    "BODY2": "Instagram のストーリーズに共有する Threads の投稿の本文（th-publish の本文と別の文）", "REPLY": "返信の本文",
    "REASON": "取り下げの理由", "TH_POST": "th-publish で出した Threads の投稿の id",
    "FB_POST": "fb-publish で出したページの投稿の id", "IG_POST": "ig-publish で出した Instagram の投稿の id",
    "AD_STORY": "宣伝するページの投稿の object_story_id（<ページの id>_<投稿の数字>・消していないもの）",
    "AD_PAGE": "リードのフォームのあるページの id（数字）", "FB_COMMENT": "ページのコメントの id",
    "IG_COMMENT": "Instagram のコメントの id", "IG_MEDIA": "いいねを付ける Instagram の投稿（media）の id",
    "IMAGE": "Instagram に出す画像（jpeg）のパス", "FB_PSID": "ページの受信箱の会話の id（thth dm list に出る）",
    "IG_PSID": "Instagram の受信箱の会話の id（thth dm list に出る）",
    "CTA": "行動ボタンの id（thth page cta show に出る）",
    "LIVE": "ライブの id（thth live create が返す）", "TITLE": "ライブの題", "WORD": "検索の語",
    "TAG": "ハッシュタグ（# なし）", "PLACE": "場所の語", "TH_URL": "Threads の投稿の URL",
    "FB_URL": "Facebook の投稿の URL", "THUSER": "Threads の公開の口座名（例 threads）",
    "IGUSER": "Instagram のビジネスの口座名（@ を付ける）", "FBPAGE": "Facebook のページ名（@ を付ける）",
    "MENTION": "ページへの言及（@[ページの id]）を含む本文", "EDITED": "直したあとの本文",
    "BUDGET": "通しの予算（円・止めた形で作るのでお金は動かない）", "END": "終了の日時（ISO 8601）",
    "CATALOG": "カタログの id（thth ads catalog list に出る）",
}


def _seg(sid, medium, banner, items, *steps, real_first=None, door=None):
    return {"id": sid, "medium": medium, "banner": banner, "items": tuple(items), "steps": tuple(steps),
            "real_first": real_first, "door": door}


_LOGIN_PREP = "招待の URL を運営者の端末から受け取り、ブラウザの app 窓（アドレス欄なし）で開いておく"
_OFF_SECRET = "完了ページの secret と鍵を録画の外で控え、画面から消す"

# 録画の区間（撮る順）。審査員の流れが媒体ごとに 1 本（Threads は録画 0 の `appreview_rec.sh` そのもの）と、
# 権限・機能ごとの短い区間（計画 §1.8）。
SEGMENTS = (
    # ---------------------------------------------------------------- Threads
    _seg("th-basic", "threads", "threads_basic — the account's own posts.", ["threads_basic"],
         ("cmd", "thth posts {TH} --limit 3")),
    _seg("th-publish", "threads", "threads_content_publish — publish a post from the command line.",
         ["threads_content_publish"],
         ("cmd", "thth send {TH} --text {BODY}"), ("hand", "スマホの Threads で投稿が見えたら（映して）Return")),
    _seg("th-reply", "threads", "threads_manage_replies — a reply, sent the same way.", ["threads_manage_replies"],
         ("cmd", "thth send {TH} --text {REPLY} --reply-to {TH_POST}"), ("hand", "スマホで投稿の下に返信が見えたら Return")),
    _seg("th-read-replies", "threads", "threads_read_replies — read the replies to the account's posts.",
         ["threads_read_replies"], ("cmd", "thth replies {TH} --refresh --limit 3")),
    _seg("th-insights", "threads", "threads_manage_insights — fetch and show the post metrics.",
         ["threads_manage_insights"], ("cmd", "thth collect {TH}"), ("cmd", "thth measured {TH} --limit 3")),
    _seg("th-search", "threads", "threads_keyword_search — search a topic word on request.",
         ["threads_keyword_search"], ("cmd", "thth where {TH} {WORD}")),
    _seg("th-mentions", "threads", "threads_manage_mentions — mentions of the account.", ["threads_manage_mentions"],
         ("cmd", "thth mentions {TH} --refresh --limit 3")),
    _seg("th-profile", "threads", "threads_profile_discovery — a public profile.", ["threads_profile_discovery"],
         ("cmd", "thth profile {TH} {THUSER}")),
    _seg("th-location", "threads", "threads_location_tagging — find a place to tag.", ["threads_location_tagging"],
         ("cmd", "thth location search {TH} {PLACE}")),
    _seg("th-share-ig", "threads", "threads_share_to_instagram — share a post to the linked Instagram stories.",
         ["threads_share_to_instagram"],
         # th-publish と同じ {BODY} だと、同じ口座の直近 30 日の同じ本文として duplicate_text で断られる（別の穴 {BODY2}）
         ("cmd", "thth send {TH} --text {BODY2} --share-to-instagram"),
         ("hand", "Instagram のストーリーズに出たのを（映して）見せたら Return")),
    _seg("th-embed", "threads", "Threads oEmbed Read — the embed HTML of a public post.", ["threads_oembed_read"],
         ("cmd", "thth embed {TH} {TH_URL}")),
    _seg("th-delete", "threads", "threads_delete — delete the post, then confirm in the Threads app.",
         ["threads_delete"],
         ("two", "thth retract {TH} {TH_POST} --reason {REASON} --by {BY}"),
         ("hand", "スマホで投稿が消えたのを見せたら Return")),
    # ---------------------------------------------------------------- Facebook ページと Instagram の審査員の流れ
    _seg("flow-meta", "facebook", "Reviewer flow — Facebook Login for Business, a Facebook Page.",
         ["pages_show_list", "business_management", "public_profile"],
         ("prep", _LOGIN_PREP + "（Facebook ページの招待）"),
         ("hand", "Authorize → Facebook ログイン for Business でテストのページを選び、権限の一覧を見せて"
                  "許可 → 完了ページが出たら Return"),
         ("off", _OFF_SECRET), ("login",),
         ("cmd", "thth doctor {FB}")),
    # ---------------------------------------------------------------- Facebook ページ
    _seg("fb-read", "facebook", "pages_read_engagement, read_insights — the Page's posts and metrics.",
         ["pages_read_engagement", "read_insights"],
         ("cmd", "thth posts {FB} --limit 3"), ("cmd", "thth collect {FB}"), ("cmd", "thth measured {FB} --limit 3")),
    _seg("fb-publish", "facebook", "pages_manage_posts — publish, edit and delete a Page post.",
         ["pages_manage_posts"],
         ("cmd", "thth send {FB} --text {BODY}"), ("hand", "ページに投稿が出たのを見せたら Return"),
         ("two", "thth edit {FB} {FB_POST} --text {EDITED} --by {BY}"),
         ("two", "thth retract {FB} {FB_POST} --reason {REASON} --by {BY}"),
         ("hand", "ページから消えたのを見せたら Return")),
    _seg("fb-mention", "facebook", "Page Mentioning — mention another Page in a Page post.", ["page_mentioning"],
         ("cmd", "thth send {FB} --text {MENTION}"), ("hand", "ページの投稿で言及が見えたら Return")),
    _seg("fb-comments", "facebook",
         "pages_read_user_content, Business Asset User Profile Access — comments and the commenter's name.",
         ["pages_read_user_content", "business_asset_user_profile_access"],
         ("prep", "別のテストの口座からページの投稿にコメントを 1 つ付けておく"),
         ("cmd", "thth replies {FB} --refresh --limit 3")),
    _seg("fb-engage", "facebook", "pages_manage_engagement — reply to a comment and hide it.",
         ["pages_manage_engagement"],
         ("cmd", "thth send {FB} --text {REPLY} --reply-to {FB_COMMENT}"),
         ("cmd", "thth reply hide {FB} {FB_COMMENT} --by {BY}"), ("hand", "ページで返事と隠したコメントを見せたら Return")),
    _seg("fb-webhook", "facebook", "pages_manage_metadata — subscribe the Page to webhooks.",
         ["pages_manage_metadata"],
         ("cmd", "thth webhook subscribe {FB} --by {BY}"),
         ("hand", "別のテストの口座からページにコメントを付けたら Return"), ("cmd", "thth webhook status")),
    _seg("fb-cta", "facebook", "pages_manage_cta — read and remove a call-to-action button.", ["pages_manage_cta"],
         ("prep", "録画用の行動ボタンをページの画面で 1 つ足しておく（API では付け直せない・計画 M4）"),
         ("cmd", "thth page cta show {FB}"), ("two", "thth page cta delete {FB} {CTA} --by {BY}"),
         ("hand", "ページの画面でボタンが消えたのを見せたら Return")),
    _seg("fb-live", "facebook", "publish_video, Live Video API — start and end a live video on the Page.",
         ["publish_video", "live_video_api"],
         ("prep", "ページのフォロワーが 100 人以上かを確かめる（2024-06-10 からの条件）。ffmpeg の配信の命令を用意する"),
         ("twooff", "thth live create {FB} --title {TITLE} --by {BY}"),
         ("off", "配信の宛先（stream_url は鍵を含む）を録画の外で ffmpeg に渡し、実配信を始める（止め絵・ループは禁止）"),
         ("hand", "ページでライブが見えたら Return"),
         ("two", "thth live end {FB} {LIVE} --by {BY}")),
    _seg("fb-dm", "facebook", "pages_messaging — read the Page inbox and answer within 24 hours.",
         ["pages_messaging"],
         ("prep", "別のテストの口座からページにメッセージを 1 通送っておく"),
         ("cmd", "thth dm list {FB}"), ("cmd", "thth dm read {FB} {FB_PSID}"),
         ("two", "thth dm send {FB} {FB_PSID} --text {REPLY} --by {BY}"),
         ("hand", "Messenger で返事が届いたのを見せたら Return"),
         real_first="messaging_type の綴りが【推】（計画 M4）"),
    _seg("fb-dm-profile", "facebook", "pages_user_locale, pages_user_timezone, pages_user_gender — the sender's "
         "profile fields, shown on screen only.", ["pages_user_locale", "pages_user_timezone", "pages_user_gender"],
         ("cmd", "thth dm read {FB} {FB_PSID} --profile")),
    _seg("fb-human-agent", "facebook", "Human Agent — a person answers after 24 hours with the human_agent tag.",
         ["human_agent"],
         ("prep", "24 時間より前に届いたメッセージを用意する"),
         ("two", "thth dm send {FB} {FB_PSID} --text {REPLY} --human-agent --by {BY}"),
         real_first="HUMAN_AGENT の札の綴りと窓の外の断りの符丁が【推】（計画 M4）"),
    _seg("fb-search", "facebook", "Page Public Content Access — search public Pages and their posts (counts only).",
         ["page_public_content_access"], ("cmd", "thth where {FB} {WORD}")),
    _seg("fb-who", "facebook", "Page Public Metadata Access — public metadata of another Page.",
         ["page_public_metadata_access"], ("cmd", "thth who {FB} {FBPAGE} --profile")),
    _seg("meta-embed", "facebook", "Meta oEmbed Read — the embed HTML of a public Facebook post.",
         ["meta_oembed_read"], ("cmd", "thth embed {FB} {FB_URL}")),
    # ---------------------------------------------------------------- Instagram
    # 審査員の招待は媒体ごとに 1 本・鍵は口座 1 つ（Worker が口座ごとの鍵の表で引く）。FB の鍵で `doctor {IG}` を打つと
    # 401 `invalid_key` なので、IG の区間の前に IG の招待で login をやり直す（計画 §4 の「遠くの道で分かったこと」）。
    _seg("flow-ig", "instagram", "Reviewer flow — an Instagram account, with the Threads account linked to it.",
         ["threads_business_basic"],
         ("prep", _LOGIN_PREP + "（Instagram の招待・Facebook ページの招待とは別の 1 本）"),
         ("hand", "Authorize → Facebook ログイン for Business で Instagram を選び、権限の一覧を見せて許可 → "
                  "完了ページが出たら Return"),
         ("off", _OFF_SECRET), ("login",),
         ("cmd", "thth doctor {IG}")),
    _seg("ig-basic", "instagram", "instagram_basic — the Instagram account's own media.", ["instagram_basic"],
         ("cmd", "thth posts {IG} --limit 3")),
    _seg("ig-publish", "instagram", "instagram_content_publish — publish an image.", ["instagram_content_publish"],
         ("cmd", "thth send {IG} --text {BODY} --media {IMAGE}"), ("hand", "Instagram で投稿が見えたら Return")),
    _seg("ig-comments", "instagram", "instagram_manage_comments — read, answer and hide a comment.",
         ["instagram_manage_comments"],
         ("prep", "別のテストの口座から Instagram の投稿にコメントを 1 つ付けておく"),
         ("cmd", "thth replies {IG} --refresh --limit 3"),
         ("cmd", "thth send {IG} --text {REPLY} --reply-to {IG_COMMENT}"),
         ("cmd", "thth reply hide {IG} {IG_COMMENT} --by {BY}")),
    _seg("ig-insights", "instagram", "instagram_manage_insights — metrics, and business discovery (counts only).",
         ["instagram_manage_insights"],
         ("cmd", "thth collect {IG}"), ("cmd", "thth measured {IG} --limit 3"),
         ("cmd", "thth who {IG} {IGUSER} --profile")),
    _seg("ig-like", "instagram", "instagram_manage_engagement — like a media object.", ["instagram_manage_engagement"],
         ("cmd", "thth like {IG} {IG_MEDIA} --by {BY}"), ("hand", "Instagram でいいねが付いたのを見せたら Return")),
    # ig-like の「本物で 1 回打ってから」は外した: 11/08 本物で POST /{ig-user-id}/likes が通った（計画 M4）。
    _seg("ig-dm", "instagram", "instagram_manage_messages — read the Instagram inbox and answer.",
         ["instagram_manage_messages"],
         ("prep", "別のテストの口座から Instagram に DM を 1 通送っておく"),
         ("cmd", "thth dm list {IG}"), ("two", "thth dm send {IG} {IG_PSID} --text {REPLY} --by {BY}"),
         ("hand", "Instagram で返事が届いたのを見せたら Return"),
         real_first="messaging_type の綴りが【推】（計画 M4）"),
    _seg("ig-hashtag", "instagram", "Instagram Public Content Access — hashtag search (counts only).",
         ["instagram_public_content_access"], ("cmd", "thth where {IG} {TAG}")),
    _seg("ig-upcoming", "instagram", "instagram_manage_upcoming_events — the account's upcoming events.",
         ["instagram_manage_upcoming_events"], ("cmd", "thth upcoming {IG}")),
    _seg("ig-delete", "instagram", "instagram_manage_contents — delete the media.", ["instagram_manage_contents"],
         ("two", "thth retract {IG} {IG_POST} --reason {REASON} --by {BY}"),
         ("hand", "Instagram から消えたのを見せたら Return")),
    # ---------------------------------------------------------------- 広告
    # 設計 広告の資格と遠くの道（2026-11-06）: 広告の招待も資格の行を作り、`thth login` の鍵が広告の台帳に結ばれる。
    # 鍵は口座 1 つなので、IG と同じく広告の区間の前に広告の招待で login をやり直す。審査員の台帳に結んだページは
    # 無いので、boost は --object-story-id、leads は --page を明示する。配信を始める口（activate）は遠くの道に無い。
    _seg("flow-ads", "ads", "Reviewer flow — an ad account, with Facebook Login for Business.", [],
         ("prep", _LOGIN_PREP + "（広告の招待・Facebook ページ・Instagram の招待とは別の 1 本）"),
         ("hand", "Authorize → Facebook ログイン for Business で広告アカウントを選び、権限の一覧を見せて許可 → "
                  "完了ページが出たら Return"),
         ("off", _OFF_SECRET), ("login",),
         ("cmd", "thth ads doctor {AD}")),
    _seg("ads-read", "ads", "ads_read — the ad account and its insights.", ["ads_read"],
         ("cmd", "thth ads show {AD}"), ("cmd", "thth ads insights {AD}")),
    _seg("ads-manage", "ads", "ads_management, pages_manage_ads — boost a Page post (created paused), then stop.",
         ["ads_management", "pages_manage_ads"],
         ("prep", "審査の間だけ運営者が VM の台帳で production と 1 操作の上限を上げておく（ads_enabled と月の上限は 0 のまま・"
                  "上げないと boost は ads_not_production で断られる）"),
         ("cmd", "thth ads boost {AD} --object-story-id {AD_STORY} --lifetime {BUDGET} --end {END} --by {BY}"),
         ("hand", "広告マネージャに止めた形で出たのを見せたら Return"),
         ("cmd", "thth ads stop {AD} --by {BY}")),
    _seg("ads-catalog", "ads", "catalog_management — the product catalogs and their items.", ["catalog_management"],
         ("cmd", "thth ads catalog list {AD}"), ("cmd", "thth ads catalog products {AD} --catalog {CATALOG}")),
    _seg("ads-leads", "ads", "leads_retrieval — leads of a lead form (shown on screen only).", ["leads_retrieval"],
         ("prep", "リード広告のテストのフォームに 1 件入れておく"), ("cmd", "thth ads leads {AD} --page {AD_PAGE}")),
    _seg("ads-network", "ads", "read_audience_network_insights — Audience Network results.",
         ["read_audience_network_insights"], ("cmd", "thth ads network {AD}")),
    _seg("ads-shop", "ads", "commerce_account_read_settings — the shop's settings.",
         ["commerce_account_read_settings"], ("cmd", "thth ads shop {AD}")),
    _seg("ads-mcp", "ads", "ads_mcp_management — connect the official ads MCP server with THTH's app.",
         ["ads_mcp_management"],
         ("hand", "Claude Code で claude mcp add --transport http --client-id <THTH の app id> meta-ads "
                  "https://mcp.facebook.com/ads を打ち、認可して報告の道具を 1 つ呼んだら Return"),
         door="claude mcp add（公式の広告 MCP に THTH のアプリで繋ぐ・計画 §1.5）"),
)

# 計画 §1.10 の判断で申請に入れるが、THTH の認可がまだ求めない権限（媒体）。口を作ったら `PERMISSIONS` へ移す。
PLANNED = {
    "email": "facebook", "pages_events": "facebook", "attribution_read": "ads",
    "facebook_branded_content_ads_brand": "facebook", "facebook_creator_marketplace_discovery": "facebook",
    "instagram_manage_events": "instagram", "instagram_branded_content_brand": "instagram",
    "instagram_branded_content_creator": "instagram", "instagram_branded_content_ads_brand": "instagram",
    "instagram_creator_marketplace_discovery": "instagram", "instagram_creator_marketplace_messaging": "instagram",
    "instagram_shopping_tag_products": "instagram",
    "commerce_account_manage_orders": "ads", "commerce_account_read_orders": "ads",
    "commerce_account_read_reports": "ads", "commerce_manage_accounts": "ads", "live_shopping_manage_video": "ads",
    "whatsapp_business_management": "whatsapp", "whatsapp_business_messaging": "whatsapp",
    "whatsapp_business_manage_events": "whatsapp", "manage_app_solution": "whatsapp",
}

# 機能（名前の鍵 → Meta の画面の名前・録画が要るか）。Full と Standard Access は録画の要らない別のボタン（計画 §1.5）。
FEATURES = {
    "threads_oembed_read": ("Threads oEmbed Read", True),
    "meta_oembed_read": ("Meta oEmbed Read", True),
    "live_video_api": ("Live Video API", True),
    "human_agent": ("Human Agent", True),
    "page_public_content_access": ("Page Public Content Access", True),
    "page_public_metadata_access": ("Page Public Metadata Access", True),
    "page_mentioning": ("Page Mentioning", True),
    "business_asset_user_profile_access": ("Business Asset User Profile Access", True),
    "instagram_public_content_access": ("Instagram Public Content Access", True),
    "marketing_api_access_tier": ("Marketing API Access Tier (Full)", False),
    "ads_management_standard_access": ("Ads Management Standard Access", False),
}
# 録画の要らない機能の口（区間に載らないので、ここで口を言う）。
FEATURE_DOORS = {"marketing_api_access_tier": "thth admin meta ledger（15 日の呼び出し数とエラー率）",
                 "ads_management_standard_access": "thth ads show"}

_TH, _FB, _IG, _AD, _MSG = "Threads:", "Facebook Pages:", "Instagram:", "Ad accounts:", "Messenger"
MEDIA_TAGS = (_TH, _FB, _IG, _AD, _MSG)   # 先頭にあれば、その媒体の項（段落か箇条）の中で残りの語を探す
# privacy の頁にあるはずの語（全部要る・大文字小文字は見ない）。媒体の語は今の privacy の「Threads: …」の項の形。
PRIVACY_WORDS = {
    "threads_basic": (_TH,), "threads_content_publish": (_TH, "publish"), "threads_delete": (_TH, "delet"),
    "threads_keyword_search": ("threads_keyword_search",), "threads_location_tagging": (_TH, "location"),
    "threads_manage_insights": (_TH, "metrics"), "threads_manage_mentions": (_TH, "mention"),
    "threads_manage_replies": (_TH, "repl"), "threads_profile_discovery": (_TH, "profile"),
    "threads_read_replies": (_TH, "repl"), "threads_share_to_instagram": (_TH, "Instagram stor"),
    "threads_business_basic": (_IG, "Threads account"),
    "pages_show_list": (_FB,), "business_management": (_FB, "business"), "public_profile": (_FB, "name"),
    "pages_read_engagement": (_FB, "metrics"), "read_insights": (_FB, "insights"),
    "pages_read_user_content": (_FB, "comment"), "pages_manage_engagement": (_FB, "hide"),
    "pages_manage_metadata": (_FB, "notification"), "pages_manage_posts": (_FB, "publish"),
    "pages_manage_cta": (_FB, "call-to-action"), "publish_video": (_FB, "live video"),
    "pages_manage_ads": (_AD, "boost"), "pages_messaging": (_MSG, "24 hours"),
    "pages_utility_messaging": (_MSG, "utility"), "pages_user_locale": (_MSG, "locale"),
    "pages_user_timezone": (_MSG, "time zone"), "pages_user_gender": (_MSG, "gender"),
    "instagram_basic": (_IG,), "instagram_content_publish": (_IG, "publish"),
    "instagram_manage_comments": (_IG, "comment"), "instagram_manage_insights": (_IG, "insights"),
    "instagram_manage_contents": (_IG, "delet"), "instagram_manage_engagement": (_IG, "like"),
    "instagram_manage_messages": (_IG, "direct message"), "instagram_manage_upcoming_events": (_IG, "event"),
    "ads_read": (_AD,), "ads_management": (_AD, "budget"), "catalog_management": (_AD, "catalog"),
    "leads_retrieval": (_AD, "lead"), "ads_mcp_management": (_AD, "MCP"),
    "read_audience_network_insights": (_AD, "Audience Network"), "commerce_account_read_settings": (_AD, "shop"),
    # PLANNED
    "email": ("email address",), "pages_events": (_FB, "event"), "attribution_read": (_AD, "attribution"),
    "facebook_branded_content_ads_brand": (_FB, "branded content"),
    "facebook_creator_marketplace_discovery": (_FB, "creator marketplace"),
    "instagram_manage_events": (_IG, "event"), "instagram_branded_content_brand": (_IG, "branded content"),
    "instagram_branded_content_creator": (_IG, "branded content"),
    "instagram_branded_content_ads_brand": (_IG, "branded content"),
    "instagram_creator_marketplace_discovery": (_IG, "creator marketplace"),
    "instagram_creator_marketplace_messaging": (_IG, "creator marketplace"),
    "instagram_shopping_tag_products": (_IG, "product tag"),
    "commerce_account_manage_orders": (_AD, "order"), "commerce_account_read_orders": (_AD, "order"),
    "commerce_account_read_reports": (_AD, "order"), "commerce_manage_accounts": (_AD, "shop"),
    "live_shopping_manage_video": (_AD, "live shopping"),
    "whatsapp_business_management": ("WhatsApp:",), "whatsapp_business_messaging": ("WhatsApp:", "message"),
    "whatsapp_business_manage_events": ("WhatsApp:", "event"), "manage_app_solution": ("WhatsApp:",),
    # FEATURES
    "threads_oembed_read": ("embed",), "meta_oembed_read": ("embed",), "live_video_api": (_FB, "live video"),
    "human_agent": (_MSG, "human agent"), "page_public_content_access": (_FB, "public content"),
    "page_public_metadata_access": (_FB, "public content"), "page_mentioning": (_FB, "mention"),
    "business_asset_user_profile_access": (_FB, "name"), "instagram_public_content_access": (_IG, "hashtag"),
    "marketing_api_access_tier": (), "ads_management_standard_access": (),
}


# ---------------------------------------------------------------- 表

def names() -> list:
    """申請に入れる名前（権限の正本 `PERMISSIONS` ＋ `PLANNED` ＋ 機能）。"""
    return [*watch.PERMISSIONS, *PLANNED, *FEATURES]


def _door_of(template) -> str:
    """命令の型から THTH の口（`thth ads catalog list` まで・穴と旗の前まで）。"""
    words = []
    for token in template.split():
        if token.startswith("-") or SLOT.fullmatch(token):
            break
        words.append(token)
    return " ".join(words)


def segment_commands(segment) -> list:
    return [step[1] for step in segment["steps"] if step[0] in ("cmd", "two", "twooff")]


def _media_of(name):
    if name in watch.PERMISSIONS:
        return watch.PERMISSIONS[name]["media"]
    return PLANNED.get(name, "feature")


def items() -> list:
    """名前ごとの行（計画 §1 の列: 名前・媒体・種別・THTH の口・審査員が打つ命令・録画の区間）。"""
    by_name = {}
    for segment in SEGMENTS:
        for name in segment["items"]:
            by_name.setdefault(name, []).append(segment)
    out = []
    for name in names():
        segments = by_name.get(name, [])
        commands = [command for segment in segments for command in segment_commands(segment)]
        doors = list(dict.fromkeys([_door_of(command) for command in commands]
                                   + [segment["door"] for segment in segments if segment["door"]]))
        if not doors and name in FEATURE_DOORS:
            doors = [FEATURE_DOORS[name]]
        if name in watch.PERMISSIONS:
            kind, label, recording = watch.PERMISSIONS[name]["kind"], name, True
        elif name in PLANNED:
            kind, label, recording = None, name, True
        else:
            kind, (label, recording) = "feature", FEATURES[name]
        out.append({"name": name, "label": label, "type": "feature" if name in FEATURES else "permission",
                    "media": _media_of(name), "kind": kind, "requested_by_thth": name in watch.PERMISSIONS,
                    "doors": doors, "commands": commands, "segments": [segment["id"] for segment in segments],
                    "recording": recording,
                    "real_first": [segment["real_first"] for segment in segments if segment["real_first"]],
                    "privacy_words": list(PRIVACY_WORDS.get(name, ()))})
    order = {media: index for index, media in enumerate((*MEDIA_ORDER, "feature"))}
    return sorted(out, key=lambda row: order[row["media"]])


def segments() -> list:
    return [{"id": segment["id"], "medium": segment["medium"], "banner": segment["banner"],
             "items": list(segment["items"]), "commands": segment_commands(segment),
             "steps": [list(step) for step in segment["steps"]], "real_first": segment["real_first"]}
            for segment in SEGMENTS]


def script_view() -> dict:
    rows = items()
    return {"items": rows, "segments": segments(),
            "counts": {"permissions": sum(1 for row in rows if row["type"] == "permission"),
                       "features": sum(1 for row in rows if row["type"] == "feature"),
                       "segments": len(SEGMENTS),
                       "real_first": sum(1 for segment in SEGMENTS if segment["real_first"]),
                       "no_door": sum(1 for row in rows if not row["doors"])}}


def tsv() -> str:
    """`tools/appreview_rec.sh --segments` が読む区間の並び（1 行 1 手・タブ区切り）。"""
    lines = ["# 生成物（手で直さない）: python3 -m thth admin appreview script --tsv > tools/appreview_segments.tsv",
             "# 正本は thth/appreview.py の SEGMENTS。tests/test_m5_appreview.py が一致を見る。"]
    lines.extend("\t".join(("slot", name, label)) for name, label in SLOTS.items())
    for segment in SEGMENTS:
        lines.append("\t".join(("seg", segment["id"], segment["medium"], segment["banner"])))
        lines.append("\t".join(("items", " ".join(segment["items"]) or "-")))
        if segment["real_first"]:
            lines.append("\t".join(("real", segment["real_first"])))
        for step in segment["steps"]:
            lines.append("\t".join(step))
        lines.append("end")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 点検

def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_HEADING_MARK = "\x01"   # 見出しの行の頭の印（`_strip` が付け、`_scopes` が読む）


def _strip(text) -> str:
    """タグを外した文。段落・箇条・見出しの切れ目は改行に残す（媒体の項の中で語を探すため）。"""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<h[1-6]\b[^>]*>", "\n" + _HEADING_MARK, text, flags=re.I)
    text = re.sub(r"</?(?:p|li|ul|ol|h[1-6]|div|section|tr|br|table|blockquote)\b[^>]*>", "\n", text, flags=re.I)
    lines = (re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", line))) for line in text.split("\n"))
    return "\n".join(line.strip() for line in lines if line.strip())


def privacy_text(path=None):
    """privacy の頁の文（タグを外す）。`path` が無ければ `tools/build_site.py` が作る頁、それも無ければ生成済みの頁。
    読めなければ None。"""
    try:
        if path:
            with open(path, encoding="utf-8") as stream:
                return _strip(stream.read(4_000_000))
        root = _repo_root()
        builder = os.path.join(root, "tools", "build_site.py")
        if os.path.isfile(builder):
            spec = importlib.util.spec_from_file_location("thth_build_site_for_appreview", builder)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return _strip(module.build_privacy())
        with open(os.path.join(root, "callback", "public", "privacy", "index.html"), encoding="utf-8") as stream:
            return _strip(stream.read(4_000_000))
    except Exception:  # noqa: BLE001 — 読めない頁は「言えない」（断りの符丁で返す）
        return None


def _recent(row, now) -> bool:
    at = jst.parse(row.get("last_success_at")) if row else None
    return at is not None and (now - at) < datetime.timedelta(days=CALL_DAYS)


def _unrecorded(name) -> bool:
    """1 回打っても THTH の記録に残らない権限か（`PERMISSIONS` の evidence が空、または `PLANNED`）。"""
    if name in watch.PERMISSIONS:
        return not watch.PERMISSIONS[name]["evidence"]
    return name in PLANNED


def _scopes(text) -> list:
    """privacy の文を「項」に分ける（小文字にしてある）。段落・箇条の 1 つが 1 つの項。見出しは、その見出しの下の
    段落・箇条を足した項も 1 つ作る（「Messenger …」の見出しの下に語が書かれる形のため）。"""
    scopes, heading = [], None
    for line in text.lower().split("\n"):
        if line.startswith(_HEADING_MARK):
            heading = [line[1:]]
            scopes.append(line[1:])
            scopes.append(heading)          # 下の項を足していく（あとで 1 つの文にする）
        else:
            scopes.append(line)
            if heading is not None:
                heading.append(line)
    return [" ".join(scope) if isinstance(scope, list) else scope for scope in scopes]


def _privacy_missing(words, text, lowered) -> list:
    """足りない語。先頭が媒体の語（`MEDIA_TAGS`）なら、その語のある項の中で残りの語を探す（Threads の項から語が
    消えても Bluesky の項にあれば通る、を許さない）。その語のある項が無ければ、頁全体で足りない語を返す。"""
    if not words:
        return []
    if words[0] not in MEDIA_TAGS:
        return [word for word in words if word.lower() not in lowered]
    tag = words[0].lower()
    best = None
    for scope in _scopes(text):
        if tag not in scope:
            continue
        missing = [word for word in words if word.lower() not in scope]
        if best is None or len(missing) < len(best):
            best = missing
    if best is None:
        return [word for word in words if word.lower() not in lowered]
    return best


def evaluate(view, text, *, now=None, rows=None) -> dict:
    """3 つの点検（読むだけ）。`view` は `permission_watch.build()`、`text` は privacy の文（None は読めない）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    rows = items() if rows is None else rows
    records = {row["permission"]: row for row in view.get("permissions", [])}
    lowered = text.lower().replace(_HEADING_MARK, "") if isinstance(text, str) else None
    found = {code: [] for code in CHECK_REFUSALS}
    out = []
    for row in rows:
        record = records.get(row["name"])
        call = None if row["type"] == "feature" else _recent(record, now)
        missing = None if lowered is None else _privacy_missing(row["privacy_words"], text, lowered)
        state = {"name": row["name"], "type": row["type"], "media": row["media"], "door": bool(row["doors"]),
                 "recent_call": call, "last_success_at": record.get("last_success_at") if record else None,
                 "recorded_by_thth": bool(row["name"] in watch.PERMISSIONS
                                          and watch.PERMISSIONS[row["name"]]["evidence"]),
                 "privacy_missing": missing}
        out.append(state)
        if call is False:
            found["appreview_no_recent_call"].append(row["name"])
        if not row["doors"]:
            found["appreview_no_door"].append(row["name"])
        if missing:
            found["appreview_privacy_missing"].append(row["name"])
    problems = [{"code": code, "level": "red", "names": hit} for code, hit in found.items() if hit]
    for problem in problems:
        if problem["code"] == "appreview_no_recent_call":
            # 1 回打っても記録に残らない権限（記録を残さない・PLANNED）は、Dashboard の呼び出しの欄で確かめる
            unrecorded = [name for name in problem["names"] if _unrecorded(name)]
            if unrecorded:
                problem["unrecorded"] = unrecorded
    if lowered is None:
        problems.append({"code": "appreview_privacy_unreadable", "level": "red", "names": []})
    return {"ok": not problems, "generated_at": jst.iso(now), "call_days": CALL_DAYS, "problems": problems,
            "items": out}
