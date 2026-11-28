#!/usr/bin/env python3
"""画面（MCP Apps）を段 K のリモート MCP（thth.me/mcp）に載せる部品を作る。

  python3 tools/render_mcp_ui.py          # callback/src/mcp-ui.js を書き出す
  python3 tools/render_mcp_ui.py --check  # 書き出す物と今の物が 1 字も違わなければ rc 0

元: 部品の表 `mcp/ui/catalog.json` の `remote_tools`（リモートの道具の名）と、その画面の HTML `mcp/ui/<id>.html`。
`remote_tools` が空の画面は載せない。HTML は stdio と同じ物をそのまま文字として入れる（作り直さない）。
決め: 第24 11/28（下調べ_画面をリモートMCPに足す の 4 つのずれを推しの形で）。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "mcp", "ui", "catalog.json")
OUT = os.path.join(ROOT, "callback", "src", "mcp-ui.js")


def render() -> str:
    with open(CATALOG, encoding="utf-8") as f:
        catalog = json.load(f)
    screens = []
    for screen in catalog["screens"]:
        tools = screen.get("remote_tools") or []
        if not tools:
            continue
        with open(os.path.join(ROOT, "mcp", "ui", screen["id"] + ".html"), encoding="utf-8") as f:
            html = f.read()
        screens.append({"id": screen["id"], "uri": screen["uri"], "title": screen["title"], "tools": tools, "html": html})
    body = {"schema": "thth.mcp-ui/1", "version": catalog["version"], "mimeType": catalog["mimeType"], "screens": screens}
    return ("// 生成物: `python3 tools/render_mcp_ui.py` が mcp/ui/catalog.json の remote_tools と mcp/ui/<画面>.html から書き出す。\n"
            "// 手で直さない。ずれは tests/test_mcp_ui_remote.py が赤にする。\n"
            "export default " + json.dumps(body, ensure_ascii=False, indent=1) + ";\n")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    text = render()
    if argv == ["--check"]:
        try:
            with open(OUT, encoding="utf-8") as f:
                same = f.read() == text
        except FileNotFoundError:
            same = False
        if not same:
            print("callback/src/mcp-ui.js が mcp/ui の表と HTML と食い違います（python3 tools/render_mcp_ui.py で作り直す）",
                  file=sys.stderr)
            return 1
        return 0
    if argv:
        print("使い方: python3 tools/render_mcp_ui.py [--check]", file=sys.stderr)
        return 2
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
