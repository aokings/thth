# 導入: 自分の Meta アプリで THTH を動かす（2026-09-12）

THTH（ThreadsThrower）を、**masaru 以外の人が・自分の Meta アプリで・自分の VM で**動かすための手順です。
使い方（毎日の操作）は [使い方_プロジェクトのセッション向け_2026-09-09.md](使い方_プロジェクトのセッション向け_2026-09-09.md) を読んでください。この文書は**最初の 1 回**だけの話です。

## この文書の読み方 —— 証拠段階

**各手順に出典（設計書の節番号）と証拠段階を付けてあります。** 盛っていません。

| 印 | 意味 |
|---|---|
| **L1** | **この repo で実測できた。** コードを読めば分かる、またはテスト（`tests/test_fresh_install.py`）が毎回確かめている |
| **L2** | **設計書が Meta の公式文書を読んで書いたもの。** この repo からは確かめられない |
| **L3** | **masaru の操作でしか確かめられていない。** この文書を書いた側は確かめていない。**外れる可能性がある** |

**L3 の手順で詰まったら、それは想定内です。** 詰まった場所を記録して、この文書に足してください。

---

## 1. 前提（VM・Python 3.12・git）

**依存は Python の標準ライブラリだけです。** `pip install` は要りません（テストを走らせるなら `pytest` だけ）。

| 前提 | 中身 | 出典 | 証拠 |
|---|---|---|---|
| Python | 3.12。`urllib.request` で HTTP を叩く。`thth/` 配下の import は全部標準ライブラリで、requirements も setup.py も無い | 設計 §2.2 | **L1** |
| git | **利用者 repo は `origin` を持つ clone でなければなりません。** origin が無いと `thth throw` は投稿せずに止まります（`origin という remote が見つかりません（同期元を確認できないため投稿しません）`・rc=2） | 設計 §4.3 | **L1** |
| 置き場 | `$THTH_ROOT/app` に この repo を clone。`state/` と `logs/` は `$THTH_ROOT` の下に出ます | 設計 §3.1 | **L1**（`thth/accounts.py` の `thth_root()`: 環境変数 `THTH_ROOT` → app repo の basename が `app` ならその親 → それ以外は app repo 自身） |
| VM の現物 | `/srv/thth/`・作業ユーザー `wt`・watchtower の隣 | 設計 §3.1・§5 | **L3** |

```
$THTH_ROOT/
  app/                   # この repo の clone
  repos/<project>/       # 利用者 repo の clone（origin つき）
  state/<account>/       # inflight・last・runs
  logs/<account>/
~/.config/thth/          # 秘密。600
```

**`~/.config/thth/` を選んだ理由**は「`/etc` は root が要り、トークン更新で THTH 自身が書き換えるファイルを root 所有にしたくない」（設計 §8-4・**L2**＝設計の判断）。

### 打ち方は 2 つ

```bash
python -m thth doctor <account>     # PATH を通す前。clone の中で打つ
ln -s $THTH_ROOT/app/bin/thth ~/.local/bin/thth   # 通したあと
thth doctor <account>
```

**`bin/thth` は symlink 越しでも動きます**（`realpath` を通してある。`tests/test_cli_entrypoint.py`・**L1**）。以下この文書では `thth …` と書きます。

---

## 2. Meta アプリ（開発モード・use case Threads・tester 追加）

**この節はまるごと Meta の管理画面の話で、この repo からは 1 つも確かめられません。**

**アプリは 1 つでよい。** アクセストークンは app-scoped（アプリとユーザーの組に固有）なので、**1 アプリ × N アカウント = N トークン**です。アカウントごとにアプリを作らないでください（設計 §2.2・**L2**）。

