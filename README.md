# THTH（ThreadsThrower・承認済み投稿の投げ手）

設計: [docs/設計_THTH_2026-09-08.md](docs/設計_THTH_2026-09-08.md)（review 待ち。実装は §8 の裁定後）。

承認済み（`status: approved`）の投稿ファイルを読み、静かな時間帯と最短間隔を守って 1 件だけ投げ、
post_id を書き戻して push し、投稿済みの返信を拾って追記し、走った記録と鮮度を出し、
走らなかったことを外部に知らせるまでを持つ。意味（何を書くか・承認・返信の解釈・採集箱への昇格）は持たない。
watchtower（`~/Developer/watchtower`）の隣に同じ流儀で並べる。watchtower は POST しないので投稿は混ぜない。

## 使う人へ

各プロジェクトのセッションが読むのは [docs/使い方_プロジェクトのセッション向け_2026-09-09.md](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) **だけ**。設計書は作った側の記録なので読まなくてよい。

## いまの状態（2026-09-09）

T1（core ＋ 厚い CLI ＋ 薄い MCP の読み取り系）実装済み。`python3 -m pytest tests/ -q` で
61 件緑。詳細は [docs/記録/検収_T1_2026-09-09.md](docs/記録/検収_T1_2026-09-09.md)。
本物の Threads API・トークンには一切触れていない（発注 [docs/記録/発注_T1_2026-09-09.md](docs/記録/発注_T1_2026-09-09.md) の範囲どおり）。

- **動くもの**: `thth send`（同席の投稿）・`thth lint`／`preview`／`queue`／`board`・`thth token set`／`auth`／`refresh`・`thth doctor`。テスト 126 件。
- **最初の本番投稿**: 2026-09-09、@aoking に疎通確認を 1 本（`17916074118445631`）。
- **アカウント 4 本**: nigamilab・kanto_nyushi・kopi_chaba・aoking。トークンは VM の `~/.config/thth/` に 600。
- **未着手**: timer（systemd）・返信と数の自動収集（T3・T4）・MCP の配線・X・FB／Instagram。
- **権限の制約**: tester に降りてくるのは 5 つだけ。トピックは**付けられるが探せない**。削除もできない。
- 次段: T2（Threads 本番 1 件・masaru の手作業待ち）。
