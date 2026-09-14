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
| 2-7 | **ユースケース → Threads → 設定** の **「Threads アプリ ID」「Threads の app secret」**を控える。**「アプリの設定 → ベーシック」に出る Meta アプリ ID とは別物**です。取り違えると認可画面まで行かず、Meta が `error_code 4476002`（アプリ ID が送信されませんでした）を返します | 設計 §2.2 | **L1**（2026-09-15 に取り違えて実際にこのエラーを踏んだ） |
| 2-8 | `thth auth` を使うなら、同じ **設定** 画面の**コールバック URL を 3 つとも埋めて保存**する。**1 つだけ入れて保存すると「フォームを保存できません」で弾かれます**（リダイレクトだけ入れて踏んだ）。登録しないまま認可すると `error_code 1349168`（リダイレクト URI が未登録） | 設計 §9-2 | **L1**（2026-09-15・両方のエラーを実際に踏んだ） |

### コールバック URL は 3 つセット（2026-09-15 追記）

masaru の値（`thth.me` は THTH 専用の Cloudflare Worker）:

| 欄 | 値 |
|---|---|
| コールバック URL をリダイレクト | `https://thth.me/callback/` |
| コールバック URL をアンインストール | `https://thth.me/deauthorize` |
| コールバック URL を削除 | `https://thth.me/data-deletion` |

後ろの 2 本は Meta が要求するだけで、Worker は 200 と定型の JSON を返すだけです
（`callback/src/index.js`・**L1**）。**台帳の `redirect_uri` は、ここに登録した値と
1 文字違わず同じにしてください**——`https://thth.me/` と `https://thth.me/callback/`
は別物として扱われます。

### 権限は 11 個とも降りてくる（2026-09-15 に訂正）

**以前ここには「tester の認可画面に出るのは 5 つだけ」と書いてありました。誤りです。**
2026-09-15 に `thth auth` で 3 アカウントを認可し直したところ、**認可画面に 11 個すべて**が
並び、承認後の `/debug_token` も 11 個を返しました（**L1**・`thth doctor` の出力）。

出る 11 個: `threads_basic`・`threads_content_publish`・`threads_manage_replies`・
`threads_manage_insights`・`threads_read_replies`・`threads_manage_mentions`・
`threads_keyword_search`・`threads_delete`・`threads_location_tagging`・
`threads_profile_discovery`・`threads_share_to_instagram`

古い記述が生まれた事情: 最初の 4 アカウントは 5 権限しか足していない時期に認可されており、
その状態が長く続いていたため「5 つしか降りてこない」と読み違えていました。

### 権限を増やしたら、`thth auth` で認可し直す（`thth token set` では変わらない）

**管理画面の「ユーザートークン生成ツール」は、そのアカウントが過去に承認した範囲でしか
トークンを出しません。** 11 個に増やしたあとで生成ツールを押しても、**5 権限のトークンが
出てきます**（2026-09-15 に kopi_chaba で実測・**L1**）。生成ツールの行には「取り消す」も
権限の選択も無いので、**権限を増やす／減らすときは `thth auth`（OAuth の往復）を通すしか
ありません**——`thth auth` は要求する scope を認可 URL に載せるので、承認画面がその一覧で出ます。

| やりたいこと | 使う口 |
|---|---|
| 期限が切れかけたトークンを入れ替える（権限は同じ） | 生成ツール ＋ `thth token set <account> --force` |
| **権限の内訳を変える** | **`thth auth <account>`**（§2-8 のコールバック URL 登録が要る） |

`.token` の `scopes_source` で、どちらで入れたかが後から分かります
（`response`＝`thth auth` が `/debug_token` に訊いた・`unknown`＝生成ツール発行で判らない）。

**「Threads API はビジネス認証済みアカウントが要る」という第三者の記述があります**（設計 §8-8・**L3**・公式で裏が取れていない）。開発モード＋tester なら不要のはずですが、アプリを作る場面で分かるので、違っていたらこの文書に足してください。

---

## 3. `~/.config/thth/app.env` —— **`thth auth` を使うときだけ**

### この節を飛ばしてよい場合

