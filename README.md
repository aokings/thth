# THTH（ThreadsThrower・承認済み投稿の投げ手）

設計: [docs/設計_THTH_2026-09-08.md](docs/設計_THTH_2026-09-08.md)（§8 の裁定済み。現行の設計書）。

承認済み（`status: approved`）の投稿ファイルを読み、静かな時間帯と最短間隔を守って 1 件だけ投げ、
post_id を書き戻して push し、投稿済みの返信を拾って追記し、走った記録と鮮度を出し、
走らなかったことを外部に知らせるまでを持つ。意味（何を書くか・承認・返信の解釈・採集箱への昇格）は持たない。
watchtower（`~/Developer/watchtower`）の隣に同じ流儀で並べる。watchtower は POST しないので投稿は混ぜない。

## 版

**2.0.1**。門を無料で開ける版（2.0.1: 同席送信の採集・セキュリティ監査 2 回の直し）（`pip install`・`thth ask`・`thth share`・台帳を repo の外へ）。範囲と裁定は [docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §3・§7、その前の版は [docs/設計_v1.0.0_他人が導入できる版_2026-09-12.md](docs/設計_v1.0.0_他人が導入できる版_2026-09-12.md)、導入は [docs/導入_自分のMetaアプリで動かす.md](docs/導入_自分のMetaアプリで動かす.md)、文書の索引は [docs/README.md](docs/README.md)。

`thth/VERSION` は `2.0.1`（版は `thth/VERSION` の 1 か所・`server.json` はテストで一致を強制）。`origin/release` は v2.0.0（`216af9f`）で、**v2.0.1 はまだ配っていない**（配布は masaru の一言）。出口条件の試験は（運用日誌 thth-notes: `記録/試験_LLMに選ばせる_2026-09-13.md`）。

**`main` への push は保存だけ。`release` を進める操作が配布**（VM は `release` だけを追う。設計 §3.2.1）。

## 使う人へ

各プロジェクトのセッションが読むのは [docs/使い方_プロジェクトのセッション向け_2026-09-09.md](docs/使い方_プロジェクトのセッション向け_2026-09-09.md) **だけ**。設計書は作った側の記録なので読まなくてよい。

## いまの状態（2026-09-13）

版 **2.0.2**。全件テスト **1990 件**（`python -m pytest tests/ -q -n auto -p no:cacheprovider`・1 skip・rc=0）。

**媒体**: Threads（稼働）・Bluesky・Mastodon（同席用の台帳あり・未稼働）。

**本番稼働中**: Threads の 4 アカウント（nigamilab・asmon 関東・kopicha・masaru の同席用）。
timer（systemd・`thth systemd` で生成）で毎時投稿、返信の採集（`thth replies`）と数の採集
（`thth measured`）は稼働、トークン更新は `thth maintain` が毎日。

**入口は 4 つ**: 厚い CLI（`bin/thth`・`python -m thth`）、薄い MCP（`mcp/server.py`・読み取りと
同席の投稿・`before_you_post`）、timer、そして `pip install`（`thth`・`thth-mcp` の entry point・
**依存 0**。PyPI に公開済み・MCP registry に `io.github.aokings/thth` として登録済み）。

**トピックの棚**（`thth topics`）: 観測者ごとに並ぶ・打ち消し `retract-note`・`history`。

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
- **`thth share on|off|status|log|sync`**（設計 v2 §3・裁定 §7-3）。**既定 off**、設定が無い・壊れて
  いるときも off。off のあいだは outbox が **0 バイト**。on にしても
  `$THTH_ROOT/state/share/outbox/<YYYY-MM>.ndjson` に積むだけで、**送り先はまだ無い**（v2-5）。
  積んだ全部は `thth share log` で読める。落ちるのは語・audience・型・件数・時刻帯・
  post_id の**塩つき sha256**（塩は outbox に出ない）。本文・返信本文・返信者の username・
  自分の判断・アカウント名・トークン・repo のパス・生の post_id は落ちない。
- **英語の文書**: [README.en.md](README.en.md)・[docs/usage.en.md](docs/usage.en.md)・[llms.txt](llms.txt)。
- **skill**: `skills/thth/SKILL.md`（wheel にも入る）。

- **動くもの**（`thth --help` の全 27 サブコマンド）: `lint`・`preview`・`approve`・`account`・`revoke`・`posts`・`replies`・`measured`・`threads`・`topics`・`forms`・`queue`・`schedule`・`throw`・`run`・`systemd`・`share`・`board`・`collect`・`auth`・`refresh`・`maintain`・`send`・`doctor`・`app`・`token`・`ask`。
- **最初の本番投稿の記録**: 2026-09-09、@aoking に疎通確認を 1 本（`17916074118445631`）。
- **未着手**: X・Facebook ページ・Instagram の各アダプタ。トピック検索の権限（tester には降りない）。泉のサーバ（v2-5）。
- **権限の制約**: tester に降りる scope は 5 つ。削除はできない。
- **masaru の手が要るもの**: repo を public にする切替と `LICENSE`、tag と `release` を進める操作（PyPI と MCP registry は 2.0.1 まで公開済み）。

## MCP registry

registry は「この PyPI の名前を名乗ってよいのは誰か」を、**配布物の README にこの 1 行があるか**で確かめる（設計 v2-4 §3・一次資料は quickstart・**L2**）。だから消さないこと——消すと登録（`mcp-publisher publish`）が通らなくなる。形（`server.json` と版の一致）は `tests/test_server_json.py` が見張る。

mcp-name: io.github.aokings/thth

