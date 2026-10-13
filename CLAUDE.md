# THTH — 入口の決まり

詳しい経緯は `docs/` の最新の申し送り（`docs/申し送り_*.md`）から読む。

## 誰が push・release・tag・deploy をしてよいか

- [THTH の開発セッション] main への push は Claude がしてよい（本番は変わらない。CI の全件試験が走るだけ）— 理由: 本番は release の枝で切り分けてある — 出所: masaru 2026-09-17（release の枝を分けた）— 期限: なし
- [THTH の開発セッション] release の枝を進める（VM が拾う）・tag `vX.Y.Z` の push（PyPI と MCP registry に出る）・`callback/` の Worker の deploy（`cd callback && npx --no-install wrangler deploy`、Worker が先・VM があと）は、**CI が緑になったあと、masaru が「出して」と言ったときに Claude が押す** — 理由: どれも本番か公開を変える — 出所: masaru 明言 2026-10-13（「それで書いていいよ」）— 期限: なし
- [運用のセッション・各レーン] THTH のコードは直さない。不具合や要望は `thth report file` か hub messenger で THTH の開発セッションへ — 理由: 同じ repo を開発と運用が同時に触る — 出所: 申し送り 2026-10-07 — 期限: なし

## 書き方の決まり

- release の push は `git push origin <sha>:refs/heads/release`。zsh で `$sha:release` と書かない（`:r` 修飾子が効いて壊れる）。
- 試験は `python -m pytest -q -n auto` の終了コードを見てから進む（`| tail` で隠さない）。
- 秘密（トークン・env）は読まない・出さない。
