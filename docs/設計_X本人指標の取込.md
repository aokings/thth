# X本人公開指標の純粋な変換（開発用）

2026-09-18。`thth.x_metrics.normalize_owned_metrics` は渡された直接X lookup応答を変換するだけの純粋関数。network・ファイル・認証・API登録・collectへの接続は行わない。実APIへの接続条件は未完了。実アカウント試験も行っていない。

## 入出力

`response`、接続主体の `connected_user_id`、取得日時 `observed_at`、検証用現在日時 `now` を必須とする。`provider` は `x_direct` のみ。日時はtimezone付き完全日時文字列で、UTCへ正規化。呼出元が時刻と主体を信頼できる状態で供給する責任を持つ。この関数は本人認証の証明を作らない。

応答 `data` は単一Post objectまたは最大100件の配列。IDはASCII正整数文字列（最大20桁）をローカル契約とする。各 `author_id` と接続主体の一致、`created_at <= observed_at <= now` を要求する。同じIDが複数ある場合、異なる所有者との重複も含め全該当行を除外する。混合応答の正しい行は残し、除外は入力indexと固定理由のみ。別人のIDや本文を除外記録へ返さない。

返却は `schema_version=1` / `report_type=owned_public_metrics`。投稿ID・UTC投稿/観測時刻・所有照合の根拠・公開指標・入力/採用/除外/APIエラー件数。coverage.completeは渡された応答の行除外/APIエラーがない意味に限定し、指標欠測なし、アカウント全件網羅、API成功認証の意味ではない。

| 出力 | public_metrics内の元項目 | 意味 |
|---|---|---|
| views | impression_count | 投稿表示回数。ユニーク人数・動画再生数ではない |
| likes | like_count | いいね数 |
| replies | reply_count | 返信数 |
| reposts | repost_count / retweet_count | リポスト数。両方ある場合は一致が必要 |
| quotes | quote_count | 引用数。リポストへ混ぜない |
| bookmarks | bookmark_count | ブックマーク数 |

指標は0以上の整数のみ。bool、float（NaN/Infinityを含む）、文字列等は型不正、負数はnegative、2^53−1超は安全な数値受け渡しの範囲外としてnullと理由にする。この上限はX公称上限ではない。欠測はnull/missing。各値に元フィールド名を添付する。public_metricsの構造不正は全指標null。未知項目・本文・username・展開User・非公開指標・生のAPIエラーは返さない。入力を変更しない。

## 境界

- 根投稿/返信の分類は行わない。本人投稿であることと根投稿であることは別。
- 24時間測定の認定や比較採用は行わない。後段が実際の時刻差を判定する。
- 部分APIエラーは件数だけ返す。data欠落を空の正常応答に変換しない。
- Buffer等は取得時刻・更新頻度・指標定義が異なるため、この契約へ流用しない。
- 元応答を関数へ渡す前/後の呼出元の保持・ログ・LLM提供は保証しない。transformによって保持/外部提供の許諾が成立するわけではない。
- 接続開始前にアプリ用途・OAuth・費用上限・削除/変更追随・外部提供/表示条件を確定する。既存Git保存先へX記録を追加してよいという実装ではない。

## 一次資料と確認範囲

すべて取得2026-09-18。文書の取得とローカルfixtureテストであり、実API応答の測定ではない。更新表示は下記Agreement以外未確認。

- [Get Posts by IDs](https://docs.x.com/x-api/posts/get-posts-by-ids): Response 200 data配列 / author_id / created_at / public_metrics。現行schemaはrepost_count。
- [Get Posts by ID](https://docs.x.com/x-api/posts/get-post-by-id): Response 200 data単一object。
- [Metrics](https://docs.x.com/x-api/fundamentals/metrics): Metric types / Post metrics。説明表はretweet_countと記載。上のschemaとの差をaliasとして明示し、相違値を推測で統合しない。公開impressions・likes・replies等と非公開指標の区別を確認。
- [Developer Agreement](https://docs.x.com/developer-terms/agreement): Last Updated April 27, 2026、I.12、III.A(d)/(k)。ID・派生を含む対象性、第三者提供制限とモデル訓練禁止。非学習推論への包括許諾は確認できない。
- [Developer Policy](https://docs.x.com/developer-terms/policy): Content compliance。保存された削除/変更済み内容への追随を要求。この変換関数だけでは履行できない。

検証: 所有不一致、混合不正行、ID重複、時刻境界、未知provider、欠測/型不正/巨大整数、本文/生エラーcanaryの非混入、alias競合、入力非変更をオフラインテストする。

### 開発版の欠測・全件error契約

`coverage.metrics_missing_records`は採用行のうち6公開指標のいずれかがnullである行数（欠落・型不正・alias不一致等を含む）。`complete=true`でもこの値が正なら指標は揃っていない。行の採用完了と指標の完備を分けて読む。

`data`欠落＋`errors`のみは現在も`XMetricsError("invalid_data")`で拒否する。全件失敗時の実API応答形は一次資料で確認できておらず、これをXの仕様とは主張しない。このオフライン変換器の現行入力契約として固定し、実接続前に一次資料・実応答fixtureで再確認する。`data=[]`＋有効な`errors`配列は採用0件・APIエラー件数・`complete=false`を返す。生errorの文言はどちらも返さない。
