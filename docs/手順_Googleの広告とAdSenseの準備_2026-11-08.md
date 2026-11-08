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
- 段 3 済み: 同意の画面を作った（アプリ名 THTH・外部・連絡先 aokings@gmail.com・Google API のユーザーデータの方針に同意）。masaru「本番にしてよい」で「本番環境」にした。公開の前にブランディングを埋める必要があり、ホームページ https://thth.me/・privacy https://thth.me/privacy・terms https://thth.me/terms・承認済みドメイン thth.me を入れた（3 つとも 200 を確かめた）。スコープ（データアクセス）は入れていない（`adwords` は要確認のスコープで、入れると確認を求められうるため。未確認のままでも自分と数人は使える）。
- 段 4 済み: デスクトップの OAuth クライアント `thth-cli` を作り、鍵の JSON を `~/.config/thth/google-ads-client.json`（600・フォルダは 700）に置いた。アプリの内蔵ブラウザはダウンロードを保存しないので、JSON は masaru が自分の Chrome でダウンロードし、Claude が形だけ確かめて移した（秘密の値は会話に出していない）。
- 段 5 済み: masaru「申請してよい」で Explorer を申請し、数分で「エクスプローラ」になった（本番の口座 1 日 2,880 操作・テストの口座 15,000）。
- 段 6 の前に分かったこと: Basic の申請はブランド確認が前提。aokings@gmail.com は Search Console で thth.me の所有者になっていない（「このプロパティへのアクセス権がありません」）。ブランド確認では承認済みドメインの所有を見るので、先に thth.me の所有を証明する（DNS の TXT を 1 行足す。Cloudflare の API で足せる）。
- thth.me の所有の証明 済み（masaru「足してよい」）: Search Console にドメインのプロパティ thth.me を足し、Cloudflare の API で TXT を 1 行足した（record id `681cbd4ff13940a8c4dd903c18ad95ed`・コメントに目的を書いた）。「所有権を証明しました」。**この TXT は消さない**（消すと証明が切れる）。
- ブランド確認の前で止めた: ブランディングの頁に「確認に送る」のボタンが出ていない。ロゴを上げると送信が要る作り（頁の説明）なので、ロゴ（`callback/icon/thth-icon-1024.png` を 120×120 にしたもの）を上げて確認に送る見込み。アプリの内蔵ブラウザにはファイルを上げる道具が無い。
- ブランド確認に送った（masaru「送ってよい」）: ロゴは内蔵ブラウザで上げられず、Claude in Chrome もつながらなかったので、masaru が自分の Chrome で `~/Downloads/thth-logo-120.png` を上げて保存した。「ブランディングを確認」のボタンは、ブランディングの頁の右上の ⓘ（情報と概要）を開いた横の欄にある（頁の本文には出ない）。押すと「Verification in progress・5 分ほど」。原文では、通ると「公開の準備完了」になり、7 日以内に「ブランディングを公開」を押す。落ちたら理由を直すか、人の確認（2〜3 営業日）を頼む。
- ブランド確認は通った（数分・「ブランディングは検証済み・7 日以内に公開」）。**期限は 2026-11-15 ごろ**（過ぎると確認のやり直し）。「ブランディングを公開」は 1 回目に権限で断られ、masaru「押して下さい」で押した。「検証済みで、ユーザーに表示されています」。
- 段 6 済み: masaru「申請して下さい。押して下さい」で Basic を申請し、数分で「基本」になった。本番の口座も 1 日 15,000 操作・すべての API 機能（キーワードの案と検索の数 `KeywordPlanIdeaService` を含む）。次の段の Standard（無制限）は申請せず置いている（画面の表示では審査 10〜15 営業日）。
- THTH の口 `thth gads auth|accounts|keywords|report`（読むだけ）を入れた（merge `fef4b8c`・全件 11,578 件緑）。11/08 に masaru が認可を押し、鍵は `~/.config/thth/google-ads-token.json`（600）。本物で `accounts` は 226-006-0755 が見えた。`keywords` は `CUSTOMER_NOT_ENABLED`（口座の初めの設定＝支払いの情報が未了）で断られた。支払いの情報は masaru の手（キャンペーンを作らなければ請求は起きない）。済んだら Claude が `thth gads keywords --customer 226-006-0755 --seed …` で確かめる。
- 11/08 夜: 226-006-0755 は「閉鎖済」で、支払いの画面が開けなかった（作りかけで閉じたと見ている・未確認）。masaru が新しい口座 **173-554-9950**（名前 THTH）をキャンペーンを作らずに（「スキップ」）作った。`thth gads accounts` に見え、`keywords` で案が引けた（例「sns 予約 投稿」月平均 40）。ただし同じ頼みが時々 `ACTION_NOT_PERMITTED` で断られる（口座が「設定中」の間の揺れと見ている）。少し後に `--seed` を 2 つ重ねて 4 回続けて打ち、4 回とも通った。以後の口座は 173-554-9950。
- gotoq として出稿するときの叩き台（11/08・決めるのは gotoq）: 口座は m.aoki@gotoq.jp 側に分け、支払いも会社のもの。THTH から読むなら aokings@gmail.com を「閲覧のみ」（作るなら「標準」）で招待すれば、今の認可のまま `thth gads accounts` に並ぶ。
- 11/08 masaru「gsc と gt 入れちゃいましょう」: Cloud の project thth-gads-2611 で Search Console API を有効にした（画面に「ステータス 有効」）。`thth gsc auth|sites|query|compare|inspect`（読むだけ・スコープ webmasters.readonly・鍵は `~/.config/thth/google-gsc-token.json`）を入れた（merge `a73e41f`・認可の流れは gads と共通の `thth/google_oauth.py`）。masaru が aokings@gmail.com で許可を押し、本物で sites は 13 件（kopicha.org・nigamilab.com・asmon.ai・thth.me など・すべて所有者）、kopicha.org で query・compare（直近 28 日と前の 28 日）・inspect（判定 PASS）が通った。media-hub の `scripts/gsc.py`（SEO レーン）は masaru のサイト用、THTH の口は THTH の利用者が自分のサイトを読む道具として作る。
- Google トレンド API は試験版で申し込み制（https://developers.google.com/search/apis/trends ）。masaru「申し込みましょう」で 11/08 に申し込んだ（名義 aokings@gmail.com・project thth-gads-2611・個人の開発者・商用・感想はメールで）。受け付けの画面「We'll notify you if you're accepted」。通った知らせは aokings@gmail.com のメールに来る（masaru はメールを見ないので、ときどき Claude が受信を確かめる手が要る）。通るまで `thth trends` は作らない。
- Yahoo!広告（LINEヤフー広告）API は見送り（11/08 masaru「推しでオケ」）。キーワードの案の口（KeywordPlanIdeaService・v20）は語しか返さず、月間の検索数が無い（公式の OpenAPI 定義 yahoojp-marketing/ads-search-api-documents の design/v20 で確かめた）。検索数の見えた「キーワードアドバイスツール」は 2023-05-31 に終わった。口座・API の申し込み・約款の同意が要るのに対し、語の案は Google で足りる。
- A の道はここで終わり。次は THTH の口: 鍵（`~/.config/thth/google-ads-client.json`）で masaru の Google 広告の口座を認可し、成績を読む口・キーワードの案の口・止めた状態で作る/止める口を作る。

