# 手順: Google の広告と AdSense の準備（2026-11-08 THTH開発 第13）

masaru 11/08「もっとサポートして」。Google の 2 つの準備を、誰が何をするかまで 1 枚にした。調べの正本は [調べ_Google広告とAdSenseのAPI_2026-11-06.md](調べ_Google広告とAdSenseのAPI_2026-11-06.md)、AdSense の申し込みの叩き台の正本は media-hub の repo の docs にある「段取り_AdSenseの申し込み_2026-11-07」（本線 第12）。

## 全体像

| 道 | 何本 | masaru の手 | Claude の手 | 終わりの印 |
|---|---|---|---|---|
| A. Google 広告（出す側・キーワードの数） | 6 段 | 2 回（承認のボタン・ブランド確認の申請を押すかの返事） | 残り全部（ブラウザの画面で） | Cloud の画面で段が「Basic」 |
| B. AdSense（稼ぐ側） | 5 段 | 1 回（申し込み。支払い先を入れるので本人だけ） | プライバシーの頁と札は各サイトの窓口へ頼む | AdSense の画面で「準備完了」 |

A と B は別々に進められる。B は審査に数日〜数週かかるので、先に申し込むと待ち時間が重なって早く済む。

## A. Google 広告の準備（Cloud project）

ログインは aokings@gmail.com で済んでいる（11/08 にブラウザで確かめた。今ある project は siroq-personal・My First Project・oremo など 4 つ、広告のものは無い）。

| 段 | すること | 誰 | 成功の印 | 失敗したら |
|---|---|---|---|---|
| 1 | 新しい project「thth-google-ads」を作る（組織なし） | Claude | 上の帯の project 名が thth-google-ads になる | 名前の重複なら末尾に数字 |
| 2 | 「Google Ads API」を有効にする | Claude | API の頁に「管理」のボタンが出る。段は Test | 請求先を求められたら止めて masaru に聞く（本来は要らない） |
| 3 | OAuth の同意の画面: 外部・アプリ名 THTH・連絡先 aokings@gmail.com・スコープ `adwords`。**状態を「本番」にする**（テスト中のままだと鍵が 7 日で切れる） | Claude（「本番にする」は押す前に masaru に聞く） | 状態が「本番環境」。未確認の警告は出るが、自分と数人なら使える | 確認を求められたら止めて画面を見せる |
| 4 | OAuth のクライアント（種類はデスクトップ）を作る。秘密の値は `~/.config/thth/google-ads-client.json` に置き、会話と repo には出さない | Claude | ファイルができ、`ls -l` で 600 | ― |
| 5 | Google Ads API の頁で Explorer を申請する | Claude（押す前に masaru に聞く） | 段が Explorer（多くは自動ですぐ） | 断られたら理由の文を masaru に見せる |
| 6 | ブランド確認 → Basic を申請する（キーワードの案と検索の数に要る） | Claude が打ち、送る前に masaru に聞く | 段が Basic | 確認に時間がかかる。待つ |

段 5 が済むと、THTH で広告の成績を読む口と、止めた状態で作る・止める口を作り始められる。段 6 が済むと、キーワードの案と検索の数を引く口を作れる。

**11/08 の進み（masaru「打ってよい」「同意してよい」）:**
- 段 1 済み: project 名 thth-google-ads・ID `thth-gads-2611`（`thth-google-ads` の ID は世界のどこかで使われていて取れなかった）。
- 段 2 済み: Google Ads API が「有効」・段は Test。
- 段 3 の途中: 同意の画面を作った（アプリ名 THTH・外部・連絡先 aokings@gmail.com・Google API のユーザーデータの方針に同意）。状態はまだ「テスト中」（「本番」にするのは押す前に masaru に聞く）。スコープ（データアクセス）は入れていない（`adwords` は要確認のスコープで、入れると確認を求められうるため。未確認のままでも自分と数人は使える）。
- 段 4 済み: デスクトップの OAuth クライアント `thth-cli` を作り、鍵の JSON を `~/.config/thth/google-ads-client.json`（600・フォルダは 700）に置いた。アプリの内蔵ブラウザはダウンロードを保存しないので、JSON は masaru が自分の Chrome でダウンロードし、Claude が形だけ確かめて移した（秘密の値は会話に出していない）。
- 残り: 段 3 の「本番」→ 段 5 Explorer → 段 6 ブランド確認と Basic。

## B. AdSense の申し込み（本線の叩き台の要点）

| 段 | すること | 誰 |
|---|---|---|
| 1 | 最初の 1 サイトを決める（推し kopicha.org。独自ドメインで記事が多く、検索のクリックが伸びている） | masaru が「よい」と言う |
| 2 | kopicha.org にプライバシーの方針の頁を作る（今は 404。GA4 と広告の両方を書く） | こぴちゃの窓口へ頼む |
| 3 | adsense.google.com で申し込む（支払い先の情報を入れるので本人だけ） | masaru |
| 4 | AdSense が出す札（頁の 1 行）と ads.txt を入れて配信する | こぴちゃの窓口 |
| 5 | 審査を待つ。通ったら hub が売上の数を出し、THTH が受ける | 実装 第10・THTH開発 |

## masaru に聞くこと（1 つずつ）

1. A の段 1〜4 を Claude がブラウザで打ってよいか（推し: よい。お金は動かず、project はあとで消せる。段 3 の「本番にする」と段 5・6 の申請は、押す前にもう一度聞く）。
2. B の最初の 1 サイトを kopicha.org にしてよいか（推し: よい）。
