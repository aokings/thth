---
range: 5ddf3e8..09ea633
dangers: 0
waived:
fixed: 645f37f
auditor: Claude Fable 5.2（作った者とは別）
---
# 監査 次の版の候補 2 つ（WordPress の記事の URL のホスト・morning の鍵の形）

印: 【動】動かして確かめた／【読】読んで確かめただけ／【未】未確認

監査した者は作った者（Opus の書き手）と別。コード・試験・ほかの文書は直していない。push・merge・stash・reset・checkout はしていない。本物の WordPress・本物の店・本番（ssh wt）は呼んでいない。Docker は立てていない。`~/.config` の秘密には触っていない。repo に書いたファイルはこの 1 つだけ（探りの台本と出力は会話の置き場 `scratchpad/audnext/` に置いて回した）。

見た範囲は `git diff 5ddf3e8..09ea633`（作業木 `agent-a7c20ed3caf8b1b43`・枝 `worktree-agent-a7c20ed3caf8b1b43`）。中身は cf08e88（WordPress の `users/me` の `link` のホストを入口と比べる）と 09ea633（`thth morning` が鍵の形の合わない店を黙って飛ばさない）。出所は `docs/監査_5.16.0_wordpress_2026-11-12.md` の直したほうがよい 7 と、`docs/監査_5.18.0_stores_2026-11-13.md` の気づき 13 の後半。版上げとリリースノートはこの範囲にまだ無い（この枝の版は 5.18.0 のまま）。

## 結論

「出す前に直す」危ないは 0 件。直したほうがよいは 3 件、気づきは 9 件。privacy に足す文は**要る**（下に英日の案）。

守られていること。

- WordPress への道は増えていない【動】。`ROUTES` は 4 つのまま。記事の URL のホストが違うサイトで `thth principal auth` と `thth doctor` を打って、偽の WordPress に届いた要求は `GET /wp-json/wp/v2/users/me` だけ。比べるのは同じ応答の 1 欄（`link`）。
- 比べ方は、出たあとの柵（`_record_outcome` の `_host_matches`）と同じ見方（ホストだけ・小文字・ポートと scheme は見ない）【動】。29 通りの表で、`whoami` が「違う」と言った組は、出たあとの柵でも conflict になる組だけだった。誤って止める組は無かった。
- 知らせは出す道を止めない・出たあとの柵も緩めない【動】。知らせを聞かずに出すと、今までどおり `published_elsewhere`・台帳は conflict。`thth doctor` で知らせを記録したあとも同じ。
- 鍵の値は、`principal auth`・`doctor`・`doctor --json`・morning の行のどこにも出ない【動】。
- 画面に出すホストは、英小文字・数字・`.`・`-`・`:` だけ・253 字まで【動】。端末の制御の字（ESC）・下線・日本語の字を含むホストは、出さずに「比べない」に落ちる。
- 別の利用者の応答（`username` が主体の `wp_user` と違う）は、ホストを比べる前に `wp_user_mismatch` で断る【動】。
- 断りの 3 つ（なぜ・通すには・もっと良く）は目録にそろう【動】。
- morning: 鍵のファイルが無い店は 3 種類とも今までどおり黙る【動】。形の合わない鍵は 3 種類とも 1 行で知らせ、別の project の 1 枚には出さない【動】。行に鍵のファイルの中身は入らない【動】。行の命令をそのまま打つと（BASE・STORES・Shopify の client の鍵）、その場で保存されて知らせが消える【動】。

動かして確かめたこと。

