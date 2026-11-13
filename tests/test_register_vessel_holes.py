"""台帳の器 段 1 — Codex の検収（`~/Documents/THTH/codex/答え_段1の検収_2026-11-14.md`）で見つかった穴を突く試験。

穴ごとに、直す前に赤を見てから直した（報告に試験の名前を挙げる）。
1. 範囲と結論を別々の判断から取る（並べ方で答えが変わる）
2. 内側の単位への判断が宣言した依る欄を使わず、状態と理由の種類を外せる・依らない欄で古くなる。指紋の段落と実装の不一致
3. 同じ指し先になった別々の単位を先頭に畳む・時点の違う同じ中身の行が 1 つの指し先になる
4. 変更者が分からないときに結び直しを通す
5. 欠けた値に「使える」を返す
6. YAML の字の逃がし・宣言の型の違いで例外が漏れる
7. 別の置き場の同じ名前（こぴちゃの kyoto）
8. 弱い試験: 中央の行から主張へたどる部品・(c) の「割り当てられないとき出典の全部」・(g) の旧新 4 組の集合
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import pathlib
import shutil

import pytest

from thth.register import judgment as J, vessel as V
from thth.register.errors import RegisterRefused
from tests.test_register_vessel import (A1, A312, AT, CLAIM, FIX, MALE_2026, NEW, ROOT, add, copy, edit_json, env,  # noqa: F401
                                        jrun, judge_values, rewrite_csv, run, states, unit_of, yaml_needed)

FEMALE_2026 = {"group.year": 2026, "gender": "女子"}


def _value(v, ref, unit, name):
    return J.make(v, {"record": ref, "unit": unit.pointer, "value": name}, "reads_right", by="audit", at=AT,
                  conclusion="good")


# ================================================================== 穴 1

def test_穴1_範囲と結論は1つの判断から取り並べ方で答えが変わらない(env):
    v = V.open_vessel(str(copy(env, "kanto")))
    male = unit_of(v, A1, **MALE_2026)
    target = {"record": A1, "unit": male.pointer, "value": "capacity"}
    g = J.make(v, target, "reads_right", by="auditor", at="2026-11-14", conclusion="good", scope="男女の合計")
    b = J.make(v, target, "reads_right", by="auditor", at="2026-11-13", conclusion="bad", scope="男子")
    for order in ([g, b], [b, g]):
        as_male = J.answer_value(v, order, A1, male.pointer, "capacity", as_scope="男子")
        assert as_male["usable"] is False and as_male["conclusion"] == "bad" and as_male["scope"] == "男子"
        total = J.answer_value(v, order, A1, male.pointer, "capacity", as_scope="男女の合計")
        assert total["usable"] is True and total["scope"] == "男女の合計"
    assert J.answer_value(v, [g, b], A1, male.pointer, "capacity", as_scope="男子") == \
        J.answer_value(v, [b, g], A1, male.pointer, "capacity", as_scope="男子")


# ================================================================== 穴 2

def _mmr_with(env, **units_extra):
    root = copy(env, "mmr")
    decl = root / ".thth" / "kinds" / "credit.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    data["questions"][0]["depends"] = ["title"]
    data["units"].update(units_extra)
    decl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


CREDIT = "credit:ap-sample.mastered_by"


def _unit_judgment(v, seed):
    unit = V.find_units(v.get(CREDIT), {"seed": seed})
    return J.make(v, {"record": CREDIT, "unit": unit.pointer}, "credit_holds", by="checker", at=AT,
                  word=unit.item.get("status"))


def test_穴2_単位への判断は状態と理由の種類を外せない(env):
    root = _mmr_with(env, not_values=["status", "held_kind"])
    v0 = V.open_vessel(str(root))
    j = _unit_judgment(v0, "seed-liked-0161")

    def flip(d):
        d["rows"][0]["status"] = "held"
        d["rows"][0]["held_kind"] = "unresolved"
    edit_json(root / "site/src/credits/ap-sample.mastered_by.json", flip)
    assert J.evaluate(V.open_vessel(str(root)), j) == "stale"


def test_穴2_単位への判断は依らない欄だけの書き換えでは古くならない(env):
    root = _mmr_with(env)
    v0 = V.open_vessel(str(root))
    j = _unit_judgment(v0, "seed-liked-0443")
    edit_json(root / "site/src/credits/ap-sample.mastered_by.json",
              lambda d: d["rows"][1].__setitem__("held_reason_ja", "説明だけを書き換えた"))
    assert J.evaluate(V.open_vessel(str(root)), j) == "effective"
    edit_json(root / "site/src/credits/ap-sample.mastered_by.json",
              lambda d: d["rows"][1].__setitem__("held_kind", "conflict"))
    assert J.evaluate(V.open_vessel(str(root)), j) == "stale"


def _paragraph_fp(obj) -> str:
    """設計 §5.1 の段落の書き方そのまま（実装の canonical を使わずに組む）。"""
    def lf(x):
        if isinstance(x, str):
            return x.replace("\r\n", "\n").replace("\r", "\n")
        if isinstance(x, list):
            return [lf(i) for i in x]
        if isinstance(x, dict):
            return {k: lf(i) for k, i in x.items()}
        return x
    text = json.dumps(lf(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_穴2_指紋の段落どおりの計算とcurrent_dependが記録と単位と値で一致する(env):
    # 記録: 依る欄 ＋ 状態の欄 ＋ 理由の種類の欄 の読んだ値
    v = V.open_vessel(str(copy(env, "kopicha")))
    row = v.get(CLAIM).values
    want = _paragraph_fp({k: row[k] for k in ("statement_ja", "object_text_ja", "claim_status")})
    assert J.current_depend(v, {"record": CLAIM}, "says_so") == want
    # 単位: 同じ欄の選び方で、値を {"value", "time", "sources"} で包む（時点・出典が無ければ null と []）
    m = V.open_vessel(str(_mmr_with(env)))
    unit = V.find_units(m.get(CREDIT), {"seed": "seed-liked-0443"})
    want = _paragraph_fp({k: {"value": unit.item.get(k), "time": None, "sources": []}
                          for k in ("title", "status", "held_kind")})
    assert J.current_depend(m, {"record": CREDIT, "unit": unit.pointer}, "credit_holds") == want
    # 値: {"value", "scope", "time", "sources"}。出典は、出典の欄の全部の読んだ値の指紋を並べた物
    k = V.open_vessel(str(copy(env, "kanto")))
    male = unit_of(k, A1, **MALE_2026)
    sources = json.loads((env["tmp"] / "kanto" / "admission_results" / "1.json").read_text(encoding="utf-8"))
    src2 = sources["years"][0]["sources"][1]
    want = _paragraph_fp({"value": 140, "scope": None, "time": 2026, "sources": [_paragraph_fp(src2)]})
    assert J.current_depend(k, {"record": A1, "unit": male.pointer, "value": "capacity"}, "reads_right") == want
    design = (ROOT / "docs" / "設計_台帳の器_2026-11-13.md").read_text(encoding="utf-8")
    assert '{"value", "time", "sources"}' in design and '{"value", "scope", "time", "sources"}' in design


# ================================================================== 穴 3

def _tiny(env, rows_by_year: dict, *, uid=False, key=("key",)) -> pathlib.Path:
    root = env["tmp"] / "tiny"
    (root / ".thth" / "kinds").mkdir(parents=True)
    units = {"groups": ["years", "*"], "items": ["rows", "*"], "key": list(key), "time": {"group": "year"}}
    if uid:
        units["id"] = "uid"
    (root / ".thth" / "kinds" / "t.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "t", "place": "t/*.json", "format": "json", "id": {"name": True},
        "units": units}), encoding="utf-8")
    (root / "t").mkdir()
    (root / "t" / "a.json").write_text(json.dumps({"years": [{"year": y, "rows": rows} for y, rows in
                                                              rows_by_year.items()]}), encoding="utf-8")
    return root


def test_穴3_同じ番号の別々の単位には判断を作らせず答えも出さない(capsys, env):
    root = _tiny(env, {2026: [{"uid": "x", "key": "a", "value": 10}, {"uid": "x", "key": "b", "value": 20}]},
                 uid=True)
    v = V.open_vessel(str(root))
    rec = v.get("t:a")
    assert "unit_id_duplicate" in rec.problems
    pointer = rec.units[0].pointer
    with pytest.raises(RegisterRefused) as caught:
        J.make(v, {"record": "t:a", "unit": pointer, "value": "value"}, "reads_right", by="x", at=AT,
               conclusion="good")
    assert caught.value.code == "register_unit_ambiguous"
    with pytest.raises(RegisterRefused) as caught:
        J.answer_value(v, [], "t:a", pointer, "value")
    assert caught.value.code == "register_unit_ambiguous"
    add(capsys, root, "tiny")
    rc_, value, _ = jrun(capsys, "register", "links", "tiny", "t:a", "--unit", pointer)
    assert rc_ == 2 and value["code"] == "register_unit_ambiguous"


def test_穴3_時点の違う同じ中身の行は別の指し先(env):
    root = _tiny(env, {2026: [{"key": "a", "value": 10}], 2027: [{"key": "a", "value": 10}]})
    rec = V.open_vessel(str(root)).get("t:a")
    assert len({u.pointer for u in rec.units}) == 2 and rec.problems == []


# ================================================================== 穴 4

def test_穴4_変更者が分からないときは結び直しを断る(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    four = next(u for u in v0.get(A312).units if u.item["applicants"] == 4)
    [old] = judge_values(v0, A312, four, ["admitted"])
    edit_json(root / "admission_results" / "312.json", lambda d: d["years"][0]["rounds"][1].__setitem__("admitted", 4))
    v1 = V.open_vessel(str(root))
    [cand] = J.candidates(v1, [old])
    for unknown in (None, ""):
        with pytest.raises(RegisterRefused) as caught:
            J.carry(v1, old, cand["appeared"][0], by="editor", at=AT, changed_by=unknown)
        assert caught.value.code == "register_carry_unnamed"


# ================================================================== 穴 5

def test_穴5_欠けた値は欠けを読んだ判断があっても値としては使えない(env):
    v = V.open_vessel(str(copy(env, "kanto")))
    female = unit_of(v, A1, **FEMALE_2026)
    j = J.make(v, {"record": A1, "unit": female.pointer, "value": "capacity"}, "reads_right", by="audit", at=AT,
               conclusion="good", scope="男女の合計")
    ans = J.answer_value(v, [j], A1, female.pointer, "capacity", as_scope="男女の合計")
    assert ans["usable"] is False and ans["reason_kind"] == "value_missing" and ans["conclusion"] == "good"
    assert ans["missing"] is True


# ================================================================== 穴 6

@yaml_needed
@pytest.mark.parametrize("text, word", [('x: "\\U00110000"\n', "syntax:1"), ('x: "\\UFFFFFFFF"\n', "syntax:1"),
                                        ('a: 1\nx: "\\U00110000"\n', "syntax:2")])
def test_穴6_YAMLの範囲外の字の逃がしも理由と行で断る(capsys, env, text, word):
    assert V.yamlread.load(text) == (None, word, True)
    root = copy(env, "kopicha")
    (root / "src" / "content" / "origins" / "esc.yaml").write_text(text, encoding="utf-8")
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_yaml_refused"


def _valid_kind():
    return json.loads((FIX / "kopicha" / ".thth" / "kinds" / "article_claim.json").read_text(encoding="utf-8"))


def test_穴6_宣言のhashの型が違っても断りになる():
    data = _valid_kind()
    data["pointers"][0]["recorded_version"]["hash"] = True
    with pytest.raises(V.DeclarationError):
        V.parse_kind(data, "x")


def _paths(obj, trail=()):
    yield trail
    if isinstance(obj, dict):
        for k, val in obj.items():
            yield from _paths(val, trail + (k,))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            yield from _paths(val, trail + (i,))


@pytest.mark.parametrize("name", ["article_claim.json", "page.json", "claim.json"])
def test_穴6_宣言のどの欄にどの型を入れても断りか受け入れで例外は漏れない(name):
    base = json.loads((FIX / "kopicha" / ".thth" / "kinds" / name).read_text(encoding="utf-8"))
    kanto = json.loads((FIX / "kanto" / ".thth" / "kinds" / "admission_result.json").read_text(encoding="utf-8"))
    for source in (base, kanto):
        for trail in list(_paths(source))[1:]:
            for bad in (True, 1, 1.5, None, [], {}, "x", [True], {"a": True}):
                data = json.loads(json.dumps(source))
                holder = data
                for step in trail[:-1]:
                    holder = holder[step]
                holder[trail[-1]] = bad
                try:
                    V.parse_kind(data, "x")
                except V.DeclarationError:
                    pass


# ================================================================== 穴 7

def test_穴7_別の置き場の同じ名前は種類を分ければ2つの記録で1つの種類なら断る(capsys, env):
    root = copy(env, "kopicha")
    v = V.open_vessel(str(root))
    assert v.get("page:kyoto").path == "src/content/origins/kyoto.yaml"
    assert v.get("producer_page:kyoto").path == "src/content/producers/kyoto.yaml"
    decl = root / ".thth" / "kinds" / "page.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    data["place"] = "src/content/*/*.yaml"
    data.pop("pointers")
    decl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (root / ".thth" / "kinds" / "producer_page.json").rename(root / ".thth" / "producer_page.json.off")
    (root / ".thth" / "kinds" / "tea_page.json").rename(root / ".thth" / "tea_page.json.off")
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_record_invalid"
    assert sorted(p["path"] for p in value["problems"] if "id_duplicate" in p["words"]) == [
        "src/content/origins/kyoto.yaml", "src/content/producers/kyoto.yaml"]


def test_穴7_実物の台本は置き場ごとの種類で読み同じ名前を2つの記録にする(env):
    spec = importlib.util.spec_from_file_location("realcheck", ROOT / "tools" / "register_vessel_realcheck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = module.kopicha(str(FIX / "kopicha"), env["tmp"] / "decl")
    assert out["problems"] == 0 and out["yaml_refused"] == 0
    assert out["records"]["page_origins"] >= 2 and out["records"]["page_producers"] == 1


# ================================================================== 穴 8

def test_穴8_中央の行から根拠をたどって主張への検収の記録にする(env):
    root = copy(env, "kopicha")
    rewrite_csv(root / "data" / "knowledge" / "claims.csv", CLAIM.split(":")[1], statement_ja=NEW,
                object_text_ja="水浸出物 17.1%")
    v = V.open_vessel(str(root))
    found = J.inspections(v)
    by_target = {j["target"]["record"]: j for j in found}
    assert set(by_target) == {CLAIM, "claim:c-origin-brazil-024"}  # 中央の 2 行がそれぞれの根拠の主張へ
    milk = by_target[CLAIM]
    assert milk["path"] == "inspection" and milk["by"] == "central" and "content" in milk
    assert milk["source"] == "central:e-milk-01"
    assert J.evaluate(v, milk) == "effective"
    assert J.answer_record(v, found, CLAIM, "says_so")["conclusion"] == "good"
    # 中央が直さなかった主張（中身が今の版と違う）は、検収にならない
    assert J.evaluate(v, by_target["claim:c-origin-brazil-024"]) == "stale"


def test_穴8_割り当てられない値は親の出典の全部に依る(env):
    root = copy(env, "kanto")

    def no_rest(d):
        for s in d["years"][0]["sources"]:
            s["field"] = "notes" if s["field"] == "rounds" else s["field"]
    edit_json(root / "admission_results" / "1.json", no_rest)
    v0 = V.open_vessel(str(root))
    male = unit_of(v0, A1, **MALE_2026)
    [x] = [val for val in V.values_of(v0.get(A1), v0.kinds["admission_result"], male) if val.name == "applicants"]
    assert len(x.sources) == 2  # 名指しも rounds も無い値は、そのまとまりの出典の全部
    js = judge_values(v0, A1, male, ["applicants", "capacity"])
    edit_json(root / "admission_results" / "1.json",
              lambda d: d["years"][0]["sources"][1].__setitem__("quote", "要項（書き換え）"))
    assert states(V.open_vessel(str(root)), js) == ["stale", "stale"]


def test_穴8_丸ごと置き換えの候補は正しい旧新4組(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    dup = [u for u in v0.get(A312).units if u.ambiguous]
    js = [j for u in dup for j in judge_values(v0, A312, u, ["admitted"])]
    old = {u.pointer for u in dup}

    def replace(d):
        d["years"][0]["rounds"][0]["admitted"] = 2
        d["years"][0]["rounds"][1]["admitted"] = 2
    edit_json(root / "admission_results" / "312.json", replace)
    v1 = V.open_vessel(str(root))
    new = {u.pointer for u in v1.get(A312).units if u.ambiguous}
    assert len(new) == 2 and not new & old
    pairs = {(c["vanished"], a) for c in J.candidates(v1, js) for a in c["appeared"]}
    assert pairs == set(itertools.product(old, new))