## B. AdSense の申し込み（本線の叩き台の要点）

| 段 | すること | 誰 |
|---|---|---|
| 1 | 最初の 1 サイトを決める（推し kopicha.org。独自ドメインで記事が多く、検索のクリックが伸びている） | masaru が「よい」と言う |
| 2 | kopicha.org にプライバシーの方針の頁を作る（今は 404。GA4 と広告の両方を書く） | こぴちゃの窓口へ頼む |
| 3 | adsense.google.com で申し込む（支払い先の情報を入れるので本人だけ） | masaru |
| 4 | AdSense が出す札（頁の 1 行）と ads.txt を入れて配信する | こぴちゃの窓口 |
| 5 | 審査を待つ。通ったら hub が売上の数を出し、THTH が受ける | 実装 第10・THTH開発 |

**11/08 に確かめた今の状態（上の表は 11/06 の叩き台のまま）:** 段 1〜4 は 11/07 に済んでいた（メディアハブ本線 第12 の依頼）。kopicha の commit `393a93e0` で `/privacy/`（Google アナリティクスと AdSense の Cookie・止め方を含む・200）、`677505c1` で審査用の札を全頁の head に入れ、ads.txt に 1 行（200）。commit の説明に「masaru 11/7 の申し込み」。自動広告はオフで、広告の枠は置いていない。今は段 5（審査を待つ）。THTH の側ですることは無い。

## masaru に聞くこと（1 つずつ）

1. A の段 1〜4 を Claude がブラウザで打ってよいか（推し: よい。お金は動かず、project はあとで消せる。段 3 の「本番にする」と段 5・6 の申請は、押す前にもう一度聞く）。
2. B の最初の 1 サイトを kopicha.org にしてよいか（推し: よい）。
