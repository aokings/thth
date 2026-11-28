#!/usr/bin/env python3
"""MCP Apps の最小の確かめ（THTH画面 第1・設計 §6 の 1〜2）。

本物の THTH の道具は呼ばない。決まった見本（偽の予定 3 件）を返す道具 1 本と、
それを描く画面 1 枚（`ui://thth/probe`）だけを持つ stdio のサーバ。
確かめたいのは「Claude のデスクトップが手元の stdio のサーバの画面を出すか」だけ。

- 受けた `initialize` の params（client の能力）を `THTH_UI_PROBE_LOG` の
  ファイルに 1 行ずつ残す（既定は残さない）。秘密は来ない（client の名前と能力だけ）。
- 標準ライブラリだけ（THTH の依存 0 の決まりのまま）。
"""
from __future__ import annotations

import json
import os
import sys
import time

UI_URI = "ui://thth/probe"
UI_MIME = "text/html;profile=mcp-app"
HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLE = {
    "kind": "thth.queue.sample",
    "note": "見本（偽のデータ）",
    "items": [
        {"id": "d-001", "account": "sample-threads", "status": "approved", "publish_at": "2026-12-01T09:00:00+09:00"},
        {"id": "d-002", "account": "sample-bluesky", "status": "draft", "publish_at": "2026-12-01T12:30:00+09:00"},
        {"id": "d-003", "account": "sample-threads", "status": "draft", "publish_at": None},
    ],
}

TOOL = {
    "name": "thth_ui_probe",
    "description": "THTH の画面の確かめ用。見本の予定 3 件（偽のデータ）を返し、対応する client では画面に一覧を出す。",
    "inputSchema": {"type": "object", "properties": {
        "count": {"type": "integer", "minimum": 0, "maximum": 3,
                  "description": "返す見本の件数（既定 3）。減らして呼び直すと、画面で消える時の動きが見える"}},
        "additionalProperties": False},
    "_meta": {"ui": {"resourceUri": UI_URI}},
}


def _log(event: str, data) -> None:
    path = os.environ.get("THTH_UI_PROBE_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "data": data},
                           ensure_ascii=False) + "\n")


def _html() -> str:
    with open(os.path.join(HERE, "probe.html"), encoding="utf-8") as f:
        return f.read()


def handle(req: dict):
    method = req.get("method")
    rid = req.get("id")
    if "id" not in req:
        _log("notification", method)
        return None
    _log("request", {"method": method, "params": req.get("params")} if method == "initialize" else method)
    if method == "initialize":
        result = {
            "protocolVersion": (req.get("params") or {}).get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "thth-ui-probe", "version": "0.0.1"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        name = (req.get("params") or {}).get("name")
        if name != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        args = (req.get("params") or {}).get("arguments") or {}
        count = args.get("count", 3)
        if type(count) is not int or not 0 <= count <= 3:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "count は 0〜3 の整数"}}
        data = dict(SAMPLE, items=SAMPLE["items"][:count])
        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                  "structuredContent": data}
    elif method == "resources/list":
        result = {"resources": [{"uri": UI_URI, "name": "THTH の予定（確かめ）", "mimeType": UI_MIME}]}
    elif method == "resources/read":
        uri = (req.get("params") or {}).get("uri")
        if uri != UI_URI:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown resource: {uri}"}}
        result = {"contents": [{"uri": UI_URI, "mimeType": UI_MIME, "text": _html(),
                                "_meta": {"ui": {"prefersBorder": True}}}]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:  # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
