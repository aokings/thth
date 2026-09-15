"""MCP の入口の頑丈さ（セキュリティ監査 2026-09-14・P2-1／P2-3／P3-2）。

**見つかり方**: `mcp/server.py` の 1 要求の処理は裸だった。`tools/call` に
`arguments` が無ければ `KeyError`、`params` が配列なら `AttributeError` が
`main()` のループの外まで抜けて、**サーバが落ちる**——クライアントから見ると
応答が来ないまま口が閉じる（どの要求が悪かったのかも判らない）。

さらに `file` は**どこでも指せた**（`/etc/passwd`・`~/.config/thth/*.token`）。
`thth lint` は中身の断片を返すので、読むだけの道具でも読む先は縛る。

ここで確かめるのは「**1 件の失敗で死なない**」と「**縛りの外は -32602**」。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

MCP_SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "mcp", "server.py")


def _load_server_module():
    spec = importlib.util.spec_from_file_location("thth_mcp_server_robust", MCP_SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def 話す(requests: list) -> list:
    """stdio 越しに要求を並べて投げ、返ってきた行を全部読む。"""
    proc = subprocess.Popen(
        [sys.executable, MCP_SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=dict(os.environ))
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    out, err = proc.communicate(payload, timeout=60)
    assert proc.returncode == 0, f"サーバが異常終了した: rc={proc.returncode} {err}"
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def 話す_生行(lines: list) -> list:
    """**行そのもの**（すでに文字列の JSON）を並べて投げる版。

    `話す()` は `json.dumps()` を通すので、Python の int → str 変換自体が
    4300 桁の壁に当たる値は作れない（呼ぶ側で `ValueError` になる）。ここでは
    行を直接の文字列として渡すので、**サーバ側だけ**が壊れた形の JSON を読む。
    """
    proc = subprocess.Popen(
        [sys.executable, MCP_SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=dict(os.environ))
    payload = "".join(line + "\n" for line in lines)
    out, err = proc.communicate(payload, timeout=60)
    assert proc.returncode == 0, f"サーバが異常終了した: rc={proc.returncode} {err}"
    return [json.loads(line) for line in out.splitlines() if line.strip()]


# ------------------------------------------------------------------ P2-1

def test_不正な要求でも応答が返り次のtools_listが生きている(isolated_account):
    """**4 通り**（必須欠落・型違い・params が list・batch）のどれでも死なない。"""
    responses = 話す([
        # 1) 必須の引数が無い（前は KeyError でサーバごと落ちた）
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "thth_lint", "arguments": {}}},
        # 2) 型が違う（前は subprocess に int が渡って TypeError）
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "thth_lint", "arguments": {"file": 123}}},
        # 3) params が配列（前は AttributeError）
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": ["thth_board"]},
        # 4) batch（配列の要求）
        [{"jsonrpc": "2.0", "id": 4, "method": "tools/list"}],
        # **そのあとも生きている**
        {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
    ])

    by_id = {r.get("id"): r for r in responses}
    for req_id in (1, 2, 3):
        assert by_id[req_id]["error"]["code"] == -32602, by_id[req_id]
    # batch は id を読み取れないので id は null・code は -32600。
    batch = [r for r in responses if r.get("error", {}).get("code") == -32600]
    assert batch, responses
    # **次の要求が通る**（サーバが生きている）。
    assert "tools" in by_id[5]["result"]


def test_必須の引数が無ければ道具を起こさない(isolated_account, monkeypatch):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError) as e:
        server.validate_arguments("thth_lint", {})
    assert "必須" in str(e.value)


@pytest.mark.parametrize("引数", [
    {"file": 123},                                  # 文字列でない
    {"file": ["a.md"]},
    {"account": {"x": 1}},
    {"file": "a.md", "知らない鍵": "x"},              # 綴り間違いを黙って無視しない
])
def test_型が違えば受け取らない(引数):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_lint", 引数)


def test_整数の口にboolを通さない():
    server = _load_server_module()
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("before_you_post",
                                   {"account": "a", "topic": "t", "window_days": True})
    # 正しい形は通る。
    server.validate_arguments("before_you_post",
                               {"account": "a", "topic": "t", "window_days": 7})


# ------------------------------------------------------------------ P2-3

def _queue_file(account) -> str:
    path = os.path.join(account["queue_dir"], "mcp-a.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\naccount: {}\nstatus: draft\npublish_at: 2026-09-09T08:00:00+09:00\n"
                "topic: コーヒー\nthth: 1\n---\n\n## threads\n\n本文\n".format(account["name"]))
    return path


@pytest.mark.parametrize("道具", ["thth_lint", "thth_preview", "thth_topic_context"])
def test_台帳のrepo_dirの外のfileは受け取らない(isolated_account, tmp_path, 道具):
    server = _load_server_module()
    よそ = tmp_path / "outside.md"
    よそ.write_text("---\nstatus: draft\n---\n\n秘密\n", encoding="utf-8")
    with pytest.raises(server.ToolInputError) as e:
        server.validate_arguments(道具, {"file": str(よそ)})
    assert "repo_dir" in str(e.value)


def test_symlinkで外を指しても受け取らない(isolated_account, tmp_path):
    server = _load_server_module()
    よそ = tmp_path / "outside.md"
    よそ.write_text("秘密\n", encoding="utf-8")
    link = os.path.join(isolated_account["queue_dir"], "link.md")
    os.symlink(str(よそ), link)
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_lint", {"file": link})


def test_repo_dirの中のfileはこれまでどおり通る(isolated_account):
    server = _load_server_module()
    path = _queue_file(isolated_account)
    assert server.validate_arguments("thth_lint", {"file": path})["file"] == path
    # `..` を含んでいても、畳んだ先が中なら通る。
    回り道 = os.path.join(os.path.dirname(path), "..", os.path.basename(
        os.path.dirname(path)), os.path.basename(path))
    server.validate_arguments("thth_lint", {"file": 回り道})


def test_stdio越しでも外のfileは_32602(isolated_account, tmp_path):
    よそ = tmp_path / "outside.md"
    よそ.write_text("秘密\n", encoding="utf-8")
    responses = 話す([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "thth_lint", "arguments": {"file": str(よそ)}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    by_id = {r.get("id"): r for r in responses}
    assert by_id[1]["error"]["code"] == -32602
    assert "秘密" not in json.dumps(by_id[1], ensure_ascii=False)
    assert "tools" in by_id[2]["result"]


# ------------------------------------------------------------------ P3-2

@pytest.mark.parametrize("値", ["--json", "-h", "--help"])
def test_位置引数がハイフンで始まれば受け取らない(isolated_account, 値):
    server = _load_server_module()
    with pytest.raises(server.ToolInputError) as e:
        server.validate_arguments("thth_queue", {"account": 値})
    assert "-" in str(e.value)


# ------------------------------------------------------------------ P2-2

def test_MCPのthth_queueは置き場の外の名前をloudに断る(isolated_account):
    """`../x` は CLI（`accounts.load_account()`）が名指しで断る。

    **同じ文言**であること（在る／無い／壊れているで返る言葉が変わらない＝
    存在の探りができない）は `tests/test_account_name_check.py` が見る。
    ここでは MCP から呼んでも同じ言葉が返ることだけ。
    """
    server = _load_server_module()
    text = server.call_tool("thth_queue", {"account": "../x"})["content"][0]["text"]
    assert "使えない字" in text
    # **読みに行っていない**（在るかどうかを言っていない）。
    assert "台帳が無い" not in text


# ------------------------------------------------------------------ C-2（監査 2026-09-16）
#
# **破れていたもの**:
#   - `main()` の 1 行パースは `except json.JSONDecodeError` だけを捕っていた。
#     `json.JSONDecodeError` は `ValueError` の子だが、Python 3.12 は 4300 桁を
#     超える整数で**別の** `ValueError`
#     （"Exceeds the limit (4300 digits) for integer string conversion"）を
#     上げる——ここが漏れてプロセスが死ぬ。深い入れ子の `RecursionError` も同じ
#     筋で、`json.JSONDecodeError` の網には入らない。
#   - `ledger_roots()` は台帳の `repo_dir` を生のまま `os.path.realpath()` して
#     いた。相対パスなら**このプロセスの cwd** 基準で畳まれる。正規の
#     `thth/accounts.py::resolved_repo_dir()` は `$THTH_ROOT` 基準——**同じ台帳で
#     許可する場所が MCP だけ食い違っていた。**

def test_5000桁のidでも死なず次の要求に応答する():
    """`json.loads` の `ValueError`（`JSONDecodeError` ではない）で死なない。"""
    こわれた行 = '{"jsonrpc": "2.0", "id": ' + ("1" * 5000) + ', "method": "tools/list"}'
    responses = 話す_生行([
        こわれた行,
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ensure_ascii=False),
    ])
    # 壊れた行は id が判らないので黙って次へ（従来どおり）——応答は正しい要求の分だけ。
    by_id = {r.get("id"): r for r in responses}
    assert "tools" in by_id[1]["result"], responses


def test_深い入れ子のRecursionErrorでも死なず次の要求に応答する():
    """`[` を 100000 個並べた行は `json.loads` で `RecursionError` になる。"""
    こわれた行 = "[" * 100000 + "]" * 100000
    responses = 話す_生行([
        こわれた行,
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ensure_ascii=False),
    ])
    by_id = {r.get("id"): r for r in responses}
    assert "tools" in by_id[2]["result"], responses


def test_repo_dirの相対パスはTHTH_ROOT基準でcwd基準ではない(
        isolated_account_factory, tmp_path, monkeypatch):
    """`resolved_repo_dir()` を通す（`os.path.realpath(生の repo_dir)` に戻さない）。

    既存の `test_repo_dirの中のfileはこれまでどおり通る` と同じ形（
    `server.validate_arguments()` を直接呼ぶ）に合わせる。
    """
    isolated_account_factory(repo_dir="repos/x")
    server = _load_server_module()
    root = os.environ["THTH_ROOT"]

    # cwd を「台帳とは無関係などこか」に動かしてから確かめる——**cwd に左右
    # されないこと**そのものが検査の対象なので、既定の cwd のままでは弱い。
    別のcwd = tmp_path / "elsewhere"
    別のcwd.mkdir()
    monkeypatch.chdir(別のcwd)

    # `$THTH_ROOT/repos/x/queue/a.md` ——**正規の場所**（許可される）。
    正規のpath = os.path.join(root, "repos", "x", "queue", "a.md")
    os.makedirs(os.path.dirname(正規のpath), exist_ok=True)
    with open(正規のpath, "w", encoding="utf-8") as f:
        f.write("---\nstatus: draft\n---\n\n本文\n")
    assert server.validate_arguments(
        "thth_lint", {"file": 正規のpath})["file"] == 正規のpath

    # `<cwd>/repos/x/queue/a.md` ——**同じ相対パスを cwd 基準で畳んだ先**
    # （旧コードなら通っていた・拒まれるべき）。
    cwd基準のpath = 別のcwd / "repos" / "x" / "queue" / "a.md"
    cwd基準のpath.parent.mkdir(parents=True)
    cwd基準のpath.write_text("---\nstatus: draft\n---\n\n秘密\n", encoding="utf-8")
    with pytest.raises(server.ToolInputError) as e:
        server.validate_arguments("thth_lint", {"file": str(cwd基準のpath)})
    assert "repo_dir" in str(e.value)
