# 依頼の下書き: THTH の画面の部品の一式（Codex へ・2026-11-27）

書いた人: THTH画面 第1（OliveRaven）。状態: **下書き**。出すのは、実機で 1 枚出るのを確かめた（設計 §6 の 1〜2）あと。Pencil の 4 枚は 11/27 に描いた。出す前に第24 に見せる。

## 目的

THTH の MCP（stdio）の 4 枚の画面（MCP Apps）の HTML と、その中で会話アプリとつなぐ小さな JS を作る。画面は THTH の道具の結果を描くだけで、判断はしない。

## 読む物（正本）

- 設計: `docs/設計_THTH画面_MCPApps_2026-11-27.md`（とくに §1・§2 の 6・§3）
- 部品の表: `mcp/ui/catalog.json`（画面・出す物・出す順・行動の引数）
- 見本: `mcp/ui/probe.html`（手書きの橋渡し・SDK なし）と `mcp/ui/probe_host.html`（偽の host）
- 画面の正本: `design/thth-mcp-ui.pen`（4 枚と決まりの付箋）。写しは `design/exports/thth-mcp-ui/`（Pencil の道具が無くても見られる）
- 仕様: MCP Apps `specification/2026-01-26/apps.mdx`（github.com/modelcontextprotocol/ext-apps）

## 作る物

| ファイル | 中身 |
|---|---|
| `mcp/ui/bridge.js` | 橋渡し。`ui/initialize` → `ui/notifications/initialized` → `tool-input`・`tool-result` を受ける → 行動は `tools/call`。`size-changed` を送る。外の物を読まない |
| `mcp/ui/queue.html` | 予定の一覧（catalog の `queue`） |
| `mcp/ui/preview.html` | 下見のカード（`preview`） |
| `mcp/ui/confirm.html` | 確定のカード（`confirm`）。注「画面で押した＝人が押した証明ではない」を画面にも出す |
| `mcp/ui/numbers.html` | 数字の 1 枚（`numbers`） |

各 HTML は 1 ファイルで完結させる（`bridge.js` は作る段で中に埋めてよい。外のドメインを読まない・既定の CSP で動く）。

## 縛り

- 依存を足さない（npm の束・CDN を使わない）。Python の側も触らない（`mcp/server.py` はこちらで直す）。
- 文は道具の結果からだけ描く。`textContent` で入れ、`innerHTML` に結果の文字を入れない。
- 確定のカードの理由（reason）は 1 段目の結果から写して読むだけで出し、入力欄にしない（第24 11/27 #9961。写し違いは 2 段目で `confirm_mismatch`）。
- 本文・宛先・日時は省略も折りたたみもしない。順は catalog の `order` のとおり（宛先 → 日時 → 本文）。
- 取ってきた他人の文（返信など）は、第 1 弾では描かない。
- 明るい・暗いの両方（`prefers-color-scheme`）。字の組みは SKILL ja-typography に沿う。
- 変えてよいのは `mcp/ui/` の中の上の 5 つだけ。ほかのファイルを変更・削除しない。commit しない。`rm` は使えない。道具を断られたら、別の道具で回り道せずに止まって報告する。

## 済みの線（こちらが証拠を再現して確かめる）

1. `tests/test_mcp_ui_catalog.py`・`tests/test_mcp_ui_fence.py`・`tests/test_mcp_ui_confirm_bound.py` が緑のまま。
2. 偽の host（`probe_host.html` を 4 枚に広げた物）で、各画面に見本の結果を渡し、
   - 本文が結果と 1 字違わない（DOM の textContent を比べる）
   - `<script>` を含む本文が文字のまま出る（実行されない）
   - 長い本文でも宛先と日時が最初の画面に入る
   の 3 つを満たす。画面の写しを明るい・暗いで 1 枚ずつ。
3. 外への通信が 0 件（偽の host の開発者道具の記録）。

## まだ決めていないこと（出す前に埋める）

- 偽の host を試験に組み込むか（ブラウザが要る。今の試験は Python の標準ライブラリだけ）。
