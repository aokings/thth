# 調べ: X の expansions で付く物の数え方（2026-11-28・書き手 A）

きっかけ: X から 11/6 に「X API Pay-Per-Use: expansions will count toward usage」という知らせ。THTH の X の読み取りの見積もり（`thth/budget_x.py`）は、`expansions` で `includes` に付く物を数えていなかった。

## 1. 一次資料（X 自身の物）で読めたこと

### 1-1. docs.x.com の料金表（11/28 に読んだ）

https://docs.x.com/x-api/getting-started/pricing （と同じ中身の `pricing.md`）

- 読み取りは「返ってきた resource 1 件ごと」、書き込みは「要求 1 回ごと」。
- Posts: Read 0.005 USD／resource、User: Read 0.010 USD／resource。
- 24 時間（UTC の 1 日）の中では同じ resource は 1 回だけ数える。ただし「soft guarantee」（必ずではない）と書いてある。
- Owned Reads（0.001 USD／resource）の表に載るのは `GET /2/users/{id}/…` の 12 本だけ。**`GET /2/tweets`（id で引く）は載っていない。**
- **11/28 の時点で、expansions・includes・Media: Read の行はまだ無い。**

### 1-2. docs.x.com の Usage and Billing・Expansions・changelog（11/28 に読んだ）

- https://docs.x.com/x-api/fundamentals/post-cap.md — 投稿の数え方と重複の除き。expansions の話は無い。
- https://docs.x.com/x-api/fundamentals/expansions — `author_id` で user、`attachments.media_keys` で media が付く。お金の話は無い。
- https://docs.x.com/changelog — 10 月〜11/19 の項に expansions の話は無い。

### 1-3. X の docs の repo の書きかけ（xdevplatform/docs PR #520・まだ merge されていない）

https://github.com/xdevplatform/docs/pull/520 （`gh api` で本文と差分を読んだ。10/28 に開かれ、10/29 が最後の更新。「本番で課金が始まるまで merge しない（予定 2026-11-22）」と注記あり）

X の docs の担当が書いた文で、merge されたら docs.x.com の料金表に載る予定の中身:

- **11/22 から**、`includes` に入る物（post・user・media・poll・place・topic）を、直に引いたときと同じ 1 件ごとの単価で数える。
- 単価: user は User: Read 0.010、media・poll・place・topic は新しく 0.005、post は 0.005。
- 1 つの物が 1 件。写真 4 枚の投稿に `expansions=attachments.media_keys` を付ければ media 4 件。
- 例: 検索で投稿 100 本・投稿者 60 人・media 40 件なら 100×0.005＋60×0.010＋40×0.005＝1.30 USD。
- Owned Reads の `GET /2/users/{id}/tweets`・`/mentions`・`/liked_tweets` に限っては、付く物も 0.001 で、本体と一緒に重複を除く。ほかの Owned Reads の口については書いていない。
- 24 時間の重複の除きは付く物にも効く。
- 「使う expansions だけ頼む」ように、と勧めている。

### 1-4. 1 本の投稿に付く media の数

https://docs.x.com/x-api/posts/manage-tweets/integrate — 「写真 4 枚、GIF 1 つ、動画 1 本まで」。

## 2. 第三者の物（出所を分ける・数字の根拠にはしない）

- X の開発者フォーラムの知らせ https://devcommunity.x.com/t/inform-x-api-ppu-expansions-update/279110 — **403 で読めなかった**。検索の要約だけ見た（「付く物も他の物と同じに数える」「新しい料金の型ではない」）。
- https://dev.to/appierun_66/x-api-expansions-start-billing-on-22-nov-2026-what-it-does-to-social-listening-and-reply-tool-29mj ・ https://api.sorsa.io/blog/twitter-api-pricing-2026 — 11/22 開始・user 0.010・media 0.005 と書く。上の PR と同じ数字。

## 3. 確かめられなかった所【未確認】と、見積もりでの倒し方

