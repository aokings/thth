# 例の台帳（見本・架空）

`thth register` の台帳の形の見本。どれも**架空**で、実在の店・メディアではない（計画 `docs/計画_実店舗_2026-11-13.md`・masaru 2026-11-13「本物の実店舗を待たずに作る」）。

- `media-sample/`: 自前のメディアの台帳の見本（事実の層と思いの層）。
- `cafe-sample/`: 実店舗の見本 1（飲食店「喫茶 みほん」）。
- `goods-sample/`: 実店舗の見本 2（物販「雑貨 ためし」）。

試験（`tests/test_register_*.py`）はこのフォルダを一時の置き場に写して使う。ここのファイルを直すと試験の結果も変わる。

使い方の例:

```
thth register add media-sample --path registers.example/media-sample --project sample --kind media --by <名前>
thth register check media-sample
thth register show media-sample
```

1 行 = 1 枚の .md。頭の 7 つの欄（id・kind・status・source・confirmed・review_by・public）が要る。この README のように頭の無い .md は読み物として数えるだけ。

**本文は外に出る材料**として持ち主の LLM に渡る（`thth register use`）。既定で出さない欄（店の住所と電話・臨時の休みの理由・外の場所の URL と管理する人）の値は、頭のその欄にだけ書き、本文には書かない。店・臨時・外の場所の行は、`use` が本文を返さない（持ち主が `public_fields` に `body` と書いたときだけ返す）。

店の見本の `外の場所/` は、店の情報が載っている外の場所（Google マップ・食べログなど）ごとの 1 枚（kind `place`・段 4）。URL は架空。THTH はその頁を読まず、管理画面にも入らない。`thth register paste` で貼る文を出し、持ち主が貼ったら `thth register pasted` で記録する。
