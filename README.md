# THTH

THTH は、自分の SNS アカウントで**調べる・投稿する・予約する・振り返る**ためのコマンドラインツールです（Threads・Bluesky・Mastodon・X）。

CLI なので、Claude Code などの AI アシスタントがそのまま操作できます。同梱の MCP サーバーとスキルは、その操作を楽にするための補助です。THTH は何を書くかを決めません。頼まれたときだけ動きます。

English: [README.en.md](README.en.md) · サイト: https://thth.me · 文書の索引: [docs/README.md](docs/README.md)

## できること

| | Threads | Bluesky | Mastodon | X |
|---|---|---|---|---|
| 投稿・予約 | ○ | ○ | ○ | ○ |
| 削除 | ○ | ○ | ○ | ○ |
| 返信とインサイト（表示・いいね・返信などの数）の取得 | ○ | ○ | ○ | 数だけ（3.16.0・THTH から出した投稿） |
| キーワード検索（`thth topics --search`・`thth where`） | ○ | ○ | ○ | － |
| 言及（`thth mentions`） | ○ | ○ | ○ | － |
| プロフィールと場所の検索 | ○ | － | － | － |

- **調べる**: `thth topics <アカウント> --search <語>` で、その語の件数・投稿者の数・最新の投稿を見ます。`thth where` は「次にどこで話すか」を、自分がすでに絡んだ相手を重ねて探します。X の行には投稿者のフォロワー数（「フォロワー 1.2 万」）と投稿数も付きます（`--json` は `author_followers`・`author_posts`。保存しません）。
- **出す**: `thth send` で今すぐ、`thth schedule` で予約、`--reply-to` で返信です。
- **ブックマークの連投は記事と別に数える**（4.0.1）: はてなブックマークは主体の別の窓（既定 10 分に 10 件）で数え、超えても主体を止めずにその 1 件だけ断ります。1 日 30 件・同じホストへ 1 日 3 件（自分の記事は 1 日 1 件のまま）。記事の連投で止める作りは変わりません（[リリースノート 4.0.1](docs/リリースノート_4.0.1_2026-10-17.md)）。
- **X の全期間の検索**（4.5.0）: `thth archive <口座> --from <handle> --title <曲名> [--max-total 20] [--json]` で 2006 年からの他人の公開投稿を読みます（id・URL・時刻・本文・handle・保存しない）。`--count-only` は件数だけ（1 回 0.010 USD）。返った本数で月の X 予算に数え、1 日の本数は `x_archive_daily_reads`（既定 0＝読まない）で別に縛ります。timer からは叩きません（[リリースノート 4.5.0](docs/リリースノート_4.5.0_2026-10-21.md)）。
- **push した直後の lint・approve**（4.5.1）: パスが VM に無ければ、その repo を取り込んで 1 回だけ探し直します。取り込んでも無ければ VM と remote の commit を並べます（[リリースノート 4.5.1](docs/リリースノート_4.5.1_2026-10-22.md)）。
- **承認は利用者の repo に commit を積まない**（4.4.1）: `thth approve --confirm`・`thth revoke`・thth.me の予約と取り消しは、VM の承認の台帳（`state/<口座>/approvals.json`）に書き、repo の原稿は書き換えません（`thth approvals <口座>` で見られます）。口座の台帳を `require_gate: true` にすると、hub の承認の番号（`--approval A261021-25`）の無い確定を断ります（[リリースノート 4.4.1](docs/リリースノート_4.4.1_2026-10-21.md)）。
- **会話の続く投稿は 20 分おきに返信を取り直す**（4.4.0）: 投稿から 48 時間以内か、直近 48 時間に返信が付いた投稿は、刻みとは別に timer が会話を取り直します（1 回 10 本まで・刻みと数の記録は変えない・X は既定で外す・台帳の `reply_follow` で変えられる）（[リリースノート 4.4.0](docs/リリースノート_4.4.0_2026-10-21.md)）。
- **止めた口座では timer は何も出さない**（4.3.1）: 安全装置か持ち主が止めた口座では、予約の投稿・返信を queue に残し、`thth account resume` で戻せば次の timer で出ます（[リリースノート 4.3.1](docs/リリースノート_4.3.1_2026-10-20.md)）。
- **返信はまとめて出す**（4.3.0）: 時刻の過ぎた返信は承認したその場で、timer では 1 回でまとめて出します（返信の窓は返信だけで 10 分に 10 件・投稿の窓に数えない・投稿の間隔を掛けない）。使い方は「`publish_at` を今か過去にしてまとめて承認する」だけです（[リリースノート 4.3.0](docs/リリースノート_4.3.0_2026-10-19.md)）。
- **1 件の断りでまとめを止めない**（4.2.2）: はてなに 1 件だけ断られた札は token を確かめてその札だけ外し、まとめを進めます。断られそうな札（半角の「%」・タグとコメントの長いもの）は 1 段目と lint で予告します（断りません）。止まったまとめは 60 分ごとにしか打たず、`thth bookmark batch-resume` で今すぐ見直せます。sparse-checkout の定義の外には記録を書かず、直し方を言います（[リリースノート 4.2.2](docs/リリースノート_4.2.2_2026-10-18.md)）。
- **ブックマークの直しとタグの長さの柵**（4.2.0）: `thth bookmark edit` で THTH が付けたブックマークのコメントとタグを 2 段の承認で直す。31 バイトを超えるタグは予告して断る。上限の設定は台帳の `limits` と別名で書け、断りに根拠と通し方が付きます（[リリースノート 4.2.0](docs/リリースノート_4.2.0_2026-10-18.md)）。
- **ブックマークを 1 日分まとめて承認する**（4.1.0）: 札を JSON Lines に並べ、`thth bookmark batch` の 1 段目で全件の一覧と digest を見て 2 段目で確定。timer が上限の中で時刻を散らして付けます（口座ごと）。あわせて、上限と連投の柵の土台（媒体の特性の台帳）を入れました（[リリースノート 4.1.0](docs/リリースノート_4.1.0_2026-10-17.md)）。
- **記事をはてなへ届けて、直したら反映**（4.0.0）: repo の記事を承認してはてなブログへ公開・予約し（画像はフォトライフへ）、SNS の告知（`announces`・`{article_url}`）とはてなブックマークを記事に結びます。記事を直せば `thth article diff` で差分を見て、承認して更新します。はてなの画面で手直しされていれば上書きせず `thth conflict` で止まります。反応は `thth article reactions` で記事ごとに。SNS だけの口座の動きと承認の指紋は変わりません（[リリースノート 4.0.0](docs/リリースノート_4.0.0_2026-10-17.md)）。
- **返信の連投は止めずに順番待ち**（3.25.0）: `thth send --reply-to`（thth.me の `send_request` も）が急な連投（burst）を超えるとき、口座を止めずにその 1 件を窓が空く時刻の順番待ちに並べ、timer が出します。止めるのは返信でない投稿の連投だけです（[リリースノート 3.25.0](docs/リリースノート_3.25.0_2026-10-16.md)）。
- **予約も `--production` で**（3.24.0）: thth.me の鍵の道の `thth schedule` は、`--production` が無ければ下書きを置いて lint だけにします（`send` と同じ）。過去の時刻の予約はサーバも断ります（[リリースノート 3.24.0](docs/リリースノート_3.24.0_2026-10-16.md)）。
- **出来事と数の要約**（3.23.0）: `thth events <口座|project> --after <next_cursor> --json` で出来事（公開・返信・言及・止まり・予算）を取りこぼさず読み直し、`thth summary` で数を 1 つにまとめます。`observe --json` は `sections_by_name` で節を名前で引けます（[リリースノート 3.23.0](docs/リリースノート_3.23.0_2026-10-16.md)）。
- **本文は全文で**（3.21.0）: 検索（`topics --search`・`where`）・`thread`・`mentions` は他人の投稿の本文を切らずに画面と `--json` に出します（保存はしません）。Threads の検索は `--since`・`--until`・`--media` で絞れます。thth.me の鍵でも `thread`・`where`・`who` が使えます。VM で打つ `thth send` にも安全装置（間隔・1 日の上限・連投）が掛かります（[リリースノート 3.21.0](docs/リリースノート_3.21.0_2026-10-15.md)）。
- **振り返る**: `thth collect` が投稿後 1 時間〜30 日の決まった時点で返信とインサイトを取得し、`thth replies`・`thth measured` で読みます。
- **再投稿といいね**: `thth repost`・`unrepost`・`like`・`unlike`（3.15.0 から）。Threads は再投稿だけ（Threads の API にいいねが無い）。Bluesky は両方。Mastodon と X のいいねは、そのアカウントを認可し直してから（[リリースノート 3.15.0](docs/リリースノート_3.15.0_2026-10-09.md)）。
- **検索の語ごとの数を残す・絞る**（3.18.0）: `thth where … --also|--aggregate --keep` で語ごとの数だけを日付つきで残し（5 未満は欠測・本文や名前は残さない）、`thth where-log <project> [--budget N --medium x]` で当たり率と翌日の配り直しを見ます。`--ja`・`--question`・`--no-replies` で表示を絞ります（X の `--ja` は検索に `lang:ja` を足して読む本数を減らす）（[リリースノート 3.18.0](docs/リリースノート_3.18.0_2026-10-12.md)）。
- **X の非公開リスト**: `thth list create|add|remove|show`（3.17.0 から・X だけ）。公開のリストは作らない。足す・外すは人が選んだ少数だけ（1 回 10 人まで）。アカウントを認可し直してから（[リリースノート 3.17.0](docs/リリースノート_3.17.0_2026-10-12.md)）。
- **X のコミュニティ**: `thth community search <account> <語…>` で探し、`thth send <account> --community <id>` か queue の front-matter `community_id:` で出す（3.19.0 から・X だけ）。参加は X の画面で先に（API に参加の口は無い）。返信とは同時に使えない。各コミュニティの規則は人が読んで守る（[リリースノート 3.19.0](docs/リリースノート_3.19.0_2026-10-13.md)）。
- **X のトレンドと語ごとの投稿数**: `thth trends <account> [--woeid N] [--max N]`（既定は日本）と `thth counts <account> <語> [--granularity hour|day]`（直近 7 日）（3.19.0 から・X だけ・1 回の有償の読み取り・保存しない）。

