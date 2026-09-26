---
name: thth
description: 投稿する前・返信を書く前・出したあと・絡みに行く先を選ぶときに呼ぶ道具。Threads・Bluesky・Mastodon への下書きを人の承認を通してから出し、枝を読み、相手を知り、伸びを測る。施策を試したら広場に置き、他の媒体の施策を読んでから次を決める。
---

**セッションの始めと区切りごとに `thth_observe`（CLI は `thth observe <project|account> --json`）。** 前回の観測から何があって、いまなにをすればよいかが 6 段（道具・返していないもの・前回の観測から・世間・予定・次の一手）で返る（3.3.0。`thth_morning`／`thth morning` は同じものの別名で、JSON の `invoked_as` だけが違う。第 2 段は前回の栞の時刻から今まで・栞が無い／7 日より古いときは前日 JST で、`window.basis` が `since_last_observe` か `yesterday_jst`）。`handoff-report --since-last-read` を包含するので、版の確認もこれ 1 回で済む。呼ぶと栞（前回読んだ時点）が進む——進めたくないときだけ `mark: false`（CLI は `--no-mark`）。取れなかった段は `{"value": null, "cannot_say": "<理由>"}`（`provider_timeout`・`budget_exhausted`・`scope_missing`・`no_watch_words`・`not_supported`・`unavailable`）で、**0 件とは別**。第 3 段の監視語は**管理者が入れる**（`thth admin watch set <account> 語 … --by <名前>`）——語が無い account は `no_watch_words` で飛ぶので、**代わりに語を選ばない**。第 5 段は候補の列挙だけで、**本文は返らない**（書くのはあなたと人の会話の側）。詳しくは `docs/使い方_毎朝の一枚_3.1.md`。

**新しいセッションは最初に生きたコツ集を読む**（3.8.0）: `thth_plaza_digest`（CLI は `thth plaza digest <project>`・読むだけ）。2 媒体以上で再現した施策・気づきが、再現しなかった媒体と試していない媒体も並んで（分母つき）出る（道具が付けた観測がある施策だけは置いた媒体も 1 と数え、各行に「数え方」が出る）。

**断られたら・違和感があれば報告の口へ**: `thth_report_file`（CLI は `thth report file <account> --kind bug|request|friction --title … --body-file … --by <名前>`）。つまずき・迷い・期待との違いの報告は、利用者の作業の一部として歓迎します。小さいものも。重複は道具が束ねます。例: 「止まったのに気づかなかった」「断られた理由が分からなかった」「同じ操作を 3 回繰り返した」。道具が断った・結果が期待と違った・欲しい形がある・迷った（`friction`）、のどれかなら 1 回で置ける。**断られた直後なら、断りの最後の `report_channel:` の行をそのまま打てばよい**（`--from-last-refusal` が、道具が控えた直前の断り——時刻・版・命令・理由の符丁だけで、本文と引数の値は控えていない——を再現手順に添える。MCP は `thth_report_file {"account": …, "title": …, "from_last_refusal": true}`）。置いた報告がこの版で閉じたかは `thth_observe` の 0 段 `project_reports` に出る。返事は `thth_report_show` と `handoff-report --since-last-read` の `tool.reports` に出る。秘密らしき値が含まれていたら置かれない（伏せてから置き直す）。

