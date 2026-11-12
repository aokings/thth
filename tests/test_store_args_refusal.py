"""`store_args_invalid` は誤りの所（詳細の語）に合った通し方を言う（監査 5.18.0 stores の気づき 13・残り 1・5.18.1）。

壊れ方（直す前）: STORES の `--url` の誤りでも、通すにはの例の命令が BASE の `add`、もっと良くが「<project>-base の形に」
のままだった。日本語のドメインや道をそのまま貼って断られても、ASCII の字だけ・punycode・% で包む形を言わなかった。

柵:
  - `thth/shop/` が `store_args_invalid` に添える詳細の語は、どれも目録の `by_detail` に文がある（語を足したら文も足す）。
  - 詳細ごとの「通すには」は、その引数（`--url` など）を名指し、例の命令はその引数を持つ店の種類の形。
  - `url` は ASCII・punycode（xn--）・% で包む形・ブラウザの番地の欄から写すことを言う。
  - `url` と `shop` の「もっと良く」は `<project>-base` の話をしない。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from thth import refusal
from thth import refusal_catalog as rc
from tests import test_store_stores as st
from tests.test_store_stores import env  # noqa: F401 — STORES の偽の店

ROOT = pathlib.Path(__file__).resolve().parent.parent
CODE = "store_args_invalid"
# 詳細の語 → 「通すには」が名指す引数と、例の命令に要る語。
EXPECT = {
    "store_name": ("店の名前", None),
    "project": ("--project", "--project"),
    "by": ("--by", "--by"),
    "shop": ("--shop", "--kind shopify"),
    "url": ("--url", "--kind stores"),
    "auth": ("--auth", "--auth client"),
    "expires": ("--expires", "--expires"),
    "item": ("--item", "thth shop items"),
    "item_or_kind": ("--item", "--kind"),
}


def _words_in_shop() -> set:
    """`thth/shop/*.py` で `StoreRefused("store_args_invalid", "<語>")` と書いた語。"""
    found = set()
    for path in (ROOT / "thth" / "shop").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "StoreRefused" and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == CODE
                    and isinstance(node.args[1], ast.Constant)):
                found.add(node.args[1].value)
    return found


def test_shopが添える詳細の語はどれも目録に文がある():
    words = _words_in_shop()
    assert words == set(EXPECT), words
    assert set(rc.CATALOG[CODE]["by_detail"]) == words


@pytest.mark.parametrize("word", sorted(EXPECT))
def test_詳細ごとの通すにはは引数を名指し例の命令もその形(word):
    arg, command_word = EXPECT[word]
    found = refusal.parts(CODE, reason=word)
    assert arg in found["fix"], (word, found["fix"])
    if command_word:
        assert command_word in found["fix"], (word, found["fix"])
    assert found["better"] and found["why"]
    if word in ("url", "shop", "auth", "item", "item_or_kind", "expires", "by"):
        assert "<project>-base" not in found["better"], word


def test_urlの断りはASCIIとpunycodeと百分率とブラウザの番地の欄を言う():
    fix = refusal.parts(CODE, reason="url")["fix"]
    for word in ("ASCII", "xn--", "%", "番地の欄"):
        assert word in fix, word
    assert "--kind stores" in fix and "--kind base" not in fix


def test_詳細の無い断りは今までどおり全体の文():
    plain = refusal.parts(CODE)
    assert plain["fix"].startswith("1 行目の（）が指す引数を直して")
    assert refusal.parts(CODE, reason="nothing_like_this")["fix"] == plain["fix"]


def test_日本語のドメインを貼ると通し方がその場で出る(capsys, env):
    rc_, err = st.erun(capsys, "shop", "add", st.STORE, "--kind", "stores", "--project", "nigamilab",
                       "--url", "https://例え.jp/", "--by", "m")
    assert rc_ == 2 and "（url）" in err
    assert "xn--" in err and "番地の欄" in err and "--kind stores" in err
    assert "<project>-base" not in err
    rc_, err = st.erun(capsys, "shop", "add", st.STORE, "--kind", "stores", "--project", "nigamilab",
                       "--url", "https://a.example/店", "--by", "m")
    assert rc_ == 2 and "（url）" in err and "% で包んだ形" in err
