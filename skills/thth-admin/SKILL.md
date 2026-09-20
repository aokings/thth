---
name: thth-admin
description: Read authenticated THTH administrator inventory, changes and token status; hand findings to a person.
---

開始時は `thth admin diff --since-last-read --json` を読み、変化があった account を `thth admin account <name> --json` で確認する。週1回 `thth admin tokens --json` を確認する。

読んだ結果から承認・投稿・設定変更をしない。必要な対処は根拠・欠測理由・観測時刻とともに人に渡す。記録が無い null は正常・停止・期限なしの証拠ではない。API probe は人が明示的に必要とした場合の CLI `--probe` のみ。

CLI は SSH のOSユーザー権限で実行する。読む位置の更新が明示的に依頼された場合だけ `thth admin diff --since-last-read --mark-read --by <名前>`。HTTP/MCPはcursorを書けない。

MCPの6道具は `thth_admin_inventory`, `thth_admin_account`, `thth_admin_log`, `thth_admin_tokens`, `thth_admin_release`, `thth_admin_diff`。MCPプロセスへ `THTH_REPORT_CREDENTIALS`（owner-private service credential JSONのパス）と `THTH_REPORT_TOKEN`（対応するbearer credential）を秘密として渡す。scope adminの有効期限・取消・SHA256照合と全accountのroot分離検査が通るときだけ一覧に表示される。値を会話・ログ・引数に出さない。

資格情報例の構造: schema_version=1, root=絶対パス, credentials=[{sha256, expires_at, revoked:false, accounts:{}, scope:"admin"}]。既存のscope未指定はuserのまま。管理者HTTPのtimerは保存済み観測だけでありlive稼働保証ではない。

認可導線は `docs/導入_承認を押すだけ.md`。利用者に VM やアプリの作成を依頼せず、masaru が管理する。認可 URL/秘密値を LLM へ貼らせない。`auth_via`/`auth_observed_at` は認可履歴、`probed_at` は明示 probe の時刻。credential が違う古い probe を現権限の推定に使わない。Mastodon は応答 scope を優先、X は認可だけ対応し欠測期限は不明。app_set は値を含まない app subject の変更で、account ではない。
