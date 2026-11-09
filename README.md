# THTH

THTH は、自分の SNS アカウントで**調べる・投稿する・予約する・振り返る**ためのコマンドラインツールです（Threads・Bluesky・Mastodon・X）。

CLI なので、Claude Code などの AI アシスタントがそのまま操作できます。同梱の MCP サーバーとスキルは、その操作を楽にするための補助です。THTH は何を書くかを決めません。頼まれたときだけ動きます。

THTH が断るときは、なぜ断ったか・どうすれば通るか・どうすればもっと良くなるかを、丁寧に前向きに返します（[原則: 前向きな断り](docs/原則_前向きな断り.md)）。

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

- **調べる**: `thth topics <アカウント> --search <語>` で、その語の件数・投稿者の数・最新の投稿を見ます。Threads では `--tag` を付けると、その語を**トピックに付けた**投稿の件数・投稿者の数・最新と最古の時刻だけを見ます（本文と投稿者は出しません。`--keep` で数と日付を `where --keep` と同じ project の置き場に残します・5.7.0）。`thth where` は「次にどこで話すか」を、自分がすでに絡んだ相手を重ねて探します。X の行には投稿者のフォロワー数（「フォロワー 1.2 万」）と投稿数も付きます（`--json` は `author_followers`・`author_posts`。保存しません）。
- **出す**: `thth send` で今すぐ、`thth schedule` で予約、`--reply-to` で返信です。
- **privacy に Meta の通知の保持**（5.12.6）: thth.me に置く Meta からの通知（最長 7 日）を頁に書きました（[リリースノート 5.12.6](docs/リリースノート_5.12.6_2026-11-05.md)）。
- **privacy に命令の控えの 14 日**（5.12.5）: 口座ごとの命令の控えの保持を頁に書きました（[リリースノート 5.12.5](docs/リリースノート_5.12.5_2026-11-05.md)）。
- **`thth board` を速く**（5.12.4）: 原稿 1 本ごとの git をまとめ、原稿が増えても遅くならなくなりました（[リリースノート 5.12.4](docs/リリースノート_5.12.4_2026-11-05.md)）。
- **止まりを `thth board --json` で言う**（5.12.3）: 投稿や鍵の止まりを各口座の `stuck` に出します。何も試みなかった回で「復旧」と言わなくなりました（[リリースノート 5.12.3](docs/リリースノート_5.12.3_2026-11-05.md)）。
- **X の予算の台帳が大きくなっても止まらない**（5.12.2）: 台帳が 256 KiB を超えると鍵の更新まで止まっていた不具合を直しました（[リリースノート 5.12.2](docs/リリースノート_5.12.2_2026-11-05.md)）。
- **同期が自分で解ける**（5.12.1）: 書き戻しの push が落ちたあと origin が進んでも、手元に残った THTH 自身の commit だけなら載せ直して push します。人の commit は書き換えずに止まります（[リリースノート 5.12.1](docs/リリースノート_5.12.1_2026-11-05.md)）。
- **断りに「なぜ・通すには・もっと良く」**（5.12.0）: 目録にある断りは、CLI の文・`--json`・MCP・thth.me の答えのどれでも、理由と、その口で本当に打てる直し方と、より良くする一手を返します。招待で入った口座には最初の 14 日の慣らし（1 日の公開の上限が半分）が掛かります（[リリースノート 5.12.0](docs/リリースノート_5.12.0_2026-11-05.md)）。
- **断りの文を直し方で終える**（5.11.1）: 打ち消しで終わって通る道を書いていなかった断りの文 31 件を、「どうすれば通るか」で終わる文に直しました。符丁・鍵・rc は変わりません（[リリースノート 5.11.1](docs/リリースノート_5.11.1_2026-11-04.md)）。
- **置く前に見る・Threads のクリック・訪問を糸の一生の数に**（5.11.0）: `thth plaza post … --preview` で、範囲を問わず置く中身（本文・観測の欄・下書き・範囲の既定）を何も書かずに見られます。置いた本人は `thth plaza hide <id> --as <account>` で自分の 1 件を隠せます。糸・seeds・広場の観測に Threads のリンクのクリック（投稿から 72h・訪問とは別の欄・言える本数／Threads の本数）を、project のダッシュボードにサイトへの参照元（host と数だけ）を出します。hub が 1 日 1 ファイルで送る訪問の控えを、糸の期間（seeds は `--days` の窓）に重なる日だけ足し、欠けた日は 0 と混ぜず日の数で言います（[リリースノート 5.11.0](docs/リリースノート_5.11.0_2026-11-03.md)）。
- **済んだ糸と返信の段**（5.10.0）: 糸の段に `replies_within_hours=` を書けば、段の名が何でも返信を数えます（告知の本数では済みになりません）。全部の段を満たした糸は「済んだ」になり、動かなくても停滞と言いません。済んだ糸は閉じる（任意）か広場に置く候補として出ます。ダッシュボードの要対応に、時刻を過ぎて出ていない原稿も並びます。媒体で取れない数（Bluesky・Mastodon の views）は「取れない」と言います（[リリースノート 5.10.0](docs/リリースノート_5.10.0_2026-11-03.md)）。
- **次の糸の種**（5.9.0）: `thth scenario seeds <project>` が、直近 28 日（`--days`）の自分の投稿と糸から、次の糸の種を 4 つの列で並べます——返信の多かった投稿・訪問の多かった告知（GA4 の数を取り込んでいなければ「言えない」）・よく見られた投稿・閉じた糸で宣言にあって使わなかった媒体。数の多い順に並べるだけで、選ぶのは窓口と人です。媒体をまたいで比べず、5 本に満たない群は比べを言いません。他人の返信の本文と名前は出ません（[リリースノート 5.9.0](docs/リリースノート_5.9.0_2026-11-03.md)）。
- **効いたかをプラザに置く**（5.8.0）: `thth plaza post <アカウント> --kind measure --from scenario <project> <糸の id>` で、閉じた糸 1 本の段ごとの本数・媒体ごとの反応（`thth after` と同じ計算）・本記事への訪問・広告の段（始めた本数と予算。使った額とリンクのクリックは記録に無いので「言えない」）を、道具が観測として付けて置きます。人や LLM の解釈は `--body-file` で、観測の欄には入りません。糸を閉じると `thth observe` の次の一手にこの命令が出て、同じ持ち主のほかの project は `thth plaza digest` の「糸の結果」で、使った媒体と試していない媒体つきで読めます（[リリースノート 5.8.0](docs/リリースノート_5.8.0_2026-11-03.md)）。
- **糸の段の語彙と本記事への訪問**（5.7.0）: キャンペーンの宣言に広告の段（`- boost: ads`・広告の口座の記録を読むだけ）とセルクマの段（`- selfbm: hatena_bookmark target=own`・自分の記事か他人のページかを分けて数える）が書けます。段の日本語の名（告知・セルクマ・広告）も出ます。`thth scenario site-import <project> --file <json>` が hub の GA4 の数（`hub.site_visits/1`）を受け取り、原稿の名前と `thread:` で糸に結んで、`thth scenario show`・`thth dashboard <project> campaign <id>` に「本記事への訪問（GA4・hub 経由）」を出します。結ばれない campaign は数だけ、取れない数は 0 と言いません。`thth scenario`・`show`・`dashboard campaign` の 1 行目は流れの型の skill `thth-flow`（`thth scenario skill --print`）です（[リリースノート 5.7.0](docs/リリースノート_5.7.0_2026-11-03.md)）。
- **広告の成績と手順の skill**（5.6.3）: `thth ads insights` が反応の内訳（いいね・リアクション／コメント／シェア／写真を開いた／ページへのいいね）・サイトへの誘導（リンククリック・サイトへ出たクリック・頁が開いた）・1 件あたりの額・効率（frequency・cpm・ctr・cpc ほか Meta の計算済みの値）・動画の見られた深さを出します。`clicks` はリンクのクリックではありません。数が 0 か、計測が無くて分からないかを分けて書きます（`site.why`）。`--by age-gender|region|country|placement|device|hour` で内訳、`thth ads estimate` で出す前の見積もり、`thth ads doctor`・`show` でピクセルの有無と最後に動いた時刻を読みます（読むだけ）。`thth ads boost --utm-campaign <原稿のファイル名>` でリンクに有機の投稿と同じ campaign の UTM（`utm_medium=paid`）を付けます。広告を出す LLM 向けの手順は skill `thth-ads`（`skills/thth-ads/`・`thth ads skill --print`）で、広告の命令の次の一手（`next_steps`）とエラーの文は先頭でそれを示します（[リリースノート 5.6.3](docs/リリースノート_5.6.3_2026-11-03.md)）。
- **Meta の広告**（5.6.2）: `thth ads boost` は全部 PAUSED で作り、始めるのは `thth ads activate` の 2 段だけです。`thth ads watch` は成績を読み続け、指定の額か表示数に達したら `thth ads stop` と同じに止めます（`--until` で必ず終わる）。`spend_cap`・月の上限・旗を上げる命令はありません。例外は 1 つで、止めた印を外す `thth ads resume` は 2 段＋hub の承認の番号が必須です（masaru 11/02・外すのは印だけで、広告は PAUSED のまま）（[リリースノート 5.6.2](docs/リリースノート_5.6.2_2026-11-02.md)）。`thth ads boost --platforms` は `facebook`・`instagram`・`threads` を受けます。Meta の決まりで Threads は Instagram のフィードと組でだけ出せるので、instagram 無しの threads は要求を出す前に断ります（`ads_threads_needs_instagram`）。Threads の口座は台帳の `threads_user_id` を使い、無ければ Instagram に結びついた口座（`connected_threads_user`）を読んでクリエイティブに入れます。Threads の投稿そのものは広告にできないので、同じ中身を Facebook ページに出した投稿を Threads にも流します（`ads_threads_post_unsupported`）。広告の鍵は `threads_business_basic` も求めます（11/07 に足した権限。それより前の鍵は `thth ads auth <名前> --by <名前>` で再認可するまで Threads には出さず、その旨を言って断ります）。Threads への配信は本番では未確認です。
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
- **振り返る**: `thth collect` が投稿後 1 時間〜30 日の決まった時点で返信とインサイトを取得し、`thth replies`・`thth measured` で読みます。X は THTH から出した自分の投稿の公開の数に加えて、作って 30 日以内の投稿ではリンクのクリック（`link_clicks`）・プロフィールのクリック（`profile_clicks`）・`engagements`・非公開の表示（`private_impressions`）と、動画が 1 本あればその再生の数（`video_views`・`video_playback_0`〜`100`）も記録します。30 日を過ぎた刻み（720 時間）は公開の数だけで、取れない数は書きません（0 にしません）。
- **再投稿といいね**: `thth repost`・`unrepost`・`like`・`unlike`（3.15.0 から）。Threads は再投稿だけ（Threads の API にいいねが無い）。Bluesky は両方。Mastodon と X のいいねは、そのアカウントを認可し直してから（[リリースノート 3.15.0](docs/リリースノート_3.15.0_2026-10-09.md)）。
- **検索の語ごとの数を残す・絞る**（3.18.0）: `thth where … --also|--aggregate --keep` で語ごとの数だけを日付つきで残し（5 未満は欠測・本文や名前は残さない）、`thth where-log <project> [--budget N --medium x]` で当たり率と翌日の配り直しを見ます。`--ja`・`--question`・`--no-replies` で表示を絞ります（X の `--ja` は検索に `lang:ja` を足して読む本数を減らす）（[リリースノート 3.18.0](docs/リリースノート_3.18.0_2026-10-12.md)）。
- **X の非公開リスト**: `thth list create|add|remove|show`（3.17.0 から・X だけ）。公開のリストは作らない。足す・外すは人が選んだ少数だけ（1 回 10 人まで）。アカウントを認可し直してから（[リリースノート 3.17.0](docs/リリースノート_3.17.0_2026-10-12.md)）。
- **X のコミュニティ**: `thth community search <account> <語…>` で探し、`thth send <account> --community <id>` か queue の front-matter `community_id:` で出す（3.19.0 から・X だけ）。参加は X の画面で先に（API に参加の口は無い）。返信とは同時に使えない。各コミュニティの規則は人が読んで守る（[リリースノート 3.19.0](docs/リリースノート_3.19.0_2026-10-13.md)）。
- **X のトレンドと語ごとの投稿数**: `thth trends <account> [--woeid N] [--max N]`（既定は日本）と `thth counts <account> <語> [--granularity hour|day]`（直近 7 日）（3.19.0 から・X だけ・1 回の有償の読み取り・保存しない）。

