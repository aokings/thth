# 照合「Meta Platform Terms と枝・語の観測」（Codex・2026-09-18）

**裁定材料。C3/C4の保留を維持する。実装・採集・審査提出の承認ではない。**

対象revision: `8f7e4cf754973899a8748dd884fbf3a7738bb606`（設計v3.1のgo記録、C3/C4据え置き）。調査・執筆worktree: `/Users/masaru/.codex/worktrees/ff31/thth`、branch: `codex/meta-terms-c3-c4-review`。正本checkout `/Volumes/NexDev/Developer/thth` は読むだけ。開始時両worktreeはclean、HEADと既存ローカル`origin/main`は一致（fetchなし）。

依頼: `/Users/masaru/thth-exchange/in/2026-09-18_依頼_照合_MetaPlatformTerms_枝と語の観測_Codex.md`。同書のmainへのcommit許可より、今回の専用worktreeのみという指示を優先した。

証拠区分: **L1**＝対象revisionの設計・コードを実読、**L2**＝2026-09-18に公式一次資料の本文を実読、**L3**＝条文と設計の当てはめ・推論。9/16のM4は構成の参考に読み、現行条文の代わりにはしていない。四値は「できる／できない／条件付き／条文では決まらない」。条件付きは条件を満たしたと確認済みという意味ではない。未取得・個別審査の不明も明記する。

## 0. 一枚で言うと

**件数・仮名化でもPlatform Data（PD）に当たる。利用者自身の台帳に閉じれば直ちに禁止とは読めないが、無期限保持・外部LLMへの自由な投入・元投稿削除後の当然の保持は認められたと扱えない。**

保存・LLM各行は適法に取得できた場合の独立評価であり、C3の取得経路の不足を解消しない。以下の取得可否と削除追随にはAPI固有資料の評価を合わせる（§2・§4）。PD該当性そのものは四値の許否とは別なので、取得行には許否と分類を併記した。

|観点|C3：絡んだ他人の枝、24h/72h|C4：語を1日1回観測|根拠・未確認|
|---|---|---|---|
|取得元・分類|**できない（既存threads_read_repliesだけで他人rootを取得する経路）**。別権限経路は未確定。取得結果と返信数・人数・API由来仮名はPD|**条件付き**。検索結果、件数・異なり数・最新時刻・タグ率もPD|T §12(l),(m)、§3(a)(viii)、Pの各権限。取得口の公開範囲と必要な承認は別条件。公開投稿＝規約対象外ではない|
|保存期間|**条件付き**。許容目的への必要性と削除条件に従う。root_post付き記録を匿名例外に即断できない|**条件付き**。集計だけでも必要性がなくなったら削除対象。語・時刻・小標本との関連付けは未評価|T §3(d)。一律の保持日数はT本文にない。法令保持例外は別途評価。必要性・適用される削除義務を無視した永久保存は**できない**|
|本人の完全ローカルLLMによる推論|**条件付き**|**条件付き**|第三者が受信・アクセスしない本人内処理なら、外部共有とは通常読まない（L3）。用途・保存・削除・セキュリティ条件は残る。実際の通信・ログ・同期は未検証|
|外部LLMサービスによる推論|**条件付き**|**条件付き**|PDの共有。サービス提供者として使うならT §3(c)(ii)2・§5(a)等。学習offだけで十分とはしない。提供先・書面条件・再委託・保持・削除は未確認|
|元投稿の削除・非公開化と過去の集計行|**条文では決まらない**：PDであることと、どの過去行をどの方法で削除するかは別|**条文では決まらない**：元IDを捨てても削除義務が消えるとは読めない|T §3(d)の明示義務と、元投稿消失時の集計への波及を区別（§4）。要求がある場合や必要性消失は明示義務。匿名例外を包括免除にしない|
|定期取得・用途説明・審査|**条文では決まらない**：時刻指定自体の禁止は確認できないが、取得経路が未成立|**条件付き**：公開検索の承認と、定期取得・履歴保持を含む実用途の説明が必要|T §3(a)(vii),(viii)、§7(a)、D §1.6。現在の個別承認状態は確認していない|

## 1. データの流れと測定条件（L1）

