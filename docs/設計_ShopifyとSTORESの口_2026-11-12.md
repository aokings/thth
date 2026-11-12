# 設計: Shopify と STORES を読む口（叩き台）

- **masaru の答えを待たずに Fable の推しで進めた。朝に覆せる。**（masaru 11/12 夜「迷ったり複雑なところは fable にふりつつ、私の回答はまたずにナイトシフトで」）
- 書いた者: Fable（設計の相談役・2026-11-12 夜）。作るのは THTH開発 第16 と Opus の書き手。
- 元の設計: [設計_ショップとWordPressの口_2026-11-12.md](設計_ショップとWordPressの口_2026-11-12.md)（§1 の決め・§3 の守ること・§4 の BASE の口はそのまま生きる）
- 下調べ: [調べ_ショップのAPI_BASE_STORES_Shopify_2026-11-12.md](調べ_ショップのAPI_BASE_STORES_Shopify_2026-11-12.md)
- 印: 【公式】公式の頁で読んだ（URL と日付つき）／【推測】確かめられず考えた／【未確認】本物の店で最初に確かめる

## 0. 先に: 読み直して分かった、前提が変わる 3 つ

1. **STORES のプライベートアプリは OAuth ではない。** STORES が発行する「クレデンシャル」という固定の文字列を `Authorization: Bearer` に載せるだけ【公式】。1 時間の鍵・35 日の取り直し・private_key_jwt（ES256）は**公開アプリ**（複数の店・事前審査あり）の話。だから STORES は BASE とほぼ同じ形（固定の鍵を手元に置く）で作れ、ブラウザで許す流れ・redirect の受け口・thth.me の relay・秘密鍵の置き場は**今回どれも要らない**。元の設計 §2 の表の「Shopify の次」の行（「鍵を取り直す作りをそのまま使う」）は、プライベートアプリについては当たらない。第16 がその行を直してください（この書は 1 つのファイルしか触らない決まりなので、私は直していない）。
2. **Shopify は GET だけでは読めない。** 鍵を取る口も、Admin GraphQL も POST。今の柵「打つのは GET だけ」は Shopify では守れないので、柵を「**決まった道に、決まった中身だけ**」に作り替える（§5）。
3. **Shopify の scope は `read_products` 1 つで足りる。** `Product` の頁が要ると書くのは `read_products` だけで【公式】、在庫の数（`totalInventory`）も `Product` の欄。`read_inventory` は求めない。

## 1. 決めたこと（1〜8・推しと理由）

| # | 問い | 推し | 理由 |
|---|---|---|---|
| 1 | Shopify の鍵の形 | **2 つとも受ける。** 主は Dev Dashboard のアプリの client id と secret（約 24 時間の鍵を取り直す）。既にある管理画面のカスタムアプリの固定の鍵（`--auth token`）も受ける。秘密は BASE と同じ `~/.config/thth/store-<店>.token`（0600）。取り直した短い鍵は**別のファイル** `store-<店>.access`（0600）に置いて使い回す。期限の知らせ（14 日前）は Shopify では出さない | 固定の鍵は取ったあとの読む道が同じで、足す分は「取り直さない」だけ。2026 年より前からの店はこれを持つ。短い鍵を毎回取ると、長く生きる secret を毎回網に流すことになる（公式も置いて使い回すよう勧める）。人が入れた秘密を機械が書き換えないよう、ファイルを分ける。client secret にも固定の鍵にも「期限の日」は無いので、知らせることが無い |
| 2 | STORES の鍵の形 | **プライベートアプリのクレデンシャルだけ**（固定の文字列・`thth shop token set` に標準入力で渡す・BASE と同じ）。OAuth（公開アプリ）は作らない。「鍵は手元だけ・VM と relay に載せない」は 3 つの店で同じ | §0 の 1。公開アプリは審査が「Web アプリであること（ダウンロード型は不可）」を求め【公式】、CLI の THTH はそのままでは通らない。ES256 の署名も、依存 0 の THTH には部品が無い |
| 3 | 「鍵を取り直す」部品 | 小さな部品 `thth/shop/access.py` を 1 つ作り、**使うのは Shopify だけ**。「取り方」は呼ぶ側が関数で渡す形にしておく。置き場は `~/.config/thth/store-<店>.access`、鎖は `store_state/<店>/access.lock`（flock）。STORES の OAuth のための回す仕組み（取り直しの鍵が毎回入れ替わる型）は今は作らない | 使う店が 1 つなのに 2 つ分の形を作ると、試せない作りが残る。入れ替わる型は「鎖を持ったまま網に出て、新しい鍵を書いてから使う」が要り、別の難しさ。要るときに足す |
| 4 | 網の柵 | 網に出るのは今のまま `wire.py` だけ。ホストは **BASE と STORES は定数、Shopify は「店の名前のラベル＋定数の `.myshopify.com`」**。台帳に持つのはラベルだけ（ホストや URL は受けない）。道・メソッド・問い合わせの文は `wire.py` の表にあるものだけ。**リダイレクトは同じホストでも追わない**（3 つの店とも） | ラベルしか選べなければ、鍵が行く先は必ず Shopify の持ち物。同じホストの中のリダイレクトを追うと、表に無い道（注文など）へ運ばれうるので、追わないのがいちばん固い |
| 5 | 注文を読まない柵・余分な scope | STORES は GET だけ・道は `/retail/202211/store` と `/retail/202211/items` の 2 つ・問い合わせの引数は `limit` と `offset` だけ。Shopify は GraphQL の文を `wire.py` の定数 2 本に固定（呼ぶ側は名前で選ぶだけ・文を渡せない）。Shopify の鍵に余分な scope があれば**断らずに前向きに知らせる**。`read_products` が無ければ断る | THTH が打つものは柵で決まるので、余分な scope があっても THTH は注文を読めない。残る害は「鍵が漏れたときの広さ」で、これは持ち主に知らせて絞ってもらう話（WordPress の管理者の鍵と同じ扱い・利用者を疑わない）。**割れる所**: 書く権限や注文の権限があれば断る案もある（§4.4） |
| 6 | 共通に使えるか | 写し・違い・下書き・読み直し・remove・leave は**共通で使える**。店ごとの差は「応答を許した欄の行に直す所」だけに閉じる。共通の側で直すのは 3 つ: 商品の id が文字列の店がある・在庫が「数えない」の店がある・商品頁の URL が取れる店がある（§6） | 行の形（`ROW_FIELDS`）を変えずに済むので、BASE の写しと試験がそのまま通る |
| 7 | 版・privacy | **Shopify を 5.17.0、STORES を 5.18.0**。5.17.0 の頭に「動きを変えない寄せ」の commit を置く。privacy は**1 つの節**（共通の段＋店ごとの短い段）。STORES の段は 5.18.0 で足す | 監査の 1 回分を小さく保つ。寄せが済めば STORES は薄い。節を 3 つにすると同じ約束を 3 か所で保つことになり、ずれる |
| 8 | 偽のサーバ | 本物の socket は立てず、BASE と同じ**差し替えの transport（関数）**で作る。材料は公式の頁の応答の例から（§8 に写した）。公式で形を確かめられなかった断りは、**HTTP の数だけで決まる**ように作り、形に寄りかからない | `wire.py` に試し用の loopback の穴を開けずに済む。確かめていない形に寄ると、本物で黙って外れる |

