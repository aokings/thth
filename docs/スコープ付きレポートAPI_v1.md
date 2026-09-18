# スコープ付きレポート API v1

V3-05 のローカル library facade。公開 HTTP、認証、サーバ提供、OS のファイル隔離は未実装。
信頼するホストが本人認証と利用者専用の実行環境の用意を済ませ、許可する account と project の対応を `ReportContext` に渡す。LLM やリクエストの自己申告から context を作ってはいけない。

```python
from thth.report_service import ReportContext, execute_report, render_markdown

context = ReportContext({"my-threads": "my-project"})
payload = execute_report(context, {
    "operation": "analytics_report",
    "project": "my-project",
    "window_days": 7,
    "min_n": 5,
})
markdown = render_markdown(payload)
```

対応操作は `analytics_report` と `operations_handoff` のみ。account/project はどちらか一方を必須とし、空文字・null・bool・両方指定を拒否する。分析のみ正整数 window_days/min_n を受ける。compare、study、任意CLI、投稿、承認、tenant/root/env/path/auth/outputなど未定義引数を拒否する。

context は渡された mapping をコピーして変更不可にする。project は context にある許可 account だけへ展開し、既存の全台帳を列挙する project API を呼ばず、単一 account API を呼ぶ。project 名が同じでも許可されない account は対象にならない。context の外にある資源は、存在の有無にかかわらず同じ `scope_unavailable` エラー。全要求の形式・scope・引数検証は core の読み取りより前に行う。

戻り値は `report_type=scoped_report_batch`、`schema_version=1`、`reports={account:既存payload}`。既存結果の計算・母数・欠測・鮮度の意味は変えない。Markdown は同じ batch payload から作り、根拠の JSON を全項目併記する。保存・artifact URL・ダウンロード token は発行しない。core の読み取り失敗は生の例外を返さず `report_unavailable` として batch 全体を失敗させる。

この制限は呼び出す account の選択を制御するもので、ファイルの所有権や symlink、同じ物理 repo の共用、プロセスの秘密、本人認証を検証するものではない。ホスト側の責任を代替しない。環境変数の切り替えは行わず、利用者専用プロセス/保存領域を前提とする。既存 payload の内容と制約は保持されるため、公開サービスの出力情報の検証も別途必要。
