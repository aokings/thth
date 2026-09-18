# 非公開レポートHTTP v1（開発版）

2026-09-18。`serve-reports` は専用Unix環境のレポートを、機械クライアントへ返すための限定的な入口です。公開済み2.4.0にはありません。stdlib `HTTPServer` による直列処理で、インターネット向け本番サーバ、本人認証、投稿承認、OSサンドボックスの完成を意味しません。

## 起動前の配置

利用者ごとにOS identityまたはコンテナと保存領域を分けます。専用rootは実行uid所有の0700、`THTH_ROOT` は同じ絶対パスに固定します。account定義はその `accounts/` に置き、repo/queue/replies/state/data/env/token参照先も同じroot内に収めます。root直下・読取対象dir・参照祖先のsymlink、特殊ファイル、hardlinkは拒否します。別利用者のrootを同一プロセスで切り替えて扱う入口はありません。

設定ファイルは信頼する管理者だけが書ける場所に置き、実行uid所有の0600（または0400）とします。最終ファイルのsymlinkを拒否します。親ディレクトリの所有と書込権限も管理者が管理します。設定・root・上位ディレクトリを利用者の投稿やLLMが書き換えられる配置にしないでください。

設定の形（以下は書式説明で、このまま使える資格情報ではありません）:

```json
{
  "schema_version": 1,
  "root": "/srv/thth/user-a",
  "credentials": [{
    "sha256": "<ランダムなBearer tokenのSHA-256、lowercase hex 64文字>",
    "expires_at": "2026-09-30T00:00:00+09:00",
    "revoked": false,
    "accounts": {"my-threads": "my-project"}
  }]
}
```

tokenは管理者が `secrets.token_urlsafe(32)` 以上の乱数で生成し、安全な秘密配布経路で利用者に渡します。サーバ設定にはdigestだけを保存します。短いパスワードをSHA-256にする運用には対応しません。受付文字はURL-safe ASCII、長さ43〜128文字。tokenそのものをコマンド引数、URL query、git、通常ログに入れないでください。これはサービス用資格情報で、人物の本人確認や公開承認ではありません。

```sh
THTH_ROOT=/srv/thth/user-a thth serve-reports --credentials /srv/thth-private/user-a.json --socket /srv/thth-private/report.sock
```

Unix socketで待ち受けます。TCP明示時は`127.0.0.1`にだけbindします。遠隔から使う場合はTLS終端と追加アクセス制限、要求制限を持つ管理下のproxyが必要です。proxyはUnix socketなら`Host: localhost`、TCPなら `Host: 127.0.0.1:<実port>` （または `localhost:<実port>`）を転送し、Authorizationをログに残さない設定にします。proxy設定・証明書・本番配置は本機能では作成しません。loopback通信自体にTLSはありません。

## 要求と応答

- `GET /health` は資格情報設定が読めるHTTPプロセスの生存のみ（認証不要）。SNS疎通、台帳の新鮮さ、投稿可能、通知配達、root検証合格の意味を持ちません。
- `POST /report` に `Authorization: Bearer <token>`、`Content-Type: application/json`、`Content-Length` を付けます。bodyはスコープ付きレポートAPIのJSONです。
- 例: `{"operation":"analytics_report","account":"my-threads","window_days":7,"min_n":5,"compare_previous":true}`。`operations_handoff` も利用可能。許可project指定は資格情報に含むaccountだけへ展開します。
- studyファイル、任意パス、tenant、環境変数、投稿、承認は受付けません。
- 既存 `scoped_report_batch` JSONを返します。元レポートの根拠・欠測・鮮度の限定は変えません。利用者自身のレポートデータを返すため、公開してよいJSONとは見なさないでください。
- 401は欠落/不一致/期限切れ/失効を区別しない `unauthorized`。400は不正要求や `scope_unavailable`、413は大きすぎるbody、415はJSON形式でない要求、503は設定/環境/レポート生成の不備です。生の例外、パス、tokenをエラーに返しません。

設定は毎要求読み直します。サーバ時計の現在時刻が期限以上なら拒否し、`revoked: true`、digest削除、scope変更、設定読取失敗は次要求から反映します。管理者は0600の新ファイルを同じ信頼ディレクトリで作って原子的置換してください。既に認証して実行中の1要求は取り消しません。起動時と認証後には専用rootのpreflightを行います。未認証要求ではaccount設定や台帳を読みません。

全応答 `Cache-Control: no-store` / `nosniff`、アクセスログ出力なし、CORSなし。Origin付きPOSTは拒否します。HostはUnix socketでlocalhost、TCPでloopback名と実portだけ、重複Authorization/Content-Length/Host、Transfer-Encoding、圧縮body、重複JSONキー、非有限数、不正JSONを拒否します。body最大16KiB、設定最大128KiB、資格情報最大100件、window_days最大3650/min_n最大100000です。1 recvあたり5秒・要求全体（要求行＋header＋body）10秒で、台帳処理時間や応答送信速度を制限する仕組みではありません。

## 検証範囲と残る運用条件

localhostの実HTTPテストで、失効/期限切れ/スコープ変更と未認証時のcore未読取、横断拒否、不正HTTP/JSON、設定不備、環境不一致を検証します。設定と台帳の書込、git fetch、SNS呼出、承認は行いません。

preflightは設定誤りを検出するもので、確認後にファイルを差し替える競合やOSアクセス権を防ぐものではありません。OS identity/mount分離、trustedディレクトリ所有、proxyの速度/同時接続/body制限、バックアップ・退出、監視、credential配布、時計同期は配置者の責任です。直列stdlibサーバのため、1要求の遅い台帳処理は後続要求を待たせます。人のログイン・セッション発行/管理画面・公開用TLS基盤・一般提供は未実装です。[Python公式資料](https://docs.python.org/3/library/http.server.html)も `http.server` を本番利用向けとしていません。


## Unix socketと読み込みdeadline

既定の運用方式は`--socket <path>`で管理者がsocketパスを明示する方式。親dirは実行UID所有の0700、socketはlisten前に0600とする。既存pathは削除・上書きせず起動を拒否し、停止時は自身が作ったsocketだけ回収する。クライアントはUnix socketに接続し`Host: localhost`を送る。例: `curl --unix-socket /srv/thth-private/report.sock http://localhost/health`。

TCPは`--tcp-port <port>`を明示した場合のみ。portは全ユーザー共有で、多利用者ホストでは停止中・再起動の隙に別ユーザーが先取りしてBearer tokenを取得できる。信頼できない別ユーザーがいるホストではUnix socketを使う。TCP loopbackにはTLSも接続相手の本人確認もない。

1 recvあたりのタイムアウトは5秒。これとは独立にaccept直後のhandler開始から要求行・header・bodyを合計10秒で遮断するwatchdogを持つ。少量ずつ送り続けても延長しない。直列処理なので読み込み待ちは1接続分までだが、処理・台帳走査自体の実行時間制限ではない。読み込み終了またはhandler終了でwatchdogを解除し、後続接続を誤って切断しない。
