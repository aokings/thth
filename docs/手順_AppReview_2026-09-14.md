# 手順: Threads の App Review（v2.1・2026-09-14）

masaru の裁定（設計 v2 §4.3・2026-09-14「取れるものは全部・App Review は 1 回で」）を実行するための台本。
**11 権限を 1 回の提出にまとめる**。証拠段階は他文書と同じ規約:
**L1**＝この repo・この worktree で実測、**L2**＝一次資料（Meta 公式ページ）の読解、**L3**＝二次情報・推測・
masaru からの申告で本 session からは裏取りできなかったもの。

固有名（アカウント名・post_id）はこの文書には書かない。`masaru-threads` だけは台帳名（識別のための名前）
なので書く。

---

## 0. 結論を先に（読みたい人はここだけでよい）

- **`masaru-threads` のトークンは既に 11 権限すべてを持っている**（`thth doctor` v2.0.2・`/debug_token` の
  結果・**L1**＝設計 v2 §4.3 が repo 内の実測として書いている）。他の運用アカウント（3 本）は 2026-09-09
  取得時の 5 権限（`threads_basic`・`threads_content_publish`・`threads_manage_replies`・
  `threads_manage_insights`・`threads_read_replies`）のまま。
- **一般の Meta Graph API 文書（`docs/graph-api/overview/access-levels/`・2026-09-14 読解・L2。
  Threads 専用ページではない）は明言している**: 「アクセス許可は、リクエストするアプリで役割を付与されている
  アプリユーザーのみがリクエストできる」（＝スタンダードアクセス）ものは**自動的に承認され**、
  「スタンダードアクセスは、アプリで役割を付与されている人だけが使用するアプリを対象としている」。
  役割だけの人しか使わないアプリは、**App Review を経ずに無期限にスタンダードアクセスで動く**、と読める。
- **THTH の実際の形はこれに一致する**（設計 v2 §0「利用者は自分の Meta アプリを持ち込む・masaru のアプリは
  自社の 4 アカウントだけ」）。masaru のアプリを使うのは、全員が Threads tester として役割を持つ自社 4
  アカウントだけで、これから増える予定もない。
- **したがって、正直に書くと**: 自社 4 アカウントで 11 権限を使うだけなら、**技術的には App Review が
  要らない可能性がある**。`masaru-threads` のトークンに 11 個すべてが乗っている事実（L1）は、この読み方と
  矛盾しない。
- **ただし確証ではない。** Threads 専用の App Review ページ（`docs/threads/app-review`）と
  `docs/development/release/app-review` は本 session からは **404 で読めなかった**（下記 §1 のとおり L3扱い）。
  「テスト準備完了」という Dashboard の表示が「読み取りだけでなく書き込みも tester に実際に降りる」ことを
  意味するのか、「App Review に出せる状態になった」だけを意味するのかは、**Threads 専用の一次資料では
  確認できていない**（一般 Graph API 文書からの類推＝L2 止まり）。2026-09-09 時点では 6 権限は
  「テスト準備完了」以前の段階（アプリレビューに追加のみ）で tester にも降りてこなかった、という repo 内の
  実測（`docs/導入_自分のMetaアプリで動かす.md` §2）があり、これは「降りてこない」の原因が Review 未提出
  なのか Dashboard の段階なのかを切り分けていない。
- **提案（判断ではなく提案）**: 録画を始める前に、まず `masaru-threads` で新しい 6 権限のうち書き込みを
  伴わない 1 つ（例: `threads_profile_discovery` の口）を実際に叩いてみる。**通れば**、スタンダードアクセス
  で自社利用が足りている実例が増え、App Review の優先度を masaru に相談し直す材料になる。**通らなければ**
  （権限エラーで断られれば）、それ自体が「tester でも App Review が要る」ことの L1 の証拠になる。
  どちらの結果でも、**本書の §2〜§5 の台本は無駄にならない**（提出することになれば台本がそのまま要るし、
  提出を見送る判断をするならその理由の記録として残る）。
- **この提案の扱いは masaru に委ねる。** 以下は裁定どおり「11 個を 1 回で提出する」ことを前提にした
  手順として書く。

---

## 1. 提出の前提

### 1.1 読めた一次資料・読めなかった一次資料