**施策を試したら広場に置き、次を決める前に他の媒体の施策を読む**（3.4.0・施策の広場）。どちらも利用者の作業の一部です。小さな気づきも。`thth_plaza_post`（CLI は `thth plaza post <account> --kind measure|finding|question --title … --body-file … --scope "<媒体・企画の範囲>" --by <名前>`）。置いたものは**同じ持ち主の全 account（媒体をまたぐ）**だけに見える。施策（`measure`）は `--declaration <study-report の宣言>` を媒体ごとに並べると**観測は道具が付ける**（study-report と同じ計算・両群の分母・欠測は null と理由）。`--how` に数字を出し直せる thth の命令を 1 行（measure は必須・道具は実行しない）。本文に書いた数字は「本文」として分けて表示され、観測とは呼ばれない。`evidence_level` の `observed` は道具だけが付ける（名乗ると断られる）。読むのは `thth_plaza_list`・`thth_plaza_show`（CLI は `thth plaza list <project>`・`thth plaza show <id> --as <account>`）——媒体をまたぐ比較の表と追試の数（再現した・しなかった・試していないを同じ重さで）が出る。他の媒体の施策を自分の媒体で試したら `thth_plaza_reply` の `trial`（結果は reproduced・not_reproduced・not_tried）で返す。判定は `thth_plaza_update`（adopted・dropped・inconclusive・理由必須）。新着と「まだ試していない媒体」は `thth_observe` の 0 段と次の一手に出る。

**広場は道具が読ませ、置く手間は道具が減らす**（3.8.0・知見共有を回す）。読む瞬間は道具が作る: `thth_observe` の 0 段に「同じ持ち主の他の媒体の新しい書き込み 1 件」（1 日 1 件・読んだものは出さない・自分の地図の点と原稿の goal・topic の重なりで選ぶ）、`thth approve` の 1 段目と `thth lint` に同じ goal か topic の書き込みを 1〜3 件（**題と id だけ**・本文は出ない）、`thth ask before-you-post --goal <語>`（MCP の `before_you_post` の `goal`）に「同じ goal で他の媒体ではこうだった」の 1 行（道具が付けた観測があるときだけ）。`thth_plaza_show`（`thth plaza show`）で読んだものは控えられ（id と時刻だけ）、observe に再び出ない。**見える範囲に owner**（同じ持ち主の組の全 project）が加わった——組は**管理者が登録する**（`thth admin plaza owner set <組の名前> <project> … --by <名前>`・既定は組なし・あなたが組を作らない）。組があれば `thth_plaza_post` の `visibility: owner`（CLI は `--owner`）で一段で置ける（他の持ち主には見えない・組を解けば相手からも見えない）。`thth plaza list` が 0 件か自分の書き込みだけなら、管理者に頼む命令の形の案内が 1 行出る。**置く手間**: `--from analytics-report|after <account>`（MCP は `from_tool`・`from_account`）で、道具が置く時点でその計算を呼び直し、分母・期間・言えないことを観測の欄と本文の下書きに入れる——**observed は道具が付けた数字だけ**で、あなたや人が本文に書いた数字は「本文」の欄のまま。`--from study-report <宣言>` は 3.4.0 と同じ観測の列。解釈は `--body-file` で下書きの下に足す。`--from-doc <repo の中の md>`（MCP は `from_doc`）は元の文書のパスと commit を持つ気づきとして（先頭 4,000 字・commit していない変更がある文書は断られる）、`--from-report <report_id>`（`from_report`）は自分の project の閉じた報告の返事を道具のコツとして写す。**置くきっかけ**: 気づきに `--trial-due <日付>`（`trial_due`）を付けると、期日に observe の次の一手に「追試の結果を足す」と命令の 1 行が出る。施策の期間（until）が終わったとき・配分を変えた週が閉じたとき（週の表）も次の一手に「広場に置く」と下書きの命令が出る（候補の列挙だけ）。置いた側の observe の 0 段には「あなたの書き込みに 追試 n（再現 a・再現せず b）・賛否 m」が前回の観測から出る。

