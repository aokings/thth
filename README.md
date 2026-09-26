# THTH（ThreadsThrower・承認済み投稿の投げ手）

設計: [docs/設計_THTH_2026-09-08.md](docs/設計_THTH_2026-09-08.md)（§8 の裁定済み。現行の設計書）。

承認済み（`status: approved`）の投稿ファイルを読み、静かな時間帯と最短間隔を守って 1 件だけ投げ、
post_id を書き戻して push し、投稿済みの返信を拾って追記し、走った記録と鮮度を出し、
走らなかったことを外部に知らせるまでを持つ。意味（何を書くか・承認・返信の解釈・採集箱への昇格）は持たない。
watchtower（`~/Developer/watchtower`）の隣に同じ流儀で並べる。watchtower は POST しないので投稿は混ぜない。

## 版

このソースの版は **2.11.0**。現在の設計は [自分の泉](docs/設計_自分の泉_2026-09-16.md)、導入は [承認を押すだけ](docs/導入_承認を押すだけ.md)、文書の索引は [docs/README.md](docs/README.md)。

版の正本は `thth/VERSION`（`server.json` はテストで一致を強制）。開発中の `main` には未配布の変更も含まれます。インストール済みの版は `thth --version`、VM の版と revision は `thth board` で確認してください。

**`main` への push は保存だけ。`release` を進める操作が配布**（VM は `release` だけを追う。設計 §3.2.1）。

## 使う人へ

各プロジェクトのセッションが読むのは [docs/使い方_プロジェクトのセッション向け_2026-09-09.md](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) **だけ**。設計書は作った側の記録なので読まなくてよい。

## 2.12 のローカル実装候補

管理者用の [サーバ書込](docs/運用_サーバ書込_2.12.md)、[停止・退出](docs/運用_サーバ退出_2.12.md)、[X 読取予算](docs/運用_X読取予算_2.12.md) を追加しています。配布・本番検収とは別です。X の認可・refresh の本人読取も予算対象で、既定 USD 0 のままでは token 交換前に停止します。X 投稿・採集 adapter はまだありません。

## 認可（2.11.0）

招待した利用者は、masaru がサーバ側で始めた認可 URL を開いて承認します。利用者に VM や Meta アプリの準備を求めません。Threads/Mastodon/X は `thth auth <account> --by masaru`、Bluesky は秘密管理ツールから App Password を `thth token set <account> --stdin --by masaru` へ渡します。X は認可だけ対応し、投稿・採集は未対応です。「接続」ページはこの版にはありません。

運営者の client 設定、貼付の逃げ道、scope と doctor 観測、X の更新間隔の限界は [統一ガイド](docs/導入_承認を押すだけ.md) にまとめています。認可の成功で承認・digest・production の条件は変わりません。

## 実装と運用の状態（2026-09-18）

2.7.0 の実装 revision `f331e8c` の全件テストは **2743 件**（`python -m pytest tests/ -q -n auto -p no:cacheprovider`・1 skip・rc=0）。開発中の変更の検証は対象 revision の CI を参照してください。

**媒体**: Threads（稼働）・Bluesky・Mastodon（同席用の台帳あり・未稼働）。

**定期実行中**: Threads の 3 アカウント（nigamilab・asmon 関東・kopicha）。masaru の Threads は同席用です。
timer（systemd・`thth systemd` で生成）は10分ごとに実行し、投稿間隔と静かな時間帯を尊重します。返信の採集（`thth replies`）と数の採集
（`thth measured`）は稼働、トークン更新は `thth maintain` が毎日。

**入口は 4 つ**: 厚い CLI（`bin/thth`・`python -m thth`）、薄い MCP（`mcp/server.py`・読み取りと
同席の投稿・`before_you_post`）、timer、そして `pip install`（`thth`・`thth-mcp` の entry point・
**依存 0**。PyPI に公開済み・MCP registry に `io.github.aokings/thth` として登録済み）。

**トピックの棚**（`thth topics`）: 観測者ごとに並ぶ・打ち消し `retract-note`・`history`。

### v2.8.0: Bluesky／Mastodon のタグ

台帳の `hashtags: true` では本文のタグと `topic` を使えます。`topic: 茶` は公開本文の末尾に `#茶` を付け、同じタグが既にあれば重ねません。`hashtags: false` ではタグを追加せず、`topic` が効かないことを警告します。`max_hashtags` は公開本文のタグの上限（省略時 3、0 以上の整数）です。承認の指紋はタグを含む公開本文を使うため、以前の本文で承認した原稿は `approval_stale` になり、再確認・再承認が必要です。

`where`／`topics --search` の JSON は `by_tag` にタグ検索・観測の結果を分けます。ローカルの期間比較は `thth analytics-report <account> --compare-previous --by tag --json`。API で確認できたタグだけを使い、未観測はタグなしと混ぜません。媒体をまたぐ合算はしません。[利用手順](docs/使い方_プロジェクトのセッション向け_2026-09-09.md)・[2.8.0 リリースノート](docs/リリースノート_2.8.0_2026-09-19.md)。

