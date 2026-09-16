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

# **CLI の在り処は 2 通りある**（設計 v2 §3・`pip install thth`）。
#
#   - repo をそのまま使う運用: このファイルの 1 つ上に `bin/thth` がある（従来）。
#   - `pip install thth`: wheel の中では**このファイル自身が `thth/mcp_server.py`**
#     として入る（`pyproject.toml` の force-include）。隣に `bin/` は無い。
#
# **無いものを指したまま subprocess を起こさない。** `bin/thth` が実在するときだけ
# それを使い、無ければ `python -m thth` に落とす（`thth/__main__.py`・同じ `main()`）。
# `THTH_BIN` は従来どおり環境変数で上書きできる（テストはこの属性を差し替える）。
_REPO_BIN = os.path.join(APP_DIR, "bin", "thth")
THTH_BIN = os.environ.get("THTH_BIN") or (_REPO_BIN if os.path.exists(_REPO_BIN) else None)

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
        # **設計 v2 §1 規約 6 の文言そのまま。** 実装が 1 語でも足すと、
        # 「正本はどっちだ」が始まる（監査 2・B7 で「。言えないことは
        # cannot_say に出る」を足していたのを外した）。**設計書ではなく
        # 実装を戻す。**
        "description": (
            "投稿する前に呼ぶ。この語とこの型で、スレッドがどう伸びたかの実績を、"
            "件数と期間つきで返す"
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
    {
        # **名前と説明文がそのまま売り文句**（設計「自分の泉」§2.2）。
        # 実装が 1 語でも足したら設計書でなく実装を戻す（§2 の頭書きそのまま）。
        "name": "after_you_posted",
        # **設計「自分の泉」§2.2 の文言そのまま。**
        "description": (
            "出したあとに呼ぶ。この語・この型・この枝で、自分の投稿と返信が"
            "どう受け取られたかを、件数と期間つきで返す"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "account 名"},
                "topic": {"type": "string", "description": "語（トピック）"},
                "kind": {"type": "string", "description": "型（行動・年度付き 等）"},
                "hour_band": {"type": "string",
                               "description": "時刻帯（朝・昼・夕・深夜）で絞る"},
                "reply_to": {"type": "string",
                              "description": "この post_id への返信だけに絞る"},
                "author_key": {"type": "string",
                                "description": "この仮名（16 進 16 桁）への返信だけに絞る"},
                "window_days": {"type": "integer", "description": "直近何日（既定 30）"},
                "min_n": {"type": "integer",
                           "description": "中央値を返す下限（既定 5）"},
            },
            "required": ["account"],
        },
    },
]


class ToolInputError(Exception):
    """要求が受け取れない（JSON-RPC `-32602`・セキュリティ監査 2026-09-14・P2-1）。

    **落ちない・黙らない。** 以前は `arguments["file"]` の `KeyError` や
    `params` が list のときの `AttributeError` が `main()` のループの外まで抜けて、
    **サーバが死んでいた**——クライアントから見ると応答が来ないまま接続が切れる
    （どの要求が悪かったのかも判らない）。1 要求の失敗は 1 要求の error で返し、
    ループは続ける。
    """


def _schema_for(name):
    for tool in TOOLS:
        if tool["name"] == name:
            return tool.get("inputSchema") or {}
    return None


# `inputSchema` の `type` を Python の型に写す。**`bool` は `int` の子**なので、
# 整数の検査から明示的に外す（`True` を `window_days` に通さない）。
_TYPES = {
    "string": ((str,), "文字列"),
    "object": ((dict,), "object"),
    "boolean": ((bool,), "真偽値"),
    "integer": ((int,), "整数"),
    "number": ((int, float), "数"),
}


def ledger_roots() -> list:
    """台帳の `repo_dir` の realpath 一覧（`file` 引数の**唯一の行き先**）。

    セキュリティ監査 2026-09-14・P2-3。`thth_preview`・`thth_lint`・
    `thth_topic_context` の `file` は**どこでも指せた**——MCP を持つ LLM が
    `/etc/passwd` や `~/.config/thth/*.token` を渡せば、`thth preview` は
    front-matter が読めないと言うだけだが、**`thth lint` は中身の断片を返す**。
    読むだけの道具でも、読む先は縛る。

    `queue_dir` は台帳で `repo_dir` からの相対なので、`repo_dir` の下で足りる。
    """
    try:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)
        from thth import accounts as accounts_mod
    except ImportError:
        return []
    try:
        names = accounts_mod.list_account_names()
    except Exception:                              # noqa: BLE001（台帳が読めない）
        return []
    roots = []
    for account in names:
        try:
            cfg = accounts_mod.load_account(account)
        except Exception:                          # noqa: BLE001（1 本壊れていても続ける）
            continue
        # **正規の函数を通す**（監査 2026-09-16）。ここが台帳の `repo_dir` を
        # 生のまま `os.path.realpath()` していたので、相対パスは**この
        # プロセスの cwd** 基準で畳まれていた。`thth/accounts.py::resolved_repo_dir()`
        # は `$THTH_ROOT` 基準——**同じ台帳で許可する場所が MCP だけ食い違って
        # いた**（cwd は systemd 経由なら `/`、手で打てば人それぞれ）。
        repo_dir = accounts_mod.resolved_repo_dir(cfg)
        if repo_dir:
            roots.append(os.path.realpath(repo_dir))
    return roots


