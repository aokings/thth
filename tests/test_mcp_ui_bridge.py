"""THTH の画面のつなぎの JS が 4 枚で 1 字違わないかの柵（依頼_THTH画面_部品の一式 の「作る物」）。

各画面は 1 ファイルで完結させるため、つなぎ（ui/initialize・tools/call・size-changed）を
`/* thth-bridge:begin */` と `/* thth-bridge:end */` の間に同じ物として埋める。
1 枚だけ直して他を直し忘れると、画面ごとに host とのやりとりがずれる。
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "mcp", "ui")
SCREENS = ["queue.html", "preview.html", "confirm.html", "numbers.html"]
BLOCK = re.compile(r"/\* thth-bridge:begin \*/(.*?)/\* thth-bridge:end \*/", re.S)


def _blocks():
    out = {}
    for name in SCREENS:
        path = os.path.join(UI, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[name] = BLOCK.findall(f.read())
    return out


def test_画面ごとにつなぎの塊が1つだけある():
    blocks = _blocks()
    if not blocks:
        pytest.skip("4 枚の画面がまだ無い")
    for name, found in blocks.items():
        assert len(found) == 1, f"{name}: 塊が {len(found)} 個"


def test_つなぎの塊が4枚で1字違わない():
    blocks = _blocks()
    if len(blocks) < 2:
        pytest.skip("比べる画面が 2 枚未満")
    first_name, first = next(iter(blocks.items()))
    for name, found in blocks.items():
        assert found == first, f"{name} のつなぎが {first_name} と違う"
