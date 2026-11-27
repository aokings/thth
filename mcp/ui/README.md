# mcp/ui（THTH の画面・MCP Apps）

設計の正本: [docs/設計_THTH画面_MCPApps_2026-11-27.md](../../docs/設計_THTH画面_MCPApps_2026-11-27.md)。

## いま有る物（確かめ用・本物の道具は呼ばない）

| ファイル | 役 |
|---|---|
| `probe_server.py` | 最小の stdio のサーバ。道具 `thth_ui_probe`（見本の予定 3 件・偽のデータ）と画面 `ui://thth/probe` だけ。`THTH_UI_PROBE_LOG=<ファイル>` を付けると、受けた `initialize` の client の能力を 1 行ずつ残す |
| `probe.html` | 画面 1 枚。橋渡しは手書きの JS（SDK なし）。`ui/initialize` → `ui/notifications/initialized` → `ui/notifications/tool-result` を受けて一覧を描く |
| `probe_host.html` | 偽の host。`probe.html` の橋渡しだけを確かめる（本物の host の代わりにはならない） |

## 確かめたこと（2026-11-27）

- 手元で JSON-RPC を流した: `initialize`（`resources` の能力を返す）・`tools/list`（`_meta.ui.resourceUri`）・`tools/call`（`structuredContent`）・`resources/list`・`resources/read`（`text/html;profile=mcp-app`）の 5 つが期待どおり。記録に client の能力（`extensions."io.modelcontextprotocol/ui"`）が残る。
- 偽の host（`python3 -m http.server` で `probe_host.html` を開く）で、橋渡しが通り一覧が描かれた（画面の写しあり）。
- **未確認**: 本物の host（Claude のデスクトップ）で画面が出るか。

## 本物の host で確かめる手順（設計 §6 の 1〜2）

Claude のデスクトップの `claude_desktop_config.json` の `mcpServers` に 1 本足して、アプリを起こし直す:

```json
"thth-ui-probe": {
  "command": "python3",
  "args": ["/Volumes/NexDev/Developer/thth/.claude/worktrees/mcp-ui/mcp/ui/probe_server.py"],
  "env": {"THTH_UI_PROBE_LOG": "/Users/masaru/Documents/thth-ui-probe.log"}
}
```

会話で「thth_ui_probe を呼んで」と頼み、(1) 記録に `io.modelcontextprotocol/ui` が出るか、(2) 会話の中に表が出るか、を見る。済んだら 1 本を外す。