## 2. 読んだ事実（2026-11-12）

### 2.1 Shopify

- 【公式】client credentials: `POST https://{店}.myshopify.com/admin/oauth/access_token`・`Content-Type: application/x-www-form-urlencoded`・本文は `grant_type=client_credentials`・`client_id`・`client_secret`。`scope` は送らない（無視される）。応答は `{"access_token": "…", "scope": "read_products", "expires_in": 86399}`。`expires_in` はいつも 86399。置いて使い回し、切れる前に取り直すよう勧めている（例は 60 秒前）。https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/client-credentials-grant ・ https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens
- 【公式】「The client credentials grant only works when the app and the store belong to the same Shopify organization.」店を持っている・アプリを入れている、だけでは同じ組織にならない。外れると `shop_not_permitted`（「Oauth error shop_not_permitted: Client credentials cannot be performed on this shop.」）。管理画面から作った開発用の店も外れる（Dev Dashboard で作った店でないと通らない）。同上。
- 【公式】持ち主の手順: Dev Dashboard でアプリを作る → 版で scope を選ぶ → 店に入れる → Settings で Client ID と Client secret を写す。鍵は `X-Shopify-Access-Token` のヘッダで送る。同上。
- 【公式】2026-01-01 から、店の管理画面で新しいカスタムアプリは作れない。既にあるものは動き続ける。https://shopify.dev/changelog/posts/legacy-custom-apps-can-t-be-created-after-january-1-2026.md （検索結果の要約で読んだ。既にあるアプリは鍵の作り直しができず、消すと作り直せない、ともある）
- 【公式】`Product`（版 2026-10・要る scope は `read_products`）: `id`（ID!）・`legacyResourceId`（UnsignedInt64!）・`title`・`handle`・`status`（ProductStatus!・ACTIVE のものだけが店に出る。問い合わせの絞りには active・archived・draft・unlisted がある）・`onlineStoreUrl`（URL・オンラインストアに出ていなければ null）・`totalInventory`（Int!）・`tracksInventory`（Boolean!）・`priceRangeV2`（`minVariantPrice`・`maxVariantPrice`）・`featuredMedia`（Media・null あり）・`updatedAt`（注文で在庫が動いても変わる）。`featuredImage` は廃止予定。https://shopify.dev/docs/api/admin-graphql/latest/objects/Product
- 【公式】`products` の引数: `first`・`after`・`query`・`sortKey`（既定 ID）。`pageInfo.hasNextPage`。https://shopify.dev/docs/api/admin-graphql/latest/queries/products
- 【公式】`currentAppInstallation { accessScopes { handle } }` で、その鍵に許された scope の一覧が読める。https://shopify.dev/docs/api/admin-graphql/latest/queries/currentAppInstallation
- 【公式】`Shop`: `id`・`name`・`myshopifyDomain`・`primaryDomain`（Domain!）・`url`（URL!）・`currencyCode`。店頭がパスワードで閉じているかを言う欄は見つからなかった。https://shopify.dev/docs/api/admin-graphql/latest/objects/Shop
- 【公式】配列の引数は 250 まで・ページ送りは 25,000 件まで・詰まったら「1 秒待つ」のが勧め。https://shopify.dev/docs/api/usage/limits 。点数の形（`extensions.cost.throttleStatus` の `maximumAvailable`・`currentlyAvailable`・`restoreRate`）は元の設計 §4.5 の書き手が確かめた。
- 【未確認】詰まったときの応答の形（HTTP 200 で `errors[].extensions.code` が `THROTTLED` と広く知られるが、今夜の頁では読めていない）・scope が足りないときの形（`ACCESS_DENIED`）・鍵が違うときの 401 の本文・`Domain.url`。

### 2.2 STORES

- 【公式】認証は 2 つ。公開アプリ（複数の店）は OAuth 2.0、プライベートアプリ（1 つの店）はクレデンシャル。「特定の単一ストアにのみ使用できるアプリを作成するには、クレデンシャルを使った認証によって API を利用します。」 https://github.com/heyinc/development-partner-docs/blob/master/auth.md
- 【公式】「クレデンシャルは[パートナーアプリ登録フォーム]で入力したストア URL のストアと 1:1 で紐付きます。」登録フォームで種類に「プライベートアプリ」を選ぶと、5 営業日以内に発行の手続きが進む。「万が一クレデンシャルが流出すると、ストアの情報が外部に漏れる危険性があるため、厳重な管理を行なってください。」 https://github.com/heyinc/development-partner-docs/blob/master/auth-credential.md
- 【公式】プライベートアプリの登録の項目は「種類・アプリ名・対象プロダクト・利用用途」だけ。「必要な権限」「コールバック URL」は公開アプリだけの項目。つまり**クレデンシャルの権限は選べない**（読みも書きも注文も通ると見て柵を作る【推測】）。https://github.com/heyinc/development-partner-docs/blob/master/development-partner-signup.md
- 【公式】「プライベートアプリでの API のご利用の場合、スタンダードプランへのお申し込みが必要です。」 https://github.com/heyinc/retail-api-docs/blob/master/README.md
- 【公式】API（202211 版が最新）: ホストは `https://api.stores.dev`。`Authorization: Bearer {credential}`。道は `GET /retail/202211/items`・`GET /retail/202211/store`・`GET /retail/202211/orders`・`GET /retail/202211/sales_channels`・`GET /retail/202211/categories`・`GET /retail/202211/remittances` ほか、書く道（`POST /retail/202211/items`・`PATCH /retail/202211/items/{item_id}`・在庫の調整・発送の完了・クーポン）もある。https://heyinc.github.io/retail-api-docs/202211.html （頁に埋まった OpenAPI の定義を取り出して読んだ）
- 【公式】`GET /retail/202211/items`: 引数は `ids`・`status`・`in_stock`・`only_preorder_sales`・`keyword`・`limit`（1〜100・既定 10）・`offset`。応答は `{"items": [...]}`。1 件の欄は `id`（**文字列**・例 `616950cd768f7263b4dab1a1`）・`name`・`created_at`・`status`（`shown` 公開・`hidden` 非公開・`unlisted` 限定公開）・`image_urls`（文字列の配列）・`price`（整数か null・「variations の中で最も高い sales_price」）・`description`・`regular_price`・`type`（`regular`・`ticket`・`subscription`・`digital_content`）・`preorder_sales`（null あり）・`sales_period`（null あり・`period_start_at`・`period_end_at`）・`variations[]`（`id`・`name`・`quantity`（整数か null・「在庫数が無制限の場合は null」）・`sales_price` ほか）。**更新の時刻・商品頁の URL は無い。**「店舗の在庫数は取得できません。」同上。
- 【公式】`GET /retail/202211/store`: `{"id": "66a0ba9ed7785100a418d4f9", "name": "STORES SHOP"}` の 2 つだけ。**店の URL は返らない。**同上。
- 【公式】断り: 400 と 403 は `{"errors": [{"message": "…"}]}`、503 は `{"message": "…"}`。多すぎると 429。401 は定義に無い。同上。
- 【公式】試しの環境は無い。「新規にテスト用のアカウントを登録する、もしくは…モックサーバーを用意する」。商品の見本のデータは「現在、対応していません」。https://github.com/heyinc/retail-api-docs/blob/master/test-data.md
- 【公式】（公開アプリの話・今回は使わない）OAuth は `https://api.id.stores.jp` の `/oauth2/auth`・`/oauth2/token`・`/oauth2/revoke`。クライアントの認証は private_key_jwt だけ・ES256 だけ。秘密鍵は登録のあと STORES が JWK で渡す。取り直すと「古いリフレッシュトークンは無効化されます。」PKCE と redirect の形（localhost が通るか）は書かれていない。https://github.com/heyinc/development-partner-docs/blob/master/auth-oauth.md
- 【未確認】クレデンシャルの期限・取り消し方・作り直し方・鍵が違うときの HTTP の数。

