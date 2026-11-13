"""台帳の器 段 1 — Codex の三回めの検収（`~/Documents/THTH/codex/答え_段1の三回め_2026-11-14.md`）の 2 と 6 と、
同じ型の穴を突く試験（1・3・4・5 は、単位の指し先と判断の依る所の決まりが来てから）。

穴ごとに、直す前に赤を見てから直した。
2. 検収の記録を持つ表が、たどる先の一部の欠けを黙って落とし、残った先へ判断を作る
6. 検収の中身の明示の null を、欄の欠落と取り違えて捨てる
探. 指す欄の並び・区切りの中の空の要素を黙って落とす（たどる先の一部の欠けと同じ型）
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth.register import judgment as J, vessel as V
from tests.test_register_vessel import env  # noqa: F401


def _ledger(env, *, insp: dict, evidence: dict, claim: dict, depends=("statement",), required=None) -> pathlib.Path:
    root = env["tmp"] / "insp"
    kinds = root / ".thth" / "kinds"
    kinds.mkdir(parents=True)

    def kind(name, **extra):
        (kinds / f"{name}.json").write_text(json.dumps({
            "schema": "thth.kind/1", "kind": name, "place": f"{name}/*.json", "format": "json",
            "id": {"name": True}, **extra}), encoding="utf-8")
    kind("claim", questions=[{"name": "says_so", "state": "status", "depends": list(depends)}])
    kind("evidence", pointers=[{"field": "claim_id", "meaning": "supports", "to": "claim", "split": "|"}])
    inspects = {"follow": ["evidence_ids", "claim_id"], "question": "says_so", "by": "central",
                "content": {k: k for k in (*depends, "status")}}
    if required is not None:
        inspects["required"] = required
    kind("insp", pointers=[{"field": "evidence_ids", "meaning": "about", "to": "evidence"}], inspects=inspects)
    (root / ".thth" / "constitution.json").write_text(json.dumps({
        "schema": "thth.constitution/1", "words": {"says_so": {"supported": {"conclusion": "good"}}}}),
        encoding="utf-8")
    for name, files in (("insp", insp), ("evidence", evidence), ("claim", claim)):
        (root / name).mkdir()
        for stem, body in files.items():
            (root / name / f"{stem}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return root


CLAIM_C = {"c": {"statement": "hello", "status": "supported"}}
ROW = {"statement": "hello", "status": "supported"}


def test_再再2_たどる先がそろえば検収の記録になる(env):
    v = V.open_vessel(str(_ledger(env, insp={"i": {"evidence_ids": ["e"], **ROW}},
                                  evidence={"e": {"claim_id": "c"}}, claim=CLAIM_C)))
    [j] = J.inspections(v)
    assert J.evaluate(v, j) == "effective" and J.answer_record(v, [j], "claim:c", "says_so")["conclusion"] == "good"


@pytest.mark.parametrize("evidence_ids, claim_id, reason", [
    (["e", "not-found"], "c", "target_missing"),      # 途中の段の 1 つが無い
    (["e"], "c|not-found", "target_missing"),         # 最後の段の区切りの 1 つが無い
    (["e", ""], "c", "target_blank"),                 # 並びの中の空の要素
    (["e"], "c||c", "target_blank"),                  # 区切りの中の空の要素
    ([], "c", "target_missing"),                      # たどる先が 1 つも無い
])
def test_再再2_たどる先が1つでも欠ければ判断にせず理由を返す(env, evidence_ids, claim_id, reason):
    v = V.open_vessel(str(_ledger(env, insp={"i": {"evidence_ids": evidence_ids, **ROW}},
                                  evidence={"e": {"claim_id": claim_id}}, claim=CLAIM_C)))
    assert J.inspections(v) == []
    report = J.inspection_report(v)
    assert report["judgments"] == [] and report["skipped"] == [{"source": "insp:i", "reason": reason}]
    assert J.answer_record(v, J.inspections(v), "claim:c", "says_so")["conclusion"] == "pending"


def test_再再6_明示のnullは読んだ値として比べ欄の欠落だけを欠けとする(env):
    claim = {"c": {"statement": "hello", "note": None, "status": "supported"}}
    v = V.open_vessel(str(_ledger(env, insp={"i": {"evidence_ids": ["e"], "statement": "hello", "note": None,
                                                    "status": "supported"}},
                                  evidence={"e": {"claim_id": "c"}}, claim=claim, depends=("statement", "note"))))
    [j] = J.inspections(v)
    assert j["content"]["note"] is None and J.evaluate(v, j) == "effective"
    assert J.answer_record(v, [j], "claim:c", "says_so")["conclusion"] == "good"


def test_再再6_中身の欄が無い行は判断にせず理由を返す(env):
    v = V.open_vessel(str(_ledger(env, insp={"i": {"evidence_ids": ["e"], "statement": "hello",
                                                    "status": "supported"}},
                                  evidence={"e": {"claim_id": "c"}}, claim=CLAIM_C, depends=("statement", "note"))))
    assert J.inspection_report(v)["skipped"] == [{"source": "insp:i", "reason": "content_missing"}]


def test_再再6_requiredの欄のnullと空は判断にしない(env):
    for status in (None, ""):
        root = _ledger(env, insp={"i": {"evidence_ids": ["e"], "statement": "hello", "status": status}},
                       evidence={"e": {"claim_id": "c"}}, claim=CLAIM_C)
        v = V.open_vessel(str(root))
        assert J.inspection_report(v)["skipped"] == [{"source": "insp:i", "reason": "required_empty"}]
        root.rename(root.with_name(f"insp-{status!r}"))


def test_探_指す欄の空の要素は黙って落とさず空の指し先として見せる(env):
    v = V.open_vessel(str(_ledger(env, insp={"i": {"evidence_ids": ["e", ""], **ROW}},
                                  evidence={"e": {"claim_id": "c||c"}}, claim=CLAIM_C)))
    blank = V.links(v)["blank"]
    assert sorted((b["from"], b["field"]) for b in blank) == [("evidence:e", "claim_id"), ("insp:i", "evidence_ids")]


def test_探_幅の足りない表の行の欠けたセルはnullの値と取り違えない(env):
    from tests.test_register_vessel import copy
    root = copy(env, "kopicha")
    central = root / "data" / "knowledge" / "staging" / "k1" / "central.csv"
    with central.open("a", encoding="utf-8") as f:
        f.write("e-cer-003-a,fix,supported\n")
    report = J.inspection_report(V.open_vessel(str(root)))
    assert {"source": "central:e-cer-003-a", "reason": "source_problem"} in report["skipped"]
    assert {j["source"] for j in report["judgments"]} == {"central:e-milk-01", "central:e-br-024-a"}


# ================================================================== 指し先・依る所・及ぶ範囲（設計 §5.1 の 3）
# 指示書 `docs/指示_台帳の器_段1_指し先と依る所_2026-11-14.md` §3 の試験。三回めの反例 1・3・4・5 と、
# この決まりで初めて言えること。

def _tiny3(env, groups: list, *, key=("key",), uid=False, time=None, scope=None, not_values=None,
           depends=None, sources=False, name="tiny") -> pathlib.Path:
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
    if depends is not None:
        kind["questions"] = [{"name": "reads_right", "level": "unit", "depends": list(depends)}]
    (root / ".thth" / "kinds" / "t.json").write_text(json.dumps(kind), encoding="utf-8")
    (root / "t").mkdir()
    _write(root, groups)
    return root


def _write(root, groups):
    (root / "t" / "a.json").write_text(json.dumps({"years": groups}, ensure_ascii=False), encoding="utf-8")


def _row_j(v, pointer):
    return J.make(v, {"record": "t:a", "unit": pointer}, "reads_right", by="x", at="2026-11-14", conclusion="good")


def _val_j(v, pointer, name, **extra):
    return J.make(v, {"record": "t:a", "unit": pointer, "value": name}, "reads_right", by="x", at="2026-11-14",
                  conclusion="good", **extra)


def test_三1_行への判断は行の範囲が変わると古い(env):
    row = {"uid": "row1", "gender": "male", "value": 10}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}], key=("gender",), uid=True,
                  scope={"key_fields": ["gender"]}, depends=["value"])
    v0 = V.open_vessel(str(root))
    rj, vj = _row_j(v0, "i:row1"), _val_j(v0, "i:row1", "value")
    _write(root, [{"year": 2026, "rows": [dict(row, gender="female")]}])
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, rj) == "stale" and J.evaluate(v1, vj) == "stale"
    for js in ([rj], [vj]):
        ans = J.answer_value(v1, js, "t:a", "i:row1", "value", as_scope="gender=female")
        assert ans["usable"] is False and ans["reason_kind"] == "no_inspection_for_new_version"


def test_三1_範囲の軸が鍵に無くても同じ(env):
    row = {"key": "a", "gender": "male", "value": 10}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}], scope={"key_fields": ["gender"]}, depends=["value"])
    v0 = V.open_vessel(str(root))
    [unit] = v0.get("t:a").units
    assert unit.pointer.startswith("k:")
    rj = _row_j(v0, unit.pointer)
    _write(root, [{"year": 2026, "rows": [dict(row, gender="female")]}])
    v1 = V.open_vessel(str(root))
    assert [u.pointer for u in v1.get("t:a").units] == [unit.pointer]
    assert J.evaluate(v1, rj) == "stale"


def test_三1_札の区切りを動かしても同じ範囲にならない(env):
    row = {"uid": "r", "a": "x,b=y", "b": "z", "value": 1}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}], key=("a",), uid=True, scope={"key_fields": ["a", "b"]},
                  depends=["value"])
    v0 = V.open_vessel(str(root))
    rj = _row_j(v0, "i:r")
    label0 = next(x.scope for x in V.values_of(v0.get("t:a"), v0.kinds["t"], v0.get("t:a").units[0])
                  if x.name == "value")
    _write(root, [{"year": 2026, "rows": [dict(row, a="x", b="y,b=z")]}])
    v1 = V.open_vessel(str(root))
    label1 = next(x.scope for x in V.values_of(v1.get("t:a"), v1.kinds["t"], v1.get("t:a").units[0])
                  if x.name == "value")
    assert label0 == label1 == "a=x,b=y,b=z"
    assert J.evaluate(v1, rj) == "stale"


# ---- 三回めの 3（範囲の空と空白）・人が範囲を書ける所

def _kanto(env):
    from tests.test_register_vessel import copy
    return copy(env, "kanto")


def _male(v):
    from tests.test_register_vessel import A1, MALE_2026, unit_of
    return A1, unit_of(v, A1, **MALE_2026)


def test_三3_空と空白の範囲は未宣言(env):
    v = V.open_vessel(str(_kanto(env)))
    ref, male = _male(v)
    target = {"record": ref, "unit": male.pointer, "value": "capacity"}
    for bad in ("", " ", "\t", 0, True, []):
        with pytest.raises(V.RegisterRefused) as caught:
            J.make(v, target, "reads_right", by="a", at="2026-11-14", conclusion="good", scope=bad)
        assert caught.value.code == "register_args_invalid"
    ok = J.make(v, target, "reads_right", by="a", at="2026-11-14", conclusion="good", scope="  男女の合計 ")
    assert ok["answer"]["scope"] == "男女の合計"
    assert J.answer_value(v, [ok], ref, male.pointer, "capacity", as_scope="男女の合計")["usable"] is True
    plain = J.make(v, target, "reads_right", by="a", at="2026-11-14", conclusion="good")
    for blank in ("", " "):
        crafted = json.loads(json.dumps(plain))
        crafted["answer"]["scope"] = blank
        ans = J.answer_value(v, [crafted], ref, male.pointer, "capacity")
        assert ans["usable"] is False and ans["reason_kind"] == "scope_undeclared"
    for blank in ("", " "):
        with pytest.raises(V.RegisterRefused) as caught:
            J.answer_value(v, [ok], ref, male.pointer, "capacity", as_scope=blank)
        assert caught.value.code == "register_args_invalid"


def _fees_capacity(v, round_name):
    from tests.test_register_vessel import unit_of
    unit = unit_of(v, "fees:142", round=round_name)
    [value] = [x for x in V.values_of(v.get("fees:142"), v.kinds["fees"], unit) if x.name == "capacity"]
    return unit, value


def test_三3_範囲の欄が空白だけなら未宣言(env):
    from tests.test_register_vessel import edit_json
    root = _kanto(env)
    edit_json(root / "fees" / "142.json",
              lambda d: d["admissions_extra"]["rounds"][1].__setitem__("capacity_shared", " "))
    _unit, value = _fees_capacity(V.open_vessel(str(root)), "帰国生入学試験")
    assert value.scope is None and value.scope_kind == "separate"


def test_三3_人が範囲を書けるのは未宣言の値だけ(env):
    v = V.open_vessel(str(_kanto(env)))
    ref, male = _male(v)
    shared, _value = _fees_capacity(v, "帰国生入学試験")
    cases = [({"record": ref, "unit": male.pointer}, "男子", ref, male.pointer, "applicants"),
             ({"record": ref, "unit": male.pointer, "value": "applicants"}, "男子", ref, male.pointer, "applicants"),
             ({"record": "fees:142", "unit": shared.pointer, "value": "capacity"}, "合算", "fees:142",
              shared.pointer, "capacity")]
    for target, said, aref, apointer, aname in cases:
        with pytest.raises(V.RegisterRefused) as caught:
            J.make(v, target, "reads_right", by="a", at="2026-11-14", conclusion="good", scope=said)
        assert caught.value.code == "register_args_invalid"
        crafted = J.make(v, target, "reads_right", by="a", at="2026-11-14", conclusion="good")
        crafted["answer"]["scope"] = said
        assert J.evaluate(v, crafted) == "effective"
        ans = J.answer_value(v, [crafted], aref, apointer, aname)
        assert ans["usable"] is False and ans["reason_kind"] == "scope_conflict"


# ---- 三回めの 4・5（時点は値の指し先）

PER_FIELD = {"group": "year", "per_field": "field_years"}


def test_三4_親の年度が進んでも自分の時点を持つ値の判断は効いたまま(env):
    row = {"key": "a", "value": 10, "other": 20, "field_years": {"value": 2026}}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}], time=PER_FIELD, not_values=["key"])
    v0 = V.open_vessel(str(root))
    [unit] = v0.get("t:a").units
    assert unit.pointer.startswith("k:")
    vj, oj, rj = _val_j(v0, unit.pointer, "value"), _val_j(v0, unit.pointer, "other"), _row_j(v0, unit.pointer)
    _write(root, [{"year": 2027, "rows": [row]}])
    v1 = V.open_vessel(str(root))
    assert [u.pointer for u in v1.get("t:a").units] == [unit.pointer]
    assert J.evaluate(v1, vj) == "effective"
    assert J.answer_value(v1, [vj], "t:a", unit.pointer, "value", as_time=2026)["usable"] is True
    assert J.evaluate(v1, oj) == "vanished"
    ans = J.answer_value(v1, [oj], "t:a", unit.pointer, "other")
    assert ans["usable"] is False and ans["reason_kind"] == "no_judgment" and ans["time"] == 2027
    assert J.answer_value(v1, [oj, rj], "t:a", unit.pointer, "other")["reason_kind"] == \
        "no_inspection_for_new_version"
    assert J.evaluate(v1, rj) == "stale"


def test_三5_値でない欄に付いた年度の違いでは断らない(env):
    row = {"key": "a", "value": 10, "field_years": {"value": 2026}}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}, {"year": 2027, "rows": [row]}], time=PER_FIELD,
                  not_values=["key"])
    v = V.open_vessel(str(root))
    rec = v.get("t:a")
    [pointer] = {u.pointer for u in rec.units}
    # 中身まで同じ 2 行は区別しない（五回めの検収の 2 で決まりを直した: 鍵で指す `k:`。設計 §5.1）
    assert pointer.startswith("k:") and [u.same_content for u in rec.units] == [2, 2] and rec.problems == []
    vj, rj = _val_j(v, pointer, "value"), _row_j(v, pointer)
    assert J.evaluate(v, vj) == J.evaluate(v, rj) == "effective"
    assert J.answer_value(v, [vj], "t:a", pointer, "value", as_time=2026)["usable"] is True


def test_三5_親の年度を時点にする値があれば別の指し先(env):
    row = {"key": "a", "value": 10, "other": 20, "field_years": {"value": 2026}}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}, {"year": 2027, "rows": [row]}], time=PER_FIELD,
                  not_values=["key"])
    v = V.open_vessel(str(root))
    rec = v.get("t:a")
    pointers = sorted({u.pointer for u in rec.units})
    assert len(pointers) == 2 and all(p.startswith("kc:") for p in pointers) and rec.problems == []
    for p in pointers:
        assert J.evaluate(v, _val_j(v, p, "other")) == "effective"


# ---- この決まりで初めて言えること

def test_三7_親の出典だけ変えても鍵で決まらない行の指し先は変わらない(env):
    from tests.test_register_vessel import A312, edit_json
    root = _kanto(env)

    def second_source(d):
        d["years"][0]["sources"].append({"field": "admitted", "url": "https://example.invalid/312/admitted",
                                         "quote": "合格者", "retrieved": "2026-08-20"})
    edit_json(root / "admission_results" / "312.json", second_source)
    v0 = V.open_vessel(str(root))
    dup = [u for u in v0.get(A312).units if u.ambiguous]
    assert len(dup) == 2 and all(u.pointer.startswith("kc:") for u in dup)
    js = {(u.pointer, n): J.make(v0, {"record": A312, "unit": u.pointer, "value": n}, "reads_right", by="a",
                                 at="2026-11-14", conclusion="good") for u in dup for n in ("admitted", "capacity")}
    edit_json(root / "admission_results" / "312.json",
              lambda d: d["years"][0]["sources"][1].__setitem__("quote", "合格者（書き換え）"))
    v1 = V.open_vessel(str(root))
    assert sorted(u.pointer for u in v1.get(A312).units if u.ambiguous) == sorted(u.pointer for u in dup)
    states = {k: J.evaluate(v1, j) for k, j in js.items()}
    assert all(s == ("stale" if name == "admitted" else "effective") for (_p, name), s in states.items())


def test_三7_親の出典だけが違う同じ姿の行は健康に出る(env):
    groups = [{"year": 2026, "sources": [{"field": "rows", "url": "u1"}], "rows": [{"key": "a", "value": 10}]},
              {"year": 2026, "sources": [{"field": "rows", "url": "u2"}], "rows": [{"key": "a", "value": 10}]}]
    root = _tiny3(env, groups, sources=True)
    v = V.open_vessel(str(root))
    rec = v.get("t:a")
    [pointer] = {u.pointer for u in rec.units}
    with pytest.raises(V.RegisterRefused) as caught:
        _val_j(v, pointer, "value")
    assert caught.value.code == "register_unit_ambiguous"
    assert "unit_parent_differs" in rec.problems


def test_三8_行の姿と中身の指紋を実装を使わずに組んで比べる(env):
    from tests.test_register_vessel_holes import _paragraph_fp
    row = {"key": "a", "value": 10, "field_years": {"value": 2026}}
    other = {"key": "a", "value": 11, "field_years": {"value": 2026}}  # 中身の違う同じ鍵の行（鍵で決まらない）
    root = _tiny3(env, [{"year": 2026, "rows": [row]}, {"year": 2027, "rows": [other]}], time=PER_FIELD,
                  not_values=["key"])
    unit = V.open_vessel(str(root)).get("t:a").units[0]
    assert unit.pointer.startswith("kc:") and unit.item == row
    shape = _paragraph_fp({"key": {"value": "a", "time": None}, "value": {"value": 10, "time": 2026},
                           "field_years": {"value": {"value": 2026}, "time": None}})
    assert unit.pointer.split(".")[1] == shape[:16]
    content = _paragraph_fp({
        "key": {"value": "a", "scope": None, "time": None, "sources": []},
        "value": {"value": 10, "scope": {"key": {}}, "time": 2026, "sources": []},
        "field_years": {"value": {"value": 2026}, "scope": None, "time": None, "sources": []}})
    assert unit.content == content


# ---- 前回挙げた 1 つ: 出典の並びの中の辞書でない要素

def test_三9_出典の並びの辞書でない要素も依る所に入る(env):
    groups = [{"year": 2026, "sources": [{"field": "rows", "url": "u1"}, "手で書いた出典"],
               "rows": [{"key": "a", "value": 10}]}]
    root = _tiny3(env, groups, sources=True)
    v0 = V.open_vessel(str(root))
    [unit] = v0.get("t:a").units
    vj = _val_j(v0, unit.pointer, "value")
    groups[0]["sources"][1] = "手で書いた出典（書き換え）"
    _write(root, groups)
    assert J.evaluate(V.open_vessel(str(root)), vj) == "stale"
