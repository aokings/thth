---
name: thth-ads
description: THTH で Meta の広告（Facebook・Instagram）を 1 本出すとき、出した広告を見張る・成績を読む・止めるときに読む。目的の決め方から見積もり・ピクセルと UTM の確かめ・作る・人の承認・見張り・成績の読み方・止め方・報告までの順と、お金の柵。
---

# 広告を 1 本出す（thth ads）

届ける流れ全体（有機の投稿・セルクマ・広告に進む条件・印の決まり）は skill `thth-flow` にある（別の書き手が作る）。ここは広告の 1 本だけ。

広告の命令は `thth ads …`。金額は**最小単位の整数**（JPY なら円）。この手順の本文は `thth ads skill --print` でいつでも出せる。広告の命令の「次の一手」（json の `next_steps`）とエラーの文は、先頭でこの skill を示し、そのあとに個別の一手を書く。`next_steps` は上から順に読む。

## お金の柵（先に読む）

- **お金が動くのは配信を始めたときだけ。** 始める `thth ads activate` と延ばす・増やす `thth ads extend` は **2 段**で、2 段目に人（masaru）の承認の hub の番号が要る。LLM は 1 段目を打って digest と木を人に見せ、承認を待つ。**MCP には無い**（MCP の広告の道具は `thth_ads_show`・`thth_ads_boost`・`thth_ads_pause`・`thth_ads_stop` だけ）。
- **上げる命令は無い。** `spend_cap` を上げる・外す、月の上限・1 操作の上限（`lifetime_budget_max`・`days_max`）・`ads_enabled`・`production` を上げるのは THTH の外（`spend_cap` は管理画面で masaru、上限と旗は VM の台帳）。頼まれても THTH で上げる道を探さない。
- **`thth ads resume` は止めの印を外すだけの例外**（masaru 11/02）。2 段＋hub の番号が要り、広告は PAUSED のまま。外したあと配信するには、もう一度 `activate` の 2 段を通す。
- **`spend_cap` の変更は 1 日 10 回まで**（Meta の決まり）。`stop` は `spend_cap` を使った額の近くまで下げるので、同じ日に `stop` を何度も打ち直さない。
- 止める（`thth ads pause`・`thth ads stop`）は承認なしで通る。迷ったら止める側に倒す。

## 0. 口座の用意（人が VM で）

広告の口座の台帳を書く `thth ads add` と鍵を取る `thth ads auth <広告の口座> --by <名前>` は人が VM で打つ（MCP には無い）。用意ができたかは `thth ads doctor <広告の口座>` で見る。

## 1. 目的を決める

人と次のどれかを決める。`thth ads boost` の `--objective` に入る。

- **反応**（いいね・コメント・シェア）: `--objective engagement`（本番で確かめた）。
- **サイトへの誘導**: `--objective traffic`（本番未確認）。ピクセルと UTM を手順 3 で確かめる。
- **認知**（たくさんの人に見せる）: `--objective reach`（本番未確認）。

## 2. 出す前の見積もり（読むだけ）

```
thth ads estimate <広告の口座> --targeting '{"geo_locations":{"countries":["JP"]},"age_min":18}' --goal LINK_CLICKS --json
```

- `reach.users_lower_bound`〜`reach.users_upper_bound`: 狙う相手の人数の幅。-1 は「出せない」で、配信が止まる意味ではない。
- `delivery.daily_outcomes_curve`: 予算あたりの見込み。`delivery.confident` が false なら Meta に確度の高い予測が無い（全部 0 の 1 点）。
- `--goal` は `POST_ENGAGEMENT`・`LINK_CLICKS`・`LANDING_PAGE_VIEWS`・`REACH`・`IMPRESSIONS`。

## 3. サイトへの誘導なら: ピクセルと UTM を確かめる

**ピクセルが要るかの判断の表**（入れるかは広告を出すときにこの表で決める）:

