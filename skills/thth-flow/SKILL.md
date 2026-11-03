---
name: thth-flow
description: 記事を軸に「調べる→はてブ→はてなブログ→本体の記事→告知とセルクマ→広告」を回すとき、キャンペーン（糸）の宣言を書くとき、原稿のリンクに印（UTM）を付けるとき、効いたかを測るときに呼ぶ。各段で叩く THTH の命令と、数の読み方・広告に進む条件をまとめた流れの型。
---

# THTH — 届ける流れの型（thth-flow）

THTH の出力が「次はこれ」と言う前に、まずこの型を読む。`thth scenario`・`thth scenario show`・`thth dashboard <project> campaign <id>` の出力の 1 行目はこの skill の 1 行（`--json` は `skill` の欄）。`thth scenario skill` が場所、`thth scenario skill --print` が全文を出す。命令ひとつずつの詳しい使い方は skill `thth`（`skills/thth/SKILL.md`）、広告の段の手順は skill `thth-ads` に任せる。ここに書くのは流れの形と、THTH が触る所だけ。

**根っこは効果測定。** あとから取り直せないのは 3 つだけ——投稿の URL の印（UTM）・原稿の札 `thread:`・広告の `url_tags`。これは出す前に必ず付ける。数の読みは後からでもできる。

## 1. 流れの型（段の名と順）

| 段 | 中身 | 宣言に書く行の例 |
|---|---|---|
| `collect` | 調べる・はてブで集める | `- collect: hatena_bookmark target=external need=3`（タグは既定で糸の id） |
| `diary` | はてなブログに仮説を書く | `- diary: hatenablog` |
| `article` | 本体のサイトの記事を出す・直す | `- article: site`（出来事 `article_published` で満たす） |
| `promote` | 告知（Threads・X・Facebook・Instagram） | `- promote: threads,x,facebook need=3` |
| `selfbm` | セルクマ（自分の記事を自分のはてな ID で） | `- selfbm: hatena_bookmark target=own need=1`（§4） |
| `boost` | 広告（有機の反応を見てから 1 本だけ） | `- boost: ads need=1`（§5） |
| `warm` | 返信を返す | `- warm: threads replies_within_hours=24` |

- 段は窓口が選ぶ。全部は要らない。上から順に満たす形で、THTH は読んで数えるだけ（原稿を書かない・段を進めない・選ばない）。
- 段の名は自由（`[a-z][a-z0-9_]` 32 字まで）。`collect`（リサーチ）・`diary`（仮説）・`article`（本記事）・`announce`（SNS 告知）・`promote`（告知）・`selfbm`（セルクマ）・`boost`（広告）・`warm`（エンゲージ）は画面に日本語の名が出る（`stages[].label`・表示は `promote（告知） 1/3`）。ほかの名は「n 段目」。
- 媒体として書けるのは 10 個: `hatena_bookmark`・`hatenablog`・`site`・`threads`・`x`・`bluesky`・`mastodon`・`facebook`・`instagram`・`ads`（5.7.0）。
- はてブの段は `target=own`（自分の記事だけ・セルクマ）か `target=external`（他人のページだけ）で分けて数える。書かなければ今までどおり両方を数え、`stages[].by_target`（`{"own": n, "external": n}`）に内訳が出る。`target=own` の段は、タグ（既定は糸の id）が付いたものに加えて、糸のはてなブログの配信 id を `--delivery` で付けた自分の記事のブックマークも数える。
- `ads` の段は広告の口座の記録（`thth ads boost`・`thth ads activate` の記録）を読むだけ。始めた（activate が通った）広告が `done`、PAUSED で作っただけの広告が `pending`。糸に結ぶのは、押し出した元の投稿の id が糸の原稿の `post_id` か、広告の印の `utm_campaign` が糸の原稿の名前のもの。`budget_minor` は始めた広告の通しの予算の和（最小単位の整数・分母は `done`）。**使った額は持たない**（`spend_minor: null`・`ads_spend_not_recorded`）——使った額とクリックは `thth ads insights` で見る。project に広告の口座が無ければ `done: null`・`ads_account_missing`。
- **台帳の更新は THTH の段ではない。** 知識の側（各 repo）の仕事。糸のファイルには出来事の 1 行だけ残す: `- 2026-11-03 researched <短い文>`・`hypothesis`・`article_published`・`closed`。`researched` は collect、`hypothesis` は diary の本数の代わりになる。
- 宣言の置き場と書式は skill `thth` の「キャンペーンとシリーズ（scenario）」の節。原稿の札 `thread: <id>` は承認の指紋に入らないので、あとから足しても再承認は要らない。

## 2. 印（UTM）の決まり

