"""画面をリモート MCP（thth.me/mcp）に載せる部品の柵（段取り 11/30）。

- `callback/src/mcp-ui.js` は `tools/render_mcp_ui.py` が mcp/ui の表と HTML から作る生成物。1 字でもずれたら赤。
- 載せるのは `remote_tools` のある画面だけ（今は queue と thth_board）。
- 画面はリモートの包み（{command, rc, output, workspace}）を開き、pending は thth_result で待ち、下見の道具の無いリモートでは「開く」を出さない。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "render_mcp_ui.py")


def _render():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import render_mcp_ui
        return render_mcp_ui.render()
    finally:
        sys.path.pop(0)


def test_生成物が表とHTMLに合う():
    r = subprocess.run([sys.executable, TOOL, "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_載せるのはqueueとthth_boardだけ():
    text = _render()
    body = json.loads(text.split("export default ", 1)[1].rstrip().rstrip(";"))
    assert [(s["id"], s["tools"]) for s in body["screens"]] == [("queue", ["thth_board"])]
    assert body["mimeType"] == "text/html;profile=mcp-app"


def test_画面はリモートの包みとpendingを扱う():
    with open(os.path.join(ROOT, "mcp", "ui", "queue.html"), encoding="utf-8") as f:
        html = f.read()
    assert "ththBridge.call('thth_result'" in html
    assert "sc.status==='pending'" in html
    assert "!remote&&" in html  # リモートでは下見の「開く」を出さない
    assert "waits>=6" in html  # 待つのは 6 回まで
