# `callback/` —— `thth.me` の Worker（認可の受け口 ＋ 製品の紹介ページ）

Cloudflare Worker 1 本（`thth-callback`・custom domain `thth.me`）が 2 つを持つ。

- **認可の受け口** `/callback/` —— Threads の OAuth の戻り先（`src/index.js`）。
  登録済み flow は SQLite Durable Object が短時間預かり、VM が read key で1回取得する。未登録・保存失敗は従来の貼付画面へ戻す。`observability` は
  **有効にしない**（認可コードをログに残す動機を作らない）。Meta が要求する
  `/deauthorize`・`/data-deletion`・`/data-deletion-status` も Worker が持つ。
- **紹介ページ** `/`・`/llms.txt`・`/robots.txt` —— static assets（`public/`）。
  中身は **`python tools/build_site.py` が repo の正本から生成する**
  （`README.en.md`・`skills/thth/SKILL.md`・`llms.txt`）。**`public/` を手で
  編集しない**——正本を直して生成し直す。ずれていれば `tests/test_site.py` が落ちる。

設計 v2 §3「ドメイン」: `thth.me` ＝製品の入口（README 相当・導入・`llms.txt`・
認可ページは残す）、`api.thth.me` ＝泉の口（まだ無い）、`pip install thth` ＝道具。

## deploy（3 行）

```bash
cd callback && npx wrangler dev --local --log-level none  # 先に見る: / ・ /callback/?code=x ・ /llms.txt
python tools/build_site.py          # 正本から public/ を作り直す（repo の根で）
cd callback && npx wrangler deploy  # ← 本番反映。masaru か開発セッションが打つ
```

**`wrangler deploy` は本番反映**なので、実装のセッション（worktree）は打たない。
**打つのは masaru か、public 化のあとに masaru の一言を受けた開発セッション。**
`node_modules/` と `.wrangler/` は repo に入れない（`.gitignore` 済み）。


## Relay（2.11、未 deploy）

`src/worker.js` が既存のページ処理と `AuthRelay` DO を export する。
`POST /relay/<state>` に `{"read_key_hash":"<SHA-256 hex>"}` を渡して登録。
state と read key は VM が別々に32byte乱数からbase64url（43文字）で生成する。
read keyそのものは登録/認可URLへ載せず、取得時の Bearer にだけ使う。

- pending → ready → consumed を SQLite-backed synchronous KV と
  `storage.transactionSync()` で遷移。登録・受付の alarm も外側の storage transaction
  で同時に保存し、alarm故障なら行の変更も戻す。Workers KV は使わない。
- `GET /relay/<state>` は正しいkeyでのみ、1回だけ code/received_at を返す。
  未登録/空待ち/consumed/期限後は404、登録済みのkey欠落・不一致は401。
  回数は正しいkeyの試行だけ400回。超過後はexpired、600秒期限は延びない。
- codeは受付から300秒、かつ登録から600秒まで。期限内の重複登録・callback再注入を拒否。
  失効するとcode/read hashを除去し、最小markerも元の600秒期限で消す。
  VMは毎回新stateを使う。cleanup後の同state永久拒否は保証しない。
- 正規の `/callback` と `/callback/` だけがDOへ受付可能。旧子pathは貼付表示のみ。
  consume直後の通信断は再認可。返送の再試行でcodeをもう一度渡さない。
- IPの120回/分制限はCloudflare Rate Limiting bindingによる拠点単位の近似。
  費用は未測定。namespace_idは他Workerと共用しないことをdeploy担当が確認する。
- `/oauth/client-metadata.json` は404。Bluesky OAuth対応を装わない。

## ローカル検証

`npm ci && npm test`。MiniflareのSQLite実storage（fake clockだけtest subclass）と、
`wrangler dev --local --log-level none` の実HTTP/assets routing・runtime再起動を検証する。
固定dev依存はWrangler4.135.0が指定するMiniflare5.20260918.0-alphaと合わせた。
stable組Wrangler4.116.0/Miniflare4.20260730.0はworkerd最大日付2026-08-06で、
既存compatibility_date 2026-09-01を扱えないため使用しない。

ローカルも `--log-level none` を必須にする。debugアクセスログはstate入りURLを出すため使わない。
テストは別HOME・明示空env file・metrics offで実行し、実Cloudflare設定・秘密を自動読込しない。
全fixtureのcode/read keyは実行時生成し、Worker/runtime/stdout/stderrを走査する。
`observability.enabled=false` は認可経路の要件であり変更しない。
`npm test` は deploy/login/Cloudflare API を実行しない。public/ は従来どおり生成物で編集しない。
