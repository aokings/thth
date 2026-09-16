# 照合「X Developer Agreement と自分の泉」（Codex 執筆・2026-09-16）——開発セッションの検収つき

**法的助言ではない。条文と設計の突き合わせ表。裁定は masaru。** 本文は Codex が `~/thth-exchange/in/2026-09-16_依頼_照合_XのDeveloperAgreement_Codex.md` に応えて書いたもの（原本 `~/thth-exchange/out/2026-09-16_照合_XのDeveloperAgreement_Codex.md`）。下の「検収」は開発セッション（Opus 5）が確かめた範囲。

## 検収（開発セッション・2026-09-16 夜）

**判定: 裁定の材料として十分。結論は「R（読む）は今のまま足せない」。**

### コードの現物で確かめたこと（L1）——**どれも正しかった。1 つは X と無関係に直すべき抜け**

1. **絡みの台帳は git に入っていない**（Codex §1(c)）。投稿の commit（`core.py` の `commit_and_push(rel_path=原稿)`）は原稿ファイルだけ、採取の commit（`collect.py` の `result["touched"]`）は採取で触ったファイルだけを add する。同席送信（`_send_locked`）は commit しない。**`data/sns/engagements/` は VM の repo に未追跡のまま溜まる。** 同期（`sync_repo`＝fetch → `pull --ff-only` → HEAD==@{u}）は未追跡を見ないので**運用は止まらない**。しかし SKILL.md・使い方・kopicha への案内の「利用者の git に残る」は誤りで、VM を作り直すと消える。→ **T9 で直す。**
2. **自分の投稿への返信の台帳は、相手の `username` と本文を保存している**（Codex §1 表の replies 行・`collect.py` の `{**row, …}`）。v1 からの仕様で、設計「自分の泉」§1 も「維持」としていた。**案内と SKILL の「相手の本文・名前は残らない」は読む口（`thread`・`where`・`who`）の話で、言い方が広すぎた。** → 文言を直す。
3. **engagements の `post_id`・`reply_to` は生の id**（ハッシュではない）。依頼文の「post_id のハッシュ」は開発セッションの書き誤り（share の頃の名残）。設計書に誤記は無い。
4. **runs に検索語（`words`）と生の post_id が残る**——正しい。読む行為の記録として意図どおり。
5. **Bluesky の一部の失敗は素の `RuntimeError`** で、`thread`・`where`・`who`・`threads_read_cli` のどれも捕まえていない（`AdapterError` は `RuntimeError` の子で、逆は成り立たない）→ 素の traceback。→ **T9 で直す。**
6. `thread_read` の `max_messages` は**取得した後に切る**ので費用の上限にならない——正しい。無料の媒体では問題にならない。X を足すなら要る（記録のみ）。

### 一次資料で確かめたこと（L2・2026-09-16 に開発セッションが同じページを取得）

- **Display requirements**: 「The following general principles apply to all display mediums.」——**原文どおり**。オンライン表示に投稿者のプロフィール画像・@username・表示名（プロフィールへのリンク）、「may not be altered or modified」な本文、permalink へリンクする時刻、X ロゴを要求——**原文どおり**。**CLI・非公開・個人用の免除の記述は無い**（開発セッションも見つけられなかった）。
- **Developer Agreement III.A**（更新 2026-04-27）: 「use the X API or X Content to fine-tune or train a foundation or frontier model」——**原文どおり**（禁じているのは訓練）。第三者への提供の禁止「…or otherwise transfer or provide access to, in whole or in part, the Licensed Material to any third party except as expressly permitted」——**原文どおり**。
- **Automation rules**（help.x.com）: **開発セッションの環境からは 403 で再読できなかった。** U II.B.2（語の検索だけを起点にした自動返信の禁止）・II.B.3（AI の返信 bot は事前の書面承認）は **Codex の読解に依拠**する。

### 開発セッションの読み（L3・裁定の材料）

1. **R（検索・枝を画面に出して LLM が読む）は、今の形のまま X に足すと表示要件に当たる読みが強い。** 要件は「all display mediums」で免除の記述が無く、THTH の `thread`（本文 120 字切り）・`where`（60 字の preview）・画像なしの表示は満たさない。
2. **本当の関所は「API 由来の本文を Claude に読ませる」ことが第三者への提供に当たるか。** 禁止が明文なのは訓練で、推論への入力は条文から決まらない（Codex と同意見）。表示を X の要件どおりに作り直しても、ここが白にならない限り THTH の芯（判断は LLM）が X では成り立たない。
3. **W（人の二段承認つき返信）は AI 返信 bot の規則に当たるかが不明**。U を再読できていないので、開発セッションの判断は保留。
4. **M（自分の投稿の公開指標）は条件付きで通る読み**。ただし削除・保護化への追随（ID・数・git 履歴まで）が要り、今のコードに削除の処理は無い。
5. **Meta との差の要点**: Meta は「集めて他人に返す」が壁だった。X はそれに加えて**「画面の出し方」と「LLM に読ませること」自体**が壁になりうる。自分の泉の芯（その場で読み、LLM が判断する）に直接当たる。

