# media-hub への回答: hub の道具を VM（wt）に同居させる件（2026-09-25）

THTH 開発セッションから。VM の実測は 2026-09-25 0:00 JST。

## 1. 同居してよいか → **よい**

- VM は 2 CPU・メモリ 3.8 GB（使用 0.5 GB）・ディスク 37 GB（空き 25 GB）・平常時の負荷はほぼ 0。1 日 1 回・数分・API 数十回なら THTH に響かない。
- 条件は 3 つ: THTH の置き場（`/srv/thth`）に書かない・THTH の秘密ファイルを読まない・THTH の timer の時刻を避ける（下の 3）。

## 2. 置き場と OS ユーザー

- **`/srv/hub/`・状態は `/srv/hub/state/`（VM の中の git で履歴）でよい**。`/srv` は root の持ち物なので、作るときだけ `sudo install -d -o wt -g wt -m 700 /srv/hub`。
- **ユーザーは THTH と同じ `wt` を勧める**。理由: THTH の置き場 `/srv/thth` は所有者だけ（0700）で、道具もその前提で検査している（別ユーザーには読めないし、緩めると THTH が止まる）。`thth board --json` を読むには `wt` で呼ぶのがいちばん素直。
  - 別ユーザー（例 `hub`）にしたいなら、`sudo` の規則を 1 行だけ足す形になる（`hub ALL=(wt) NOPASSWD: /home/wt/.local/bin/thth board --json`）。守りは堅くなるが、置き方が 1 段複雑になる。勧めるのは `wt`。
- `/srv/hub` は 0700・中のファイルは 0600 で（THTH と同じ作法）。umask 077 で書く（systemd の unit に `UMask=0077`）。

## 3. `thth board --json` の呼び方

- **hub の timer から呼んでよい**。読むだけで、THTH の台帳にも repo にも書かない。
- ユーザー `wt`・パスは `/home/wt/.local/bin/thth`（中身は `PYTHONPATH=/srv/thth/app python3 -m thth` 相当の入口）。unit の中では `Environment=PATH=/home/wt/.local/bin:/usr/bin:/bin` を付けると確実。
- 負荷: 1 回 1 秒前後（2026-09-25 実測）。repo の遅れを確かめる所は、THTH の実行が repo を使っている間は取り込まずに前回の値で答える（3.8.2 から）ので、THTH とぶつかっても止めない。
- **時刻**: THTH の timer は口座ごとに 10 分おきで、分の 3・5・6・8 の辺りに走り、1 回 10〜60 秒。夜中の `thth-maintain` は 04:17。**06:00 ちょうど〜06:02 は空いている**ので、`OnCalendar=*-*-* 06:00:30` を勧める（`RandomizedDelaySec` は付けない・ずれると THTH の枠に入る）。
- board の欄の意味は、先日の回答（`docs/回答_media-hub_THTHの投稿の現状_2026-09-24.md` の 4）を正とする。欄は増えることはあっても、名前を黙って変えない方針。

## 4. 道具の配り方

- **THTH の配り方**: VM の `/srv/thth/app` は GitHub の repo（aokings/thth）の git clone。`thth run` が毎回 `release` 枝を ff で取り込んで自分を更新する（release を進めるのが本番反映）。PyPI は外の利用者向けで、VM では使っていない。rsync は使っていない。
- **hub に勧める形**: GitHub に**非公開の repo**（例 `aokings/media-hub`）を作り、VM で `git clone` して `/srv/hub/app` に置く。更新は当面**手で** `git -C /srv/hub/app pull --ff-only`（人が打つ）。THTH のような自動更新は、道具が落ち着いてから（`release` 枝を分けて、timer の最初に ff だけする形）。
  - VM から GitHub に入る鍵は、hub 専用の deploy key（読み取りだけ）を 1 本作り、`~/.ssh/config` に `Host gh-hub`（THTH は `gh-thth` という別名で分けている）として足す。THTH の鍵は使い回さない。
  - remote を持ちたくないなら、Mac から `rsync -a --delete` で `/srv/hub/app` に送る手もある。ただし履歴と「どの版が動いているか」が VM に残らないので、勧めない。
- 注意: 非公開 repo の GitHub Actions は課金の枠を食う（9/24 に kopicha の repo の Actions が支払い・上限で止まった）。hub の repo に Actions を置くなら、それも見張りの対象に。

## 5. Mac からのラッパ

- **`~/.local/bin/thth` と同じ作りでよい**。作りの要点（THTH のラッパから）:
  - `ssh wt` に、VM 側で `PATH="$HOME/.local/bin:$PATH"; exec hub` と引数を渡す。引数は **1 つずつ `printf '%q'` で引用**して、VM 側のシェルに展開させない（glob は使わず、VM のパスをそのまま渡す）。
  - **tty は割り当てない**（`ssh -t` は、秘密を対話で入れる命令だけ）。tty を付けると、呼び出す側が端末でないときに出力がたまって返らない。
  - 終了コードをそのまま返す（`exec ssh …`）。
  - macOS 既定の bash 3.2 で動くように（`set -u` の下で空配列を展開しない）。
- 許可は `Bash(hub:*)` の 1 行で足りる。ラッパの中で生の `ssh` を持たせるので、セッションに生の `ssh` の許可を配らずに済む（THTH がこの形にした理由）。

## 6. 認証の置き場

- **`~/.config/media-hub/`（repo の外・ディレクトリ 0700・ファイル 0600）でよい**。THTH も VM の秘密は repo の外の所有者だけのファイルに置いている（`/srv/thth/secrets/`・`~/.config/thth/*.token`）。
- 作法（THTH と masaru の決まりごと）:
  - 値を**チャットに貼らない**・コマンドの出力に出さない（`echo` しない）。Claude のセッションに値を見せない。
  - 置くときは人が VM で直接入れる: `ssh -t wt 'install -m 600 /dev/stdin ~/.config/media-hub/github.token'` のあとに貼って Ctrl-D（端末に残らない）。または `read -rs` で受ける小さな命令を hub に用意する（THTH の `thth token set` と同じ考え）。
  - Claude の Edit／Write で秘密ファイルを作らない・編集しない（書いた内容が会話の記録に流れる）。
  - トークンは最小の権限で: GitHub は fine-grained・読み取りだけ・Actions と中身の読み取りを対象の repo だけ。Cloudflare は Pages の読み取りと Analytics の読み取りだけ。期限を付ける。
  - 道具の出力（JSON）にトークンの値や認証のヘッダを入れない。エラーの文面も、値を伏せてから出す。

## まとめ

同居してよい。`/srv/hub`（0700・`wt`）に置き、`thth board --json` は `wt` から 06:00:30 に読む。配り方は非公開 repo の clone を手で pull、秘密は `~/.config/media-hub/`（0600）に人が直接入れる。ラッパは thth と同じ作りで、tty を付けず、引数を 1 つずつ引用する。
