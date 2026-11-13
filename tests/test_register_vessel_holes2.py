"""台帳の器 段 1 — Codex の再検収（`~/Documents/THTH/codex/答え_段1の再検収_2026-11-14.md`）の穴と、同じ型の穴を突く試験。

穴ごとに、直す前に赤を見てから直した。
1. 依る欄を狭めた行への判断が、依らない値にも当たる
2. 検収の記録を持つ表が、番号の重なった記録の先頭を選ぶ（同じ型: `links`・健康の数えが先頭を選ぶ）
3. 値ごとの時点があるのに、まとまりの年度を鍵に足して指し先が変わる
4. 中央の行の正当な空のセルで、一致する検収を捨てる
5. 依らない親の注の違いだけで、同じ単位を曖昧として断る
6. 読み口から例外が漏れる（YAML の NUL・孤立したサロゲート。同じ型: JSON の枚と並び・判断の記録）
7. `links --key` が同じ番号の別々の単位を黙って 1 つに畳む
8. 検収の記録を持つ表の真偽値の状態を、語に直さない
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth.register import judgment as J, vessel as V
from tests.test_register_vessel import (AT, CLAIM, NEW, add, copy, edit_json, env, jrun, rewrite_csv,  # noqa: F401
                                        yaml_needed)

SR = "school_record:142"


# ================================================================== 穴 1

def _kanto_with_question(env, question: dict) -> pathlib.Path:
    root = copy(env, "kanto")
    decl = root / ".thth" / "kinds" / "school_record.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    data["questions"] = [question]
    decl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def test_再1_依る欄を狭めた行への判断は依る欄の値にだけ当たる(env):
    root = _kanto_with_question(env, {"name": "reads_right", "level": "unit", "depends": ["capacity"]})
    v0 = V.open_vessel(str(root))
    [unit] = v0.get(SR).units
    j = J.make(v0, {"record": SR, "unit": unit.pointer}, "reads_right", by="audit", at=AT, conclusion="good")
    assert J.answer_value(v0, [j], SR, unit.pointer, "capacity")["usable"] is True
    other = J.answer_value(v0, [j], SR, unit.pointer, "subject_options")
    assert other["usable"] is False and other["reason_kind"] == "no_judgment"

    def next_year(d):
        d["admissions"]["rounds"][0]["field_years"]["subject_options"] = 2027
        d["admissions"]["rounds"][0]["subject_options"] = "4科"
    edit_json(root / "school_records" / "142.json", next_year)
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, j) == "effective"  # 依る欄（定員）は変わっていない
    ans = J.answer_value(v1, [j], SR, unit.pointer, "subject_options", as_time=2027)
    assert ans["usable"] is False and ans["conclusion"] == "pending"


def test_再1_依る欄を宣言しない行への判断は値の欄の全部に当たり年度が変われば古い(env):
    root = _kanto_with_question(env, {"name": "reads_right", "level": "unit"})
    v0 = V.open_vessel(str(root))
    [unit] = v0.get(SR).units
    j = J.make(v0, {"record": SR, "unit": unit.pointer}, "reads_right", by="audit", at=AT, conclusion="good")
    assert J.answer_value(v0, [j], SR, unit.pointer, "subject_options")["usable"] is True
    edit_json(root / "school_records" / "142.json",
              lambda d: d["admissions"]["rounds"][0]["field_years"].__setitem__("subject_options", 2027))
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, j) == "stale"
    assert J.answer_value(v1, [j], SR, unit.pointer, "subject_options", as_time=2027)["usable"] is False


# ================================================================== 穴 2

def _central_matches_milk(root: pathlib.Path) -> None:
    central = root / "data" / "knowledge" / "staging" / "k1" / "central.csv"
    rewrite_csv(root / "data" / "knowledge" / "claims.csv", CLAIM.split(":")[1], statement_ja=NEW,
                object_text_ja="水浸出物 17.1%")
    assert central.exists()


@pytest.mark.parametrize("first", ["milk", "brazil"])
def test_再2_番号の重なった根拠からは検収の記録を作らない(env, first):
    root = copy(env, "kopicha")
    _central_matches_milk(root)
    path = root / "data" / "knowledge" / "evidence.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    to_brazil = "e-milk-01,c-origin-brazil-024,s-9,supports,,,ナッツ,,,,v_checked"
    milk = next(i for i, line in enumerate(lines) if line.startswith("e-milk-01,"))
    lines.insert(milk if first == "brazil" else milk + 1, to_brazil)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = V.open_vessel(str(root))
    found = J.inspections(v)
    assert all(j["source"] != "central:e-milk-01" for j in found)
    assert J.answer_record(v, found, CLAIM, "says_so")["conclusion"] == "pending"


def test_再2_番号の重なった先へは来歴の控えも健康の数えも先頭を選ばない(capsys, env):
    root = copy(env, "kopicha")
    path = root / "data" / "knowledge" / "claims.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    dup = next(line for line in lines if line.startswith(CLAIM.split(":")[1] + ","))
    lines.append(dup.replace("17.3%", "99%"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = V.open_vessel(str(root))
    graph = V.links(v)
    assert all(e["to"] != CLAIM for e in graph["review"])
    assert any(e["to"] == CLAIM for e in graph["ambiguous"])


# ================================================================== 穴 3・5（時点と、比べるのは依る所）

def _tiny(env, groups: list, *, time=None, key=("key",), sources=False) -> pathlib.Path:
    root = env["tmp"] / "tiny"
    (root / ".thth" / "kinds").mkdir(parents=True, exist_ok=True)
    units = {"groups": ["years", "*"], "items": ["rows", "*"], "key": list(key), "time": time or {"group": "year"}}
    if sources:
        units["sources"] = {"list": "sources", "field": "field"}
    (root / ".thth" / "kinds" / "t.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "t", "place": "t/*.json", "format": "json", "id": {"name": True},
        "units": units}), encoding="utf-8")
    (root / "t").mkdir(exist_ok=True)
    (root / "t" / "a.json").write_text(json.dumps({"years": groups}, ensure_ascii=False), encoding="utf-8")
    return root


PER_FIELD = {"group": "year", "per_field": "field_years"}


def test_再3_値ごとの時点があれば親の年度だけ変えても判断は効いたまま(env):
    row = {"key": "a", "subject_options": "2科", "field_years": {"subject_options": 2026}}
    root = _tiny(env, [{"year": 2026, "rows": [row]}], time=PER_FIELD)
    v0 = V.open_vessel(str(root))
    [unit] = v0.get("t:a").units
    j = J.make(v0, {"record": "t:a", "unit": unit.pointer, "value": "subject_options"}, "reads_right", by="x",
               at=AT, conclusion="good")
    edit_json(root / "t" / "a.json", lambda d: d["years"][0].__setitem__("year", 2027))
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, j) == "effective"
    assert J.answer_value(v1, [j], "t:a", unit.pointer, "subject_options", as_time=2026)["usable"] is True


def test_再3_値ごとの時点が無い値があれば行の姿で別の指し先になる(env):
    row = {"key": "a", "value": 10, "field_years": {}}
    root = _tiny(env, [{"year": 2026, "rows": [row]}, {"year": 2027, "rows": [row]}], time=PER_FIELD)
    rec = V.open_vessel(str(root)).get("t:a")
    assert len({u.pointer for u in rec.units}) == 2 and rec.problems == []
    assert all(u.pointer.startswith("kc:") for u in rec.units)


def test_再5_依らない親の注だけが違う同じ単位は断らずどちらにも当たる(env):
    root = _tiny(env, [{"year": 2026, "note": "A", "rows": [{"key": "a", "value": 10}]},
                       {"year": 2026, "note": "B", "rows": [{"key": "a", "value": 10}]}])
    v = V.open_vessel(str(root))
    rec = v.get("t:a")
    [pointer] = {u.pointer for u in rec.units}
    j = J.make(v, {"record": "t:a", "unit": pointer, "value": "value"}, "reads_right", by="x", at=AT,
               conclusion="good")
    assert J.evaluate(v, j) == "effective"


def test_再5_親の出典が違う同じ指し先の単位は断る(env):
    root = _tiny(env, [{"year": 2026, "sources": [{"field": "rows", "url": "u1"}], "rows": [{"key": "a", "value": 10}]},
                       {"year": 2026, "sources": [{"field": "rows", "url": "u2"}], "rows": [{"key": "a", "value": 10}]}],
                 sources=True)
    v = V.open_vessel(str(root))
    [pointer] = {u.pointer for u in v.get("t:a").units}
    with pytest.raises(V.RegisterRefused) as caught:
        J.make(v, {"record": "t:a", "unit": pointer, "value": "value"}, "reads_right", by="x", at=AT,
               conclusion="good")
    assert caught.value.code == "register_unit_ambiguous"


# ================================================================== 穴 4

def test_再4_正当な空のセルの検収は捨てず文や状態の空は判断にしない(env):
    root = copy(env, "kopicha")
    rewrite_csv(root / "data" / "knowledge" / "claims.csv", CLAIM.split(":")[1], statement_ja=NEW, object_text_ja="")
    central = root / "data" / "knowledge" / "staging" / "k1" / "central.csv"
    rewrite_csv(central, "e-milk-01", object_text_ja="")
    v = V.open_vessel(str(root))
    [milk] = [j for j in J.inspections(v) if j["source"] == "central:e-milk-01"]
    assert milk["content"]["object_text_ja"] == "" and J.evaluate(v, milk) == "effective"
    rewrite_csv(central, "e-milk-01", statement_ja="")
    assert not [j for j in J.inspections(V.open_vessel(str(root))) if j["source"] == "central:e-milk-01"]


# ================================================================== 穴 6

@yaml_needed
@pytest.mark.parametrize("text, word", [('x: "\x00"\n', "syntax:1"), ('a: 1\nx: "\\uD800"\n', "surrogate:2"),
                                        ('x: "\\udfff"\n', "surrogate:1")])
def test_再6_YAMLのNULと孤立したサロゲートは理由と行で断る(capsys, env, text, word):
    assert V.yamlread.load(text) == (None, word, True)
    root = copy(env, "kopicha")
    (root / "src" / "content" / "origins" / "esc.yaml").write_text(text, encoding="utf-8")
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_yaml_refused"


def test_再6_JSONの枚と並びの孤立したサロゲートは記録の問題で断る(capsys, env):
    root = copy(env, "kanto")
    (root / "admission_results" / "9.json").write_text('{"school_id": "9", "years": [{"year": 2026, "note": "\\ud800",'
                                                       ' "rounds": []}]}', encoding="utf-8")
    v = V.open_vessel(str(root))
    assert "surrogate" in v.get("admission_result:9").problems
    add(capsys, root, "kanto")
    rc_, value, _ = jrun(capsys, "register", "check", "kanto")
    assert rc_ == 2 and value["code"] == "register_record_invalid"


def test_再6_判断の記録の孤立したサロゲートは行で断る(env):
    root = copy(env, "kopicha")
    line = json.dumps({"schema": J.SCHEMA, "target": {"record": CLAIM}, "question": "says_so",
                       "content": {"statement_ja": "\ud800"}})
    (root / ".thth" / "judgments.jsonl").write_text(line + "\n", encoding="utf-8")
    with pytest.raises(V.RegisterRefused) as caught:
        J.read(str(root))
    assert caught.value.code == "register_judgment_invalid"


# ================================================================== 穴 7

def test_再7_鍵で引いても同じ番号の別々の単位は断る(capsys, env):
    root = env["tmp"] / "tiny"
    (root / ".thth" / "kinds").mkdir(parents=True)
    (root / ".thth" / "kinds" / "t.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "t", "place": "t/*.json", "format": "json", "id": {"name": True},
        "units": {"items": ["rows", "*"], "id": "uid", "key": ["key"]}}), encoding="utf-8")
    (root / "t").mkdir()
    (root / "t" / "a.json").write_text(json.dumps({"rows": [{"uid": "x", "key": "a", "value": 10},
                                                            {"uid": "x", "key": "a", "value": 20}]}), encoding="utf-8")
    with pytest.raises(V.RegisterRefused) as caught:
        V.find_units(V.open_vessel(str(root)).get("t:a"), {"key": "a"})
    assert caught.value.code == "register_unit_ambiguous"
    add(capsys, root, "tiny")
    rc_, value, _ = jrun(capsys, "register", "links", "tiny", "t:a", "--key", "key=a")
    assert rc_ == 2 and value["code"] == "register_unit_ambiguous"


# ================================================================== 穴 8

def test_再8_検収の記録の真偽値の状態は観測と同じ語に直す(env):
    root = env["tmp"] / "bool"
    kinds = root / ".thth" / "kinds"
    kinds.mkdir(parents=True)
    (kinds / "doc.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "doc", "place": "doc/*.json", "format": "json", "id": {"name": True},
        "questions": [{"name": "says_so", "state": "status", "depends": ["statement"]}]}), encoding="utf-8")
    (kinds / "chk.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "chk", "place": "chk/*.json", "format": "json", "id": {"name": True},
        "pointers": [{"field": "doc", "meaning": "about", "to": "doc"}],
        "inspects": {"follow": ["doc"], "question": "says_so", "by": "central",
                     "content": {"statement": "statement", "status": "status"}}}), encoding="utf-8")
    (root / ".thth" / "constitution.json").write_text(json.dumps({
        "schema": "thth.constitution/1", "words": {"says_so": {"true": {"conclusion": "good"}}}}), encoding="utf-8")
    (root / "doc").mkdir()
    (root / "chk").mkdir()
    (root / "doc" / "d.json").write_text(json.dumps({"statement": "hello", "status": True}), encoding="utf-8")
    (root / "chk" / "c.json").write_text(json.dumps({"doc": "d", "statement": "hello", "status": True}),
                                         encoding="utf-8")
    v = V.open_vessel(str(root))
    assert V.problems(v) == []
    [j] = J.inspections(v)
    assert j["answer"]["word"] == V.observations(v.kinds["doc"], v.get("doc:d"))[0]["word"] == "true"
    assert J.evaluate(v, j) == "effective"
    assert J.answer_record(v, [j], "doc:d", "says_so")["conclusion"] == "good"


# ================================================================== 自分で探した同じ型の穴（読み口・黙って畳む所）

def test_探1_JSONの深い入れ子は例外を漏らさず記録の問題で断る(capsys, env):
    root = copy(env, "kanto")
    for name, depth in (("8", 300), ("9", 5000)):
        (root / "admission_results" / f"{name}.json").write_text(
            '{"school_id": "%s", "x": %s%s}' % (name, "[" * depth, "]" * depth), encoding="utf-8")
    v = V.open_vessel(str(root))
    assert v.get("admission_result:8").problems == ["too_deep"]
    assert v.get("admission_result:9").problems in (["json"], ["too_deep"])
    add(capsys, root, "kanto")
    rc_, value, _ = jrun(capsys, "register", "check", "kanto")
    assert rc_ == 2 and value["code"] == "register_record_invalid"


@yaml_needed
def test_探1_YAMLの深い入れ子は理由と行で断る():
    assert V.yamlread.load("a: 1\nb: " + "[" * 300 + "]" * 300 + "\n") == (None, "too_deep:2", True)


def test_探1_判断の記録の深い入れ子は行で断る(env):
    root = copy(env, "kopicha")
    (root / ".thth" / "judgments.jsonl").write_text("[" * 5000 + "]" * 5000 + "\n", encoding="utf-8")
    with pytest.raises(V.RegisterRefused) as caught:
        J.read(str(root))
    assert caught.value.code == "register_judgment_invalid"


def test_探2_表の大きすぎるセルは例外を漏らさず行の問題で断る(capsys, env):
    root = copy(env, "kopicha")
    path = root / "data" / "knowledge" / "claims.csv"
    with path.open("a", encoding="utf-8") as f:
        f.write("c-big,origin-brazil,has_property,,x,\"" + "長" * 200_000 + "\",sensory,,supported,,,,\n")
    v = V.open_vessel(str(root))
    assert any("csv" in p["words"] for p in V.problems(v))
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_record_invalid"


def test_探3_表の見出しの同じ名前は黙って後ろの列に畳まず断る(env):
    root = copy(env, "kopicha")
    path = root / "data" / "knowledge" / "claims.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("object_text_ja", "statement_ja")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = V.open_vessel(str(root))
    claims = [r for r in v.all_records if r.kind == "claim"]
    assert claims and all("csv_header_duplicate" in r.problems and r.values is None for r in claims)


def test_探3_番号の表で同じ置き場を2つの番号に当てると断る(env):
    root = copy(env, "kopicha")
    (root / ".thth" / "ids.json").write_text(json.dumps({"schema": "thth.ids/1", "ids": {
        "page:a": "src/content/origins/brazil.yaml", "page:b": "src/content/origins/brazil.yaml"}}), encoding="utf-8")
    with pytest.raises(V.RegisterRefused) as caught:
        V.open_vessel(str(root))
    assert caught.value.code == "register_kind_invalid"