| URL | 結果 | 使った情報 |
|---|---|---|
| `developers.facebook.com/docs/threads/app-review` | **404（読めなかった）** | 無し。**L3 扱い** |
| `developers.facebook.com/docs/development/release/app-review` | **404（読めなかった）** | 無し。**L3 扱い** |
| `developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review` | 読めた（一般 App Review の説明） | 「アプリに Role を持たない人が使うなら App Review が要る」の一文 |
| `developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review/submission-guide` | 読めた | 提出物一覧（下記 1.2） |
| `developers.facebook.com/docs/threads/overview` | 読めた（該当箇所なし） | Standard/Advanced の区別への言及なし |
| `developers.facebook.com/docs/threads/get-started` | 読めた（部分） | 「Threads tester はいつでもこれらの権限を付与できる」の一文（5 権限を指すとみられる。11 のうちどれを指すかは明記が無い） |
| `developers.facebook.com/docs/graph-api/overview/access-levels/` | 読めた（Threads 専用ではない） | §0 のスタンダード／アドバンストの定義 |

**Threads 専用の App Review 手順ページには到達できなかった。** 一般ページ（Graph API 共通・
resp-plat-initiatives）からの類推で書いた箇所はすべて **L2**、Threads だけに特有の挙動（5 権限しか
降りてこない等）は repo 内の実測（**L1**・masaru の実操作）か、二次情報のブログ（**L3**）に拠っている。

### 1.2 提出物（一般 App Review・L2）

| 項目 | 内容 | 状態（THTH） |
|---|---|---|
| 権限ごとの用途説明 | 「このユーザーに何をもたらすか・なぜ要るか・データをどう使うか」を権限ごとに書く | 本書 §2 に用意 |
| 画面録画（screencast） | 1080p 以上・英語 UI 推奨（日本語なら字幕）・音声は不要（審査員は聞かない）・**申請した権限が画面に映っていない録画は通らない** | 本書 §3 に手順（撮影は masaru の手） |
| 事前の API 呼び出し | 「Advanced Access を申請する各権限を、提出の 30 日以内に最低 1 回実際に呼んでいること」（**L3**・検索で複数の二次情報が一致して言及。一次資料では確認できず） | §4 のチェックリストに組み込む |
| プライバシーポリシー URL | 「Meta の認可画面に出す URL」としてアプリ設定に必須 | **無い（要る）**。下記 1.3 |
| データ削除の手順 | ユーザーがデータ削除を申し出る手段（URL でも記載でもよい） | **ある**（`/data-deletion`・`/data-deletion-status`。下記 1.4） |
| アプリアイコン | 1024×1024・Meta の商標を含まない | **未確認**（Dashboard を見ないと分からない・**L3**） |
| Development → Live の切替 | 「承認が終わってから切り替える。早まって切り替えるとアプリに Role を持つ人にすら使えなくなる」（一次資料の警告） | **審査中は Development のまま。** §0 の読み方が正しければ、承認後も Live へ切り替える必要自体が無いかもしれない（自社 4 アカウントしか使わないため） |
| ビジネス認証 | — | **済**（masaru の申告。本 worktree からは Meta Business Manager を見られないため、この session では未確認・**L3**） |
| 技術提供者の認証（Tech Provider Verification） | 「本番アカウントに公開する前に技術提供者としての本人確認が要る」という二次情報あり（**L3**・一次資料で該当ページは見つからず） | **審査中**（masaru の申告・**L3**） |

### 1.3 プライバシーポリシー URL — 要る

`callback/src/index.js`（本 session で読んだ・**L1**）は `/deauthorize`・`/data-deletion`・
`/data-deletion-status` の 3 本を持つが、**独立したプライバシーポリシーのページは無い**。
`/data-deletion-status` の文面（「THTH は masaru 個人の道具で、取得した内容は本人の repository にのみ
保存される」）はデータ削除専用の説明で、プライバシーポリシーの体裁ではない。`callback/public/index.html`
（紹介ページ）にもプライバシーポリシーへのリンクは無い（本 session で grep して確認・**L1**）。

**提出前に要る**: `thth.me` に `/privacy`（仮）のような 1 ページを足し、Meta の App Dashboard の
「プライバシーポリシー URL」欄にその URL を入れる。中身は `/data-deletion-status` に既にある事実
（masaru 個人の道具・保存先は利用者本人の repository・収集する項目は §2 の各権限の用途説明と同じ）を
1 枚にまとめれば足りるはず。**このページの追加は本文書の範囲外**（コードを触らない、という今回の役割の
外）——別セッションで `callback/` を直す。

