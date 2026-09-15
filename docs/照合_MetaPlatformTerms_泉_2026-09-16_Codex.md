# 照合「Meta Platform Terms と泉」（Codex 執筆・2026-09-16）——開発セッションの検収つき

**法的助言ではない。条文と設計の突き合わせ表。最終判断は masaru・gotoq。** 本文は Codex が `~/thth-exchange/in/2026-09-15_依頼_照合_MetaPlatformTerms_泉_Codex.md` に応えて書いたもの（原本 `~/thth-exchange/out/` の同名・2026-09-16 05:20）。下の「検収」は開発セッション（Fable）が読んで確かめた範囲。

## 検収（開発セッション・2026-09-16 朝）

**判定: 裁定の材料として十分。結論は重い。**

**現物で確かめたこと（L1）**
- `share.py` の 3 record の項目数（observation 9・retraction 5・thread_shape 19）と各 field の由来——合う。`_assert_clean()` が `topic`・`audience` を自由文として生 ID 検査から外していること——合う（`FREE_TEXT_KEYS`）。`sync()` が読む観測元が旧 `state/topics.json` の checks だけで、keyword_search・mentions・profile の直接の流入経路が無いこと——合う。
- `build_privacy()` の該当行（cloud/server なし・callback は表示だけ・第三者に送らない・販売しない・こちらの app を通らない）——合う。
- 手順_AppReview §2.3 の共通前置き「nothing is stored on our servers」——合う。

**一次資料で確かめたこと（L2・2026-09-16 に開発セッションが同じページを取得）**
- §12(l) Platform Data の定義に「including data anonymized, aggregated, or derived from such data」——**原文どおり**。
- §12(s) Tech Provider「a Developer of an App whose primary purpose is to enable Users thereof to access and use Platform or Platform Data」・§12(e) Client——原文どおり。
- §3(a)(iv)「Selling, licensing, or purchasing Platform Data.」——原文どおり。
- §3(c)(i) TP として集めた Platform Data の共有は §5(b) の範囲だけ——原文どおり。
- §5(b)(ii)1「…and not for your own purposes or another Client's or entity's purposes」・2（Client ごとに分けて保管）・4（共有できる相手の列挙）——原文どおり。
- §3(d)(i)2 の削除条件の括弧書き例外（集計・匿名化して特定の User に結び付かないもの）——原文どおり。Codex の「TP の Client 要求の部分には同じ例外が反復されていない」は本文を読んだ限り正しい。
- 更新日 2026-02-03——合う。

**開発セッションの読み（L3・裁定の材料）**
1. **要は Tech Provider 該当性。** 共有アプリ（gotoq の Meta アプリを利用者全員が使う）は「利用者が Platform を使えるようにするアプリ」そのもので、TP の定義に当たる読みが自然。TP なら §5(b)(ii)1 が「他の Client のために使わない」と明文で言うので、**利用者横断の泉（API 由来の数）は無料でも成り立たない**。Codex と同意見。
2. **「薄い門」と「泉」は Meta の規約の下では両立しにくい。** 門（共有アプリ）は導入の敷居を消すが、gotoq を TP にして泉を閉じる。逆に今の持ち込み型（own-app）では利用者自身が Developer なので、泉への提供は §3(c)(ii)「TP として集めたのでない Platform Data」の共有条件（本人の明示の指示・受領者の義務など）で評価する余地が残る——Codex §6 の 3 行目「条文からは読めない」。**ここが唯一開いている戸**で、次に読むべき条項は §3(c)(ii) の列挙条件そのもの。
3. **段階 2（溢れたら売る）は、どの形でも §3(a)(iv) に当たる読み。** API 由来の集計は §12(l) で Platform Data のまま。ハッシュ・集計・閾値は逃げ道にならない（§3）。
4. **人の観測（画面を見た記録）だけの泉**は条文から読めない。API の表示を写した観測は「間接取得」に当たりうる。THTH の観測は `--search` の画面を見て人が書く形なので、「API を経ない」とは言い切れない。
5. App Review の説明文（「nothing is stored on our servers」）とプライバシーポリシー（§5 の 8 行）は、共有アプリ＋自動提供にすると嘘になる。Codex の列挙は現物と一致。