## 3. 作り（ファイル）

```
thth/shop/
  wire.py     網の口と柵（3 つの店の表・GET と POST の 2 か所・リダイレクトを追わない）※直す
  rows.py     新: 店に寄らない行の部品（ROW_FIELDS・clean_text・https の検査・snapshot_row・in_stock・buyable）
  kinds.py    新: 店の種類の表（名前・断り書き・台帳の欄・鍵の形・口の作り方・id の形）
  access.py   新: 短い鍵の置き場と取り直し（鎖つき）。使うのは Shopify だけ
  base.py     今のまま（rows.py から名前を引き直すだけ。今の試験が import している名前は残す）
  shopify.py  新: Shopify を読む（shop・products の 2 本の問い合わせ → 行）
  stores.py   新（5.18.0）: STORES を読む（store・items の 2 つの道 → 行）
  ledger.py・diff.py・draft.py・recheck.py・remove.py・cli.py   ※店の種類で分かれる所だけ直す
```

- `tools/release_gate.py` の `DANGER_AREAS` は `thth/shop/*` なので、新しいファイルは自動で入る（足す作業は無い。入っていることを今の試験が見る）。
- `kinds.py` の表（1 つの種類につき）: `label`（"BASE"・"Shopify"・"STORES"）・`not_by`（「この道具（THTH）は ○○ が提供するものではありません。」）・`ledger_fields`・`auths`（受ける鍵の形）・`client(cfg, secret, transport)`（`user()` と `items()` を持つ口を返す）・`parse_item_id(文字列)`。`cli.py`・`recheck.py`・`morning.py` は `base.Client` を直に呼ばず、この表を通す。

## 4. Shopify（5.17.0）

### 4.1 台帳と鍵

- 台帳: `{"store", "kind": "shopify", "project", "token", "shop"}`。`shop` は **myshopify のラベルだけ**（`[a-z0-9]([a-z0-9-]{0,58}[a-z0-9])?`）。`thth shop add <店> --kind shopify --project <p> --shop <ラベル> --by <名前>`。`xxx.myshopify.com` と打たれたら、後ろを落としてラベルにする（断らない）。独自ドメインや URL は `store_args_invalid（shop）`で前向きに断る（「管理画面の URL の `admin.shopify.com/store/<ここ>` か、`<ここ>.myshopify.com` を入れてください」）。
- 台帳の欄は種類ごとに決まる（base は今の 4 つ・shopify は＋`shop`・stores は＋`public_url`）。知らない欄・足りない欄は今と同じく断る。
- 鍵のファイル `~/.config/thth/store-<店>.token`（0600・人が入れたときだけ書く）:
  - client の形: `{"auth": "shopify_client", "client_id", "client_secret", "shop_id", "obtained_at", "expires_on": null, "no_expiry": true}`
  - 固定の鍵の形: `{"auth": "shopify_token", "access_token", "shop_id", "obtained_at", "expires_on": null, "no_expiry": true}`
  - BASE の今のファイルには `auth` が無い。無ければ今の形（`access_token` と `expires_on`）として読む。
- 短い鍵のファイル `~/.config/thth/store-<店>.access`（0600・機械が書く）: `{"schema": "thth.store_access/1", "access_token", "expires_at"（ISO）, "scope": [...], "obtained_at"}`。
- 渡し方: `thth shop token set <店> --auth client|token --by <名前> [--stdin]`。`--auth client` は 2 行（1 行目 client id・2 行目 client secret。端末なら隠した入力を 2 回）、`--auth token` は 1 行。**引数では受けない**（今と同じ）。`--expires` は Shopify では受けない（付けたら `store_args_invalid（expires）`で「Shopify の鍵には期限の日がありません」）。BASE は今のまま必須（argparse の required を外し、命令の中で BASE のときだけ求める）。
- `token set` の流れ: 入力を受ける → `redact.register_secret` → （client なら）短い鍵を取る → `ThthShop` を打つ → `read_products` があるか・通貨が JPY か・`myshopifyDomain` が `<ラベル>.myshopify.com` か → 鍵のファイルを書く → 短い鍵のファイルを書く（client）→ 店が替わっていれば写しを捨てる（今の作り）→ 記録。**店が見えたときだけ保存する**のは今と同じ。
- 期限の知らせ: `ledger.expiry` に状態 `none` を足す（`no_expiry` が真のとき）。`none` は morning にも命令の注意にも何も出さない。BASE の `unknown`（期限を入れ忘れた）とは別にする。短い鍵の 24 時間は中の都合で、人には知らせない。取り直しが断られたとき（secret を作り直した・アプリを外した）は、その命令の断りで知らせる（morning は網に出ないので気づけない。これは朝の知らせの穴として リリースノートの「残したこと」に書く）。