**推薦**: X は**保留**。進めたいなら、Codex §7-2・7-3 の形（非学習の外部 LLM・実際の CLI 表示例・二段承認・自分の指標の時系列・利用者ごとの App 方式を添えて、条項ごとに回答を求める）で X に照会。回答の保証は無い。

---

# X Developer Agreement と「自分の泉」の逐条照合

取得・照合日：2026-09-16（JST）。対象：`/Volumes/NexDev/Developer/thth`、main、`54d61755f7c8194c556cfda47ac51895439c292b`。開始時の作業ツリーに差分なし。
**法的助言ではなく、条文と設計の突き合わせ表。裁定は masaru に残す。** 利用者横断の泉・販売は対象外。
L1＝指定revisionのコード・文書の現物確認（実APIでの実測ではない）、L2＝今回取得した一次資料、L3＝当てはめ・推測・推薦。条文がL2でも設計の該当性はL3。
指定の設計全文、前回Meta照合と検収欄、指定7コード全文、設計v2 §4を読んだ。Xアダプタは現REGISTRYにないため、以下は既存経路をXへ移す場合の照合であり、既にX規約に違反して運用中という報告ではない（L1）。

## 0. 一枚で言うと

|段階|判定（設計への当てはめ＝L3）|判断の範囲|
|---|---|---|
|R：検索・枝を表示しLLMが読む|**不許容：現行CLI表示をそのまま移す案**|非保存でも表示要件は残る。画像・表示名・ロゴ等がない現表示はDに適合しない。表示を変更／Xと調整した後も、外部LLMへの提供可否は**条文からは読めない**。AI推論そのものの一律禁止を発見したわけではない。|
|W：個別の二段承認後に投稿・返信|**条文からは読めない**|本人が確認した通常投稿は同意等を満たす条件付きの経路。だがAI下書き＋人の承認がU II.B.3のAI reply botに該当するか、除外の明文がない。投稿と返信を一括して承認済みとは扱えない。|
|M：自分の投稿の公開指標を取得・保存|**条件付き**|指標取得の公式機能はある。用途申告、削除・変更対応、保存範囲と予算管理が必要。現`collect`は返信本文も保存し、履歴削除も持たないので、そのままの転用を合格にはしない。|

Mの条件（L3、根拠A/Pと§1）：
- X側の承認用途を本人の投稿分析に限定し、公開指標だけを採る。X全体の性能・利用状況の競争比較や人物プロファイルへ広げない。
- 返信本文を採集台帳へ落とす現経路を分離し、保存項目を確定する。欠測を0にしない。
- 投稿削除・保護化・編集・契約終了を、ID・数値・派生・git履歴まで処理できるようにする。過去時点の数値を継続保持できる範囲はXへ確認する。
- 従量の支出上限を設ける。API利用資格とユーザーの認可を分け、各利用者に同一用途Appを登録させる配布方式を無条件に採用しない。

## 1. データの流れ 1 枚

`X API → adapter → メモリ内の投稿/会話 → stdout（人/LLM） → 原稿・個別承認 → X投稿 → 自分のID/指標等の台帳`。
**stdout→外部LLM事業者・会話履歴**も別の受領／保持経路。THTHがファイルへ書かないことだけでは、この経路まで消えない（L1境界／L3適用）。

### (a) 取得するもの／(b) 画面に出して捨てるもの

|項目|由来・加工|保持・現物根拠（L1）|
|---|---|---|
|根・返信：ID、親ID、根ID、投稿者、時刻、本文、媒体、author_key、reply_deadline|adapterの`fetch_post/conversation`。`Message`へ正規化。BlueskyではDIDから仮名を生成|`thread_read.answer`内のメモリ。`base.py:161,197`、`bluesky.py`の`_message/fetch_post`。Xのfield対応は未実装。|
|枝の出力：root、messages、counts、you_and_them、provenance|全文＋深さ・自分判定・返信済み印・過去の反応を合成|`thread_read.py:287,327`。JSONは本文全文とusername、人向けは各120字。明示的なメモリ消去や端末履歴消去はない。|
|検索：post_id、permalink、時刻、author、author_key、返信有無／数、返信済み印、preview|検索の生本文を空白正規化し先頭60字＋省略記号。語別件数・履歴を併記|`where_cli.py:88,160,281`、`threads_read_cli.py:135`。human/JSONともpreviewを返し、結果本文はdata/runsへ書かない。|
|公開指標|API指標を`metrics/available`へ変換。不存在項目はnull、取得失敗と区別|`collect.py:523`。Mでは本文も応答に含まれ得るが、必要な数だけ取り出すX側処理は未実装。|

