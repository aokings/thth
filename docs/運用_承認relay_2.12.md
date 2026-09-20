# 承認 relay の管理（masaru 向け）

これは招待する側のサーバ設定です。利用者自身の VM やアプリは要りません。
2.12 の承認ページと利用者の書く口は組み合わせて導入します。
この文書の追加は、本番への配布や接続試験が完了したという意味ではありません。

## 署名鍵と承認 secret

サーバには OpenSSL 3.x が必要です。thth は `/opt/homebrew/bin/openssl`、
`/usr/bin/openssl`、`/usr/local/bin/openssl` の順に 3.x を探します。
見つからなければ署名せず `openssl_3_required` で止まります。
外部 Python パッケージや自作の公開鍵暗号は使いません。

`thth admin relay-key init --by masaru` は RSA 3072 bit の署名秘密鍵を
`~/.config/thth/apps/relay-signer.key` に mode 600 で新規作成します。
親 apps ディレクトリは本人所有の mode 700 が必要です。既存の鍵は上書きしません。
表示する `APPROVAL_PUBLIC_KEY` は公開鍵だけです。配布担当がこれを Worker の同名 secret に設定します。
初回表示を失った場合や鍵の作成後に公開鍵出力が失敗した場合は、
`thth admin relay-key show --by masaru` で同じ公開鍵だけを再表示できます。
`show` は鍵や変更ログを更新せず、鍵が無い場合や安全でない置き場なら拒否します。
秘密鍵は Worker に置きません。`THTH_APPS_DIR` による既存のプロジェクト外の置き場指定も使えます。

`thth admin approver set <person> --by masaru` は 43 文字のランダムな承認 secret を生成し、
管理者の tty に一度だけ表示します。stdout、ログ、引数へは渡しません。
tty が無ければ生成・登録前に拒否します。`--stdin-out` はありません。
管理者が別経路で本人に渡し、本人はパスワードマネージャに保存します。
本人は承認ページで内容と操作を確認し、この secret を自動入力して押します。LLM へは渡しません。
短い数字の PIN は採用しません。phishing 耐性や passkey と同じ本人確認は主張しません。

再度 `set` すると新しい世代になり、以前の承認ページは無効です。
`thth admin approver revoke <person> --by masaru` は失効、
`thth admin approver unlock <person> --by masaru` は連続失敗のロック解除です。
5 回の連続失敗で本人単位のロック、同じ承認ページでの 5 回の失敗でそのページの失効となります。
正しい secret の照合で本人の連続失敗回数は戻ります。

遠隔登録とローカル変更ログは単一トランザクションではありません。
`approver_remote_changed_audit_unconfirmed` は遠隔変更済みでローカル記録が不確か、
`approver_remote_changed_delivery_failed` は遠隔変更済みで tty への受け渡し失敗です。
後者や通信結果不明では、変更が無かったと判断せず管理者が新しい secret を再発行してください。
登録成功後のログ失敗でも、生成した secret は開いていた tty へ一度だけ渡します。

## 保存と一回限りの受領

本文・表示情報は最大 600 秒の論理期限で、承認または失効で稼働中の session から除きます。
管理者の登録では secret 本体を Worker に送りません。本人のフォーム送信時だけ照合に使い、保存しません。32 byte の salt と PBKDF2-SHA256 100,000 回の verifier（Workers の WebCrypto が受け付ける上限。secret は 32 byte の乱数なので伸長回数は本人確認の強さを左右しない） を
本人専用 DO に置き、本文の session DO と分けます。
同時承認、受領、失効の判定は本人 DO の世代と一回限りの grant で照合します。
本文や URL、secret、署名を観測ログへ出さず、ページには no-store と no-referrer を設定します。
SQLite DO の PITR は 30 日あるため、この論理削除をバックアップを含む物理消去とは説明しません。

VM は毎回新しい承認 token と読取鍵を生成します。期限後の同じ token の永久再利用拒否はしません。
署名は method、path、operator/job、対象、操作、時刻、nonce、送信 bytes の hash に結合します。
有効な署名でも同じ nonce の再送を拒否します。通信失敗後は未変更と決めつけません。
受領を一回返した後にサーバが停止すると、結果が不明になることがあります。無条件で再公開しません。

承認公開ページ、署名検証入口、認証済み job は OAuth relay と別のレート制限です。
署名入口は同一送信元で毎分 600、job は対象ごと毎分 180、公開ページは送信元ごと毎分 120 です。
2 秒 poll の 4 flow が既存 OAuth の枠を消費しないことをローカルで確認します。
上限時は 429 と retry-after を返し、永久に待たず元の 600 秒の期限を保ちます。
本番の CPU 枠・実機の OpenSSL・実利用者の通しは配布担当の検証対象です。