### 4.2 短い鍵の取り直し（`access.py`）

```
ensure(cfg, secret, mint, *, now) -> 短い鍵の文字列
  1. 置き場を読む。残りが 300 秒より長ければそれを返す
  2. 鎖（store_state/<店>/access.lock・flock・排他）を取る。20 秒待って取れなければ store_refresh_busy
  3. 鎖の中でもう一度置き場を読む（待つ間に別の thth が取り直していれば、それを使う）
  4. mint() を 1 回呼ぶ（網に出るのは wire.shopify_token）。応答の access_token が文字列・expires_in が正の整数でなければ store_api_changed（token_response）
  5. expires_at = 今 + min(expires_in, 86400)。atomic に書く（secrets_fs.atomic_write_json・0600）。鎖を放す
drop(cfg, 断られた鍵) -> 置き場の鍵がそれと同じなら消す（鎖の中で）
```

- 読む命令が 401 を受けたとき: 置き場の鍵を使っていたなら、`drop` → `ensure` → **同じ問い合わせを 1 回だけ**打ち直す。それでも 401 なら `store_token_rejected`。「再試行しない」の決まりの、ただ 1 つの例外（鍵を取り直しての 1 回）として注に書く。固定の鍵（`shopify_token`）は打ち直さない。
- 同時に 2 つの thth: 鎖の中の読み直しで、2 つめは取り直さずに済む。新しい鍵を取ると前の鍵が死ぬのかは公式に書かれていない【未確認】。死ぬとしても、上の 401 の 1 回で立ち直る。
- `token set` と `remove` も同じ鎖を取る（取り直しの途中で秘密を替えない・消さない）。`token set` は置き場を消してから書き直す。
- 鎖のファイルは `store_state/<店>/` の中なので、`remove` の「写しを消す」（rmtree）で一緒に消える。`remove` の順は **鍵 → 短い鍵 → 写し → 台帳**（`CATEGORIES` に `access` を足す。`store-<店>.access` も既定の置き場のものだけ消す・symlink は断る）。無い物は数えないので、BASE の店の digest は変わらない。
- 取り直しの断り（どれも なぜ・通すには・もっと良く の 3 つを目録に）:

| 起きたこと | 符丁 | 通すには（要点） |
|---|---|---|
| 応答に `shop_not_permitted` の語がある（本文を 4096 バイトまで見て、語が**あるか無いか**だけで決める。本文は断りに入れない） | `store_org_mismatch`（新） | Dev Dashboard で、アプリと店が同じ組織に並んでいるか確かめる。管理画面から作った開発用の店は通らないので、Dev Dashboard から店を作る。組織を合わせられない店は、既にある固定の鍵があれば `--auth token` |
| 上のほかの 400・401・403 | `store_refresh_rejected`（新・detail は `HTTP 401` など） | Dev Dashboard の Settings の Client ID と secret を写し直して `thth shop token set`。アプリが店に入っているかも確かめる |
| 404 | `store_shop_unknown`（新） | 台帳の `shop` のラベルを確かめる（`thth shop remove` → `add` で作り直す） |
| 3xx（リダイレクトは追わない） | `store_request_rejected（redirect）` | 同上（店の名前を変えた店は、元の myshopify のラベルを使う） |
| 429・5xx・届かない | `store_unavailable` | 少し待ってもう一度 |
| 鎖が取れない | `store_refresh_busy`（新） | 別の thth が取り直している。少し待ってもう一度 |

### 4.3 読む（`shopify.py`）

- 呼び先は `https://<ラベル>.myshopify.com/admin/api/2026-10/graphql.json`（版は `wire.py` の定数 `SHOPIFY_API_VERSION = "2026-10"`）。文は `wire.py` の定数 2 本（書き手は作る前に、欄の名前を公式の `Product`・`Shop`・`products` の頁で 1 度見直す）:

```graphql
query ThthShop { shop { id name myshopifyDomain url currencyCode } currentAppInstallation { accessScopes { handle } } }

query ThthProducts($first: Int!, $after: String) {
  products(first: $first, after: $after, sortKey: ID) {
    nodes { id legacyResourceId title status onlineStoreUrl totalInventory tracksInventory updatedAt
            priceRangeV2 { minVariantPrice { amount currencyCode } }
            featuredMedia { preview { image { url } } } }
    pageInfo { hasNextPage endCursor }
  }
}
```

- `user()`: `shop_id` は `myshopifyDomain`・`shop_name` は `name`・`shop_url` は `url`（https の検査を通す）・`able_to_business` は None（公開前かを言う欄が見つからないので「不明」。`store_not_open` は Shopify では出ない）。`myshopifyDomain` が `<ラベル>.myshopify.com` と違えば `store_shop_mismatch`。`currencyCode` が `JPY` でなければ `store_currency_unsupported`（新・「初めの版は円の店だけ。値段を整数の円で持つため」）。`scopes` は handle の並び（`[a-z_]{1,64}` に合うものだけ・上限 300）。
- `items()`: `first` は 50・上限 10 頁（500 件）・`complete` は最後の頁の `hasNextPage` が偽のとき。頁の間は 0.5 秒。応答の `extensions.cost.throttleStatus` が読めて `currentlyAvailable` が `requestedQueryCost` の 2 倍を切っていたら、`requestedQueryCost / restoreRate` 秒（上限 5 秒）待つ。読めなければ 1 秒。
- 行への直し方（`ROW_FIELDS` は今のまま）:

| 欄 | Shopify |
|---|---|
| `id` | `int(legacyResourceId)`（数字の文字列でなければ `store_api_changed`） |
| `visible` | `status == "ACTIVE"` かつ `onlineStoreUrl` が https の URL。偽なら行は `{"id", "visible": False}` だけ（下書き・アーカイブ・限定公開・オンラインストアに出していない商品は名前も持たない） |
| `name` | `clean_text(title)` |
| `price` | `minVariantPrice.amount` を `decimal.Decimal` で読み、整数の円なら `int`。`currencyCode` が JPY でない・小数がある → `store_currency_unsupported`。種類で値段が違う商品は**いちばん安い値段** |
| `stock` | `tracksInventory` が偽なら **None（数えない）**、真なら `totalInventory`（負のこともある・そのまま） |
| `sales_status` | `"active"` |
| `image_url` | `featuredMedia.preview.image.url`（https の検査・無ければ None） |
| `item_url` | `onlineStoreUrl` |
| `modified_at` | `updatedAt`（注文でも変わるので、違いの判定には使わない。今も使っていない） |

