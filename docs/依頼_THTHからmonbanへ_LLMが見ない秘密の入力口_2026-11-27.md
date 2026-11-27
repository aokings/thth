# 依頼: THTH → monban「LLM が中身を見ない、秘密の入力口」（2026-11-27）

書いた人: THTH括り 第2（名乗り SageFox）。宛先: monban の窓口（モンバーン 第16・IvoryPuma）。
決まったこと: masaru 11/27「やりましょう」。この口は **THTH でなく monban に持たせる**。monban が骨を持てば、THTH・hub・h2d・ほかの人の家が同じ口を使える。

## 1. 何がほしいか（一文）

人がブラウザの入力欄に秘密（パスワード・鍵・token）を入れると、その値が **LLM の会話にも、monban の Edge にも見えないまま** 家（VM）に届く。LLM には「受け取った・確かめた（か落ちた）・どの口座の何か」だけが返る。

masaru が個人で使っている別のアプリに、こういう入力口があった（11/27 の話）。THTH の決まり「秘密は LLM の文脈を通らない」（[切り出し候補_ヘッドレス道具の小品_2026-09-21.md](切り出し候補_ヘッドレス道具の小品_2026-09-21.md) の原則 2）を、OAuth 以外の秘密にも広げるための口。

## 2. 今の困りごと（THTH の側・コードを読んで確かめた）

OAuth の媒体（Threads・Mastodon・X）は預かり所（`thth.me/callback`）で済んでいる。それ以外の秘密は、**VM の端末に打つか、標準入力で渡すか**しかない。Claude の Bash は端末にならないので、毎回 masaru が ssh で打つ。LLM に渡すと、値が会話を通る。

| 秘密 | 今の入口（THTH） | 差し込める口 | 受け取ったあとの検め | 置き場 |
|---|---|---|---|---|
| アプリの client secret（Meta・Threads・X の App Secret） | `thth app set`（`cmd_app_set`・`thth/appenv.py`） | `--secret-stdin` | 形の検め | `~/.config/thth/app.env` |
| 口座の token を手で貼る | `thth token set`（`oauth.run_token_set`） | `--stdin` | 媒体に問い合わせて本人を照合 | 口座の token（0600） |
| Bluesky の App Password | `thth auth`（`adapters/bluesky.py` の `auth_interactive`） | `password_input=` | 実際にログインして確かめる | 口座の token |
| はてなの consumer key・secret と確認の文字列 | `thth principal auth`（`principal_cli.run_auth`・端末が必須） | `prompts=` | 署名つきの要求が通るか。確認の文字列は**認可の頁のあとに 2 回目の入力** | 主体の consumer・token |
| WordPress のアプリケーションパスワード | `thth principal auth`（`run_auth_wordpress`） | `prompts=`・`use_stdin` | サイトに問い合わせる | 主体の token |
| 店の token | `thth shop … token set`（`thth/shop/cli.py` の `_read_secret`） | `READ_SECRET`・`--stdin` | 店に読みに行って照合 | 店の置き場 |
| thth.me の鍵（Mac の `thth login`） | `thth/remote.py` の getpass | `--stdin` | — | **Mac** の `~/.config/thth/` |

THTH の入口は、どれも値を外から差し込める作りになっている（試験のための口）。なので THTH の側は「預かった値をこの口に渡す係」を足すだけで済む見込み。

## 3. 分け方（monban の設計 §3.1・§3.2 のまま）

- **monban が持つ骨**: 一回限りの URL（期限 5 分・1 回・どの家のどの「種類」か決まっている）／入力の頁／頁の中で家の公開鍵で包む暗号／包んだ箱の預かり（中身を見ない・記録しない）／家が署名つき sync で取りに来る道／「受け取った・確かめた・落ちた」を頼んだ側（LLM）へ返す口／値を残さない帳面（presence-only）。
- **THTH が渡す物（家の handlers 表）**: 種類の表。上の 7 行の「種類 → 差し込める口・検め・置き場」。値を受け取ったら THTH の今の関数に渡し、結果の符丁（`ok`・`invalid_credential` など）だけを返す。

## 4. 流れ（叩き台）

1. LLM が THTH の命令を打つ（例 `thth secret ask bluesky-app-password --account kopicha-bsky`）。THTH は家を通して monban に「この種類の入力口を 1 つ開けて」と頼み、URL を受け取って LLM に返す。
2. masaru（か、招待で入った外の人）がその URL を開き、入力欄に入れて送る。会話アプリが MCP の「URL で人に入力してもらう」仕組み（elicitation の URL の形）に合っていれば、会話の中から開ける。**この仕組みの今の決まりは、作る前に原文で確かめる（未確認）。**
3. 頁は、値を家の公開鍵で包んでから Edge に送る。Edge は箱を 5 分・1 回だけ持つ。
4. 家が sync で箱を取り、中を開け、THTH の係に渡す。THTH が検めて置く。
5. LLM には、頼んだ命令の答えとして「受け取った・確かめた（か落ちた）」だけが返る。

はてなのように途中で人の操作が挟まる物は、同じ URL の頁を 2 段にする（consumer の 2 つ → 認可の頁へ → 確認の文字列）か、2 つの URL に分けるかを決める必要がある。

## 5. 決めてほしい・確かめてほしい所（monban の側）

1. **家の暗号の鍵**: 今の家の鍵は署名の鍵（RSA-PSS）。箱を包むには暗号の鍵（別の鍵の組）が要ると読んだ。金庫の段で鍵の形を決めているなら、それに乗せるのがよいか。
2. **頁を出す場所**: monban の Edge（thth.me でない）。thth.me に頁を足さないので、Meta の録画（12/17 の予定）と審査員の試す道に触らない。これがこの形のいちばんの利点。
3. **wt の家**: VM（wt）には hub のための monban の家がもう動いている。THTH が monban に全部乗り換える前でも、その家に THTH の係を足して使えるか（11/25「段 2 の残りは THTH が入るまで待つ」との関係）。
4. **頼んだ側への返し方**: 結果を LLM に返す道（郵便受けの結果の口 `/result/<id>` と同じ形か）。
5. **Mac の鍵（表の最後の行）**: 受け取るのが家でなく Mac なので形が違う。monban の Client の login（ブラウザ式）に寄せられるか。

## 6. THTH の側でやること（monban の形が決まってから・THTH開発の領分）

- 種類の表と、預かった値を今の入口に渡す係。命令の形（`thth secret ask <種類>` など）。
- 置き場は、括り（THTH アカウント）が入ったら括りの `secrets/`（[設計_THTHアカウント（括り）_叩き台_2026-11-26.md](設計_THTHアカウント（括り）_叩き台_2026-11-26.md) の段 4 の準備・枝 `worktree-ws-stage-d` の 702cbeac）。頼みの URL か鍵に括りを持たせる。
- THTH の版に入れるのは THTH の release の決まりどおり（CI 緑・関門・masaru の「出して」）。

## 7. やらないこと

- 値を会話・記録・出力に出さない。LLM に「値を貼って」と頼む道を作らない。
- Edge で中身を開けない（開けられない形にする）。
- 入力の頁を thth.me に置かない。
