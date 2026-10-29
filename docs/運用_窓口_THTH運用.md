# THTH運用の窓口（2026-10-29〜）

運用の頼みを受ける専用の窓口。立ち上げは masaru 10/29（本線 メディアハブ本線 第10 が起こした）。開発が大きな仕事の最中でも、運用の頼みが止まらないようにする。

- 呼び名: **THTH運用 第1**（hub roster では `thth（ops）`、伝言の宛先は `thth/ops`）
- 受けるもの: 口座の追加、Facebook ページ・Instagram のつなぎ、予約やはてなの札の流れの確認、本番で何が動いているかの確認
- しないもの: THTH のコードを直すこと（不具合・要望は `thth report file` か hub msg で開発 `thth` へ）、`release` を進める・tag・Worker の deploy（開発が masaru の「出して」で押す）
- 決まりの正本は repo の `CLAUDE.md` と `docs/定型_運用セッションの始まり.md`。この文書は持っている案件の台帳だけ

## 持っている案件

| # | 件 | 状態 | 次の一手 |
|---|---|---|---|
| 1 | Facebook ページを THTH につなぐ（手順の正本 `docs/手順_FBページと広告_2026-10-28.md`） | こぴちゃは済み（§0-5。10/29 16 時に本番の `thth board` で `kopicha-fb token=ok/期限なし`）。10/29 16 時、ページの透明性で id を引き、台帳 `nigamilab-fb`（1339887359217489）と `asmon-kanto-fb`（1469823909537300・原稿は `repos/asmon` で asmon-kanto-threads とそろえた）を作った。どちらも `production: false`・`scheduled: false` | `thth auth` を 1 口座ずつ（ログは VM の `~/thth-auth-<口座>.log`）。許可の画面の「次へ」は masaru。通ったら `thth doctor`。本番にするかは masaru。残る 3 ページ（染めと化学・キョンシーラボ・MMR）は masaru が作ってから。Instagram・広告・Webhook は開発の計画ができるまで足さない（開発 #5886） |
| 2 | kopicha-threads が 10/29 15:49 に `blocked（account_locked）` の運用通知を出した | 原因は前の回とのロックの取り合い（journal に「既に実行中です（ロック取得失敗）」）。次の 15:58 の回は「出すものが無い」で正常に終わった | 閉じた |

## 気づいたこと（開発へ渡すか判断中）

- `account_locked`（2 つの回が重なってロックが取れなかっただけ）でも、利用者と管理者へ `blocked` の運用通知が飛ぶ。重なりだけなら通知は要らないかもしれない。