- 【動】`python -m pytest -q -n 2` で対象を回した（`tests/test_v5182_wordpress_home_host.py`・`tests/test_v5182_store_morning_key_shape.py`・`tests/test_v5160_wordpress.py`・`tests/test_privacy_matches_code.py`・`tests/test_refusal_catalog.py`）。171 本が緑、終了コード 0。
- 【動】探りの試験を会話の置き場に置き、作った者の試験の土台（偽の WordPress・127.0.0.1／店の偽の transport）を借りて回した。WordPress は 39 本、店は 3 種類で 31 本（うち 1 本は探りの側の書き誤りで赤・コードの誤りではない）。結果は下の各項目に書いた。
- 【動】柵を 19 通りわざと壊し、18 通りで赤を見た（repo のファイルは触らず、読み込んだあとのモジュールの中身を差し替えた）。赤にならなかった 1 通りは害の無い形。下の「柵にしたもの」に書いた。
- 【動】`python3 tools/release_gate.py --ref 09ea633 --skip-ci` の止まりは `release_audit_missing`（`v5.18.0..09ea633` で `wordpress_write` に触る cf08e88 を覆う監査の書が無い）。この書の `range` は cf08e88 を覆う。
- 【読】WordPress の本体のコード（GitHub の `WordPress/wordpress-develop` の trunk）で書き手の前提を確かめた。下の「書き手の前提」に書いた。

回していないもの。

- 全件の試験（頼まれたとおり対象だけ。別に全件が走っている）。
- 本物の WordPress【未】。入口と記事の URL のホストが違うサイト（www あり・なしの両方で応える・別名のホスト）で、`users/me` の `link` が本当に記事と同じホストで返るかは、本物でも Docker でも見ていない。コードを読んだだけ。
- 本物の店【未】。

## 危ない（出す前に直す）

なし。

## 直したほうがよい

1. 【動】**通し方の文が、本物のホストでは通らない。試験が通るのは、試しの loopback だけ。**
   - 場所: `thth/refusal_catalog_part_wp.py` の `wordpress_home_host_mismatch` の「通すには」（「site_url（無ければ blog_id）を、記事の URL のホストにそろえてください」）と、`thth/doctor.py` の `_diagnose_wordpress` の文（「site_url を記事の URL のホストにそろえてください」）。
   - 事実: `principals.valid_site_url` は、loopback の外では `site_url` のホストが `blog_id` と同じであることを求める。`blog_id` が `example.com`・記事の URL が `www.example.com` のサイトで、文のとおりに `site_url` だけを `https://www.example.com` にすると、主体の台帳が `site_url_invalid` で読めなくなる（`thth principal show` も rc 2）。その断りの「通すには」は「site_url を https://<blog_id> の形に直すか、欄を消してください」で、元に戻す向きを言う。2 つの断りが互いを指して回る。
   - 本当に通る道: 主体のブログの行の `blog_id` と、その口座の台帳の `blog_id` の**両方**を記事の URL のホストにする（`site_url` を書いているならそれも）。主体の行だけ替えると `blog_unknown`、両方替えると `whoami` が通る（網に出ない差し替えで確かめた）。どちらも手で JSON を直すことになる（`thth principal set` に `blog_id`・`site_url` を替える旗は無い【読】）。
   - 試験が通る理由: `test_入口を記事のURLのホストにそろえれば鍵が通り出せる` は 127.0.0.1 と localhost の組で、どちらも `valid_site_url` の loopback の抜け道を通る。本物の形のホストの通し方は、柵になっていない。
   - 害: 知らせを受けた利用者が文のとおりに直すと、別の断りで止まり、直し方が分からなくなる。前向きな断りの「通る直し方」が外れている。記事が誤って出ることは無い。
   - 直す案: 「通すには」を「主体のブログの行の blog_id と、その口座の台帳の blog_id を、記事の URL のホスト（上に出たホスト）にそろえてください。site_url を書いている行は、それも同じホストに（または欄を消す）」に。`doctor` の文も同じに。試験に、loopback でないホストの組（`whoami` の読みを差し替える）で「文のとおりに直すと通る」を 1 本足す。