**次に決めること**は Codex §7 のとおり。開発セッションの推薦: (a) Threads の API 由来の横断の泉は**現案のまま進めない**、(b) 共有アプリの可否と泉の可否を**分けて**裁定する、(c) Meta の開発者サポートへ 2 段階の流れを添えて照会するかは masaru の判断（回答の保証はない）、(d) 泉の芯を「API 非由来の情報」か「利用者ごとに閉じた分析」に組み替えられるかは、事業判断（役に立つ答えが残るか）と一緒に別途。

---

# Meta Platform Terms と「泉」の逐条照合

取得・照合日：2026-09-16。対象 revision：`490fcbeb8761ee00a7210f1e3f2fd75a3f7b5a4c`（main）。法的助言ではなく、読めた条文と指定設計の対応表。最終判断は masaru・gotoq 側に残す。
L1＝この revision のコード・文書の現物確認、L2＝今回読んだ一次資料、L3＝設計への当てはめ・提案。**条文が L2 でも、THTH の該当性の判断は L3**。別件のセキュリティ監査は根拠に使用していない。
出力はユーザーの「結果は /out」指定に従い、この repo の out に置く。アプリ・設定・運用データの変更、実 API 呼出、問い合わせ送信、commit・push はしていない。

## 0. 一枚で言うと

|対象|判定|理由と範囲|
|---|---|---|
|段階1：投稿・検索の観測を自動収集し、提供者だけに横断集計を返す無料バーター|**不許容（L3）**|共有アプリを Tech Provider として読むと、各 client のために得た API データを他 client のために使う流れが §5(b)(ii)1・4 と整合しない。無料でもこの制約は残る。|
|段階2：同じ泉の集計回答を、非提供者・非利用者にも販売|**不許容（L3）**|上記に加え、集計・派生も Platform Data に含む §12(l) と、その販売・ライセンスを禁ずる §3(a)(iv) に抵触する読みになる。|
|API から独立した、人の独自観測だけで作る別の泉|**条文からは読めない（L3）**|人が入力したという事実だけでは非該当を証明できない。§12(l) はアプリ経由・間接取得も含む。元情報・取得経路・アプリとの関係を特定する必要がある。|

上2行は、**依頼された共有アプリ＋API由来データの横断利用に、確認済みの個別例外がない**前提の判定。Meta が THTH を Tech Provider と個別認定した事実は未確認。§12(s) の主目的の定義と、利用者の Threads 利用を代理する機能からの当てはめである。
「バーター＝販売」とは断定しない。段階1の否定根拠は client 間の目的外利用・共有であり、金銭の有無とは別。全ての有料 SNS ツールを禁止する結論でもない。
設計の最新裁定を採用した：`via: thth.me` は自動提供・off 不可、`own-app` は opt-in、段階2は非提供者にも販売。旧案の貢献検収・資格更新や「永久に非提供者へ売らない」は前提にしていない。
**現実装には共有サーバ送信・横断回答・販売はない（L1）。** 本稿は将来のデータ流を評価しており、現行運用で当該行為が起きたという報告ではない。

## 1. データの流れ1枚

