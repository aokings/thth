# 依頼: thth.me の文章と、途中に出るブラウザの画面の校正（2026-09-27）

宛先: media-hub（CobaltGrove・UX のレーン／Codex）。送り手: THTH開発（DustyCave）。masaru の言葉: 「唐突に masaru が出てきたりして、とても酷い」「途中に出てくるブラウザの見た目や文言も手抜き感がありあり」。

**状況**: THTH は Meta の App Review を 09-27 に提出し、判定待ち（1 週間目安）。仕組みは凍結中だが、**文章と画面の見た目は直してよい**（静的な文と HTML/CSS だけ・Worker の deploy は masaru の手）。ただし審査員の手順に書いた語は**動かせない**（下の固定の語）。

## 1. 公開サイトの文章（thth.me の紹介ページ・llms.txt・privacy と規約の日本語）

thth.me の HTML は `tools/build_site.py` が生成する。**生成物（`callback/public/*.html`）は触らず、元を直す**:

1. `tools/build_site.py` の `build_index()`（紹介ページの日本語本文。「masaru」が 6 か所）
2. `README.en.md` の 3 節 "What it guarantees" / "What it refuses" / "What it never does"（英語・サイトに写される）
3. `skills/thth/SKILL.md` の同じ 3 節「何を保証するか／何を拒むか／何を絶対にしないか」（日本語）
4. `llms.txt` の冒頭（`callback/public/llms.txt` は 1 バイトも変えずに写される）
5. `tools/build_site.py` の `build_privacy()`・`build_terms()`: 誤字・不自然な日本語だけ。事実（保持期間・数値・約束）は変えない

直し方の決まり:
- 「masaru」は書かない。運営者は "the operator (gotoq)"／「運営者（gotoq）」。個人名・チャットの引用（「あのシンプルなテイスト」など）・日付つきの申し送りは消す。
- 今の仕組みと一致させる: 承認ページは無い（3.13.0 で廃止）。外の人は招待リンク → Threads の認可 → `pip install thth` → `thth login`（ブラウザで許可）→ `thth send/posts/replies/measured/retract` で自分の口座を動かす。安全装置（最短間隔・1 日の上限・急な連投で停止）は持ち主が決め、LLM は締める向きにしか変えられない。持ち主の画面 https://thth.me/activity で見る・止める・鍵を発行する。正確な事実は `docs/導入_招待されたら.md`・`docs/手順_Meta申請_世間の層_2026-09-24.md` §1.1・§2.1。**実装に無いことは書かない**（迷う所は「未決」として報告に）。
- 運営者自身の使い方（自分の repo の queue → `thth approve` の 2 段 → timer が投稿）は今も本当なので残す。「招待した利用者」の段落だけ新しい流れに。
- 調子は今の紹介ページに合わせる（サクラエディタ・CotEditor・jq のような気取らない案内。宣伝の形容詞を足さない）。英語は簡潔な平叙文。日本語と英語で同じことを言う。

## 2. 途中に出るブラウザの画面（Worker の中・HTML と CSS と文言だけ）

対象: `callback/src/person.js` の `page()`/`en()`（全ページ共通の枠と CSS）、`invite.js`（招待・認可の準備・完了・secret と鍵の表示）、`login.js`（`/login/<code>`: 鍵を渡す・待つ・渡した）、`activity.js`（`/activity`: 入る・動きの一覧・操作の form・鍵の発行）、`index.js` の `/callback` の受付ページ、`deletion.js` の data-deletion の状況ページ、各エラーページ（404・410・429・503 の文）。

やること:
- 見た目を 1 つに揃える（`page()` の CSS を 1 か所に。余白・行間・見出しの階層・ボタン・入力欄・コードの枠・「表示は一度だけ」の強調・幅 44rem・スマホでの読みやすさ）。装飾は足さず、揃えるだけ。
- 文言を「読む人の順」に: 見出し → 何が起きたか 1 文 → 次にすること → 注意。日本語が先、英語は同じ内容で下（`en()` の作法のまま）。語を統一（「口座」「口座の secret」「アシスタントの鍵」「動きの一覧」「持ち主」「運営者」）。「運営者に連絡してください」は、どこへ（https://thth.me/privacy/ の Contact）を 1 文で。
- 手抜きに見える所（改行だけの羅列・全角と半角の混在・句読点の揺れ・英文の受動態の連続・同じ文の繰り返し）を直す。
- script は使わない（CSP）。no-store・no-referrer・同一 origin の POST・csrf・form の name と action・URL・状態コード・`<meta http-equiv="refresh">` の待ちは変えない。

**変えてはいけない語**（Meta に出した審査員の手順が引いている。1 字も変えない）:
- 「THTH への招待 / Invitation to THTH」（招待ページの見出し）
- 「Threads で認可する / Authorize with Threads」（ボタン）
- 「Threads の認可ページを開く / Open Threads authorization」（ボタン）
- 「用意ができました / Your account is ready」（完了の見出し）
- 「口座の secret を表示する / Show account secret」（ボタン）
- 「口座の secret / Account secret」・「アシスタントの鍵 / Assistant key」（表示の見出し）
- 「この機械に鍵を渡す / Allow this device」（`/login` のボタン）
- 「動きの一覧 / Activity」（`/activity` の見出し）・「この口座を止める / Stop this account」・「LLM の鍵を発行する / Issue a key for your LLM」

## 3. 手順

1. THTH の repo（`/Volumes/NexDev/Developer/thth`）で `git worktree` の枝 `site-proofread`（main から）。**main・release・tag・deploy は触らない。**
2. 元を直す → `python tools/build_site.py` → `git diff callback/public/` で生成の差分を見る。
3. 試験: `python -m pytest tests/test_site.py tests/test_docs_links.py tests/test_cli_help.py -q -p no:cacheprovider`（rc を確かめる）と `cd callback && npm test`・`npx --no-install wrangler deploy --dry-run`。落ちたら試験を書き換える前に自分の変更を疑う。試験が古い事実を固定している場合だけ、その行を報告に挙げる。
4. commit（`git add` はパスを明示・`-A` 禁止）。push はしない。
5. 報告（Agent Mail で DustyCave 宛て・本文は THTH の `docs/報告_thth.me_校正_2026-09-2x.md` に）: 変えた文の一覧（前後）・ページごとの前後（見出し・主な文）・CSS の変更点・スクリーンショット（`wrangler dev` で開いた各ページ 1 枚ずつ・鍵や secret は偽の値で）・判断した所・未決・試験の rc。

## 4. 触らないもの

`callback/src/*.js` の HTML/CSS/文言以外（処理・DO・署名・URL）・`thth/*`・`docs/*`（正本のまま）・版・release 枝。秘密や招待 URL を会話に出さない。THTH 側の受け入れは DustyCave が diff を見て main に入れ、Worker の deploy は masaru の 1 行。
