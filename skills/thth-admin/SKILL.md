---
name: thth-admin
description: Read authenticated THTH administrator inventory, changes and token status; hand findings to a person.
---

開始時は `thth admin diff --since-last-read --json` を読み、変化があった account を `thth admin account <name> --json` で確認する。週1回 `thth admin tokens --json` を確認する。

読んだ結果から承認・投稿・設定変更をしない。必要な対処は根拠・欠測理由・観測時刻とともに人に渡す。記録が無い null は正常・停止・期限なしの証拠ではない。API probe は人が明示的に必要とした場合の CLI `--probe` のみ。

CLI は SSH のOSユーザー権限で実行する。読む位置の更新が明示的に依頼された場合だけ `thth admin diff --since-last-read --mark-read --by <名前>`。HTTP/MCPはcursorを書けない。

MCPの6道具は `thth_admin_inventory`, `thth_admin_account`, `thth_admin_log`, `thth_admin_tokens`, `thth_admin_release`, `thth_admin_diff`。MCPプロセスへ `THTH_REPORT_CREDENTIALS`（owner-private service credential JSONのパス）と `THTH_REPORT_TOKEN`（対応するbearer credential）を秘密として渡す。scope adminの有効期限・取消・SHA256照合と全accountのroot分離検査が通るときだけ一覧に表示される。値を会話・ログ・引数に出さない。

資格情報例の構造: schema_version=1, root=絶対パス, credentials=[{sha256, expires_at, revoked:false, accounts:{}, scope:"admin"}]。既存のscope未指定はuserのまま。管理者HTTPのtimerは保存済み観測だけでありlive稼働保証ではない。