L1：[share.py](/Volumes/NexDev/Developer/thth/thth/share.py:309) の3 record が正本。observation 9、retraction 5、thread_shape 19 のトップレベル項目、さらに growth 子3・views_at 子2を下表に全列挙した。
現在：人の入力／API取得 → 利用者のローカル台帳 → 選別・数値化 → 月別 outbox。将来：outbox 相当 → gotoq の泉 → 提供者への回答 → 段階2の購入者への回答。後半は未実装（L3）。
経路 **C**＝Threads `GET /v1.0/{post_id}/conversation`（返信 id・username・timestamp・親参照等）→ collect の返信台帳 → threadshape。**I**＝`GET /v1.0/{post_id}/insights` → collect → measured → threadshape。**H**＝旧 `state/topics.json` の人の観測。**M**＝原稿・ローカル設定／時計。
根拠：[API口](/Volumes/NexDev/Developer/thth/thth/adapters/threads.py:610)、[返信保存](/Volumes/NexDev/Developer/thth/thth/collect.py:400)、[指標保存](/Volumes/NexDev/Developer/thth/thth/collect.py:537)、[形状加工](/Volumes/NexDev/Developer/thth/thth/threadshape.py:403)、[人の記録](/Volumes/NexDev/Developer/thth/thth/topics.py:133)。
全行の **V**＝現在はローカルのみ。想定の泉では gotoq が当該 field を受け取り、他利用者は集計回答、買い手は段階2のみ集計回答を受け取る。個別 field・行の直接閲覧権限、最低集計人数、回答からの再識別対策は未定義。全行の **R**＝現在の outbox に TTL・自動消去なし、手動削除まで残る。未来サーバ・回答 cache・購入者側の保持期限は未定義。「90日で stale」は物理削除を意味しない。

|record.field|由来と加工（L1）|可視性|保持|
|---|---|---|---|
|observation.schema|固定 `thth.share.observation.v1`|V|R|
|observation.row_id|H全行の無塩内容hash。直接 enqueue で未指定なら乱数ID|V|R|
|observation.observer|インストールごとの永続乱数16hex。人名hashではない|V|R|
|observation.topic|Hの語。空白除去・200字まで|V|R|
|observation.audience|Hの「誰がいたか」の自由文。空白除去・200字まで|V|R|
|observation.kind|Hの分類。40字まで|V|R|
|observation.obs_status|Hの取得状態。40字まで|V|R|
|observation.observed_at|H記録時刻 checked_at。40字まで。API取得時刻の保証なし|V|R|
|observation.enqueued_at|Mの積込時刻。時刻帯に粗くしていない|V|R|
|retraction.schema|固定 `thth.share.retraction.v1`|V|R|
|retraction.row_id|`ret:`＋対象観測ID|V|R|
|retraction.observer|同じインストール乱数|V|R|
|retraction.retracts|打ち消す観測ID。通常H元行hash|V|R|
|retraction.enqueued_at|Mの打消し積込時刻|V|R|
|thread_shape.schema|固定 `thth.share.thread_shape.v1`|V|R|
|thread_shape.row_id|`shape:`＋post_hash|V|R|
|thread_shape.observer|同じインストール乱数|V|R|
|thread_shape.post_hash|API投稿IDにローカル永続塩を付けSHA256。塩はoutboxに出ない|V|R|
|thread_shape.medium|Mのaccount設定の媒体名。40字まで|V|R|
|thread_shape.topic|Mの原稿front-matter由来。200字まで|V|R|
|thread_shape.kind|Hの topic/account 対応分類。40字まで|V|R|
|thread_shape.hour_band|Mの posted_at を朝・昼・夕・深夜へ変換|V|R|
|thread_shape.branches|Cの親参照treeでdepth=1のnode数|V|R|
|thread_shape.depth|Cのtreeで判明した最大depth。空なら0|V|R|
|thread_shape.replies_total|Cの取得できた返信行数。insightsのreplies値ではない|V|R|
|thread_shape.author_replies|Cのusernameと全登録accountを照合した「身内」の返信数。単一投稿者限定ではない|V|R|
|thread_shape.other_replies|Cのown=False返信数。不明は含めない|V|R|
|thread_shape.participants_count|Cのother返信者usernameを正規化した異なり数|V|R|
|thread_shape.participants_denominator|Cのusernameが読めたother返信行数。同一人の複数返信も数える|V|R|
|thread_shape.first_reply_min|Cの最初のother返信時刻とMの投稿時刻の差分分。不能ならnull|V|R|
|thread_shape.growth|Cの返信を経過時間markごとにまとめた辞書|V|R|
|thread_shape.growth.&lt;mark&gt;.replies|Cの当該時間までの全返信累計。欠測ならnull|V|R|
|thread_shape.growth.&lt;mark&gt;.others|Cの同じ時間までのother返信累計|V|R|
|thread_shape.growth.&lt;mark&gt;.covered|ローカル取得履歴が当該経過時間に到達したか。Trueだけ真|V|R|
|thread_shape.views_at|Iのmark別辞書|V|R|
|thread_shape.views_at.&lt;mark&gt;.views|Iのmetrics.viewsの最初の該当mark値|V|R|
|thread_shape.views_at.&lt;mark&gt;.age_hours|Mの実取得時の投稿経過時間。厳密なmark時点ではない|V|R|
|thread_shape.enqueued_at|Mの積込時刻|V|R|
|観測（人が画面を見た記録）のPlatform Data該当性|**独立評価：条文からは読めない（L3）。** API表示を転記・要約した観測なら間接・派生取得として該当する読み。独立発想・独自調査も同じとは断定できず、Hには元取得経路の証跡がない|該当性未決でもV|R|