2. 【動】**記事の URL のホストは、診断の記録に残る。コードの注記の「ホストは残さない」は事実と違う。**
   - 場所: `thth/adapters/wordpress.py` の `whoami` の注記（「ホストは残さない（比べて、違うときに画面で言うだけ）」）と、`thth/doctor.py` の `_diagnose_wordpress` の `detail`。
   - 事実: `thth doctor <口座>` は診断を `state/<口座>/doctor.json` に書く（`record_observation`・`OBSERVATION_FIELDS` に `detail` がある）。違いを見つけたあとのファイルには、`detail` の文ごと、記事の URL のホスト（偽では `localhost`）と入口のホスト（`127.0.0.1`）が入っていた。`thth principal auth` だけなら、どのファイルにも残らない（置き場の全ファイルを探した）。
   - 広がり【読】: 同じ `detail` は `doctor --json` の出力、管理の報告（`admin_report._permissions`）、遠くの道の doctor（`server_meta._doctor`）にも載る。どれも持ち主と運営者が読む所。
   - 害: 小さい。ホストは持ち主自身のサイトの公開の名前で、入口のホストは既に主体の台帳にある。ただし privacy の WordPress の節は「users/me からは、利用者名と役割を口座の診断の記録に残します」と、残すものを名指ししている。今のままでは、書いていないものを読み、残すことになる。
   - 直す案: 注記を「違うときは、画面と診断の記録（`doctor.json` の `detail`）にホストが入る」に直し、privacy に下の文を足す。記録に入れない作り（`detail` は符丁だけ・ホストは画面だけ）にする道もあるが、あとで記録を読んで直す人にホストが見えなくなるので、推しは privacy に足すほう。

3. 【動】**Shopify の固定の鍵（`--auth token`）で使っている店は、行の命令をそのまま打つと通らない。**
   - 場所: `thth/morning.py` の `_STORE_TOKEN_SET_ARGS` と `_store_key_unusable_line`。
   - 事実: Shopify の行の命令は `thth shop token set <店> --by <名前>`。`--auth` の既定は `client` なので、client id と secret の 2 行を求める。固定の鍵 1 行を渡すと 2 行めで詰まり、保存されず、知らせは残った。client の鍵の店は、行のとおりで通る。
   - 害: 小さい。固定の鍵の店だけ、行のとおりに打っても直らない。鍵は漏れない。
   - 直す案: `_STORE_TOKEN_SET_ARGS` に `"shopify": " [--auth client|token]"` を足す（`thth shop` の助けと同じ書き方）。試験に Shopify の店の 1 本を足す（今の試験は STORES の店だけ）。

## privacy に足す文（要る）

理由は直したほうがよい 2。`users/me` から読む欄が 1 つ増え、違うときはホストが診断の記録に残る。道は増えないので「道は 4 つ」の文と `tests/test_privacy_matches_code.py` の柵はそのままでよい。

英（WordPress の節）。

- 道の説明を置き換える: `users/me (to check which user the password belongs to and its roles)` → `users/me (to check which user the password belongs to, its roles, and whether the site builds its page addresses on the host name THTH sends requests to)`
- `From users/me THTH keeps the user name and its roles in the account's check record.` のあとに足す: `From the same response THTH also reads the address of that user's author page and compares its host name with the host name it sends requests to. When the two differ, THTH shows both host names on the terminal and keeps them in the account's check record, so that the owner can correct the site's address before publishing.`

日（WordPress の記事の節）。

- 道の説明を置き換える: `users/me（パスワードがどの利用者のものか、その役割は何か）` → `users/me（パスワードがどの利用者のものか、その役割は何か、サイトが頁のアドレスを THTH の送り先と同じホスト名で組んでいるか）`
- `users/me からは、利用者名と役割を口座の診断の記録に残します。` のあとに足す: `同じ応答から、その利用者の著者の頁のアドレスも読み、そのホスト名を THTH が要求を送るホスト名と比べます。違うときは、2 つのホスト名を端末に出し、口座の診断の記録に残します。記事を出す前に、持ち主がサイトのアドレスを直せるようにするためです。`

## 書き手の前提（REST の入口も著者の頁も home の上に組む）

正しい【読】。WordPress の本体のコードで確かめた。

