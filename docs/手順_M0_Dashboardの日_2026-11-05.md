# M0 Dashboard の日（masaru の手・1〜1.5 時間）

Meta 全部盛り申請の最初の回。正本は [計画_Meta全部盛り申請_2026-11-05.md](計画_Meta全部盛り申請_2026-11-05.md) の §5。THTH開発が内蔵ブラウザで横に付き、masaru のログインで読みます。**申請はこの日にはしません。** Meta の画面の操作だけです。

時間は FB の投稿の空いているところで選びます。⑥で FB ページの鍵が一度切れるためです。

## 読むだけ（先にする）

1. アプリレビュー →「権限と機能」に、次が出るかを見る。出るものは入れ、出ないものは落とす。
   - `user_*` 11 件・`pages_user_gender`・`pages_user_locale`・`pages_user_timezone`
   - `pages_manage_instant_articles`（機能が今もあるか）
   - 機能の節に出るもの（Page Public Content Access ほか 11）
2. 承認済みの権限を新しい申請に入れられるか（`threads_keyword_search` の説明の差し替えの流れ A か B）。
3. Data Use Checkup の期日・App Purpose・9/22 の欄。
4. テストユーザーがページや広告アカウントを持てるか。
5. Ads Management Standard Access の状態。

## 変える（読んだあと）

6. 構成「THTH pages」に足す権限を足す。Product Catalog Terms などの同意が出たら、その場で masaru が読んで決める。
7. アプリに WhatsApp の用途を足せるかを見る。足すとアプリの構成が変わるので、影響を読んでから足す。
8. 機能の節で、申請できる機能を「アプリレビューに追加」する。
9. 古い広告の鍵を無効にし、FB ページの鍵を取り直す（`thth auth`）。無効にすると FB ページの鍵も一緒に切れる。

## この日にしないこと

- **Threads の webhook の購読:** 新しい VM が出たあとにする。古い VM は Threads の行を含む頁を丸ごと断り、FB の取り込みまで止まるおそれがある。
- **申請の提出:** M1〜M6 のあと、録画の翌日。

## 別に要る準備（M7 の録画までに）

- こぴちゃの Meta のショップ（コマースアカウント）・商品カタログ・注文のデータ
- WhatsApp のビジネス口座（WABA）と試験の番号
- 配信を了承した受け手（マーケティングのメッセージを入れるとき）