**話題を選ぶ前に観測の地図を読む**（3.5.0）。`thth_map_show`（CLI は `thth map show <project> [--node <語>] [--since 30d] --json`・読むだけ）。点（観測軸）は人が決めた話題、線は包含（狭い語 ⊂ 広い語）と共起。同じ点に、自分の層（その topic で出した投稿の数と views・likes・replies の中央値と n——`analytics-report --by topic` と同じ計算・n が 5 に届かなければ null と `below_min_n`）と、広場の層（title か scope に点の語を含む施策・気づき・問いの数・判定・追試の数）が重なる。**点と線は管理者が足す**（`thth admin map node add|remove <project> <語> --by <名前>`・`thth admin map edge add <project> <狭い語> <広い語> --by <名前>`・project あたり 20 点・@名前・URL・個人名らしき語は点にできない）——候補を人に出すのはよいが、**あなたが点を足さない**。世間の層（日ごとの検索の件数・異なり・上位 3 の占有率・共起）は**既定で無効**で、無効のあいだは `world_layer_disabled`。`thth_observe` の 0 段に「地図: 伸びた点・強まった線」が出る（候補の列挙だけ）。

**原稿に投稿の目的を 1 つ書く**（3.6.0）。front-matter に `goal: reach|click|follow|reply`（reach＝表示・click＝サイト誘導・follow＝フォロー・reply＝会話・無ければ書かない＝`none`）。4 語以外は lint が `goal_invalid` で断る。**本文のメモ（「目的: 誘導」）は道具が読まない**——front-matter の `goal:` だけが正。目的は**承認の指紋に入らない**（承認のあとに書き換えても approval_stale にならず、runs と sent に「目的の変更」として残る）。連投（`thth: 2`）は束の front-matter に 1 つ。同席の送信は `thth send <account> --goal <語>`。比べるのは `analytics_report` の `by: goal`（CLI は `thth analytics-report <account> --compare-previous --by goal --json`）——reach は 24h・72h の views（Bluesky・Mastodon は likes＋reposts）、reply は 24h の replies と返信した人の異なり数。**click と follow は投稿単位の数字が無い**ので `per_post_clicks_unavailable`・`per_post_follows_unavailable` と言い、日次（click の投稿が 1 本だけの日の clicks・follow の投稿が出た日と出ていない日の followers の前日差）は観察の差（因果ではない）として並ぶ——投稿の効果と書かない。3.6.0 より前の投稿は `unrecorded`（目的を書く口が無かった頃）で、`none`（目的なし）とは別の層。`thth_observe` の「前回の観測から」と `thth_map_show` の自分の層にも目的が出る。つまずきの年表は `thth_report_timeline`（CLI は `thth report timeline <project>`・読むだけ・自分の project の報告とリリースノートを日付で並べる）。（click の物差しは 3.7.0 で変わった——次の段。）

**click はリンク先を投稿ごとに分けると投稿単位で測れる**（3.7.0）。あるリンク先を前後 72 時間で 1 本の投稿しか使っていなければ、`clicks_by_url` のそのリンク先の投稿日を含む 3 暦日のクリックを投稿のものとして出す（`basis: unique_url_72h`・`window: post_day_plus_2`・クリック率は clicks_72h / views_24h）。同じリンク先を前後 72 時間に 2 本以上で使う・リンクが無い・プロフィールのリンク（台帳の `profile_links`）と同じなら `url_shared_72h`・`no_link`・`profile_link` で言わない。lint は同じリンク先を前後 72 時間に使う click の原稿を `warning: url_shared_72h` で知らせる（断らない・同じ日でもリンク先が違えば測れる）。**本文に札（utm）を付けて書き換えない**——リンク先を分ける。follow の投稿が毎日出ていれば `no_comparison_days`（事実だけ・推奨しない）。週の表は `thth analytics-report <account> --weekly-goals --weeks 6`（観察の表）。承認は 1 段目だけでは出ない——**確定するまで出ません**（queue・board・observe に「確定待ち」、publish_at まで 3 時間を切ると observe の次の一手に `confirm_due` と確定の命令）。`thth account` は「run を止めるもの」と「run が自分で直すもの」（repo が遅れているだけ）を分け、出られない原稿（held）は別の段（「投稿できます」のまま）。連投も段ごとのリンク先が記録に残り、click の照合に入る。`thth lint`・`preview`・`approve` は手元の Mac のパス（`…/docs/sns/queue/<file>`）を VM のパスに読み替える。`thth where` は `--also <語>`（同じ結果を絞るだけ・追加の検索なし）・0 件は `zero_or_filtered`（無いのか受け付けなかったのか区別できない）・`--aggregate [--lexicon <file>]`（数と時刻だけ・本文も username も返さない・保存しない）。