- REST の入口: `get_rest_url()` は `get_home_url()` に REST の頭を足して組む（パーマリンクが無い設定でも home の上の `index.php?rest_route=`）。最後に `rest_url` の filter を通す。
- 著者の頁: `get_author_posts_url()` は `home_url()` で組み、`author_link` の filter を通す。
- `users/me` の `link`: `WP_REST_Users_Controller` が `get_author_posts_url()` の値を入れる。欄の context は `embed`・`view`・`edit` なので、`context=edit` の応答に入る。`/users/me` は、鍵の利用者その人を返す。
- 出所の監査（5.16.0 の 7・監査した者と同じ Fable）は「REST の入口（`siteurl`）と記事の URL（`home`）」と書いたが、これは誤り。入口も home の上にある。だから「通すには」の向き（入口を記事の URL のホストにそろえる）は合っている。
- 違いが起きるのは、利用者が台帳に入れたホストが home と別の名前で、そのホストでも WordPress が REST に応えるとき（www あり・なしの両方で応える・WordPress アドレスのホスト・CDN の裏の元のホスト）。この見立ては読んだだけで、本物では見ていない【未】。

## 気づき

1. 【動】**比べ方の表（29 通り）。** 同じホスト・大文字小文字の違い・ポートの違い・http と https の違いは「同じ」。www の有無・末尾の点（`example.com.`）・`example.com@evil.example`・`example.com.evil.example`・127.0.0.1 と localhost は「違う」。`link` が無い・空・数・配列・scheme の無い URL・`ftp://`・壊れた URL（`http://[::1/…`）・2048 字を超える・253 字を超えるホストは「比べない」（何も言わない）。末尾の点は出たあとの柵でも conflict になるので、誤って止めてはいない。
2. 【動】**日本語の字のままのホスト（`https://日本語.jp/…`）は比べない。** 台帳が punycode で、WordPress が日本語の字のまま返す同じサイトでは、鍵を入れる時点では何も言わず、出たあとの柵が conflict にする（`_host_matches` は字のまま比べる）。知らせが漏れるだけで、誤った成功は言わない。punycode どうしは比べる。直すなら、両方を IDNA に直してから比べる（出たあとの柵も同じに）。
3. 【読】**filter で著者の頁と記事の URL のホストが分かれるサイト。** `author_link` と `post_link` は別の filter なので、多言語の部品などでホストが分かれると、知らせが外れる（言い過ぎ・言い漏れのどちらも）。出たあとの柵はそのまま働く。まれな構えなので、今は何もしなくてよい。
4. 【読】**home が表の別のサーバーで、`/wp-json` を返さない構え（ヘッドレス）。** 「通すには」のとおりに入口を home のホストにそろえると、REST に届かない。この構えは、出たあとの柵がある限り THTH では出せない（前から）。知らせで出す前に分かるようになったのは良い。文に 1 行足すなら「記事の URL のホストで REST が開いていないサイトは、今の THTH では出せません」。
5. 【動】**知らせるのは `principal auth` と `doctor` だけ。** 承認の 1 段目は `users/me` を読まず、ホストの違いは言わない。この版より前に鍵を入れた人は、`doctor` を打つまで気づかない。リリースノートに「WordPress の口座で 1 度 `thth doctor` を」と書くとよい。
6. 【読】**目録の `since` は 5.18.0、注記は 5.18.2。** 書き手の注記のとおり、版上げのときに直す（今は「今の版を越えない」柵のため 5.18.0）。
7. 【動】**morning の漏れの柵は、JSON の 2 つの形だけが効く。** 行に鍵のファイルの中身を足す壊し方で、赤になったのは「BASE の PAT」と「Shopify の client」の 2 本。「読めないファイル」の試験は、ファイルに印（`MARKER`）を入れていないので赤にならなかった。`"{not json" + MARKER` にすると 3 本とも柵になる。
8. 【動】**鍵の置き場がディレクトリ・切れた symlink・権限 000 でも、同じ行を出す。** 「ほかの種類の店の鍵か、読めないファイル」の文で覆えている。ディレクトリのときに `token set` で置き直せるかは見ていない【未】。
9. 【読】**文の小さな点。** Shopify は期限の日が無い鍵なのに「期限を見ずに飛ばしました」と言う。BASE の行は `--expires <期限の日>`、前からある期限の行は `--expires <日付>` で、呼び名が 2 つある。「…で`BASE の PAT`を置き直す」は、前後の空白がそろっていない。