**管理画面の「ユーザートークン生成ツール」で発行したトークンを `thth token set`（§5）で入れる運用なら、`app.env` は要りません。この節を飛ばして §4 へ進んでください**（設計 §4.2「置かないものは漏れない」）。

`app.env`（`THREADS_APP_ID`・`THREADS_APP_SECRET`）を読むのは **`thth auth`（認可コードから長期トークンを取る道）だけ**です。**トークンの延長も読みません**——`thth refresh` / `thth maintain` が叩く `refresh_access_token` は app secret を使いません（`thth/oauth.py`・**L1**）。masaru の本番 4 アカウントは `app.env` を置かずに動いています（2026-09-13・**L3**＝masaru の VM でしか確かめていない）。

### 置くとき —— 手で書かずに `thth app set`

**全アカウント共通の 1 本です。** アカウントが増えても書き足しません（設計 §3.1・§9-1b）。

```bash
$ thth app set --app-id <Threads app ID>
Threads の App Secret を貼り付けてください（表示されません）:
app.env を書きました: /home/wt/.config/thth/app.env（600）
```

**App Secret は `getpass` で受け取ります——打っても画面に出ません。** 書いたあとに言うのは path と `600` だけで、**値は標準出力にも標準エラーにも出ません**。

非対話（スクリプト・tty を割り当てない `ssh`）は `--secret-stdin` で標準入力から 1 行:

```bash
printf '%s\n' "$APP_SECRET" | thth app set --app-id <Threads app ID> --secret-stdin
```

**`--secret-stdin` を付けずに端末以外から呼ぶと、読まずに止まります**（tty が無いとエコーを止められず、手元の画面に secret がそのまま出てしまうため。`thth token set` と同じ作法）。

置いたものを確かめる（**値は出ません**）:

```
$ thth app show
app.env: /home/wt/.config/thth/app.env
  あり
  項目: THREADS_APP_ID・THREADS_APP_SECRET
  パーミッション: 600
  値は表示しません。
rc = 0
```