### v2.7.0 の変更

- 指標採集に720時間（30日）を追加し、対象窓を刻みから38日と計算。正常時は指標6回、返信は従来の5回です。返信の採集窓は独立して維持します。
- 新しい観測に、採取時刻以前48時間以内で最も新しい日次フォロワー数を文脈として添えます。`followers_count_at` と `staleness_hours` を含み、追加APIや過去行の後補完はありません。該当しない場合は `context_reason` に理由を残します。
- Threads・Bluesky・Mastodonの刻みを揃え、媒体にないviewsはnullのまま維持。[合成検証と限界](docs/検証_v31_C実装_2026-09-18.md)。実SNS APIでの30日後採集は未確認です。
- C3・C4は実装保留。[独立規約照合報告](docs/照合_MetaPlatformTerms_枝と語の観測_2026-09-18_Codex.md)の未確認事項とApp Reviewの結果を踏まえて別途判断します。

### v2.6.0 の変更

- 既存台帳から刻み別の値、中央値の IQR・min・max、枝別の帰結、採集成否と鮮度、層別比較、採集時刻を上限とした枝の形を追加。既存キーは維持し `schema_version=1` のままです。
- `cannot_say_details` にコードと既存の説明文を併記。施策レポートには未成熟投稿が24時間に達する予定を添え、採集保証とは分けます。
- `handoff-report --since-last-read` で保存済みsnapshotとの差分。cursor保存は CLI の `--mark-read --by 名前` を明示したときだけ。MCP・HTTPは読むだけです。
- API呼出を増やさない分析の改良です。VM5accountの読取検証と限界は [検証記録](docs/検証_v31_A実装_2026-09-18.md) に記載。Bluesky/Mastodon の非空実台帳と実採用施策の検証は未確認です。

### v2.5.0 の変更

- 読み取り専用のレポート 3 本: `analytics-report`（活動のスナップショット・`--compare-previous` で隣接期間の比較）・`handoff-report`（ローカル運用記録の引継ぎ）・`study-report`（施策の宣言と本人の観測の結合）。MCP に `analytics_report`・`operations_handoff`・`study_report`。数値は期間・母数・欠測・根拠を連れて歩き、因果や推奨は出しません。
- `serve-reports`: 専用環境向けの非公開レポート HTTP（Unix socket 既定・service credential・読むだけ）。**開発版**で、人の認証・TLS・一般提供は含みません。[限界](docs/非公開レポートHTTP_v1.md)。 2.12 の明示的な user `writes: true` は [書く口](docs/運用_サーバ書込_2.12.md) を追加し、既存の read-only credential は維持します（3.13.0 で承認ページは無くなり、書く口の依頼はその場で行います）。
- `worker`（3.13.0 までの名前は `approval-worker`）: 招待の完了・`/activity` の要約と操作・安全装置を回す常駐。管理者が同じ隔離環境で常駐させます。
- X の本人公開指標の純粋な変換関数（API・投稿・台帳には未接続）。
- 独立監査（P2 4・P3 7）とその直し: Unix socket 既定・要求全体の 10 秒 deadline・分離検査の走査を読取 dir に限定・期間比較の母集団から時刻不一致の返信を除外・git 無しでも import 可。

### v2.4.0 の変更

- 利用者・管理者への停止／復旧メールと、元原稿の運用記録。設定・秘密は repo 外に保存します。導入時は [共通通知手順](docs/停止通知と運用記録.md) を実施します。
- VM・プロセス停止を検知する外部 missed-ping 監視と、board の停止理由表示。
- Threads の投稿エラーに HTTP 番号・許可した API コード等を記録。本文や任意のエラーメッセージは保存しません。
- `after` の投稿集計、project 指定、topic kind 別集計。
- self-update の署名検証と merge を同一 commit に固定し、媒体固有の秘密値を伏字対象へ追加。

### v2.0.0 で増えた口

- **台帳を repo の外へ**（設計 v2 §3・裁定 §7-1）。正は `$THTH_ROOT/accounts/`（`$THTH_ACCOUNTS_DIR` があればそちら）。
  **repo に台帳は入っていない**（2026-09-14 に 6 本を削除）——clone しても他人の台帳は付いて来ないので、
  `thth account add` で自分の 1 本を作るところから始まる。配るのは雛形 `accounts.example/` の 3 本だけ。
  repo の `accounts/` は**1 版だけ互換で読む**（stderr に警告 1 行・移行前の機械を止めないため）。
  `thth account migrate` が repo の中を外へ **copy**（移動しない・上書きしない・冪等）、
  `thth account add <name> --media threads|bluesky|mastodon --project <p>` が
  `accounts.example/<media>.json` の雛形から 1 本書く（**必ず `production: false`**）。
  **書く先は互換に落ちていても常に外**。`doctor`・`board` が置き場を 1 行で言う。
