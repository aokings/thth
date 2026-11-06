# 調べ: THTH の広告の口を Threads に対応させる土台（2026-11-06）

THTH開発 第13 が Opus に頼んだ調べもの。Meta の公式の頁を読んで確かめた。本番の API は叩いていない。頁の例はどれも v27.0（THTH は `thth/meta/graph.py` の `GRAPH_VERSION = "v26.0"`）。

## 要点

- **Threads 単独には出せない。** targeting は `publisher_platforms: ["instagram","threads"]`・`instagram_positions: ["stream"]`・`threads_positions: ["threads_stream"]` の組でだけ出せる。
- **Threads の自分の投稿は広告にできない。** 広告にできるのは FB か IG の投稿（画像・動画・カルーセル）で、それを Threads にも流す。
- creative に `threads_user_id` を足す（FB の投稿なら `object_story_id`＋`instagram_user_id`＋`threads_user_id`）。
- 結果は `breakdowns=publisher_platform,platform_position` で Threads の行（`threads` / `threads_feed`）に分かれる。THTH の `graphread.BREAKDOWNS["placement"]` はもうこの組を頼んでいる。
- **広告の結果にもフォローの数は無い**（Ads Action Stats に follow 系の action_type が無い）。
- 使える目的: `OUTCOME_AWARENESS`・`TRAFFIC`・`ENGAGEMENT`・`SALES`（2026-03 からアプリの広告も）。試しの間は配信の量が低く抑えられる。

## 1. 出し方

- 出典: Placement Targeting https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/reference/placement-targeting ・ Threads Ads Creation https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/threads-ads/creation.md
- `threads_positions` の値は `threads_stream` だけ。`threads_stream` だけを選ぶことはできない。
- 同じ頁に「publisher_platforms を書くなら facebook を含めよ」という古い一文も残っていて、例と食い違う。
- 最適化の例は `LINK_CLICKS`＋`IMPRESSIONS` だけ。THTH の engagement（`ON_POST`＋`POST_ENGAGEMENT`）が Threads を含めて通るかは書いていない。
- Advantage+ 配置との関係は公式の記述なし（THTH は出し先をいつも明示するので当たらない）。
- 対象の国の公式の一覧は無い。**JP で出せるかは文書で確かめられていない**（第三者の記事では米国と日本で試しを始めた）。

## 2. 既存の投稿を広告にする

- 出典: https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/threads-ads/creation/use-posts-as-ads.md
- FB の投稿: `object_story_id=<PAGE_ID>_<POST_ID>`・`instagram_user_id`・`threads_user_id`・`call_to_action`（例にあるが必須かは不明）。
- IG の投稿: `source_instagram_media_id`・`object_id`（ページ）・`instagram_user_id`・`threads_user_id`・`call_to_action`。
- 不可: 提携の広告・著作権のある音楽やフィルタつきの投稿。Threads の広告への反応は元の投稿に戻らない。
- `threads_user_id` の取り方（Threads の口座の 3 種類）:
  - IG に結びついた口座: `GET /<IG_USER_ID>/connected_threads_user?fields=threads_user_id`
  - IG を元に作る口座: `POST /<IG_USER_ID>/instagram_backed_threads_user`
  - ページを元に作る口座: `POST /<PAGE_ID>/page_backed_threads_accounts`（2026-04-21 から）
  - 後の 2 つはログインして投稿を管理できない。
- IG の口座の条件: プロフィールの画像あり・非公開でない・広告主の権限あり。2026-01-29 より後に作った口座はビジネスに手で足す。
- 未確認: THTH の Threads の adapter の `user_id`（graph.threads.net の `/me`）が広告の `threads_user_id` と同じ値か。

## 3. 権限

- IG に結びついた口座: `instagram_basic`・`threads_business_basic`・`pages_read_engagement`（役割をページ経由で受けるなら `ads_management` か `ads_read` も）。
- THTH の今: ads の鍵の SCOPES（`thth/ads/auth.py`）は `ads_read, ads_management, business_management, public_profile`。`threads_business_basic` は instagram の招待の追加（`thth/scopes.py`）にだけある。
- `threads_business_basic` の Advanced Access の要否は頁に書いていない。
- 返信の管理: 見るのは `ads_read`、隠すのは `ads_management`（boost の広告などは対象外）。

## 4. 結果の数

- 出典: https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/threads-ads/insights
- targeting の値（`threads_stream`）と結果の値（`threads_feed`）が違う。
- 外の計測は `url_tags` に `utm_source=threads`、配置の見分けはマクロ `SITE_SOURCE_NAME`。

## 5. 制限

- 本文は 1000 字まで（越えると IG には出て Threads には出ない）。ハッシュタグと URL の扱いは頁の中で食い違う。
- 画像は幅 500px 以上・1.91:1〜9:16（4:5 より縦長は切られる）。カルーセルは 1:1。

## 6. THTH に足すもの（小さい順・見立て）

1. 出し先の検査（`thth/ads/ops.py`）: `PLATFORMS` に threads。instagram 無しの threads は前向きに断る。threads のときだけ positions を足す。
2. 既定の印: `default_url_tags` の `utm_source` が出し先の 1 つ目になる。threads を分けるならマクロにするかを決める。
3. 台帳と口座: 台帳に `threads_user_id` の欄。`connected_threads_user` を引く読みの口と doctor の突き合わせ。
4. creative（`graphwrite.create_creative`）に `threads_user_id`。
5. 権限: ads の鍵に `threads_business_basic` などを足すか（App Review の対象が増える。計画_Meta全部盛り申請 と合わせる）。
6. digest と木（`digest.py`・`AD_TREE_FIELDS`）: creative の `threads_user_id` を入れるなら版の語を上げ golden を直す。
7. 遠くの道（`server_ads.py`）: 新しい断りの符丁（例 `ads_threads_needs_instagram`・`ads_threads_unlinked`）。activate は今の決まりどおり出さない。
8. できないこと: Threads の自分の投稿を boost する道（Meta が許していない）。
9. 本番で確かめること: v26 で通るか・JP で通るか・engagement＋threads が断られないか・user_id の一致。
