"""台帳の器 段 1 の芯 — 段 3（route.py・seal.py・route_cli.py）の書き手が求めた 4 つの直しを突く試験。

直す前に赤を見てから直した。
1. `Vessel.get` が全部の記録を頭から見る（記録の数の 2 乗）。番号の索引で引く。重なる番号を断る動きは同じ
2. `evaluate` が、よその台帳を指す判断（`target.ledger`）を「消えた指し先」と数える。「確かめられない」にする（設計 §5.4）
3. 判断の結論を引く関数が非公開の名前（`J._conclusion`）で、段 3 が呼んでいる。公開の名前にする
4. 段 3 が判断の番号を自前で作り直している。番号の作り方を公開し、足す欄を渡せる形にする
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth.register import judgment as J, route as R, vessel as V, vessel_cli
from thth.register.errors import RegisterRefused
from tests.test_register_vessel import env  # noqa: F401

AT = "2026-11-14"
ROUTE_SRC = pathlib.Path(R.__file__).read_text(encoding="utf-8")


def _table(env, rows: str) -> pathlib.Path:
    root = env["tmp"] / "table"
    (root / ".thth" / "kinds").mkdir(parents=True)
    (root / ".thth" / "kinds" / "n.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "n", "place": "n.csv", "format": "csv", "id": {"field": "id"},
        "questions": [{"name": "reads_right", "depends": ["text"]}]}), encoding="utf-8")
    (root / "n.csv").write_text("id,text\n" + rows, encoding="utf-8")
    return root


class _NoWalk(list):
    """頭から順に見られたら落ちる並び（索引で引くことを確かめる）。"""

    def __iter__(self):
        raise AssertionError("all_records を頭から見た")


# ================================================================== 1

def test_芯1_番号で引くのは索引で全部を頭から見ない(env):
    v = V.open_vessel(str(_table(env, "".join(f"n{i},t{i}\n" for i in range(50)) + "d,x\nd,y\n")))
    v.all_records = _NoWalk(v.all_records)
    assert v.get("n:n7").values["text"] == "t7"
    with pytest.raises(RegisterRefused) as dup:
        v.get("n:d")   # 重なる番号は、今までどおり先頭を選ばずに断る
    assert dup.value.code == "register_unit_ambiguous"
    with pytest.raises(RegisterRefused) as missing:
        v.get("n:nothing")
    assert missing.value.code == "register_record_missing"


# ================================================================== 2

def test_芯2_よその台帳を指す判断は消えた指し先でなく確かめられない(env):
    root = _table(env, "a,hello\n")
    v = V.open_vessel(str(root))
    take = {"schema": J.SCHEMA, "target": {"ledger": "common", "record": "source:fujimoto-2021"}, "question": "take",
            "answer": {"word": None, "conclusion": "good", "scope": None}, "reason": {"kind": None, "text": None},
            "by": "a", "how": None, "at": AT, "until": None, "path": "acceptance", "carries": None,
            "seen": {"version": "x" * 64, "depend": None}}
    take["id"] = J.new_id(take)
    here = J.make(v, {"record": "n:a"}, "reads_right", by="a", at=AT, conclusion="good")
    assert J.evaluate(v, take) == "unverifiable"
    assert J.summary(v, [take, here]) == {"effective": 1, "stale": 0, "vanished": 0, "unverifiable": 1}
    assert J.candidates(v, [take]) == []
    J.append(str(root), [take, here])
    assert vessel_cli.check(str(root))["judgments"]["unverifiable"] == 1


# ================================================================== 3

def test_芯3_結論を引く関数は公開の名前で段3もそれを呼ぶ(env):
    v = V.open_vessel(str(_table(env, "a,hello\n")))
    j = J.make(v, {"record": "n:a"}, "reads_right", by="a", at=AT, conclusion="bad", reason_kind="wrong")
    assert J.conclusion_of(v, j) == ("bad", "wrong")
    assert R.conclusion_of(v, j) == ("bad", "wrong")
    assert "J._" not in ROUTE_SRC  # 段 3 は段 1 の非公開の名前を呼ばない


# ================================================================== 4

def test_芯4_判断の番号の作り方は公開で足す欄を渡せる(env):
    v = V.open_vessel(str(_table(env, "a,hello\n")))
    plain = J.make(v, {"record": "n:a"}, "reads_right", by="a", at=AT, conclusion="good")
    assert J.new_id(plain) == plain["id"]  # 番号の欄は番号の作り方に入らない
    pinned = [{"record": "n:a", "version": v.get("n:a").version}]
    extra = J.make(v, {"record": "n:a"}, "reads_right", by="a", at=AT, conclusion="good",
                   extra={"against": pinned})
    assert extra["against"] == pinned and extra["id"] != plain["id"]
    assert extra["id"] == J.new_id({k: x for k, x in extra.items() if k != "id"})
    with pytest.raises(RegisterRefused) as caught:
        J.make(v, {"record": "n:a"}, "reads_right", by="a", at=AT, conclusion="good", extra={"by": "b"})
    assert caught.value.code == "register_args_invalid"
    assert "def new_id" not in ROUTE_SRC and "J.new_id" in ROUTE_SRC  # 段 3 は番号を自前で作らない
