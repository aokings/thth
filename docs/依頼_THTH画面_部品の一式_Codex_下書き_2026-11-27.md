# 依頼: THTH の画面の部品の一式（Codex へ・2026-11-27）

書いた人: THTH画面 第1（OliveRaven）。状態: **出した**（11/27）。実機の確かめ（設計 §6 の 1〜2）を待たずに出す。部品は口（stdio かリモート MCP か）に依らないので、実機で直す所が出たらそのとき直す。

## あなた（Codex）への頼み

THTH の MCP の 4 枚の画面（MCP Apps）の HTML を作ってください。画面は THTH の道具の結果を描くだけで、判断はしません。

作業の場所は、今あなたがいる作業木（`.claude/worktrees/mcp-ui-codex`）だけです。

## 読む物（正本）

1. 設計: `docs/設計_THTH画面_MCPApps_2026-11-27.md`（とくに §1・§1-4 演出・§2 の 6・§3・§5）
2. 部品の表: `mcp/ui/catalog.json`（画面・出す物・出す順・行動の引数・読むだけの欄）
3. 画面の正本の写し: `design/exports/thth-mcp-ui/` の PNG 5 枚（A 予定の一覧・B 下見・C 確定の押す前と押したあと・D 数字）。色と字は写しに合わせる（背景 #FFFDF8・文字 #26221C・副 #6B645A・淡 #9A9286・線 #ECE6DA・暖 #F0A040・予約の青 #2C5A8C・字は system-ui と Hiragino Sans、数と時刻は等幅）
4. 見本: `mcp/ui/probe.html`（手書きのつなぎ・演出・消える時・画面から道具を呼ぶ釦）と `mcp/ui/probe_host.html`（偽の host・仕様の既定の CSP を掛けて読む）
5. 結果の形: 画面は `structuredContent` を描く。中身は CLI の `--json` と同じ形にする予定。形は次から読み取る（読むだけ）:
   - 予定の一覧: `thth/cli.py` の `cmd_queue`（`thth queue --json`）と `thth_board`
   - 下見: `thth preview` の出力（`thth/cli.py`）
   - 確定の 1 段目: `thth/server_writes.py` の `retract_request` の `confirm_required` の答えと、`thth/retract_cli.py` の 1 段目の `--json`（`reason` を含む）
   - 数字: `thth_summary` と `analytics_report` の実装
   形が読み切れない欄は、画面の側で「無ければ出さない」にして、何を推したかを報告に書く。
6. 仕様: MCP Apps `specification/2026-01-26/apps.mdx`（github.com/modelcontextprotocol/ext-apps）。つなぎは `ui/initialize` → `ui/notifications/initialized` → `ui/notifications/tool-input`・`tool-result` を受ける → 行動は `tools/call` → `ui/notifications/size-changed`。ネットに出られなければ見本の `probe.html` のつなぎをそのまま広げる。

## 作る物（`mcp/ui/` の中だけ）

| ファイル | 中身 |
|---|---|
| `queue.html` | A 予定の一覧（catalog の `queue`）。時刻を大きく・口座・状態の札・絞り（画面の中で）・1 件を開く（`thth_preview` を画面から呼ぶ）。次の結果で減った行は消える時の動きと痕の 1 行 |
| `preview.html` | B 下見（`preview`）。宛先 → 日時 → 本文（全文）→ 文字数と上限 |
| `confirm.html` | C 確定（`confirm`）。押す前は静かに・押したあとは判子の 1 行に縮む。理由は読むだけ（入力欄にしない・第24 11/27 #9961。写し違いは 2 段目で `confirm_mismatch`）。「画面で押しても、人が押した証明にはなりません。確かめはサーバの digest です。」を画面にも出す。確定は `thth_ui_confirm` を `{tool, account, target, digest, reason}`（1 段目の結果の写し）で呼ぶ |
| `numbers.html` | D 数字（`numbers`）。期間の切り替え（7・28・90 日）は `analytics_report` を画面から呼ぶ。期間・母数・欠測を添える |
| `screens_host.html` | 偽の host。4 枚を切り替えて開き、見本の結果を渡す（`probe_host.html` を広げる）。見本には「`<script>alert(1)</script>` を含む本文」「2,000 字の本文」「欄の欠けた結果」を入れる |

- 各画面は **1 ファイルで完結**させる（host は `resources/read` で HTML の文字だけを受けるので、別ファイルの JS や CSS は読めない）。
- つなぎの JS は 4 枚とも**同じ塊**を `/* thth-bridge:begin */` と `/* thth-bridge:end */` の間に置く（こちらで「4 枚の塊が 1 字違わない」の柵を足す）。

## 縛り

- 依存を足さない（npm の束・CDN・外のフォントを使わない）。外の URL を読まない（既定の CSP では読めない）。Python の側は触らない（`mcp/server.py` はこちらで直す）。
- 文は道具の結果からだけ描く。`textContent` で入れる。`innerHTML` を使わない。
- 本文・宛先・日時は省略も折りたたみもしない。順は catalog の `order`。
- 取ってきた他人の文（返信など）は描かない。
- 演出は設計 §1-4 のとおり: 芯は CSS と SVG・GIF は使うなら data: の 1 KB 前後だけ・確定の押す前は可愛くしない・消えた物は痕を残す・**中身を初めは透明にしない**（動きは出だしだけ上書きする `backwards`）・`@media (prefers-reduced-motion: reduce)` で動きを止める。
- 明るい・暗いの両方（`prefers-color-scheme`）。
- 変えてよいのは `mcp/ui/` の上の 5 つのファイルだけ。ほかのファイルを変更・削除しない。**commit しない**。`rm` は使えない。道具や権限を断られたら、別の道具で回り道せずに止まって報告する。

## 済みの線（こちらが証拠を再現して確かめる）

1. `python3 -m pytest -q -p no:cacheprovider tests/test_mcp_ui_html.py tests/test_mcp_ui_catalog.py tests/test_mcp_ui_fence.py` が緑（`test_mcp_ui_html.py` は `mcp/ui/*.html` のうち `_host.html` で終わらない物を全部見る）。
2. `screens_host.html` で 4 枚それぞれに見本を渡したとき:
   - 本文が結果と 1 字違わない
   - `<script>` を含む本文が文字のまま出る（実行されない）
   - 2,000 字の本文でも、宛先と日時が最初の画面に入る
   - 欄の欠けた結果で画面が壊れない（誤りの記録が出ない）
3. 外への通信が 0 件。

## 報告（最後の答えに書く）

- 作ったファイルと行数。
- 結果の形で推した所（読み切れなかった欄）。
- 済みの線 1 を自分で回した結果（打った命令と出力の要点）。
- 気づいた設計の穴（あれば）。