- 断り: HTTP 401 → 4.2 の 1 回の取り直しのあと `store_token_rejected`。402・423 → `store_unavailable（shop_frozen）`【推測の数・形に寄らない】。403 → `store_scope_missing`。404 → `store_shop_unknown`。429・5xx → `store_unavailable`。200 で `errors` がある → `extensions.code` が `THROTTLED` なら `store_unavailable（throttled）`、`ACCESS_DENIED` なら `store_scope_missing`、ほかは `store_api_changed（graphql_errors）`。`data` が無い・形が違う → `store_api_changed`。**`errors` の文は断りに入れない**（`code` を `[A-Z_]{1,40}` で検査した語だけ）。
- 知っている弱い所（リリースノートの「残したこと」に書く）: 「在庫 0 でも売り続ける」設定の商品は `stock` が 0 以下になり、THTH は「買えない」と見て下書きを作らない（出しすぎない側）。店頭がパスワードで閉じているかは分からない（承認する人が見る）。

### 4.4 scope の扱い

- 求めるのは `read_products` だけ。`add` と `token set` の案内に「Dev Dashboard のアプリの版で、scope は read_products だけを選んでください」と書く。
- 鍵の scope は 2 か所で分かる（短い鍵を取った応答の `scope`・`currentAppInstallation`）。使うのは後者（固定の鍵でも読める）。
- `read_products` が無い → `store_scope_missing` で保存しない。
- 余分がある → **保存して、前向きに知らせる。** `token set` と `whoami` の出力に 1〜2 行と、`--json` に `scopes_extra`（handle の並び）。文の型: 「この鍵は read_products のほかに 3 つの権限を持っています（write_products・read_orders・read_customers）。THTH が Shopify に送るのは、店の名前と商品を読む決まった 2 つの問い合わせだけです。鍵が漏れたときの害を小さくするため、Dev Dashboard でアプリの版の scope を read_products だけにして店に入れ直すと安心です。」記録（`shopread_token_set`）に `scopes_extra` の数を残す。
- **割れる所（朝に覆せる）**: 書く権限（`write_*`）や注文・お客さんの権限（`read_orders`・`read_all_orders`・`read_customers`）があれば保存を断る案。固い代わりに、既にあるカスタムアプリの鍵（ほかの道具と共用で、絞れない店が多い）がほぼ使えなくなる。覆すなら、`token set` の 1 か所を「知らせる」から `store_scope_excess` の断りに替えるだけで済むように作る。

## 5. 網の柵（`wire.py`・3 つの店）

- 網の部品（`urllib.request`・`httpsafe`）を import するのは `wire.py` だけ（今の柵のまま）。`access.py`・`shopify.py`・`stores.py` は `wire` の関数を呼ぶ。
- 呼ぶ側に見せる口は 3 つ。**URL も本文も `wire.py` が組む**（呼ぶ側は URL・問い合わせの文・ヘッダを渡せない）:
  - `get(kind, path, params, token, *, transport)` — base・stores だけ。`Authorization: Bearer`。
  - `shopify_token(shop, client_id, client_secret, *, transport)` — 本文は決まった 3 つの欄の form。
  - `shopify_graphql(shop, name, variables, token, *, transport)` — `name` は `QUERIES` の鍵（`"shop"`・`"products"`）。`variables` は `products` の `first`（1〜50 の整数）と `after`（文字列か None・長さ 1024 まで）だけ。`X-Shopify-Access-Token`。
- 表（`ROUTES`）:

| 店 | ホスト | メソッドと道 | 問い合わせの引数 |
|---|---|---|---|
| base | `apiv2.thebase.com`（定数） | GET `/api/user`・GET `/api/items` | `limit`・`offset` |
| stores | `api.stores.dev`（定数） | GET `/retail/202211/store`・GET `/retail/202211/items` | `limit`・`offset`（`ids`・`keyword`・`status` は使わない） |
| shopify | `<ラベル>.myshopify.com`（ラベルは正規表現・後ろは定数） | POST `/admin/oauth/access_token`・POST `/admin/api/2026-10/graphql.json` | なし |

- `check_request(method, url, body)`: https・port なし・userinfo なし・fragment なし。**ホストから店の種類を決め**（定数 2 つに等しいか、`.myshopify.com` で終わりラベルが正規表現に合うか）、(メソッド, 道) が表にあるか、引数の名前が表にあるかを見る。POST のときは本文も見る（token は 3 つの欄で `grant_type` が `client_credentials`・graphql は JSON で `query` が `QUERIES` のどれかと**1 字も違わず**、`variables` の鍵が許したものだけ）。外れたら打たずに `store_request_rejected（blocked_request）`。`default_transport` は打つ直前にこれを通す（呼ぶ側が正しく組んでいても、もう 1 度）。今の `check_url(method, url)`・`BASE_HOST`・`BASE_ORIGIN`・`BASE_PATHS` は名前を残す（今の試験と privacy の柵が使っている）。
- transport の形: `transport(method, url, headers, body=None)`。GET のときは今と同じ 3 つの引数で呼ぶ（BASE の今の偽の transport を直さずに済む）。
- **リダイレクトを追わない**: `wire.py` に、どのリダイレクトも `httpsafe.RedirectBlocked` にする handler を置き、`httpsafe.build_opener(redirect_handler=…)` で作った opener を `leave_gate.urlopen(opener.open, request, timeout=…)` に渡す（退出の止めの作法は今のまま通る）。3xx は `(数, 本文)` で返り、口が `store_request_rejected（redirect）` に直す。BASE もこれに替わる（動きが少し固くなるので、5.17.0 の 2 つめの commit に分け、監査に見せる）。
- 鍵が行く先: `Authorization`・`X-Shopify-Access-Token`・client secret を載せるのは `wire.py` の 3 つの口だけで、行く先は上の表のホストだけ。台帳の `public_url`（STORES）と、応答に入っている URL（`onlineStoreUrl`・画像の URL）には**何も送らない**（下書きの本文に載せるだけ）。
- 柵の試験（`tests/test_store_wire.py` を新しく・今の `test_store_base_audit.py` の 2 本は下のように直す）:
  1. 網の部品を使うのは `wire.py` だけ（今の試験のまま。新しいファイルも自動で見る）。
  2. `wire.py` の `Request(` は 2 か所だけ。1 つは `method="GET"` の定数で `data` なし、もう 1 つは `method="POST"` の定数。`urlopen` に当たる呼び出しは 1 か所（今の「Request は GET だけ」の試験をこれに書き替える。**書き替えるのはこの 1 本だけ**）。
  3. base と stores のホストへの POST・PUT・PATCH・DELETE は全部断る。表に無い道（`/api/orders`・`/retail/202211/orders`・`/retail/202211/remittances`・`/retail/202211/sales_channels`・`/retail/202211/items/x`）は GET でも断る。
  4. Shopify: GET は全部断る。`QUERIES` に無い文（1 字足したもの・`mutation` を含むもの）は断る。`variables` に知らない鍵があれば断る。
  5. `QUERIES` の文はどれも `query ` で始まり、`mutation`・`subscription`・`order`・`customer`・`draftOrder` の語を含まない（大文字小文字を無視）。
  6. ラベル: `a.b`・`a/b`・`a@b`・`a.myshopify.com.evil.example`・大文字・空・61 字 は断る。`evil.example`・`myshopify.com`（ラベルなし）・`x.myshopify.com:8443` も断る。
  7. 3xx を返す偽の opener で、次の網の呼び出しが起きないこと（同じホストへのリダイレクトでも）。
  8. どれも、柵を 1 度わざと壊して赤になるのを見る（表に `/retail/202211/orders` を足す → 3 が赤、など）。

