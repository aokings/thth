"""THTH の画面の HTML（mcp/ui/*.html・偽の host を除く）の柵。設計 §1-4（演出）・§2・§5。

- 動きを減らす設定（prefers-reduced-motion: reduce）に応える節がある（動きを使う画面だけ）。
- 中身を「初めは透明」にしない: @keyframes の外で opacity: 0 を書かない（動きが止められても中身が見える）。
- 消えた痕の 1 行は読み上げにも届く（role="status" か aria-live）。
- 結果の文字を innerHTML で入れない（textContent だけ）。
- 外の URL を読まない（src・href・url() に http(s):// を書かない）。
"""
from __future__ import annotations

import glob
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = sorted(p for p in glob.glob(os.path.join(ROOT, "mcp", "ui", "*.html"))
               if not os.path.basename(p).endswith("_host.html"))


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _without_keyframes(css):
    out, i = [], 0
    for m in re.finditer(r"@keyframes[^{]*\{", css):
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while depth and j < len(css):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        i = j
    out.append(css[i:])
    return "".join(out)


def test_画面がある():
    assert PAGES


@pytest.mark.parametrize("path", PAGES, ids=os.path.basename)
def test_動きを使う画面は動きを減らす設定に応える(path):
    css = "".join(re.findall(r"<style>(.*?)</style>", _read(path), flags=re.S))
    if "animation" in css or "transition" in css:
        assert re.search(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)", css)


@pytest.mark.parametrize("path", PAGES, ids=os.path.basename)
def test_中身を初めは透明にしない(path):
    css = "".join(re.findall(r"<style>(.*?)</style>", _read(path), flags=re.S))
    assert not re.search(r"opacity\s*:\s*0(?![.\d])", _without_keyframes(css))


@pytest.mark.parametrize("path", PAGES, ids=os.path.basename)
def test_消えた痕は読み上げにも届く(path):
    html = _read(path)
    for tag in re.findall(r"<[^>]*class=\"trace\"[^>]*>", html):
        assert 'role="status"' in tag or "aria-live" in tag, tag


@pytest.mark.parametrize("path", PAGES, ids=os.path.basename)
def test_結果の文字をinnerHTMLで入れない(path):
    assert "innerHTML" not in _read(path)


@pytest.mark.parametrize("path", PAGES, ids=os.path.basename)
def test_外のURLを読まない(path):
    html = _read(path)
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
    assert not re.search(r"""url\(\s*["']?https?://""", html)
