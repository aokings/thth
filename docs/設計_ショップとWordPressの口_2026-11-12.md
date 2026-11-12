# 設計: ショップ（BASE）を読む口と、WordPress に出す口

- 書いた者: THTH開発 第16（2026-11-12）。同じ日の夕方、Fable の見立てで直し、masaru の決めを入れた
- 出所: masaru 11/12「ショップ優先」「うちの妻も BASE」「ショッピファイとワードプレスも進めましょう」「3 つとも推しの通りで OK」
- 下調べ: [調べ_ショップのAPI_BASE_STORES_Shopify_2026-11-12.md](調べ_ショップのAPI_BASE_STORES_Shopify_2026-11-12.md)・[調べ_APIのあるブログサービス_2026-11-12.md](調べ_APIのあるブログサービス_2026-11-12.md)
- 背景: [構想_台帳ベースの総合サイト運営_2026-11-12.md](構想_台帳ベースの総合サイト運営_2026-11-12.md)（叩き台）

## 1. 決めたこと（masaru 2026-11-12 夕方「推しの通りで OK」）

1. 命令の名前は **`thth shop`**。今の Instagram の商品タグの口（`thth/meta_shop.py`）と同じ命令の家族にする。`meta_shop.py` は登録の数行だけ触り、今の 5 つの動詞（eligibility・catalogs・products・tags・tag）の名前・引数・出力は変えない（審査の録画と privacy の文がそのまま通る）
2. BASE の PAT は**店主（か家族）の手元の thth にだけ置く**。VM と thth.me の relay には載せない。BASE の公式 FAQ の「PAT は自分の店を自分で扱うためのもの・第三者に渡さない」の線の内にいるため
3. WordPress の書き手は、混ざった変更を消さずに手で書き分けて commit を分ける

## 2. 目的と版

| 版 | 中身 |
|---|---|
| 5.15.0 | BASE の 1 店を読む口（`thth shop` の新しい動詞）と、違いから 1 商品ずつ下書きを作る口 |
| 5.16.0 | WordPress（自分で置く型）に出す口。はてなブログの記事の道に 2 つめの出し先として乗せる |
| 後 | Shopify（利用者と試しの店が出てから。鍵は約 24 時間ごとに取り直す形で作る・§4.5）・2 つめの取り込む元は Meta のカタログを先に・WordPress の予約・消す・アイキャッチ・WordPress.com |
| Shopify の次 | STORES（masaru 11/12 夜「次の版に入れる」）。鍵はプライベートアプリの固定の「クレデンシャル」で、OAuth も取り直しも要らない（Fable 11/12 夜に公式で確かめた。1 時間の鍵・35 日の取り直し・private_key_jwt は公開アプリの話。詳しくは [設計_ShopifyとSTORESの口_2026-11-12.md](設計_ShopifyとSTORESの口_2026-11-12.md)）。読む権限 `retail.shop.read` は注文も読めてしまうので、BASE と同じく商品の道だけを呼び、注文の道は呼ばない（柵と privacy で縛る）。webhook は無いので、読みに行って前の写しと比べる。先に masaru の手が要る: 開発パートナーの登録（氏名・生年月日・住所などを書く・2〜5 営業日）と、プライベートアプリならその店のスタンダードプランの申込（お金が動く）。身近に STORES の店が出たら、Shopify より先にする。材料は [調べ_ショップのAPI_BASE_STORES_Shopify_2026-11-12.md](調べ_ショップのAPI_BASE_STORES_Shopify_2026-11-12.md) §2.2 |

最初の利用者は masaru の家族の BASE の店。**この版は「店主が自分で使う道具」ではなく「masaru が家族の店を代わりに回す道具」**（鍵は標準入力で渡す・承認は CLI の二段）。そう書いて、ずれたまま書かない。

## 3. 守ること（柵にする）

- **お客さんの個人の情報を読まない・残さない。** 注文は読まない。
- **店主本人の情報も最小限。** `GET /api/user` から取り出すのは店の名前と URL だけ。応答をそのまま `--json`・ログ・MCP に流さない（写しも `items` も、許した欄だけ）。
- **ショップに書かない。** ショップのまとまりは GET しか打たない（試験で縛る）。送り先のホストは定数（`apiv2.thebase.com`）で、利用者の入力で変わらない。
- **非公開の商品（`visible` が偽）は、写しに `id` と `visible` だけ。** 下書きは「公開で買える」商品だけ。
- **出すのは承認のあとだけ。** 違いから作るのは承認されていない queue の下書きまで。
- **鍵は会話・出力・追跡されるファイルに出さない。引数で取らない。**
- **前向きな断り**（`docs/原則_前向きな断り.md`）。断り文と説明に「BASE が提供するものではない」の 1 行（BASE の規約）。
- **新しいファイルを `tools/release_gate.py` の `DANGER_AREAS` に足す**（足さないと関門が監査を求めない）。

