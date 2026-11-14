"""台帳の器 段 4 §6「器の不足の直し」G1〜G4・G6〜G8（G5・G9〜G11 は `tests/test_register_checks.py`）。

指示 `docs/指示_台帳の器_段4_検査の形と済みの線_2026-11-14.md` §6。段 1 の芯（指し先・依る所・及ぶ範囲・前から
読めていた番号の読み）は変えない。新しい欄は、どの値を書かれても宣言の断り（例外を漏らさない）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import unicodedata

import pytest

from thth.register import checks as C, vessel as V
from thth.register.errors import RegisterRefused

yaml_needed = pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")


def csv_text(header, rows) -> str:
    out = [",".join(header)]
    for row in rows:
        out.append(",".join(str(row.get(h, "")) for h in header))
    return "\n".join(out) + "\n"


def make_ledger(base: pathlib.Path, kinds: list, files: dict) -> pathlib.Path:
    (base / ".thth" / "kinds").mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        (base / ".thth" / "kinds" / f"{kind['kind']}.json").write_text(json.dumps(kind, ensure_ascii=False),
                                                                       encoding="utf-8")
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return base


def kind(name, place, fmt="csv", **extra):
    return {"schema": "thth.kind/1", "kind": name, "place": place, "format": fmt, **extra}


def refs(vessel, name=None):
    return sorted(r.ref for r in vessel.all_records if r.id is not None and (name is None or r.kind == name))


def parse_fails(raw, word):
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(raw, ".thth/kinds/x.json")
    assert info.value.word == word


# ================================================================== G1 台帳のたどりは 1 回

def test_G1_台帳のたどりは種類の数によらず1回(tmp_path, monkeypatch):
    kinds = [kind(f"k{i}", f"d{i}/*.csv", id={"field": "id"}) for i in range(5)]
    root = make_ledger(tmp_path / "l", kinds, {f"d{i}/t.csv": "id\na\n" for i in range(5)})
    calls = []
    real = os.walk

    def spy(*a, **k):
        calls.append(a[0])
        return real(*a, **k)
    monkeypatch.setattr(V.os, "walk", spy)
    v = V.open_vessel(str(root))
    assert refs(v) == [f"k{i}:a" for i in range(5)]
    assert len(calls) == 1


def test_G1_どの置き場の型にも当たらないフォルダには降りない_読む記録は前と同じ(tmp_path, monkeypatch):
    kinds = [kind("a", "data/*/rows.csv", id={"field": "id"}), kind("b", "top.csv", id={"field": "id"}),
             kind("c", "da*/x.json", "json")]
    files = {"data/q1/rows.csv": "id\nr1\n", "data/q2/rows.csv": "id\nr2\n", "top.csv": "id\nt\n",
             "dax/x.json": "{}", "node_modules/p/rows.csv": "id\nno\n", "other/top.csv": "id\nno\n"}
    root = make_ledger(tmp_path / "l", kinds, files)
    seen = []
    real = os.walk

    def spy(top, *a, **k):
        for directory, dirs, names in real(top, *a, **k):
            seen.append(os.path.relpath(directory, top))
            yield directory, dirs, names
    monkeypatch.setattr(V.os, "walk", spy)
    v = V.open_vessel(str(root))
    assert refs(v) == ["a:r1", "a:r2", "b:t", "c:x"]
    assert not any(d.startswith(("node_modules", "other")) for d in seen)


# ================================================================== G2 置き場の中でだけ一意な番号

def _folder_ledger(tmp, studies_scope=True, extra_files=None):
    study = kind("study", "q/*/studies.csv", id={"field": "id", **({"scope": "folder"} if studies_scope else {})})
    result = kind("result", "q/*/results.csv", id={"field": "id", "scope": "folder"},
                  pointers=[{"field": "study_id", "to": "study", "meaning": "about", "same_folder": True}])
    files = {"q/qa/studies.csv": "id\ns1\ns2\n", "q/qb/studies.csv": "id\ns1\n",
             "q/qa/results.csv": "id,study_id\nr1,s2\nr2,s9\n", "q/qb/results.csv": "id,study_id\nr1,s1\n",
             **(extra_files or {})}
    return make_ledger(tmp / "l", [study, result], files)


def test_G2_番号はフォルダの名と点でつなぎ_置き場をまたぐ重なりは無い(tmp_path):
    v = V.open_vessel(str(_folder_ledger(tmp_path)))
    assert refs(v, "study") == ["study:qa.s1", "study:qa.s2", "study:qb.s1"]
    assert V.problems(v) == []


def test_G2_same_folderの指す欄は同じフォルダの番号として引く(tmp_path):
    v = V.open_vessel(str(_folder_ledger(tmp_path)))
    graph = V.links(v)
    assert sorted((e["from"], e["to"]) for e in graph["edges"]) == [
        ("result:qa.r1", "study:qa.s2"), ("result:qa.r2", "study:qa.s9"), ("result:qb.r1", "study:qb.s1")]
    assert [e["to"] for e in graph["dangling"]] == ["study:qa.s9"]


def test_G2_フォルダの名に点があれば番号を付けず読みの問題(tmp_path):
    v = V.open_vessel(str(_folder_ledger(tmp_path, extra_files={"q/x.y/studies.csv": "id\ns1\n"})))
    bad = [p for p in V.problems(v) if p["path"] == "q/x.y/studies.csv"]
    assert bad and "id_folder" in bad[0]["words"]


def test_G2_scopeを書かない種類の番号は今のまま(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("study", "q/*/studies.csv", id={"field": "id"})],
                       {"q/qa/studies.csv": "id\ns1\ns2\n", "q/qb/studies.csv": "id\ns1\n"})
    v = V.open_vessel(str(root))
    assert refs(v, "study") == ["study:s1", "study:s1", "study:s2"]
    assert any("id_duplicate" in p["words"] for p in V.problems(v))


@pytest.mark.parametrize("scope", ["file", 1, None, True, ["folder"], {"a": 1}])
def test_G2_宣言の攻め_scope(scope):
    parse_fails(kind("s", "q/*/s.csv", id={"field": "id", "scope": scope}), "id")


def test_G2_宣言の攻め_置き場の型のすぐ上のフォルダの名に点があれば断る():
    parse_fails(kind("s", "q/a.b/s.csv", id={"field": "id", "scope": "folder"}), "id")


@pytest.mark.parametrize("same", [1, "yes", None, [], {}])
def test_G2_宣言の攻め_same_folder(same):
    parse_fails(kind("r", "r.csv", id={"field": "id"},
                     pointers=[{"field": "s", "to": "s", "meaning": "about", "same_folder": same}]), "pointers")


def test_G2_same_folderの先がscopeの無い種類なら宣言の断り(tmp_path):
    root = _folder_ledger(tmp_path, studies_scope=False)
    with pytest.raises(RegisterRefused) as info:
        V.open_vessel(str(root))
    assert info.value.code == "register_kind_invalid" and info.value.extra["declaration"] == ".thth/kinds/result.json"


# ================================================================== G3 ASCII でない番号

def test_G3_字と数字のUnicodeの番号を受けNFCにそろえる(tmp_path):
    nfd = unicodedata.normalize("NFD", "ガイド")
    root = make_ledger(tmp_path / "l", [kind("page", "pages/*.json", "json")],
                       {"pages/記事.json": "{}", f"pages/{nfd}.json": "{}", "pages/ロード2.json": "{}"})
    v = V.open_vessel(str(root))
    assert refs(v) == sorted(["page:ガイド", "page:ロード2", "page:記事"])
    assert all(unicodedata.is_normalized("NFC", r.id) for r in v.all_records)


@pytest.mark.parametrize("bad", ["a b", "a\tb", "a:b", "a#b", "a?b", "a*b", "記事\\x", "記 事", "・記事", "-記事",
                                 "記" * 54, "記事​"])
def test_G3_空白と制御の字と区切りの字と長すぎる番号は受けない(tmp_path, bad):
    root = make_ledger(tmp_path / "l", [kind("row", "rows.json", "json", records="rows", id={"field": "id"})],
                       {"rows.json": json.dumps([{"id": bad}])})
    v = V.open_vessel(str(root))
    assert refs(v) == [] and "id_form" in V.problems(v)[0]["words"]


def test_G3_前から読めていた番号の読みは変わらない(tmp_path):
    ids = ["a", "A-1", "a.b_c", "0x", "a" * 160, "1"]
    root = make_ledger(tmp_path / "l", [kind("row", "rows.json", "json", records="rows", id={"field": "id"})],
                       {"rows.json": json.dumps([{"id": i} for i in ids] + [{"id": 7}, {"id": "a" * 161}])})
    v = V.open_vessel(str(root))
    assert refs(v) == sorted(f"row:{i}" for i in ids + ["7"])


def test_G3_内側の単位の元の番号はASCIIのまま読む(tmp_path):
    k = kind("school", "s/*.json", "json", units={"groups": ["years", "*"], "items": ["rows", "*"], "id": "rid",
                                                  "key": ["course"]})
    root = make_ledger(tmp_path / "l", [k], {"s/a.json": json.dumps(
        {"years": [{"rows": [{"rid": "行1", "course": "c"}, {"rid": "r2", "course": "d"}]}]})})
    units = V.open_vessel(str(root)).get("school:a").units
    assert sorted(u.pointer[:2] for u in units) == ["i:", "k:"]


# ================================================================== G4 指す先なしの書き方

def test_G4_noneの字は指す先なしと読む(tmp_path):
    page = kind("page", "p.csv", id={"field": "id"},
                pointers=[{"field": "parent", "to": "page", "meaning": "parent", "none": ["null", "-", "~"]},
                          {"field": "tags", "to": "page", "meaning": "related", "split": ";", "none": ["-"]}])
    root = make_ledger(tmp_path / "l", [page], {"p.csv": csv_text(["id", "parent", "tags"], [
        {"id": "a", "parent": "null", "tags": "b;-"}, {"id": "b", "parent": " - ", "tags": "-"},
        {"id": "c", "parent": "~", "tags": "a;zz"}])})
    graph = V.links(V.open_vessel(str(root)))
    assert sorted((e["from"], e["to"]) for e in graph["edges"]) == [("page:a", "page:b"), ("page:c", "page:a"),
                                                                     ("page:c", "page:zz")]
    assert [e["to"] for e in graph["dangling"]] == ["page:zz"]
    assert graph["blank"] == []


def test_G4_noneは並びの要素にも効く(tmp_path):
    page = kind("page", "p/*.json", "json",
                pointers=[{"field": "refs", "to": "page", "meaning": "related", "none": ["null"]}])
    root = make_ledger(tmp_path / "l", [page], {"p/a.json": json.dumps({"refs": ["b", "null"]}),
                                                "p/b.json": json.dumps({"refs": "null"})})
    graph = V.links(V.open_vessel(str(root)))
    assert [(e["from"], e["to"]) for e in graph["edges"]] == [("page:a", "page:b")]


@pytest.mark.parametrize("none", ["null", [], [1], [None], [["x"]], [str(i) for i in range(17)], ["a\nb"],
                                  ["x" * 65], {"a": 1}, True])
def test_G4_宣言の攻め_none(none):
    parse_fails(kind("p", "p.csv", id={"field": "id"},
                     pointers=[{"field": "a", "to": "p", "meaning": "related", "none": none}]), "pointers")


# ================================================================== G6 YAML の並びを行として読む

@yaml_needed
def test_G6_YAMLでもrecords_rowsとrows_atで並びを行として読む(tmp_path):
    media = kind("media", "media.yaml", "yaml", records="rows", rows_at=["media"], id={"field": "id"},
                 checks=[{"check": "words", "id": "status", "field": "status", "in": ["live", "paused"]}])
    root = make_ledger(tmp_path / "l", [media], {"media.yaml": "media:\n  - id: a\n    status: live\n"
                                                                "  - id: b\n    status: gone\n"})
    v = V.open_vessel(str(root))
    assert refs(v) == ["media:a", "media:b"]
    assert v.get("media:a").values == {"id": "a", "status": "live"}
    out = C.run(v, [])
    assert [r["record"] for r in out["list"][0]["records"]] == ["media:b"]


@yaml_needed
def test_G6_YAMLの並びの読みの断りは今の枚と同じ(tmp_path):
    media = kind("media", "media.yaml", "yaml", records="rows", rows_at=["media"], id={"field": "id"})
    root = make_ledger(tmp_path / "l", [media], {"media.yaml": "media:\n  - &x {id: a}\n  - *x\n"})
    v = V.open_vessel(str(root))
    assert any(w.startswith("yaml:") for p in V.problems(v) for w in p["words"])


def test_G6_部品の無い機械では中は見ていない(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    media = kind("media", "media.yaml", "yaml", records="rows", rows_at=["media"], id={"field": "id"})
    root = make_ledger(tmp_path / "l", [media], {"media.yaml": "media:\n  - id: a\n"})
    v = V.open_vessel(str(root))
    assert len(v.all_records) == 1 and v.all_records[0].inspected is False and V.problems(v) == []


# ================================================================== G7 欄の組で指す

def _pair_ledger(tmp, names_key=("entity_id", "name"), fields=("entity_id", "matched_name")):
    names = kind("names", "names.csv", id={"key": list(names_key)})
    mentions = kind("mention", "mentions.csv", id={"field": "id"},
                    pointers=[{"fields": list(fields), "to": "names", "meaning": "about"}])
    return make_ledger(tmp / "l", [names, mentions], {
        "names.csv": csv_text(["entity_id", "name"], [{"entity_id": "e1", "name": "苦味"},
                                                       {"entity_id": "e2", "name": "bitter"}]),
        "mentions.csv": csv_text(["id", "entity_id", "matched_name"], [
            {"id": "m1", "entity_id": "e1", "matched_name": "苦味"}, {"id": "m2", "entity_id": "e1", "matched_name": "x"},
            {"id": "m3"}])})


def test_G7_欄の組で指し_先の鍵と同じ作り方で引く(tmp_path):
    v = V.open_vessel(str(_pair_ledger(tmp_path)))
    graph = V.links(v)
    target = "names:k-" + V.short(V.fp(["e1", "苦味"]))
    assert target in v.records
    assert [(e["from"], e["to"]) for e in graph["edges"]] == [("mention:m1", target),
                                                              ("mention:m2", graph["dangling"][0]["to"])]
    assert [e["from"] for e in graph["dangling"]] == ["mention:m2"]  # 組が全部空の m3 は指していない


@pytest.mark.parametrize("pointer", [
    {"fields": ["a"], "to": "n", "meaning": "about"},
    {"fields": "a", "to": "n", "meaning": "about"},
    {"fields": ["a", "a"], "to": "n", "meaning": "about"},
    {"fields": ["a", None], "to": "n", "meaning": "about"},
    {"fields": [f"f{i}" for i in range(17)], "to": "n", "meaning": "about"},
    {"fields": ["a", "b"], "field": "a", "to": "n", "meaning": "about"},
    {"fields": ["a", "b"], "to": "n", "meaning": "about", "split": ";"},
    {"fields": ["a", "b"], "to": "n", "meaning": "about", "recorded_version": {"field": "v", "of": ["a"],
                                                                                "hash": "sha1:12"}},
])
def test_G7_宣言の攻め_fields(pointer):
    parse_fails(kind("m", "m.csv", id={"field": "id"}, pointers=[pointer]), "pointers")


@pytest.mark.parametrize("names_key,fields", [(("entity_id",), ("entity_id", "matched_name")),
                                              (("entity_id", "name", "lang"), ("entity_id", "matched_name"))])
def test_G7_先が鍵の組の種類でないか組の数が違えば宣言の断り(tmp_path, names_key, fields):
    root = _pair_ledger(tmp_path, names_key, fields)
    if len(names_key) == 1:
        (root / ".thth/kinds/names.json").write_text(json.dumps(kind("names", "names.csv", id={"field": "name"})),
                                                     encoding="utf-8")
    with pytest.raises(RegisterRefused) as info:
        V.open_vessel(str(root))
    assert info.value.code == "register_kind_invalid" and info.value.extra["declaration"] == ".thth/kinds/mention.json"


# ================================================================== G8 入れ子の欄の道・内側の単位の指す欄

def test_G8_検査と条件の欄に点でつないだ道(tmp_path):
    row = kind("row", "rows.json", "json", records="rows", id={"field": "id"}, checks=[
        {"check": "words", "id": "verdict", "when": {"mb.state": {"in": ["done"]}}, "field": "mb.verdict",
         "in": ["ok", "ng"]},
        {"check": "required", "id": "deep", "fields": ["a.b.c.d"]}])
    root = make_ledger(tmp_path / "l", [row], {"rows.json": json.dumps([
        {"id": "1", "mb": {"state": "done", "verdict": "ok"}, "a": {"b": {"c": {"d": "x"}}}},
        {"id": "2", "mb": {"state": "done", "verdict": "maybe"}, "a": {"b": [{"c": {"d": "x"}}]}},
        {"id": "3", "mb": {"state": "todo"}, "a": "flat"},
        {"id": "4", "mb": ["state"]}])})
    out = {c["id"]: c for c in C.run(V.open_vessel(str(root)), [])["list"]}
    assert [r["record"] for r in out["verdict"]["records"]] == ["row:2"]
    assert [r["record"] for r in out["deep"]["records"]] == ["row:2", "row:3", "row:4"]  # 並びの中は読まない


@pytest.mark.parametrize("name", ["a.b.c.d.e", "a..b", ".a", "a.", "a.*", "a.0"])
def test_G8_宣言の攻め_道は4段まで_欄の名前だけ(name):
    parse_fails(kind("r", "r.csv", id={"field": "id"}, checks=[{"check": "required", "id": "x", "fields": [name]}]),
                "checks")
    parse_fails(kind("r", "r.csv", id={"field": "id"},
                     checks=[{"check": "required", "id": "x", "fields": ["a"], "when": {name: {"blank": True}}}]),
                "checks")


def _units_ledger(tmp):
    school = kind("school", "schools.csv", id={"field": "id"}, checks=[
        {"check": "count", "id": "rows", "from": {"kind": "exam", "via": "school_id"}, "min": 1}])
    exam = kind("exam", "exams/*.json", "json",
                units={"groups": ["years", "*"], "items": ["rows", "*"], "key": ["group.year", "course"]},
                pointers=[{"field": "school_id", "to": "school", "meaning": "about", "in_units": True}])
    return make_ledger(tmp / "l", [school, exam], {
        "schools.csv": "id\ns1\ns2\ns3\n",
        "exams/e1.json": json.dumps({"years": [{"year": "2025", "rows": [
            {"course": "a", "school_id": "s1"}, {"course": "b", "school_id": "s1"},
            {"course": "c", "school_id": "s2"}, {"course": "d", "school_id": "s9"}]}]})})


def test_G8_内側の単位の欄が指す(tmp_path):
    v = V.open_vessel(str(_units_ledger(tmp_path)))
    graph = V.links(v)
    assert sorted(e["to"] for e in graph["edges"]) == ["school:s1", "school:s2", "school:s9"]
    assert [e["to"] for e in graph["dangling"]] == ["school:s9"]
    rows = next(c for c in C.run(v, [])["list"] if c["id"] == "rows")
    assert [r["record"] for r in rows["records"]] == ["school:s3"]


def test_G8_内側の単位の指す欄は指し先と依る所を変えない(tmp_path):
    root = _units_ledger(tmp_path)
    before = [(u.pointer, u.content) for u in V.open_vessel(str(root)).get("exam:e1").units]
    data = json.loads((root / ".thth/kinds/exam.json").read_text(encoding="utf-8"))
    data.pop("pointers")
    (root / ".thth/kinds/exam.json").write_text(json.dumps(data), encoding="utf-8")
    school = json.loads((root / ".thth/kinds/school.json").read_text(encoding="utf-8"))
    school.pop("checks")
    (root / ".thth/kinds/school.json").write_text(json.dumps(school), encoding="utf-8")
    after = [(u.pointer, u.content) for u in V.open_vessel(str(root)).get("exam:e1").units]
    assert before == after


@pytest.mark.parametrize("extra", [{"in_units": 1}, {"in_units": "yes"}, {"in_units": None},
                                   {"in_units": True, "template": "x-{id}"},
                                   {"in_units": True, "recorded_version": {"field": "v", "of": ["a"],
                                                                           "hash": "sha1:12"}}])
def test_G8_宣言の攻め_in_units(extra):
    pointer = {"to": "s", "meaning": "about", **extra}
    if "template" not in pointer:
        pointer["field"] = "s"
    parse_fails(kind("e", "e/*.json", "json", units={"items": ["rows", "*"], "key": ["a"]}, pointers=[pointer]),
                "pointers")


def test_G8_内側の単位の宣言の無い種類にin_unitsは書けない():
    parse_fails(kind("e", "e.csv", id={"field": "id"},
                     pointers=[{"field": "s", "to": "s", "meaning": "about", "in_units": True}]), "pointers")