| 何 | 印 |
|---|---|
| SNS の告知（有機） | `?utm_source=<threads|x|facebook|instagram>&utm_medium=social&utm_campaign=<原稿のファイル名、拡張子なし>` |
| 広告 | 同じ `utm_campaign`、`utm_medium=paid`（`utm_source=facebook|instagram`）。付け方は skill `thth-ads` |
| セルクマ・はてなブログからの導線 | 印を付けない（付けると別のエントリーになる）。参照元（`b.hatena.ne.jp`・`<blog>.hatenablog.com`）で見る |

- **印は窓口が原稿に書く。THTH は本文を書き換えない**（承認の指紋が変わるため）。
- いま印が付いているのはほぼ Facebook の原稿だけ。**Threads・X の原稿にも次の原稿から付ける。** 承認済みの原稿は付け直さない。
- `utm_campaign` を原稿のファイル名にしておけば、GA4 の campaign → 原稿 → `thread:` → 糸、と新しい札なしで結べる。口座名（例 `utm_campaign=kopicha`）にしない——記事ごとに分けられなくなる。
- THTH のクリックの照合は `utm_*` を外して数えるので、印を付けても数え方は壊れない。

## 3. 各段で叩く THTH の命令

| 段 | 命令（実在するものだけ） |
|---|---|
| 全体 | `thth scenario <project> --json`（糸と型の一覧）・`thth scenario show <project> <id> --json`（段ごとの原稿と状態・`next`）・`thth scenario lint <project>`（宣言の誤り・宣言の無い札）・`thth dashboard <project> campaign <id> --json` |
| collect | `thth bookmark add <口座> --url <URL> --comment … --tags <糸の id>`（二段・1 段目は表示と digest だけ）・`thth bookmark list <口座>` |
| diary | 原稿を queue に置いて `thth approve` の二段。`thth deliveries <口座|project> [--article <記事の id>]` で配信の状態と配信 id |
| article | 本体の記事は各 repo の手順で出す。出たら糸のファイルに `- <日付> article_published …` の 1 行 |
| promote | 原稿に `thread: <id>`・`goal: click`・印つきの URL → `thth lint` → `thth approve` の二段 |
| selfbm | `thth bookmark add <口座> --delivery <配信 id> --comment … --tags …`（§4 の柵） |
| boost | skill `thth-ads`（`thth ads boost` は全部 PAUSED で作るだけ、お金が動くのは `thth ads activate` の二段＋hub の承認） |
| warm | `thth replies`・`thth unanswered <口座> --since 7d --json`・返事は承認の二段 |
| 測る | `thth after <口座>|--project <p> --json`・`thth analytics-report <口座> --per-post-clicks --since 30d --json`・`thth article reactions <project> --article <記事の id> --json`・`thth events <project> --since 7d --json`（`bookmark_received`・`scenario_stalled`）・`thth measured <口座> --json`（`file` 欄で原稿の名と結ぶ） |

### 測りの読み方