## 柵にしたもの

危ないは無いので、この監査で新しく柵にしたものは無い。下は、書き手が足した柵を、わざと壊して赤を見た記録。

repo のファイルは触らず、pytest の差し込み（会話の置き場の `mutplug.py`）で、読み込んだあとのモジュールの中身を差し替えた。どれも `python -m pytest -q -n 2` で回し、終了コードを読んだ（赤は 1）。

WordPress（`tests/test_v5182_wordpress_home_host.py`）。

| 壊し方 | 赤になった試験 |
|---|---|
| `link` を読まない（比べない） | 鍵を入れる時点で知らせる・doctor も知らせる |
| `link` があればいつも「違う」 | そろえれば通る・同じホストでは何も言わない |
| 比べを逆にする（同じなら「違う」） | 4 本 |
| 違っても `ok` を真で返す | 鍵を入れる時点で知らせる・doctor も知らせる |
| `ROUTES` に道を足す | REST の道は増やさない（`test_privacy_matches_code.py` の「道は ROUTES と同じ」も赤） |
| `WP_REFUSALS` から符丁を抜く | 目録に 3 つがある |
| 目録の「もっと良く」を空にする | 鍵を入れる時点で知らせる・目録に 3 つがある |
| 目録の「通すには」を空にする | 同上 |
| 出たあとの柵を緩める（`_host_matches` がいつも真） | 知らせを聞かずに出しても柵は緩めない |
| `principal auth` の分岐を外す | 鍵を入れる時点で知らせる |
| `principal auth` が違っても rc 0 | 鍵を入れる時点で知らせる |
| `doctor` の分岐を外す | doctor も知らせる |

morning（`tests/test_v5182_store_morning_key_shape.py`）。

| 壊し方 | 赤になった試験 |
|---|---|
| ファイルがあっても黙る（前の動き） | 知らせる 3 本・置き直せば消える |
| ファイルが無くても言う | 鍵のファイルが無い店は何も言わない |
| 行に鍵のファイルの中身を足す | 知らせる 2 本（気づき 7） |
| 行から `--by <名前>` を抜く | 知らせる 3 本 |
| project の絞りを外す | 知らせる 3 本 |
| 「飛ばしました」を言わない | 知らせる 3 本 |
| 行から店の種類の名前（label）を抜く | 赤にならない（鍵の呼び名に「STORES」が入っているため。害は無い） |

柵になっていないもの: 本物の形のホストでの通し方（直したほうがよい 1）・Shopify と BASE の店の行（直したほうがよい 3。今の試験は STORES の店だけで、BASE と Shopify は探りで確かめた）・診断の記録にホストが入ること（直したほうがよい 2。privacy に足すなら、文と記録を結ぶ柵を 1 本）。

## 直しの確かめ（645f37f）

確かめた者は、直した者（Opus の書き手）と別（Claude Fable 5.2）。見た範囲は `git diff b3525a3..645f37f`。コード・試験・ほかの文書は直していない。push・merge・stash・reset・checkout はしていない。本物の WordPress・本物の店・本番（ssh wt）は呼んでいない。`~/.config` の秘密には触っていない。repo に書いたのはこの書だけ（頭の `fixed` を `645f37f` に直し、この節を足した）。探りの台本と出力は会話の置き場 `scratchpad/aud_645f37f/` にある。

途中で 1 度、探りをまとめて打つ命令が権限で断られた。別の書き方では続けずに止まって知らせ、masaru の許しのあと、命令を 1 つずつ分け、`rm` を使わない形で続けた。

### 結論

新しい危ないは 0 件。直したほうがよい 3 件のうち、1 と 3 は閉じた。2 は一部（コードの注記と柵は直った。privacy の文はまだ入っていない）。気づき 7 は閉じた。新しい直したほうがよいが 1 件（直しが開けた穴ではなく、前からあるもの）、新しい気づきが 4 件。