**検索の語は道具が数えて候補を出し、クリックは目的に依らず投稿ごとに見る**（3.9.0）。`thth analytics-report <account> --per-post-clicks [--since 30d]` は goal を問わず、一意のリンク先の投稿（`unique_url_72h`）の 72h のクリック・クリック率・窓の後（参考）・窓の前（`clicks_before_post`）を並べる——goal の層とは混ぜない（unrecorded の投稿もリンク先が記録にあれば並ぶ）。**窓の前と後は窓の和に足さない**。`clicks_before_post` が 1 以上なら「THTH を通していない投稿か Threads の外のクリックが混ざっている可能性」の 1 行（原因は言わない）。`--by goal` の click にも同じ欄。`thth where <account> <語…> --also <語>` は語ごとの当たり率（取れた件数・also に合った件数）を高い順に並べ、0 件の語に「also に合った投稿が 0 件（直近 n 日）」。`--suggest` は語の候補: 自分のデータ（地図の点・原稿の topic と goal・反応の多かった topic）が先、次に also に合った投稿の本文で 2 投稿以上に出た語（その場の人が書いた語・上位 10）。**語と数だけで、本文・username・post_id は出ない・保存しない**（MCP の `where_to_appear` には無い）。`--aggregate` の `rate_by_span` は返った投稿の期間で割った速さ（n と期間の時間つき・n < 20 は `rough`）。要求数に届かず期間が短いと `more_may_exist`（これで全部かは区別できない）。

段だけを読み直したいときは `thth handoff-report <account> --since-last-read --json` を読む。`tool.changed_since_last_read` が真なら、手元の `tool.notes_root_local_hint` を手掛かりに `tool.release_notes` の相対名を読んでから作業する。既読の記録は `--mark-read --by <名前>` の明示時だけ。`tool.notes_reason` が `notes_directory_unavailable` なら、このインストールには読める docs がない。空の `release_notes` を「変更なし」と解釈しない。

添付を付ける前に `tool.capabilities` を読む（媒体ごとの「出せるもの」表。`thth doctor` にも同じ表が出る。`unverified` は「非対応」ではない）。


# THTH — 投稿する前に呼ぶ道具

## 何を保証するか

- **手元の CLI では、人が承認していない本文は 1 文字も出ない。** 承認は 2 段（見せる → digest を渡す）。digest が違えば拒否する。**「見せた本文」と「出す本文」が同じであることを機械が確かめる。**
  サーバ MCP（招待の口座）には承認の関所が無い（3.13.0）。持ち主が頼んだらそのまま出し、安全装置（最短間隔・1 日の上限・連投で停止）が機械的に守る。
- **`--production` を付けるまで出ない。** 付けない実行は、投げるはずの本文と digest を
  表示して終わる。
- **記録は利用者の git に残る。** post_id・承認者・承認時刻・返信・実測は、あなたの repo の
  commit として残る。招待者のサーバ mode では管理者の専用 managed repo に残る。絡みの台帳（`data/sns/engagements/`）は
  公開の commit とは別で、**次の採取（`thth collect`・最大 10 分の遅れ）**で commit される。
- **数値には分母が付く。** 件数・期間・揃えた条件が無い数字は返さない。取れていない刻みは
  `null` であって `0` ではない。
- **黙って間違えない。** 拒むときは理由と次の一手を言って非ゼロで終わる（loud reject）。

## 招待されたサーバ MCP の書く口

