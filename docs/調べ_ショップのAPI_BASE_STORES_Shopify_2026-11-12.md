# 調べ: ネットショップ 3 社（BASE・STORES・Shopify）の API

読んだ日: 2026-11-12。公式の開発者向けの頁を読んだ。申し込み・登録・ログインはしていない（ログインが要る頁は読んでいない）。

印の意味:

- 【公式】 公式の頁（開発者向け文書・規約・ヘルプ）で確かめた。
- 【記事】 公式でない記事（報道・解説）で読んだ。
- 【推測】 公式で確かめられず、周辺の事実から考えた。
- 「分からない」 調べたが確かめられなかった。

## 1. 3 社 × 4 点の表

| | BASE | STORES | Shopify |
|---|---|---|---|
| 1. 外の店主の鍵をもらえるか | v1 は OAuth 2.0 で可。ただし開発者の申請と BASE の承認が要る。v2（β）は個人用の鍵（PAT）だけで、外の店主の鍵を集める形は今はない（OAuth は予定・時期不明） | OAuth 2.0 で可。開発パートナー登録（無料・個人可・2〜3 営業日）。複数の店主向けの「公開アプリ」は事前審査（通常 2 週間）が要る。自分の店だけの「プライベートアプリ」は審査なし | OAuth で可。複数の店主に配る「公開アプリ」は Shopify の審査が要り、アプリストア登録は一度きり 19 米ドル。1 店だけの「カスタム」は審査なし。パートナー登録は無料 |
| 2. 読めるもの・注文・読むだけの権限 | 商品・在庫・値段・画像は取れる。注文は v2 では買い手の名前・住所付きの形（数だけの口は見つからず）。読む権限は商品と注文で分かれている（v1: read_items / read_orders） | 商品・値段・画像・種類ごとの在庫は取れる（ただし在庫が空の場合あり）。注文は住所・名前付きの形だけ。権限は retail.shop.read 1 本で、商品と注文を分けられない | 商品・在庫・値段・画像は取れ、権限も分かれている（read_products / read_inventory）。注文は「数」だけ取れる口（ordersCount）があり、買い手の情報なしで済む。ただし売上の合計は別の道具で、買い手の情報の許可が絡む |
| 3. Meta への公式のつなぎ | あり（「Instagram販売 App」。ただし公式の頁は確かめられず記事のみ） | 分からない（見つからず） | あり（「Facebook & Instagram」の販売チャネル。商品を Meta のカタログに同期） |
| 4. 変化の知らせ・回数の上限 | webhook は見つからない。上限は v1 で 1 時間 5,000・1 日 100,000。v2 β は上限なし（正式版の前に設ける予定） | webhook なし（公式の FAQ）。上限は 1 分 60 回ほど（トークンごと）、超えると 429 | webhook あり（商品の作成・更新・削除、在庫の変化、注文など）。上限は費用の点数方式で、標準プランで毎秒 100 点 |

要点: 3 社の中で、外の店主の鍵を集めて読むだけで使える形が一番整っているのは Shopify。BASE は v1 から v2 への切り替えの途中で、先行きが読みにくい。STORES は入口は開いているが、読む権限を絞れず webhook もない。

## 2. 社ごとの根拠

### 2.1 BASE

**API が 2 本ある。**

- 【公式】v1（`api.thebase.in`）は OAuth 2.0。「β版」と明記され、仕様が変わりうる。https://docs.thebase.in/docs/api/ （2026-11-12 読了）
- 【公式】v2（`apiv2.thebase.com`）は β。認証は個人用アクセストークン（PAT）だけで、OAuth は「提供予定」。正式版は 2026 年の終わりごろを目標。時期の約束は文書にない。https://apiv2.thebase.com/docs/ ・ https://apiv2.thebase.com/docs/migration/ （2026-11-12 読了）
- 【公式】v2 の FAQ は「PAT は自分の店を自分で扱うためのもの。第三者に渡さない。第三者のアプリには OAuth を使う」と書く。OAuth が出たら、アプリは店主から PAT をもらってはいけない。https://apiv2.thebase.com/docs/faq/ （2026-11-12 読了）
- 【公式】v1 の終了日は、どの頁にも書かれていない。v1 のホスト（`api.thebase.com`）は v1 のまま残るとある。https://apiv2.thebase.com/docs/migration/ （2026-11-12 読了）

