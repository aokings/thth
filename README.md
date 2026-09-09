# THTH（ThreadsThrower・承認済み投稿の投げ手）

設計: [docs/設計_THTH_2026-09-08.md](docs/設計_THTH_2026-09-08.md)（review 待ち。実装は §8 の裁定後）。

承認済み（`status: approved`）の投稿ファイルを読み、静かな時間帯と最短間隔を守って 1 件だけ投げ、
post_id を書き戻して push し、投稿済みの返信を拾って追記し、走った記録と鮮度を出し、
走らなかったことを外部に知らせるまでを持つ。意味（何を書くか・承認・返信の解釈・採集箱への昇格）は持たない。
watchtower（`~/Developer/watchtower`）の隣に同じ流儀で並べる。watchtower は POST しないので投稿は混ぜない。

## いまの状態（2026-09-09）

設計 review 中。実装は未着手。T1 の発注書は [docs/発注_T1_2026-09-09.md](docs/発注_T1_2026-09-09.md)。

- 裁定済み: 当面 Threads だけ（X は保留）／アカウントはプロジェクト・サテライトごとに分ける／2 本目は asmon 関東／入口は timer・手打ち・薄い MCP の 3 つで core は 1 つ。
- masaru の手待ち: **Meta アプリと tester だけ**（scope 4 つを最初から）。GitHub repo は 2026-09-09 に作成済。