## 6. 共通の部品（3 つの店で使う・店ごとの差）

### 6.1 店ごとの差の表

| | BASE | Shopify | STORES |
|---|---|---|---|
| 鍵 | PAT（期限あり・`--expires` 必須） | client id と secret（24 時間の鍵を取り直す）か固定の鍵（期限なし） | クレデンシャル（期限は分からない・`--expires` は任意） |
| 商品の id | 整数 | 整数（`legacyResourceId`） | **文字列**（英数字） |
| 公開 | `visible` | `status` が ACTIVE でオンラインストアに出ている | `status` が `shown`（`hidden` と `unlisted` は非公開の扱い） |
| 在庫 | 整数 | 整数か**数えない** | 種類の数の合計か**数えない（無制限）** |
| 値段 | `price`（税込の円） | 種類の中で**いちばん安い**値段（円の店だけ） | 種類の中で**いちばん高い** `sales_price`（公式の定義） |
| 売っているか（`sales_status`） | `item_now_on_sale` ほか 3 つ | `active` | `on_sale`・`before_sale`・`after_sale`・`preorder`（下の 7.3） |
| 商品頁の URL | 取れない（店の URL を載せる） | **取れる**（`onlineStoreUrl`） | 取れない |
| 店の URL | `shop_url` | `shop.url` | **応答に無い**（台帳の `public_url`・持ち主が入れる） |
| 店が公開前か | `able_to_business` | 分からない | 分からない |
| 鍵の権限を読めるか | 読めない | 読める（4.4） | 読めない |
| 更新の時刻 | あり | あり（注文でも動く） | なし（None） |

### 6.2 共通の側で直す 3 つ

1. **id が文字列の店。** 写しの鍵はもう `str(id)` なのでそのまま。直すのは: `cli._item_id`（数字だけ → `[A-Za-z0-9]{1,64}` を受け、店の種類の `parse_item_id` で整数か文字列に直す）・`draft.choose` と `recheck.check` の比べ方（`str()` 同士で比べる）・`recheck._ORIGIN`（`item/([A-Za-z0-9]{1,64})`）。出所の形 `store/<店>/item/<id>` とファイルの名前は変えない。
2. **在庫が「数えない」。** `rows.in_stock(row)`: `stock` が None なら真、整数なら 0 より大きいとき真。`diff.compare` の在庫の節目は `in_stock` の前と今で決める（今の `(x or 0) > 0` と、整数のときは同じ結果）。`rows.buyable(row)`: 公開・`sales_status` が「売っている」の語（`item_now_on_sale`・`active`・`on_sale`。店の間で語がぶつからないので 1 つの集合でよい）・`in_stock`。`cli` の一覧は None を「在庫 —（数えない）」と出す。**非公開の行は `stock` の鍵が無い**ので、`in_stock` は公開の行にだけ使う（今の `compare` の順のまま）。
3. **商品頁の URL が取れる店。** `draft.text_for`: `row["item_url"]` があればそれを本文に載せ、頭は `from_store_url: item`。無ければ店の URL で `from_store_url: shop`（今のまま）。`cli` の注意書き（「商品頁の URL は組み方を…」）は `shop` のときだけ出す。

### 6.3 そのまま使えるもの

- 写し（`thth.store_snapshot/1`・最新の 1 枚・非公開は id と visible だけ）・違いの 8 種・初回は基準だけ・読み切れなければ「消えた・売り切れ」を言わない・`--accept` でだけ写しを進める・別の店の鍵に替えたら写しを捨てる（`shop_id` で見分ける）。
- 下書き（1 本か上限 3 本・値下げの前後は頭にだけ・同じ違いから 2 本目を作らない・lint を通す・画像は載せない）。
- 承認の読み直し（確定の段だけ・台帳の無い機械では知らせて通す）。**初めの版は 3 つの店とも「全部読んで探す」**（今の BASE と同じ道。1 商品だけ読む道は足さない。網の表を増やさないため）。500 件を超える店は `store_read_incomplete` になる（残したことに書く）。
- remove（二段確認）と leave（その project の最後の口座が抜けるとき・打った機械の分だけ）。直すのは 4.2 の `access` の 1 行と、文の中の「BASE の側の PAT は消えません」を店の種類ごとの 1 行に（Shopify「Dev Dashboard でアプリを店から外すか、secret を作り直してください」・STORES「クレデンシャルの取り消しは STORES に頼んでください【未確認】」）。
- 記録（`shopread_*`）。`{"media": "base"}` の決め打ちを台帳の `kind` に替える。**変更ログは鍵の名前に `token`・`secret`・`client_id` を含む欄を「ある・ない」に潰す**（`admin_log.SECRET`）ので、`scopes_extra` のように当たらない名前を使う。
- morning の店の鍵の行: 文の「（BASE の PAT）」を種類の `label` に。状態 `none` は出さない。

## 7. STORES（5.18.0）

### 7.1 台帳と鍵

- 台帳: `{"store", "kind": "stores", "project", "token", "public_url"}`。`thth shop add <店> --kind stores --project <p> --url <店の頁の URL> --by <名前>`。`public_url` は https だけ・userinfo と query と fragment なし・2048 字まで。**THTH はこの URL に何も送らない**（下書きの本文に載せるだけ）。持ち主が入れた値なので、`whoami` に「店の URL（台帳に入れた値）」と出す。
- 鍵: `thth shop token set <店> --by <名前> [--expires YYYY-MM-DD] [--stdin]`。1 行。ファイルは `{"auth": "bearer", "access_token", "expires_on"（あれば）, "no_expiry"（`--expires` が無ければ真）, "shop_id", "obtained_at"}`。保存するのは `GET /retail/202211/store` が通ったときだけ。
- クレデンシャルの権限は読めず、選べもしない（2.2）。`add` の案内と privacy に「THTH は店の名前と商品の一覧だけを GET で読み、注文・入金・店舗の道は呼ばない。店には書かない」と書く。

