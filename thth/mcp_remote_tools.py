"""リモート MCP（thth.me/mcp・段 K）の道具の表を、括りの通り道の表（`server_passthrough.PASS`・`WRITE_PASS`）から作る。

Worker（`callback/src/mcp.js`）はこの表を JSON（`callback/src/mcp-tools.js` の `export default` の値・中身は JSON そのもの）で持ち、道具の引数を argv に組んで
VM の通り道（operation `ws_command`）へ渡すだけ。通すか・旗の検めは VM の `server_passthrough` が決める（権威は VM）。
JSON は手で書かない: `python -m thth.mcp_remote_tools --write` で作り、`tests/test_mcp_remote_tools.py` が
この表と 1 字でも違えば赤にする（ずれたまま出さない）。

道具の形:
- 読む命令は `thth_<命令>`（`-` は `_`）。名前の位置（口座か project）は `name`、値を取る旗は旗の名（`--window-days` → `window_days`）、
  値を取らない旗は真偽。`--json` は Worker が必ず付ける（表に出さない）。表が足す旗（`--no-mark`）は VM が足す。
- 書く命令（approve・revoke・withdraw・hold）は、原稿の相対パスを `files`、口座を `account`（必須）で取る。
  二段の確認は `confirm`: approve は VM の digest（1 段目の答えにある）を、revoke・withdraw・hold は Worker が 1 段目で
  出した確認の値を渡す。どちらも、同じ引数の 1 段目をこの接続が 10 分の内に受け取っていなければ Worker が断る
  （人に見せる前に書く道を作らない）。`--digest-only` は出さない（1 段目は confirm を省けばよい）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import server_passthrough as sp

SCHEMA = "thth.mcp-tools/1"
JSON_PATH = Path(__file__).resolve().parent.parent / "callback" / "src" / "mcp-tools.js"
HEADER = ("// 生成物: `python -m thth.mcp_remote_tools --write` が括りの通り道の表（thth/server_passthrough.py）から書き出す。\n"
          "// 手で直さない。ずれは tests/test_mcp_remote_tools.py が赤にする。\n")
HIDDEN_FLAGS = frozenset({"--json", "--digest-only"})

# 道具の説明（人と AI が読む 1 文）。表の命令が増えたら、ここに足さないと試験が赤になる。
DESCRIPTIONS = {
    "board": "括りの一枚（口座ごとの未承認・予約・止まり・次の一手）を読む。読むだけ",
    "morning": "project の毎朝の一枚を読む（既読の印は付けない）。読むだけ",
    "observe": "project の観測の一枚を読む（既読の印は付けない）。読むだけ",
    "schedule": "予約と未承認の一覧を読む（name に口座を入れるとその口座だけ）。読むだけ",
    "where-log": "どこに出たかの記録を読む。読むだけ",
    "handoff-report": "申し送りの報告を読む（name か project で絞る）。読むだけ",
    "analytics-report": "数字の報告を読む（project・期間・切り口を選ぶ）。読むだけ",
    "unanswered": "まだ返していない返信を読む（name に口座）。読むだけ",
    "approvals": "口座の承認の台帳を読む（name に口座）。読むだけ",
    "approve": ("原稿を承認する（2 段）。confirm を省くと本文の全文と digest を返すだけで何も書かない。"
                "その本文を持ち主に見せ、承認すると言われたら、同じ引数に digest を confirm として付けて呼ぶ"),
    "revoke": ("承認を取り消して draft に戻す（2 段）。confirm を省くと何をするかと確認の値を返すだけ。"
               "持ち主に見せて、そうすると言われたら同じ引数に confirm を付けて呼ぶ"),
    "withdraw": ("出さないと決めた draft を閉じる（undo で戻す・2 段）。confirm を省くと何をするかと確認の値を返すだけ。"
                 "持ち主に見せて、そうすると言われたら同じ引数に confirm を付けて呼ぶ"),
    "hold": ("試しなどで止めた draft に期限（until・YYYY-MM-DD）と理由を付け、未承認の数えから外す（undo で戻す・2 段）。"
             "confirm を省くと何をするかと確認の値を返すだけ。持ち主に見せて、そうすると言われたら同じ引数に confirm を付けて呼ぶ"),
}

# 通り道にあるが MCP の道具にしない命令と、その理由（`server_passthrough.ACTION_PASS`）。CLI の `thth --lane` では通る。
NOT_TOOLS = {
    "reply": ("pending は承認待ち（まだ公開されていない）の他人の返信の本文を読む。公開の返信（unanswered）と違い、"
              "外の AI へ渡すかは masaru の決め（取得物を別の AI に渡す件）なので出さない。approve・ignore は二段の作り"
              "（1 段目で中身を持ち主に見せる）に合わない: Worker の 1 段目が見せられるのは返信の id だけで、本文は pending を"
              "出さない限り会話に無く、持ち主が何を公開するかを見て決められない"),
}


# 原稿の相対パスの形（`server_passthrough.relative_path_ok` より広くない: `-` と `/` 始まり・`\\`・`//`・`.` と `..` の段・
# 制御文字を断る）。Worker の buildArgv も同じ型で断り、VM は `--` のあとにだけ位置の引数を受ける（段 K の監査 1）。
PATH_PATTERN = r"^(?![-/])(?!.*\\)(?!.*//)(?!(?:.*/)?\.\.?(?:/|$))[^\u0000-\u001f\u007f]{1,%d}$" % sp.PATH_MAX


def prop(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def _anchored(pattern) -> str:
    """Python の `…\\Z` の型を、JSON Schema（JS の正規表現）の `^…$` にする。"""
    return "^" + pattern.pattern.removesuffix(r"\Z") + "$"


def tool_name(command: str) -> str:
    return "thth_" + command.replace("-", "_")


def _tool(command: str, spec: sp.Spec, writes: bool) -> dict:
    properties, required = {}, []
    values, flags, texts = {}, {}, {}
    for flag in sorted(spec.values):
        name = prop(flag)
        schema = {"type": "string", "maxLength": 64, "pattern": _anchored(sp.VALUE)}
        if flag in spec.choices:
            schema = {"type": "string", "enum": sorted(spec.choices[flag])}
        properties[name] = schema
        values[name] = flag
    for flag in sorted(spec.texts):
        name = prop(flag)
        properties[name] = {"type": "string", "minLength": 1, "maxLength": sp.TEXT_MAX}
        texts[name] = flag
    for flag in sorted(spec.flags - HIDDEN_FLAGS):
        name = prop(flag)
        properties[name] = {"type": "boolean"}
        flags[name] = flag
    low, high = spec.positionals
    positional = None
    if writes:
        positional = "files"
        properties["files"] = {"type": "array", "minItems": low, "maxItems": high,
                               "items": {"type": "string", "minLength": 1, "maxLength": sp.PATH_MAX, "pattern": PATH_PATTERN},
                               "description": "原稿の、口座の repo の根からの相対パス"}
        required += ["files", "account"]
        if "confirm" not in properties:
            properties["confirm"] = {"type": "string", "maxLength": 128,
                                     "description": "1 段目が返した確認の値（省くと 1 段目）"}
        else:
            properties["confirm"]["description"] = "1 段目が返した digest（省くと 1 段目）"
    elif high:
        positional = "name"
        properties["name"] = {"type": "string", "maxLength": 64, "pattern": _anchored(sp.NAME)}
        if low:
            required.append("name")
    schema = {"type": "object", "properties": dict(sorted(properties.items())), "additionalProperties": False}
    if required:
        schema["required"] = sorted(required)
    return {
        "name": tool_name(command),
        "command": command,
        "writes": writes,
        "confirm": None if not writes else ("vm" if "--confirm" in spec.values else "worker"),
        "description": DESCRIPTIONS[command],
        "inputSchema": schema,
        "argv": {"values": values, "texts": texts, "flags": flags, "positional": positional,
                 "always": ["--json"] if "--json" in spec.flags else []},
        "annotations": {"readOnlyHint": not writes, "destructiveHint": writes and command != "approve",
                        "openWorldHint": False},
    }


def build() -> dict:
    tools = [_tool(command, spec, False) for command, spec in sorted(sp.PASS.items())]
    tools += [_tool(command, spec, True) for command, spec in sorted(sp.WRITE_PASS.items())]
    return {"schema": SCHEMA, "operation": "ws_command", "tools": tools}


def render() -> str:
    return HEADER + "export default " + json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + ";\n"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--write"]:
        JSON_PATH.write_text(render(), encoding="utf-8")
        print(f"wrote {JSON_PATH}")
        return 0
    if argv == ["--check"]:
        ok = JSON_PATH.exists() and JSON_PATH.read_text(encoding="utf-8") == render()
        print("ok" if ok else f"{JSON_PATH} is stale: run python -m thth.mcp_remote_tools --write")
        return 0 if ok else 1
    print("usage: python -m thth.mcp_remote_tools --write | --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