## はじめ方

### 招待された人

運営（gotoq）から届いた招待リンクを開き、招待の媒体（Threads・Mastodon・Facebook ページ・Instagram）で認可します。そのあとターミナルで次を打ちます。

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

Facebook ページと Instagram では `thth doctor`・`thth dm`・`thth live`・`thth page cta`・`thth like`・`thth edit`・`thth reply hide` も同じように使えます（書く命令は手元と同じ二段確認）。`thth reply hide|unhide` は Threads の返信にも使えます。Threads の承認待ちの返信（`thth reply pending|approve|ignore`）と広告（`thth ads`）はまだこの道にありません。

サーバーも Meta アプリも git も要りません。手順の全体は [招待されたら](docs/導入_招待されたら.md) にあります。

### 自分の機械で運用する人（運営者）

アカウントの台帳を自分の機械に置き、下書きのファイルを承認してから timer で出す運用もできます。承認は 2 段（本文と digest を見せる → その digest で確定）で、記録は VM の承認の台帳に残ります（4.4.1 から利用者の repo には書きません）。準備は [統一ガイド](docs/導入_承認を押すだけ.md)、日々の使い方は [プロジェクトのセッション向け](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) にあります。

## AI アシスタントから使う

```bash
claude mcp add thth -- thth-mcp
```