### 1.4 データ削除・認可の取り消し — ある

- `/deauthorize`: masaru 自身のアカウントしか使わない開発モードでも欄を埋める必要があるため、200 を返す
  だけの実装（`callback/src/index.js`・**L1**）。
- `/data-deletion`: Meta の仕様どおり `{"url": ..., "confirmation_code": ...}` の JSON を返す（**L1**）。
- `/data-deletion-status`: 人間向けの説明ページ（**L1**）。

この 2 本は**既にある**。App Review の設問には**そのまま使える**。

---

## 2. 権限ごとの用途の説明文

各 3 文以内。Meta の審査員が読む前提で、THTH の実際の口に即して書く。**嘘を書かない**——実装が
まだ無い 6 つは「実装中」と明記する。

### 2.1 既に使っている 5 つ

| 権限 | 日本語 | English |
|---|---|---|
| `threads_basic` | THTH は Threads へ操作を行う前に、対象アカウント本人であることを `/me` で確認します。この確認は実行のたびに行われ、投稿・返信・削除などすべての操作の前提になります。取得するのはユーザー ID とユーザー名だけです。 | THTH verifies the account's identity via `/me` before any operation. This check runs on every invocation and is a prerequisite for publishing, replying, and every other action. Only the user ID and username are retrieved. |
| `threads_content_publish` | THTH は、人が Git リポジトリ上で承認した下書きだけを Threads に投稿します。本文は改変せず、承認された内容のままコンテナ作成→パブリッシュの 2 段階で送信します。承認前の下書きが投稿されることはありません。 | THTH publishes only drafts a human has approved in a Git repository. The text is sent unaltered, via the container-then-publish flow. Unapproved drafts are never published. |
| `threads_manage_replies` | 利用者への返信も、投稿と同じ二段承認（全文表示→digest 確認）を経てから送信します。この権限は返信の投稿と、不適切な返信の非表示化に使います。自動返信は行いません。 | Replies also go through the same two-stage human approval (full text shown, then a digest confirms it) before being sent. This permission posts replies and hides inappropriate ones. THTH never auto-replies. |
| `threads_manage_insights` | 投稿後の表示回数・いいね数・リンクのクリック数を定期的に取得し、利用者自身の Git リポジトリに記録します。指標はその repository の外へは送信しません。記録は本人がトピック選定などを振り返るために使います。 | THTH periodically fetches views, likes, and link-click counts for published posts and records them in the user's own Git repository. Metrics never leave that repository. The record supports the user's own retrospective analysis (e.g. choosing topics). |
| `threads_read_replies` | 投稿への返信を全階層で取得し、誰が・いつ・何に返信したかを記録します。取得するのは自分の投稿への公開の返信だけです。この記録をもとに、返信するかどうかを人が選びます。 | THTH retrieves the full reply tree for a post — who replied, when, and to what. Only public replies to the user's own posts are retrieved. A human then decides which replies to answer. |

### 2.2 新しい 6 つ（実装中・Opus が並行して実装。録画は実装が着地してから）

