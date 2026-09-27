# thth.me の文章と画面の校正報告（2026-09-27）

対象: `site-proofread`（base `1335bd6f86cd44b7b36110f1f7cc5e5d5cc03af3`）。
source revision: `abd40f754dc0c44259b8ddc0a7c7babe6497f5f5`。
独立監査は PASS。これは main への受入ではない。

## 変更したこと

### 変えた文の前後

| 場所 | 前 | 後 |
| --- | --- | --- |
| 紹介ページ | 招待された利用者は認可 URL を開き、接続ページは今後の実装。 | 招待リンク、Threads 認可、`pip install thth`、ターミナルの `thth login`、ブラウザでの許可の順に案内。 |
| 紹介ページ | 自分の Git と二段承認を、全利用者の流れとして説明。 | 「自分のリポジトリから投稿する」と「招待された方へ」を分け、queue の二段承認と招待口座の命令実行を区別。 |
| README と skill | queue の承認、`--production`、Git 記録を無限定に保証・拒否。 | 手元の台帳または運営者管理 queue に適用する、と主語を限定。 |
| llms.txt 冒頭 | すべてが Git に記録され、opt-in しない限り機械から出ない、と無限定に説明。 | ローカル queue の Git 記録と、招待口座が運営者（gotoq）のサーバを通る流れを区別して説明。 |
| 招待の secret 画面 | 初回鍵を `thth login` へ 1 度だけ入れる説明。 | 通常は browser の secret 入力で許可し、初回鍵は browser が使えない場合の `thth login --stdin` 用と説明。 |
| 連絡が必要な招待画面 | 運営者に連絡してください。 | プライバシーポリシーの Contact へのリンクを示す。 |

### ページごとの前後

| 経路または状態 | 前の見出し・主な文 | 後の見出し・主な文 |
| --- | --- | --- |
| `/` | 「使い方」と二段承認を先に示す。招待は旧い認可 URL の説明。 | 「自分のリポジトリから投稿する」と「招待された方へ」を分け、招待の完了までの順序と持ち主の動きの一覧を示す。 |
| `/invite/*` open / clicked | 権限と短い手順。待ち時間の日英が一致しない。 | 「何が起きるか」から認可、secret と鍵の保存へ進む。日英とも数十秒は “a few tens of seconds”。 |
| `/invite/*` ready / secret / done / error | secret の保存と再発行案内が混在。 | 「表示は一度だけ」を強調し、secret、鍵、login、動きの一覧の順に整理。連絡は Contact へ案内。 |
| `/login/*` form / waiting / done | 鍵を渡す理由と注意が長い文に混在。 | 見出し、本人確認、入力、10 分の期限、完了を段落で分け、英語を各日本語段落の下に置く。 |
| `/activity` sign-in / list / key | 鍵を「LLM の鍵」と表記し、日本語と英語が同じ段落に続く箇所がある。 | 「アシスタントの鍵」に統一。鍵の発行画面の MCP 説明も日本語の下に英語を置く。固定操作ラベルは維持。 |
| `/callback` empty / error / code / consumed / ready | 主要な受付画面に英語がない。 | 日本語を先に残し、同内容の英語を `en()` で下に追加。既存 script、id、headers、status は保持。 |

### 紹介ページ・公開文書

- 紹介ページから個人名と古い「接続ページは今後の実装」の説明を外した。運営者は「運営者（gotoq）」とし、招待された人の順序を、招待リンク、Threads の認可、`pip install thth`、ターミナルでの `thth login`、ブラウザでの許可、と書き直した。
- 「自分のリポジトリから投稿する」と「招待された方へ」を分けた。二段承認、queue、Git の記録、`--production` は、自分のリポジトリまたは運営者管理 queue の流れにだけ適用する、と英日とも明示した。
- 招待された口座については、自分の口座を `send`、`posts`、`replies`、`measured`、`retract` で扱い、持ち主が動きの一覧で確認・停止・鍵の管理をする流れを書いた。安全装置は持ち主が決め、アシスタントは締める向きにしか変えられない。
- X の説明を、投稿・削除・自分の最近の投稿の読み取りに対応し、返信と実測の採集には対応しない、と実装に合わせた。稼働保証は追加していない。
- README、skill、llms.txt の冒頭も同じ区別に直した。`thth login` はターミナルで実行し、ブラウザでこの機械を許可する、と統一した。
- privacy と terms の日本語は、運営者表記と助詞の不自然さだけを直した。保持期間、数値、約束は変更していない。

### Worker の画面

