"""THTH の画面の部品の表（mcp/ui/catalog.json）の柵。設計 §1-3・§3。

- 画面に結びつく道具・行動が呼ぶ道具は、今の MCP に実在する（画面だけの新しい道具 thth_ui_* を除く）。
- 行動の引数の文字の欄は、列挙・形・日時のどれか（AI や人が自由な文を入れる欄を作らない）。
  例外は digest に縛られた写し（x-thth-bound-by-digest）だけ。同じ行動に digest の欄が要る（第24 11/27 #9961）。
- 行動は画面だけ（visibility: ["app"]）。
- 重い画面（確定のカード）には「人が押した証明ではない」の注がある。
- 確定のカードの順は 宛先の口座 → 日時 → 本文（長い本文に押し出されない）。
"""
from __future__ import annotations

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "mcp", "ui", "catalog.json")


def _catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def _existing_tool_names():
    spec = importlib.util.spec_from_file_location("thth_mcp_server_cat", os.path.join(ROOT, "mcp", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {t["name"] for t in mod.TOOLS + mod.SERVER_TOOLS + mod.REMOTE_TOOLS}


def _string_fields(schema, path=""):
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "string":
        yield path, schema
    for key, sub in (schema.get("properties") or {}).items():
        yield from _string_fields(sub, path + "." + key)
    if "items" in schema:
        yield from _string_fields(schema["items"], path + "[]")


def test_画面と行動が指す道具は実在する():
    names = _existing_tool_names()
    for screen in _catalog()["screens"]:
        assert screen["uri"] == "ui://thth/" + screen["id"]
        for tool in screen["tools"]:
            assert tool in names, (screen["id"], tool)
        for action in screen["actions"]:
            assert action["calls"] in names or action["calls"].startswith("thth_ui_"), (screen["id"], action["id"])


def test_行動の引数に自由な文の欄が無い():
    for screen in _catalog()["screens"]:
        for action in screen["actions"]:
            props = action["args"].get("properties") or {}
            for path, field in _string_fields(action["args"]):
                kinds = [k for k in ("enum", "pattern", "format", "x-thth-bound-by-digest") if k in field]
                assert kinds, (screen["id"], action["id"], path)
                if "x-thth-bound-by-digest" in field:
                    # 例外は 1 つだけ: digest に縛られた写し。同じ行動に digest の欄があり、結果から写す欄であること。
                    assert "digest" in props and "pattern" in props["digest"], (screen["id"], action["id"], path)
                    assert field.get("x-thth-from-result") is True, (screen["id"], action["id"], path)
            assert action["args"].get("additionalProperties") is False, (screen["id"], action["id"])


def test_行動は画面だけ():
    for screen in _catalog()["screens"]:
        for action in screen["actions"]:
            assert action["visibility"] == ["app"], (screen["id"], action["id"])


def test_重い画面には人が押した証明ではないの注がある():
    for screen in _catalog()["screens"]:
        if screen["weight"] == "heavy":
            assert "人が押した証明ではない" in screen["note"], screen["id"]
            for action in screen["actions"]:
                assert "人が押した証明にはならない" in action["description_note"], action["id"]


def test_本文を見せる画面は宛先と日時を本文より先に出す():
    for screen in _catalog()["screens"]:
        order = screen.get("order")
        if not order or "本文" not in order:
            continue
        assert order.index("宛先の口座") < order.index("本文"), screen["id"]
        assert order.index("日時") < order.index("本文"), screen["id"]


def test_digestに縛られた写しは画面で読むだけ():
    for screen in _catalog()["screens"]:
        for action in screen["actions"]:
            bound = [k for k, v in (action["args"].get("properties") or {}).items() if v.get("x-thth-bound-by-digest")]
            if bound:
                assert "理由" in screen.get("readonly", []), screen["id"]