**1. 鍵・登録・審査・費用**

- 【公式】v1 は BASE Developers に利用登録し、承認を受けてから client_id と client_secret をもらう。申込は https://developers.thebase.com/apply（ログインが要るため中身は読んでいない）。https://docs.thebase.in/docs/api/ （2026-11-12 読了）
- 【公式】API 利用規約: v1 は申込を BASE が承認する。v2 は事前の審査なし。どちらも無料（v2 は事前告知のうえ有料化しうる）。申込者は法人でも個人でもよい書き方。https://thebase.com/pages/api_term （2026-11-12 読了）
- 【公式】規約のデータの使い方: v1 のデータは登録したサイトやアプリでのみ使え、元の BASE の URL へのリンク・データを最新に保つこと・求められたら削除、が条件。v2 のデータは「その店の運営と管理」のみ。第三者のアプリが店のデータにアクセスすること自体は認められているが、BASE が提供するものではないと明示する条件つき。https://thebase.com/pages/api_term （2026-11-12 読了）
- 【分からない】個人の開発者が v1 の承認を得られるか。規約は個人を排除していないが、承認の基準は公開されていない。

**2. 読めるもの**

- 【公式】v1 の権限: read_users（既定）・read_users_mail・read_items・read_orders・read_savings・write_items・write_orders。読みだけに絞れる。https://docs.thebase.in/docs/api/ （2026-11-12 読了）
- 【公式】v2 の商品一覧は、商品名・説明・価格・在庫数・種類ごとの在庫・画像の URL（原寸・幅指定・正方形）・カテゴリを返す。権限は items.read。https://apiv2.thebase.com/docs/endpoints/get-api-items.md （2026-11-12 読了）
- 【分からない】商品の頁の URL（買い物客が見る頁）が応答に入っているか。v2 の応答の `_links` は API の操作へのリンクで、商品頁の URL は見つからなかった。
- 【公式】v2 の注文取得は、買い手の氏名・カナ・メール・住所・電話、配送先、金額の内訳を含む。権限は orders.read で、買い手の情報を外して読む形は文書にない。数や合計だけを返す口も見つからない。https://apiv2.thebase.com/docs/endpoints/get-api-orders-unique-key.md （2026-11-12 読了）
- 【推測】v1 の注文も同じく買い手の情報付き。v1 の注文の頁（docs.thebase.in 側）は 404 で読めなかった。
- 【公式】v2 の権限一覧は items.read・orders.read・customers.read など。説明欄は空。https://apiv2.thebase.com/docs/scopes/ （2026-11-12 読了）

**3. Meta**

- 【記事】BASE は 2018 年に「Instagram販売 App」を始め、2020 年 10 月に Meta のショップ機能に対応した。流れは Facebook アカウント連携、カタログ登録、ドメイン認証、Instagram の審査。https://ecnomikata.com/ecnews/28062/ ほか（2026-11-12 読了。古い記事で、今の画面とは違うかもしれない）
- 【分からない】BASE の公式の頁で現在の状況は確かめられなかった。これは店主が BASE の管理画面で使う機能で、API でカタログに書く口があるかも分からない。

**4. 変化の知らせ・上限**

- 【公式】v1 の頁・v2 の頁とも、webhook の記述はない（なしと断言はできない）。
- 【公式】v1 の上限は、ユーザーごとに 1 時間 5,000 回・1 日 100,000 回。超えると `hour_api_limit` / `day_api_limit`。https://docs.thebase.in/docs/api/ （2026-11-12 読了）
- 【公式】v2 β は上限なし。正式版の前に設け、事前に知らせる。商品作成には 1 日の上限があり、超えると 422（2026-10-19 の変更）。https://apiv2.thebase.com/docs/changelog/ （2026-11-12 読了）

**ほかの大事な点**

