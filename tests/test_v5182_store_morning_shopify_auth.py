"""5.18.2 の候補: `thth morning` の「鍵の形が合わない店」の行の命令は、Shopify の固定の鍵（`--auth token`）の店では
そのまま打っても通らなかった（監査 次の版・2026-11-13 の直したほうがよい 3）。`thth shop token set` の `--auth` の
既定は client（client id と secret の 2 行）で、固定の鍵 1 行では詰まる。

直し: Shopify の店の行は `thth shop` の助けと同じ書き方で `[--auth client|token]` を言う。壊れた鍵のファイルからは
今の鍵の種類を読めない（読めないから行が出ている）ので、2 つを並べて利用者に選んでもらう。どちらを選んでも、
行の命令のとおりに打てば保存されて知らせが消える。

送る先は偽の transport（本物の Shopify には何も送らない）。鍵は試験の値。
"""
from __future__ import annotations

import pathlib

import pytest

from tests.test_store_shopify import STORE, add, env, run  # noqa: F401
from tests.test_store_stores import NOW
from tests.test_v5182_store_morning_key_shape import MARKER
from thth import morning
from thth.shop import ledger

SAID = f"thth shop token set {STORE} [--auth client|token] --by <名前>"


def put_broken_token():
    path = pathlib.Path(ledger.load(STORE)["token"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json " + MARKER, encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.parametrize("auth", ["client", "token"])
def test_Shopifyの店の行は鍵の種類を選べる命令を言いそのとおりに打てば通る(capsys, env, auth):
    assert add(capsys)[0] == 0
    put_broken_token()
    [line] = morning._store_key_lines({"nigamilab"}, NOW)
    assert "Shopify" in line and "飛ばしました" in line, line
    assert SAID in line, line                                          # 鍵の種類を選ぶ旗を言う
    assert MARKER not in line                                          # 鍵のファイルの中身は出さない
    # 行の命令の [--auth client|token] から、今の鍵の種類を選んで打つ。
    env["lines"][:] = [env["client_id"], env["client_secret"]] if auth == "client" else [env["fixed"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--auth", auth, "--by", "m")
    assert rc_ == 0, out + err
    assert morning._store_key_lines({"nigamilab"}, NOW) == []
    assert env["fixed"] not in out + err and env["client_secret"] not in out + err
