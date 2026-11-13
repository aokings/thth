# 申請の下書き: Google ビジネス プロフィールの API（基本のアクセス）

2026-11-13、THTH開発 第16。**まだ出していない。** masaru が中身を見て OK を出してから、内蔵のブラウザで form に入れて送る。

## 前提（Google の頁の原文）

- 手順: Google Cloud の project → 組織のアカウント → 問い合わせの form の「基本の API アクセスの申請」（https://developers.google.com/my-business/content/prereqs）。
- form の頭に「ビジネス プロフィールに関連付けられている Google アカウントにログインする必要があります」（https://support.google.com/business/contact/api_default）。

## 状況

| 項目 | 今 |
|---|---|
| 出すアカウント | aokings@gmail.com（内蔵のブラウザでログイン済み） |
| そのアカウントのビジネス プロフィール | 0 件。学校のプロフィールの管理者に足してもらう（masaru 11/13「基本はなれます」）→ まだ |
| Cloud の project | 既存の `thth-google-ads`（ID `thth-gads-2611`・番号 654856288966）を使う案。THTH の Google の口を 1 つの project にまとめる。新しく作らずに済む |
| 組織のアカウント | 未確認（GBP ヘルプの「組織アカウント」。要るなら学校の管理者になったあとに見る） |

## form の欄と下書き

- どのようなことでお困りですか: 基本の API アクセスの申請
- お名前: （送るときに masaru の名前を入れる。ここには書かない）
- メールアドレス: （同上）
- 会社名: **未決**。案 A: THTH を出している側の名前（THTH・運営者の名前）。案 B: 学校の名前。推しは A（申請するのは THTH の使い道のため）。Google がプロフィールの事業と同じ名前を求めるかは原文で未確認
- Google Cloud Platform のプロジェクト ID: thth-gads-2611
- プロジェクト番号: 654856288966

### 詳しい説明（英語で出す案・日本語の訳つき）

> THTH (https://thth.me) is a command-line tool that local business owners run on their own computer to manage how their business information appears online. We are requesting Basic API access so that owners can keep their Google Business Profile in sync with the information they already maintain in THTH.
>
> What we will do with the API:
> - Read the owner's own locations, regular and special hours, and basic business information, to show the owner where Google differs from their own records.
> - Update hours (including special/temporary closures) and create local posts (events, announcements) only after the owner reviews the exact change and approves it. Nothing is changed automatically.
>
> What we will not do:
> - We do not access locations the signed-in owner does not manage.
> - We do not create fake reviews, reply to reviews automatically, or post on the owner's behalf without their approval.
> - We do not sell or share Business Profile data. Data stays on the owner's computer except what is sent to Google to make the approved change.
>
> Privacy policy: https://thth.me/privacy  Terms: https://thth.me/terms

（訳）THTH は、地域の店の持ち主が自分の PC で動かし、店の情報の出し方を管理する道具。持ち主が THTH に持っている情報と Google のプロフィールをそろえるために、基本のアクセスを申請する。読むのは持ち主自身の店の場所・営業の時間・臨時の時間・基本の情報で、Google と持ち主の記録の違いを見せる。営業の時間（臨時の休みを含む）の更新と投稿は、持ち主が変わる中身を見て承認したあとだけ。自動では何も変えない。管理していない店には触らない・口コミの自動の返信や偽の口コミはしない・データを売らない。

## 送る前に要ること

1. 学校のプロフィールで aokings@gmail.com を管理者にする（masaru か学校のオーナー）
2. 会社名の欄を決める（推し A）
3. 上の英文で良いか（masaru）
4. privacy と terms の頁に、Google ビジネス プロフィールの口の文がまだ無い。申請の段で要るかは未確認。口を作るとき（計画の段 6）に足す
