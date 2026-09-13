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
        "name": "thth_topic_context",
        "description": (
            "原稿のトピックを決める前に**最初に**呼ぶ。原稿と（あれば）記事本文から、"
            "判断の入力を固定して返す。記事がまだ無ければ、取りに行くべき URL と"
            "必要な JSON の形を返す（読むだけ・副作用なし）。"
            "使う順序: context → 足りない資料を取る → evaluate。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "queue ファイルのパス"},
                "article": {"type": "object", "description": "記事証拠（ArticleEvidence）"},
                "article_url": {"type": "string",
                                 "description": "本文に URL が複数ある場合の主対象"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "thth_topic_evaluate",
        "description": (
            "記事本文と候補比較を渡して、**引用が本文に在るか・参照が実在するか・"
            "観測が新しいか・投稿者が偏っていないか**を検査させる。"
            "THTH は候補を作らないし順位も付けない——足りないものを言うだけ。"
            "候補が suitable でなければ別の候補に繰り上げず、比較し直しを求める。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "article": {"type": "object"},
                "proposal": {"type": "object", "description": "候補比較（TopicProposal）"},
                "article_url": {"type": "string"},
            },
            "required": ["file", "article", "proposal"],
        },
    },
    {
        "name": "thth_topic_decision",
        "description": "保存済みの判断を読む（読むだけ）。今の原稿と食い違っていれば freshness で言う。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "decision_id"}},
            "required": ["id"],
        },
    },
    {
        "name": "thth_board",
        "description": "アカウントごとの最終投稿・approved 待ち・inflight・型外の骨を返す（読むだけ）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        # **名前と説明文がそのまま売り文句**（設計 v2 §1 規約 6）。LLM は説明文で
        # 呼ぶかを決めるので、**この文言は設計 v2 §1 の正本から動かさない。**
        "name": "before_you_post",
        "description": (
            "投稿する前に呼ぶ。この語とこの型で、スレッドがどう伸びたかの実績を、"
            "件数と期間つきで返す。言えないことは cannot_say に出る"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "account 名"},
                "topic": {"type": "string", "description": "語（トピック）"},
                "kind": {"type": "string", "description": "型（行動・年度付き 等）"},
                "hour_band": {"type": "string",
                               "description": "出す予定の時刻帯（朝・昼・夕・深夜）"},
                "is_reply": {"type": "boolean", "description": "返信として出すか"},
                "window_days": {"type": "integer", "description": "直近何日（既定 30）"},
                "min_n": {"type": "integer",
                           "description": "中央値を返す下限（既定 20）"},
            },
            "required": ["account", "topic"],
        },
    },
]


def run_cli(args: list, *, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, THTH_BIN, *args], capture_output=True,
                           text=True, input=stdin_text)


def _topic_stdin(arguments: dict, *, with_proposal: bool) -> tuple:
    """記事・候補比較は**標準入力に 1 つの JSON** で渡す（設計 §7）。

    外部モデルが指定した任意のサーバファイルへ書いて受け渡さない。stdin は 1 本
    しかないので、記事と候補比較を別々の口にせず 1 つの封筒にまとめる
    （**設計からの変更・理由はここ**）。CLI 側はファイル入力と同じ検査関数へ
    合流する。
    """
    args = ["topics", "suggest", arguments["file"], "--input-json-stdin"]
    if arguments.get("article_url"):
        args += ["--article-url", arguments["article_url"]]
    payload = {"article": arguments.get("article")}
    if with_proposal:
        payload["proposal"] = arguments.get("proposal")
    return args, json.dumps(payload, ensure_ascii=False)


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
    elif name in ("thth_topic_context", "thth_topic_evaluate"):
        args, payload = _topic_stdin(
            arguments, with_proposal=(name == "thth_topic_evaluate"))
        proc = run_cli(args, stdin_text=payload)
        text = proc.stdout
    elif name == "thth_topic_decision":
        proc = run_cli(["topics", "decision", arguments["id"], "--json"])
        text = proc.stdout
    elif name == "thth_board":
        proc = run_cli(["board", "--json"])
        text = proc.stdout
    elif name == "before_you_post":
        args = ["ask", "before-you-post", arguments["account"],
                "--topic", arguments["topic"]]
        if arguments.get("kind"):
            args += ["--kind", arguments["kind"]]
        if arguments.get("hour_band"):
            args += ["--hour-band", arguments["hour_band"]]
        if arguments.get("is_reply"):
            args.append("--reply")
        if arguments.get("window_days") is not None:
            args += ["--window-days", str(arguments["window_days"])]
        if arguments.get("min_n") is not None:
            args += ["--min-n", str(arguments["min_n"])]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    else:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}

    if name.startswith("thth_topic_") or name == "before_you_post":
        # **新しい道具は exit 1 も isError**（設計 §7）。lint の exit 1（検査結果）
        # とは意味が違う——こちらは stale_context・不正な候補比較で、
        # **そのまま使ってはいけない**応答。既存の扱いは変えない。
        # `before_you_post` も同じ（設計 v2 §1）: exit 1 は台帳が無い、2 は問いが
        # 受け取れない。**`cannot_say` だらけの答えは exit 0 で、error ではない。**
        is_error = proc.returncode != 0
    else:
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