### (c) 利用者のrepoに残るもの／gitに入るもの（実装からの列挙）

保存先は設定された利用者repoの`data/sns/`。repoがなければstate内のローカル保存であり、必ずgitとは限らない。採集のcommit/push経路は`collect.py:921,1010`。**engagementsはrepo内に追記されるが、通常の投稿・採集commit対象には入らない**（`core.py:707,759–780`、`writeback.py:339–353`、`collect.py:1007–1012`）。repo内の保存とgit履歴への保存は異なる。本照合では実行していない。
以下の「期限なし」は**自動削除期限がコードにない**意味で、永続保存を規約が許可した意味ではない。

|台帳・項目|由来・加工|保持期間|
|---|---|---|
|engagements：`schema`|定数`thth.engagement.v1`|月別追記・期限なし|
|`post_id`|公開結果の自分の返信ID。**生ID**|同上|
|`reply_to`|原稿／送信引数の相手投稿ID。生ID|同上|
|`root_post`|`reply_to_root`を形検査。生IDまたはnull|同上|
|`author_key`|入力の16hex、なければ返信先を取得して補完。媒体＋identityのSHA-256先頭16hex|同上|
|`account`、`medium`|ローカルaccount名、媒体名|同上|
|`topic`、`form`|原稿等のメタデータ。自由入力を含む|同上|
|`hour_band`|投稿時刻を帯へ変換|同上|
|`posted_at`|公開時刻|同上|
|`found_by`|where_to_appear/manual/mentionの3値またはnull|同上|
|`recorded_at`|追記時刻を補充|同上|
|insights/posts：`post_id`、`reply_to`|自分／相手の生ID。filename変換もハッシュ化ではない|追記・期限なし|
|`file`、`source`|原稿basename（同席送信はnull）、queue/sent|同上|
|`account`、`medium`|採集時点のローカル設定|同上|
|`topic`|原稿の語を正規化|同上|
|`text_length`、`has_link`|**自分の原稿**の文字数・URL有無|同上|
|`collected_at`、`posted_at`、`age_hours`|採取時刻、投稿時刻、その差を小数2桁|同上|
|`marks`|1/6/24/72/168hの採取済み刻み|同上|
|`metrics`|APIの数。標準名はviews/likes/replies/reposts/quotes/shares、不在はnull。追加指標も辞書として渡る|同上|
|replies：**adapter行全体**＋`kind`、`post_id`、`collected_at`|`**row`を無選別で複写。Blueskyではmessage_id、**username、text**、timestamp、replied_to、root_post、medium、author_key、reply_deadlineも保存|ID重複を除いて追記・期限なし。編集更新・消えた行の削除なし|
|replies取得記録：`kind`、`post_id`、`collected_at`、`age_hours`、`marks`、`trigger`、`source`、`replies`、`id_missing`|取得事実・対象生ID・件数・識別子欠落数。返信0件も記録|同上|
|insights/account：`account`、`date`、`collected_at`、`metrics`|能力のあるadapterだけ、前日の日次指標を複写|追記・期限なし。Xで有効にするとは未決定|
|inbox：adapter行全体＋`kind`、`collected_at`|能力があるときだけ。本文等を落とす検査なし|追記・期限なし。**今回の公開投稿R/W/Mには含めず、Xで有効にしない前提**|

根拠：`core.py:518–567`＋`engagements.py:77–97`、`collect.py:400–443,545–585,708–846`。engagementsは13項目、insights/postsは14項目。**engagementsの1行は、アプリが保存するもの全部ではない**（L1）。
依頼中の「post_idのハッシュ」は現実装と異なる。投稿IDは生で残り、仮名化するのはauthor_key。saltなしの決定的な64bit表現であり、匿名化済みの証明にはならない（`base.py:144–158`、L1／L3）。

