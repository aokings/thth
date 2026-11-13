"""台帳の器 段 1 — Codex の四回めの検収（`~/Documents/THTH/codex/答え_段1の四回め_2026-11-14.md`）の穴と、
同じ型の穴（一部の欠けを全体で見る・比べる両側の形の違い・型の違う同じ値）を突く試験。

穴ごとに、直す前に赤を見てから直した。
1. たどる段で、枝の 1 つが 0 件でも、ほかの枝があれば判断になる
2. 範囲の軸の値が欠け・null・空でも「範囲が決まっている」として使える
3. 出典の欄が並びでないと、黙って出典 0 にする
4. 欠けた状態の欄があると、同じ中身の行の先頭の選び方で依る所が変わる（並べ替えだけで古くなる）
5. 見せた範囲の札（前後の空白）をそのまま尋ねると、範囲が合わない
6. 数の 1 と字の "1" の同じ番号が、判断では断られるのに健康に出ない
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth.register import judgment as J, vessel as V
from tests.test_register_vessel import env  # noqa: F401
from tests.test_register_vessel_holes3 import CLAIM_C, ROW, _ledger

AT = "2026-11-14"
PER_FIELD = {"group": "year", "per_field": "field_years"}


def _tiny(env, groups: list, *, key=("key",), uid=False, time=None, scope=None, not_values=None,
          question=None, sources=False, name="tiny") -> pathlib.Path:
    root = env["tmp"] / name
    (root / ".thth" / "kinds").mkdir(parents=True)
    units = {"groups": ["years", "*"], "items": ["rows", "*"], "key": list(key), "time": time or {"group": "year"}}
    if uid:
        units["id"] = "uid"
    if scope:
        units["scope"] = scope
    if not_values:
        units["not_values"] = list(not_values)
    if sources:
        units["sources"] = {"list": "sources", "field": "field"}
    kind = {"schema": "thth.kind/1", "kind": "t", "place": "t/*.json", "format": "json", "id": {"name": True},
            "units": units}
    if question is not None:
        kind["questions"] = [dict({"name": "reads_right", "level": "unit"}, **question)]
    (root / ".thth" / "kinds" / "t.json").write_text(json.dumps(kind), encoding="utf-8")
    (root / "t").mkdir()
    _write(root, groups)
    return root


def _write(root, groups):
    (root / "t" / "a.json").write_text(json.dumps({"years": groups}, ensure_ascii=False), encoding="utf-8")


def _one(v):
    [unit] = {u.pointer: u for u in v.get("t:a").units}.values()
    return unit


def _value(v, unit, name):
    return next(x for x in V.values_of(v.get("t:a"), v.kinds["t"], unit) if x.name == name)


# ================================================================== 穴 1

@pytest.mark.parametrize("f", [{"claim_id": ""}, {"claim_id": None}, {}])
def test_四1_たどる枝の1つが0件なら残った枝があっても判断にしない(env, f):
    v = V.open_vessel(str(_ledger(env, insp={"i": {"evidence_ids": ["e", "f"], **ROW}},
                                  evidence={"e": {"claim_id": "c"}, "f": f}, claim=CLAIM_C)))
    report = J.inspection_report(v)
    assert report["judgments"] == [] and report["skipped"] == [{"source": "insp:i", "reason": "target_missing"}]


# ================================================================== 穴 2

@pytest.mark.parametrize("gender", ["(欠け)", None, "", " "])
def test_四2_範囲の軸の値が空なら範囲は未宣言(env, gender):
    row = {"key": "a", "value": 10}
    if gender != "(欠け)":
        row["gender"] = gender
    root = _tiny(env, [{"year": 2026, "rows": [row]}], scope={"key_fields": ["gender"]},
                 question={"depends": ["value"]})
    v = V.open_vessel(str(root))
    unit = _one(v)
    value = _value(v, unit, "value")
    assert value.scope is None and value.scope_form is None
    rj = J.make(v, {"record": "t:a", "unit": unit.pointer}, "reads_right", by="a", at=AT, conclusion="good")
    plain = J.make(v, {"record": "t:a", "unit": unit.pointer, "value": "value"}, "reads_right", by="a", at=AT,
                   conclusion="good")
    for js in ([rj], [plain]):
        ans = J.answer_value(v, js, "t:a", unit.pointer, "value")
        assert ans["usable"] is False and ans["reason_kind"] == "scope_undeclared"
        assert J.answer_value(v, js, "t:a", unit.pointer, "value", as_scope="gender=")["usable"] is False
    said = J.make(v, {"record": "t:a", "unit": unit.pointer, "value": "value"}, "reads_right", by="a", at=AT,
                  conclusion="good", scope="男子")
    assert J.answer_value(v, [said], "t:a", unit.pointer, "value", as_scope="男子")["usable"] is True


# ================================================================== 穴 3

def test_四3_出典の欄が並びでなければ記録の問題に出し読んだ値そのものに依る(env):
    groups = [{"year": 2026, "sources": {"field": "rows", "quote": "old"}, "rows": [{"key": "a", "value": 10}]}]
    root = _tiny(env, groups, sources=True)
    v0 = V.open_vessel(str(root))
    assert "sources_unreadable" in v0.get("t:a").problems
    unit = _one(v0)
    j = J.make(v0, {"record": "t:a", "unit": unit.pointer, "value": "value"}, "reads_right", by="a", at=AT,
               conclusion="good")
    groups[0]["sources"]["quote"] = "new"
    _write(root, groups)
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, j) == "stale"
    assert J.answer_value(v1, [j], "t:a", unit.pointer, "value")["usable"] is False


def test_四3_出典の欄が無いのは出典なしで問題ではない(env):
    root = _tiny(env, [{"year": 2026, "rows": [{"key": "a", "value": 10}]}], sources=True)
    assert V.open_vessel(str(root)).get("t:a").problems == []


# ================================================================== 穴 4

def test_四4_欠けた状態の欄があっても並べ替えだけでは古くならない(env):
    row = {"key": "a", "value": 10, "field_years": {"value": 2026}}
    groups = [{"year": 2026, "rows": [row]}, {"year": 2027, "rows": [row]}]
    root = _tiny(env, groups, time=PER_FIELD, not_values=["key"], question={"state": "status", "depends": ["value"]})
    v0 = V.open_vessel(str(root))
    unit = _one(v0)
    j = J.make(v0, {"record": "t:a", "unit": unit.pointer}, "reads_right", by="a", at=AT, conclusion="good")
    _write(root, list(reversed(groups)))
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v0, j) == J.evaluate(v1, j) == "effective"
    assert J.answer_value(v1, [j], "t:a", unit.pointer, "value")["usable"] is True


def test_四4_欠けた欄の4つ組は親の年度も出典も持たない(env):
    root = _tiny(env, [{"year": 2026, "rows": [{"key": "a", "value": 10}]}])
    v = V.open_vessel(str(root))
    assert V.cell(v.kinds["t"].units, _one(v), "status") == {"value": None, "scope": None, "time": None,
                                                              "sources": []}


# ================================================================== 穴 5

def test_四5_見せた範囲の札をそのまま尋ねれば合う(env):
    row = {"key": "a", "value": 10, "covered": "合計 "}
    root = _tiny(env, [{"year": 2026, "rows": [row]}], scope={"separate": ["value"], "fields": {"value": "covered"}})
    v = V.open_vessel(str(root))
    unit = _one(v)
    value = _value(v, unit, "value")
    j = J.make(v, {"record": "t:a", "unit": unit.pointer, "value": "value"}, "reads_right", by="a", at=AT,
               conclusion="good")
    assert J.answer_value(v, [j], "t:a", unit.pointer, "value", as_scope=value.scope)["usable"] is True
    assert J.answer_value(v, [j], "t:a", unit.pointer, "value", as_scope=" covered=合計")["usable"] is True


# ================================================================== 穴 6

def test_四6_数と字の同じ番号は健康に出る(env):
    root = _tiny(env, [{"year": 2026, "rows": [{"uid": 1, "key": "a", "value": 10},
                                               {"uid": "1", "key": "a", "value": 10}]}], uid=True)
    v = V.open_vessel(str(root))
    rec = v.get("t:a")
    assert "unit_id_duplicate" in rec.problems and all(u.ambiguous for u in rec.units)
    assert [a["units"] for a in V.ambiguous_units(v)] == [["i:1", "i:1"]]
    with pytest.raises(V.RegisterRefused):
        J.make(v, {"record": "t:a", "unit": "i:1", "value": "value"}, "reads_right", by="a", at=AT,
               conclusion="good")


# ================================================================== 同じ型で探した物

@pytest.mark.parametrize("groups", [[{"year": 2026, "rows": [{"key": "a", "value": 10}, "手で書いた行"]}],
                                    [{"year": 2026, "rows": [{"key": "a", "value": 10}]}, "手で書いたまとまり"]])
def test_探_行やまとまりの並びの中の物でない要素は黙って落とさず記録の問題に出す(env, groups):
    root = _tiny(env, groups)
    assert "unit_not_object" in V.open_vessel(str(root)).get("t:a").problems