MCP の道具は CLI の写しで、同じ `thth` コマンドを呼ぶだけです。スキル（`skills/thth/`）は「投稿する前に何を見るか」「返信をどう扱うか」をアシスタントに教えます。記事を軸に告知・セルクマ・広告までつなぐ流れと、印（UTM）・測り方は `skills/thth-flow/` が教えます。広告を出すときの手順は別のスキル（`skills/thth-ads/`・pip で入れた場合は `thth ads skill` が場所を出します）です。キーはアシスタントに渡しません。`thth login` が手元に置いたキーを `thth` コマンドが使います。

## ガード

アシスタントが誤って走っても、アカウントごとのガードで止まります。

| ガード | 既定 |
|---|---|
| 投稿の最短間隔（返信には掛けない） | 6 時間 |
| 1 日の投稿 | 8 件 |
| 1 日の削除 | 5 件 |
| 急な連投 | 10 分に 3 件を超えたらアカウントを止める |
| 夜間 | 22:00〜07:00 は今すぐの投稿をしない |

夜間でも出したい原稿（運休・中止などの緊急の知らせ）は、queue の front-matter に `urgent: true` を書いて承認します。timer は夜間だけを越えて出し、最短間隔・1 日の本数・停止は今までどおり守ります。札は承認の対象に入るので、承認のあとに付けた原稿は出ません。