def check_file_argument(name: str, path: str) -> None:
    """`file` は**台帳のどれかの `repo_dir` の下**だけ（外れれば `ToolInputError`）。

    `realpath` してから比べる（symlink・`..`・相対パスを畳んでから見る）。
    """
    real = os.path.realpath(path)
    roots = ledger_roots()
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return
    raise ToolInputError(
        f"{name}: file は台帳の repo_dir の中のパスだけを渡してください"
        f"（受け取ったものはどの repo_dir にも入っていません）")


def validate_arguments(name: str, arguments) -> dict:
    """道具の `inputSchema` どおりの引数か。違えば `ToolInputError`。

    見るのは 3 つだけ: **object であること**・**必須が揃っていること**・
    **型が合っていること**（知らない鍵も断る——綴りを間違えたまま既定値で
    動いたことにしない）。判断はしない（中身の意味は CLI の仕事）。
    """
    schema = _schema_for(name)
    if schema is None:
        return {}                     # 知らない道具は `call_tool()` が断る
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolInputError(
            f"{name}: arguments は object で渡してください"
            f"（受け取った: {type(arguments).__name__}）")
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if arguments.get(key) is None:
            raise ToolInputError(f"{name}: 必須の引数がありません: {key}")
    for key, value in arguments.items():
        spec = props.get(key)
        if spec is None:
            raise ToolInputError(f"{name}: 知らない引数です: {key}")
        if value is None:
            continue
        types, 名 = _TYPES.get(spec.get("type"), ((object,), "値"))
        if spec.get("type") == "integer" and isinstance(value, bool):
            raise ToolInputError(f"{name}: {key} は{名}で渡してください（受け取った: bool）")
        if not isinstance(value, types):
            raise ToolInputError(
                f"{name}: {key} は{名}で渡してください"
                f"（受け取った: {type(value).__name__}）")
        # **位置引数に渡る値が `-` で始まらない**（監査 2026-09-14・P3-2）。
        # `run_cli()` は値を argv の位置に置く。`--json` のような綴りを渡されると
        # **CLI の別の旗として読まれる**（`thth lint --help` のように無害な形も
        # あるが、旗の意味は CLI 側の都合で増える）。`--` を足して黙って通すより、
        # **受け取れないものとして断る**（作法 5）。
        if isinstance(value, str) and value.startswith("-"):
            raise ToolInputError(
                f"{name}: {key} が `-` で始まっています（{value!r}）。"
                f"CLI の旗と区別できないので受け取りません")
        if key == "file":
            check_file_argument(name, value)
    return arguments


def cli_argv(args: list) -> list:
    """CLI を起こす argv。`THTH_BIN` があればそれ、無ければ `python -m thth`。"""
    head = [sys.executable, THTH_BIN] if THTH_BIN else [sys.executable, "-m", "thth"]
    return [*head, *args]