```
$ thth app show --json
{"path": "/home/wt/.config/thth/app.env", "exists": true, "keys_present": ["THREADS_APP_ID", "THREADS_APP_SECRET"], "mode_ok": true}
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| 既定のパスは `~/.config/thth/app.env`。**環境変数 `THTH_APP_ENV_PATH` で差し替えられる**（テストの隔離用） | `thth/appenv.py` `default_path()` | **L1** |
| 鍵の名前は `THREADS_APP_ID` と `THREADS_APP_SECRET` の 2 つだけ。どちらかが空なら `app.env に項目が足りません` | `thth/appenv.py` `REQUIRED_KEYS` | **L1** |
| 読むときに 600 でなければ**警告して直します**（`警告: … のパーミッションが … です。600 に直します。`） | `thth/secrets_fs.py` `ensure_mode_600()` | **L1** |
| `thth app set` は**一時ファイル ＋ `os.replace` で原子的に**書き、**600** にします。ディレクトリが無ければ **700** で作ります。上書きしても 600 に戻します | 実測（隔離した乾式試験・`tests/test_app_env_cli.py`） | **L1** |
| **App Secret は stdout にも stderr にも出ません。** 成功時の出力は `app.env を書きました: <path>（600）` の 1 行だけ | 実測（隔離した乾式試験・`tests/test_app_env_cli.py`） | **L1** |
| 空の `--app-id`・空の App Secret は **rc=2 で断り、ファイルを作りません**（既存があれば壊しません） | 実測（隔離した乾式試験・`tests/test_app_env_cli.py`） | **L1** |
| `thth app show` は**有無・鍵の名前・パーミッションだけ**を出します。**値は出しません**し、パーミッションも直しません（読むだけ）。`--json` は `{"path", "exists", "keys_present", "mode_ok"}` | 実測（隔離した乾式試験・`tests/test_app_env_cli.py`） | **L1** |
| **`app.env` を使うのは `thth auth` だけです。** `lint`・`board`・`throw`・`refresh` は読みません。`doctor` は**存在と、2 つの項目が空でないことだけ**を見ます（値は出力しません）。**無いときは「任意」と言うだけで §3 は指しません。置いたのに項目が空・読めないときだけ `次の一手: 導入文書 §3` になります** | 実測（隔離した乾式試験・`tests/test_doctor_next_step.py`） | **L1** |
| **`app set` / `app show` は MCP に出しません**（秘密は人の手のまま・設計 §3.7。`auth`・`refresh`・`token set` と同じ扱い） | `mcp/server.py` `TOOLS`・`tests/test_mcp.py` | **L1** |

**値をこの repo に書かないでください。** 台帳（`accounts/*.json`）にも、docs にも、commit message にも。`thth app set` を使えば、値が shell の履歴にも残りません（`--app-id` は秘密ではありません）。

---

## 4. アカウント台帳 `$THTH_ROOT/accounts/<account>.json`

**台帳は repo の外に置きます**（設計 v2 §3「台帳を repo の外へ」・masaru 裁定 2026-09-13「出す」）。**正は `$THTH_ROOT/accounts/`**。秘密は入りませんが、repo に commit すると「配る道具」と「その人の顔ぶれ」が同じ履歴に混ざり、clone した人に他人の台帳が付いて来ます。**開発側の 6 本は 2026-09-14 に repo から消しました**（§4-2 の末尾）。

### 置き場の決まり方（`thth/accounts.py` `accounts_dir_info()`・**L1**）

上から順に見て、**最初に見つかったところ**を使います。

| 順 | 置き場 | いつ効くか |
|---|---|---|
| (a) | 環境変数 `THTH_ACCOUNTS_DIR` | 明示したとき（テスト・特殊な配置） |
| (b) | **`$THTH_ROOT/accounts/`** ← **正** | **ディレクトリがあれば**。中が 0 本でもここが正 |
| (c) | app repo の `accounts/` | **(b) が無いときだけ**。**互換・1 版かぎり**。新しい clone には `accounts/` が無いので効きません（2026-09-14 に削除）——効くのは、まだ移行していない既存の機械だけ |

**(b) は「ディレクトリがあるか」だけで見ます**（中に台帳があるかは見ません）。`thth account add` を 1 本打った時点で外が正になり、**repo の中の台帳は二度と読まれません**。「外に足したのに repo の分も混ざって並ぶ」を作らないためです。

**(c) に落ちているときは、`thth doctor` と `thth board` が 1 行目あたりでそう言います**（`台帳の置き場: … （**repo の中・互換**。…）`）。加えて標準エラーへ警告が 1 行出ます。**この互換は次の版で外します**——見かけたら §4-2 の移行を済ませてください。

### アカウントを 1 本足す（`thth account add`）

```bash
# Threads（`--redirect-uri` は §2 で Meta アプリに登録した戻り先）
thth account add demo-threads --media threads --project demo \
  --redirect-uri https://thth.me/callback/
# 媒体で足す欄が違う（**handle は必須**。既定では当たりません）
thth account add demo-mastodon --media mastodon --project demo \
  --handle user --instance https://mastodon.social
thth account add demo-bluesky  --media bluesky  --project demo \
  --handle name.bsky.social
```

`accounts.example/<media>.json` の雛形から `$THTH_ROOT/accounts/<name>.json` を書きます。

**媒体ごとに要る欄**（2026-09-13・監査 2 の C10 で必須化。**既定で当たらない欄は、黙って埋めずに聞きます**）:

| 媒体 | `--handle` | `--instance` | `--redirect-uri` |
|---|---|---|---|
| threads | **任意**（既定は `--project` の値。Threads の handle は利用者名そのものなので、だいたい当たります） | — | **省略可。ただし省くと雛形のダミー `https://example.invalid/` のまま**で、`thth auth` が rc=2 で断ります |
| bluesky | **必須**（`name.bsky.social` のドメイン形。`--project` の値は当たりません） | 任意（`service` 欄。既定 `https://bsky.social`） | — |
| mastodon | **必須**（`@` を除いた利用者名） | **必須**（インスタンスごとに口が違うので推測できません） | — |

**`--redirect-uri` を省いたときは、`add` が書いたその場で 1 行言います**（**L1**・試験 `test_addはredirect_uriを省いたら次の一手で1行言う`）:

```
**redirect_uri はダミーのままです**（https://example.invalid/）。**`thth auth` の前に。** Meta アプリに登録した URL を `thth account add <name> --redirect-uri <url>` か、台帳の `redirect_uri` に入れてください（導入文書 §4）。
```

**この 1 行が無かったころは、`add` と `thth auth` の間に「どこにも書かれていない手作業」が挟まっていました**（`thth doctor` も黙っていた）。いまは **`add` → `doctor` → `auth`** の 3 か所すべてがダミーを名指しします。

**clone したばかりなら、そのまま通ります**（2026-09-14 から。repo に台帳が 1 本も入っていないので、互換 (c) に落ちません）。`--force` は要りません。

**`--force` が要るのは、互換 (c) で動いている既存の機械だけです。** 読みが repo の `accounts/` に落ちている状態で `add` を打つと、`$THTH_ROOT/accounts/` が出来た瞬間に**その台帳は以後読まれなくなる**ので、道具は何が起きるかを言って 1 度止まります（rc=1）。そこで `--force` を付けて進むのは、**repo の中にあるのが他人の台帳だと判っているとき**だけにしてください：

```bash
thth account add demo-threads --media threads --project demo --force
```

（**すでに自分の台帳で動いている機械**なら、`--force` ではなく §4-2 の `thth account migrate` が先です。順番を間違えると次の実行で「台帳が無い」になります。）

| 事実 | 出典 | 証拠 |
|---|---|---|
| 書く先は**必ず外**。読みが (c) の互換に落ちていても、**repo の中には書きません** | `thth/account_cli.py` `target_accounts_dir()` | **L1**（試験 `test_addは互換のときでもrepoの中に書かない`） |
| **`production: false`・`scheduled: false` で生まれます。** 雛形が万一 true でもここで落とします | `thth/account_cli.py` `build_ledger()` | **L1**（試験・実測で `mode: rehearsal`） |
| **既にあるものは上書きしません**（rc=1 で断る） | 同上 `cmd_add()` | **L1**（試験） |
| **Threads の** `--handle` の既定は **`--project` の値**（アカウント名ではない） | 同上 | **L1**（試験） |
| **Bluesky・Mastodon は `--handle` が必須**（Mastodon は `--instance` も）。省くと例つきで rc=2 | 同上 | **L1**（試験 `test_addはblueskyのhandleを必須にする`・`…mastodonのhandleとinstanceを必須にする`） |
| Mastodon は `instance`、Bluesky は `service` の欄に入ります | 同上 | **L1**（試験） |
| `--redirect-uri` は **threads のときだけ**。他媒体に付けると rc=2（黙って捨てない） | 同上 | **L1**（試験） |

足した後の続き（**値に触るので人の手**・設計 §4.2）:

1. `thth doctor <account>` —— **雛形のダミーが残っていれば名指しで言います**（`redirect_uri: https://example.invalid/`・Mastodon の `instance: https://mastodon.example`・handle が `demo` のまま）。ここで直してから先へ進みます（§7-1）
2. その Threads アカウントを Meta アプリの tester に招待・承諾（§2-5・§2-6）
3. トークンを入れる（§5）
4. `~/.config/thth/<account>.env`（`HEALTHCHECK_URL` だけ。**app ID と secret は共通の `app.env` にあるので触らない**）
5. 本番にするときだけ、台帳の `production` を手で `true` に
6. `systemctl enable --now thth@<account>.timer`（§6）
7. 死活監視の check を 1 つ

### 4-2. すでに動いている機械の移行（`thth account migrate`）

**すでに repo の `accounts/` で動いている機械**（VM・2026-09-13 時点の `/srv/thth/app`）は、下の 4 手で外へ移します。**運用セッションの手順は（運用日誌 thth-notes: `記録/引継ぎ_運用セッション_2026-09-13.md`）末尾にそのまま貼れる形であります。**

```bash
thth account migrate --dry-run   # 何も書かない。写す顔ぶれを見るだけ
thth account migrate             # repo の accounts/*.json を $THTH_ROOT/accounts/ へ copy
thth board                       # 6 本が変わらず見えること
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| **copy であって移動ではありません。repo は触りません** | `thth/account_cli.py` `cmd_migrate()` | **L1**（試験） |
| **冪等。** 2 回目からは「写すものはありませんでした」 | 同上 | **L1**（試験） |
| 外に**中身の違うもの**があれば、**名指しで断って rc=1**（上書きしない） | 同上 `plan_migration()` | **L1**（試験） |
| **`--dry-run` は `$THTH_ROOT/accounts/` を作りません** | 同上 | **L1**（試験） |

**なぜ copy か**: VM の `/srv/thth/app` は `merge --ff-only origin/release` で更新する clone です。道具がそこの作業ツリーを動かすと、次の自己更新が止まります。

**`repo の accounts/` は 2026-09-14 に消しました**（設計 v2 §8 の順番どおり: VM の移行を確かめ、board の互換警告が消え、次の `thth run` が通ってから、人の手で `git rm`）。**互換 (c) はコードには 1 版だけ残してあります**——(b) を失った機械を止めないためで、消すのは次の版です。

### clone した直後（新しく導入する人）

**clone に台帳は 1 本も入っていません**（2026-09-14 から。入っているのは雛形 `accounts.example/` の 3 本だけです）。`thth board` を打つと「台帳の置き場: `$THTH_ROOT/accounts`」の 1 行が出て、台帳 0 本です。

**`thth account add` を 1 本打てば、そこから始まります**（`--force` は要りません・**L1**・乾式試験 `test_v2_2a_clone_は台帳0本_addは断られずに通る`）。

### 4-3. 台帳の中身

`thth account add` が書くのはこの形です（`accounts.example/threads.json` と同じ）。手で直すときの表も兼ねます。

```json
{
  "account": "demo-threads",
  "project": "demo",
  "media": "threads",
  "handle": "demo",
  "user_id": "",
  "redirect_uri": "https://example.invalid/",
  "repo_dir": "$THTH_ROOT/repos/demo",
  "queue_dir": "docs/sns/queue",
  "replies_dir": "data/sns/replies",
  "quiet_hours": ["22:00", "07:00"],
  "min_interval_hours": 6,
  "max_per_run": 1,
  "tick_minutes": 10,
  "collect_days": 14,
  "hashtags": false,
  "stale_days": 7,
  "env": "~/.config/thth/demo-threads.env",
  "token": "~/.config/thth/demo-threads.token",
  "ping": "wrapper",
  "timeout": 300,
  "dry_run_env": "THTH_DRY_RUN",
  "production": false,
  "scheduled": false
}
```

| 事実 | 出典 | 証拠 |
|---|---|---|
| **必須は 18 項目。** 上の例のうち `user_id`・`redirect_uri`・`max_per_run`・`tick_minutes`・`scheduled` は任意で、残り 18 が必須。1 つでも欠けると `台帳に項目が足りません: [...]` で止まる | `thth/accounts.py` `REQUIRED_FIELDS` | **L1** |
| `$THTH_ROOT` と `~` を展開するのは **`repo_dir`・`env`・`token` の 3 つだけ** | `thth/accounts.py` `_expand()` | **L1** |
| **`production` を自分で `true` にしない限り dry-run。** 出力の 1 行目が `mode: rehearsal` になる（`thth account add` が作るものは必ず `false`） | 設計 §4.2・設計 v2 §3 | **L1**（実測） |
| `account` は `<project>-<media>`。`project` は clone の dir 名と board の見出し | 設計 §4.2 | **L2**（設計の決め） |
| **`thth auth` を使うなら `redirect_uri` の欄が要ります。** 設計 §4.2 の例には**載っていません**。無いと `redirect_uri が accounts/<account>.json に無い` で rc=2 | `thth/oauth.py` `run_auth()` | **L1**（実測） |
| **雛形のダミー（`https://example.invalid/`）のままだと、`thth auth` は認可 URL を出す前に rc=2 で断ります。** 前はその URL をそのまま表示していたので、ブラウザで開いて Meta 側のエラーで初めて詰まりました | 同上 | **L1**（試験 `test_C10_ダミーのredirect_uriでは認可URLを出さない`） |
| `scopes` の欄を書けば既定 scope より優先される（任意） | `thth/oauth.py`・`thth/scopes.py` | **L1（コード読解・テスト無し）** |
| `env`（`HEALTHCHECK_URL` 等）は**任意**。無くても `thth run` は止まらない | `thth/accounts.py` `token_exists()` の docstring | **L1** |

**1 本足す手順は上の「アカウントを 1 本足す」に移りました**（`thth account add` が 1 手目を引き受けます。2 手目から先は値に触るので、今までどおり人の手です・設計 §4.2・**L2**＝設計の決め）。

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

順番は **`accounts/<account>.json` → `app.env` → `redirect_uri` → 認可 URL の表示 → **戻り URL 全体**の貼り付け → 短期→長期の交換 → handle の照合 → `.token`（600）** です（**L1**・§7 の乾式試験が `app.env` を読むところまで毎回通しています）。

**貼るのは `code` の値ではなく、戻り URL 全体です**（セキュリティ監査 2026-09-14・P2-4・**L1**）。認可 URL には `state` が付いていて、戻りの `state` と照合します——`state` が無い戻り・食い違う戻りは rc=2 で受け付けません（この道具が出した URL の戻りだと確かめられないため）。**対話でない口から使うときは 2 段**になります: 1 回目（`thth auth <account>`）が URL を出し、2 回目に `thth auth <account> --code '<戻り URL 全体>'` で渡します。

**トークンが別のアカウントのものなら保存しません**（同上・`thth token set` と同じ守り）。台帳の `handle` と、トークンが実際に指しているアカウントが食い違えば rc=1 で止まります。

**既定で要求する scope は 11 個**（`thth/scopes.py` `DEFAULT_SCOPES`・**L1**）。裁定は「例外なく全部」（設計 §8-14）で、**11 個とも tester に降ります**（2026-09-15 に 3 アカウントで実測・**L1**・§2 の「権限は 11 個とも降りてくる」）。

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
| §3 `app.env` が無くても `doctor` は §3 を指さない（「任意」と言う）・壊れているときだけ指す | `test_doctorはapp_env無しでも節3を言わず任意だと言う` / `test_doctorは壊れたapp_envでは節3を言う` |
| §3 `thth app set` が 600 で原子的に書き、App Secret を出力に出さない | `tests/test_app_env_cli.py` |
| §6 unit は台帳から生成される | `test_systemd_unit_is_generated_from_the_ledger` |
| §8 board が「追いついています」と言わない | `test_board_does_not_claim_to_be_up_to_date_without_a_release_check` |

### 7-1. `thth doctor <account>` —— トークンが無いことを、無いと言う

```
$ thth doctor demo-threads
次の一手: 導入文書 §5 トークン を見てください。

トークンが無い（thth token set を先に）
rc = 2
```

**これが期待どおりの停止です。** 台帳（§4）が無ければ、その節を指す行が先に出ます。トークンを入れる前にここで止まるのが正しい。トークンを入れたあとに走らせると、そのトークンで**実際に何ができるか**を読み取りだけで測ります（投稿・返信・削除は呼びません）。

Threads は **11 権限すべてを 1 行ずつ**確かめます（`○` 通った・`×` 通らなかった／権限がトークンに乗っていない・`―` **読み取りでは確かめられません**）。`threads_delete`・`threads_share_to_instagram`・`threads_manage_replies` は削除・投稿（DELETE／POST）にしか使われず読み取りの口が無いので `―` になります——**× ではありません**し、rc も 1 にしません（5xx や接続断で「確かめられなかった」ときも同じ `―`）。
加えて `/debug_token`（自分のトークンを `access_token` と `input_token` の両方に渡す読み取り）で**トークンに乗っている権限の一覧**を訊き、取れたときはこの 3 行を「乗っています／乗っていません」に格上げします（取れなければ `―` のまま）。その一覧と `DEFAULT_SCOPES` の突き合わせは `debug_token の scope: 11 個（DEFAULT_SCOPES と一致）` の 1 行に出ます（不一致なら `足りない=…・余計=…`）。**doctor は `.token` を書き換えません**（読むだけの道具）。
先頭の `記録上の scope: 11 個（source=requested）` は `.token` に書かれた認可の範囲（`thth auth` が書く。`token set` は管理画面発行で判らないので `不明（source=unknown）`）で、下の probe（叩いて確かめた実力）とは別物です。

**`app.env` を置いていない場合は、こう出ます**（§3 は指しません——無いのは任意だからです）:

```
$ thth doctor demo-threads
app.env: 無し（任意。`thth auth` を使うときだけ要ります。管理画面で発行したトークンを `thth token set` で入れる運用なら不要。置くなら `thth app set`）
次の一手: 導入文書 §5 トークン を見てください。
`thth auth` を使うなら先に `thth app set`（導入文書 §3）。

トークンが無い（thth token set を先に）
rc = 2
```

**トークンを入れたあとは、`app.env` について言うのは 1 行目だけになります**（「次の一手」は消えます）。**`app.env` を置いたのに項目が空・読めないときだけ**、いままでどおり `次の一手: 導入文書 §3 app.env を見てください。` が出ます。**「無い」（任意）と「置いたのに使えない」（要修理）を分ける**のが 2026-09-13 の直しです——正しい状態を毎回「足りない」と言う道具は、本当に足りないときに読まれなくなります。

`--json` には `app_env` が `"absent"` / `"ok"` / `"broken"` で入ります（既存の鍵はそのまま・追加のみ）。

**雛形のダミーが残っていれば、doctor が名指しで言います**（2026-09-13・監査 2 の C10）:

```
$ thth doctor demo-threads
台帳の置き場: /srv/thth/root/accounts（$THTH_ROOT/accounts）
台帳の `redirect_uri` が**ダミーのままです**: https://example.invalid/
次の一手: Meta アプリに登録した URL を `thth account add <name> --redirect-uri <url>` か、台帳の `redirect_uri` に入れてください（導入文書 §4）。
台帳の `handle` が**ダミーのままです**: demo
次の一手: そのアカウントの本物の handle を台帳の `handle` に入れてください（Bluesky は `name.bsky.social`・Mastodon は `@` を除いた利用者名・導入文書 §4）。
```

見るのは 3 種類 —— `redirect_uri` が `https://example.invalid/`・Mastodon の `instance` が `https://mastodon.example`・handle が雛形のまま（threads / mastodon は `demo`、bluesky は `demo.bsky.social`）。`--json` には `dummy_fields: [{"field", "value", "next"}, …]` で入ります（既存の鍵はそのまま・追加のみ）。

**rc の決め方**（**L1**・試験 `test_C10_ダミーが残っていれば全部丸でも0で返さない`）: **台帳は読めているので 2 にはしません**（2 は「診断できなかった」——台帳が読めない・トークンが無い・トピックの棚が壊れている）。かといって **0（異常なし）でも返しません**——`thth auth` はそこで必ず止まるからです。probe が×のときと同じ **1**。壊れたトピックの棚を「異常なし」で返さないのと同じ筋です（独立監査 1・P1-1）。

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
| §3 app.env | 10 | 0 | 1 |
| §4 台帳 | 7 | 2 | 0 |
| §5 トークン | 8 | 2 | 3 |
| §6 timer | 7 | 0 | 1 |
| §7 確かめ方 | 2 | 0 | 0 |
| §8 配布の枝 | 6 | 1 | 0 |
| **計 68** | **44** | **11** | **13** |

**§7 の 2 件は、行ごとの印ではなく「この 4 つは `tests/test_fresh_install.py` が毎回同じ順番で通している」という一括宣言から数えたものです。** 個別に印を付けている他の節と数え方が違う点に注意してください。

**L3 は 13 / 68 ≒ 19%。** 設計 §6 の止まる条件（「導入文書に L3 しか無い手順が半分を超えたら止まる」）には当たっていません。

**ただし L3 は §2 に固まっています。** Meta の管理画面まわりは 11 手順のうち 7 つが L3 です。**この文書で導入して詰まるとしたら、ほぼ確実に §2 です。** 詰まったら §2 に足してください。
