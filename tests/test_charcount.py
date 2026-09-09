"""文字数の数え方（設計 §2.2・受け入れ 3）: Threads は 500 字、絵文字は UTF-8 バイト数。"""
from __future__ import annotations

from thth import queuefile


def test_絵文字はutf8バイト数で数える():
    # 🍶（U+1F376・4 バイト）1 個 → 4 字扱い。
    assert queuefile.char_count("🍶") == 4
    # 通常の文字は 1 個 1 字。
    assert queuefile.char_count("あ") == 1
    assert queuefile.char_count("a") == 1


def test_絵文字入り本文の境界を固定する():
    # 496 字の通常文字 + 絵文字 1 個（4 字換算）＝ 500 字ちょうど（超過ではない）。
    text = ("あ" * 496) + "🍶"
    assert queuefile.char_count(text) == 500

    # 497 字の通常文字 + 絵文字 1 個＝ 501 字（超過）。
    text_over = ("あ" * 497) + "🍶"
    assert queuefile.char_count(text_over) == 501


def test_異体字セレクタ付き絵文字も数えられる():
    # ☕（U+2615・Symbol,Other・UTF-8 3 バイト）＋ U+FE0F 異体字セレクタ
    # （Unicode カテゴリは Mn なので絵文字扱いせず 1 字）＝ 3 + 1 = 4 字。
    text = "☕️"
    assert queuefile.char_count(text) == 4