| 広告の目的 | ピクセルが要るか | 無いとどうなるか |
|---|---|---|
| 反応（`--objective engagement`・最適化は POST_ENGAGEMENT） | 要らない | 何も欠けない（反応は `engagement` で読める） |
| サイトへの誘導（`--objective traffic`・最適化は LINK_CLICKS） | 無くても配信できる | `link_clicks`・`outbound_clicks` は読める。`landing_page_views`（頁が開いた）が測れず `not_measured_or_left` になる |
| 頁が開いた数で最適化（LANDING_PAGE_VIEWS） | 要るものとして扱う | THTH の boost はこの最適化で作らない（見積もり `--goal LANDING_PAGE_VIEWS` だけ）。Meta の公式の文書でピクセルが要るとは確かめられていない |

ピクセルをサイトに入れると Meta への外部送信が増える（サイトのプライバシーの頁の更新も要る）。**入れる前に masaru へ 1 行**（何のサイトに・どのピクセルを・なぜ）を送り、返事を待つ。

1. `thth ads doctor <広告の口座> --json` の `next_steps` を読む（`thth ads show <広告の口座> --json` にも同じ欄がある）。
   - `kind: pixel_create` … 広告アカウントにピクセルが無い。人にイベントマネージャでピクセルを作ってもらう（THTH からは作らない）。
   - `kind: pixel_install` … ピクセルはあるが動いた記録が無い。**誘導先のサイトの repo で**、文に出ているピクセルの id の基本のコードを全部の頁の `<head>` に入れる（入れ方は文に出る公式の文書）。THTH はサイトのコードに触らない。
   - `kind: pixel_check_page` … ピクセルは動いている。誘導先の頁にもコードが入っているかを確かめる。
   - `kind: landing_url_unknown` … 誘導先の URL を `--landing-url` か台帳の `landing_url` に書くと、文に URL が入る。
2. 入れたら `thth ads doctor <広告の口座>` を打ち直し、「ピクセル」の行が ○（最後に動いた時刻が出る）になるのを見る。
3. **UTM**: 有機の投稿と同じ鍵にそろえる（10/27 に決まった形）。有機は `utm_source=<媒体>&utm_medium=social&utm_campaign=<原稿のファイル名、拡張子なし>`、広告は `utm_medium=paid` で campaign は同じ。`thth ads boost` の `--utm-campaign <元の投稿の原稿のファイル名、拡張子なし>` で `utm_source=<出し先の 1 つ目>&utm_medium=paid&utm_campaign=<それ>` を組む。違う形が要るときだけ `--url-tags 'key=value&key=value'`（無ければ台帳の `url_tags`）。どれもクリエイティブの `url_tags` として送られ、広告から押されたリンクに付く。口座名を campaign にしない（記事ごとに分けられない）。

## 4. 作る（全部 PAUSED・お金は動かない）

```
thth ads boost <広告の口座> --post <post_id> --lifetime <額> --end <終了の時刻> --objective traffic \
  --utm-campaign <原稿のファイル名> --landing-url <誘導先の URL> --by <名前> --json
```

- 投稿は `--post <post_id>`（THTH で出したページの投稿）か `--object-story-id <ページID>_<数字>`。
- `--end` は必須。1 操作の上限（通しの予算・期間）を越える指定は作らない。`--platforms facebook,instagram` で Instagram にも出す。
- 出力の `created`（キャンペーン・広告セット・クリエイティブ・広告の id）と `next_steps` を読む。サイトへの誘導でピクセルが動いていなければ、ここでも手順 3 の一手が出る。

## 5. 人の承認を得て始める

1. `thth ads activate <広告の口座> <campaign_id> --by <名前>` … 1 段目。digest と木（予算・期間・広告）が出る。**何も変わらない。**
2. digest・予算・期間・見積もりを人に見せ、hub で承認の番号をもらう。
3. `thth ads activate <広告の口座> <campaign_id> --confirm <digest> --approval <hub の番号> --by <名前>` … 2 段目（人か、人が許したときだけ）。Meta から木を読み直し、変わっていれば `ads_tree_changed` で断る。

## 6. 見張る

```
thth ads watch <広告の口座> <campaign_id> --stop-at-spend <額> --by <名前>
```

- `--stop-at-spend <額>` か `--stop-at-impressions <数>` に達したら `thth ads stop` と同じに止める。指定なしなら配信が始まった（表示が 1 以上）ら知らせて終わる。`--interval <秒>`・`--until <時刻>` で間隔と終わりを決める。
- 終わりの表示に反応とサイトへの誘導の数も出る。
- 普段の見張りは `thth ads show <広告の口座>` と `thth events` の要対応（`ads_stop_unconfirmed`・`ads_active_outside_thth` は最優先）。