## はじめ方

### 招待された人

運営（gotoq）から届いた招待リンクを開き、Threads で認可します。そのあとターミナルで次を打ちます。

```bash
pip install thth
thth login
```

ブラウザが開くので、アカウント名とアカウントのシークレットを入れて許可します。以後は自分のアカウントにそのまま使えます。

```bash
thth posts <アカウント>
thth topics <アカウント> --search コーヒー
thth send <アカウント> --text "こんにちは"
```

サーバーも Meta アプリも git も要りません。手順の全体は [招待されたら](docs/導入_招待されたら.md) にあります。

### 自分の機械で運用する人（運営者）

アカウントの台帳を自分の機械に置き、下書きのファイルを承認してから timer で出す運用もできます。承認は 2 段（本文と digest を見せる → その digest で確定）で、記録は VM の承認の台帳に残ります（4.4.1 から利用者の repo には書きません）。準備は [統一ガイド](docs/導入_承認を押すだけ.md)、日々の使い方は [プロジェクトのセッション向け](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) にあります。

## AI アシスタントから使う

```bash
claude mcp add thth -- thth-mcp
```

MCP の道具は CLI の写しで、同じ `thth` コマンドを呼ぶだけです。スキル（`skills/thth/`）は「投稿する前に何を見るか」「返信をどう扱うか」をアシスタントに教えます。キーはアシスタントに渡しません。`thth login` が手元に置いたキーを `thth` コマンドが使います。

