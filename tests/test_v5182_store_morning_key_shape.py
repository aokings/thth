"""5.18.2 の候補: `thth morning` は、鍵の形が店の種類に合わない店の行を黙って飛ばしていた（5.18.0 監査 の 13 の後半）。

前は、鍵のファイルが無い店と同じ扱い（行を出さない）だった。BASE の店に STORES の鍵を置いた・STORES の店に
BASE の PAT を置いた、などのときは、期限を見られないまま何も言わない。直し: 鍵のファイルはあるのに使えない店は、
飛ばしたことと、通し方（`thth shop token set <店>` で正しい鍵を置き直す）を 1 行で知らせる（原則 前向きな断り）。
鍵のファイルが無い店は今までどおり何も言わない（まだ鍵を置いていないのは誤りではない）。鍵の値は出さない。

送る先は偽の transport（本物の STORES には何も送らない）。鍵は試験の値。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tests.test_store_stores import NOW, STORE, add, env, run, setup_store  # noqa: F401
from thth import morning
from thth.shop import ledger

MARKER = "test-marker-not-a-real-key-0123"
WRONG_SHAPES = {
    "BASE の PAT": {"access_token": MARKER, "expires_on": "2027-01-31"},
    "Shopify の client": {"auth": "shopify_client", "client_id": MARKER, "client_secret": MARKER},
    "読めないファイル": None,
}


def put_token(value):
    path = pathlib.Path(ledger.load(STORE)["token"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json" if value is None else json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.parametrize("what", list(WRONG_SHAPES))
def test_鍵の形が合わない店は飛ばしたと知らせ通し方を言う(capsys, env, what):
    assert add(capsys)[0] == 0
    put_token(WRONG_SHAPES[what])
    [line] = morning._store_key_lines({"nigamilab"}, NOW)
    assert STORE in line and "STORES" in line, line
    assert "飛ばしました" in line, line                                   # 黙らない
    assert f"thth shop token set {STORE}" in line and "--by <名前>" in line, line   # 通し方
    assert MARKER not in line                                             # 鍵の値は出さない
    assert morning._store_key_lines({"other"}, NOW) == []                 # 別の project の 1 枚には出さない


def test_鍵のファイルが無い店は今までどおり何も言わない(capsys, env):
    assert add(capsys)[0] == 0
    assert morning._store_key_lines({"nigamilab"}, NOW) == []


def test_正しい鍵を置き直せば知らせは消える(capsys, env):
    assert add(capsys)[0] == 0
    put_token(WRONG_SHAPES["BASE の PAT"])
    assert len(morning._store_key_lines({"nigamilab"}, NOW)) == 1
    env["lines"][:] = [env["credential"]]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--by", "m")   # 言われたとおりに打つ
    assert rc_ == 0, err
    assert morning._store_key_lines({"nigamilab"}, NOW) == []


def test_正しい鍵の店の知らせは変わらない(capsys, env):
    setup_store(capsys, env)
    assert morning._store_key_lines({"nigamilab"}, NOW) == []