credential の user scope / writes がある場合だけ `thth_draft_put`、`thth_send_request`、
`thth_schedule_request`、`thth_retract_request` を使える。account は本人の許可範囲だけ。
actor/by/person/confirm/digest/ファイルパスを引数に加えない。本文は body、下書き更新は draft_id と expected_revision。
`thth_send_request` はその場で出て post_id とリンクが返る。断りは符丁（`too_soon`・`daily_limit`・`retract_limit`・`quiet_hours`・`account_stopped`）で、添えられた時刻まで待つか本人に伝える。
本人の口座の secret とアシスタントの鍵を聞かない・入力しない・会話へ貼らせない。止まった口座を戻す・安全装置を緩めるのは本人が https://thth.me/activity で行う（LLM からはできない）。
出す前に本文を見たいと本人が言えば、`thth_draft_put` の結果を見せてから `thth_send_request` を呼ぶ。
unknown は自動再依頼・再公開で解消せず、管理者が媒体と記録を照合する。
詳しい管理設定と入力は `docs/運用_サーバ書込_2.12.md`。無 credential のローカル MCP とは別の入口。

## 何を拒むか

- **`--confirm` の無い本番。** digest の食い違う本番。承認後に本文が変わった下書き
  （`approval_stale`）。
- **同期を確認していない commit の本文。** ディスクに置いただけのファイルは選ばれない。
- **静かな時間帯・最短間隔に反する投稿。**
- **語（トピック）の未確認。** 承認の一段目が「未確認です」と言う。

## 何を絶対にしないか

- **何を書くかを決めない。** 文体・頻度・時刻・語の選び方はあなたのプロジェクトのもの。
  渡された本文を**整形も切り詰めもせず**そのまま出す。
- **読む口は SNS 台帳に書かない。** `thread_read`・`where_to_appear`・`who_is_this` はどれも、
  読んで見せるだけで**台帳に 1 バイトも書かない**。この 3 つと絡みの台帳
  （`data/sns/engagements/`）だけを見れば、残るのは**自分の行為と反応**だけで、
  **相手の本文・相手の名前・あなたの判断は入らない**。**別の台帳**——自分の投稿への
  返信の台帳（`thth collect` が採り、`thth replies` で読む）——は、この規律の外で
  相手の `username` と本文をそのまま残す（読む口でもなく、絡みの台帳でもない）。
- **人の代わりに承認しない。** あなた（エージェント）は下書きと digest を人に見せ、
  人が `--confirm` を打つ。

---

## 使う順番——場面で

### 1. 探す——どこに絡みに行くか

```bash
thth where <account> <語…>          # 検索の一覧に自分の履歴を重ねて返す
thth where --project <P> <語…>      # project 単位で全媒体を一度に
```

MCP `where_to_appear`。**順位は無い**——材料を並べるだけで、選ぶのは LLM。
**SNS 台帳への保存: なし**。運用 runs には account・検索語・件数・成否だけを記録し、本文・username は残さない。
**呼ばなくてよいとき**: 返す先がもう決まっている・自分の投稿への返信。

### 2. 読む——枝をその場で

```bash
thth thread <account> <post_id>
```

MCP `thread_read`。枝の会話を**生で**読み、誰がどんな立場かをその場で判断する。
**`already_replied` を見落とさない**——もうこの枝に返しているかがここに乗る。
**残すもの: なし**（読んで捨てる。runs には件数だけ）。
**呼ばなくてよいとき**: 自分の根の枝は `thth threads` の形で足りるとき。

### 3. 相手を知る——この仮名と何度

```bash
thth who <account> <author_key>
```

MCP `who_is_this`。この仮名と自分のアカウントが何度・いつ・どんな反応だったか
だけを返す。**人物像ではない**——発言の内容は持たない。
**SNS 台帳への保存: なし**。既存の最小運用 runs は残る。`who` は 1 度に 1 人。

### 4. 書く——下書きに宛先を乗せる

下書きの front-matter に:

```yaml
reply_to: <post_id>
reply_to_author_key: <一覧の --json の author_key をそのまま写す>
found_by: where_to_appear|manual|mention
```