## 4. ショップの口

### 4.1 置き場と名前

- コード: `thth/shop/`（`meta_shop.py` と並ぶ）。CLI の登録は親の `shop` の parser を 1 か所で作り、Meta の側と新しい側が動詞を足す。
- 店の台帳は `accounts/` に**置かない**（run・collect・doctor・board・`make_adapter` に当たる）。主体（`principals/`）と同じ作りで `stores/<名前>.json`（0600）と `store_state/<名前>/`。名前に `:` を入れない。
- 動詞: `add`・`token set`・`whoami`・`items`・`diff`・`draft`・`remove`（二段確認・監査 5.15.0 で足した）。動きの名前（記録と見張りに出る語）は `shopread_` で始める（add・token set・remove・draft を `admin_log` に `shopread_added` などで残す・鍵は入れない）（`shop_` は Meta の権限の見張り `permission_watch.py`・`leave_gate.py` が使う）。
- 助けの文は「店を読む（BASE など）: 第 1 引数は店の名前」と「Instagram の商品タグ: 第 1 引数は Instagram の口座」の 2 つの山に分ける。

### 4.2 読む・比べる

- `diff` は**読むだけ**。写しを進めるのは `--accept` の一手だけ（下書きでは進めない・監査 5.15.0 で決め直した）。写しを進めていない間は、同じ違いの下書きを日をまたいでも 1 本だけにする（頭の `from_store_since` で見分ける）。別の店の鍵に替えたら写しを捨てて基準を取り直す。
- 初回は「基準を取っただけ・違いなし」（全商品が「新しい」にならない）。
- 全ページを読み切れなかったら写しを進めず、「消えた・売り切れ」と言わない。ページの上限に当たったら、黙って切らずにその旨を出す。
- 写しは最新の 1 枚だけ（履歴を持たない）。在庫の違いは「売り切れ・再入荷」の節目だけ（1 個ずつの動きは売上の写しになる）。
- β への備え: 要る欄が無ければ `api_changed` の類で前向きに断り、写しを進めない。知らない欄は無視。再試行しない。1 回の読みのページの上限と最小の間を定数に。

### 4.3 下書き

- まとめて作らない。1 回に 1 商品（`--item`）か、種類を選んで上限 3 本。
- 本文は素の 1〜2 行（商品名・「新しく入りました／再入荷しました」・商品の URL）。文は利用者の LLM が書く前提で、THTH は材料を渡す。
- 頭に出所（店・商品の ID・読んだ時刻。例 `store/<店>/item/<id>`）。あとで「測って行へ返す」をつなぐ唯一の先回り。
- 値下げの「旧 → 新」は書かない（二重価格の表示の決まり）。材料として頭に置くだけ。売り切れの下書きは作らない。
- 画像は 1 枚目を、今の queue の画像の欄に素直に乗るなら入れる（ホストを限り、大きさと形式を検査）。乗らなければ「画像なし」と明記し、Instagram を対象から外す。
- 商品名・説明は利用者の入力: 頭に入れる前に改行・制御文字・`---` を落とす。
- `--to` の口座が店と同じ project でなければ前向きに知らせる（店と別の人の口座から出すと、関係を明かす表示が要りうる）。
- 承認のときに 1 回読み直し、値段・公開・在庫切れが変わっていれば前向きに断る（`store_item_changed`）。読むのは確定の段（`--confirm`・`--confirm-file`）だけ。店の台帳が無い機械では読み直さずに 1 行知らせて通す（`thth/shop/recheck.py`）。店が公開前（`able_to_business` が偽）なら下書きを作らない。

### 4.4 BASE（v2・β）の事実

公式（2026-11-12、`https://apiv2.thebase.com/docs/` ほか）:

- ベース URL `https://apiv2.thebase.com`。認証は PAT だけ（`Authorization: Bearer <token>`）。OAuth は予定（正式版は 2026 年末が目標）。
- PAT の期限は発行時に決まり、変えられない。期限切れは 401。**`token set` のときに期限の日を受けて持ち、`morning` の今の型（14 日前から知らせる）に乗せる。**
- 商品一覧 `GET /api/items`（`items.read`）。`limit` 1〜100（既定 20）・`offset`。総件数は返らない。
- 応答の欄: `id`・`name`・`detail`・`price`・`proper_price`・`stock`・`visible`・`sales_status`・`images[]`・`variations[]`・`modified_at` ほか。`detail` は利用者の入力のままなのでエスケープが要る。
- 商品頁の URL は応答に無い。`GET /api/user` の店の URL から組めるかを作るときに確かめる（組めなければ URL なしで作り、その旨を出す）。
- β のため予告なく変わる。呼び出しの上限は今は無いが、正式版の前に設ける予定。

