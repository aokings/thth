# 調べ: Google の広告を THTH に取り込む土台（2026-11-06）

THTH開発 第13 が Opus に頼んだ調べもの。masaru の決定 11/06「出す側も稼ぐ側もどちらもやりたい」。公式の discovery 文書と PyPI の JSON は機械で数え、ほかは developers.google.com の頁を読んだ。本番の API は叩いていない。

## 要点

- **Google Ads API の developer token は 2026-09-09 に廃止。** 段は Google Cloud の project（OAuth のクライアントを持つもの）に付く。**管理者の口座（MCC）も要らなくなった。**
- 段: Test（テストの口座だけ）→ Explorer（本番も可・本番は 1 日 2,880 操作・ふつうは自動で通る）→ Basic（15,000・ブランド確認のあと）→ Standard（上限なし・審査 10 営業日・RMF がかかる）。
- 段と量は THTH の project に付くので、結んだ利用者の全員で分け合う（公式の 2 つの記述からの推論）。
- **作るときに PAUSED にできる**（公式が推奨）。`validateOnly` で試してから送れる。Meta と同じ「作るのは止めた状態・配信の開始は人の二段」がそのまま作れる。
- OAuth のスコープは `adwords` の 1 本だけ（読むだけの細いスコープは無い）。利用者は passkey での認証が要る。
- 版は約 12 か月で廃止。年に 1 回は版を上げる。今は v26（廃止 2027-11）。
- **AdSense Management API は実質読むだけ**（書く口は特別な project だけ）。スコープ `adsense.readonly`、API 独自の審査は無い。収益・表示・クリックを日別・サイト別・ページ別に取れる。
- Ad Manager の REST の API は今もベータ（書く口は揃っている）。Ad Manager の network を持つときだけの話なので後回し。

## A. Google Ads API

### 使い始め

- 出典: developer token https://developers.google.com/google-ads/api/docs/api-policy/developer-token ・ 段 https://developers.google.com/google-ads/api/docs/api-policy/access-levels ・ 量 https://developers.google.com/google-ads/api/docs/best-practices/quotas
- 申請は Cloud Console の「Google Ads API Overview」から。`developer-token` の header は今は無視される（先の版で送ると断られる予定・日付なし）。
- Explorer で使えないのは、口座の作成・利用者の管理・計画・請求。キャンペーンを作る・止めることは禁じられていない。
- キーワードの案と検索の数（キーワードプランナーと同じ中身・`KeywordPlanIdeaService.GenerateKeywordIdeas`）は「計画」に入るので Explorer では呼べない。Basic から（ブランド確認が前提・Cloud の画面のボタンで申請・自動で上がることがある・日数の記載なし）。返る検索の数は月の平均の整数と月ごとの数。広告費の実績が要るとは書いていない【一次 11/08・access-levels・generate-keyword-ideas】。
- 1 日の数え方は project 単位の直近 24 時間。Search は 1 回で 1 操作（ページ送りは数えない）、mutate は中の操作の数だけ（1 回 10,000 まで）、失敗も数える。
- v25 から、Test の project で本番の口座を呼ぶと `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`。90 日使わない project は段を外されうる。
- 既知の不具合: 9/09 以降に段を上げた project で、本番の口座に `AUTHORIZATION_ERROR`（直しを配っている途中）。

### OAuth

- 出典: https://developers.google.com/google-ads/api/docs/oauth/multi-user-authentication ・ https://developers.google.com/identity/protocols/oauth2
- 他人の口座を扱う道具は multi-user authentication の流れ。OAuth アプリの確認を求めている。
- 管理者の口座を経由するときだけ `login-customer-id` が必須。
- Google の OAuth 一般: 公開状態が Testing の project は refresh token が 7 日で切れる。1 つのアカウント×1 つの client ID で refresh token は 100 本まで。端末に入れるアプリの client secret は秘密として扱わない。
- スコープの確認を受けていないと、確認されていない旨の画面と人数の上限がかかる（審査は最大 10 日）。自分と知っている数人だけなら確認は要らない。
- 未確認: `adwords` が sensitive に当たるか（2020 年のブログの又聞きだけ）。

### 道具を配るときの決まり

- RMF https://developers.google.com/google-ads/api/rmf ・ 規約 https://developers.google.com/google-ads/api/terms
- RMF は Standard の段だけにかかる。全部入りの道具は作成・管理・報告をすべて求められ、報告だけの道具は報告だけ、自分だけが使う道具には何もかからない。
- 規約: 利用者に規約を守らせる義務がある。権利は人に譲れない。道具を変えたら permissible use の更新を出す。規約の最終更新は 2019-02-21 で、developer token の前提のまま。