- **`thth ask before-you-post <account> --topic <語>`**（設計 v2 §1）。この語・この型・この時刻帯で
  スレッドがどう伸びたかを、件数と期間つきで返す。**読むだけ・手元の台帳だけ**（`provenance.source`
  は `local`。泉のサーバはまだ無い）。原稿本文は渡さないし、答えにも出ない。
  **n が閾値（既定 20）に満たない群は中央値を返さず `cannot_say` に理由を出す**——
  手元の水ではほとんどが `cannot_say` になる。それが正しい答えで、rc は 0。MCP からは `before_you_post`。
- **読む口（`thth where`・`thth thread`・`thth who`）は取得した投稿本文を保存しません**（裁定
  2026-09-16「横断の泉はやめる」）。検索語・投稿ID・件数等の最小限の実行記録と、絡みの台帳（自分の行為と反応）は残ります。
- **英語の文書**: [README.en.md](README.en.md)・[docs/usage.en.md](docs/usage.en.md)・[llms.txt](llms.txt)。
- **skill**: `skills/thth/SKILL.md`（wheel にも入る）。

- **動くもの**（`thth --help` の全サブコマンド）: `lint`・`preview`・`approve`・`account`・`revoke`・`posts`・`replies`・`measured`・`threads`・`after`・`analytics-report`・`study-report`・`study`・`unanswered`・`handoff-report`・`observe`・`morning`・`serve-reports`・`worker`・`topics`・`forms`・`queue`・`schedule`・`throw`・`run`・`systemd`・`board`・`collect`・`pull`・`auth`・`refresh`・`maintain`・`send`・`doctor`・`app`・`token`・`ask`・`mentions`・`profile`・`thread`・`where`・`who`・`retract`・`inflight`・`location`・`notifications`・`report`・`plaza`・`map`・`admin`。
- **最初の本番投稿の記録**: 2026-09-09、@aoking に疎通確認を 1 本（`17916074118445631`）。
- **未着手**: X・Facebook ページ・Instagram の各アダプタ。トピック検索の権限（tester には降りない）。泉のサーバ（v2-5）。
- **権限の制約**: tester に降りる scope は 5 つ。削除はできない。
- **配布**: `release` への反映と version tag の push を分けます。tag の CI が全件テスト後に PyPI と MCP registry へ OIDC で公開します。

## MCP registry

registry は「この PyPI の名前を名乗ってよいのは誰か」を、**配布物の README にこの 1 行があるか**で確かめる（設計 v2-4 §3・一次資料は quickstart・**L2**）。だから消さないこと——消すと登録（`mcp-publisher publish`）が通らなくなる。形（`server.json` と版の一致）は `tests/test_server_json.py` が見張る。

mcp-name: io.github.aokings/thth

## v3 基盤（2.5.0 から）

`study-report` は明示した施策の宣言と本人の投稿観測を結ぶ読み取り専用レポートです。採用者の本人確認や因果効果は主張しません。[使い方と契約](docs/施策レポート_v1.md)。

`analytics-report` はローカル台帳から期間・母数・欠測・根拠を揃えたスナップショットを返します。
[形式と使い方](docs/分析レポート_v1.md)。

`handoff-report` / MCP `operations_handoff` はローカル運用状態・通知未処理を鮮度の制約付きで返します。[形式と使い方](docs/運用引継ぎレポート_v1.md)。

## 管理者の読み口（2.9.0）

`thth admin inventory|account|log|tokens|timers|release|diff` は秘密値を出さない読み取り専用レポートです。開始時は `thth admin diff --since-last-read`、変化した account は `thth admin account <name>`、週1回 `thth admin tokens`。`--json` で構造化結果を取得できます。既読 cursor の保存は `admin diff --since-last-read --mark-read --by <名前>` の明示時だけです。VM の `admin timers` は観測を `state/_admin/timers.json` に保存します。`doctor <name>` と admin の明示的な `--probe` は秘密を除いた診断記録を `state/<name>/doctor.json` に保存します。HTTP／MCP は時刻付きの記録を読むだけで、観測・保存を実行しません。

`account add`（`--force` の上書きも）・`app set`（旧 flags も）・`auth`・`token set`・`token revoke` には `--by <名前>` が必須です。既存の作成来歴は上書きしません。`token revoke` はローカルcredential削除でありリモート側の認可取消ではありません。選定した管理変更は既定で管理者へ通知し、台帳の `notify_admin_on_change: false` で停止できます。

認証付きMCPの6道具とHTTPのadmin scopeは [管理者skill](skills/thth-admin/SKILL.md) を参照してください。管理レポートから承認・投稿・設定変更は行いません。ログ追記後のfsync失敗は変更を保持して `durability_unconfirmed`、部分追記は `outcome_uncertain` と報告します。再試行前に台帳とログを確認してください。