**git台帳以外の保持（L1）：** `thread`はaction/account/medium/post_id/messages/truncated/status/error、`where`はaction/account/**words**/n/status/errorをrunsへ渡す。`runs.py:73–119`がrun_id・modeを補い、共通fieldとoptional field（未指定はnull）をstateに追記。**読む行為自体と検索語・生IDは残る**。collectのinbox_stateは`at/state/permission/skipped/count`等を上書きする。
**保証の範囲（L1）：** Rの正常経路はAPI本文を台帳へ渡さない。`FORBIDDEN_KEYS`はengagements/runsのトップレベル鍵検査であり、自由文・入れ子・`error`内に本文が入らないことを意味的に証明しない。エラー文のredactも本文一般の除去ではない。
**例外経路（L1）：** Blueskyの一部は素のRuntimeErrorを投げ、R側のAdapterError捕捉から漏れてstderrへ出る。失敗時まで全経路で「本文が残らない」とは証明されていない（`bluesky.py:256,388,516`、`thread_read.py:445`）。
**保証の外（L1境界／L3）：** stdoutのリダイレクト、端末ログ、MCPやLLMの履歴、利用者の転記、返信原稿への引用。`collect`の返信本文保存は単なる外部操作ではなく**現実装の別経路**。`collect_days=14`は採取対象の年齢制限で、14日後削除ではない。`after_cli.py`は台帳の読取り・集計のみで保存しない。集計窓30日も保存期限ではない。

## 2. 逐条表