| 所 | 状態 | THTH の見積もり |
|---|---|---|
| 11/22 に本当に始まったか | 【未確認】docs.x.com はまだ書いていない（PR が merge されていない） | 始まった物として数える |
| 24 時間の重複の除きが付く物に効くか | 書きかけには「効く」・料金表は「soft guarantee」 | **当てにしない**（返った物は毎回数える） |
| `GET /2/tweets?ids=`（THTH の自分の投稿の数字）の media の単価 | 【未確認】この口は Owned Reads の表に無く、付く物を 0.001 にする口にも入っていない | **0.005**（Owned の 0.001 にしない） |
| 付く投稿者が投稿の本数より多くなるか | `author_id` は 1 本に 1 人なので起きない形 | 押さえは 1 本 1 人。超えたら確定せず押さえた上限のまま（uncertain） |
| 知らない `includes` の鍵 | 料金表に無い | user と同じ 0.010 で数える |
| 付く物が月 300 万本の投稿の上限に入るか | 【未確認】 | 上限の数は THTH では数えていない（今までどおり） |

## 4. 直したこと（5.33.0）

- 付く物の台帳を別のファイル `budget_x_expansions.json` にした（単位 0.005・user 2 単位・media 1 単位・版 `2026-11-22-expansions`）。投稿の台帳の形と版は変えていないので、VM に残る古い行はそのまま読める。
- 読む前に、投稿の上限と付く物の上限を同じ錠の中で一緒に押さえる（`expansion_units`）。月の上限は他の台帳と分け合う。1 日の本数（`x_daily_reads`）には数えない。
- 応答の `includes` の件数で確定する（`included_units`）。読めない応答・読めない形は押さえた上限のまま。断りの応答（4xx・5xx）は 0。
- `budget_x.EXPANSION_RULES` に無い expansion は、要求も予約もせずに断る（`expansion_not_priced`）。
- 全期間の検索（`archive`）は投稿者の handle しか使わないので、`user.fields` を `username` だけにした。値段は変わらない（数えるのは user の件数で、欄の数ではない）。検索・枝・リストはフォロワー数・投稿数を画面と `--json` に出すので残した。`author_id` の expansion は、どの命令も permalink と仮名（`author_key`）に username を使うので外せない。

## 5. 続けて直したこと（5.33.0・第24 の依頼）

最初の commit（f0708585）のあとで気づいた、少なく見積もる側の穴 3 つ。

- `recent_posts`（`GET /2/users/:id/tweets`・最大 100 本）を、本人確認と同じ 1 回 0.010 で押さえていた。今は `owned_read` で `max_results` 本ぶんを押さえ、返った本数で確定する。読めない応答は押さえた上限のまま、断りの応答は 0。本人確認の口（`READ_PATH`）からこの path を外したので、1 回 0.010 では通らない。
- 自分の投稿の読みを Owned Reads の 0.001 で数えていた。でも `GET /2/tweets?ids=` は料金表の Owned Reads の表に無い（`docs/照合_XのDeveloperAgreement_自分の泉_2026-09-16_Codex.md` の 192 行目と同じ）。`/2/users/{id}/tweets` も「自分の developer app の持ち主＝認可した人」のときだけで、THTH では確かめられない。そこで 1 本 0.005 にした（版 `2026-11-28-owned-read-standard`）。前の版 `2026-10-12-owned-read` の行は `_LEGACY_PRICES` によって 0.001 のまま検める。この形の行（確定・押さえ・uncertain・released）が読めることは、直す前と直した後の両方で試験で確かめた（VM の現物は見ていない）。
- where・thread の 1 行を「約 $0.070（投稿 $0.050＋投稿者の分 $0.020）」にした。投稿者の分も入った金額だと分かる。

## 6. まだ残る所

- `docs/使い方_プロジェクトのセッション向け_2026-09-09.md` などの使い方の文書には、まだ「1 本約 0.001 USD」と書いてある（範囲の外なので直していない）。