| 項目 | 結果 | 確かめ |
|---|---|---|
| 直したほうがよい 1（通し方の文） | 閉じた | 【動】 |
| 直したほうがよい 2（ホストが記録に残る） | 一部 | 【動】注記と柵。privacy の文は入っていない【読】 |
| 直したほうがよい 3（Shopify の固定の鍵） | 閉じた | 【動】 |
| 気づき 7（漏れの柵は 2 本だけ） | 閉じた | 【動】 |
| 気づき 1〜6・8・9 | 開いたまま | この直しの範囲の外（書き手も触っていない）【読】 |

### 動かして確かめたこと

1. 【動】**通し方の文（1）。** `blog_id` が `example.com`・記事の URL が `www.example.com` の組（網には出ない。`users/me` の読みを差し替えた）。
   - `thth principal auth` は rc 1 で、文は「主体のブログの行の blog_id と、口座の台帳の blog_id の両方を」と言う。`thth doctor` の文も同じ道を言う。
   - 文のとおりに両方の `blog_id` を `www.example.com` に直すと、**鍵を打ち直さなくても** `thth doctor` が rc 0・`wp_users_me` が ○。
   - `site_url` を書いている行: `https://www.example.com`・末尾に `/`・パスつき（`/blog`）は通る。古いホストのまま、または `http://` だと `site_url_invalid`（文の言うとおり）。欄を消す形も通る。
   - 偽の WordPress（127.0.0.1 と localhost の組）では、知らせ → 入口をそろえる → 出す、で rc 0・記事が 1 本出て conflict にならない。届いた道は `users/me`・`categories`・`tags`・`posts` だけ。
2. 【動】**ホストが残る所（2）。** `thth principal auth` のあと、試しの置き場のどのファイルにも記事の URL のホストは無い。`thth doctor` のあとは `state/<口座>/doctor.json` の 1 つだけに入る。`doctor --json` の出力にも載る。鍵の値はどの出力にも出ない。コードの注記はこの事実と合っている。管理の報告と遠くの道の doctor に `detail` が載ることは読んだだけ【読】。
3. 【動】**Shopify の行（3）。** 行は `thth shop token set <店> [--auth client|token] --by <名前>` と、どちらを選ぶかの一文を言う（`thth shop` の助けと同じ書き方）。壊れた鍵の形 6 通り × 鍵の種類 2 通りの 12 本で、選んで打つと rc 0・知らせが消え、鍵の値と印は出力に出ない。別の project の 1 枚には出ない。BASE と STORES の行は前のまま（前の監査の探りを回し直した。赤は前と同じ探りの側の書き誤りの 1 本だけ）。
4. 【動】**試験。** `python -m pytest -q -n 2` の終了コードを読んだ。
   - 対象と周り（`tests/test_v5182_wordpress_home_host.py`・`tests/test_v5182_store_morning_key_shape.py`・`tests/test_v5182_store_morning_shopify_auth.py`・`tests/test_v5160_wordpress.py`・`tests/test_privacy_matches_code.py`・`tests/test_refusal_catalog.py`・`tests/test_store_shopify.py`・`tests/test_store_stores.py`）: 300 本が緑・rc 0。
   - 全件: 12298 本が緑・4 本が飛ばし・rc 0（52 分。ほかの仕事で機械が混んでいた）。

### 柵をわざと壊して赤を見た

repo のファイルは触らず、pytest の差し込み（置き場の `mutplug2.py`）で、読み込んだあとのモジュールの中身を差し替えた。壊さない回しは 17 本が緑・rc 0。壊した回しは 8 通りで、7 通りが赤（rc 1）。差し込みを外せば元に戻る（repo に変更は無い）。

| 壊し方 | 赤になった試験 |
|---|---|
| 目録の「通すには」を前の文（site_url だけ）に戻す | 本物の形のホストでも文のとおりに直せば通る |
| `doctor` の文を前の形（site_url をそろえる）に戻す | 同上 |
| `valid_site_url` のホストの見比べを外す（site_url だけ替える道が通る） | 同上 |
| `doctor` の `detail` から 2 つのホストを抜く | 違うときのホストは画面と診断の記録に入り… |
| `principal auth` が state にホストを書き残す | 同上 |
| Shopify の行から `[--auth client|token]` を抜く | Shopify の店の行は…（client・token の 2 本） |
| 行に鍵のファイルの中身を足す | 鍵の形の 3 本（BASE の PAT・Shopify の client・読めないファイル）と Shopify の 2 本。**気づき 7 は閉じた** |
| Shopify の行から、どちらを選ぶかの一文を抜く | **赤にならない**（旗があれば命令は通る。害は小さい。下の気づき 13） |