### 読む・書く（v26 の discovery で確かめた）

- `POST v26/customers/{id}/googleAds:search`・`searchStream`・`mutate`・`campaigns:mutate`、`GET v26/customers:listAccessibleCustomers`。書き込みは `validateOnly`・`partialFailure` を付けられる。
- Campaign の status は `ENABLED`・`PAUSED`・`REMOVED`。予算（CampaignBudget、`amount_micros`）を先に作る。出典: https://developers.google.com/google-ads/api/docs/campaigns/create-campaigns
- 未確認: 報告で使う指標とコンバージョンの欄の一覧。

### クライアントと版

- `google-ads` 35.0.0 は wheel 18 MB で、grpcio などに依存する。THTH は依存なし（`pyproject.toml` の `dependencies = []`）なので、REST を urllib で呼ぶのが合う。出典: https://developers.google.com/google-ads/api/rest/overview
- 版: 主な版は約 12 か月、同時に最大 5 つ、3〜4 か月ごとに出る。出典: https://developers.google.com/google-ads/api/docs/sunset-dates

## B. 稼ぐ側

### AdSense Management API v2

- 報告は `accounts.reports.generate`・`generateCsv`。指標の例 `ESTIMATED_EARNINGS`・`PAGE_VIEWS`・`IMPRESSIONS`・`CLICKS`・RPM・CTR。切り口の例 `DATE`・`OWNED_SITE_DOMAIN_NAME`・`PAGE_URL`・`COUNTRY_*`・`TRAFFIC_SOURCE_*`。
- 書く口（adunits・customchannels）は「AdSense for Platforms」の project だけ。
- 量: 1 人 1 分 100 回・project 1 分 500 回・1 日 10,000 回。報告は JSON 10 万行まで。出典: https://developers.google.com/adsense/management/appendix/limits
- 使い始めは project と Client ID を作るだけ。利用者が自分の口座を許す形が前提。出典: https://developers.google.com/adsense/management/getting_started
- 未確認: 第三者の道具に限った決まり、取れるデータの総量の上限の数字。

### Ad Manager

- REST の「Google Ad Manager API (Beta)」は 2024-09 から誰でも使える。報告（`networks.reports`）と、orders・lineItems・creatives などの書く口がある。スコープ `admanager`・`admanager.readonly`。出典: https://developers.google.com/ad-manager/api/beta
- 古い SOAP は四半期ごとの版で、最新 v202608。SOAP を終える日は告知が見つからない。

## C. THTH に足すもの（小さい順・見立て）

今は Google の OAuth が THTH に 1 つも無い。広告の台帳（`thth/ads/ledger.py`）は Meta 専用の形。

1. **段 0（masaru の手）**: Cloud project を作り、Google Ads API と AdSense API を有効にし、OAuth の同意の画面を置き、Explorer を申請する。自分と数人で始めるなら確認なしでよいが、Testing のままだと refresh token が 7 日で切れる。
2. **段 1: AdSense の報告を読むだけ**（例 `thth adsense report`）。Google の OAuth の土台（許可の URL・code の交換・refresh token の保存と更新）もここで作る。台帳の置き場を足し（例 `adsense/`）、`accounts.ledger_kind` に加える（名前の一意の検査を広げる）。
3. **段 2: Google 広告の報告を読むだけ**。`listAccessibleCustomers` で結べる口座を照らし合わせ、GAQL でキャンペーン・広告ごとの表示・クリック・費用・コンバージョン。
4. **段 3: お金の動かない書く口**。止める（PAUSED）と、予算・キャンペーン・広告グループ・広告をすべて PAUSED で作る。`validateOnly` で試してから送る。金額は micros（JPY は 1 円 = 1,000,000）。
5. **段 4: 配信の開始と予算の引き上げを人の二段で**（Meta の activate・budget の考え方を移す）。
6. **段 5: 外の人に広く配るなら Basic → Standard**。Standard で RMF がかかる（報告だけの道具として出すなら軽い）。MCP と遠くの道は、読むだけの口から出す。
7. Ad Manager は後回し。
8. 版は 1 か所の定数にして、毎年上げる。

## 分からなかったこと

- `adwords`・`adsense` のスコープが sensitive に当たるか（Cloud Console の同意の画面で分かる）
- Basic・Standard の申請に道具の設計書が要るか、Basic の審査の日数
- 端末の loopback の転送先が今も許されるか
- AdSense の総量の上限・第三者の道具の決まり
- Ad Manager の REST が正式版になる時期