通常のmarkは1・6・24・72・168時間だが、share は任意のmark文字列を鍵にできる。数値は int/float のみ、bool/文字列はnull。username欠測・own不明・coverage理由などの上流情報は出力されず、数字だけで取得制約を復元できない（L1）。
`root_hash` という field はない。実名は `post_hash`。観測の row_id は、除外した account/by/verdict/note も含む元行をhashする。[topics.py](/Volumes/NexDev/Developer/thth/thth/topics.py:244) と [topic_models.py](/Volumes/NexDev/Developer/thth/thth/topic_models.py:39)。平文漏出の確認ではなく、元行照合可能性が残るという意味。
`sync` が読む観測元は旧 topics の checks。新 topic_store や keyword_search・mentions・profile取得を直接積む経路ではない。[share.sync](/Volumes/NexDev/Developer/thth/thth/share.py:619)。検索を自動注入する将来設計では、検索固有の項目・保持を追加定義する必要がある。
`_assert_clean` は禁止key・一部の生ID表現を検査するが、topic/audienceは自由文例外。[検査](/Volumes/NexDev/Developer/thth/thth/share.py:256)。合成データの純粋validator確認では、topicの数字ID、audienceの本文相当＋handle＋URL、AT URIの3件が通過した。実データ混入の確認ではない。既存 [安全性テスト](/Volumes/NexDev/Developer/thth/tests/test_share_safety.py:57) は禁止fieldへの混入検査であり、意味的匿名化を証明しない（L1）。
offは新規追記停止、retractionは打消し行の追記で原行削除ではない。1投稿1行のため後から増えたmarkも現状では追記しない。解除後の泉からの除去・再集計・配布済み回答の扱いは未実装（L1）。

## 2. 逐条表

以下の資料は全て2026-09-16取得。T全文（§1–12・追加条件を含む）、D全文、E・R本文、I・K英語本文、U本文、S日本語全文を実読。Meta本文は通常fetchの429等を避け、ブラウザの表示本文でも確認した。