- 【公式】v1 の「検索」API は 2022-02-21 で新規の申込が終わっている。
- 【公式】規約の禁止: BASE と競合するサービスでの利用、データを有償で売ること、上限を超える負荷。AI や自動投稿に関する条項はこの規約にはない（別に「BASE AI アシスタント利用規約」があるが読んでいない）。https://thebase.com/pages/api_term （2026-11-12 読了）
- 【推測】v1 は OAuth で使えるが「β・終了日不明・v2 へ移る方針」なので、いま v1 に組むと作り直しになる恐れがある。かといって v2 は OAuth がまだなく、外の店主の鍵を集められない。今は「どちらも一長一短」の時期。

### 2.2 STORES

文書の置き場: 開発者向けの頁（stores.fun/development）は決済 SDK の案内が中心で、ネットショップの API の文書は GitHub にある。

**1. 鍵・登録・審査・費用**

- 【公式】開発パートナー登録は Google フォームで、メール・氏名・ストア URL・事業形態・法人名・代表者氏名・生年月日・住所などを書く。2〜3 営業日で連絡、登録完了メールは 5 営業日以内。https://github.com/heyinc/development-partner-docs/blob/master/development-partner-signup.md ・ faq.md （2026-11-12 読了）
- 【公式】費用: API は無料。個人でも使える（FAQ）。https://github.com/heyinc/development-partner-docs/blob/master/faq.md （2026-11-12 読了）
- 【公式】アプリは 2 種類。「公開アプリ」は複数の店主が使え、STORES の事前審査が必要（通常 2 週間）。「プライベートアプリ」は単一の店だけで、事前審査なし、店が発行する「クレデンシャル」で認証し、スタンダードプランへの申込が要る。https://github.com/heyinc/development-partner-docs/blob/master/partner-app.md ・ partner-app-review.md （2026-11-12 読了）
- 【公式】公開アプリの審査項目に、Web アプリであること（ダウンロード型や拡張機能は不可）、スコープが必要最小限か、店主の便利になるか、プライバシーポリシー・サポート窓口・セキュリティの申告、連携解除時の個人情報の削除、がある。STORES の文字をアプリ名や画像に入れない。https://github.com/heyinc/development-partner-docs/blob/master/partner-app-review.md （2026-11-12 読了）
- 【公式】OAuth 2.0（認可コード）。クライアント認証は秘密鍵で署名した JWT（private_key_jwt・ES256）で、鍵は登録後に STORES が渡す。アクセストークンは 1 時間、リフレッシュトークンは 35 日。https://github.com/heyinc/development-partner-docs/blob/master/auth-oauth.md （2026-11-12 読了）

**2. 読めるもの**

- 【公式】権限は `retail.shop.read`・`retail.shop.write`・`offline_access` の 3 つだけ。読むだけに絞れるが、商品と注文は分けられない（read を付けると注文も読める）。https://github.com/heyinc/development-partner-docs/blob/master/auth-oauth.md （2026-11-12 読了）
- 【公式】商品一覧（2022-11 版）は、名前・説明・価格・割引・画像の URL・公開状態・種類ごとの在庫数（quantity）を返す。ただし「店舗の在庫数は取得できません」とあり、種類の在庫は空（null）の例もある。商品の頁の URL は応答の例に見当たらない（分からない）。https://heyinc.github.io/retail-api-docs/202211.html （2026-11-12 読了）
- 【公式】注文一覧は、配送先の氏名・電話・郵便番号・住所を含む。数や合計だけを返す口は見つからない。同上。
- 【公式】最新の文書は 202211 版。それ以降の版の有無は分からない。

**3. Meta**

- 【分からない】公式の文書では見つからなかった。

**4. 変化の知らせ・上限**

- 【公式】FAQ「現在、webhook の提供はしておりません。順次、開発予定」。https://github.com/heyinc/development-partner-docs/blob/master/faq.md （2026-11-12 読了。この FAQ の更新日は分からない）
- 【公式】上限: 同じトークンで短時間に多いと 429。目安は 1 分 60 回ほど（トークンごと）。同上。

**ほかの大事な点**

