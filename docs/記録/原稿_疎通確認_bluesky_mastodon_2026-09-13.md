# 原稿: Bluesky・Mastodon の最初の実投稿（疎通確認・2026-09-13）

**このセッションは送っていません。** 承認するのも打つのも masaru です。ここに置くのは
「本文 2 本」と「VM で打つコマンド列」だけ。

**なぜ queue でなく `thth send` か。** 最初の 1 本は**同席の様態**で出すのが筋です
（`masaru-threads` の疎通確認と同じ）。`masaru-bluesky` / `masaru-mastodon` は
`repo_dir: $THTH_ROOT/repos/_none`・queue を持たない・`scheduled: false` の台帳なので、
そもそも timer も予約投稿も無い（設計 `docs/設計_v2_泉と門_2026-09-13.md` §4.2、
引継ぎ `docs/引継ぎ_開発セッション_2026-09-13.md` §3）。

---

## 1. 本文（そのままファイルに入れる）

**URL を入れていません**（`docs/記録_URLの使用を限定した期間_2026-09-12.md` の期間中）。
ハッシュタグも入れていません。どちらも 1 本だけ・短く・日本語。

### Bluesky（`masaru-bluesky`）

```
THTH という道具の疎通確認です。承認は人が、送信は道具が。
```

- **31 字**（`thth send` は上限を超えたら切り詰めずに断ります。Bluesky の上限は
  **300**——`app.bsky.feed.post.text` の maxGraphemes 300・**L2**）。
- 絵文字も結合文字も無いので、数え方の揺れ（`thth.queuefile.char_count` は Threads の
  数え方）に当たりません。

### Mastodon（`masaru-mastodon`）

```
THTH という道具の疎通確認です。承認は人が、送信は道具が。
```

- 同じ本文で構いません（**媒体をまたいで比較しない**ので、揃える必要も違える必要も
  ありません・設計 v2 §2.1）。`mastodon.social` の上限は既定 500（**L2**）。

**本文を変えるなら、変えた本文で dry-run をやり直してください。** `--confirm` に渡す
digest は本文から計算されるので、1 文字でも違えば一致しません（それがこの仕組みの目的）。

---

## 2. VM で打つ順番

VM は `ssh wt` の `/srv/thth`。`thth` は PATH に通っています。

### 2-0. まだ認可していなければ、先に

```
ssh -t wt 'thth auth masaru-bluesky'        # handle と App Password を対話で入れる
ssh -t wt 'thth token set masaru-mastodon'  # 発行済みの access token を貼る
```

`-t` が要るのはこの 2 つです（**端末から読む**ので、`-t` が無いと入力を受け取れません）。
手順の詳細は [導入_Bluesky_2026-09-13.md](../導入_Bluesky_2026-09-13.md) と
[導入_Mastodon_2026-09-13.md](../導入_Mastodon_2026-09-13.md)。

確かめる:

```
ssh -t wt 'thth doctor masaru-bluesky'
ssh -t wt 'thth doctor masaru-mastodon'
```

### 2-1. 本文のファイルを VM に置く

**本文をコマンドライン引数で渡す口はありません**（シェルの履歴に残る・引用の扱いで
本文が変わる。`thth/cli.py::cmd_send` の docstring）。ファイルか標準入力だけです。

```
ssh wt 'mkdir -p ~/thth-send'

ssh wt "cat > ~/thth-send/bluesky-2026-09-13.txt" <<'EOF'
THTH という道具の疎通確認です。承認は人が、送信は道具が。
EOF

ssh wt "cat > ~/thth-send/mastodon-2026-09-13.txt" <<'EOF'
THTH という道具の疎通確認です。承認は人が、送信は道具が。
EOF
```

`<<'EOF'`（引用符付き）にしてあるので、手元のシェルが本文を展開しません。
置けたか確認:

```
ssh wt 'cat ~/thth-send/bluesky-2026-09-13.txt; echo ---; cat ~/thth-send/mastodon-2026-09-13.txt'
```

### 2-2. dry-run（**まだ出ません**）

**引数は `--text-file`** です（`thth send <account> --text-file <path>`。位置引数は
account だけ）。

```
ssh -t wt 'thth send masaru-bluesky  --text-file ~/thth-send/bluesky-2026-09-13.txt'
ssh -t wt 'thth send masaru-mastodon --text-file ~/thth-send/mastodon-2026-09-13.txt'
```

