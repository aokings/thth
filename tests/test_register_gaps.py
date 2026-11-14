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


# ================================================================== G12 頭に YAML を持つ .md（md_yaml）

MD_OK = "---\ntitle: 苦味の話\ncategory: tea\nparent: null\n---\n本文の 1 行目\n\n2 行目\n"


@yaml_needed
def test_G12_md_yamlは頭の段の鍵を欄に_本文は_body(tmp_path):
    art = kind("article", "articles/*.md", "md_yaml", max_bytes=200_000,
               pointers=[{"field": "parent", "to": "article", "meaning": "parent", "none": ["null"]}],
               checks=[{"check": "words", "id": "cat", "field": "category", "in": ["tea"]},
                       {"check": "required", "id": "title", "fields": ["title", "_body"]}])
    root = make_ledger(tmp_path / "l", [art], {"articles/a.md": MD_OK,
                                               "articles/b.md": "---\ntitle: B\ncategory: coffee\nparent: a\n---\n",
                                               "articles/big.md": "---\ntitle: X\ncategory: tea\n---\n" + "字" * 30000})
    v = V.open_vessel(str(root))
    a = v.get("article:a")
    assert a.inspected and a.values == {"title": "苦味の話", "category": "tea", "parent": None,  # 段 4 §9
                                        "_body": "本文の 1 行目\n\n2 行目\n"}
    assert a.version == V.bytes_version(MD_OK.encode("utf-8"))  # 版は全バイト
    assert V.problems(v) == []
    graph = V.links(v)
    assert [(e["from"], e["to"]) for e in graph["edges"]] == [("article:b", "article:a")]
    out = {c["id"]: c for c in C.run(v, [])["list"]}
    assert [r["record"] for r in out["cat"]["records"]] == ["article:b"]
    assert [r["record"] for r in out["title"]["records"]] == ["article:b"]  # 本文が空


@yaml_needed
def test_G12_番号は欄からも取れる_CRLFの頭も読む(tmp_path):
    art = kind("article", "a/*.md", "md_yaml", id={"field": "slug"})
    root = make_ledger(tmp_path / "l", [art], {})
    (root / "a").mkdir()
    (root / "a" / "x.md").write_bytes(b"---\r\nslug: hello\r\n---\r\nbody\r\n")
    v = V.open_vessel(str(root))
    assert refs(v) == ["article:hello"] and v.get("article:hello").values["_body"] == "body\r\n"


@yaml_needed
@pytest.mark.parametrize("text,word", [
    ("本文だけ\n", "md_head_missing"),
    ("---\ntitle: a\n本文\n", "md_head_open"),
    ("---\n- a\n- b\n---\n", "md_head_shape"),
    ("---\n_body: x\n---\n", "md_head_shape"),
    ("---\na: &x 1\nb: *x\n---\n", "yaml:"),
    ("---\na: 1\na: 2\n---\n", "yaml:"),
])
def test_G12_頭が無い_閉じていない_範囲の外の書き方は読みの問題(tmp_path, text, word):
    root = make_ledger(tmp_path / "l", [kind("article", "a/*.md", "md_yaml")], {"a/x.md": text})
    v = V.open_vessel(str(root))
    rec = v.all_records[0]
    assert rec.values is None and any(w.startswith(word) for w in rec.problems)