- 【公式】開発パートナー規約の禁止に、「STORES と競合するサービスのための利用」、事前の許諾のない API の二次利用、サービスと関係のない目的での API 利用、個人情報の不正な収集、がある。AI や自動投稿の条項は見つからなかった。https://github.com/heyinc/development-partner-docs/blob/master/terms.md 第 15 条（2026-11-12 読了）
- 【公式】公開アプリの審査は「店主の情報を取るだけで店主の便利にならないもの」を通さない。THTH が読んだ商品を SNS へ出す、という価値を審査に説明できるかが鍵になる。【推測】
- 【記事】OAuth 対応と開発パートナー向けの公開は 2023 年 2 月。https://www.st.inc/news/2023-02-09-oauth

### 2.3 Shopify

**1. 鍵・登録・審査・費用**

- 【公式】配り方は「公開（Public）」と「カスタム（Custom）」。公開は複数の店に入れられ、Shopify の承認が要る。カスタムは 1 つの店（または Plus の同じ組織の複数店）向けで承認は要らない。配り方は後から変えられない。https://shopify.dev/docs/apps/launch/distribution （2026-11-12 読了）
- 【公式】公開アプリは、アプリストアへの登録（一度きり 19 米ドル）が要る。カスタムだけを作る人は登録を免除される。パートナープログラム自体は無料。https://shopify.dev/docs/apps/launch/distribution/revenue-share ほか（2026-11-12 検索結果で確認。「登録しない場合は収益の 20% を払う」という条件の記述もあるが、読み取りだけの THTH には関係が薄い）
- 【公式】2025 年 4 月以降、新しい公開アプリは GraphQL のみ。REST は 2024 年 10 月から「レガシー」で、重要な更新しかない。全体の終了日は見つからず（分からない）。https://shopify.dev/changelog/starting-april-2025-new-public-apps-submitted-to-shopify-app-store-must-use-graphql
- 【公式】個人の開発者でもよいか: 文書に資格の条件は書かれていない。パートナー登録は無料。個人が禁じられているとは読めない。【推測】
- 【公式】外の店主ごとに配るなら公開アプリ（審査あり）。カスタム配布は店ごとに別の配布になる見込みで、多数の店主向けには向かない。【推測】

**2. 読めるもの**

- 【公式】権限は細かく分かれる。read_products は商品・種類・コレクション、read_inventory は在庫、read_orders は注文（直近 60 日。古いものは read_all_orders が別に必要）。読むだけに絞れる。https://shopify.dev/docs/api/usage/access-scopes （2026-11-12 読了）
- 【公式】買い手の氏名・住所・電話・メールは「保護された顧客データ」で、既定では取れない。承認を受けるまでは、開発用の店以外では返らない（権限の宣言とは別の審査）。同上。
- 【公式】注文の「数」だけを返す `ordersCount` は read_orders で足り、顧客データの条件の記述はない。https://shopify.dev/docs/api/admin-graphql/latest/queries/ordersCount （2026-11-12 読了）
- 【公式】売上の合計など集計を出す `shopifyqlQuery` は read_reports と、顧客データの 2 段目の許可が要る。https://shopify.dev/docs/api/admin-graphql/latest/queries/shopifyqlQuery （2026-11-12 読了）。つまり「売れた金額の合計」を顧客データなしで取る道は、この調べでは確かめられていない（注文を読んで足すと、注文の権限になる）。
- 【推測】商品の頁の URL（`onlineStoreUrl`）と画像・値段は商品の取得で得られるはず。この調べでは該当頁を読んでいない。

**3. Meta**

- 【公式】「Facebook & Instagram」の販売チャネルがあり、Shopify の商品を Meta のカタログに同期して Facebook ショップ・Instagram ショッピングに出せる。全プランで無料（店の資格しだい）。https://help.shopify.com/en/manual/online-sales-channels/facebook （2026-11-12 読了）
- 【公式】商品は検証済みドメインで直接買えること、Meta のコマース規約に沿うことが条件。商品ごとに Meta の審査がある。https://help.shopify.com/en/manual/online-sales-channels/facebook-instagram-by-meta/requirements-and-considerations （2026-11-12 読了）
- 【記事】検索結果の要約（Shopify のヘルプ頁の抜粋）では、Meta 上の直接の決済は米国の店のみ、コレクションの同期も米国の店のみ。日本が対応国かは頁ごとに食い違う。公式の最新の一覧は読めていない（分からない）。
- 【公式】2025-08-26 から Meta は税の代行をしない。店が税を集めて納める。同上。