**残すもの**: この 3 つの欄（下書きファイルそのもの。承認まではあなたの repo の
untracked/コミット前のファイル）。語を選ぶときだけ `before_you_post`
（原稿本文は渡さない・件数と期間つきで返す）。

**まだ出ていない自分の原稿へ返すとき（3.2.0）**: `reply_to` の代わりに
`reply_to_file: <同じ queue・同じ account の原稿のファイル名>` と書く（パスは書けない・
`reply_to` との併用不可）。指した原稿が出るまで待ち、出たらその post_id に返す——
**root には落とさない**。待っている間は `thth board`・`thth morning` に「返信待ち」として
名前と待ち先が出る（時刻超過には数えない）。承認の指紋は `file:<名前>`。

### 5. 承認——二段（そのまま）

```bash
thth lint <ファイル…>          # front-matter と字数の検査
thth preview <ファイル>        # 実際に投げる本文そのもの
thth approve <ファイル or ディレクトリ>                       # 一段目: 本文と digest を見せる
thth approve <同じ> --confirm <digest> --by "<承認した人>"     # 二段目: 承認して commit
```

**一段目は非ゼロで終わる。** それは失敗ではなく「まだ承認していない」。
**`--by` に人の名前を書く。** 承認したのは人であって、あなたではない。
**残すもの**: post_id・承認者・承認時刻（あなたの repo の commit）。

### 6. 測る——出したあとの反応

```bash
thth after (<account>|--project <project>) [--reply-to <post_id>] [--topic …] [--kind …]
```

MCP `after_you_posted`。**24h の刻みが無ければ `null`**（0 と混ぜない・
`covered: false` を付ける）。`one_thing_to_change` は 1 個か `null`。
`posts` は所有を確認できた実測台帳の根投稿だけ（`n` と 24h views の中央値・
分母）、`engagements` は絡みに行った返信。`kind` は現在の topic shelf の分類で、
投稿構成の `form` や投稿時点の分類ではない。
**残すもの: なし**（この口は読むだけ。絡みの台帳は**公開の瞬間**に 1 行書かれる——
自分の行為と反応だけで、相手の本文・名前・自分の判断は入らない）。
**媒体・account をまたぐ数は無い**——`--project` は `by_account` に並べるだけで、
足さない・割らない・順位も付けない。

---

## 詰まったら

- `thth --help` / `thth <subcommand> --help` が正本。
- `thth doctor <account>` が「いま投稿できる状態か」を最初から最後まで言う。
- 道具の不具合・欲しい形・迷ったことは `thth report file`（MCP `thth_report_file`）で実装側に届ける（小さいものも歓迎・重複は道具が束ねる）。
- 承認済みなのに出ない原稿は `thth_observe` の予定の段 `held_items`（file・理由・予定・経過）と `thth board` の要確認（先頭 5 本）に名前が出る。`approval_stale` は再承認（`thth approve` の二段）で直る。時刻を過ぎて出られないものがあると `thth run` は `held` として死活通知と運用通知に知らせる。
- 自分の開いている報告に書き足すなら `thth report add <report_id> --body-file <path|-> --by <名前>`（MCP `thth_report_add`）。
- 他の媒体で試した施策と結果は `thth plaza list <project>`（MCP `thth_plaza_list`）で読める。試したら `thth plaza post`（MCP `thth_plaza_post`）で置く。
- 使い方の全文: `docs/使い方_プロジェクトのセッション向け_2026-09-09.md`。

## 運用と分析のレポート（v3系・2.5.0 から）

2.4.0 以前には無い入口を含む。接続先のhelp/tools一覧にある場合だけ使う。

