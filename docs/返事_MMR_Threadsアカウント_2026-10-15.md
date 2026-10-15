# 返事: マイミュージックリサーチの Threads を THTH に載せる（2026-10-15・THTH開発 第2）

依頼: music-research `docs/依頼_thth_MMRのThreadsアカウント_2026-10-15.md`（#2396）

## 1. 原稿の置き場 — 新しい repo は作らない（queue なし・その場で出す形）

@head.lesson（headlesson）と同じ形にした。原稿は music-research の repo の中に置いたままでよく、VM へは渡さない。出すときは本線が `thth send` を打ち、その場で 2 段の承認を通す。

- 一段目: 本文を渡すと、本文と digest を見せて止まる（何も出ない）
- masaru が本文を見て「出して」
- 二段目: 同じ本文に `--confirm <digest>` を付けて出る

timer で決まった時刻に出す（queue の予約）は、この形では使わない。予約が要るようになったら、そのとき queue の置き場を相談する。そのときも新しい repo より、VM の中の置き場（headlesson と同じ `repos/_<名前>`）のほうが軽い。

`thth send` は VM で打つ: `ssh wt 'env THTH_ROOT=/srv/thth /srv/thth/app/bin/thth send music-research-threads --text-file - …'`（`ssh wt '…'` の形ではログインの設定を読まないので、`thth` を素の名前で呼ぶと見つからない）。

## 2. アカウントの追加 — 済み

10/15 15:57 に THTH の側で VM の台帳に足した。

- `music-research-threads`（project `music-research`、media threads）
- redirect_uri は `https://thth.me/callback/`（ほかのアカウントと同じ）
- production: false・scheduled: false（このままでは出ない）

## 3. 認可と、masaru が用意するもの

1. masaru が Threads のアカウントを作る（Instagram のアカウントが要る）。
2. 認可: `thth auth music-research-threads --by masaru` を VM で打つと認可の URL が出る。masaru がその URL をブラウザで開き、MMR の Threads で承認する。トークンは relay が受け取る。打つのは本線でも THTH でもよい。
   - Meta のアプリの側で、このアカウントが断られることがある（アプリがまだ開発中の扱いの権限があるとき）。断られたら、Meta のアプリで Threads のテスターにこのアカウントを足し、Threads の設定で招待を受けてもらう。断られた画面の文を THTH に送ってくれれば調べる。
3. 本番にする（production: true）のは、最初の 1 本を出す直前。masaru の「始めて」を受けて THTH が台帳を書き換える。
