# ダッシュボードの型（5.0 段 2）

会話の中に、利用者の LLM が描く 3 階層のダッシュボード。THTH は `thth dashboard … --json` で中身を全部出し、ここの型が描く。正本の設計は `docs/設計_5.0_シナリオと一覧_2026-10-28.md` §5（見本は `design/prototypes/`）。

## 何を叩いて、どの型に貼るか

| 言われたこと | 叩く命令 | 型 |
|---|---|---|
| ダッシュボード見せて（全体） | `thth dashboard --json` | `all.html` |
| 〇〇（メディア）を開いて | `thth dashboard <project> --json`（詳細まで要るなら `--full`） | `project.html` |
| キャンペーン〇〇を開いて | `thth dashboard <project> campaign <id> --json` | `campaign.html` |
| シリーズ〇〇を開いて | `thth dashboard <project> series <id> --json` | `series.html` |

貼り方: 型の `<script type="application/json" id="thth-data">{}</script>` の `{}` を、`--json` の答えとそっくり入れ替える。答えの中に `</` があれば `<\/` にする（script が途中で閉じないように）。ほかは変えない。型は `schema: "thth.dashboard/1"` と `level` を確かめてから描き、合わなければ「答えを貼ってください」とだけ出す。

遠くの道（thth.me の鍵）では `thth dashboard <口座> --json`。宣言（キャンペーン・シリーズ）が読めないときは `scenario: {"available": false, "cannot_say": "no_declarations"}` で、KPI・要対応・承認待ち・カレンダーは出る。

## LLM がしないこと

- 数字を HTML に書き込まない・型を書き換えない・色を足さない（会話の側の変数 `--surface-*`・`--text-*`・`--bg-warning` などだけで、明るい・暗いの両方に合う）。
- 並べ替えない・おすすめを足さない。要対応は `days` の大きい順（`sort_basis: "days_desc"`）のまま。
- 取れない数（`null`）を 0 と書かない。型は「—」と理由（`cannot_say` の符丁を言葉にしたもの）を出す。今の THTH で取れないのはブログ PV・本記事へのクリック。キャンペーン・シリーズの回ごとの成果は、段 1 の `scenario show` が繋がるまで「—」。
- 他人の本文（はてブのコメント・返信の本文）を足さない。`--json` にも入っていない。

## 押されたとき

押すところは全部 `actions[]` の `prompt` を `sendPrompt` に渡す。指示文は THTH が作り、`ダッシュボードから: [<project>/<種類>:<id>] …` の形（種類は `campaign`・`series`・`draft`・`account`・`delivery`、メディアだけなら `[<project>]`）。受けた LLM は:

- 角括弧の id をそのまま使う（同名で混ざらない・探し直さない）。
- 公開の承認は本人が承認の頁で押す。「承認へ」は `thth approve` の 1 段目を出して承認の頁の URL を貼るまで。
- 「戻せない操作です」が付いたもの（`irreversible: true`・宣言の書き換え・口座を止める・予約の取り消し）は、実行の前に 1 度確かめる。
- 取り下げ（retract）はダッシュボードには無い。頼まれたら今までどおり二段の digest で。

`sendPrompt` が無い環境（外の利用者の LLM など）では、押すと指示文がその場の枠に出て「コピー」で写せる。

## 型の直し方

- 見た目（並び・色・言葉）は型を直して commit するだけ（release は要らない）。
- 4 つの型の「共通」の印の間（見た目と JS の小道具）は 4 つとも同じに保つ（試験 `tests/test_v50_dashboard.py` が見る）。
- 新しい数字が要るときは `thth/dashboard_cli.py` の `--json` に鍵を足す（足すだけ・名前を変えない・消さない）。これは release が要る。
- 型が読む鍵は試験の `TEMPLATE_KEYS` に書いてある。鍵を読むのをやめる・足すときはそこも直す。

## `--json` の形（要点）

- 共通: `schema`・`report_type`・`level`（`all`・`project`・`campaign`・`series`）・`generated_at`・`project`・`as_of`（`generated_at`・`measured`・`schedule`・`events`・`scenario`）・`cannot_say`・`limitations`。
- `all`: `totals`（要対応・承認待ち・予約済み 14 日・公開済み 7 日の**本数だけ**）・`projects[]`（`mediums`・`kpi`・`scenario`・`week`・`first_attention`・`actions`）。
- `project`: `kpi`（`attention`・`pending_approval`・`scheduled_14d`・`published_7d`・`impressions_7d.by_account`）・`attention[]`（`kind`・`ref`・`title`・`since`・`days`・`next`・`actions`）・`pending_approval[]`・`scenario`・`campaigns[]`・`series[]`・`calendar[]`（14 日・`items[]` の `status` は `published`・`scheduled`・`draft`・`overdue`・`gap`）・`actions`。`--full` で `campaign_details[]`・`series_details[]`。
- `campaign`: `campaign`（`steps`・`outcomes`・`deliverables`・`history`・`next`・`actions`）。
- `series`: `series`（`stock`・`next_slot`・`empty_slots_14d`・`material`・`outcomes`・`items`・`slots_upcoming`・`actions`）。