### 新しく見つけたこと

危ない 0・直したほうがよい 1・気づき 4。番号は上の続き。

直したほうがよい。

4. 【動】**`thth principal show <WordPress の主体>` が落ちる。断りの「通すには」が案内している命令。**
   - 場所: `thth/principal_cli.py` の `status`（187 行め `"hatena_id": p["hatena_id"]`）と `cmd_show`（217 行めの「はてな ID」）。
   - 事実: 手を入れていない偽の WordPress の主体（鍵は置いた）で、`thth principal show wp-main` と `--json` のどちらも `KeyError: 'hatena_id'` で落ちた。WordPress の主体の台帳に `hatena_id` は無い（`wp_user`）。この行は 4.0 のころからあり、5.16.0 で WordPress の主体を足したときから落ちる。645f37f が開けた穴ではない。
   - 広がり【読】: `thth/refusal_catalog_part_wp.py` の断りのうち 8 つが、次の一手の命令に `thth principal show <主体>` を出す。`wordpress_home_host_mismatch` もその 1 つで、直したあとの文の末尾にも付く。
   - 害: 断りの案内のとおりに打つと、台帳が見える代わりに Python の例外が出る。記事が誤って出ることは無く、鍵も漏れない。前向きな断りの「通る直し方」が 1 つ外れている。
   - 直す案: `status` と `cmd_show` を主体の種類の表（`ARTICLE_KINDS` の `id_field`）から引く。試験に「WordPress の主体で `principal show` と `--json` が rc 0」を 1 本。

気づき。

10. 【動】**記事の URL のホストが `blog_id` の形に入らないと、文のとおりに直しても通らない。** 末尾に点（`example.com.`）・点の無い名前（`wiki`）・IPv6（`::1`）は、知らせは出るが、そのホストを `blog_id` にすると `blog_id_invalid` で台帳ごと読めなくなる。IP アドレス（`192.168.0.10`）・punycode（`xn--…`）は通る。ポートつき（`www.example.com:8443`）は、ホストだけ（`www.example.com`）を言い、そろえると通るが、入口は 443 になる（もともと `site_url` はポートを許さない）。どれもまれ。直すなら、形に入らないホストのときは「このホストは今の THTH では入口にできません」と言う。
11. 【動】**入口や `blog_id` を替えると、替える前に承認した原稿は `approval_stale` で出ない。** 柵としては正しい（指紋に `blog_id` と `site_url` が入る）。記事は 1 本も出なかった。文に「承認ずみの原稿は承認し直します」の一言があると迷わない。
12. 【読】**同じブログの行を 2 つ以上の口座が使うとき。** 文は「その口座の台帳」と 1 つを言う。もう 1 つの口座の `blog_id` を替え忘れると、そちらが `blog_unknown` になる。まれ。
13. 【動】**Shopify の行の「どちらを選ぶか」の一文は柵になっていない。** 抜いても試験は緑。`[--auth client|token]` を字のまま打つと使い方の誤りで rc 2（保存しない・漏れない）。`--auth` を付けずに固定の鍵 1 行を渡すと、前と同じく 2 行めで詰まる（文が選び方を言うので、行のとおりではない打ち方）。

### 未確認

- 本物の WordPress・本物の店【未】（前と同じ。偽と差し替えだけ）。
- privacy の文【未】。この直しには入っていない（書き手は「まとめるときに入れる」と書いた）。入れたあと、文と記録を結ぶ柵を見る。
- 管理の報告・遠くの道の doctor に `detail` が載ること【読】。
- CI と release の関門【未】。push していないので CI は走っていない。
