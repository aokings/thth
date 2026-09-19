---
name: thth
description: 投稿する前・返信を書く前・出したあと・絡みに行く先を選ぶときに呼ぶ道具。Threads・Bluesky・Mastodon への下書きを人の承認を通してから出し、枝を読み、相手を知り、伸びを測る。
---

開始時に `thth handoff-report <account> --since-last-read --json` を読む。`tool.changed_since_last_read` が真なら、`tool.notes_root` に対する `tool.release_notes` を読んでから作業する。既読の記録は `--mark-read --by <名前>` の明示時だけ。


# THTH — 投稿する前に呼ぶ道具

## 何を保証するか

- **人が承認していない本文は 1 文字も出ない。** 承認は 2 段（見せる → digest を渡す）。
  digest が違えば拒否する。**「見せた本文」と「出す本文」が同じであることを機械が確かめる。**
- **`--production` を付けるまで出ない。** 付けない実行は、投げるはずの本文と digest を
  表示して終わる。
- **記録は利用者の git に残る。** post_id・承認者・承認時刻・返信・実測は、あなたの repo の
  commit として残る。THTH のサーバには何も無い。絡みの台帳（`data/sns/engagements/`）は
  公開の commit とは別で、**次の採取（`thth collect`・最大 10 分の遅れ）**で commit される。
- **数値には分母が付く。** 件数・期間・揃えた条件が無い数字は返さない。取れていない刻みは
  `null` であって `0` ではない。
- **黙って間違えない。** 拒むときは理由と次の一手を言って非ゼロで終わる（loud reject）。

## 何を拒むか

- **`--confirm` の無い本番。** digest の食い違う本番。承認後に本文が変わった下書き
  （`approval_stale`）。
- **同期を確認していない commit の本文。** ディスクに置いただけのファイルは選ばれない。
- **静かな時間帯・最短間隔に反する投稿。**
- **語（トピック）の未確認。** 承認の一段目が「未確認です」と言う。

## 何を絶対にしないか

- **何を書くかを決めない。** 文体・頻度・時刻・語の選び方はあなたのプロジェクトのもの。
  渡された本文を**整形も切り詰めもせず**そのまま出す。
- **読む口は何も保存しない。** `thread_read`・`where_to_appear`・`who_is_this` はどれも、
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
**残すもの: なし**（この口自身は何も書かない。材料は `after_you_posted` と
絡みの台帳から来ている）。
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
**残すもの: なし**（絡みの台帳・返信の台帳から導くだけで、この口自体は書かない）。

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

プロジェクトの作業開始・再開時は `git fetch` 後にリモート `docs/sns/queue/` の `thth_run_state`・`thth_run_detail`・`thth_run_next` を確認する。dirty worktree へ無条件に pull しない。リモートの確認は `git show origin/main:docs/sns/queue/FILE.md` 等で行える。停止・成否不明なら inflight を消して再投稿せず、示された確認を行う。復旧状態は投稿成功の保証ではなく、公開結果は status/post_id/posted_at を見る。