- `person.js` の共有スタイルを、最大幅 44rem、見出し、段落、入力欄、選択欄、ボタン、コード枠、1 回だけ表示の強調、light/dark の背景と文字色に整理した。招待・login・activity・callback が同じ基準で表示する。
- 招待、login、activity、callback の本文を、見出し、何が起きたか、次にすること、注意、の順に整えた。日本語の段落の直下に同内容の英語を置いた。
- 連絡が必要な招待のエラーは、プライバシーポリシーの Contact へ行くリンクを示すようにした。
- 初回のアシスタントの鍵は 1 回だけ表示されることを強調した。通常の login は口座の secret をブラウザへ入力して許可する流れであり、表示した鍵はブラウザを使えない場合の `thth login --stdin` 用であると明記した。
- callback の既存 script は変更していない。コピー操作の script 内の文言も対象外として保持した。

認可・送信・操作に使う経路 URL、form の action/name、CSRF、status、headers、meta refresh、既存 script、固定ラベルは変更していない。

## 検証

証拠ログの正本は [validation](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/validation/) に置いた。依存は既存 lockfile に対する `npm ci` で復元し、upgrade はしていない。

画面証拠の索引は [README-local-evidence.md](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/README-local-evidence.md)、再現 package は [reproduction README](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/reproduction/README.md) にある。代表の比較は [before](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/before/callback-ready-invite-390.png) と [after](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/after/callback-ready-invite-390.png)、dark の鍵画面は [after dark](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/after/activity-key-390-dark.png)。before 58 枚、after 61 枚、共通 58 組の保護属性差分は 0、after 全画面の横 overflow は 0 で、manifest は `b2a891086a12b76c716025919282f3cf188e640af1c2104c7687e30cba64f587`。

[独立監査報告](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/audit/audit-report.md) は 36 の保護契約、7 の scope 検査、7 の改善確認を PASS とした。最終 source の同じ rate-limit 試験を独立に単独実行し、rc 0（1 pass）だった。[監査の最終 manifest](/Users/masaru/Documents/asmon_private/thth-proofread/2026-09-27/audit/final-manifest-check.json) も参照。

この画面証拠は fake backend を使う visual fixture である。実際の Threads 認可、招待 URL、secret を使う end-to-end 試験ではない。

| 検証 | 結果 | 内容 |
| --- | --- | --- |
| `python -m pytest tests/test_site.py tests/test_docs_links.py tests/test_cli_help.py -q -p no:cacheprovider` | rc 1 | 28 pass、3 fail。下記の既存 fixture と正本起因の失敗。 |
| `callback npm test` | rc 1 | 131 pass、1 fail。`invite.test.mjs:140` が旧い `thth login` の説明文を固定している。 |
| `npx --no-install wrangler deploy --dry-run` | rc 0 | assets と既存 binding を読み込み、dry-run で終了。 |
| JavaScript syntax | rc 0 | `index.js` と `activity.js`。 |
| 差分の空白検査 | rc 0 | `git diff --check`。 |

Python 試験の失敗は次のとおり。

1. `tests/test_site.py:151` の callback 応答 bytes fixture は、今回正本が求める callback の英訳と共有 CSS の変更を許さない。
2. `tests/test_site.py:228` は紹介ページに個人名を含む旧文を期待する。今回の正本の「masaru は書かない」と直接矛盾する。
3. docs link 試験は、依頼正本にある複数ファイル名の連記と `2026-09-2x` の例示を存在しないパスとして扱う。基準 revision でも同じ docs link failure が再現している。報告書を追加した後の実測は 2 pass、1 fail で、余分な failure は増えていない。

callback の単独 `invite.test.mjs` は 15 pass、1 fail だった。失敗の実原因は `callback/test/invite.test.mjs:140` が旧い `<code>thth login</code> で 1 度だけ入れます` を必須としていることであり、今回の browser login と `--stdin` の正確な説明に更新した箇所と衝突する。login の rate limit 試験は基準 revision の単独 `login.test.mjs` で 11 pass であり、最終の全 `npm test` の failure には含まれなかった。

途中の対話実行で login の rate limit assertion が 1 回失敗したが、その実行は 30 秒の対話出力であり、検証用ログとして保存されていない。基準 revision の単独試験は pass、最終の保存済み全試験ログは 131 pass・1 fail である。原因は未確定で、最終結果には含めない。

## 未決・範囲外

- `README.en.md` の指定3節以外と `llms.txt` 冒頭以外には、旧い招待・X の説明が残る。指定外のため変更していない。
- `deletion.js` は data-deletion の JSON 応答だけで、HTML の状況ページは実装されていない。画面を新設していない。
- callback の script を使わないという新規画面方針と、既存のコピー・履歴消去 script は衝突する。既存処理を変えない制約を優先し、script は保持した。
- branch baseline では依頼正本の連記・例示を docs link 試験が誤検出する。main の `d0bb29b` では正本が修正され、docs link 試験は 3 pass と親が確認している。受入時に統合後の revision で再確認する。