## ガード

アシスタントが誤って走っても、アカウントごとのガードで止まります。

| ガード | 既定 |
|---|---|
| 投稿の最短間隔（返信には掛けない） | 6 時間 |
| 1 日の投稿 | 8 件 |
| 1 日の削除 | 5 件 |
| 急な連投 | 10 分に 3 件を超えたらアカウントを止める |
| 夜間 | 22:00〜07:00 は今すぐの投稿をしない |

アシスタントが変えられるのは、厳しくする向きだけです。緩める・止まったアカウントを戻す・キーを発行し直すのは、オーナーが https://thth.me/activity でアカウントのシークレットを使って行います。

## 保存しないもの

- 検索結果（`topics --search`・`where`・`thread`・`who`）の本文と投稿者は、その場で見せるだけで保存しません。`where … --keep` のときだけ、語ごとの数（件数・問いの形・分類・also の当たり）を日付つきで project の私有の置き場に残します（5 未満は欠測・`thth where-log` で読む・3.18.0）。
- ダイレクトメッセージは読みません。
- トークンとシークレットは、ログと出力から伏せます。

詳しくは [プライバシーポリシー](https://thth.me/privacy/) にあります。

## コマンドの一覧

`thth --help` で各コマンドの説明が出ます。

動くもの（`thth --help` の全サブコマンド）: `lint`・`preview`・`approve`・`approvals`・`account`・`revoke`・`posts`・`replies`・`measured`・`threads`・`after`・`analytics-report`・`study-report`・`study`・`unanswered`・`handoff-report`・`observe`・`morning`・`events`・`summary`・`scenario`・`dashboard`・`deliveries`・`media`・`article`・`conflict`・`hatena`・`bookmark`・`serve-reports`・`worker`・`topics`・`forms`・`queue`・`schedule`・`throw`・`run`・`systemd`・`board`・`collect`・`pull`・`auth`・`refresh`・`maintain`・`send`・`doctor`・`app`・`token`・`ask`・`mentions`・`profile`・`thread`・`where`・`where-log`・`who`・`retract`・`edit`・`reply`・`comments`・`quota`・`repost`・`unrepost`・`like`・`unlike`・`list`・`community`・`trends`・`counts`・`archive`・`principal`・`fotolife`・`inflight`・`location`・`notifications`・`report`・`plaza`・`map`・`admin`・`login`・`logout`。

## 版と配布

- 版の正本は `thth/VERSION` です。手元の版は `thth --version` で確かめます。
- `main` への push は保存だけです。`release` を進めると運営のサーバーに出て、版の tag を push すると PyPI と MCP registry に出ます。
- 依存は 0 です。入口は `thth` と `thth-mcp` の 2 つです。
- AI エージェント向けの試用の記録: [headlesson の THTH のページ](https://headlesson.com/apps/thth)（THTH と同じ運営）。3.15.0 の導入と `--help` を 2026-10-11 に確かめた記録で、投稿と予約は試していません。

## ライセンス

MIT。

## MCP registry

registry は「この PyPI の名前を名乗ってよいのは誰か」を、配布物の README にある次の 1 行で確かめます。消さないでください。

mcp-name: io.github.aokings/thth
