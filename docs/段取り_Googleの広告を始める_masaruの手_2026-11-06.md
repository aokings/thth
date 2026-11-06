# 段取り: Google の広告を始めるのに masaru の手が要る所（2026-11-06）

THTH開発 第13 がまとめた叩き台。本線 第12 と合意した分担（#7084）: AdSense は hub が読む（`seo-hub-260905`）、Google 広告は THTH が持つ（新しい Cloud project）。

## masaru の手が要る 3 つ

1. **新しい Google Cloud の project を作る（Google 広告の THTH 用）**
   - Cloud Console で project を作り、Google Ads API を有効にする。
   - OAuth の同意の画面を置く（外部向け・まずは Testing で自分だけ）。
   - 「Google Ads API Overview」から Explorer の段を申請する（ふつうは自動で通る）。
   - 注意: Testing のままだと refresh token が 7 日で切れる。外の人に配る段になったら、ブランド確認とスコープの確認を出す（審査は最大 10 日）。
   - 作業は画面の操作だけ。お金はかからない。Claude が 1 画面ずつ横で案内する。
2. **AdSense の読み取りを許す（hub の project）**
   - hub が使う gcloud のログインに、AdSense の読むだけのスコープ（`adsense.readonly`）を足して、aokings@gmail.com で 1 回同意する。
   - 打つコマンドは hub の実装が用意する。お金は動かない。
3. **広告費**
   - THTH は作るのは止めた状態、配信の開始と予算の引き上げは人の二段（Meta と同じ）。Google 広告の口ができても、masaru が二段で押すまでお金は動かない。
   - 決めておくこと: Google 広告の口座の 1 か月の上限（THTH の台帳の上限）。最初は 0 円（読むだけ・作るだけ）を推す。

## 推す順

- 2（AdSense）を先に。読むだけで軽く、自サイトの収益がすぐ見える。
- 1 は Google 広告の報告の口（段 2）を作る前まででよい。
- 3 は配信を始める段（段 4）の前に決める。それまでは 0 円。
