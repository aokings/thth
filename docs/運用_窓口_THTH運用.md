# THTH運用の窓口（2026-10-29〜）

運用の頼みを受ける専用の窓口。立ち上げは masaru 10/29（本線 メディアハブ本線 第10 が起こした）。開発が大きな仕事の最中でも、運用の頼みが止まらないようにする。

- 呼び名: **THTH運用 第1**（hub roster では `thth（ops）`、伝言の宛先は `thth/ops`）
- 受けるもの: 口座の追加、Facebook ページ・Instagram のつなぎ、予約やはてなの札の流れの確認、本番で何が動いているかの確認
- しないもの: THTH のコードを直すこと（不具合・要望は `thth report file` か hub msg で開発 `thth` へ）、`release` を進める・tag・Worker の deploy（開発が masaru の「出して」で押す）
- 決まりの正本は repo の `CLAUDE.md` と `docs/定型_運用セッションの始まり.md`。この文書は持っている案件の台帳だけ

## 持っている案件

| # | 件 | 状態 | 次の一手 |
|---|---|---|---|
| 1 | Facebook ページを THTH につなぐ（手順の正本 `docs/手順_FBページと広告_2026-10-28.md`） | こぴちゃは済み（§0-5。10/29 16 時に本番の `thth board` で `kopicha-fb token=ok/期限なし`）。10/29 16 時、ページの透明性で id を引き、台帳 `nigamilab-fb`（1339887359217489）と `asmon-kanto-fb`（1469823909537300・原稿は `repos/asmon` で asmon-kanto-threads とそろえた）を作った。どちらも `production: false`・`scheduled: false` | **10/29 16:07〜16:10 に 2 口座とも認可済み**（masaru が「次へ」）。`thth doctor` は両方 rc 0・権限 20 個・`expires_at=0`・ページ名が合う（Nigami Lab（苦味研究所）・関東中学受験データ）。本番にする（`production: true`）かと停止メールの設定は masaru と各レーンに聞いてから。残る 3 ページ（染めと化学・キョンシーラボ・MMR）は masaru が作ってから。Instagram・広告・Webhook は開発の計画ができるまで足さない（開発 #5886） |
| 1-2 | FB ページ MMR（10/29 masaru「作ってください」） | 10/29 16:20 に THTH運用 が内蔵ブラウザで「My Music Research」を作った（カテゴリ ニュース・メディアのウェブサイト・紹介文は下ごしらえの短い文・ウェブサイト https://mymusicresearch.com/・画像なし・ユーザーネーム未設定）。ページ id 1443475698843510。台帳 `music-research-fb` を作り認可（masaru が「次へ」）、doctor rc 0・権限 20 個・期限なし。`production: false` | 画像とユーザーネームを足す（内蔵ブラウザからファイルを渡せないので、masaru か別の道で）。本番の時機は MMR のレーン |
| 1-3 | FB ページ キョンシーラボ・染めと化学 | 10/29 16:25、キョンシーラボを作ろうとしたが「作成」を押しても何も起きず、作られなかった（10/28 の 3 つに続けて 4 つ目。短い間の作りすぎの制限とみる） | 10/30 以降に 1 日 1 つずつ |
| 3 | 染めと化学のはてな（本線 #5888 の §2） | ID `sometokagaku`・ブログ https://sometokagaku.hatenablog.com/（「染めと化学出張所」）を masaru が作った。主体 `hatena-sometoka`（owner masaru・`bookmark.lanes` sometoka・consumer は kopicha から `cp -p`）、口座 `sometokagaku-hatenablog`（`repo_dir` は repos/sometokagaku・`link_base` は消した・`production: false`）。認可は 1 回目が id:maoki のまま許可されて `hatena_id_mismatch` で断られ（鍵は書かれず）、sometokagaku でやり直して通った。doctor rc 0: AtomPub ○・ブックマーク ○・**フォトライフ ×（403）**（10/20 の MMR と同じ形・未調査） | フォトライフは 10/29 17 時に sometoka・MMR とも ○ に戻った（開発 #5909、運用も doctor で ○ を見た。原因は未確認・新しい ID は使えるまで時間がかかる見立て【推】）。画像の待ちは解いた。最初の画像つきの記事の前に doctor をもう一度。記法は記事が無いので不明（Markdown にしてあるかは masaru の画面で）。id:maoki 側に THTH との連携が 1 つ残っている（害なし・外すなら masaru） |
| 4 | kopicha-fb の定期（こぴちゃ #5918） | 10/29 17 時: 停止メールを kopicha-threads と同じ宛先で設定（試し送り 利用者・管理者とも SMTP 受領）、min_interval_hours 6→2、daily_max_posts 8→2。scheduled と timer は、VM の /etc/systemd/system に置く操作がこちらの権限で止まった | masaru に聞く（timer を置いてよいか・置くのは誰か） |
| 5 | music-research-fb を本番に（MMR #5921） | 10/29 17 時に production: true（控え VM ~/music-research-fb.json.bak-20261029・doctor rc 0・scheduled false）。停止メールは music-research-threads と同じく管理者あてだけ | MMR が出したら公開で見えるか確かめる |
| 6 | 染めと化学のはてなブックマーク 1 日分（#5912・#5915） | data/hatena/batch/2026-10-30.jsonl（03eb74c・8 件）の 1 段目を打った。digest c8365207b363・同じホストは最大 2・今日の枠に収まる。2 段目は口座が production: false・scheduled: false で断られる（schedule_unavailable） | masaru の承認と「出して」→ production・scheduled・timer → 2 段目 |
| 2 | kopicha-threads が 10/29 15:49 に `blocked（account_locked）` の運用通知を出した | 原因は前の回とのロックの取り合い（journal に「既に実行中です（ロック取得失敗）」）。次の 15:58 の回は「出すものが無い」で正常に終わった | 閉じた |

## 気づいたこと（開発へ渡すか判断中）

- `account_locked`（2 つの回が重なってロックが取れなかっただけ）でも、利用者と管理者へ `blocked` の運用通知が飛ぶ。重なりだけなら通知は要らないかもしれない。