アシスタントが変えられるのは、厳しくする向きだけです。緩める・止まったアカウントを戻す・キーを発行し直すのは、オーナーが https://thth.me/activity でアカウントのシークレットを使って行います。

## 保存しないもの

- 検索結果（`topics --search`・`where`・`thread`・`who`）の本文と投稿者は、その場で見せるだけで保存しません。`where … --keep` のときだけ、語ごとの数（件数・問いの形・分類・also の当たり）を日付つきで project の私有の置き場に残します（5 未満は欠測・`thth where-log` で読む・3.18.0）。`topics --search <語> --tag --keep`（5.7.0）も同じ置き場・同じ決まりで、その語をトピックに付けた投稿の件数・投稿者の数・最新と最古の日付だけを残します（5 未満は欠測・正確な時刻は残さない・`thth where-log` で読む）。
- Instagram のハッシュタグと Facebook のページの検索（`where`）・他のプロ口座とページの公開の数（`who --profile`）は、本文も画像も頼みません。数と id とリンクを見せるだけで保存しません。Instagram の 7 日 30 種を数えるために、調べたタグと時刻だけを 7 日間残します（Meta の機能の審査が通るまでは自分の口座の範囲です）。`embed`・`upcoming`・`ads catalog`・`ads leads`・`ads network`・`ads shop` も見せるだけで保存しません。
- ダイレクトメッセージは、`thth dm` を打ったときだけ読みます（Facebook ページと Instagram の受信箱）。本文と相手は画面に出すだけで保存せず、返事は人が二段確認で送ります（自動の返信はしません・相手の最後の通から 24 時間の内だけ）。`thth live create` が返す配信の宛先も画面に出すだけです。
- トークンとシークレットは、ログと出力から伏せます。