|項目|入力→加工→保存|残る関連付け・未定義|
|---|---|---|
|C3|他人の公開root投稿の`/conversation`→返信行と投稿者を数える→`{collected_at, mark, reply_count, participant_count, source}`|設計§3 C3の保存先例`data/sns/engagements/threads/<root_post>.ndjson`はroot投稿IDを保持。行の列だけで匿名性を判定できない。`author_key`を計数時のみ使うか、集合を永続保持するかは設計に明記がない|
|C4|語リスト→keyword_search→件数、投稿者異なり数、直近投稿時刻、タグ割合→語別台帳|本文・post_id・usernameを捨てる計画。語・時刻・少数の結果から特定投稿／人と関連付かないことは未証明。元ID破棄後の削除照合方式が未定義|

- `thth/adapters/base.py:145–159`：既存author_keyは`medium + 改行 + username`をSHA256にし先頭16hex、秘密saltなし。同一入力の照合可能性が残り、コードコメントの「非可逆」は規約上の関連不能を証明しない。逆算・再識別の試行はしていない。
- `thth/adapters/threads.py:627–655`：既存conversationは本文・username・ID等を取得し、author_keyを追加して返す。**新C3がこの生データを保存しないことは未実装であり、未検証**。通常行だけでなくstdout・エラー・MCP・LLM・バックアップも将来の確認対象。
- 同`:1175–1196`：既存keyword_searchは先頭1頁、既定TOP・25件。`thth/threads_read_cli.py:266–312`の集計は取得rows内のn／投稿者判明行内の異なり数／取得rows内の最新時刻／topic_tag既知行を分母とするタグ割合。SNS全体の件数、日次全件数、実人数、全投稿のタグ率ではない。C4の検索順序・時間範囲・上限・欠測の記録方式はまだ固定されていない。
- `thth/ask.py:336`のbefore_you_postは既存台帳を読む機能。設計の呼称から検索採集・保存の実装済みを推定しない。
- 利用者横断集約は対象外。本人のgit repoがローカルだけか、private remote／共同編集者／クラウドバックアップを持つかは未確認。gitという保存形式自体は第三者提供でもその免除でもない。削除義務が及ぶデータは作業ツリーだけでなく管理下の履歴・コピーも対象として検討が要る（L3）。

## 2. 現行一次資料と取得記録（L2）

取得日はいずれも**2026-09-18 JST**。公開日表示は確認できず、以下はページの更新表示。web取得ツールではTが取得不可、Dと旧keyword-search URLが429、E/Rが取得エラーだった。T/D/E/Rは通常の非表示IABブラウザで本文を読めた。403回避・認証変更・CAPTCHA操作はしていない。

