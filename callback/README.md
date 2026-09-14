# `callback/` —— `thth.me` の Worker（認可の受け口 ＋ 製品の紹介ページ）

Cloudflare Worker 1 本（`thth-callback`・custom domain `thth.me`）が 2 つを持つ。

- **認可の受け口** `/callback/` —— Threads の OAuth の戻り先（`src/index.js`）。
  受け取ったコードを画面に出すだけで、どこにも送らない。`observability` は
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
cd callback && npx wrangler dev     # 先に見る: / ・ /callback/?code=x ・ /llms.txt
python tools/build_site.py          # 正本から public/ を作り直す（repo の根で）
cd callback && npx wrangler deploy  # ← 本番反映。masaru か開発セッションが打つ
```

**`wrangler deploy` は本番反映**なので、実装のセッション（worktree）は打たない。
**打つのは masaru か、public 化のあとに masaru の一言を受けた開発セッション。**
`node_modules/` と `.wrangler/` は repo に入れない（`.gitignore` 済み）。