| 権限 | 口（設計 v2 §4.3） | 日本語 | English |
|---|---|---|---|
| `threads_keyword_search` | `thth topics <account> --search <語>` | 語で公開投稿を検索し、投稿者の異なり数・直近の投稿時刻・タグ付きの割合といった観測の材料だけを出します。本文は画面に表示するだけで保存しません。トピックに人がいるかを、これまで人がブラウザで確かめていた手間を減らすためのものです。 | Searches public posts by keyword and reports only aggregate observation material — number of distinct authors, most recent post time, share of tagged posts. Post bodies are shown on screen but never saved. This replaces a manual browser check the user currently does before choosing a topic. |
| `threads_manage_mentions` | `thth mentions <account>`／`collect` の `inbox` | 自分のアカウントへの言及を取得し、利用者自身の Git リポジトリに記録します。返信するかどうかは人が決め、返信は既存の承認の仕組みを通ります。取得するのは自分宛ての公開の言及だけです。 | Retrieves mentions of the user's own account and records them in the user's Git repository. A human decides whether to reply, and any reply goes through the existing approval flow. Only public mentions of the user's own account are retrieved. |
| `threads_profile_discovery` | `thth profile <account> <username>` | 公開プロフィール情報を取得し、返信や言及をしてきた相手がどんな読者かを把握するために使います。取得できるのは Meta が公開している基本項目だけです。スレッドの枝に参加する人の偏りを数える観測の一部です。 | Fetches public profile information to characterize who is replying to or mentioning the account. Only the basic fields Meta exposes publicly are retrieved. This supports observing who takes part in a thread's branches. |
| `threads_location_tagging` | queue の front-matter `location:` ＋ `thth location search <語>` | 地名で場所を検索して `location_id` を取得します。下書きに場所が指定されている場合、承認の一段目で場所名を表示したうえで、承認された投稿にだけ場所を付けて出します。場所の推測はせず、常に人が明示した場所だけを使います。 | Searches for a location by name to obtain its location_id. When a draft specifies a location, the approval screen shows the location name before publishing, and only approved posts carry the tag. THTH never infers a location — only one a human explicitly wrote. |
| `threads_delete` | `thth retract <account> <post_id> --reason … --by …` | 公開済みの投稿を取り下げるために使います。取り下げにも二段承認が要り、理由と実行者を記録します。取り下げても記録は消さず、取り下げ日時・実行者・理由を書き戻します。 | Used to retract an already-published post. Retraction requires the same two-stage approval, and the reason and actor are recorded. Retracting never erases history — the time, actor, and reason are written back into the record. |
| `threads_share_to_instagram` | queue の front-matter `share_to_instagram: true` | 承認済みの投稿を Instagram にも同時に出すために使います。承認の一段目で「Instagram にも出ます」と明示したうえで、承認された投稿だけに適用します。Instagram との連携が設定されていないアカウントでは、この設定自体を拒否します。 | Used to also share an approved post to Instagram at publish time. The approval screen states "this will also appear on Instagram" before publishing, and it applies only to approved posts. THTH refuses this setting entirely for accounts without a linked Instagram account. |

**入れないもの**（審査員向けの説明にも明記する）: 検索結果の本文の保存、自動返信、場所の推測。

---

## 3. 録画の手順（権限ごとに 1 分以内・masaru の手）

**共通の作法**: 高解像度（1080p 以上）・カーソルを大きくする・音声は録らない（審査員は聞かない）・
日本語 UI のまま英語字幕を付けるか、実行するコマンドを画面内にテキストで残す。**申請する権限が
実際に画面に映っている**ことが必須——権限名を言うだけでは通らない（§1.2）。

**取り下げ（`threads_delete`）と Instagram 共有（`threads_share_to_instagram`）の録画は、本物の投稿を
消したり Instagram に出したりする必要がある。** 対象は**新しく本物を使わず**、09-13 に `masaru-threads`
へ出した疎通確認の投稿を使う、と決めておく（**録画の直前に `thth posts masaru-threads --limit 5`
などで対象の post_id を人が確認してから使う**——本 session はこの post_id そのものを確認していない）。

| 権限 | コマンド | 画面に映れば足りるもの |
|---|---|---|
| `threads_basic` | `thth doctor masaru-threads` | `/me` の応答（ユーザー ID・ユーザー名）と「○」の行 |
| `threads_content_publish` | `thth approve <queue>` → `thth throw` | 一段目の全文表示→digest→承認→投稿の一連。post_id が返る場面まで |
| `threads_manage_replies` | 返信の下書きを `approve` → `throw` | 返信本文の一段目表示→承認→送信。相手の投稿に返信が現れる画面 |
| `threads_manage_insights` | `thth topics masaru-threads` | 表示回数・いいね数などの数値が並ぶ出力 |
| `threads_read_replies` | `thth collect masaru-threads` の後 `thth replies masaru-threads` | 返信の一覧（誰が・いつ・何に）が出る画面 |
| `threads_keyword_search`（実装後） | `thth topics masaru-threads --search <語>` | 検索結果の集計（異なり数・直近時刻）が出る画面。本文一覧を長々と映さない |
| `threads_manage_mentions`（実装後） | `thth mentions masaru-threads` | 言及の一覧が出て、`inbox` の ndjson に追記されたことが分かる画面 |
| `threads_profile_discovery`（実装後） | `thth profile masaru-threads <相手のユーザー名>` | 公開プロフィールの基本項目が出る画面 |
| `threads_location_tagging`（実装後） | `thth location search <語>` → queue の `location:` を付けて `approve`（本番投稿はしない） | 場所の検索結果→承認の一段目に場所名が出る画面まで（dry-run で止めてよい） |
| `threads_delete`（実装後） | `thth retract masaru-threads <疎通確認の post_id> --reason "録画のための取り下げ" --by masaru` | 二段承認の一段目（本文と URL）→二段目→取り下げ後に `thth posts` が「取り下げ済み」と出す画面 |
| `threads_share_to_instagram`（実装後） | queue に `share_to_instagram: true` を付けた疎通確認相当の下書き→承認→`throw` | 一段目に「Instagram にも出ます」と出る画面→投稿後、Instagram 側にも投稿が現れる画面 |