詳しくは [プライバシーポリシー](https://thth.me/privacy/) にあります。

## コマンドの一覧

`thth --help` で各コマンドの説明が出ます。

動くもの（`thth --help` の全サブコマンド）: `lint`・`preview`・`approve`・`approvals`・`account`・`revoke`・`withdraw`・`posts`・`replies`・`measured`・`threads`・`after`・`analytics-report`・`study-report`・`study`・`unanswered`・`handoff-report`・`observe`・`morning`・`events`・`summary`・`scenario`・`dashboard`・`deliveries`・`media`・`article`・`conflict`・`hatena`・`bookmark`・`serve-reports`・`worker`・`topics`・`forms`・`queue`・`schedule`・`throw`・`run`・`systemd`・`board`・`collect`・`pull`・`auth`・`refresh`・`maintain`・`send`・`doctor`・`app`・`token`・`ask`・`mentions`・`profile`・`thread`・`where`・`where-log`・`who`・`embed`・`upcoming`・`retract`・`edit`・`reply`・`comments`・`page`・`live`・`dm`・`brand`・`creators`・`shop`・`wa`・`quota`・`repost`・`unrepost`・`like`・`unlike`・`list`・`community`・`trends`・`counts`・`archive`・`principal`・`ads`・`gads`・`gsc`・`webhook`・`fotolife`・`inflight`・`location`・`notifications`・`report`・`plaza`・`map`・`admin`・`login`・`logout`。

`gsc` は Google Search Console を読むだけの口です。`thth gsc auth` で 1 度だけ認可し（`gads` と同じ Desktop の OAuth クライアント・scope は `webmasters.readonly` だけ・鍵は `~/.config/thth/google-gsc-token.json` に 600 で別に置く）、`sites` で見えるプロパティと権限、`query --site <プロパティ>` でクリック・表示回数・CTR・平均掲載順位（既定は 3 日前までの 28 日・太平洋時間）、`compare --site <プロパティ>` で直近 N 日とその前の N 日の差（伸びた・落ちた語か頁）、`inspect --site <プロパティ> --url <頁>` で URL の検査（Google の索引にある版）を読みます。書く命令はありません。

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
