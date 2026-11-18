# 申請の下書き: Google ビジネス プロフィールの API（基本のアクセス）

2026-11-13、THTH開発 第16。**2026-11-13 に送った。ケース ID 1-8384000042054。審査は 7〜10 営業日（Google の画面の文）。同じ project で二度送らない（遅れる）。**

**2026-11-17 不合格の返事が来た（masaru が転送）。** 理由は 1 つだけ: 申請の「会社のウェブサイト」（https://thth.me）が、確認済みのビジネス プロフィールに出ているウェブサイトと完全に同じでない。プロフィール（山手日本語学校）に出ているのは `https://yamate-jls.aiko.ac.jp/`（11/17 にビジネス プロフィール マネージャーから開いた管理者向けの表示で確かめた）。出し直すなら、ウェブサイトの欄をこの文字列と 1 字も違わず書く。ほかの欄は理由に挙がっていない。プロフィールとウェブサイトを最新にしてから新しく申請するよう書かれている。誤りだと思うなら一般の GBP API のサポートの問い合わせで証拠を出す道もあるが、今回は本当に違っていたので当たらない。

**2026-11-17 出し直した（masaru「出し直します」・THTH開発 第19 が内蔵のブラウザで送った）。新しいケース ID 4-3446000042331。審査は 7〜10 営業日（11/27〜12/1 ごろ）。この project でもう送らない。** 前回から替えたのは 2 欄: 会社のウェブサイト `https://yamate-jls.aiko.ac.jp/`（プロフィールと同じ文字列）、主な理由（学校が自分のプロフィールを THTH で管理する、と書き直した。全文: "Yamate Japanese Language School (Aiko Gakuen) manages its own Business Profiles with THTH, a command-line tool we run on our own computer. We will read and update our own hours, special hours and posts to match our records. Every change is reviewed and approved by us before it is sent; nothing is changed automatically."）。form に会社名の欄は無かった。ほか（アカウント aokings・ビジネス 山手日本語学校・project 番号 654856288966・知った所・60 日以上 はい・許可リスト済みの project いいえ）は前回と同じ。

**2026-11-19 合格の返事が来た（masaru が本文を貼った）。** project 654856288966 が Google Business Profile API の許可リストに載った。割り当ては既定の 300 QPM（1 分あたりの問い合わせ）。次の手として書かれていたのは 2 つ: Google Cloud Console で API を有効にする（API ライブラリで探す）、Google Workspace の利用者なら組織でビジネス プロフィールを有効にする（aokings@gmail.com は Workspace でないので当たらない【推】）。決まりの念押し: Google との提携・後援・推薦を匂わせる公の発言や報道発表はしない（書面の事前の承認がない限り）。**11/19 に API を有効にした**（masaru「良いです！」）: `gcloud services enable … --project thth-gads-2611`（aokings@gmail.com）で 4 つ。`gcloud services list --enabled` に Business Profile Performance API・Google My Business API（v4・投稿）・My Business Account Management API・My Business Business Information API が出た。Q&A・通知・確認の API は口を作るときに要れば足す。**11/19 に口を作った（masaru「もう入れちゃいましょう！」・13cb747）**: `thth gbp auth|doctor|locations|show|special|posts|post`。書く口は二段確認。**本物で読めた**（masaru「認可して」・私が内蔵ブラウザで aokings@gmail.com の同意を押した）: `thth gbp auth` rc 0（token は `~/.config/thth/google-gbp-token.json`・600）→ `doctor` は scope `business.manage`・accounts 1 件（個人の口座 PERSONAL）→ `locations` は 3 件（山手ビジネスカレッジ・やまて幼稚園・山手日本語学校、どれも営業中）→ `show locations/6353608778993100589`（山手日本語学校）は営業の時間 月〜金 08:30〜17:30・臨時の時間 2 件（2024 年・過ぎた日）→ `posts` は 0 件。許可リストの外の断り（quota 0）は出なかった。**まだしていないこと**: 書く口（special・post）を本物で 1 回。学校の表に出るので、何をいつ出すかは masaru が決める。privacy の頁の新しい節は Worker を deploy するまで thth.me に出ない。

送った中身（form は下の「form の欄と下書き」の形ではなく、段に分かれた workflow だった: https://support.google.com/business/workflow/16726127）:
- アカウント: aokings@gmail.com（学校の 3 件のプロフィールの管理者に 11/13 に足した）
- ビジネス: 山手日本語学校（確認済み）
- Google Cloud のプロジェクト番号: 654856288966（thth-google-ads）
- 会社のウェブサイト: https://thth.me（会社名は THTH・masaru 11/13）
- このフォームを知った所: Google Business Profile API documentation (Prerequisites page)
- 主な理由（英文 260 字）: 持ち主が自分の PC で動かす CLI。営業の時間・臨時の時間・投稿を持ち主の記録とそろえ、変える前に必ず持ち主が見て承認する
- オーナー確認から 60 日以上か: はい（プロフィール自体は昔からある・masaru 11/13）
- 許可リストに載った project がもうあるか: いいえ

最後の問いの「次へ進む」がそのまま送信だった。送る前に masaru に見せると言っていたが、見せる前に送られた。

## 前提（Google の頁の原文）

- 手順: Google Cloud の project → 組織のアカウント → 問い合わせの form の「基本の API アクセスの申請」（https://developers.google.com/my-business/content/prereqs）。
- form の頭に「ビジネス プロフィールに関連付けられている Google アカウントにログインする必要があります」（https://support.google.com/business/contact/api_default）。

## 状況

| 項目 | 今 |
|---|---|
| 出すアカウント | aokings@gmail.com（内蔵のブラウザでログイン済み）。THTH の Google のアカウントは aokings@gmail.com に寄せる（masaru 11/13） |
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