### 4.5 作った者が確かめた事実（ショップの書き手・2b1f454）

書き手が公式の頁で確かめて足した行（そのまま写す。Shopify の分は Shopify を入れるときに使う）:

- 商品一覧 `GET /api/items`（scope `items.read`）。`limit` 1〜100（既定 20）・`offset`。総件数は返らない（`limit` 未満なら終わり）。`visible`・`order`（`list_order`・`created`・`modified`）・`sort`。
- 応答の欄: `id`・`name`・`detail`・`price`・`proper_price`・`stock`・`visible`・`sales_status`・`images[]`（`url`・`scaled_url`・`square_url`）・`variations[]`（`stock` など）・`modified_at` ほか。`type` は `normal`・`digital`・`takeout`・`subscription`・`lottery`。
- **商品頁の URL は応答に無い。** `GET /api/user`（認証の利用者とショップの情報）から店の URL を取り、商品の URL を組む形を作るときに確かめる（組めなければ、URL なしで下書きを作り、その旨を出す）。
- β のため予告なく変わることがある。呼び出しの上限は今は無いが、正式版の前に設ける予定。**THTH の側で控えめにする**（1 回の diff で読むのは数ページ・間を空ける）。
- 作るときに確かめたこと（2026-11-12・公式 `https://apiv2.thebase.com/docs/endpoints/get-api-user.md`・`get-api-items.md`・`/docs/errors/`）:
  - `GET /api/user`（scope `user.read`）の店の URL は `shop_url`（独自ドメインがあればそれ、無ければ BASE のドメイン）。ほかに `shop_id`・`shop_name`・`able_to_business`（偽の間は商品頁が公開されない）。**`mail_address`（ログインのメール）・`nickname`・`shop_introduction`・`external_service_account` も返る**ので、THTH は店の id・名前・URL・公開の有無だけを取り出し、ほかは読み捨てる。
  - `GET /api/items` の応答は包む鍵の無い配列。`id` は整数。`images[]` は `url`（原寸）・`scaled_url`・`square_url`。`_links` は API の操作へのリンクで、店の頁ではない。
  - **商品頁の URL の組み方は、公式の頁（v2 の文書・BASE のヘルプ）で確かめられなかった**（v1 の文書 `docs.thebase.in/docs/api/` は 404）。だから組まない: 写しと一覧の `item_url` は空、下書きには店の URL（`shop_url`）を載せ、頭に `from_store_url: shop` と書く。`<shop_url>/items/<id>` の形は一般に知られているが【推測】で、持ち主の画面で確かめてから足す。
  - PAT の期限・scope を問い合わせる口は無い（期限は BASE Developers の画面で見る）。だから `thth shop token set` で期限の日（`--expires`）を受けて持ち、`thth morning` が 14 日前から知らせる。鍵の scope は読めないので、書く権限の鍵は見分けられない（`thth shop add` の案内と断りの文で、読むだけの権限で作るよう頼む）。
  - 断りは Problem Details（`type`・`title`・`status`）。401 `/errors/auth/unauthenticated`（期限切れも）・403 `/errors/auth/forbidden`（scope が足りない）・503 `/errors/internal/maintenance`。上限の断りの形はまだ文書に無い。

#### Shopify（後の版の材料・書き手の元の §3.3 のまま）

