"""3.9.1 本文の数字の抜き出し・雑音を除く（報告 r20260924-d8bac63a）。

見るのは:
  - `plaza.text_numbers()` は抜き出す前に、日付・時刻・THTH の id（`r`/`p` 始まり）・URL・
    `--` 引数とその値・コードの囲み（バッククォート内）を本文から除く。
  - 拾うのは単位や記号が付いた数（`153 件`・`97%`・`r=0.92`・`n=27`・`¥1,151`・
    `713 中 692`）と、表のセルの数値。符号は数に付いているときだけ（`-11.0`）。
  - 重複は 1 回に。
  - 観測（道具が付けた数字）と本文の数字の区別そのもの（3.4.0 の規律）は変えない
    （`plaza.show()` 経由の分離は tests/test_v340_plaza_observe.py・
    tests/test_v380_plaza_from.py が引き続き見る）。
"""
from __future__ import annotations

from thth import plaza


def test_日付は拾わない():
    assert plaza.text_numbers("2026-09-23 に確認した") == []
    assert plaza.text_numbers("2026/09/23 に確認した") == []
    assert plaza.text_numbers("9/23 に確認した") == []
    assert plaza.text_numbers("9/23〜9/24 の間に伸びた") == []


def test_ISO8601の日時も拾わない():
    assert plaza.text_numbers("2026-09-24T10:30:00+09:00 に置いた") == []


def test_時刻は拾わない():
    assert plaza.text_numbers("10:30 に投稿した") == []
    assert plaza.text_numbers("10:30:05 に投稿した") == []


def test_THTHのidは拾わない():
    assert plaza.text_numbers("報告 r20260924-39741ee5 を見て書いた") == []
    assert plaza.text_numbers("書き込み p20260924-39741ee5 から") == []


def test_URLは拾わない():
    assert plaza.text_numbers("https://example.com/path/2026/09?x=5 を見て") == []


def test_コマンドの引数の値は拾わない():
    assert plaza.text_numbers("--since 3d を指定した") == []
    assert plaza.text_numbers("`--count 5` を渡す") == []


def test_マイナス記号だけの切れ端は拾わない():
    # 日付を割ったときに出る "-09" のような切れ端も、単位が無い裸の数字も拾わない。
    assert plaza.text_numbers("2026-09-23 と 2026-09-24 を比べた") == []
    assert plaza.text_numbers("-11.0 の差があった") == []


def test_単位や記号が付いた数は拾う():
    assert plaza.text_numbers("153 件のうち 97% が該当") == ["153 件", "97%"]
    assert plaza.text_numbers("r=0.92 で n=27") == ["r=0.92", "n=27"]
    assert plaza.text_numbers("¥1,151 の支払い") == ["¥1,151"]
    assert plaza.text_numbers("713 中 692") == ["713 中 692"]
    assert plaza.text_numbers("3倍になった") == ["3倍"]


def test_表のセルの数値は単位が無くても拾う():
    body = "比較:\n| 指標 | 朝 | 夜 |\n| views | 153 | -11.0 |\n"
    assert plaza.text_numbers(body) == ["153", "-11.0"]


def test_符号は数に付いているときだけ数える():
    # 表のセルでは符号つきの数をそのまま拾う。地の文の単位なし裸数字は符号があっても拾わない。
    assert plaza.text_numbers("| a | -11.0 |\n| b | 12 |") == ["-11.0", "12"]
    assert plaza.text_numbers("差は -11.0 だった") == []


def test_重複は1回に():
    assert plaza.text_numbers("97% と 97% の両方で見えた") == ["97%"]


def test_日付の末尾が単位語にくっついても数えない():
    # 日付を先に除かないと "23" が単位語 "件" にくっついて偽の "23件" に化ける（単変異で確認）。
    assert plaza.text_numbers("確認: 2026-09-23件目からログを見た") == []


def test_日時idなどを除いた後の地の文の数字は引き続き拾う():
    body = "報告 r20260924-39741ee5（2026-09-23 10:30 に置いた）: 153 件のうち 97% が該当。"
    assert plaza.text_numbers(body) == ["153 件", "97%"]
