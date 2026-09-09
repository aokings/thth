#!/usr/bin/env python3
"""薄い MCP（stdio）。発注 §4・設計 §3.7。

中身は CLI（`bin/thth`）を subprocess で呼んで `--json` の出力を返すだけ。
判断・整形・条件分岐はここに書かない（MCP に業務論理を持たせない・§3.7）。

T1 で出すのは読み取り 4 本だけ: thth_lint・thth_queue・thth_preview・thth_board。
thth_throw・thth_approve・thth_auth・thth_refresh は T1 では作らない（§4）。

MCP の stdio トランスポートは改行区切りの JSON-RPC 2.0（1 メッセージ 1 行、
埋め込み改行なし）。追加の依存を持ち込まないため（標準ライブラリのみ・発注 §0-4）
最小限のループを自前で書く。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THTH_BIN = os.environ.get("THTH_BIN", os.path.join(APP_DIR, "bin", "thth"))

TOOLS = [
    {
        "name": "thth_lint",
        "description": "docs/sns/queue の 1 ファイルを形式検査する（読むだけ・副作用なし）。",
        "inputSchema": {
            "type": "object",
            "properties": {"file": {"type": "string", "description": "queue ファイルのパス"}},
            "required": ["file"],
        },
    },
    {
        "name": "thth_queue",
        "description": "draft/approved/posted/型外 の一覧と、次に出るもの・いつかを返す（読むだけ）。",
        "inputSchema": {
            "type": "object",
            "properties": {"account": {"type": "string", "description": "省略時は全アカウント"}},
        },
    },
    {
        "name": "thth_preview",
        "description": "実際に投げる本文そのもの（媒体の節。前後に何も足さない）を返す（読むだけ）。",
        "inputSchema": {
            "type": "object",
            "properties": {"file": {"type": "string", "description": "queue ファイルのパス"}},
            "required": ["file"],
        },
    },
    {
        "name": "thth_board",
        "description": "アカウントごとの最終投稿・approved 待ち・inflight・型外の骨を返す（読むだけ）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def run_cli(args: list) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, THTH_BIN, *args], capture_output=True, text=True)


def call_tool(name: str, arguments: dict | None) -> dict:
    """CLI を呼んで結果を返すだけ。判断（条件分岐・整形）をここに書かない。"""
    arguments = arguments or {}
    if name == "thth_lint":
        proc = run_cli(["lint", arguments["file"], "--json"])
        text = proc.stdout
    elif name == "thth_queue":
        args = ["queue"]
        if arguments.get("account"):
            args.append(arguments["account"])
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "thth_preview":
        proc = run_cli(["preview", arguments["file"]])
        text = proc.stdout if proc.returncode == 0 else proc.stderr
    elif name == "thth_board":
        proc = run_cli(["board", "--json"])
        text = proc.stdout
    else:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}

    is_error = proc.returncode not in (0, 1)  # lint は 1 も正常な「検査結果」
    if not text:
        text = proc.stderr
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle_request(req: dict):
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "thth", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        result = call_tool(params.get("name"), params.get("arguments"))
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle_request(req)
        if resp is not None:
            _send(resp)


if __name__ == "__main__":
    main()