| 手順 | 中身 | 出典 | 証拠 |
|---|---|---|---|
| 2-1 | Meta for Developers でアプリを作る。**開発者アカウントは個人の Meta アカウント**で、プロジェクト名では登録できない。プロジェクト名になるのは**アプリ名**の方（認可画面に出るのがこれ） | 設計 §8-8 | **L3**（設計自身が「第三者記事の記述で、公式ページには明記が見つからなかった」と書いている） |
| 2-2 | use case に **「Threads API」** を選ぶ | 設計 §2.2 | **L2** |
| 2-3 | 権限を足す。初期状態ではどれも「アプリレビューに追加」しか出ていないが、**押すとアプリで使える状態になり、tester なら審査を出さずにテストできる**（「テスト準備完了」に変わる） | 設計 §2.2 | **L3**（masaru の実操作） |
| 2-4 | **開発モードのまま**にする。App Review は通さない | 設計 §8-8・§8-17 | **L2** |
| 2-5 | App roles > Roles の **「Add or Remove Threads Test Users」** で、使う Threads アカウントを 1 本ずつ **Threads tester** に招待する。**招待だけでは何も起きない**ので、使う見込みのあるアカウントは先に招待しておいてよい | 設計 §2.2・§4.2 | **L2**（招待の場所）／**L3**（「招待だけでは何も起きない」） |
| 2-6 | 招待した数だけ、**それぞれのアカウントで Threads アプリを開いて承諾**する | 設計 §4.2 | **L3** |
| 2-7 | Settings の **「Threads app ID」「Threads app secret」**を控える。**Meta アプリ側の ID とは別物**で、取り違えると認可が通らない | 設計 §2.2 | **L2** |
| 2-8 | `thth auth` を使うなら Redirect URI を登録する（静的サイトの URL でよい。認可後に URL バーへ `?code=…#_` が付いて戻る。末尾の `#_` は code に含めない） | 設計 §9-2 | **L3** |

### 降りてくる権限は 5 つだけ

**管理画面で 11 個の権限を「アプリレビューに追加」しても、tester の認可画面に出るのは 5 つです**（設計 §2.2・**L3**）。

出る: `threads_basic`・`threads_content_publish`・`threads_manage_replies`・`threads_manage_insights`・`threads_read_replies`

出ない: `threads_delete`・`threads_keyword_search`・`threads_location_tagging`・`threads_manage_mentions`・`threads_profile_discovery`・`threads_share_to_instagram`

**投稿・返信・返信の取得・数の取得は 5 つで足ります。** 届かないのは検索・メンション・削除・位置情報です。**削除ができないので、事故の後始末は Threads の画面から手で行います**（設計 §2.2）。

**「Threads API はビジネス認証済みアカウントが要る」という第三者の記述があります**（設計 §8-8・**L3**・公式で裏が取れていない）。開発モード＋tester なら不要のはずですが、アプリを作る場面で分かるので、違っていたらこの文書に足してください。

---

## 3. `~/.config/thth/app.env`

**全アカウント共通の 1 本です。** アカウントが増えても書き足しません（設計 §3.1・§9-1b）。

```
THREADS_APP_ID=<Threads app ID>
THREADS_APP_SECRET=<Threads app secret>
```

