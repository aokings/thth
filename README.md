# THTH（ThreadsThrower・承認済み投稿の投げ手）

設計: [docs/設計_THTH_2026-09-08.md](docs/設計_THTH_2026-09-08.md)（§8 の裁定済み。現行の設計書）。

承認済み（`status: approved`）の投稿ファイルを読み、静かな時間帯と最短間隔を守って 1 件だけ投げ、
post_id を書き戻して push し、投稿済みの返信を拾って追記し、走った記録と鮮度を出し、
走らなかったことを外部に知らせるまでを持つ。意味（何を書くか・承認・返信の解釈・採集箱への昇格）は持たない。
watchtower（`~/Developer/watchtower`）の隣に同じ流儀で並べる。watchtower は POST しないので投稿は混ぜない。

## 版

**1.1.0**（`thth --version`）。他人が自分の Meta アプリで導入できる最初の版。範囲は [docs/設計_v1.0.0_他人が導入できる版_2026-09-12.md](docs/設計_v1.0.0_他人が導入できる版_2026-09-12.md)、導入は [docs/導入_自分のMetaアプリで動かす.md](docs/導入_自分のMetaアプリで動かす.md)、文書の索引は [docs/README.md](docs/README.md)。

**`main` への push は保存だけ。`release` を進める操作が配布**（VM は `release` だけを追う。設計 §3.2.1）。

## 使う人へ

各プロジェクトのセッションが読むのは [docs/使い方_プロジェクトのセッション向け_2026-09-09.md](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) **だけ**。設計書は作った側の記録なので読まなくてよい。

## いまの状態（2026-09-12）

版 **1.1.0**。全件テスト **1506 件**（`python -m pytest tests/ -q -n auto`）。

**媒体**: Threads（稼働）・Bluesky・Mastodon（v2・同席用の台帳あり・未稼働）。

**本番稼働中**: Threads の 4 アカウント（nigamilab・asmon 関東・kopicha・masaru の同席用）。
timer（systemd・`thth systemd` で生成）で毎時投稿、返信の採集（`thth replies`）と数の採集
（`thth measured`）は稼働、トークン更新は `thth maintain` が毎日。

**入口は 3 つ**: 厚い CLI（`bin/thth`・`python -m thth`）、薄い MCP（`mcp/server.py`・読み取りと
同席の投稿）、timer。

**トピックの棚**（`thth topics`）: 観測者ごとに並ぶ・打ち消し `retract-note`・`history`。

- **動くもの**（`thth --help` の全サブコマンド）: `lint`・`preview`・`approve`・`account`・`revoke`・`posts`・`replies`・`measured`・`topics`・`forms`・`queue`・`schedule`・`throw`・`run`・`systemd`・`board`・`collect`・`auth`・`refresh`・`maintain`・`send`・`doctor`・`app`・`token`。
- **最初の本番投稿の記録**: 2026-09-09、@aoking に疎通確認を 1 本（`17916074118445631`）。
- **未着手**: X・Facebook ページ・Instagram の各アダプタ。トピック検索の権限（tester には降りない）。
- **権限の制約**: tester に降りる scope は 5 つ。削除はできない。