|略号・一次資料URL|公開／更新表示・照合範囲|
|---|---|
|[T：Platform Terms](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US)|更新2026-02-03。定義、禁止、Tech Provider、削除、審査、全条項|
|[D：Developer Policies](https://developers.facebook.com/devpolicy/)|更新2026-02-03。全節。Threads固有節なし|
|[E：§3例外](https://developers.facebook.com/terms/3e/)|更新2025-02-03。Messenger会話・Lead Data・Shops User Data等の指定。Threadsの指定なし|
|[R：Restricted Platform Data除外](https://developers.facebook.com/terms/12n/)|更新2020-07-01。Threadsの明示除外なし。全ThreadsデータがRestrictedとの意味ではない|
|[I：Threads Insights](https://developers.facebook.com/documentation/threads/insights.md)|更新日表示なし。Overview・Media/User Insights・limitations。本人insightsの取得機能|
|[K：Keyword Search](https://developers.facebook.com/documentation/threads/keyword-search.md)|HTML版更新2026-01-21。Permissions・Limitations・Interacting with Public Threads|
|[U：Data Use Checkup](https://developers.facebook.com/documentation/resp-plat-initiatives/individual-processes/data-use-checkup.md)|更新日表示なし。Overview以下。許可用途・遵守・データ取扱いの年次確認|
|[S：Threads Supplemental Terms](https://help.instagram.com/769983657850450)|更新2025-05-28。全11節。一般Threadsアプリ／ウェブ用で、API専用規約ではない|
|[A：Threads APIトップ](https://developers.facebook.com/documentation/threads)|更新2026-07-02。本文・目次・footerの規約リンクを確認|

引用欄は原文のごく短い断片だけを引用符で区別し、その後の当てはめは要約・推論とした。Tリンク各行は同じ全文URLを参照。判定列は「段階1／段階2」、L欄は「条文／当てはめ」。定義・保護等の「条件付き」は当該条項単独の評価であり、全体許容を意味しない。

|条項・URL|原文の短い引用|段階1への当てはめ|段階2への当てはめ|判定 1／2|L|
|---|---|---|---|---|---|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §12(l) 定義|“aggregated”|APIの集計・派生・匿名化も対象。数だけで適用除外にならない|販売回答もAPIからの派生なら対象|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §12(m) 処理|“automated”|手動処理も定義に含まれ、転記だけで除外されない|手作業で回答作成しても同じ|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §12(s),(e) TP・client|“primary purpose”|他利用者がPlatformを使うための共有アプリはTPに該当する読み|非利用者への販売で、取得時のTP性は消えない|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §3(a)(iv) 販売等|“Selling”|情報バーター自体が販売／購入かは断定できない|API由来集計回答の販売・ライセンスに当たる読み|条文からは読めない／不許容|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §3(a)(viii) 用途|“permitted purposes”|取得権限の存在から横断利用許可は導けない|有料の別用途も許可根拠が必要|条文からは読めない／条文からは読めない|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §3(b)(i),12(p) Restricted|“Restricted”|本人の特定サービス体験を意味ある形で改善する必要性。これはRestrictedへの限定で、全データに一律の本人限定条項ではない|非利用者への販売をこの本人向け取得必要性で説明できない|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §3(c)(i),(ii) 共有|“solely”|TP取得分は§5(b)の共有だけ。非TPの同意・受領者契約経路をそのまま使えない|同意取得だけで販売禁止もTP共有制限も解除されない|不許容／不許容|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §5(b)(ii)1 目的|“Client’s Purpose”|取得元clientの指示・目的のためだけ。他clientやgotoq自身の共通資産化に当たる部分が問題|第三者購入者のための利用は取得元clientの目的を超える読み|不許容／不許容|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §5(b)(ii)2 分離|“separately”|client別に保管する義務。横断プール設計と整合が要る|同じ。DBを分けても他client目的の利用が許されるわけではない|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §5(b)(ii)4 受領者|“applicable Client”|取得元client・法令・必要なサービス提供者等の列挙。他の泉利用者は通常含まれない|一般の買い手も通常含まれない|不許容／不許容|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §6(a)(iv) 秘密|“access tokens”|token/ID/secretを保護。運営補助のサービス提供者例外は泉の配布許可ではない|同じ。outboxの除外だけで共有門全体の適合を証明しない|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §3(d)(i)2 削除|“Delete”|許容目的消失・サービス停止・本人／client要求等の削除条件に対応する必要。下記参照|販売済回答やcacheを含め残存処理を定義する必要|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §3(a)(vii) 用途変更|“re-submit”|重要な処理変更は事前の再審査・承認対象。共有化を旧説明のまま始められない|販売という追加変更も申告対象として扱う|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §4(c),(d) 公表|“clearly described”|処理目的等をpolicyで明示。同意やpolicyで規約を上書きできない|販売を明示しても販売禁止の例外にはならない|条件付き／条件付き|L2／L3|
|[T](https://developers.facebook.com/terms/dfc_platform_terms/?locale=en_US) §7(a),(d) 審査・証明|“certifications”|審査・DUCで実用途を正確に申告。承認されても規約遵守義務は残る|同じ。一般的な審査合格は横断販売の免除ではない|条件付き／条件付き|L2／L3|
|[D](https://developers.facebook.com/devpolicy/) §1.6・1.7|「許可された用途」|文書の用途に従い、本人代理の公開には事前同意。泉への自動提供と投稿承認は別|同じ|条件付き／条件付き|L2／L3|
|[D](https://developers.facebook.com/devpolicy/) §2.7|「購入、販売、交換」|反応・フォロワー等の交換禁止。観測情報のバーター全般とは同一視しない|情報販売全般の判断はT§3へ戻る|条文からは読めない／条文からは読めない|L2／L3|
|[U](https://developers.facebook.com/documentation/resp-plat-initiatives/individual-processes/data-use-checkup.md) Overview|“annual”|許可用途・データ取扱の年次確認は共有目的を隠したまま満たせない|販売を含む実用途と回答を一致させる|条件付き／条件付き|L2／L3|

削除の主要条項は依頼文の§7ではなく **T§3(d)**。§7は審査・監査・執行も扱う。§3(d)(i)2(d) の一般Userの削除要求／退会には、特定User・browser・deviceへ結び付かない集計等の括弧書き例外がある。ただし続く **Tech Provider のUser／Client要求・Client退会部分には同じ例外が反復されていない**。TPの泉へ一律に転用しない（L2→L3）。
OAuth解除だけで「全ての匿名集計を直ちに削除」とする専用文言は、確認したT・D・I・Kでは読めなかった。一方、解除で許容目的が失われた場合の§3(d)(i)2(a)、削除要求・退会は別途評価する。現行 observer・hash は関連付けを残すため、無関連化の条件達成も未証明（L1/L3）。
I・Kの読めた全文には、横断集計・他利用者共有の例外許可、アプリ側の一律保持日数は見つからない。公開検索できることは再配布自由を意味しない。D§10.7の広告データ用の集計匿名利用規定、Eの別製品の例外、S§5のMeta側削除期間・fediverse残存説明はThreads APIの泉へ転用しない（L2/L3）。
API専用Termsは、A本文・目次・footer、D全文と公式ドメインでのThreads API Terms／Supplemental Terms検索の範囲では**見つからなかった**。不存在の証明ではない。Sは一般利用規約でAPI専用ではない。Dashboard限定条件・個別契約は未確認で、そこから許容の結論は出していない。

## 3. ハッシュと集計は逃げ道になるか

|処理・粒度|判定と根拠|
|---|---|
|API post_id → post_hash|**Platform Dataのままという読み（L2 T§12(l)／L3適用）**。原IDの派生であり、復元しにくさは定義の適用除外ではない。|
|観測者の仮名observer|乱数そのものはローカル生成（L1）。それだけをAPI由来と断定しない。ただし紐づくAPI由来記録を非該当にせず、同一インストールの追跡も残る（L3）。|
|語×観測者×時刻|元がAPI観測なら要約・集計後も対象という読み。独立した人の観測なら§1の未決評価。語の一般性だけでは出自は消えない（L3）。|
|返信形状・人数・views・集計回答|APIに由来する数値をさらに集約しても対象。集計人数を増やすことはプライバシー対策になり得るが、T§3・§5の許可を生成しない（L2／L3）。|
|観測row_id|元行hashであることと、本文を共有しないことは両立する。しかし匿名化済みの証明にはならず、元行がPDなら派生評価が必要（L1／L3）。|

T§12(l)は匿名化・集計・派生を定義内に置いている。「個人を識別できない」と「Platform Dataでない」は別である。Restricted該当性が下がるとしても、Platform Data一般の禁止まで消えるとは読めない。

## 4. App Review の用途説明との整合

L1：対象は [提出説明案 §2.3](/Volumes/NexDev/Developer/thth/docs/手順_AppReview_2026-09-14.md:173)。Dashboardに実際に送った文面・審査結果は未確認。以下はこの文書と将来設計の照合。

|箇所|共有アプリ＋泉との関係／必要な対応（L3）|
|---|---|
|共通前置き（184行）「nothing is stored on our servers」|中央に観測を置けば直接矛盾。自社用ローカルCLIから共有アプリ・中央処理へ変わるため、共通説明の変更対象。|
|keyword search（188行）|利用者が投稿テーマを選ぶ目的に限定した説明。横断的な観測蓄積・他者回答・販売は説明されていない。**never saved の主語は Post bodies**で、集計値全般ではない。本文を保存しない実装なら、この一文だけを集計保存との直接矛盾とは扱わない。|
|mentions（189行）|本人のGitで本人が返信判断する用途。mentions由来データも泉へ流すなら、保存先・受領者・二次用途の説明が不足する。現shareには直接流入経路なし。|
|profile discovery（190行）|利用者自身のリポジトリにとどまるという約束。profile由来情報を中央へ送れば矛盾。現shareに専用profile fieldはないため、既に違反とは判定しない。|
|location tagging（191行）|本人が明示した場所だけを承認後に付ける説明。泉の導入だけでこの動作説明が偽になるわけではない。共通前置きの修正は必要。|

T§3(a)(vii) の重要変更に当たる読みなので、共有化・自動提供・横断回答・段階2販売を区別して事前申告・再審査を扱う。説明を書き換えたり一般審査を通過したりしても、T§7(a)により禁止条項は免除されない。禁止と読む流れを説明更新だけで実施可能にはしない。

## 5. プライバシーポリシーの書き換え点

L1：[build_privacy()](/Volumes/NexDev/Developer/thth/tools/build_site.py:275) の英日生成元を確認。公開サイトへの現在の反映状態は未検証。下表は**共有アプリ＋自動提供へ変更した場合**に事実でなくなる、または追加実装次第で事実でなくなる約束。書き換え文案は作らない。

|英語／日本語のコード行|現行の約束（要旨）|矛盾する流れ（L3）|
|---|---|---|
|303–306／375–378|THTHのcloud・account・内容受領保管serverはない|泉の中央受領保管はserver不存在と矛盾。accountは共有門で登録を持つ場合にも矛盾。|
|312–317／384–388|callbackはコードを表示するだけで、保存・記録・送信しない|共有門がコードを受けてtoken交換する設計なら「表示だけ」と矛盾。ログ無効等は別途実装依存。|
|318–319／389–390|Meta通知には応答するだけで何も保存しない|共有側の認可失効・削除処理とその状態保存を実装するなら、この説明では実態を表せない。|
|337–341／406–409|全て手元のファイル。作者にも第三者にも送らず、通信先は設定SNSだけ|gotoqの泉への自動送信・中央保管が直接矛盾。|
|344／412|販売・共有・譲渡しない。こちら側の保管serverはない|段階1の中央共有と段階2の回答販売。生データでなく集計というだけでは約束を維持できない。|
|347／415|手元のファイルを残す間だけ手元に保持、いつでも削除できる|中央コピー・回答cacheが加わるため、保存と削除の説明として不完全。ローカル削除だけで全コピーは消えない。|
|351–355／419–422|ローカルtoken削除でアクセス解除。serverに削除対象なし|共有serverがtokenを持つならローカルtoken削除だけでは失効しない。中央の泉があれば削除対象なしとも言えない。|
|359–361／426–428|他の利用者は自分のMeta appを登録し、こちらのappを通らない|共有app利用という設計そのものと直接矛盾。|

静的ページにCookie等がないという約束は、泉導入だけで直ちに偽にはならない。また観測の自動送信とThreads上で本人の指示なく投稿することは別なので、投稿承認の約束まで一括して虚偽にしない。段階2でもCLI自体が無料なら、無料CLIという表現単独を虚偽とはしない。

## 6. NO のときの逃げ道

|案|判定・条件・限界|
|---|---|
|利用者ごとに閉じた分析・回答|**条件付き（L3、根拠L2 T§3・§5）**。本人clientの許容目的、分離保管、適切な受領者、申告・削除等を満たす。横断の泉にはならないが、投稿支援サービス自体を否定する理由ではない。|
|API由来の数を入れず、人の独自観測のみ|**条文からは読めない（L3）**。API結果の転記・要約を除き、観測の出自と権利を記録して再照合。手入力ボタンを挟むだけでは足りない。|
|自分のアプリを各自が持ち寄り、同意して集約|**条文からは読めない（L3）**。非TPの共有経路は別評価になり得るが、取得時の役割と全禁止条項の評価が必要。own-app・同意だけで横断利用や販売を許容とはしない。|
|集計閾値・匿名化・別法人化を追加|**不許容の理由を解消しない（L3）**。プライバシー改善や組織変更だけで、API由来・目的・販売禁止は変わらない。|
|Metaへ具体的な2段階の流れを提示し照会|**条件付きの検討経路（L3）**。適用条項・TP該当性・例外の根拠を確認。一般的なApp Review承認を規約免除とは扱わない。回答が得られる保証はない。|
|独自に権利を確保したAPI非由来の情報源へ変更|**条文からは読めない（L3）**。泉の考え方を残す候補。別媒体・別情報源それぞれの規約・権利確認が必要で、この照合だけで許容とは判定しない。|

## 7. masaru に決めてほしいこと

1. **推薦：ThreadsのAPI由来データを横断利用する泉は、現案のまま実装へ進めない。** 無料であれば解決する制約ではないため。共有アプリ自体の可能性と、横断の泉の可否を分けて判断する（L3）。
2. **推薦：無料バーターと有料回答の2案を別々にMetaへ照会する。** [開発者サポート](https://developers.facebook.com/support/) の「アプリの遵守」入口を2026-09-16に実読し、App Review・DUC・データ保護審査の案内を確認した（L2）。既存ログイン状態での確認で、未ログイン時の利用可否・政策照会への回答保証は未確認。送信はしていない。
3. **照会で欲しい回答：** この共有appのTP該当性、API由来集計を別clientへ返す根拠、非clientへ販売できる例外の有無、人の独自観測／API画面転記の区別、解除・削除要求時の既存集計・cache・販売済回答の扱い。§1のfield表と2本のデータ流を添える。担当者の一般論でなく、適用条項とこの流れへの明示回答を求める（L3）。
4. **推薦：人の独自観測だけで商品価値が成立するかは別途検証する。** API由来指標を外すと、参加者数・伸び・views等の比較材料は失われる。「役に立つ答え」が残るかという事業判断と、非PDと扱えるかという規約判断の両方が要る（L3）。
5. **推薦：段階2の構想自体は保持し、仕入れる情報の権利から成立条件を組み直す。** 十分に溜まれば非提供者へ売るという裁定は理解している。ただし量が増えても販売権が生じるわけではない（L3）。

検証範囲：統括によるコード・設計・英語T全文・D本文の照合、独立担当による全field由来追跡と一次規約の照合、完成稿の再査読（T/E/R、D/U、field表・privacyを分担）。再現は対象revisionのshareの3 record辞書・sync入力・各API口をたどり、上記URLの指定節を確認する。実利用者データ・秘密設定・VM・本番API・実際の審査提出内容・個別契約は未検証。実装変更を伴うテストやデプロイは行っていない。
