# THTH

THTH は、自分の SNS アカウントで**調べる・投稿する・予約する・振り返る**ためのコマンドラインツールです（Threads・Bluesky・Mastodon・X）。

CLI なので、Claude Code などの AI アシスタントがそのまま操作できます。同梱の MCP サーバーとスキルは、その操作を楽にするための補助です。THTH は何を書くかを決めません。頼まれたときだけ動きます。

English: [README.en.md](README.en.md) · サイト: https://thth.me · 文書の索引: [docs/README.md](docs/README.md)

## できること

| | Threads | Bluesky | Mastodon | X |
|---|---|---|---|---|
| 投稿・予約 | ○ | ○ | ○ | ○ |
| 削除 | ○ | － | ○ | ○ |
| 返信とインサイト（表示・いいね・返信などの数）の取得 | ○ | ○ | ○ | － |
| キーワード検索（`thth topics --search`・`thth where`） | ○ | ○ | ○ | － |
| 言及（`thth mentions`） | ○ | ○ | ○ | － |
| プロフィールと場所の検索 | ○ | － | － | － |

- **調べる**: `thth topics <アカウント> --search <語>` で、その語の件数・投稿者の数・最新の投稿を見ます。`thth where` は「次にどこで話すか」を、自分がすでに絡んだ相手を重ねて探します。
- **出す**: `thth send` で今すぐ、`thth schedule` で予約、`--reply-to` で返信です。
- **振り返る**: `thth collect` が投稿後 1 時間〜30 日の決まった時点で返信とインサイトを取得し、`thth replies`・`thth measured` で読みます。
- **再投稿といいね**: `thth repost`・`unrepost`・`like`・`unlike`（3.15.0 から）。Threads は再投稿だけ（Threads の API にいいねが無い）。Bluesky は両方。Mastodon と X のいいねは、そのアカウントを認可し直してから（[リリースノート 3.15.0](docs/リリースノート_3.15.0_2026-10-09.md)）。

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

アカウントの台帳を自分の機械に置き、下書きのファイルを承認してから timer で出す運用もできます。承認は 2 段（本文と digest を見せる → その digest で確定）で、記録は git に残ります。準備は [統一ガイド](docs/導入_承認を押すだけ.md)、日々の使い方は [プロジェクトのセッション向け](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) にあります。

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

- 検索結果（`topics --search`・`where`・`thread`・`who`）の本文と投稿者は、その場で見せるだけで保存しません。
- ダイレクトメッセージは読みません。
- トークンとシークレットは、ログと出力から伏せます。

詳しくは [プライバシーポリシー](https://thth.me/privacy/) にあります。

## コマンドの一覧

`thth --help` で各コマンドの説明が出ます。

動くもの（`thth --help` の全サブコマンド）: `lint`・`preview`・`approve`・`account`・`revoke`・`posts`・`replies`・`measured`・`threads`・`after`・`analytics-report`・`study-report`・`study`・`unanswered`・`handoff-report`・`observe`・`morning`・`serve-reports`・`worker`・`topics`・`forms`・`queue`・`schedule`・`throw`・`run`・`systemd`・`board`・`collect`・`pull`・`auth`・`refresh`・`maintain`・`send`・`doctor`・`app`・`token`・`ask`・`mentions`・`profile`・`thread`・`where`・`who`・`retract`・`repost`・`unrepost`・`like`・`unlike`・`inflight`・`location`・`notifications`・`report`・`plaza`・`map`・`admin`・`login`・`logout`。

## 版と配布

- 版の正本は `thth/VERSION` です。手元の版は `thth --version` で確かめます。
- `main` への push は保存だけです。`release` を進めると運営のサーバーに出て、版の tag を push すると PyPI と MCP registry に出ます。
- 依存は 0 です。入口は `thth` と `thth-mcp` の 2 つです。

## ライセンス

MIT。

## MCP registry

registry は「この PyPI の名前を名乗ってよいのは誰か」を、配布物の README にある次の 1 行で確かめます。消さないでください。

mcp-name: io.github.aokings/thth