- 再開時は `handoff-report` / MCP `operations_handoff`。ローカルの停止・要確認・通知記録を読む。`waiting`をtimer正常や投稿成功と解釈しない。
- `analytics-report` / MCP `analytics_report` は期間・母数・欠測・根拠付きsnapshot。生成時刻とデータ更新時刻を混同しない。
- 前期間比較は `--compare-previous` / `compare_previous: true`。投稿後24〜30時間未満の実観測で比較し、欠測をゼロにしない。
- 施策メモの振り返りは `study-report` / `study_report`。採用宣言と観測を分け、提案を自動で採用済みにしない。投稿の承認には使えない。
- 観測差を因果効果と断定しない。accountをまたいだ合算・順位を作らない。詳しい読み順は `docs/手順_LLM_分析と運用の引継ぎ.md`。

## 停止通知と運用状況（開発版 main）

新規 account は利用者メール・管理者メール・TLS SMTP を `thth notifications config` で設定し、`status` → `test` を実行する。両宛先の受信箱で到達、account 専用 HEALTHCHECK_URL の missed-ping 通知を確認してから production/scheduled を有効にする。既存 account にも同じ設定を追加する。詳細は `docs/停止通知と運用記録.md`。

プロジェクトの作業開始・再開時は `git fetch` 後にリモート `docs/sns/queue/` の `thth_run_state`・`thth_run_detail`・`thth_run_next` を確認する。dirty worktree へ無条件に pull しない。リモートの確認は `git show origin/main:docs/sns/queue/FILE.md` 等で行える。停止・成否不明なら inflight を手で消して再投稿しない（3.3.1 から道具が毎 run の最初に媒体へ 1 回だけ問い合わせ、決まれば自分で解く）。止まり続けるなら `thth inflight <account> show` で中身を見て、媒体で確かめた人が `thth inflight <account> resolve --not-published|--published <post_id> --by <名前>` で解く（二段確認・MCP には無い）。復旧状態は投稿成功の保証ではなく、公開結果は status/post_id/posted_at を見る。

`mentions <account> --json` は 3 媒体の言及を同じ項目で読む。返信前に `replied` の object／false／null を区別する。`unanswered <account> --since 7d --json` は自分の根投稿の未回答候補を台帳で読む。`cannot_say` と採集の古さを確認し、`--refresh` は明示されたときだけ使う。件数は account ごとに扱い、送信は既存の承認・digest・production の門を通す。

## 2.10.0 の入口

- `where <account> --recent 茶 --word コーヒー --since 7d --exclude-engaged --max-per-author 2 --json`。位置引数とオプションを混在できる。各語の `dropped` と `material.n` は絞り込み後の内訳。Bluesky の期間は API の sortAt、他媒体は投稿日時。タグ観測は独立した 24 時間。
- Bluesky の `thread`・`send --reply-to`・`after --reply-to`・`replies --post` は `https://bsky.app/profile/<handle|did>/post/<rkey>` も受ける。公開 handle 解決後の AT URI を用い、解決不能は止める。
- `approve <queue ディレクトリ> --account <name>` はその account の draft だけ。glob は手元で展開されるので使わず、VM のディレクトリを渡す。承認二段・digest・`--by` は同じ。
- `send` dry-run はロックなし、最小運用 runs は 1 行。書く口の `--wait <秒>` はロック待機（既定 0）。`send` と `approve` の一段目は媒体の数え方と文字数上限を表示する。
- `study add <施策JSON> <post_id|queueのパス> --by <名前>` は `changed_post_ids` に追加。`--baseline` は `baseline_post_ids`。この口は利用者の JSON だけを保存し、採用判断・投稿承認・git commit は行わない。VM ラッパ経由なら JSON と queue のパスは VM 上のもの。
- 比較の `by_mark` は views が無くても likes・replies・reposts を表示。3 媒体とも採集済みの値を使い、欠測は null。媒体・account を足さない。
- `mentions` と `unanswered` の CLI も最小運用 runs を残す。本文・username・投稿 ID は運用記録へ保存しない。`unanswered` の計算共有先の handoff-report は純粋な読み取りのまま。
