# THTH — 入口

## 地図

- 経緯と次の一手: `docs/` の最新の申し送り（`docs/申し送り_*.md`）。版ごとの中身は `docs/リリースノート_*.md`。
- 本番: VM（`ssh wt`）の `/srv/thth`（道具は `app/`・台帳は `accounts/`・状態は `state/`）。サイトは `callback/` の Worker（thth.me）。
- 担当: 開発は THTH の開発セッション（hub messenger の名乗り thth＝DustyCave）。各プロジェクトの運用は各レーン。

## 誰が push・release・tag・deploy をするか

- [THTH の開発セッション] main への push は Claude がしてよい（本番は変わらない。CI の全件試験が走るだけ）— 理由: 本番は release の枝で切り分けてある — 出所: masaru 2026-09-17 — 期限: なし
- [THTH の開発セッション] release の枝を進める（VM が拾う）・tag `vX.Y.Z` の push（PyPI と MCP registry）・Worker の deploy（`cd callback && npx --no-install wrangler deploy`、Worker が先・VM があと）は、CI が緑になったあと、masaru が「出して」と言ったときに Claude が押す — 理由: 本番か公開を変える — 出所: masaru 2026-10-13 — 期限: なし
- [運用のセッション・各レーン] THTH のコードは直さない。不具合や要望は `thth report file` か hub messenger で開発セッションへ — 理由: 同じ repo を開発と運用が同時に触る — 出所: 申し送り 2026-10-07 — 期限: なし

## 事故が起きた操作

- release の push は `git push origin <sha>:refs/heads/release`。zsh で `$sha:release` と書くと `:r` 修飾子が効いて壊れる。
- 試験は `python -m pytest -q -n auto` の終了コードを見てから進む。`| tail` を挟むと落ちても進んでしまう。
- VM の `pkill -f`／`pgrep -f` の型に自分の命令の文字列が入ると、ssh ごと止まる。型を絞る。
- `ssh wt '…'` の形はログインの設定を読まないので `thth` が PATH に無い。`env THTH_ROOT=/srv/thth /srv/thth/app/bin/thth` と書く。
