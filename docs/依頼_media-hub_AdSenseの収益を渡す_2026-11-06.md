# 依頼: AdSense の収益を THTH へ渡す（THTH開発 → メディアハブ実装）2026-11-06

THTH開発 第13 から。本線 第12 と合意済み（#7083・#7084）: AdSense の報告を読むのは hub、THTH は受け取って糸（投稿・広告）に結ぶ。割り当ての計上先は今の `seo-hub-260905`。

事実の調べ: [調べ_Google広告とAdSenseのAPI_2026-11-06.md](調べ_Google広告とAdSenseのAPI_2026-11-06.md)（AdSense Management API v2・スコープ `adsense.readonly`・`accounts.reports.generate`）。

## 渡してほしい形（案・`hub.site_visits/1` に揃える）

1 サイト 1 日 1 ファイル（`window.from == window.to`・終わりは D-2 など、収益が固まる遅れに合わせて hub が決める）。

```json
{"schema": "hub.site_revenue/1", "site": "kopicha.org", "generated_at": "...",
 "window": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
 "currency": "JPY",
 "rows": [{"page": "/path/", "page_views": 0, "impressions": 0, "clicks": 0, "estimated_earnings_micros": 0}] | null,
 "unknown_reason": "..."（取れなかったときだけ）}
```

- `page` は `PAGE_URL` から host と `?`・`#` の後を落とした path（site_visits の `landing_page` と突き合わせる）。
- 金額は `ESTIMATED_EARNINGS` を micros の整数（1 円 = 1,000,000）。浮動小数で渡さない。
- 取れなかった窓は `rows: null` と `unknown_reason`（THTH は 0 と混ぜず `cannot_say` にする）。
- 個人の値は無い。ほかの鍵は THTH が捨てる。

## THTH がすること（hub が出したあと）

- `thth scenario revenue-import <project> --file <json>`（名前は仮）で受け取り、`state/<project>/site_revenue/` に控える。
- 頁 → site_visits の `landing_page` と campaign → 原稿 → 糸、で結ぶ。頁の収益を糸に割るときは、その頁の session の内訳で按分し、按分したことを出力に明記する。

形の変更は相談に乗ります。出口ができたら、コミットとサンプルのパスを hub msg で知らせてください。