```bash
chmod 600 ~/.config/thth/app.env
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| 既定のパスは `~/.config/thth/app.env`。**環境変数 `THTH_APP_ENV_PATH` で差し替えられる**（テストの隔離用） | `thth/appenv.py` `default_path()` | **L1** |
| 鍵の名前は `THREADS_APP_ID` と `THREADS_APP_SECRET` の 2 つだけ。どちらかが空なら `app.env に項目が足りません` | `thth/appenv.py` `REQUIRED_KEYS` | **L1** |
| 読むときに 600 でなければ**警告して直します**（`警告: … のパーミッションが … です。600 に直します。`） | `thth/secrets_fs.py` `ensure_mode_600()` | **L1** |
| **`app.env` を使うのは `thth auth` だけです。** `lint`・`board`・`throw` は読みません。`doctor` は**存在と、2 つの項目（`THREADS_APP_ID`・`THREADS_APP_SECRET`）が空でないことだけ**を見て、足りなければ `次の一手: 導入文書 §3` と言います（値は出力しません。実測: 項目が空でも同じく `次の一手: 導入文書 §3` になる） | 実測（§7 の乾式試験・`tests/test_doctor_next_step.py`） | **L1** |

**だから、管理画面の「ユーザートークン生成ツール」で発行したトークンを `thth token set` で入れる運用なら、`app.env` は作らなくても動きます**（設計 §4.2「置かないものは漏れない」）。`thth auth` を使う日に作ってください。

**値をこの repo に書かないでください。** 台帳（`accounts/*.json`）にも、docs にも、commit message にも。

---

## 4. アカウント台帳 `accounts/<account>.json`

**台帳はこの repo に commit します**（秘密は入りません）。探し先は **app repo の `accounts/`** で、環境変数 `THTH_APP_DIR` で差し替えられます（`thth/accounts.py` `app_dir()`・**L1**）。

**clone した直後の `accounts/` には masaru の 4 本（`nigamilab-threads`・`asmon-kanto-threads`・`kopicha-threads`・`masaru-threads`）が入っています。** `thth board` はそれを全部並べます。**自分の台帳を足す前に、使わないものは消してください**（**L1**・実測）。

```json
{
  "account": "demo-threads",
  "project": "demo",
  "media": "threads",
  "handle": "demo",
  "user_id": "",
  "repo_dir": "$THTH_ROOT/repos/demo",
  "queue_dir": "docs/sns/queue",
  "replies_dir": "data/sns/replies",
  "quiet_hours": ["22:00", "07:00"],
  "min_interval_hours": 6,
  "collect_days": 14,
  "hashtags": false,
  "stale_days": 7,
  "env": "~/.config/thth/demo-threads.env",
  "token": "~/.config/thth/demo-threads.token",
  "ping": "wrapper",
  "timeout": 300,
  "dry_run_env": "THTH_DRY_RUN",
  "production": false
}
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| **必須は 18 項目**（上の全部から `user_id` を除いたもの）。1 つでも欠けると `台帳に項目が足りません: [...]` で止まる | `thth/accounts.py` `REQUIRED_FIELDS` | **L1** |
| `$THTH_ROOT` と `~` を展開するのは **`repo_dir`・`env`・`token` の 3 つだけ** | `thth/accounts.py` `_expand()` | **L1** |
| **`production: true` を commit しない限り dry-run。** 出力の 1 行目が `mode: rehearsal` になる | 設計 §4.2 | **L1**（実測） |
| `account` は `<project>-<media>`。`project` は clone の dir 名と board の見出し | 設計 §4.2 | **L2**（設計の決め） |
| **`thth auth` を使うなら `redirect_uri` の欄が要ります。** 設計 §4.2 の例には**載っていません**。無いと `redirect_uri が accounts/<account>.json に無い` で rc=2 | `thth/oauth.py` `run_auth()` | **L1**（実測） |
| `scopes` の欄を書けば既定 scope より優先される（任意） | `thth/oauth.py`・`thth/scopes.py` | **L1（コード読解・テスト無し）** |
| `env`（`HEALTHCHECK_URL` 等）は**任意**。無くても `thth run` は止まらない | `thth/accounts.py` `token_exists()` の docstring | **L1** |

### アカウントを 1 本足す 6 手順（設計 §4.2）

1. `accounts/<account>.json` を書いて commit
2. その Threads アカウントを Meta アプリの tester に招待・承諾（§2-5・§2-6）
3. トークンを入れる（§5）
4. `~/.config/thth/<account>.env`（`HEALTHCHECK_URL` だけ。**app ID と secret は共通の `app.env` にあるので触らない**）
5. `systemctl enable --now thth@<account>.timer`（§6）
6. 死活監視の check を 1 つ

**1 以外は値に触るので、人の手です**（設計 §4.2・**L2**＝設計の決め）。

---

## 5. トークン（`thth auth`・`thth token set`・60 日更新）

**トークンはアカウントごとに 1 本、`~/.config/thth/<account>.token` に 600 で置きます。repo には入れません**（設計 §3.1・**L1**）。

### 道は 2 つ

**(a) 管理画面の「ユーザートークン生成ツール」＋ `thth token set`** —— 管理画面の「ユースケース → Threads API → 設定」の下にあり、tester ごとに長期トークンをボタン 1 つで発行できます（設計 §4.2・**L3**）。**OAuth の往復も app secret も要りません。**

> **落とし穴**（設計 §4.2・**L3**）: 認可の画面は「押した行のアカウント」ではなく**「そのブラウザで Threads にログインしているアカウント」を認可します**。**アカウントごとにプライベートウィンドウを開き、そのアカウントで threads.net にログインしてから生成する**のが確実です。

```bash
thth token set demo-threads          # 貼り付けを求められる。値は画面に出ない
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| 保存する前に `me` を叩いて実在を確かめ、台帳の `handle` と食い違うトークンは保存しない。**`--force` でも保存しない、という部分は、テストが `--force` を渡した食い違いまでは確かめていない** | `thth/oauth.py` `run_token_set()` | **L1**（`--force` でも、の部分は**コード読解のみ・テスト無し**） |
| 既に `.token` があると rc=1 で止まる（入れ替えは `--force`） | 同上 | **L1** |
| 管理画面発行では発行時刻も scope も分からないので、`obtained_at` はコマンドを打った時刻、`scopes` は `null`（嘘の一覧を書かない） | 同上 | **L1** |

**(b) `thth auth`** —— OAuth の往復をこちらでやる道です。tester 以外を扱う日のために残してあります。

```bash
thth auth demo-threads
```

順番は **`accounts/<account>.json` → `app.env` → `redirect_uri` → 認可 URL の表示 → code の貼り付け → 短期→長期の交換 → `.token`（600）** です（**L1**・§7 の乾式試験が `app.env` を読むところまで毎回通しています）。

**既定で要求する scope は 11 個**（`thth/scopes.py` `DEFAULT_SCOPES`・**L1**）。裁定は「例外なく全部」（設計 §8-14）ですが、**tester に実際に降りるのは §2 の 5 つだけ**（**L3**）。

### 60 日と更新

| 事実 | 出典 | 証拠 |
|---|---|---|
| 長期トークンの寿命は **60 日**。`GET /refresh_access_token?grant_type=th_refresh_token` で更新でき、**更新に app secret は要らない**。**24 時間以上経過・未失効**が条件で、更新後は更新日から 60 日 | 設計 §2.2 | **L2** |
| **更新は投稿から切り離してあります。** `thth maintain` が 1 日 1 回、**全アカウント**を見て 50 日超で更新する。timer を持たないアカウント・`inflight` で詰まっているアカウントでもトークンだけは死なない | 設計 §3.2・`thth/maintain.py` | **L1** |
| **期限切れは更新を試みません**（Meta が受け付けない）。取り直しになる | 設計 §3.2 | **L2** |
| `thth board` に `token_state`・`token_remaining_days` が出る。トークンが無ければ `token=no_token` | 設計 §3.2 | **L1**（実測） |

---

## 6. timer（`thth systemd` の出力を使う）

**unit を手で書かないでください。** 手で書くと台帳と刻みがずれます（実際に旧 unit は毎時になっていて、設計の 10 分刻みと食い違っていました。`thth/systemd_gen.py` の冒頭）。

```bash
thth systemd demo-threads          > /etc/systemd/system/thth@demo-threads.timer
thth systemd --maintain            > /etc/systemd/system/thth-maintain.timer
thth systemd --maintain --service  > /etc/systemd/system/thth-maintain.service
systemctl daemon-reload
systemctl enable --now thth@demo-threads.timer
systemctl enable --now thth-maintain.timer
```

実際に出てくるもの（**L1**・実測）:

```ini
[Timer]
OnCalendar=*:7/10
Persistent=true
RandomizedDelaySec=0
Unit=thth@demo-threads.service
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| 刻みは台帳の `tick_minutes`（既定 10）。**timer の刻みがそのまま遅れの上限**になる（`publish_at` は「この時刻以降の最初の実行で出る」の意味） | 設計 §3.2 | **L1**（既定 10 分・生成した unit で確認） |
| 起点（上の `7`）は**アカウント名の sha256 から決まる**ので、アカウントが増えても勝手にずれる。`PYTHONHASHSEED` に依らず毎回同じ | `thth/systemd_gen.py` `offset_minutes()` | **L1** |
| **静かな時間帯（`quiet_hours`）は unit に出しません。** timer は終日走らせ、コード側の `select` だけが見る（真実の置き場を 1 つにする） | 設計 §3.2 | **L1** |
| `thth-maintain` は **1 日 1 回 04:17**・アカウント別ではない。`Persistent=true` なので VM が落ちていた日も起動後に 1 回走る | `thth/systemd_gen.py` | **L1** |
| **投稿の timer と maintain の timer は別**（投稿が止まっていてもトークンの保守は走る） | 設計 §3.2・§9 | **L1** |
| repo 同梱の `systemd/thth@.service` は **`User=wt`・`THTH_ROOT=/srv/thth`・`/srv/thth/app/bin/thth-run` 決め打ち**。別の置き場なら書き換えが要ります | `systemd/thth@.service` | **L1** |
| `systemctl enable --now` が実際に通ること | 設計 §9 | **L3** |

---

## 7. 確かめ方（`thth doctor` → `thth lint` → `thth board` → dry-run）

**この 4 つは `tests/test_fresh_install.py` が毎回同じ順番で通しています**（まっさらな clone・偽の `app.env`・偽の台帳・トークンなし・`production: false`）。**だから全部 L1 です。**

| この文書 | 確かめている試験 |
|---|---|
| §7-1 doctor が rc=2 で止まる | `test_step_1_doctor_stops_because_there_is_no_token` |
| §7-2 lint が `OK`（rc=0） | `test_step_2_lint_passes_on_the_queue` |
| §7-2 空ディレクトリは rc=1 | `test_step_2b_lint_refuses_an_empty_directory` |
| §7-3 board に `token=no_token` | `test_step_3_board_shows_the_account_without_a_token` |
| §7-4 dry-run が `mode: rehearsal` | `test_step_4_dry_run_is_rehearsal_and_posts_nothing` |
| §3・§5 `app.env` を使うのは `auth` だけ（`doctor` は有無だけ見る） | `test_auth_reads_app_env_and_stops_at_the_code_prompt` / `test_auth_stops_loudly_when_app_env_is_missing` / `tests/test_doctor_next_step.py` |
| §6 unit は台帳から生成される | `test_systemd_unit_is_generated_from_the_ledger` |
| §8 board が「追いついています」と言わない | `test_board_does_not_claim_to_be_up_to_date_without_a_release_check` |

### 7-1. `thth doctor <account>` —— トークンが無いことを、無いと言う

```
$ thth doctor demo-threads
次の一手: 導入文書 §5 トークン を見てください。

トークンが無い（thth token set を先に）
rc = 2
```

**これが期待どおりの停止です。** `app.env`（§3）や台帳（§4）が無ければ、その節を指す行が先に出ます。 トークンを入れる前にここで止まるのが正しい。トークンを入れたあとに走らせると、そのトークンで**実際に何ができるか**を読み取りだけで測ります（投稿・返信・削除は呼びません）。

### 7-2. `thth lint <queue ディレクトリ>` —— front-matter の形

```
$ thth lint /srv/thth/repos/demo/docs/sns/queue
OK
rc = 0
```

**空のディレクトリは rc=1 です**（`検査できるものがありません（対象 0 件です）` が stderr に出ます）。**「検査できるものが無い」を成功にしない**——これも期待どおりの停止です（**L1**・実測）。

### 7-3. `thth board` —— 1 画面で全部

```
$ thth board
道具: <head>  配布の枝: `release`
  **配布の枝（`release`）の取得試行の記録を確認できません**

demo-threads: project=demo last_post=(なし) approved_waiting=0 type_mismatch=0 inflight=(なし) token=no_token
rc = 0
```

`token=no_token` が「トークンをまだ入れていない」、2 行目が「配布の枝をまだ一度も取りに行っていない」の意味です（§8）。

### 7-4. dry-run —— `thth throw <account> --now`

```
$ thth throw demo-threads --now
mode: rehearsal
出すものが無い
  いま出ない: 2026-09-12-hello.md — not_approved
rc = 0
```

**1 行目の `mode: rehearsal` を必ず見てください。** 台帳の `production` が `true` でない限りここは `rehearsal` で、**本物の API は叩きません**（設計 §0・§3.2 の fail-closed）。

途中で止まる形も覚えておいてください（どれも**投稿せずに** rc=2・**L1**・実測）:

- `利用者 repo の同期に失敗しました（投稿しません）: git repository として確認できませんでした` —— `repo_dir` が clone になっていない
- `利用者 repo の同期に失敗しました（投稿しません）: origin という remote が見つかりません` —— clone に origin が無い

---

## 8. 配布の枝（`release` を追う仕組み・自分で fork した場合の注意）

**`thth run` は仕事の前に自分自身を更新します。** ただし引くのは **`main` ではなく `release`** です（masaru 裁定 2026-09-12・設計 §3.2.1）。

**`main` への push は開発の保存・共有で、本番には降りません。配布は `release` を進める操作です。**

| 事実 | 出典 | 証拠 |
|---|---|---|
| `git fetch origin +refs/heads/release:refs/remotes/origin/release` → `git merge --ff-only origin/release`。**checkout している枝の上流を見ない** | 設計 §3.2.1・`thth/selfupdate.py` | **L1** |
| **refspec を明示する。** `--single-branch` / `--depth` 付きの clone だと `git fetch origin release` が **rc=0 で成功したまま `origin/release` を作らない** | 同上 `_refspec()`・`tests/test_selfupdate.py::test_単一枝のcloneでも配布が届く` | **L1**（単一枝 clone の実挙動もテストが再現して確かめている） |
| 枝の名前は環境変数 **`THTH_RELEASE_REF`** で変えられる（既定 `release`） | `thth/selfupdate.py` `RELEASE_REF` | **L1** |
| **`behind_release` の `None` は「遅れていない」ではなく「判らない」。** 0 と混ぜない | 設計 §3.2.1 | **L1** |
| **`behind == 0` は「配ったもので動いている」ではない。** HEAD が配布の枝より**先**にいると 0 に見えるので、`ahead_of_release` を別に数え、board では遅れより先に出す | 設計 §3.2.1 | **L1** |
| **board は取りに行かない。だから「いま」を言わない。** 保存済みの記録から言えるのは「その時刻に確認した参照との比較」まで | 設計 §3.2.1 | **L1**（実測: 記録が無い clone で `取得試行の記録を確認できません` が出た） |

### fork した場合の注意

1. **`release` 枝を自分で作ってください。** 無いと board は永久に「確認できません」のままで、`thth run` は自分を更新しません（止まりはしません）。
2. **`main` に push しても本番は変わりません。** 検収した commit を `release` へ進めたときだけ変わります。**これは仕様です。**
3. **上流（`aokings/thth`）の `release` をそのまま追わないでください。** 上流の `release` は masaru の運用の判断です。fork したら、自分の origin の `release` を自分で進めてください。
4. **`main` の先端を無条件に取り込む形に戻さないでください。** それが「push が本番反映と同義」だった元の姿で、2026-09-11 の 1 日に 20 回以上の push が本番へ降りていました（設計 §3.2.1・**L2**＝設計の記録）。

---

## 証拠段階のまとめ

| 節 | L1 | L2 | L3 |
|---|---|---|---|
| §1 前提 | 4 | 1 | 1 |
| §2 Meta アプリ | 0 | 5 | 7 |
| §3 app.env | 4 | 0 | 0 |
| §4 台帳 | 8 | 2 | 0 |
| §5 トークン | 8 | 2 | 3 |
| §6 timer | 7 | 0 | 1 |
| §7 確かめ方 | 2 | 0 | 0 |
| §8 配布の枝 | 6 | 1 | 1 |
| **計 63** | **39** | **11** | **13** |

**§7 の 2 件は、行ごとの印ではなく「この 4 つは `tests/test_fresh_install.py` が毎回同じ順番で通している」という一括宣言から数えたものです。** 個別に印を付けている他の節と数え方が違う点に注意してください。

**L3 は 13 / 63 ＝ 21%。** 設計 §6 の止まる条件（「導入文書に L3 しか無い手順が半分を超えたら止まる」）には当たっていません。

**ただし L3 は §2 に固まっています。** Meta の管理画面まわりは 11 手順のうち 7 つが L3 です。**この文書で導入して詰まるとしたら、ほぼ確実に §2 です。** 詰まったら §2 に足してください。