---

## 4. 提出の順番（チェックリスト）

**【masaru の手】** は認証・秘密・本番反映・録画を含むので本人が行う操作。**【開発でよい】** は
Claude セッションに投げてよい操作。

1. **【開発でよい】** `thth/adapters/threads.py` に 6 つの新しい口が実装され、偽サーバのテストが通る
   （Opus の実装が着地）。
2. **【masaru の手】** §0 の提案（`masaru-threads` で書き込みを伴わない 1 権限を実際に叩いてみる）を
   行い、スタンダードアクセスで足りるかどうかの手がかりを得る。**このまま提出を続けるかどうかを
   ここで一度確かめる**（続ける判断であれば以下をそのまま進める)。
3. **【masaru の手】** `thth.me` にプライバシーポリシーのページを作る（§1.3）。App Dashboard の
   プライバシーポリシー URL 欄に入れる。
4. **【masaru の手】** アプリアイコン（1024×1024）を Dashboard に用意する（§1.2・未確認事項）。
5. **【masaru の手】** 11 権限それぞれについて、提出の 30 日以内に最低 1 回、実際の API 呼び出しを
   行っておく（§1.2 の事前条件・L3 だが安全側に倣う）。§3 の録画自体がこの条件も満たす。
6. **【masaru の手】** §3 の録画を権限ごとに撮る（実装が着地した 6 つ＋既存の 5 つ）。
7. **【masaru の手】** §2 の用途説明文（日本語・英語）を Dashboard の各権限の申請フォームに貼る。
8. **【masaru の手】** 11 権限をまとめて 1 回の App Review として提出する（裁定どおり）。
9. **【masaru の手】** 提出後は**アプリを Development のまま維持する**（§1.2 の警告——早まって
   Live に切り替えない）。承認が全部そろってから、Live へ切り替える必要が実際にあるかを §0 の
   読み方に照らして再確認する。

---

## 5. 落ちたときの対処

**典型の差し戻し理由**（§1.2・二次情報 L3 を含む）:

- **録画に申請した権限の動作が映っていない。** → §3 の対応表どおり、コマンドの実行から結果が
  画面に出るところまでを映し直す。
- **プライバシーポリシー URL が無い・内容が実態と合わない。** → §1.3 のページを作ってから出し直す。
  内容は「masaru 個人の道具・保存先は利用者本人の repository・収集する項目は §2 の各権限の説明と同じ」
  以上を盛らない。
- **要求している権限が実際の機能より広い（over-requesting）。** → §2 の説明文を見直し、使っていない
  機能を含む言い回しがあれば削る。「入れないもの」を審査員向けにも明記する。
- **個々の権限が却下・一部だけ通る。** → 通らなかった権限だけ、理由を読んで §2・§3 を直し、
  **その権限だけ**再提出する（11 個まとめての再提出は不要——一次資料 L2「各権限は個別に審査される」）。
- **技術提供者の認証が終わっていないまま提出した場合。** → 認証が終わるのを待ってから提出し直す
  （§1.2）。

**再提出のたびに**、本書 §2 の説明文と §3 の録画対応表を実装の実態に合わせて直す
（実装が変われば口の名前も変わりうるため）。

---

## 参考

- [設計_v2_泉と門_2026-09-13.md](設計_v2_泉と門_2026-09-13.md) §4.3（この文書のもとになった裁定と表）
- [導入_自分のMetaアプリで動かす.md](導入_自分のMetaアプリで動かす.md) §2（5 権限しか降りてこなかった実測）
- [使い方_プロジェクトのセッション向け_2026-09-09.md](使い方_プロジェクトのセッション向け_2026-09-09.md)（二段承認・digest の作法）