def test_G12_部品の無い機械では中は見ていない(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    root = make_ledger(tmp_path / "l", [kind("article", "a/*.md", "md_yaml")], {"a/x.md": MD_OK})
    rec = V.open_vessel(str(root)).get("article:x")
    assert rec.inspected is False and rec.values is None and rec.problems == []


def test_G12_今の店の台帳の読みは変わらない(tmp_path):
    from thth.register import form
    text = "---\nid: r1\nkind: menu\n---\n本文\n"
    root = make_ledger(tmp_path / "l", [kind("row", "rows/*.md", "thth_row")], {"rows/r1.md": text})
    rec = V.open_vessel(str(root)).get("row:r1")
    assert rec.values == dict(form.parse("rows/r1.md", text).head, body="本文\n") and hasattr(rec, "thth_row")


@pytest.mark.parametrize("raw", [kind("a", "a/*.md", "md_yaml", records="rows"),
                                 kind("a", "a/*.md", "md_yaml", rows_at=["x"]),
                                 kind("a", "a/*.md", "md_yaml", max_bytes=10 ** 12)])
def test_G12_宣言の攻め_md_yaml(raw):
    with pytest.raises(V.DeclarationError):
        V.parse_kind(raw, ".thth/kinds/a.json")


# ================================================================== G13 鍵の辞書を行として読む

def test_G13_JSONの鍵の辞書は鍵ごとに1行_番号は鍵(tmp_path):
    ken = kind("todofuken", "koyomi.json", "json", records="rows", rows_at=["todofuken"], id={"from": "key"},
               checks=[{"check": "words", "id": "shurui", "when": {"_value": {"blank": True}}, "field": "shurui",
                        "in": ["kimari", "kanko"]}])
    data = {"todofuken": {"tokyo": {"shurui": "kimari"}, "kanagawa": {"shurui": "x"}, "saitama": "字だけ",
                          "chiba": [1, 2]}}
    root = make_ledger(tmp_path / "l", [ken], {"koyomi.json": json.dumps(data, ensure_ascii=False)})
    v = V.open_vessel(str(root))
    assert refs(v) == ["todofuken:chiba", "todofuken:kanagawa", "todofuken:saitama", "todofuken:tokyo"]
    assert v.get("todofuken:saitama").values == {"_value": "字だけ"}
    assert v.get("todofuken:chiba").values == {"_value": [1, 2]}
    tokyo = v.get("todofuken:tokyo")
    assert tokyo.version == "v:" + V.fp({"key": "tokyo", "value": {"shurui": "kimari"}})
    assert [r["record"] for r in C.run(v, [])["list"][0]["records"]] == ["todofuken:kanagawa"]


def test_G13_鍵が変われば同じ中身でも版が変わる(tmp_path):
    ken = kind("t", "k.json", "json", records="rows", rows_at=["m"], id={"from": "key"})
    root = make_ledger(tmp_path / "l", [ken], {"k.json": json.dumps({"m": {"a": {"x": 1}, "b": {"x": 1}}})})
    v = V.open_vessel(str(root))
    assert v.get("t:a").version != v.get("t:b").version


def test_G13_鍵が番号の形でなければ読みの問題_並びはfrom_keyで番号が付かない(tmp_path):
    ken = kind("t", "k.json", "json", records="rows", rows_at=["m"], id={"from": "key"})
    lst = kind("u", "u.json", "json", records="rows", rows_at=["m"], id={"from": "key"})
    root = make_ledger(tmp_path / "l", [ken, lst], {"k.json": json.dumps({"m": {"a b": {}, "ok": {}}}),
                                                    "u.json": json.dumps({"m": [{"x": 1}]})})
    v = V.open_vessel(str(root))
    assert refs(v) == ["t:ok"]
    assert sorted(w for p in V.problems(v) for w in p["words"]) == ["id_form", "id_form"]


def test_G13_並びの読みは今のまま(tmp_path):
    lst = kind("u", "u.json", "json", records="rows", rows_at=["m"], id={"field": "id"})
    root = make_ledger(tmp_path / "l", [lst], {"u.json": json.dumps({"m": [{"id": "a"}]})})
    assert V.open_vessel(str(root)).get("u:a").version == "v:" + V.fp({"id": "a"})


@yaml_needed
def test_G13_YAMLの鍵の辞書も鍵ごとに1行(tmp_path):
    ken = kind("t", "k.yaml", "yaml", records="rows", rows_at=["m"], id={"from": "key"})
    root = make_ledger(tmp_path / "l", [ken], {"k.yaml": "m:\n  a:\n    x: 1\n  b: two\n"})
    v = V.open_vessel(str(root))
    assert refs(v) == ["t:a", "t:b"] and v.get("t:b").values == {"_value": "two"}


@pytest.mark.parametrize("rule", [{"from": "value"}, {"from": 1}, {"from": None}, {"from": "key", "field": "id"},
                                  {"from": "key", "key": ["a"]}, {"from": ["key"]}])
def test_G13_宣言の攻め_from(rule):
    parse_fails(kind("t", "k.json", "json", records="rows", rows_at=["m"], id=rule), "id")


def test_G13_from_keyは並びの記録だけ():
    parse_fails(kind("t", "k/*.json", "json", id={"from": "key"}), "id")


# ================================================================== G14 タブ区切りの表

def test_G14_タブ区切りの表を読み_行の版は元のバイト(tmp_path):
    owner = kind("owner", "owner-read.txt", delimiter="\t", id={"field": "id"},
                 checks=[{"check": "required", "id": "who", "fields": ["who"]}])
    text = "id\twho\tnote\na\tm\t\"x,y\"\nb\t\t\"改\n行\"\n"
    root = make_ledger(tmp_path / "l", [owner], {"owner-read.txt": text})
    v = V.open_vessel(str(root))
    assert refs(v) == ["owner:a", "owner:b"]
    assert v.get("owner:a").values == {"id": "a", "who": "m", "note": "x,y"}
    assert v.get("owner:b").values["note"] == "改\n行"
    assert v.get("owner:b").version == V.bytes_version("b\t\t\"改\n行\"\n".encode("utf-8"))
    assert [r["record"] for r in C.run(v, [])["list"][0]["records"]] == ["owner:b"]


def test_G14_delimiterを書かない表はコンマのまま(tmp_path):
    root = make_ledger(tmp_path / "l", [kind("t", "t.csv", id={"field": "id"})], {"t.csv": "id,a\nx,1\n"})
    assert V.open_vessel(str(root)).get("t:x").values == {"id": "x", "a": "1"}


@pytest.mark.parametrize("delim", [";", "|", "", ",,", 1, None, ["\t"], " "])
def test_G14_宣言の攻め_delimiter(delim):
    parse_fails(kind("t", "t.csv", delimiter=delim, id={"field": "id"}), "delimiter")


def test_G14_表でない形式にdelimiterは書けない():
    parse_fails(kind("t", "t.json", "json", delimiter="\t"), "delimiter")


# ================================================================== G15 行ごとに先の種類が変わる指す欄（to_by）

def _route_ledger(tmp, pointer, fix_rows, extra_kinds=()):
    claim = kind("claim", "claims.csv", id={"field": "id"}, questions=[{"name": "reads_right", "depends": ["text"]}])
    mood = kind("mood", "moods.csv", id={"field": "id"}, questions=[{"name": "reads_right", "depends": ["text"]}])
    fix = kind("fix", "fixes.json", "json", records="rows", rows_at=["fixes"], id={"field": "n"}, pointers=[pointer])
    return make_ledger(tmp / "l", [claim, mood, fix, *extra_kinds], {
        "claims.csv": "id,text,scope_ja\nclaim-1,a,x\nclaim-2,b,y\n",
        "moods.csv": "id,text\nmood-1,m\n",
        "fixes.json": json.dumps({"fixes": fix_rows}, ensure_ascii=False)})


FIX_ROWS = [{"n": "f1", "id": "claim-1", "file": "claims.csv"}, {"n": "f2", "id": "mood-1", "file": "moods.csv"},
            {"n": "f3", "id": "claim-9", "file": "claims.csv"}, {"n": "f4", "id": "dos-1", "file": "x.md"},
            {"n": "f5", "id": "", "file": ""}]


def test_G15_to_byの頭で先の種類を決め_当たらない行はunrouted(tmp_path):
    p = {"field": "id", "meaning": "about", "to_by": {"prefix": {"claim-": "claim", "mood-": "mood", "c": "mood"}}}
    graph = V.links(V.open_vessel(str(_route_ledger(tmp_path, p, FIX_ROWS))))
    assert sorted((e["from"], e["to"]) for e in graph["edges"]) == [
        ("fix:f1", "claim:claim-1"), ("fix:f2", "mood:mood-1"), ("fix:f3", "claim:claim-9")]  # 長い頭が先
    assert [e["to"] for e in graph["dangling"]] == ["claim:claim-9"]
    assert [(u["from"], u["target"]) for u in graph["unrouted"]] == [("fix:f4", "dos-1")]


def test_G15_to_byの別の欄の値で先の種類を決める(tmp_path):
    p = {"field": "id", "meaning": "about", "to_by": {"field": "file", "map": {"claims.csv": "claim",
                                                                             "moods.csv": "mood"}}}
    graph = V.links(V.open_vessel(str(_route_ledger(tmp_path, p, FIX_ROWS))))
    assert sorted(e["to"] for e in graph["edges"]) == ["claim:claim-1", "claim:claim-9", "mood:mood-1"]
    assert [u["from"] for u in graph["unrouted"]] == ["fix:f4"]


def test_G15_輪と来歴の控えも先の種類ごとに見る(tmp_path):
    p = {"field": "id", "meaning": "related", "to_by": {"prefix": {"claim-": "claim"}},
         "recorded_version": {"field": "sha", "of": ["text"], "hash": "sha1:12"}}
    import hashlib
    rows = [{"n": "f1", "id": "claim-1", "sha": hashlib.sha1(b"a").hexdigest()[:12]},
            {"n": "f2", "id": "claim-2", "sha": "000000000000"}]
    graph = V.links(V.open_vessel(str(_route_ledger(tmp_path, p, rows))))
    assert [(r["from"], r["to"]) for r in graph["review"]] == [("fix:f2", "claim:claim-2")]
    loop = kind("node", "nodes.csv", id={"field": "id"},
                pointers=[{"field": "parent", "meaning": "parent", "to_by": {"prefix": {"n": "node"}}}])
    root = make_ledger(tmp_path / "loop", [loop], {"nodes.csv": "id,parent\nn1,n2\nn2,n1\n"})
    assert V.links(V.open_vessel(str(root)))["cycles"]


@pytest.mark.parametrize("to_by", [
    None, "claim", [], {}, {"prefix": {}}, {"prefix": {"": "claim"}}, {"prefix": {"a": "Claim!"}},
    {"prefix": {f"p{i}": "claim" for i in range(33)}}, {"prefix": {"a\n": "claim"}}, {"prefix": ["claim-"]},
    {"field": "file"}, {"field": "file", "map": {}}, {"field": "a.b", "map": {"x": "claim"}},
    {"field": "file", "map": {f"m{i}": "claim" for i in range(65)}}, {"field": "file", "map": {"x": 1}},
    {"prefix": {"a": "claim"}, "field": "file", "map": {"x": "claim"}}, {"prefix": {"a": "claim"}, "other": 1},
])
def test_G15_宣言の攻め_to_by(to_by):
    parse_fails(kind("fix", "f.csv", id={"field": "n"},
                     pointers=[{"field": "id", "meaning": "about", "to_by": to_by}]), "pointers")


@pytest.mark.parametrize("extra", [{"to": "claim"}, {"fields": ["a", "b"]}, {"same_folder": True}])
def test_G15_to_byはtoと組と同じフォルダとは書けない(extra):
    pointer = {"meaning": "about", "to_by": {"prefix": {"c": "claim"}}, **extra}
    if "fields" not in extra:
        pointer["field"] = "id"
    parse_fails(kind("fix", "q/*/f.csv", id={"field": "n", "scope": "folder"}, pointers=[pointer]), "pointers")


@pytest.mark.parametrize("chk", [{"check": "forbid_pair", "id": "x", "via": "id", "target": {"a": {"blank": True}}},
                                 {"check": "count", "id": "x", "from": {"kind": "fix", "via": "id"}, "min": 1}])
def test_G15_forbid_pairとcountのviaには使えない(tmp_path, chk):
    p = {"field": "id", "meaning": "about", "to_by": {"prefix": {"claim-": "claim"}}}
    if chk["check"] == "forbid_pair":
        parse_fails(kind("fix", "f.csv", id={"field": "n"}, pointers=[p], checks=[chk]), "checks")
    else:
        root = _route_ledger(tmp_path, p, FIX_ROWS)
        data = json.loads((root / ".thth/kinds/claim.json").read_text(encoding="utf-8"))
        data["checks"] = [chk]
        (root / ".thth/kinds/claim.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(RegisterRefused) as info:
            V.open_vessel(str(root))
        assert info.value.code == "register_kind_invalid"


def test_G15_にがみの英訳の表の_file_idを_mapで指す(tmp_path):
    texts = kind("text_en", "i18n/en/texts.csv", id={"key": ["file", "id"]},
                 pointers=[{"field": "id", "meaning": "about", "to_by": {"field": "file", "map": {
                     "claims.csv": "claim", "moods.csv": "mood"}}}])
    root = _route_ledger(tmp_path, {"field": "id", "meaning": "about", "to_by": {"prefix": {"claim-": "claim"}}},
                         [], extra_kinds=[texts])
    (root / "i18n/en").mkdir(parents=True)
    (root / "i18n/en/texts.csv").write_text("file,id,text\nclaims.csv,claim-1,A\nclaims.csv,claim-7,B\n"
                                            "moods.csv,mood-1,C\n", encoding="utf-8")
    graph = V.links(V.open_vessel(str(root)))
    assert [e["to"] for e in graph["dangling"]] == ["claim:claim-7"]


# ================================================================== G16 inspects の content_by

def _fix_ledger(tmp, rows, depends=("text",)):
    claim = kind("claim", "claims.csv", id={"field": "id"},
                 questions=[{"name": "reads_right", "depends": list(depends)}])
    fix = kind("fix", "fixes.json", "json", records="rows", rows_at=["fixes"], id={"field": "n"},
               pointers=[{"field": "id", "meaning": "about", "to_by": {"prefix": {"claim-": "claim"}}}],
               inspects={"follow": ["id"], "question": "reads_right", "by": "fixer",
                         "content_by": {"field": "field", "value": "new.text"}})
    return make_ledger(tmp / "l", [claim, fix], {
        "claims.csv": "id,text,scope_ja\nclaim-1,new-a,x\nclaim-2,b,y\n",
        "fixes.json": json.dumps({"fixes": rows}, ensure_ascii=False)})


def test_G16_content_byは直した欄とその中身を検収の記録にする(tmp_path):
    from thth.register import judgment as J, trail as T
    rows = [{"n": "f1", "id": "claim-1", "field": "text", "new": {"text": "new-a"}},
            {"n": "f2", "id": "claim-2", "field": "text", "new": {"text": "old-b"}},
            {"n": "f3", "id": "claim-1", "field": "scope_ja/scope_en", "new": {"text": "x"}},
            {"n": "f4", "id": "claim-1", "field": "nope", "new": {"text": "x"}},
            {"n": "f5", "id": "claim-1", "field": "text"},
            {"n": "f6", "id": "dos-1", "field": "text", "new": {"text": "x"}}]
    v = V.open_vessel(str(_fix_ledger(tmp_path, rows)))
    report = J.inspection_report(v)
    js = {j["source"]: j for j in report["judgments"]}
    assert sorted(js) == ["fix:f1", "fix:f2"]
    assert js["fix:f1"]["content"] == {"text": "new-a"} and js["fix:f1"]["target"] == {"record": "claim:claim-1"}
    assert J.evaluate(v, js["fix:f1"]) == "effective" and J.evaluate(v, js["fix:f2"]) == "stale"
    assert all(T.shows_version(v, j) for j in js.values())
    assert sorted((u["source"], u["reason"]) for u in report["unversioned"]) == [
        ("fix:f3", "field_compound"), ("fix:f4", "field_not_in_target"), ("fix:f5", "value_missing")]
    assert ("fix:f6", "target_unrouted") in {(s["source"], s["reason"]) for s in report["skipped"]}


def test_G16_問いの依る欄が2つ以上なら1欄の中身は版を示していない(tmp_path):
    from thth.register import judgment as J, trail as T
    v = V.open_vessel(str(_fix_ledger(tmp_path, [{"n": "f1", "id": "claim-1", "field": "text",
                                                  "new": {"text": "new-a"}}], depends=("text", "scope_ja"))))
    j = J.inspection_report(v)["judgments"][0]
    assert not T.shows_version(v, j)


def test_G16_今のcontentの読みは変わらない(tmp_path):
    from thth.register import judgment as J
    claim = kind("claim", "claims.csv", id={"field": "id"}, questions=[{"name": "q", "state": "st",
                                                                         "depends": ["text", "st"]}])
    insp = kind("insp", "insp.csv", id={"field": "n"}, pointers=[{"field": "cid", "to": "claim", "meaning": "about"}],
                inspects={"follow": ["cid"], "question": "q", "by": "c", "content": {"text": "text", "st": "st"}})
    root = make_ledger(tmp_path / "l", [claim, insp], {"claims.csv": "id,text,st\nc1,a,ok\n",
                                                       "insp.csv": "n,cid,text,st\ni1,c1,a,ok\n"})
    v = V.open_vessel(str(root))
    report = J.inspection_report(v)
    assert [j["content"] for j in report["judgments"]] == [{"text": "a", "st": "ok"}]
    assert report["unversioned"] == []


@pytest.mark.parametrize("insp", [
    {"follow": ["id"], "question": "q", "by": "c", "content_by": {"field": "f"}},
    {"follow": ["id"], "question": "q", "by": "c", "content_by": {"field": "f", "value": "v", "x": 1}},
    {"follow": ["id"], "question": "q", "by": "c", "content_by": {"field": 1, "value": "v"}},
    {"follow": ["id"], "question": "q", "by": "c", "content_by": {"field": "a.b.c.d.e", "value": "v"}},
    {"follow": ["id"], "question": "q", "by": "c", "content_by": "f"},
    {"follow": ["id"], "question": "q", "by": "c", "content_by": {"field": "f", "value": "v"},
     "content": {"a": "a"}},
    {"follow": ["id"], "question": "q", "by": "c", "content_by": {"field": "f", "value": "v"}, "required": ["f"]},
])
def test_G16_宣言の攻め_content_by(insp):
    parse_fails(kind("fix", "f.csv", id={"field": "n"}, pointers=[{"field": "id", "to": "c", "meaning": "about"}],
                     inspects=insp), "inspects")


# ================================================================== つなぎ: 読者に見える欄の点検を check に

def _reader_ledger(tmp, title):
    page = kind("page", "pages.csv", id={"field": "id"})
    root = make_ledger(tmp / "l", [page], {"pages.csv": f"id,title,memo\npage-1,{title},社内メモ\n"})
    (root / ".thth" / "outlets.json").write_text(json.dumps({
        "schema": "thth.outlets/1", "internal_words": ["社内メモ"],
        "outlets": {"site": {"audience": "reader", "dest": "site", "kinds": {"page": {"reader_fields": ["title"]}}}}},
        ensure_ascii=False), encoding="utf-8")
    return root


def test_つなぎ_読者に見える欄の点検がcheckのchecksに入り数えにも入る(tmp_path):
    from thth.register import vessel_cli
    ok = vessel_cli.check(str(_reader_ledger(tmp_path / "a", "ふつうの題")))
    ids = {c["id"] for c in ok["checks"]["list"]}
    assert {"reader_internal_words", "reader_hidden_ids"} <= ids and ok["checks"]["total"] == 2
    bad = vessel_cli.check(str(_reader_ledger(tmp_path / "b", "社内メモの題")))
    assert bad["checks"]["error"] == 1 and C.red(bad["checks"])


def test_つなぎ_kindsに出し口ごとの読者に見える欄と内部の語の数(tmp_path):
    from thth.register import vessel_cli
    value = vessel_cli.kinds(str(_reader_ledger(tmp_path, "t")))
    assert value["outlets"] == [{"outlet": "site", "audience": "reader",
                                 "reader_fields": {"page": ["title"]}}]
    assert value["internal_words"] == 1


# --- 段 4 §8 断りの語: 借り物の語から専用の語へ ---------------------------------------------------------------

def test_断りの語_出したURLの関門の赤はrc1とverdictのまま専用の符丁と3つの文を載せる(tmp_path, monkeypatch, capsys):
    from tests import test_register_gate as G
    from thth.register import published as P
    from thth import refusal
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    env = tmp_path
    led = G.seeded(env)
    v = P.published(str(led), "site", str(G.lst(env, "/", "/new/", "/日本/")), G.NOW)
    assert not v["ok"] and v["code"] == "register_published_gate"
    assert v["why"] and v["fix"] and v["better"] and "published.redirects" in v["fix"]
    assert refusal.parts("register_published_gate")
    ok = P.published(str(led), "site", str(G.lst(env, "/", "/a/", "/new/", "/日本/")), G.NOW)
    assert ok["ok"] and "code" not in ok


def test_断りの語_3つの語は断りの理由と目録にある():
    from thth import refusal
    from thth.register import errors as E
    for code in ("register_published_gate", "register_judgments_rewritten", "register_version_unavailable"):
        assert code in E.REASONS, code
        found = refusal.parts(code)
        assert found and found["why"] and found["fix"] and found["better"], code
    assert "not_git" in refusal.parts("register_version_unavailable")["why"]
    assert "judgments_rewritten" not in refusal.parts("register_judgment_invalid")["why"]


def test_断りの語_前の版を取り出せないは専用の語_控えの名の無いのは今までどおり(tmp_path):
    from thth.register import trail as T
    led = make_ledger(tmp_path / "led", [{"schema": "thth.kind/1", "kind": "a", "place": "a.csv", "format": "csv",
                                          "id": {"field": "id"}}], {"a.csv": "id\nx\n"})
    with pytest.raises(RegisterRefused) as caught:
        T.against(str(led), "HEAD")
    assert caught.value.code == "register_version_unavailable" and caught.value.detail == "not_git"
    with pytest.raises(RegisterRefused) as caught:
        T.against(str(led), "0123456789abcdef")
    assert caught.value.code == "register_kept_missing" and caught.value.detail == "snapshot"


# --- 段 4 §9 穴 2: 比べを読み替えない（足し 1 の「空の語 "" は空」を置き換える） ---------------------------------

def _m(cond, values):
    return C.match(C.parse_condition(cond), values)


def test_穴2_条件は型も込みで比べる_関東K_A2の数の1と空の字():
    tier = {"source_tier": {"not_in": ["1", "3", None]}}         # 元の台本 `x not in ("1", "3", None)`
    assert _m(tier, {"source_tier": 1})                          # 数の 1 は字の "1" でない → 当たる（赤に数える）
    assert _m(tier, {"source_tier": ""})                         # 長さ 0 の字は null でない → 当たる
    assert _m(tier, {"source_tier": "2"})
    assert not _m(tier, {"source_tier": None}) and not _m(tier, {})
    assert not _m(tier, {"source_tier": "3"})
    assert not _m({"f": {"in": [True]}}, {"f": 1}) and not _m({"f": {"in": [1]}}, {"f": True})
    assert _m({"f": {"in": [1]}}, {"f": 1}) and _m({"f": {"in": [True]}}, {"f": True})
    assert not _m({"f": {"in": ["1"]}}, {"f": 1}) and not _m({"f": {"in": ["true"]}}, {"f": True})


def test_穴2_nullと長さ0の字を分ける_or_blankは空の全部():
    assert _m({"f": {"in": [None]}}, {}) and _m({"f": {"in": [None]}}, {"f": None})
    assert not _m({"f": {"in": [None]}}, {"f": ""}) and not _m({"f": {"in": [""]}}, {"f": None})
    assert _m({"f": {"in": [""]}}, {"f": ""}) and not _m({"f": {"in": [""]}}, {"f": " "})
    for value in (None, "", [], {}):
        assert _m({"f": {"in": ["a"], "or_blank": True}}, {"f": value}), value
    assert _m({"f": {"in": ["a"], "or_blank": True}}, {})
    assert not _m({"f": {"in": ["a"], "or_blank": True}}, {"f": "  "})  # 空白だけの字は空でない
    assert not _m({"f": {"blank": True}}, {"f": "  "}) and _m({"f": {"blank": True}}, {"f": ""})


def test_穴2_空白を落とさない_trimを書いた条件の中だけ落とす_こぴちゃK4_6():
    assert not _m({"s": {"in": ["unverified"]}}, {"s": "unverified "})
    assert _m({"s": {"in": ["unverified"], "trim": True}}, {"s": " unverified "})
    assert _m({"s": {"blank": True, "trim": True}}, {"s": "  "})
    assert _m({"s": {"in": ["a"], "or_blank": True, "trim": True}}, {"s": " \t"})
    assert not _m({"s": {"in": ["1"], "trim": True}}, {"s": 1})          # trim は字だけ。数を字にしない


def _one(tmp, checks, rows, header=None, fmt="json"):
    k = kind("t", "t.json", "json", records="rows", rows_at=["rows"], id={"field": "id"}, checks=checks)
    root = make_ledger(tmp / "l", [k], {"t.json": json.dumps({"rows": rows}, ensure_ascii=False)})
    out = {}
    for e in C.run(V.open_vessel(str(root)))["list"]:
        assert not e.get("failed"), e
        out[e["id"]] = sorted(r["record"].split(":", 1)[1] for r in e["records"])
    return out


def test_穴2_空白だけの字は空でない_MMR_M_C13_trimを書けば空(tmp_path):
    rows = [{"id": "a", "r": " "}, {"id": "b", "r": ""}, {"id": "c", "r": None}, {"id": "d"}, {"id": "e", "r": "x"},
            {"id": "f", "r": []}, {"id": "g", "r": {}}]
    got = _one(tmp_path, [{"check": "required", "id": "req", "fields": ["r"]},
                          {"check": "required", "id": "req_t", "fields": ["r"], "trim": True},
                          {"check": "empty", "id": "emp", "fields": ["r"]},
                          {"check": "one_of", "id": "one", "fields": ["r", "zz"]}], rows)
    assert got["req"] == ["b", "c", "d", "f", "g"]
    assert got["req_t"] == ["a", "b", "c", "d", "f", "g"]
    assert got["emp"] == ["a", "e"]
    assert got["one"] == ["b", "c", "d", "f", "g"]


def test_穴2_wordsとuniqueとdistinctも型と空白を読み替えない(tmp_path):
    rows = [{"id": "a", "s": "ok "}, {"id": "b", "s": "ok"}, {"id": "c", "s": 1}, {"id": "d", "s": True},
            {"id": "e", "s": None}, {"id": "f", "s": "1"}]
    got = _one(tmp_path, [{"check": "words", "id": "w", "field": "s", "in": ["ok", 1], "allow_blank": True},
                          {"check": "words", "id": "w_t", "field": "s", "in": ["ok", 1, True, "1"], "trim": True},
                          {"check": "unique", "id": "u", "fields": ["s"]},
                          {"check": "unique", "id": "u_t", "fields": ["s"], "trim": True}], rows)
    assert got["w"] == ["a", "d", "f"]
    assert got["w_t"] == ["e"]                                     # 空は allow_blank が偽で赤
    assert got["u"] == []                                          # "ok " と "ok"・1 と "1"・1 と True は別
    assert got["u_t"] == ["a", "b"]


def test_穴2_countのdistinctは型も込み_whereは条件の読み(tmp_path):
    card = kind("card", "cards/*.json", "json", records="file", id={"name": True},
                units={"groups": [], "items": ["rows", "*"], "key": ["k"]},
                checks=[{"check": "count", "id": "d", "from": {"units": True}, "distinct": "v", "min": 3},
                        {"check": "count", "id": "d_t", "from": {"units": True}, "distinct": "v", "min": 2,
                         "trim": True},
                        {"check": "count", "id": "w", "from": {"units": True},
                         "where": {"p": {"in": [True]}}, "max": 0}])
    root = make_ledger(tmp_path / "l", [card], {"cards/x.json": json.dumps({"rows": [
        {"k": "1", "v": "a", "p": 1}, {"k": "2", "v": "a ", "p": "true"}, {"k": "3", "v": 1, "p": True},
        {"k": "4", "v": "1", "p": None}]})})
    got = {e["id"]: e["violations"] for e in C.run(V.open_vessel(str(root)))["list"]}
    assert got == {"d": 0, "d_t": 0, "w": 1}                       # a・a ・1・"1" は 4 つ。trim で 3 つ。真は 1 行だけ


@pytest.mark.parametrize("value,ok", [("12", True), (12, True), (" 12", False), (True, False), (12.0, False),
                                      ("0", False), (0, False), ("012", False)])
def test_穴2_positive_intは字と整数の両方を受ける_ほかの形は字だけ(value, ok):
    c = {"shape": "positive_int", "prefix": None}
    assert C.shape_ok(c, value) is ok
    assert C.shape_ok({"shape": "slug", "prefix": None}, 12) is False
    assert C.shape_ok({"shape": "slug", "prefix": None}, True) is False


def test_穴2_語の並びはJSONのスカラーを受ける():
    cond = C.parse_condition({"f": {"in": ["a", 1, True, None, ""]}})
    assert cond["f"][0] == "in"
    for words in ([1.5], [[]], [{}], ["a\n"], [float("nan")], ["x" * 300], [], [2 ** 80]):
        with pytest.raises(V.DeclarationError):
            C.parse_condition({"f": {"in": words}})
    for trim in (1, "yes", None, [True]):
        with pytest.raises(V.DeclarationError):
            C.parse_condition({"f": {"in": ["a"], "trim": trim}})
    parse_fails(kind("t", "t.csv", id={"field": "id"}, checks=[{"check": "required", "id": "x", "fields": ["a"],
                                                                "trim": 1}]), "checks")
    parse_fails(kind("t", "t.csv", id={"field": "id"}, checks=[{"check": "words", "id": "x", "field": "a",
                                                                "in": [[1]]}]), "checks")


# --- 段 4 の足し 2: 同じ欄に指す欄が 2 本あるとき、forbid_pair と count の via_to で先の種類を選ぶ ---------------

def _two_ways(tmp, song_checks=(), shelf_checks=(), credit_checks=()):
    """MMR の credit_version.seed → song と shelf_song の形（同じ欄から 2 本）。"""
    song = kind("song", "songs.csv", id={"field": "id"}, checks=list(song_checks))
    shelf = kind("shelf_song", "shelf.csv", id={"field": "id"}, checks=list(shelf_checks))
    credit = kind("credit", "credits.csv", id={"field": "id"}, checks=list(credit_checks), pointers=[
        {"field": "seed", "to": "song", "meaning": "about"},
        {"field": "seed", "to": "shelf_song", "meaning": "about"}])
    for k in (song, shelf, credit):
        if not k["checks"]:
            del k["checks"]
    return make_ledger(tmp / "l", [song, shelf, credit], {
        "songs.csv": "id,status\ns1,draft\ns2,ok\ns3,ok\n",
        "shelf.csv": "id,status\ns1,ok\n",
        "credits.csv": "id,seed\nc1,s1\nc2,s2\n"})


def _bad(vessel, check_id):
    return sorted(r["record"] for e in C.run(vessel)["list"] if e["id"] == check_id for r in e["records"])


def test_足し2_forbid_pairのvia_toで2本のうちの先を選ぶ(tmp_path):
    fp = {"check": "forbid_pair", "id": "no_draft", "via": "seed", "via_to": "song",
          "target": {"status": {"in": ["draft"]}}}
    fp2 = {**fp, "id": "no_draft_shelf", "via_to": "shelf_song"}
    vessel = V.open_vessel(str(_two_ways(tmp_path, credit_checks=[fp, fp2])))
    assert _bad(vessel, "no_draft") == ["credit:c1"]
    assert _bad(vessel, "no_draft_shelf") == []


def test_足し2_countのvia_toで2本のうちの先を選ぶ(tmp_path):
    cs = {"check": "count", "id": "credited", "from": {"kind": "credit", "via": "seed", "via_to": "song"}, "min": 1}
    cf = {"check": "count", "id": "credited2", "from": {"kind": "credit", "via": "seed", "via_to": "shelf_song"},
          "min": 2}
    vessel = V.open_vessel(str(_two_ways(tmp_path, song_checks=[cs], shelf_checks=[cf])))
    assert _bad(vessel, "credited") == ["song:s3"]
    assert _bad(vessel, "credited2") == ["shelf_song:s1"]


def test_足し2_via_toが無くて2本ならforbid_pairは断り_断りの文がvia_toを言う():
    from thth import refusal
    credit = kind("credit", "credits.csv", id={"field": "id"}, pointers=[
        {"field": "seed", "to": "song", "meaning": "about"}, {"field": "seed", "to": "shelf_song", "meaning": "about"}],
        checks=[{"check": "forbid_pair", "id": "x", "via": "seed", "target": {"status": {"in": ["draft"]}}}])
    parse_fails(credit, "checks")
    assert "via_to" in refusal.parts("register_kind_invalid")["fix"]


@pytest.mark.parametrize("via_to", ["shelf", "", None, 1, ["song"], {"a": 1}, "Song", "../song", "s" * 300])
def test_足し2_宣言の攻め_forbid_pairのvia_to(via_to):
    credit = kind("credit", "credits.csv", id={"field": "id"}, pointers=[
        {"field": "seed", "to": "song", "meaning": "about"}, {"field": "seed", "to": "shelf_song", "meaning": "about"}],
        checks=[{"check": "forbid_pair", "id": "x", "via": "seed", "via_to": via_to,
                 "target": {"status": {"in": ["draft"]}}}])
    parse_fails(credit, "checks")


@pytest.mark.parametrize("src", [
    {"kind": "credit", "via": "seed"},                              # 2 本で via_to が無い
    {"kind": "credit", "via": "seed", "via_to": "shelf_song"},      # この種類（song）でない先
    {"kind": "credit", "via": "seed", "via_to": "nosuch"},
    {"kind": "credit", "via": "seed", "via_to": ""},
    {"kind": "credit", "via": "seed", "via_to": 3},
    {"kind": "credit", "via": "seed", "via_to": None},
    {"kind": "credit", "via": "seed", "via_to": "song", "x": 1},
])
def test_足し2_宣言の攻め_countのvia_to(tmp_path, src):
    root = _two_ways(tmp_path, song_checks=[{"check": "count", "id": "c", "from": src, "min": 1}])
    with pytest.raises(RegisterRefused) as info:
        V.open_vessel(str(root))
    assert info.value.code == "register_kind_invalid"


def test_足し2_to_byの指す欄にはvia_toでも使えない():
    credit = kind("credit", "credits.csv", id={"field": "id"}, pointers=[
        {"field": "seed", "meaning": "about", "to_by": {"prefix": {"s": "song"}}}],
        checks=[{"check": "forbid_pair", "id": "x", "via": "seed", "via_to": "song",
                 "target": {"status": {"in": ["draft"]}}}])
    parse_fails(credit, "checks")


# --- 段 4 §9 穴 3: YAML の引用していない null と真偽だけを型にする ----------------------------------------------

YAML_TYPES = ("a: null\nb: ~\nc: Null\nd: NULL\ne:\nf: true\ng: True\nh: TRUE\ni: false\nj: False\nk: FALSE\n"
              "l: 'null'\nm: \"true\"\nn: yes\no: no\np: on\nq: off\nr: 12\ns: 2026-08-27\nt: 1.5\nu: tRue\nv: nuLL\n"
              "w: ''\nx: [null, true, 'false', '~', 3]\ny: {z: ~, zz: False}\nnull: 1\ntrue: 2\n")


@yaml_needed
def test_穴3_YAMLの引用していないnullと真偽だけを型にする_ほかは字():
    value, problem, seen = V.yamlread.load(YAML_TYPES)
    assert problem is None and seen
    assert value == {"a": None, "b": None, "c": None, "d": None, "e": None,
                     "f": True, "g": True, "h": True, "i": False, "j": False, "k": False,
                     "l": "null", "m": "true", "n": "yes", "o": "no", "p": "on", "q": "off", "r": "12",
                     "s": "2026-08-27", "t": "1.5", "u": "tRue", "v": "nuLL", "w": "",
                     "x": [None, True, "false", "~", "3"], "y": {"z": None, "zz": False},
                     "null": "1", "true": "2"}                     # 鍵は字のまま


@yaml_needed
def test_穴3_YAMLの断りは今までどおり():
    for text, word in (("a: 1\na: 2\n", "key_duplicate:2"), ("a: &x 1\nb: *x\n", "anchor:1"),
                       ("a: !!str 1\n", "tag:1"), ("<<: {a: 1}\n", "merge:1"), ("a: 1\n---\nb: 2\n", "multi_document:2")):
        assert V.yamlread.load(text) == (None, word, True), text


CHUJU = ("---\ntitle: 倍率\ndraft: {draft}\ndekiru: {dekiru}\nbranches:\n  - konkyo: {konkyo}\n    {extra}\n---\n本文\n")


@yaml_needed
@pytest.mark.parametrize("dekiru,konkyo,extra,draft,bad", [
    ("できる", "あり", "next: x", "false", set()),
    ("", "あり", "next: x", "false", {"pub"}),
    ("null", "あり", "next: x", "false", {"pub"}),
    ("~", "あり", "next: x", "false", {"pub"}),
    ("できる", "null", "next: x", "false", {"konkyo"}),
    ("できる", "あり", "terashi: true", "false", {"terashi"}),
    ("できる", "あり", "terashi: True", "false", {"terashi"}),
    ("できる", "あり", "terashi: 'true'", "false", set()),
    ("できる", "あり", "next: x", "False", set()),
    ("'null'", "あり", "next: x", "false", set()),
])
def test_穴3_中受の問の写しで元の型と同じ答え(tmp_path, dekiru, konkyo, extra, draft, bad):
    toi = kind("toi", "toi/*.md", "md_yaml", units={"groups": [], "items": ["branches", "*"], "key": ["konkyo"]},
               checks=[{"check": "required", "id": "pub", "fields": ["dekiru"],
                        "when": {"draft": {"in": [False]}}},
                       {"check": "words", "id": "draft_w", "field": "draft", "in": [True, False]},
                       {"check": "count", "id": "konkyo", "from": {"units": True},
                        "where": {"konkyo": {"blank": True}}, "max": 0},
                       {"check": "count", "id": "terashi", "from": {"units": True},
                        "where": {"terashi": {"in": [True]}}, "max": 0}])
    root = make_ledger(tmp_path / "l", [toi], {"toi/a.md": CHUJU.format(dekiru=dekiru, konkyo=konkyo, extra=extra,
                                                                         draft=draft)})
    v = V.open_vessel(str(root))
    assert V.problems(v) == []
    got = {e["id"] for e in C.run(v, [])["list"] if e["violations"]}
    assert got == bad


@yaml_needed
def test_穴3_鍵の辞書とYAMLの行にも効く(tmp_path):
    rows = kind("r", "r.yaml", "yaml", records="rows", rows_at=["rows"], id={"field": "id"})
    keyed = kind("kd", "k.yaml", "yaml", records="rows", rows_at=[], id={"from": "key"})
    root = make_ledger(tmp_path / "l", [rows, keyed], {
        "r.yaml": "rows:\n  - id: a\n    p: ~\n    q: TRUE\n  - id: b\n    p: 'null'\n",
        "k.yaml": "x:\n  on: True\n  off: ~\n"})
    v = V.open_vessel(str(root))
    assert v.get("r:a").values == {"id": "a", "p": None, "q": True}
    assert v.get("r:b").values == {"id": "b", "p": "null"}
    assert v.get("kd:x").values["on"] is True and v.get("kd:x").values["off"] is None


# --- 段 4 §9 穴 1: 1 行が指す先の数の手間・上限。ついで: 指す欄の値の NFC・置き場の型の re.error -----------------

def _many(tmp, n, pointer, checks=(), dup=1):
    ids = [f"c{i}" for i in range(n)]
    claim = kind("claim", "claims.json", "json", records="rows", rows_at=["rows"], id={"field": "id"})
    ev = kind("ev", "ev.json", "json", records="rows", rows_at=["rows"], id={"field": "id"},
              pointers=[pointer], **({"checks": list(checks)} if checks else {}))
    return make_ledger(tmp / "l", [claim, ev], {
        "claims.json": json.dumps({"rows": [{"id": i, "status": "ok"} for i in ids]}),
        "ev.json": json.dumps({"rows": [{"id": "e1", "claims": ids * dup}, {"id": "e2", "claims": ["c1"]}]})})


FP = [{"check": "forbid_pair", "id": "fp", "via": "claims", "target": {"status": {"in": ["x"]}}}]
TO = {"field": "claims", "to": "claim", "meaning": "supports"}
TO_BY = {"field": "claims", "to_by": {"prefix": {"c": "claim"}}, "meaning": "supports"}


@pytest.mark.parametrize("pointer,checks", [(TO, FP), (TO_BY, ())])
def test_穴1_1つの欄が指す先は10000個まで_超えたら読みの問題too_many_targets(tmp_path, pointer, checks):
    import time
    root = _many(tmp_path, 80_000, pointer, checks)
    start = time.monotonic()
    v = V.open_vessel(str(root))
    graph = V.links(v)
    C.run(v, [])
    assert time.monotonic() - start < 5
    assert v.get("ev:e1").problems == ["too_many_targets"]
    assert [e["from"] for e in graph["edges"]] == ["ev:e2"]        # 黙って切らない（その行の指しは数えない）
    assert graph["dangling"] == [] and graph["unrouted"] == []


@pytest.mark.parametrize("pointer,checks", [(TO, FP), (TO_BY, ())])
def test_穴1_10000個の先は重なりを集合で除いて1秒ほど(tmp_path, pointer, checks):
    import time
    root = _many(tmp_path, 5_000, pointer, checks, dup=2)          # 10,000 個（重なり 5,000）
    v = V.open_vessel(str(root))
    start = time.monotonic()
    graph = V.links(v)
    C.run(v, [])
    assert time.monotonic() - start < 2
    assert v.get("ev:e1").problems == []
    assert len({e["to"] for e in graph["edges"] if e["from"] == "ev:e1"}) == 5_000


def test_穴1_内側の単位の指す欄も重なりを除いて上限を見る(tmp_path):
    claim = kind("claim", "claims.csv", id={"field": "id"})
    card = kind("card", "cards/*.json", "json", records="file", id={"name": True}, max_bytes=1_000_000,
                units={"groups": [], "items": ["rows", "*"], "key": ["k"]},
                pointers=[{"field": "c", "to": "claim", "meaning": "about", "in_units": True}])
    many = [{"k": str(i), "c": [f"c{j}" for j in range(i * 3000, i * 3000 + 3000)]} for i in range(4)]
    root = make_ledger(tmp_path / "l", [claim, card], {
        "claims.csv": "id\nc1\n", "cards/a.json": json.dumps({"rows": [{"k": "1", "c": "c1"}, {"k": "2", "c": "c1"}]}),
        "cards/big.json": json.dumps({"rows": many})})
    v = V.open_vessel(str(root))
    assert V.pointer_pieces(v.get("card:a"), v.kinds["card"].pointers[0]) == (["c1"], 0)
    assert v.get("card:big").problems == ["too_many_targets"]       # 単位を合わせて 12,000 個
    assert V.pointer_pieces(v.get("card:big"), v.kinds["card"].pointers[0]) == ([], 0)


def test_ついで_指す欄の値もNFCにそろえて引く(tmp_path):
    claim = kind("claim", "claims.csv", id={"field": "id"})
    ev = kind("ev", "ev.csv", id={"field": "id"}, pointers=[{"field": "c", "to": "claim", "meaning": "about"}])
    nfd = unicodedata.normalize("NFD", "が")
    root = make_ledger(tmp_path / "l", [claim, ev], {"claims.csv": "id\nが\n", "ev.csv": f"id,c\ne1,{nfd}\n"})
    graph = V.links(V.open_vessel(str(root)))
    assert [e["to"] for e in graph["edges"]] == ["claim:が"] and graph["dangling"] == []


def test_ついで_置き場の型の正規表現のre_errorは宣言の断り(tmp_path, monkeypatch):
    import fnmatch
    import re as _re
    root = make_ledger(tmp_path / "l", [kind("a", "a/*/*.csv", id={"field": "id"}),
                                        kind("b", "b/*/*.csv", id={"field": "id"})], {"a/x/t.csv": "id\n1\n"})
    real = fnmatch.translate
    monkeypatch.setattr(fnmatch, "translate", lambda p: "(?=(?P<g1>))" + real(p))   # 3.10 の同じ名前の組
    assert sorted(refs(V.open_vessel(str(root)))) == ["a:1"]
    monkeypatch.setattr(fnmatch, "translate", lambda p: "(")
    with pytest.raises(RegisterRefused) as info:
        V.open_vessel(str(root))
    assert info.value.code == "register_kind_invalid"
    del _re


# --- 段 6・7 の検収 6-1: 表の行の分け方を csv の部品の読みと同じに（引用符が欄を開くのは欄の頭だけ） -----------------

@pytest.mark.parametrize("delim", [",", "\t"])
def test_6_1_途中の引用符は欄を開かない_後ろのレコードを飲み込まない(tmp_path, delim):
    import csv as _csv
    import io as _io
    nl = "\r\n" if delim == "\t" else "\n"
    text = nl.join(delim.join(r) for r in (["id", "title", "note"], ["t-1", "A", '5" pipe'], ["t-2", "B", "y"],
                                            ["t-3", "C", "z"])) + nl
    assert len(V.split_csv_rows(text, delim)) == 4
    assert len(list(_csv.reader(_io.StringIO(text, newline=""), delimiter=delim))) == 4
    extra = {"delimiter": delim} if delim != "," else {}
    root = make_ledger(tmp_path / "l", [kind("t", "t.csv", id={"field": "id"}, **extra)], {"t.csv": text})
    v = V.open_vessel(str(root))
    assert refs(v) == ["t:t-1", "t:t-2", "t:t-3"] and V.problems(v) == []
    assert v.get("t:t-1").values["note"] == '5" pipe'
    assert v.get("t:t-2").version == V.bytes_version(f"t-2{delim}B{delim}y{nl}".encode("utf-8"))


def test_6_1_欄の頭の引用符は今までどおり改行と区切りを含む():
    text = 'id,note\na,"x\ny, ""q"""\nb,"z"w\nc,p\n'
    assert V.split_csv_rows(text, ",") == ["id,note\n", 'a,"x\ny, ""q"""\n', 'b,"z"w\n', "c,p\n"]
    assert V.split_csv_rows("a\tb\n\"x\ty\"\tz\n", "\t") == ["a\tb\n", "\"x\ty\"\tz\n"]


def test_6_1_器の分け方とcsvの部品の数が食い違う表は読みの問題row_split_mismatch(tmp_path, monkeypatch):
    root = make_ledger(tmp_path / "l", [kind("t", "t.csv", id={"field": "id"})], {"t.csv": "id,n\na,1\nb,2\n"})
    real = V.split_csv_rows
    monkeypatch.setattr(V, "split_csv_rows", lambda text, *a: real(text, *a)[:-1])   # 分け方が食い違った体
    v = V.open_vessel(str(root))
    assert all("row_split_mismatch" in r.problems for r in v.all_records) and v.all_records


# --- 段 6・7 の検収 6-5: 途中で止まった入れ替えを、器の読み手が半分の姿で読まない ------------------------------

def _halted(tmp_path, monkeypatch):
    import subprocess
    from tests import test_register_write as W
    from thth.register import keep as K
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    led = W.claim_ledger(tmp_path)
    b = W.bundle_of(tmp_path, dict(W.claims_fix(), **{W.VERIFY: "id,claim_id,text,verdict\nv-1,c-1,茶はとても苦い,ok\n"}))
    from thth import cli
    assert cli.main(["register", "keep", str(led), "--by", "m"]) == 0
    code = ("import os, sys\n" f"sys.path.insert(0, {str(W.ROOT)!r})\n" "from thth import cli\n"
            "from thth.register import keep\nreal = keep._replace\ncount = [0]\n"
            "def stop(a, b):\n    if count[0] == 1:\n        os._exit(9)\n    count[0] += 1\n    real(a, b)\n"
            "keep._replace = stop\n"
            f"cli.main(['register', 'merge', {str(led)!r}, {str(b)!r}, '--by', 'a', '--checked-by', 'b'])\n")
    assert subprocess.run([sys.executable, "-c", code], env=dict(os.environ), capture_output=True).returncode == 9
    assert K.pending(str(led)) is not None
    return led


def _cli(capsys, *argv):
    from thth import cli
    rc = cli.main([str(a) for a in argv] + ["--json"])
    out, err = capsys.readouterr()
    return rc, (json.loads(out) if out.strip() else None)


def test_6_5_入れ替えの段取りが残る台帳は器の読み手が断る_resumeのあとは通る(tmp_path, monkeypatch, capsys):
    led = _halted(tmp_path, monkeypatch)
    from thth import cli
    assert cli.main(["register", "add", "halted", "--path", str(led), "--project", "sample", "--kind", "media",
                     "--by", "m"]) == 0
    capsys.readouterr()
    with pytest.raises(RegisterRefused) as info:
        V.open_vessel(str(led))
    assert info.value.code == "register_batch_pending"
    assert V.open_vessel(str(led), allow_pending=True).kinds                    # 前の姿を自分で扱う口
    from thth import refusal
    fix = refusal.parts("register_batch_pending")["fix"]
    assert "resume" in fix and "--continue" in fix and "--rollback" in fix
    for argv in (("check", "halted"), ("health", led), ("links", "halted", "claim:c-1"), ("used", led, "claim:c-1")):
        rc, value = _cli(capsys, "register", *argv)
        assert rc != 0 and value and value["code"] == "register_batch_pending", argv
    rc, value = _cli(capsys, "register", "resume", led, "--continue", "--by", "a")
    assert rc == 0, value
    assert V.open_vessel(str(led)).get("claim:c-1").values["text"] == "茶はとても苦い"
    for argv in (("check", "halted"), ("health", led), ("links", "halted", "claim:c-1")):
        rc, value = _cli(capsys, "register", *argv)
        assert not (value and value.get("code") == "register_batch_pending"), argv