### 7.2 読む（`stores.py`）

- `user()`: `GET /retail/202211/store` → `shop_id` は `id`（`[A-Za-z0-9]{1,64}`）・`shop_name` は `name`・`shop_url` は台帳の `public_url`・`able_to_business` は None。
- `items()`: `GET /retail/202211/items?limit=100&offset=…`。応答は `{"items": [...]}`（鍵が無い・配列でない → `store_api_changed`）。100 件に満たなければ終わり。上限 5 頁・頁の間 1 秒（1 分 60 回ほどの目安の内）。
- 断り: 401 と 403 → `store_token_rejected（HTTP 403 など）`（STORES には scope が無いので `store_scope_missing` にしない。目録の文に「クレデンシャルが違う・取り消された・スタンダードプランでなくなった、のどれか」）。429・5xx → `store_unavailable`。400 → `store_request_rejected`。**`errors[].message` は断りに入れない**。

### 7.3 行への直し方

| 欄 | STORES |
|---|---|
| `id` | 文字列（`[A-Za-z0-9]{1,64}` に合わなければ `store_api_changed`） |
| `visible` | `status == "shown"`。`hidden`・`unlisted` は `{"id", "visible": False}` だけ（限定公開の商品は名前も持たない）。3 つのどれでもなければ `store_api_changed` |
| `name` | `clean_text(name)` |
| `price` | `price`（整数か None） |
| `stock` | `variations` のどれかの `quantity` が None なら None（数えない）。全部が整数ならその合計。`variations` が空・配列でない → `store_api_changed（item_variations）`【未確認: 種類の無い商品がどう返るか。空で返るなら最初の本物の読みでここに当たるので、そのとき直す】 |
| `sales_status` | `preorder_sales` が null でない → `preorder`。`sales_period` が null → `on_sale`。今が `period_start_at` より前 → `before_sale`、`period_end_at` より後 → `after_sale`、間 → `on_sale`。時刻が読めない → `store_api_changed` |
| `image_url` | `image_urls[0]`（https の検査・無ければ None） |
| `item_url` | None（組み方を公式で確かめられないので組まない。BASE と同じ） |
| `modified_at` | None |

- 予約販売（`preorder`）の商品は、初めの版では下書きを作らない（「新しく入りました」が合わない）。残したことに書く。
- `description`（説明の本文）・`variations` の品番とバーコード・`category_ids`・`delivery_method_ids` は読み捨てる。

## 8. 試験と偽の材料

- 新しい試験: `tests/test_store_wire.py`（§5 の柵）・`tests/test_store_kinds.py`（寄せのあとも BASE が同じ・id の文字列・在庫の「数えない」）・`tests/test_store_shopify.py`・`tests/test_store_access.py`・`tests/test_store_stores.py`（5.18.0）。**今の `tests/test_store_base.py`・`test_store_leave.py` は 1 字も直さず緑**、`test_store_base_audit.py` は「Request は GET だけ」の 1 本だけ書き替える。これが「BASE を壊していない」の証拠になる。
- 偽は transport の関数（BASE の `env` と同じ作り）。中で `wire.check_request` を通す（本物の口と同じ柵）。呼ばれた (メソッド, URL, 本文) を全部ためて、試験の終わりに「表のものだけだった」を見る。
- Shopify の材料（公式の例のまま・値は偽）:
  - 鍵: `{"access_token": "<16 進 32 字>", "scope": "read_products", "expires_in": 86399}`
  - 取り直しの断り: 400 で本文に `shop_not_permitted` の語（JSON の形と素の文の形の 2 通りを用意する。どちらでも `store_org_mismatch` になること）・401（ほかの語）→ `store_refresh_rejected`
  - GraphQL: `{"data": {...}, "extensions": {"cost": {"requestedQueryCost": 312, "actualQueryCost": 58, "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": 1942, "restoreRate": 100.0}}}}`。`extensions` が無い応答でも読めること。
  - 商品: ACTIVE で URL あり・ACTIVE で `onlineStoreUrl` が null・DRAFT・ARCHIVED・UNLISTED・`tracksInventory` が偽・`totalInventory` が 0 と負・`featuredMedia` が null・値段が `"1200.0"` と `"1200.5"` と通貨 USD・知らない欄つき・2 頁（`hasNextPage` 真 → 偽）・上限の 10 頁で終わらない。
  - 断り: 401（本文は `{"errors": "…"}` の**文字列**の形と、空の本文の 2 通り）・200 で `errors[0].extensions.code` が `THROTTLED`・`ACCESS_DENIED`・知らない code・404・302。
- STORES の材料: 2.2 の公式の例（T シャツ・種類 S は `quantity` が null・M は 20・L は 10 → `stock` は None）を土台に、全部が整数・全部 0・`hidden`・`unlisted`・`preorder_sales` あり・`sales_period` の前と後・`price` が null・知らない欄つき・ちょうど 100 件の頁。店は `{"id": "66a0ba9ed7785100a418d4f9", "name": "STORES SHOP"}`。断りは 403 の `{"errors": [{"message": "…"}]}`・503 の `{"message": "…"}`・429・401。
- 鍵が出ないこと（今の BASE の試験と同じ型）: client secret・短い鍵・クレデンシャルの目印が、標準出力・標準エラー・`--json`・変更ログ・写し・下書き・断りのどこにも無い。鍵のファイル 2 つは 0600。
- 取り直しの試験: 残り 300 秒より長ければ網に出ない・切れていれば 1 回だけ取る・鎖の中の読み直し（2 つの呼び出しで mint が 1 回）・401 のあと 1 回だけ取り直して打ち直す・2 回目も 401 なら断る・固定の鍵は取り直さない・鎖が取れなければ `store_refresh_busy`・`remove` が `.access` と鎖を消す。
- 目録: 新しい符丁（`store_org_mismatch`・`store_refresh_rejected`・`store_refresh_busy`・`store_shop_unknown`・`store_currency_unsupported`）に 3 つがそろう（今の試験の型）。今の符丁の文で「BASE」「PAT」と決め打ちの所は、3 つの店で読める文に直す（例 `store_token_missing`「この店の鍵がまだ置かれていないか…（BASE は PAT・Shopify は client id と secret か固定の鍵・STORES はクレデンシャル）」）。`store_ledger_invalid` の「4 つだけ」は「店の種類ごとに決まった欄だけ」に。
- 試験は `python -m pytest -q -n 4`（書き手を並べるとき）で、終了コードを見てから進む。

## 9. privacy と説明