- 初めの版は**1 店向けのカスタムのアプリ**（店主が自分の店の管理画面で作る・審査なし）の Admin API の鍵で読む。外の店主に配る公開アプリ（OAuth・審査）は、試しが回ってから考える。
- Admin GraphQL（新しい公開アプリは GraphQL だけ・REST はレガシー。下調べ【公式】）。商品・種類・在庫・画像・`onlineStoreUrl`（商品頁の URL。下調べでは【推測】なので作るときに確かめる）。
- 呼び出しは費用の点数方式（下調べ）。応答の点数の残りを見て控えめにする。
- 作るときに確かめたこと（2026-11-12・公式。**Shopify は初めの版から外した**ので、作るときの材料として残す）:
  - `Product.onlineStoreUrl`（URL・null ならオンラインストアに出ていない）は今もある（`admin-graphql/latest/objects/Product`・版 2026-10）。ほかに `handle`・`status`・`totalInventory`・`priceRangeV2`・`featuredMedia`・`updatedAt`。`featuredImage` は廃止予定。`ProductVariant.price` は `Money`・`inventoryQuantity` は Int（null あり）。
  - `Shop` の `name`・`myshopifyDomain`・`primaryDomain`・`url`。許された scope は `currentAppInstallation.accessScopes`（`AppInstallation` の頁）で読めるので、書く権限の鍵を見分けられる。
  - 呼び先は `https://{店}.myshopify.com/admin/api/2026-10/graphql.json`・鍵は `X-Shopify-Access-Token`。点数は応答の `extensions.cost.throttleStatus`（`maximumAvailable`・`currentlyAvailable`・`restoreRate`）。
  - **2026-01-01 から、店の管理画面で新しいカスタムアプリ（固定の Admin API の鍵）は作れない**（既にあるものは動く・公式の掲示板と記事）。新しく作るなら Dev Dashboard のアプリで、client credentials の流れ（`POST https://{店}.myshopify.com/admin/oauth/access_token`・鍵は約 24 時間・アプリと店が同じ組織のときだけ・公式 `client-credentials-grant`）。つまり「1 店向けの固定の鍵を貼る」形は新しい店では取れず、client secret を預かって 1 日ごとに鍵を取り直す形になる。設計 §3.3 の前提が変わるので、Shopify を入れるときに決め直す。
- 試しの店: Shopify の開発用の店（Partner の登録が要る・masaru の手）。それまでは、公式の応答の形に沿った試験の材料で作る。

## 5. WordPress の口（5.16.0）

- はてなブログの記事の道に 2 つめの出し先として乗せる。**2 つの commit に分ける**: まず動きを変えない commit で、`media == "hatenablog"` の分岐（30 か所ほど）を「記事の口座か」の 1 つの問いに寄せる（golden と全件が緑のまま）。そのあと WordPress を足す。
- 指紋: はてなの `thth-hatena-entry-1` は変えない。`thth-wp-entry-1` を別に足し、サイトの URL・公開の状態・予約の日時を入れる（承認のあとに draft を publish に替えられない）。
- 本文の変換: Markdown の狭い部分集合（段落・見出し・強調・リンク・箇条書き・コード）だけ HTML にする（依存 0 なので自前）。外れる記法と手元の画像は前向きに断る。変換の決まりは指紋の一部。
- 読み戻しは `context=edit` の `content.raw`。初回の読み戻しを基準にする（権限の弱い利用者は kses で削られる）。
- `whoami`（`/wp/v2/users/me?context=edit`）で権限を見て、管理者の鍵なら「編集者の利用者を作ってその鍵を」と前向きに勧める（断らない）。
- 試しの平文 HTTP は今の `httpsafe` の柵（`THTH_TEST_ALLOW_HTTP=1` かつ loopback）の内。VM に載せる日までに、内向きのアドレスを試しのとき以外は断る検査を足す。
- 断りは「REST が閉じている・Authorization が落とされる・https でない・セキュリティの部品が止めている」を分ける。国内のレンタルサーバが海外の IP からの REST を止める件は要確認。
- 初めは 作る・直す・下書きと公開 まで。

## 6. 人の手が要ること

| 何 | 誰 | いつ |
|---|---|---|
| 店主向けの 1 枚（何を読み・何を残し・何を残さないか・PAT の作り方・選ぶ権限 2 つ・期限）を読んでもらい了承をもらう | masaru と店主 | 本物の店につなぐ前 |
| BASE の PAT（`items.read`・`user.read`、期限を決める）。masaru が同席する | 店主本人 | 本物の店で試すとき |
| 店の SNS の口座（特に Instagram のプロ口座）が THTH につながっているか | masaru | 下書きを出す前 |
| 店主が下書きを見て「よい」と言える道（初めは masaru が見せて口頭で、その形を了承に含める） | masaru と店主 | 初めの出す前 |
| WordPress の本物のサイト | masaru（あれば） | 5.16.0 を本物に出すとき |

## 7. 出し方

- 危ない領域に入る版なので、作った者と別の者の監査が要る。`meta_shop.py` の登録の数行も監査の範囲に入れる。
- privacy に、店を読むこと（読むもの・残すもの・残さないもの・鍵は手元だけ）を足す。足す時期は Meta の審査の節目と合わせる（審査の人も privacy を読む）。
- `thth leave` で店の台帳・写し・鍵が消えること（その project の最後の口座が抜けるとき・`thth shop remove` でも外せる。試験 `tests/test_store_leave.py`）。privacy の文と「privacy とコードの一致」の柵にも足す。
- 出すのは masaru の「出して」のあと。