|略号・公式URL|更新表示|今回の実読範囲・短い引用|
|---|---|---|
|[T：Platform Terms](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US)|2026-02-03|§1–12全文。§12(l) “anonymized, aggregated, or derived”; §3(d)(i)1 “Update or delete”; §5(a)(i) “agrees in writing”。定義、保存・削除、共有、SP/TP、審査を照合|
|[D：Developer Policies](https://developers.facebook.com/devpolicy/)|2026-02-03|日本語本文全節。§1.6「許可された用途」。Threads固有の節は見当たらない。Instagram／広告等の別機能の条件をThreads全般へ転用しない|
|[E：§3.e Exceptions](https://developers.facebook.com/terms/3e/)|2025-02-03|全文。“Messenger Communication Content”。列挙はMessenger会話、Lead Data、Shops User Data。C3/C4に合う例外は確認できない|
|[R：Restricted Platform Data additions/exceptions](https://developers.facebook.com/terms/12n/)|2020-07-01|日本語全文。「含まれません」の列挙にThreadsの明示はない。全ThreadsデータがRestrictedであるとの意味ではない|
|[P：Permissions Reference](https://developers.facebook.com/documentation/development/permissions?locale=en_US)|2026-09-14|冒頭・Requirements・Threads各権限の本文を統括実読。threads_read_repliesのAllowed Usage: “Get replies to a thread owned by the app user”。keyword_searchには “public content tree” と匿名集計の補足許可もある|
|[K：Keyword and Topic Tag Search](https://developers.facebook.com/documentation/threads/keyword-search?locale=en_US)|2026-01-21|本文全文。Permissions: “After approval, public posts will be searchable.”。Limitations、検索条件、公開投稿への操作も確認|
|[C：Retrieve Media Replies and Conversations](https://developers.facebook.com/documentation/threads/retrieve-and-manage-replies/replies-and-conversations?locale=en_US)|2026-02-02|本文全文。A thread’s conversations: “only intended to be used on the root-level threads with replies”。頁分割・全階層の平坦一覧|
|[M：Retrieve User Posts](https://developers.facebook.com/documentation/threads/retrieve-and-discover-posts/retrieve-posts?locale=en_US)|2026-04-14|本文。単一media取得のPermissions: “After approval, public posts created by other users will be retrievable.”。threads_basicのAdvanced Access条件で、conversationの権限とは別|
|[W：Webhooks for Threads](https://developers.facebook.com/documentation/threads/webhooks?locale=en_US)|2026-06-30|本文。Moderate topic fieldsのdelete: “Threads posts that were deleted by the authenticated user.”。第三者任意投稿の削除通知ではない|
|[U：Data Use Checkup](https://developers.facebook.com/documentation/resp-plat-initiatives/individual-processes/data-use-checkup?locale=en_US)|2024-09-16|Overview: “annual assessment”。年次の許可用途・Terms/Policies・データ取扱いの認証。本文を統括も再実読。削除イベント通知ではない|
|[O：Threads API overview](https://developers.facebook.com/documentation/threads/overview?locale=en_US)|2025-12-22|本文全文。見出し “Rate limiting”。API上限・機能別制約等。Content complianceという見出しは見当たらない|

P/K/C/M/W/U/Oは統括が通常ブラウザで再実読した。API調査担当の報告だけで採用せず、Pの匿名集計に関する肯定的な補足も加えた。

## 3. 逐条照合：保持・LLM・役割

|条項|条文の要旨（原文の引用は§2）|C3/C4への当てはめ（L3）|
|---|---|---|
|T §12(l),(m)|PDは直接・間接取得、匿名化・集計・派生を含む。処理は収集・保存・共有等を含み自動化に限定されない|本文を消す、件数化する、ローカルに移すだけではPDでなくならない|
|T §3(d)(i)1、2(a)–(f)|Meta/Userの更新削除要求への迅速な対応、正当かつ規約整合的な目的に不要となった場合、サービス停止、Meta要求、User要求/退会、法令、§7による削除|§3(d)(i)冒頭は法令上保持が必要な場合を除外し、同(ii)はその法的根拠の証拠保持を求める（本件の該当性未確認）。固定TTLがなくても無期限保持の許可ではない。24h/72hは測定時刻であり保存期限ではない。必要な期間と終了時の処理が現設計に足りない|
|T §3(d)(i)2(d)|特定User/browser/deviceに関連不能な集計・秘匿・非識別化について、一般Userの要求/退会の箇所に括弧書き例外。後続TPのUser/Client要求・Client退会部分には同じ例外の反復なし|C3のroot ID、永続仮名、C4の少数結果等を含め評価する。例外が成立しても同1や2(a)–(c),(e),(f)まで除外しない。§12(u)のUserはAppのend userなので、全Threads投稿者を自動的に同一視しない。投稿者の削除はAPI固有条件も確認|
|T §3(c)(i),(ii)、§5(a)、§12(r)|TPとして集めたPDは§5(b)の共有経路。非TPは法令、SP、Userの明示指示/同意、非Restrictedデータについて所定の第三者契約等の列挙|own-appだから無条件共有可とはならない。外部LLMが提供サービスのためにPDを処理する場合、SPとしての要件を確認する。任意の一般消費者向けチャット契約で満たすとは断定しない|
|T §5(a)(i)–(iv)|SPが本人の指示と依頼サービスのためだけに処理すること等へ事前書面同意。再委託先への書面条件、遵守責任、利用終了時の処理停止・速やかな削除等|学習offは一条件の手掛かりにすぎない。ログ、キャッシュ、モデル改善等の独自目的、再委託、保有期間、削除を別に照合。今回はプロバイダ未指定で契約未確認。契約締結もしていない|
|T §12(p)、R|合理的にUser/deviceを識別できるPD等はRestricted。指定された除外もある|件数は必ず非Restrictedとも、公開投稿は必ず除外とも言えない。仮名・投稿ID・時刻・語の組合せを含め判定する。RestrictedでなくてもPDであり、共有条件が消えるわけではない|
|T §12(f),(s),(e)、§5(b)|Developer/TP/Clientの定義と、client目的限定・分離・限定された共有|持込型は9/16の共有アプリ横断案とは異なる。しかしapp作成・運営・処理の実態を見ずに非TPと確定しない。TPなら本人client限定の条件を適用し、別client利用へ拡張しない|
|T §3(a)(vii),(viii)、§4、§7(a)|重要な機能/処理変更は事前再審査、許可用途、privacy記載、正確な審査情報と継続遵守|C4の目的が投稿トピック選択の支援であることには整合する余地。ただし自動巡回と履歴保持を加えた事実を説明し、重要変更かと承認範囲を確認。審査通過は保存/共有条件の免除ではない|

ローカルLLMについての「条件付き」は**本人の管理する計算機内で推論し、第三者が入力・出力・ログを受信も閲覧もしない**限定である。外部ホストのモデル、クラウド同期するUI、telemetryで入力を送る機能は別評価。推論と学習・fine-tuningを混同せず、今回の推論評価を学習許可へ拡張しない。

## 4. API権限、Content compliance、元投稿の削除

### C3の取得経路

Pの`threads_read_replies`は本人所有threadへの返信取得が許可用途。**この権限だけを根拠に他人rootを読むことはできない（L2→L3）**。本人が返信したことはrootの所有権を変えない。既存adapterのconversationのdocstringも同権限を想定するが、権限構成を実APIで試してはいない。

一方、Pの`threads_keyword_search`には検索した公開content treeの表示、`threads_manage_mentions`には本人が言及された公開treeの表示という別用途がある。Kには最近検索した公開投稿への返信・引用・再投稿があり、追加権限に従う。これらは**任意の既知rootを24h/72h後に再取得して保存できることの確認ではない**。Mの単一公開post取得とCのconversationも別endpoint。なおPのthreads_basic行は本人投稿の本人向け表示を許可用途とする一方、Mは同権限のAdvanced Access後に他人の公開投稿を取得可能と説明する。技術仕様と許可用途の記述差を本報告だけで解消したとはせず、C3への包括的許可に使わない。代替経路を一律禁止とはせず、検索/言及との関係、必要権限、再取得条件、集計保存用途が未確定として保留する。

Cのconversationはroot-level専用。自分の返信IDを渡して枝全体を取得できるとはしない。全頁取得の成否と、username欠測・可視性の制限を記録しなければ、返信総数・実人数として読めない。Cのusernameは公開ユーザーと本人に限定される。見えた返信の人数と全参加者数を混同しない。

### C4の定期取得とApp Review

Kでは未承認時は本人投稿のみ、承認後は公開投稿検索可。PのRequirementsは非所有・非管理データへのアクセスにはApp Review、Advanced Access申請にはBusiness Verificationを要求する。own-appでも公開データ取得の条件が消えず、作者のアプリの承認を別の利用者アプリへ移せるとは確認していない。

Pはkeyword_searchの利用目的を利用者のsocial media presence管理とし、検索した公開content/treeの表示等を列挙する。**同ページ冒頭と各権限には、アプリ改善やmarketing/advertisingのため、再識別できない集計・非識別化/匿名化情報によるanalytics insightsを要求する補足的な許可もある**。従って「表示以外は一律禁止」「集計用途の許可がない」とは言えない。ただし、この補足で取得権限外のデータまで読めるわけではなく、再識別不能は未証明、保存期間や第三者提供の義務も残る（L3）。

Kは同一語の反復照会もquotaに数えると明記する。定期照会という形式だけを禁止する根拠は見当たらない。上限はユーザー当たりrolling 24hに2,200 queries、アプリ横断で合算、同一語の再照会も加算、0件結果は非加算。語を何個登録するか・他アプリ使用量・再試行は未確認であり、「1日1回だから必ず上限内」とは判定しない。敏感/不適切と判定された語にも空配列が返り得るため、0件＝関心ゼロとは読めない。

`docs/手順_AppReview_2026-09-14.md:162,188`は、人がトピックを決める前に検索し、集計を表示、本文は一度だけ画面に表示して保存しないという説明。C4は同じ投稿支援目的に沿う余地があるが、**選んだ語を自動で毎日取得し、集計履歴を本人repoに保持することは追加の処理**。目的・頻度・保存項目・保持終了条件・LLM経路を実態どおり説明する必要がある。重要変更に該当すればT §3(a)(vii)の事前再審査条件が働く。提出済み実文・Dashboard・審査結果は見ていないので、その説明でC4が承認済みとも必ず再審査対象とも断定しない。一般的な承認だけで規約の例外を得たとは扱わない。

### 削除・変更への追随

**依頼のContent complianceという語をMetaの固有条項名と仮定しない。** 今回読めたT/D、Threads O/K/C/M/W、投稿取得の入口では、その見出しに基づく「元投稿の消失に同期して過去の匿名集計を削除/変更する」という具体規定を特定できなかった。不存在の証明ではなく、Metaの一般削除義務はT §3(d)で評価する。調査担当もThreads公式内検索で同名の結果なしを確認し、top・Get Started・Reference・Delete Postsを含め限定確認した（2026-09-18）。repo中の同名の用語はXの照合・設計にも出てくるが、**Xの削除期限やContent complianceをMetaへ転用しない**。UのData Use Checkupも別制度である。

|事象|明示義務／今回決まらないこと|設計上の扱い（提案、未実装）|
|---|---|---|
|Meta/App Userから更新・削除要求|法令上保持が必要な場合の例外（T §3(d)(i)冒頭、(ii)証拠保持）を除き、T §3(d)(i)1の迅速な対応。2の削除条件も個別適用|要求受付、対象識別、管理下コピー・外部SPへの対応が必要。匿名集計というだけで受付不要にはしない|
|不要化・停止・Meta削除要求等|上記の法令保持例外を除き、T §3(d)(i)2の条件に該当すれば速やかな削除が必要|目的・保持終了条件を定める。本文を保存しないことだけでは対応にならない|
|第三者の元投稿が削除・非公開・変更|この出来事だけで過去の件数行すべてを必ず消すか、匿名化の条件を満たした集計を保持できるかは、読めた条文から一意に決まらない|「残してよい」とも「必ず全部削除」とも断定しない。影響範囲を判断できない場合の行全体の廃棄を含む方針を決める|
|C3の返信者の一人が投稿を消す|participant_countもPD。永続author_keyやroot IDがある場合、関連不能という例外の成立は未確認|観測差だけで削除・非公開・権限変化を断定しない。単純に人数を1減らせるとは限らない（同じ人の他の返信が残る場合等）|
|C4が元post_idを捨てる|特定投稿の寄与を確実に特定・選択除外する対応表が残らない|行全体の廃棄は可能。過去の検索結果を再取得できる保証もない。IDを捨てることを削除義務の免除にしない|

Wのdelete通知は認証ユーザーが削除した投稿が対象。C3/C4の任意の第三者投稿の削除・非公開化を網羅する通知としては使えない。これは**今回確認したWebhookだけでは完全追随を証明できない**という意味で、あらゆる対処方法が不可能という意味ではない。APIエラーや再検索の不在は削除の確定通知ではない。義務の適用と通知能力の限界を混ぜて、守れないから免除されるとはしない。

## 5. 保留を解除する前に残る確認

1. C3の他人rootについて、利用目的に合う取得権限・endpoint・検索/言及との関係を固定する。threads_read_repliesだけの経路は採用しない。C4は当該利用者アプリの公開検索承認と、日次観測・履歴保持を含む用途の整合を確認する。
2. 永続保存する列・ファイル名・既存台帳とのjoin、author_key集合の扱い、検索条件と母数を固定する。PD/Restricted/関連不能な集計を別々に判定する。
3. 保持目的と期間、不要化・要求・非公開化等での削除/更新方針、git履歴・バックアップ・同期先・SP保有分の扱いを定める。今回条文で決まらない過去集計の扱いは未決のまま明示する。
4. LLMを本人の完全ローカル推論か外部SPかで分け、外部なら提供先と書面条件・独自目的利用・再委託・保持/削除を照合する。今回は具体的サービスの適合を認定していない。

これは確認事項の報告であり、外部照会・契約締結を依頼または実行したものではない。C3/C4の実装は元タスクが担当し、今回の結論で保留を解除しない。

## 6. 検証・証跡・未検証

- 対象設計SHA256: `6378fb3a9619af564863814967c402ef9f5a2cd93183c4fbe82b7864e699ba02`。
- 依頼書SHA256: `9f2a13c6d9b5a5aa029278482bd0ed041c914fd1366314b5aa83b399bccf6680`。
- 再現: 対象revisionで設計§3 C3/C4・§5、`base.py`のauthor_key、Threads adapterのconversation/keyword_search、`threads_read_cli.search_material`、App Review説明の上記行を読む。公式URLの更新表示と指定節を通常ブラウザで照合する。動的文書は後日の同一性を保証しない。
- 統括が設計・コード・主要一次資料と報告を照合。調査担当はAPI資料の収集、独立監査担当はread-onlyでT/E/Rと主要判断を反証確認した。独立監査のP再取得はブラウザ利用不能/取得不可となり未実読。Pは統括が現物確認し、この制約を独立確認済みとは扱わない。
- 実利用者台帳・秘密設定・個別契約・Dashboard・実提出内容・App Review結果・LLMの実通信/保持・本番を未検証。実装変更も実APIによる試行もなく、コードテストは実施対象外。
- 納品対象は本報告書1ファイルのみ。専用branchへcommit、pushなし。同名写しは`/Users/masaru/thth-exchange/out/`へ排他的に新規保存し、同一SHA256を確認する。既存ファイルは上書きしない。