出るのはこの形です:

```
mode: rehearsal
投げるはずの本文:
THTH という道具の疎通確認です。承認は人が、送信は道具が。
digest: 0123456789ab
```

- **`mode: rehearsal` のあいだは、媒体を 1 度も叩いていません**（`createSession` も
  呼ばない・`tests/test_send_media.py`）。
- `digest` は本文・account・reply_to・topic から決まる 12 桁（`approval.compute_send_digest`）。
  **次の段でそのまま貼ります。**

### 2-3. `--production` が効くようにする（**ここが masaru の判断**）

いまの `accounts/masaru-bluesky.json`・`accounts/masaru-mastodon.json` は
**`production: false`** です。この状態では

```
ssh -t wt 'thth send masaru-bluesky --text-file … --production --confirm <digest>'
```

と打っても **`mode: rehearsal` のまま何も出ません**（fail-closed・設計 §0。
`tests/test_send_media.py::test_send_blueskyは台帳がproduction_falseなら出さない`）。

**`--production` を効かせるには、台帳を直して配る必要があります**——手元で
`"production": true` に変え、commit して、**`origin/release` に配って**、VM が
それを取り込んだあとです（VM の `app` は `origin/release` を追いかけています。
`docs/引継ぎ_運用セッション_2026-09-13.md`）。`git push origin main` だけでは
VM に届きません。

VM が新しい台帳を見ているかは、これで確かめられます:

```
ssh wt 'python3 -c "import json;print(json.load(open(\"/srv/thth/app/accounts/masaru-bluesky.json\"))[\"production\"])"'
```

### 2-4. 実投稿（1 本ずつ・digest を貼る）

```
ssh -t wt 'thth send masaru-bluesky  --text-file ~/thth-send/bluesky-2026-09-13.txt  --production --confirm <2-2 で出た digest>'
ssh -t wt 'thth send masaru-mastodon --text-file ~/thth-send/mastodon-2026-09-13.txt --production --confirm <2-2 で出た digest>'
```

- **digest は媒体ごとに違います**（account 名が計算に入るため）。取り違えると
  `digest が一致しないので送信しません` で止まります（省略も拒否）。
- 成功するとこう出ます:

```
mode: production
投稿しました: post_id=at://did:plc:…/app.bsky.feed.post/…
```

### 2-5. 出たことを、道具の側からも確かめる

```
ssh wt 'ls ~/.config/thth/ | cat'                 # トークンが在るか（値は見ない）
ssh -t wt 'thth account masaru-bluesky'           # 「<媒体> 側 : 直近 N 件」が出る
ssh -t wt 'thth posts masaru-bluesky'             # 実際に出ている投稿の一覧
ssh wt 'ls /srv/thth/state/masaru-bluesky/sent/'  # **送った本文そのものの記録**
```

`sent/` は `thth send` が公開に成功した直後に書く正本です（`post_id` は
percent-encode してファイル名にしています——Bluesky の `post_id` は `/` と `:` を
含む AT URI なので）。

---

## 3. 出さないと決めるとき

- 台帳を `production: true` にしない限り、**何度打っても出ません。** 迷ったら
  2-2 まででやめてください。
- 出したあとに消したいときは、**媒体側の画面から消してください。** THTH に削除の口は
  ありません（設計 §3.4「アダプタは渡されたものを 1 回投げるしかしない」）。
- 疎通確認が済んだら、台帳を `production: false` に戻すかどうかも 1 つの判断です
  （`scheduled: false` のままなので timer は回りませんが、`--production` は効き続けます）。

---

## 4. このセッションが確かめたこと・確かめていないこと

| 何 | 印 |
|---|---|
| `thth send` が Bluesky・Mastodon で dry-run → `--production` → `sent` まで通る | **L1**（`tests/test_send_media.py`・**偽サーバ**） |
| 台帳が `production: false` なら `--production` でも出ない | **L1**（同上） |
| Bluesky の上限 300 grapheme / Mastodon の上限 既定 500 | **L2**（lexicon と docs.joinmastodon.org） |
| **本物の `bsky.social` / `mastodon.social` に対して動くか** | **未確認**。このセッションは実 API に触れていません |
| VM の `thth` の版・`~/.config/thth/` の中身 | **未確認**。VM は運用セッションの担当 |