**4. 変化の知らせ・上限**

- 【公式】webhook の題がある: PRODUCTS_CREATE / UPDATE / DELETE（read_products）、INVENTORY_LEVELS_UPDATE（read_inventory）、ORDERS_CREATE / ORDERS_PAID（read_orders）。「売り切れ」専用の題は見つからず、在庫の変化の題で受けて数が 0 になったかを見る形になる。https://shopify.dev/docs/api/admin-graphql/latest/enums/WebhookSubscriptionTopic （2026-11-12 読了）
- 【公式】届け先は HTTPS・Google Pub/Sub・Amazon EventBridge。「配信は必ず届くとは限らない」ので、定期的に取り直す処理（照合）を置くよう公式が勧める。順番も保証されず、重複もある。https://shopify.dev/docs/apps/build/webhooks （2026-11-12 読了）
- 【公式】GraphQL の上限は費用の点数方式（バケツ）。標準プランは毎秒 100 点、Advanced 200、Plus 1000、企業向け 2000。1 つの問い合わせは最大 1000 点。アプリと店の組ごとに別のバケツ。https://shopify.dev/docs/apps/build/apis/graphql-admin/rate-limits （2026-11-12 読了。別の頁には毎秒 50 点とする記述もあり、実行時の応答の throttleStatus を見るのが確実）

**ほかの大事な点**

- 【公式】公開アプリは、2025 年以降 GraphQL のみ・商品は新しい GraphQL 商品 API が必須。作るなら最初から GraphQL。
- 【分からない】AI や自動投稿に関する Shopify 側の規約。App Store の要件の頁は 404 で読めず（検索結果の別 URL https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements は読んでいない）。公開アプリの審査で何を見られるかは、実際に出す前に読み直す必要がある。

## 3. THTH から見て、最初の 1 社

**推し: Shopify。**

理由:

1. 外の店主の鍵を集める形（OAuth）が公式に整っていて、読むだけの権限（read_products・read_inventory）に絞れる。
2. 注文は「数」だけを、買い手の情報なしで取る口（ordersCount）がある。THTH は「売れた数」しか要らないので、お客さんの個人情報に触れずに済む。BASE と STORES は、注文を読むと買い手の名前・住所が付いてくる。
3. webhook があり、商品の新規・在庫の変化を受けられる（BASE・STORES は見つからない・なし）。
4. Meta のカタログへの公式のつなぎがあり、THTH がもう持つカタログの口とつながる話をしやすい。

前提（これが変わると結論も変わる）:

- 外の店主に広く配るなら、公開アプリとして Shopify の審査を通す必要がある。1 店ずつ個別に配る形なら審査なしだが、数が増えると破綻する。審査の中身（特に AI・自動の投稿への見方）は未確認で、出す前に読み直す。
- 売れた「金額の合計」まで要るなら、Shopify でも顧客データの許可が絡む道（shopifyqlQuery）になる可能性が高く、「数だけ」の利点が薄れる。金額が要るかを先に決める。
- 日本の店主の多くは BASE・STORES を使っている。Shopify を選ぶと、そもそも使っている店主が少ないかもしれない。【推測】対象の店主が BASE か STORES なら、順序が変わる。その場合は BASE の v2 が OAuth に対応するのを待つのがよい（正式版は 2026 年末ごろの目標・時期は約束なし）。
- 個人開発者として公開アプリを出せるかは文書から確定できていない。

次の一手の案: Shopify の App Store の要件の頁と、公開アプリの審査の「データの使い道」の条件を読み直す。そのうえで、「数だけで足りる」か「金額が要る」かを masaru が決める。