## 7. 成績を読む

```
thth ads insights <広告の口座> --preset maximum --level campaign --json
thth ads insights <広告の口座> --preset last_7d --by age-gender
```

- `--preset` の `last_7d` などは**今日を含まない**。今日の分は `--preset today` か `--preset maximum`。`--since YYYY-MM-DD [--until YYYY-MM-DD]` でも読める。
- **反応** `engagement`: `reactions`（いいね・リアクション）・`comments`・`shares`・`photo_views`・`page_likes`。
- **サイトへの誘導** `site`: `link_clicks`・`outbound_clicks`（サイトへ出たクリック）・`landing_page_views`（頁が開いた）・`inline_link_clicks`。
- **1 件あたり** `cost_per`: 上の数ごとの spend ÷ 件数（0 件・不明は null）。
- **効率** `efficiency`（Meta の計算済み）: `frequency`・`cpm`・`ctr`・`cpc`・`cost_per_unique_click`・`inline_link_click_ctr`・`cost_per_inline_link_click`・`outbound_clicks_ctr`・`cost_per_outbound_click`。
- **動画** `video`: `plays`・`p25`〜`p100`（見られた深さ）・`avg_time_watched`・`cost_per_thruplay`。動画でない広告では null。
- **内訳** `--by age-gender|region|country|placement|device|hour`: 1 区分 1 行。内訳つきは効率・動画を出さない。`hour` では reach が 0（Meta の決まり）。
- `actions`・`outbound_clicks_raw` は Meta の生の行。

**0 と不明を分けて読む**（`site.why` と `engagement_unknown`）:

- `reported`: Meta が返した数。`zero_reported_list`: 並びに行が無い＝0（Meta は 0 の行を出さない）。
- `no_link_clicks`: リンクが 1 度も押されていないので本当に 0。
- `not_measured_or_left`: リンクは押されたのに頁が開いた記録が無い。**0 と書かない**。頁の計測（ピクセル）が無いか、開く前に離れた。このとき `next_steps` にピクセルの一手が出る。
- `actions_absent_as_zero`（行の `actions_absent_as_zero: true`）: その行に `actions` の欄が無いが、同じ読みのほかの行には返っている＝反応が 1 件も無かった区分（Meta は欄ごと返さない）。反応もリンククリックも頁が開いた数も 0（5.7.0）。ただしその行の `inline_link_clicks` が 1 以上なら 0 と見なさない（押されているので不明のまま）。
- `actions_missing`・`outbound_clicks_missing` など、値が null: 欄が無い＝不明。`actions` は**読み全体で 1 行も返らない**ときだけ不明（内訳なしの 1 行の読みもこちら）。
- `clicks` は広告のどこを押しても数える。**リンクのクリックではない**。サイトへの誘導は `site` を読む。
- 品質の評価（quality_ranking など）は THTH では読まない（公式の欄の一覧で名前を確かめられていない）。

## 8. 止める

- 全部止める: `thth ads stop <広告の口座> --by <名前>`。全部 PAUSED → Meta から読み直して確かめる → `spend_cap` を下げる → THTH の止め。「止まった」と出なければ管理画面で止めてもらう（出力の `waiting_on_masaru` の 1 行を hub へ）。
- 一部だけ: `thth ads pause <広告の口座> --id <id>`（`--all` で全部）。
- 減らす: `thth ads budget lower <広告の口座> --adset <id> --lifetime <額>`。早く終える: `thth ads end <広告の口座> <adset_id> --end <時刻|now> --by <名前>`。
- 今月の見積もり: `thth ads budget show <広告の口座>`（Meta の請求書ではない）。

## 9. 人への報告

1 本ごとに、次を短く渡す: 目的・期間・使った額（`spend`）・表示とリーチ・反応の内訳・サイトへの誘導（リンククリック・サイトへ出たクリック・頁が開いた）と 1 件あたり・**0 か不明か**（不明ならその理由と `next_steps` の一手）・止めたか。数は `thth ads insights … --json` の値をそのまま書き、推して埋めない。