- 節は 1 つのまま。見出しを「Online shops (read-only)」にし、今の BASE の文を「共通の段」と「BASE の段」に分ける（約束の中身は変えない）。5.17.0 で Shopify の段、5.18.0 で STORES の段を足す。**出していない店のことは書かない。**
- Shopify の段に書くこと: 送り先は持ち主の店の `<店>.myshopify.com` だけ。送るのは 2 種類（鍵を取る求めと、店の名前・商品を読む決まった 2 つの問い合わせ。GraphQL の決まりで POST だが、店は変えない）。持ち主が Dev Dashboard で作ったアプリの client id と secret（か、既にある固定の鍵）を手元のファイルに置き、取り直した約 24 時間の鍵も同じ所に置く。店について持つのは myshopify のドメイン・名前・URL。鍵に許された権限の名前の一覧を読み、余分があれば知らせる（一覧は残さない。記録に残すのは余分の数だけ）。注文・お客さんは読まない。THTH は Shopify が提供するものではない。
- STORES の段（5.18.0）: 送り先は `api.stores.dev` への GET だけで道は 2 つ。クレデンシャルは注文も読める鍵だが、THTH は店の名前と商品の一覧の道しか呼ばない。店の URL は持ち主が入れた値。THTH は STORES が提供するものではない。
- 共通の段の「THTH sends only GET requests to …」は BASE の段に移す（Shopify では当たらないため）。
- 「自分を縛る『しない』を書かない」（masaru 11/08）に沿い、送り先・道・持つ物を**事実で**書く。「読まない」と書くのは、柵で縛ってある注文とお客さんだけ。
- `tests/test_privacy_matches_code.py` に結ぶ: Shopify のホストの後ろ（`wire.SHOPIFY_SUFFIX`）・問い合わせの数（`len(wire.QUERIES)`）・STORES のホストと道の数・短い鍵の置き場。1 つを消して赤を見る。
- 助けの文（`PARENT_DESCRIPTION`）とリリースノートに、店の種類ごとの `add` と `token set` の形を足す。`NOT_BY_BASE` は種類ごとの `not_by` に（名前 `NOT_BY_BASE` は残す）。
- privacy が本番に出るのは Worker の deploy のとき（masaru の「出して」のあと）。

## 10. 版と commit の順

**5.17.0（Shopify）**

1. 寄せ（動きを変えない）: `rows.py`・`kinds.py`・台帳の欄を種類ごとに・`cli`／`recheck`／`morning` が種類の表を通す・id の文字列と在庫の「数えない」の受け口。**今の BASE の試験が 1 字も直さず緑**。
2. 網の柵の作り替え: `ROUTES`・`check_request`・POST の口・リダイレクトを追わない・`tests/test_store_wire.py`（わざと壊して赤を見る）。
3. Shopify: `access.py`・`shopify.py`・`add --shop`・`token set --auth`・scope の知らせ・`remove` の `access`・目録。
4. privacy・助けの文・リリースノート・版。

**5.18.0（STORES）**

5. `stores.py`・`add --url`・`token set` の `--expires` 任意・表に 2 つの道・目録の文・privacy の段・リリースノート・版。

- どちらの版も、作った者と別の者が**証拠を再現して**監査する（危ない領域 `store_read`）。監査に特に見てもらう所: §5 の柵・鍵が出ないこと・取り直しの鎖・BASE が変わっていないこと。
- main への push までは夜のうちに進めてよい（本番は変わらない）。release の枝・tag・Worker の deploy は、CI が緑で関門が rc 0 のあと、masaru の「出して」を待つ。

## 11. 本物の店で最初に確かめること（【未確認】の一覧）

本物につないだ最初の `whoami`・`items` で、次を 1 つずつ見て、この書とコードの注を直す。

**Shopify**: `shop_not_permitted` の応答の HTTP の数と本文の形／鍵が違うときの 401 の本文／詰まったときの形（`THROTTLED`）と scope が足りないときの形（`ACCESS_DENIED`）／`totalInventory` と `tracksInventory` が `read_products` だけで読めること／新しい短い鍵を取ると前の鍵が死ぬか／店頭がパスワードで閉じた店の `onlineStoreUrl`／`products` の 1 頁（50 件）の点数。

**STORES**: クレデンシャルの形・期限・取り消し方／鍵が違うときの数（401 か 403 か）／種類の無い商品の `variations`／商品頁の URL の形（`<店の URL>/items/<id>` と広く知られるが【推測】）／スタンダードプランをやめたときの応答。

## 12. masaru に朝に伝えること

1. **STORES は OAuth でなく「クレデンシャル」だった**ので、作りは BASE とほぼ同じになった（ブラウザで許す流れ・relay・秘密鍵は無し）。外の店主に広く配る「公開アプリ」は別の話で、審査が Web アプリを求めるため、THTH を thth.me の上のサービスとして出すかどうかの判断になる（「鍵は手元だけ」の決めとぶつかる）。今は作っていない。
2. **Shopify の試しの店（masaru の手・お金は動かない見込み）**: Dev Dashboard に入り、**Dev Dashboard から**開発用の店を作る（管理画面から作った店では鍵が取れない）→ アプリを作る → 版の scope は `read_products` だけ → 店に入れる → Settings の Client ID と secret を `thth shop token set <店> --auth client` に渡す。
3. **STORES の試し（masaru の手・お金が動く）**: 開発パートナーの登録（氏名・生年月日・住所などを書く・5 営業日以内）で種類に「プライベートアプリ」を選ぶ。その店の**スタンダードプランの申込が要る（有料・額は申込の前に公式で確かめてください。私は読んでいない）**。試しの環境は無いので、試すなら本物の店。身近に STORES の店が無ければ、5.18.0 は「偽の材料でだけ確かめた版」のままになる。
4. **朝に覆せる割れ目は 1 つ**: Shopify の鍵に余分な権限（書く・注文・お客さん）があったとき、今は「保存して知らせる」。「断る」に替えるなら 1 か所で済む（§4.4）。
5. **privacy の文が変わる**（Shopify の段。GraphQL のため「GET だけ」と言えるのは BASE と STORES だけになる）。本番に出るのは Worker の deploy のとき。Meta の審査の節目と合わせるかを見てください。
6. **読んでいない規約**: Shopify の API License and Terms of Use と、STORES の開発パートナー規約の「二次利用」の読み（商品を読んで SNS の下書きを作ることが当たるか）。本物の店につなぐ前に 1 度読む（私か第16 が読んで要点を出せる）。
7. 5.17.0・5.18.0 とも、**本物の店では 1 度も動かしていない**（§11 の一覧が残る）。リリースノートにそう書く。