- **分母と窓を必ず添えて言う。** `null`・`cannot_say` は「取れない」で、0 ではない。0 と書かない。
- 投稿ごとのクリックが THTH で取れるのは Threads だけ（`--per-post-clicks`・同じリンク先を前後 72 時間で 1 本だけが使った投稿 `unique_url_72h`）。X・Facebook・Instagram の投稿ごとのクリックは THTH に無い。Meta の `clicks` はリンク以外も含む。**記事への訪問はサイト側（GA4）で見るのが本筋**（次の 2 行）。
- THTH は GA4 を読まない。hub（SEO レーン）が GA4 を読み、`hub.site_visits/1` の JSON を 1 サイト 1 ファイルで `thth scenario site-import <project> --file <json>` に渡す（hub の timer が日次で打つ）。THTH は `state/<project>/site_visits/` に控え（サイトと窓の終わりの日で 1 ファイル・二度入れても上書き）、campaign → 原稿の名前（原稿のパスの `/` を `-` にした形も試す）→ `thread:` で糸に結ぶ。
- 結んだ数は `thth scenario show <project> <id> --json` の `site_visits` と `thth dashboard <project> campaign <id> --json` の `outcomes.article_visits` に「本記事への訪問（GA4・hub 経由）」として出る（`sessions`・`engaged_sessions`・サイトごとの窓・`by_medium`（social と paid）・campaign ごとの行・`as_of`）。窓の違うサイトは足さない（`windows_differ`）。結ばれない campaign（`""`・`(organic)`・`(direct)`・原稿が無い・札が無い）は `untied_campaigns` に数だけ（名前は出さない）。控えが無い project は `cannot_say: site_visits_not_imported`、取れなかった窓は `site_visits_unavailable`、印の付いた原稿の訪問が無い糸は `no_tied_campaign`。どれも 0 ではない。新しい窓が取れなかった日は、前の取れた窓の数に `stale: true`・`newer_unavailable` が付く（「最新の数」と言わない）。同じ名前の原稿が複数あって一部にだけ `thread:` があると結ばない（`ambiguous`）。
- セルクマの効きは「第三者のブックマークが付いたか」: `thth events` の `bookmark_received`、`thth article reactions`。はてなブログの PV は取らない（公式の口が無い）。
- 効いた・効かなかったは施策としてプラザに置く。糸を閉じたら（糸のファイルに `- <日付> closed` の 1 行）、`thth observe` の次の一手に「広場に置く」とこの命令が出る: `thth plaza post <口座> --kind measure --from scenario <project> <糸の id> --scope "<媒体・企画の範囲>" --by <名前> [--body-file <解釈>]`。道具が観測として付けるのは、段ごとの本数（済み/要る）・媒体ごとの反応（`thth after` と同じ 1 本の計算・分母は糸のその媒体の投稿の本数・n が 5 未満は中央値を言わない）・本記事への訪問（`site_visits`・取り込んでいなければ `null` と `site_visits_not_imported`）・広告の段（始めた本数と通しの予算。使った額と link_click は `null` と理由・Meta は呼ばない）・窓（`started` から `closed` の日まで）。**「観測あり（observed）」の印が付くのは、24h の実測が取れた投稿・訪問の数・始めた広告のどれかがあるときだけ**（糸のファイルの出来事や原稿の `status: posted` だけでは付かない）。原稿の台帳が読めなかったら本数は「言えない」と出る（0 ではない）。open にすると、他の持ち主には糸の id・サイトの名前・自由な段の名は見えない（数と媒体と状態だけ）。**解釈は `--body-file` に書く**（観測の欄には入らない）。原稿の本文・他人の返信・username は付けない。project が持ち主の組に入っていないと別の project から読めない（observe が `thth admin plaza owner set …` の案内を添える・管理者が打つ）。別の project は `thth plaza digest <project|組>` の「糸の結果」で、使った媒体と**試していない媒体**つきで読む。糸でなく口座の期間の数なら `--from after <口座>`（または `--from analytics-report <口座>`）。

## 4. セルクマの柵

はてなの原文: 自分が運営するサイトを自分のアカウントでブックマークするのは問題にしていない（ただし宣伝目的・機械的な高頻度はスパムと判断されうる）。THTH の柵はこのまま使う:

- 自分の記事は `--delivery`（配信の台帳にある自分の記事）で付ける。主体ごとに 1 日 1 件。同じページは owner をまたいで 1 回だけ。
- コメントに運営者であることを書く（1 段目に「運営者であることが分かる文に」と出る）。
- 二段の承認。まとめ打ち（`thth bookmark batch`）や timer に混ぜず、人がその場で `bookmark add` を打つ。記事が出た直後に 1 件。
- 「何件で新着に載るか」を試さない（表示の仕組みの検証はガイドラインが禁じている）。URL に印を付けない。
- 宣言の段は `- selfbm: hatena_bookmark target=own need=1`。collect の段は `target=external` にしておくと、セルクマが調べた数に混ざらない（`target` を書かない段は両方を数える）。

## 5. 広告に進む条件とピクセル

- **有機の告知を出して 48〜72 時間の反応を見てから**（`thth after`・`--per-post-clicks`）、効いた 1 本だけを押し出す。THTH は選ばない（数を並べるだけ）。選ぶのは窓口と人。
- 手順・承認・止め方は skill `thth-ads`。宣言の段は `- boost: ads need=1`。作るときに `thth ads boost --utm-campaign <元の投稿の原稿のファイル名>` を付けると、印から糸に結べる（`--post` で THTH から出した投稿を押し出すなら、その投稿の id からも結べる）。

| 広告の目的 | ピクセル | 無いとどうなるか |
|---|---|---|
| 反応（`--objective engagement`） | 要らない | 何も欠けない |
| サイト誘導（`--objective traffic`・リンクのクリックで最適化） | 無くても出せる | 「頁が開いた」（`landing_page_view`）が取れない |
| サイト誘導で「頁が開いた」に最適化 | **要る** | 作れない・最適化が効かない |

- ピクセルをサイトに入れるのは窓口の仕事。Meta への外部送信が増えるのでプライバシーの頁も直す。**入れる前に masaru へ 1 行。**

## 6. 知識の側の手順（THTH の外）

台帳の更新・本体の記事の出し方・自分のサイトの GA4 の見方・プライバシーの文は、各メディアの repo の `docs/sns/README.md` などに書く（例: asmon の `docs/sns/README.md`）。そこからこの skill へリンクする。ここには写さない。

設計の正本: `docs/設計_効果測定と連動_2026-11-03.md`（段 0〜5）。