def run_cli(args: list, *, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cli_argv(args), capture_output=True,
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
    elif name == "after_you_posted":
        # **`before_you_post` と同じ型**（発注 T0-2）: CLI を `--json` で呼ぶだけ。
        args = ["after", arguments["account"]]
        if arguments.get("topic"):
            args += ["--topic", arguments["topic"]]
        if arguments.get("kind"):
            args += ["--kind", arguments["kind"]]
        if arguments.get("hour_band"):
            args += ["--hour-band", arguments["hour_band"]]
        if arguments.get("reply_to"):
            args += ["--reply-to", arguments["reply_to"]]
        if arguments.get("author_key"):
            args += ["--author-key", arguments["author_key"]]
        if arguments.get("window_days") is not None:
            args += ["--window-days", str(arguments["window_days"])]
        if arguments.get("min_n") is not None:
            args += ["--min-n", str(arguments["min_n"])]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    else:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}

    if name.startswith("thth_topic_") or name in ("before_you_post", "after_you_posted"):
        # **新しい道具は exit 1 も isError**（設計 §7）。lint の exit 1（検査結果）
        # とは意味が違う——こちらは stale_context・不正な候補比較で、
        # **そのまま使ってはいけない**応答。既存の扱いは変えない。
        # `before_you_post`／`after_you_posted` も同じ（設計 v2 §1・「自分の泉」§5）:
        # exit 1 は台帳が無い、2 は問いが受け取れない。**`cannot_say` だらけの
        # 答えは exit 0 で、error ではない。**
        is_error = proc.returncode != 0
    else:
        is_error = proc.returncode not in (0, 1)  # lint は 1 も正常な「検査結果」
    if not text:
        text = proc.stderr
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _is_notification(req: dict) -> bool:
    """JSON-RPC の**通知**（`id` を持たない要求）か。

    監査 2 回目・P3-6。**通知には応答を返してはいけない**（JSON-RPC 2.0 §4.1）。
    `notifications/initialized` だけを名前で特定して黙っていたので、
    **`id` の無い `tools/call` には結果を、`id` の無い未知の method には
    エラーを返していた**——`"id": null` を付けた応答は仕様違反で、厳密な
    クライアントは接続を切る。**名前ではなく形で決める。**
    """
    return "id" not in req


def _handle_request(req: dict):
    method = req.get("method")
    req_id = req.get("id")

    if _is_notification(req):
        # **通知は黙って処理する**（応答を作らない）。いまのところ中身のある
        # 通知は `notifications/initialized` だけで、それも何もしない。
        return None

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
        if not isinstance(params, dict):
            raise ToolInputError(
                f"params は object で渡してください（受け取った: {type(params).__name__}）")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ToolInputError("params.name（道具の名前）が要ります")
        arguments = validate_arguments(name, params.get("arguments"))
        result = call_tool(name, arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if req_id is not None:
        return _error(req_id, -32601, f"method not found: {method}")
    return None


def main() -> None:
    """1 行 1 要求のループ。**1 件の失敗でループを降りない**（監査 2026-09-14・P2-1）。

    以前は `_handle_request()` の中で上がった例外がここまで抜けてサーバが死んで
    いた（`arguments` に `file` が無い `KeyError`・`params` が list のときの
    `AttributeError`）。クライアントから見ると応答が来ないまま口が閉じる——
    **どの要求が悪かったのかも判らない。** 1 要求の失敗は 1 要求の error にして、
    次の要求は今までどおり受ける。
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, RecursionError):
            # **`json.JSONDecodeError` だけでは足りない**（監査 2026-09-16）。
            # `json.JSONDecodeError` は `ValueError` の子だが、Python 3.12 は
            # 4300 桁を超える整数で別の `ValueError`
            # （"Exceeds the limit (4300 digits) for integer string conversion"）
            # を上げる——`except json.JSONDecodeError` だけではここが漏れて
            # プロセスが死ぬ。深い入れ子（`[` の連続）の `RecursionError` も同じ
            # 筋で、ここでしか捕まえられない。**id が判らないので返しようがない**
            # （JSON-RPC も id:null を許すが、従来どおり黙って次の行へ・方針は
            # 変えない）。
            continue
        if isinstance(req, list):
            # **batch（配列）は受けない。** 受けたふりをして 1 件目だけ処理する
            # と、残りが黙って消える（`-32600 Invalid Request`）。
            _send(_error(None, -32600,
                         "batch（配列）の要求は受け付けません。1 行 1 要求で送ってください"))
            continue
        if not isinstance(req, dict):
            _send(_error(None, -32600,
                         f"要求は object で送ってください（受け取った: {type(req).__name__}）"))
            continue
        req_id = req.get("id")
        try:
            resp = _handle_request(req)
        except ToolInputError as e:
            resp = _error(req_id, -32602, str(e))
        except Exception as e:                      # noqa: BLE001
            # **死なない。** 想定していない失敗も 1 要求の error にして次へ。
            resp = _error(req_id, -32602, f"{type(e).__name__}: {e}")
        # **通知には何も返さない**（監査 2 回目・P3-6）。失敗しても同じ——
        # 返す先の `id` が無いので、`"id": null` の error を送ることになる
        # （JSON-RPC 2.0 §4.1 違反・厳密なクライアントは接続を切る）。
        # **死なないことと、黙ることは両立する。**
        if resp is not None and not _is_notification(req):
            _send(resp)


if __name__ == "__main__":
    main()