全X資料の取得日は2026-09-16。旧[agreement-and-policy](https://developer.x.com/en/developer-terms/agreement-and-policy)はPへ転送され、Agreementは別URLになっていた。

|略号・一次資料URL|公開／更新表示・実読範囲|
|---|---|
|[A：Developer Agreement](https://docs.x.com/developer-terms/agreement)|更新2026-04-27。前文、I–XIV全文|
|[P：Developer Policy](https://docs.x.com/developer-terms/policy)|更新表示なし。X + Developers〜X passwords全文|
|[Q：Restricted uses](https://docs.x.com/developer-terms/restricted-use-cases)|更新表示なし。全文。A II.Cの組込み規則|
|[D：Display requirements](https://docs.x.com/developer-terms/display-requirements)|更新表示なし。General／Online／Broadcast／Verbal全文|
|[U：Automation rules](https://help.x.com/en/rules-and-policies/x-automation)|更新2026-04。I・II全文|
|[C：料金](https://docs.x.com/x-api/getting-started/pricing)|更新表示なし。料金表・Owned Reads・credits・dedup・limitsを確認|

引用は原文の短い断片、当てはめは別欄。番号のない資料は**節見出し**を位置として使う。判定は「当該条項だけ」の評価で全体の合格ではない。R/W/M欄と判定はL3、引用はL2、実装対照はL1。

|条項・URL／節|原文の短い引用|Rへの当てはめ|Wへの当てはめ|Mへの当てはめ|判定 R／W／M|
|---|---|---|---|---|---|
|[A](https://docs.x.com/developer-terms/agreement) I.4,10,12 定義|“copies and derivative works”|API・投稿・IDが対象|投稿支援もAPI利用|数やIDもAPI由来。本文なしで除外にならない|条件付き／条件付き／条件付き|
|[A](https://docs.x.com/developer-terms/agreement) II.A.1–4 許諾|“explicitly approved”|表示・分析の用途承認が前提|用途に投稿支援を含める|本人分析の用途を申告|条件付き／条件付き／条件付き|
|[A](https://docs.x.com/developer-terms/agreement) III.A(d) 提供制限|“third party”|外部LLM受領を無視できない|本文引用の再利用は別問題|private gitでもホスト等の受領を確認|条文からは読めない／条件付き／条件付き|
|[P](https://docs.x.com/developer-terms/policy) Content redistribution|“Post IDs”|表示許諾と、LLMへの素材提供を分ける|相手本文の丸ごと提供は別許諾|他人へ配布しない前提でも例外許可を創作しない|条文からは読めない／条件付き／条件付き|
|[A](https://docs.x.com/developer-terms/agreement) IV.B＋[P](https://docs.x.com/developer-terms/policy) Content compliance|“delete or modify”|runs中のID・外部履歴も検討|相手ID／取得コピーの同期|数・ID・履歴を処理する必要|条件付き／条件付き／条件付き|
|[A](https://docs.x.com/developer-terms/agreement) VII.I 終了|“permanently delete”|保存分の終了時処理|API由来コピーと独立原稿を区別|git履歴を残して完了とは扱えない|条件付き／条件付き／条件付き|
|[P](https://docs.x.com/developer-terms/policy) Public display of Posts|“most current version”|利用者向け表示も最新状態で|返信前に元投稿の状態を確認|過去の数を現在値として表示しない|条件付き／条件付き／条件付き|
|[D](https://docs.x.com/developer-terms/display-requirements) General／Online|“all display mediums”|既存CLIの省略本文・画像等欠落は不適合|元投稿を同じ表示で見せるなら同じ|数字だけの画面へのPost anatomy適用は明示なし|不許容／条件付き／条文からは読めない|
|[A](https://docs.x.com/developer-terms/agreement) III.A(k)|“fine-tune or train”|訓練なし推論を直接禁じる文言ではない|下書き生成の全面許可でもない|訓練へ流用しない|条件付き／条件付き／条件付き|
|[Q](https://docs.x.com/developer-terms/restricted-use-cases) Sensitive information|“Never derive or infer”|人物の政治・健康等の推定は非保存でも不可|返信案から人物像を作らない|仮名履歴を敏感属性へ結ばない|条件付き／条件付き／条件付き|
|[Q](https://docs.x.com/developer-terms/restricted-use-cases) Off-X matching|“reasonably expect”|同じ話題を媒体別に読むことと人物照合を分ける|別SNSの同名人物に結び付けない|仮名化だけで例外とはしない|条件付き／条件付き／条件付き|
|[U](https://help.x.com/en/rules-and-policies/x-automation) II.B.2|“keyword searches alone”|検索自体を禁止する条項ではない|検索起点の無差別自動返信は禁止。送信者承認と受信者opt-inは別|測定自体は返信でない|許容／条件付き／許容|
|[U](https://help.x.com/en/rules-and-policies/x-automation) II.B.3|“prior written and explicit approval”|読むだけはreply botでない|二段承認の下書き補助への該当性不明|測るだけはreply botでない|許容／条文からは読めない／許容|
|[P](https://docs.x.com/developer-terms/policy) Consent & permissions＋[U](https://help.x.com/en/rules-and-policies/x-automation) II.A|“Show exactly what will be published”|OAuthだけで用途同意を代替しない|本文・宛先・媒体等の個別同意を維持|認可範囲を超えない|条件付き／条件付き／条件付き|
|[Q](https://docs.x.com/developer-terms/restricted-use-cases) Multiple applications|“end users”|同じCLIのため各人にApp登録を要求する案は問題|同じ|同じ|条件付き／条件付き／条件付き|
|[A](https://docs.x.com/developer-terms/agreement) III.D,J–M／VII、[C](https://docs.x.com/x-api/getting-started/pricing) Spending limits|“Rate Limits”|課金とrequest上限は別|支払っても自動返信制限は免除されない|lookupも読み課金|条件付き／条件付き／条件付き|
|[A](https://docs.x.com/developer-terms/agreement) VIII 監査|“inspect and audit”|非保存の説明と実経路を一致させる|承認・利用用途の記録を説明できること|保存・削除の証拠を提示できること|条件付き／条件付き／条件付き|

**表示（L2→L3）：** D Onlineは、投稿者画像・表示名・@usernameとプロフィールリンク、改変しない本文、リンク付き日時、Xロゴ等を要求。操作群は“View on X”で代替できるが、ロゴ等全部の代替ではない。非保存・一人用CLIの明示免除は読めなかった。現`thread`の120字切取り／`where`の60字加工にも問題がある。CLI一般の禁止ではなく、**この表示仕様の不適合**。
**人物照合（L3）：** 話題を媒体別に並べるだけなら、同一人物へ結び付ける処理とは異なる。author_keyは媒体を含むが、それだけでOff-X matchingの適用除外にはならない。会話上の論点整理と政治・健康等の個人属性推定を分ける。
**配布方式（L2→L3）：** Q Multiple applicationsは利用者が登録するAppにも適用すると明記。単独の自作用Appと、配布CLIの全利用者に同用途App作成を求めることを区別する。データ横断をやめても後者の問題は残る。

### 削除された投稿について、手元の何をどう扱うか

A IV.B／P Content complianceは、削除だけでなく保護化・停止・変更等への追随を求める。**速やかに対応し、Xまたは当該利用者の要求を受けた場合は24時間以内**。全投稿を一律24時間で捨てる規定ではない（L2）。

|手元のもの|適用と必要な処理案（L3）|
|---|---|
|保存していない本文|THTH台帳で消す本文はない。ただし表示・端末／LLM側コピーの残存は別途確認する。|
|生ID、reply_to、root_post、runsのpost_id|ID自体がA I.12の対象。対象IDを索引に、記録を除去／必要部分を変更する運用が必要。自分の独立した行為記録まで一律全削除とは断定しない。|
|post_idハッシュ（将来もし作る場合）、author_key|ハッシュ化による免除なし。関連付けと出自を追える形で処理。author_keyの全履歴削除が常に必要とは未確定。|
|削除された自分の投稿に結び付く公開指標|API由来の数なので除外根拠なし。対象数値・そのAPI由来集計を除去／再計算する設計を推薦。削除後も匿名集計を保持できる範囲は照会。|
|過去の観測値（24h時点の数等）|現値に上書きすると測定記録の意味が変わる。**時点付きの履歴保存をどこまで認めるかは条文だけでは読めない**。保存目的・表示方法を提示して確認。|
|git・remote・backup|現行ファイル削除だけで古いobjectは消えない。履歴・複製・cacheも管理対象に含める。現コードにこの削除処理はない。|
|独立執筆した自分の原稿|APIから得たコピーと区別。全ての下書きが派生物だと断定しない。引用・要約を含む場合は個別に由来を評価。|

## 3. 「LLM に読ませる」は条文上どう当たるか

**「訓練」と「推論入力」は文言上同じではない。** A III.A(k)は基盤／フロンティアモデルの微調整・訓練を対象とし、Q末尾も同趣旨（Grok例外の記述あり）。非学習の推論入力を、この禁止だけで不許容とは読まない。ただし「入力を包括許可する条文」でもない（L2→L3）。

|実際の流れ|判定と未決点（L3）|
|---|---|
|ローカルモデルが端末上だけで推論、重み更新なし|学習禁止の直接該当は避けられる読み。用途承認・表示・人物属性推定等の条件は残る。外部提供がないことの実確認が要る。|
|Claude等の外部サービスへ画面／stdout／tool結果を送る|**条文からは読めない**。「画面」という形式にしてもAPI由来本文。A III.A(d)／P再配布の許諾、LLM事業者の受領条件と保存を確認する必要がある。自動転送をID限定原則の外で許す一般例外は確認できない。|
|人が画面をコピーして渡す|非自動の例外候補でも、受領者の規約同意等が残る。人のクリックだけで解決としない。|
|生成した返信を人が個別に承認してAPI投稿|WのAI reply bot該当性は未確定。U II.B.3は事前の書面承認を要求するが、人の下書き補助が必ず該当するとの定義も、二段承認の免除もない。|
|提供先で入力がモデル訓練へ回る|非学習という本件の前提を失う。契約・設定・実処理を確認できなければこの経路を開始しない。|

**原典間の不整合（L2）：** P Content redistributionは非自動の公開Post/User Objects提供を1人1日500件、Q同節は受領者1日50,000件と記す。どちらも第三者の規約同意が条件。多い方を採ってLLM送信を正当化しない。自動転送・外部推論をこの例外に当てはめる可否も未確定。
**「保存しない」の主語（L1／L3）：** THTH・端末・MCPクライアント・LLM事業者・gitを分ける。API本文を一旦画面に出すことは、由来を消す変換ではない。外部LLMへの提供承認と、公開する返信の承認は別の確認事項。

## 4. 費用と上限

### 現在の単価と9月13日との差

[C料金](https://docs.x.com/x-api/getting-started/pricing) Credit consumption details／Owned Reads（取得2026-09-16、L2）。金額はUSD。税等は本試算に含めない。

|項目|9月13日の設計記録|今回の公開料金|比較|
|---|---:|---:|---|
|Post作成|$0.015|$0.015／request|一致|
|URL付き作成|$0.200|$0.200／request|一致|
|Post読取|$0.005|$0.005／resource|一致|
|Owned Reads|$0.001|$0.001／resource|一致、適用範囲注意|
|User読取|$0.010|$0.010／resource|一致|

Owned Readsは**開発Appの所有者＝認証ユーザー＝列挙されたuser endpointの対象**という条件。`GET /2/users/{id}/tweets`は列挙されるが、`GET /2/tweets/{id}`と会話検索は列挙されない。自分の投稿の個別lookupを一律$0.001と見積もらない（L2→L3）。
最低額について、今回は“No contracts, subscriptions, or minimum spend.”と**最低支出なしが明記**。ただし購入画面の最低チャージ単位は未確認。「最低購入額記載なし」から任意の微小額を購入可能とは断定しない。

|指定したRの量（計算＝L3）|Post返却数|基準額|
|---|---:|---:|
|月30回×5語×20件|3,000|$15.00|
|60枝×50件|3,000|$15.00|
|合計|6,000|**$30.00／月**|

これは**Postだけの基準額で総額上限ではない**。全件を標準料金で課金し、重複割引なしの仮定。枝の50件に根を含めず別取得するなら60件＝$0.30を追加。投稿者名・画像のUser取得、追加ページ、編集／削除確認、W/M、税・為替、LLM費用は別。User expansionの課金適用は今回の文言だけでは確定できず、Console等で確認が要る。もし6,000 User resourcesが別課金ならさらに$60という感度計算になる（L3、実請求ではない）。
同一資源のUTC日内重複課金抑制は**soft guarantee**で、絶対保証ではない。月300万Post readsの枠とドル上限も別（C Deduplication／Read operations、L2）。

### 読む口・アクセス・上限

以下は一次資料の仕様で、当該アカウントの実権限・レスポンスは未測定（L2）。各資料の更新表示なし、取得2026-09-16。

|用途・endpoint|アクセス／認証／範囲|主な上限・根拠|
|---|---|---|
|R検索：`GET /2/tweets/search/recent`|全developer向け、直近7日。App-onlyまたはuser context|10既定・最大100件。app 450／15分、user 300／15分。[Search Overview](https://docs.x.com/x-api/posts/search/introduction)・[Authentication](https://docs.x.com/x-api/posts/search/integrate/overview)・[Rate Limits / Recent search](https://docs.x.com/x-api/fundamentals/rate-limits)|
|古い枝：`GET /2/tweets/search/all`|Pay-per-use／Enterprise、2006年3月まで遡る。**App-only限定**|最大500件、app 300／15分かつ1／秒。同上。Rate表のuser欄にも1／秒があるが、認証GuideはApp-onlyと明記するためuser token対応としない。|
|R会話：検索の`conversation_id:<根ID>`|親子は`referenced_tweets`、根はconversation_id。必要ならlookupで根を得る|検索の時間窓・ページング・アクセス範囲に従う。[Conversation ID / Getting a full conversation](https://docs.x.com/x-api/fundamentals/conversation-id)。7日超の返信、削除等を含む「完全な枝」をrecentだけで保証しない。|
|M：`GET /2/tweets/{id}`／`GET /2/tweets?ids=…`|`tweet.fields=public_metrics`。公開のimpression_count/like_count/reply_countをviews/likes/repliesへ写す案|単件 app450/user900、複数 app3,500/user5,000（各15分）。[Metrics / Requesting metrics](https://docs.x.com/x-api/fundamentals/metrics)・[Rate Limits / Tweets lookup](https://docs.x.com/x-api/fundamentals/rate-limits)|
|M別経路：`GET /2/users/{id}/tweets`|自分のtimeline＋public_metrics。Owned Reads適用は上記の所有条件次第|app10,000/user900／15分。[Rate Limits / Timelines](https://docs.x.com/x-api/fundamentals/rate-limits)|

公開impressionsはユニーク人数ではない。非公開クリック・organic/promotedは本人user認証＋作成後30日以内という別条件で、今回は対象外（[Metrics / Metric types・Metric definitions](https://docs.x.com/x-api/fundamentals/metrics)、L2）。
「本人のXアカウント資格」をuser OAuth tokenだけと定義すると、full archiveには足りない。developer App資格も必要という設計差を残す。[Getting Access](https://docs.x.com/x-api/getting-started/getting-access)と検索Authentication（L2）。

### creditsと機械的な停止

前払いcreditsをConsoleで購入し利用時に控除。残高が僅かに負になる場合もあり、補充までリクエスト停止。auto-rechargeは任意設定で、5分に1回まで・残高0以下では動かない。**ConsoleのSpending limit**は請求周期ごとのドル上限で、到達後は次周期まで停止（C Credit balance／Auto-recharge／Spending limits、L2）。
[Usage / Endpoints](https://docs.x.com/x-api/usage/introduction)の`GET /2/usage/tweets`は消費数、[Get usage credits](https://docs.x.com/x-api/usage/get-usage-credits)の`GET /2/usage/credits`はUSD残高を読む口。**ドル上限を設定する公開write APIは今回の確認範囲では見つからない**。Console設定があることとプログラム設定可能であることを混ぜない。
推薦（L3）：Console上限＋auto-recharge offを基本に、THTH側でもページを取る前に最大返却資源数分の予算を予約し、並行処理で共有する。`max_messages`で取得後に切るだけでは費用上限にならない（現`thread_read.py:249–280`は会話取得後に切る、L1）。429と残高・予算不足を別理由で止め、本文を課金台帳に入れない。
A VIIはsubscriptionを中心とする記述を残す一方、Cは従量・subscriptionなし。Policyにも旧plan名が残る。個別契約／Consoleでの適用条件は未確認で、旧定額枠を現在料金に混ぜていない。

## 5. Meta との差

Meta側は指定の[前回照合・検収欄](/Volumes/NexDev/Developer/thth/docs/照合_MetaPlatformTerms_泉_2026-09-16_Codex.md)を根拠とする。今回MetaのTerms／Keyword Search原URLを再取得したがweb取得エラーで読めなかった。下表のMeta側は**同日先行照合の記録（L1、その中のL2読解）**であり、今回再検証済みの現行条文とはしない。

|同じ設計の点|Meta（先行照合）|X（今回L2→L3）|
|---|---|---|
|公開投稿を検索して表示|Threads Keyword Searchの機能・権限はある。審査／用途制限があり、公開だから自由ではない|検索機能はあるがDの具体的表示要件が既存CLIに追加の障害。検索可能＝このCLI表示を許可、ではない|
|非保存のLLM判断|前回は外部LLM推論の包括許可を判定していない|訓練禁止と推論は区別できるが、外部提供・表示・AI返信の条件を独立に照合する必要|
|本人用の数の保存|前回§6は条件付き。API由来の匿名／派生も対象|ID・API数値・派生の対象性と削除義務がある。git保持だけで免除にならない|
|各自Appを持ち込む配布|前回も持込みだけで全用途を許容していない|Q Multiple applicationsは利用者自身のApp登録にも明文で制限。Metaと同じ導入手順を流用できない|
|承認して返信|本人代理の事前同意が必要|加えてAI reply bot該当性、受信者opt-inの問題。二段承認を明文免除とは扱えない|

**「この設計はMetaでは通ったがXでは通らない」とはまだ言えない**。Metaの先行結論も本人用途は条件付きであり、外部LLM経路の包括許可ではなかった。今回確定したのはXの追加条件・未決点である（L3）。

## 6. 止まる条件

以下は採用・再開の判断線（L3）。未確認を「禁止条文が見つかった」と言い換えない。

- **既にある明文との不整合：** 既存CLIのまま表示要件を満たせず、Xとの調整でも解消できないならRは足さない。
- **核心が否定された場合：** 非学習の外部推論への本文提供が禁止／許諾不可との明示を受けたら、そのLLM経路を足さない。訓練禁止をこれと取り違えない。
- **人物属性推定：** 個人の政治・健康等を推定する用途が不可欠なら採用しない。保存しないことで回避しない。
- **Wの分類：** 本件がAI reply botに該当し必要な書面承認を得られない、または自動返信の受信者条件を満たさないならWを足さない。R/Mまで機械的に禁止としない。
- **保存・配布：** 削除要求にgit履歴／外部コピーまで対応できない、または各利用者App方式が認められず代替も採れないなら該当段階を止める。
- **費用・取得範囲：** 予算切れで止まれない、欠けた枝を全部と表示する、指標のみのMが本文保存へ広がる場合は受け入れない。

## 7. masaru に決めてほしいこと

1. **推薦：Rの着手を、表示方法と外部LLM提供の確認まで保留。** $30というPost基準費用は成立するが、設計§10-6の前提は料金だけでは満たせない。本文非保存だけを根拠に進めない。
2. **推薦：Xへ一つの具体的なデータフローで照会する。** 非学習の外部LLM、提供先・保持設定、人が一件ずつ確認する二段承認、CLIの実際の表示例、本人指標の時系列・git履歴、各利用者App方式を添付する。単なる「AI利用できますか」では判断材料が不足する。
3. **推薦：照会で回答を分けてもらう。** (a)外部推論への提供根拠と500/50,000の不整合、(b)CLI／JSONの表示要件、(c)人の個別承認時のAI reply bot該当性、(d)削除後のID・hash・指標・過去集計・gitコピー、(e)各利用者App方式。許可される条件と適用条項を求める。
4. **推薦：Mは「公開指標のみ」を受け入れ境界にする。** 現collectの会話採集を黙って有効にせず、R・Mの非保存説明を一致させる。書面回答後に実装対象を裁定する。
5. **推薦：上限予算はPost読取$30と付随費用を分けて決める。** Console上限と取得前予算の両方を受け入れ条件とする。月$30を総額保証とは扱わない。

問い合わせ入口：[Developer Support / Policy Support](https://docs.x.com/support)、U II.B.3のdeveloper portal案内、[Developer Console](https://developer.x.com/)。Dが指す[Policy Support form](https://help.x.com/forms/platform)は今回取得時にHelp Centerトップへ転送され、**フォームとして利用可能かは確認できなかった**。ログイン後の窓口・回答保証は未確認。問い合わせは送信していない。

検証記録：統括が設計・前回検収・主要保存箇所とA/P全文を照合。コード読解担当（gpt-5.6-sol/high）が指定7コード＋設計全文を読み保存項目を独立確認、料金担当（同）が公式料金・endpointを収集、条文監査担当（gpt-6-astra/high）がA/P/Q/D/Uの解釈を反証確認。完成稿の独立照合も実施。
再現：対象revisionの上記関数・行を読み、表のURL／節と対比する。実API、アカウント作成、Consoleログイン、秘密設定、VM、本番、利用者データは未確認・未操作。実装変更・動的テスト・commit・pushなし。成果物は指定のexchange/outの本書1本のみ。
