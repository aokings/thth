"""台帳の器 段 1「読んで、同じ対象を指す」— 受け入れの試験 1「判断が正しい対象にだけ残る」。

物差しは `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` の §2（(a)〜(g) と「加えて」）。見本は
`tests/fixtures/register_vessel/` に、期待する答えが挙げた実物の形を小さく写した（値は見本。実物を大量に写さない）:

- kanto: 関東の入試結果（青山学院の男女の行と男女を合わせた定員・志学館の区別できない 2 行・コースで分かれる重なる
  組）・学校 142 のマスタ（2026）と要項（2027）と結合したレコード（欄ごとの年度）
- kopicha: こぴちゃの主張・根拠・点・来歴の控え（記事 → 主張・主張の文の sha1 頭 12 字）・中央の判断・枚（YAML）
- mmr: MMR の照合台帳（出し口 2 つ・理由の種類つきの held）
- にがみラボの混ざった改行の表は、バイトをそのまま作るため試験の中で書く

［レーン待ち］の項は、期待する答えに書かれた答えで組み、試験の名前に「レーン待ち」と残した。
柵: 鍵で決まらない単位を黙って 1 つにしない・範囲の外の YAML を黙って読み違えず断る・今の見本の台帳 3 つが同じ結果・
宣言に何を書いても命令が走らない。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from thth import cli
from thth.register import form, judgment as J, ledger, vessel as V, vessel_cli

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "register_vessel"
EXAMPLES = ROOT / "registers.example"
AT = "2026-11-14T10:00:00+09:00"
yaml_needed = pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    return {"tmp": tmp_path, "root": root}


def copy(env, name) -> pathlib.Path:
    target = env["tmp"] / name
    shutil.copytree(FIX / name, target)
    return target


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def add(capsys, path, name, project="sample", kind="media"):
    rc_, _out, err = run(capsys, "register", "add", name, "--path", str(path), "--project", project, "--kind", kind,
                         "--by", "m")
    assert rc_ == 0, err


def edit_json(path: pathlib.Path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    # 関東の年度の置き換えの道具と同じく、1 枚を決まった字下げで丸ごと書き直す（バイトの形は変わる）。
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def unit_of(vessel, ref, **key):
    return V.find_units(vessel.get(ref), key)


def judge_values(vessel, ref, unit, names, *, by="v", question="reads_right", scope=None):
    out = []
    for name in names:
        out.append(J.make(vessel, {"record": ref, "unit": unit.pointer, "value": name}, question, by=by, at=AT,
                          conclusion="good", how="原典で照らした", scope=scope))
    return out


def states(vessel, judgments) -> list:
    return [J.evaluate(vessel, j) for j in judgments]


A1 = "admission_result:1"
A312 = "admission_result:312"
A199 = "admission_result:199"
MALE_2026 = {"group.year": 2026, "gender": "男子"}
FEMALE_2026 = {"group.year": 2026, "gender": "女子"}


# ================================================================== 試験 1 (a)

def test_試験1a_並べ替えと行を挟んでも判断は同じ値に付いたまま(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    rec312 = v0.get(A312)
    dup = [u for u in rec312.units if u.ambiguous]
    assert len(dup) == 2 and len({u.pointer for u in dup}) == 2  # 鍵が同じで中身が違う 2 行は 2 つの指し先
    js = []
    for unit in dup:
        js += judge_values(v0, A312, unit, ["admitted"])
    js += judge_values(v0, A1, unit_of(v0, A1, **MALE_2026), ["applicants"])
    js += judge_values(v0, A1, unit_of(v0, A1, **FEMALE_2026), ["applicants"])
    before = {r.ref: {u.pointer for u in r.units} for r in v0.all_records if r.units}
    versions = {r.ref: r.version for r in v0.all_records}

    edit_json(root / "admission_results" / "312.json",
              lambda d: d["years"][0]["rounds"].__setitem__(slice(0, 2), d["years"][0]["rounds"][1::-1]))
    edit_json(root / "admission_results" / "1.json", lambda d: d["years"][0]["rounds"].insert(0, {
        "round": "見本", "date": "2026-02-03", "time_of_day": "午後", "gender": "男子", "subject_type": "2科",
        "applicants": 10}))
    edit_json(root / "admission_results" / "199.json", lambda d: d["years"][0]["rounds"].reverse())

    v1 = V.open_vessel(str(root))
    assert states(v1, js) == ["effective"] * 4  # 移った判断 0・外れた判断 0
    after = {r.ref: {u.pointer for u in r.units} for r in v1.all_records if r.units}
    assert after[A312] == before[A312] and after[A199] == before[A199]
    assert len(after[A1] - before[A1]) == 1 and before[A1] <= after[A1]  # 挟んだ行は判断の無い新しい単位
    for ref in (A1, A312, A199):
        assert v1.get(ref).version != versions[ref]  # 原物の版（全バイト）は変わる
    amb = V.ambiguous_units(v1)
    assert sum(len(a["units"]) for a in amb) == 6  # 重なる組の行は、組の数 × 2 の指し先のまま


def test_試験1a_レーン待ち_関東問い3_コースを鍵に足しても移らない(env):
    """［待ち］関東 問い 3（後ろ半分）: 199 のコースを鍵に足すか。足すと指し方が「鍵」になるだけで、移らないのは同じ。"""
    root = copy(env, "kanto")
    decl = root / ".thth" / "kinds" / "admission_result.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    data["units"]["key"].append("notes")  # 見本ではコースを notes に書いた
    decl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    v0 = V.open_vessel(str(root))
    assert not [u for u in v0.get(A199).units if u.ambiguous]
    js = judge_values(v0, A199, unit_of(v0, A199, gender="男子", notes="コースＩ"), ["applicants"])
    edit_json(root / "admission_results" / "199.json", lambda d: d["years"][0]["rounds"].reverse())
    assert states(V.open_vessel(str(root)), js) == ["effective"]


# ================================================================== 試験 1 (b)

def test_試験1b_数値を1つ直すとその値の判断だけが効かなくなる(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    male, female = unit_of(v0, A1, **MALE_2026), unit_of(v0, A1, **FEMALE_2026)
    names = ["applicants", "examinees", "admitted", "min_score", "ratio_actual"]
    js_male = judge_values(v0, A1, male, names)
    js_female = judge_values(v0, A1, female, ["applicants"])
    js_2025 = judge_values(v0, A1, unit_of(v0, A1, **{"group.year": 2025}), ["examinees"])

    edit_json(root / "admission_results" / "1.json", lambda d: d["years"][0]["rounds"][0].__setitem__("examinees", 297))
    v1 = V.open_vessel(str(root))
    assert unit_of(v1, A1, **MALE_2026).pointer == male.pointer  # 行の指し先は変わらない（性別で鍵が 1 つに決まる）
    assert dict(zip(names, states(v1, js_male))) == {"applicants": "effective", "examinees": "stale",
                                                     "admitted": "effective", "min_score": "effective",
                                                     "ratio_actual": "effective"}  # 器は ratio_actual を計算し直さない
    assert states(v1, js_female + js_2025) == ["effective", "effective"]
    ans = J.answer_value(v1, js_male, A1, male.pointer, "examinees", as_scope="gender=男子")
    assert ans["usable"] is False and ans["reason_kind"] == "no_inspection_for_new_version"


def test_試験1b_鍵で決まらない行は値を1つ直すと行の値の判断を全部引き継がない(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    four = next(u for u in v0.get(A312).units if u.item["applicants"] == 4)
    js = judge_values(v0, A312, four, ["applicants", "examinees", "admitted"])
    edit_json(root / "admission_results" / "312.json", lambda d: d["years"][0]["rounds"][1].__setitem__("examinees", 5))
    assert states(V.open_vessel(str(root)), js) == ["vanished"] * 3


# ================================================================== 試験 1 (c)

def test_試験1c_レーン待ち_関東_出典の読み方_親の出典だけを変えると依る値の判断だけが効かなくなる(env):
    """［待ち］関東・新しく聞く: `sources[].field` の読み方と `retrieved` を依る欄に入れるか。期待する答えの叩き台
    （名指す出典 → `rounds` の出典 → 全部）で組んだ。"""
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    male, female = unit_of(v0, A1, **MALE_2026), unit_of(v0, A1, **FEMALE_2026)
    second = ["capacity", "full_score", "date"]
    first = ["applicants", "examinees", "admitted", "ratio_actual", "min_score"]
    js2 = judge_values(v0, A1, male, second) + judge_values(v0, A1, female, ["full_score", "date"])
    js1 = judge_values(v0, A1, male, first)
    js_2025 = judge_values(v0, A1, unit_of(v0, A1, **{"group.year": 2025}), ["capacity", "applicants"])

    edit_json(root / "admission_results" / "1.json",
              lambda d: d["years"][0]["sources"][1].__setitem__("quote", "募集人員 男女合わせて約140名（書き換え）"))
    v1 = V.open_vessel(str(root))
    assert states(v1, js2) == ["stale"] * 5
    assert states(v1, js1 + js_2025) == ["effective"] * 7

    # 逆に 1 つめの出典（rounds）を変えると、名指す出典の無い値だけが効かなくなる
    root2 = env["tmp"] / "kanto2"
    shutil.copytree(FIX / "kanto", root2)
    edit_json(root2 / "admission_results" / "1.json",
              lambda d: d["years"][0]["sources"][0].__setitem__("quote", "志願者349（書き換え）"))
    v2 = V.open_vessel(str(root2))
    assert states(v2, js1) == ["stale"] * 5
    assert states(v2, js2) == ["effective"] * 5


def test_試験1c_出典の依る欄を宣言しなければ取った日だけの書き換えでも効かなくなる(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    js = judge_values(v0, A1, unit_of(v0, A1, **MALE_2026), ["applicants"])
    edit_json(root / "admission_results" / "1.json",
              lambda d: d["years"][0]["sources"][0].__setitem__("retrieved", "2026-11-01"))
    assert states(V.open_vessel(str(root)), js) == ["stale"]


# ================================================================== 試験 1 (d)

def test_試験1d_レーン待ち_関東問い3_男子の行への判断は男女を合わせた定員に及ばない(env):
    """［待ち］関東 問い 3（前半分）: `notes` の「男女の合計」を欄に起こすか。起こさない間は「範囲が未宣言」。"""
    root = copy(env, "kanto")
    v = V.open_vessel(str(root))
    male = unit_of(v, A1, **MALE_2026)
    row = J.make(v, {"record": A1, "unit": male.pointer}, "reads_right", by="v", at=AT, conclusion="good")
    ans = J.answer_value(v, [row], A1, male.pointer, "capacity", as_scope="gender=男子")
    assert ans["usable"] is False and ans["reason_kind"] == "scope_undeclared"
    # 範囲が行の鍵と同じ値には、行への判断が及ぶ
    ok = J.answer_value(v, [row], A1, male.pointer, "applicants", as_scope="gender=男子")
    assert ok["usable"] is True and ok["scope"] == "gender=男子"
    # 監査の記録（値そのものを確かめ、範囲を「男女の合計」と書いた判断）があれば、合計としては使え、男子の定員としては使えない
    audit = J.make(v, {"record": A1, "unit": male.pointer, "value": "capacity"}, "reads_right", by="audit", at=AT,
                   conclusion="good", scope="男女の合計")
    assert J.answer_value(v, [row, audit], A1, male.pointer, "capacity", as_scope="男女の合計")["usable"] is True
    bad = J.answer_value(v, [row, audit], A1, male.pointer, "capacity", as_scope="gender=男子")
    assert bad["usable"] is False and bad["reason_kind"] == "scope_mismatch"
    # 行への判断は、定員の値が変わっても効いたまま（範囲を別に持つ値は行の依る所に入らない）
    edit_json(root / "admission_results" / "1.json", lambda d: d["years"][0]["rounds"][0].__setitem__("capacity", 141))
    assert J.evaluate(V.open_vessel(str(root)), row) == "effective"


def test_試験1d_範囲の欄があれば範囲はその欄から読む(env):
    root = copy(env, "kanto")
    v = V.open_vessel(str(root))
    shared = unit_of(v, "fees:142", round="帰国生入学試験")
    [value] = [x for x in V.values_of(v.get("fees:142"), v.kinds["fees"], shared) if x.name == "capacity"]
    assert value.scope_kind == "separate" and value.scope == "capacity_shared=true"
    plain = unit_of(v, "fees:142", round="午後特別入学試験")
    [value] = [x for x in V.values_of(v.get("fees:142"), v.kinds["fees"], plain) if x.name == "capacity"]
    assert value.scope is None  # 欄が無い回は未宣言（推測で埋めない）


# ================================================================== 試験 1 (e)

def test_試験1e_レーン待ち_関東問い1_2026年度の値への判断は2027年度に及ばない(env):
    """［待ち］関東 問い 1: 判断を出所の値に縫い止める（推し）とき。"""
    root = copy(env, "kanto")
    v = V.open_vessel(str(root))
    master = unit_of(v, "school_master:142", round="午後特別入学試験")
    js = judge_values(v, "school_master:142", master, ["subject_options", "capacity"])
    # 操作 1: 2026 年度の値を 2027 年度の値として使えるか → 使えない。2026 年度の値としてなら使える
    no = J.answer_value(v, js, "school_master:142", master.pointer, "subject_options", as_time=2027)
    assert no["usable"] is False and no["reason_kind"] == "time_mismatch"
    assert J.answer_value(v, js, "school_master:142", master.pointer, "subject_options", as_time=2026)["usable"]
    # 操作 2: 要項の 2027 年度の定員 30 は、値が同じでも別の単位で、判断は無い
    fees = unit_of(v, "fees:142", round="午後特別入学試験")
    same = J.answer_value(v, js, "fees:142", fees.pointer, "capacity", as_time=2027)
    assert same["usable"] is False and same["reason_kind"] == "no_judgment"


def test_試験1e_結合したレコードで欄の年度が進むと前の年度の判断は前の単位に残る(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    rec = unit_of(v0, "school_record:142", round="午後特別入学試験")
    js = judge_values(v0, "school_record:142", rec, ["subject_options"])
    assert js[0]["target"]["time"] == 2026
    # 操作 3: 2027 年度の要項に同じ文が足され、結合し直すと field_years だけが 2027 に進む（値は同じ・prev は作られない）
    edit_json(root / "school_records" / "142.json",
              lambda d: d["admissions"]["rounds"][0]["field_years"].__setitem__("subject_options", 2027))
    v1 = V.open_vessel(str(root))
    assert states(v1, js) == ["vanished"]  # 2026 年度の単位はもう無い（判断は 2026 年度の単位に付いたまま）
    unit = unit_of(v1, "school_record:142", round="午後特別入学試験")
    ans = J.answer_value(v1, js, "school_record:142", unit.pointer, "subject_options", as_time=2027)
    assert ans["usable"] is False and ans["reason_kind"] == "no_judgment"


def test_試験1e_同じ値が翌年も続いても引き継がない(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    js = judge_values(v0, A1, unit_of(v0, A1, **{"group.year": 2025}), ["admitted"])  # 2025 年度 109
    v = V.open_vessel(str(root))
    male26 = unit_of(v, A1, **MALE_2026)  # 2026 年度も 109
    ans = J.answer_value(v, js, A1, male26.pointer, "admitted")
    assert ans["usable"] is False and ans["reason_kind"] == "no_judgment"


# ================================================================== 試験 1 (f)

CLAIM = "claim:c-term-tea-grade-ctc-res-milktea1-24"
OLD = "CTC 茶葉の水浸出物は 17.3% とされる"
NEW = "CTC 茶葉の水浸出物は 17.1% とされ、テアニンは 18.6% とされる"


def rewrite_csv(path: pathlib.Path, row_id: str, **changes) -> None:
    import csv
    import io
    text = path.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    head = rows[0]
    for row in rows[1:]:
        if row and row[0] == row_id:
            for key, value in changes.items():
                row[head.index(key)] = value
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def test_試験1f_文を直して状態を据え置くと新しい版は観測だけ(env):
    root = copy(env, "kopicha")
    v0 = V.open_vessel(str(root))
    base = J.accept_baseline(v0, "claim", "says_so", by="masaru", at=AT)
    assert J.answer_record(v0, base, CLAIM, "says_so")["conclusion"] == "good"
    claims = root / "data" / "knowledge" / "claims.csv"
    rewrite_csv(claims, CLAIM.split(":")[1], statement_ja=NEW, object_text_ja="水浸出物 17.1%")
    v1 = V.open_vessel(str(root))
    ans = J.answer_record(v1, base, CLAIM, "says_so")
    assert ans["observed"] == "supported"  # 書いてある状態は観測として控える
    assert ans["conclusion"] == "pending" and ans["reason_kind"] == "no_inspection_for_new_version"
    # 来歴の控えは古い文の指紋なので「見直す本文」が 1。控えを取り直すと 0 になるが、それは判断ではない
    assert len(V.links(v1)["review"]) == 1
    hashes = root / "data" / "knowledge" / "article-claims.csv"
    hashes.write_text(hashes.read_text(encoding="utf-8").replace(
        hashlib.sha1(OLD.encode()).hexdigest()[:12], hashlib.sha1(NEW.encode()).hexdigest()[:12]), encoding="utf-8")
    v2 = V.open_vessel(str(root))
    assert V.links(v2)["review"] == []
    assert J.answer_record(v2, base, CLAIM, "says_so")["conclusion"] == "pending"


def test_試験1f_文と状態を同時に書き換えても承認にならない(env):
    root = copy(env, "kopicha")
    v0 = V.open_vessel(str(root))
    base = J.accept_baseline(v0, "claim", "says_so", by="masaru", at=AT)
    rewrite_csv(root / "data" / "knowledge" / "claims.csv", "c-origin-brazil-024",
                statement_ja="ブラジルの豆はナッツとチョコレートの香りがする", claim_status="supported")
    ans = J.answer_record(V.open_vessel(str(root)), base, "claim:c-origin-brazil-024", "says_so")
    assert ans["observed"] == "supported" and ans["conclusion"] == "pending"
    assert ans["reason_kind"] == "no_inspection_for_new_version"


def test_試験1f_レーン待ち_こぴちゃ問い2_中央の直したあとの文が今の版と合えば検収の記録になる(env):
    """［待ち］こぴちゃ 問い 2（V・T の判定と verify_input の突き合わせの欄）。中央の行は「確かめた中身そのもの」を
    持つ検収の記録として読む。V・T の判定は直す前の文を見た物なので、新しい版には結ばない（別の者の判断は 0）。"""
    root = copy(env, "kopicha")
    v0 = V.open_vessel(str(root))
    vt = J.make(v0, {"record": CLAIM}, "says_so", by="verifier-v", at=AT, word="supported", how="原典で照らした")
    rewrite_csv(root / "data" / "knowledge" / "claims.csv", CLAIM.split(":")[1], statement_ja=NEW,
                object_text_ja="水浸出物 17.1%")
    v1 = V.open_vessel(str(root))
    central = v1.get("central:e-milk-01").values
    content = {k: central[k] for k in ("statement_ja", "object_text_ja", "claim_status")}
    integ = J.make(v1, {"record": CLAIM}, "says_so", by="central", at=AT, word="supported", how="統合の者",
                   content=content)
    assert "seen" not in integ  # 指紋は持たない。中身そのものを持つ
    assert J.evaluate(v1, vt) == "stale" and J.evaluate(v1, integ) == "effective"
    ans = J.answer_record(v1, [vt, integ], CLAIM, "says_so")
    assert ans["conclusion"] == "good" and ans["by"] == "central" and ans["effective"] == 1
    # 中身が 1 字でも違えば、その版への検収にはならない
    off = J.make(v1, {"record": CLAIM}, "says_so", by="central", at=AT, word="supported",
                 content=dict(content, statement_ja=OLD))
    assert J.evaluate(v1, off) == "stale"


def test_試験1f_状態の欄は依る欄から外せない(env):
    root = copy(env, "kopicha")
    v0 = V.open_vessel(str(root))
    q = V.question_of(v0.kinds["claim"], "says_so")
    assert "claim_status" in q.depend_fields()
    base = J.accept_baseline(v0, "claim", "says_so", by="masaru", at=AT)
    rewrite_csv(root / "data" / "knowledge" / "claims.csv", CLAIM.split(":")[1], claim_status="contested")
    assert J.answer_record(V.open_vessel(str(root)), base, CLAIM, "says_so")["conclusion"] == "pending"


# ================================================================== 試験 1 (g)

def test_試験1g_区別できない2行の片方を直すと器は結ばず候補を見せる(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    one = next(u for u in v0.get(A312).units if u.item["applicants"] == 1)
    four = next(u for u in v0.get(A312).units if u.item["applicants"] == 4)
    j_one = judge_values(v0, A312, one, ["admitted"])
    j_four = judge_values(v0, A312, four, ["admitted"])
    edit_json(root / "admission_results" / "312.json", lambda d: d["years"][0]["rounds"][1].__setitem__("admitted", 4))
    v1 = V.open_vessel(str(root))
    assert states(v1, j_one) == ["effective"] and states(v1, j_four) == ["vanished"]
    [cand] = J.candidates(v1, j_one + j_four)
    assert cand["vanished"] == four.pointer and len(cand["appeared"]) == 1  # 同じ鍵で、消えた 1 つと現れた 1 つ
    assert cand["appeared"][0] not in {one.pointer, four.pointer}
    # 候補が 1 つでも自動では結ばない（新しい指し先への判断は無い）
    ans = J.answer_value(v1, j_one + j_four, A312, cand["appeared"][0], "admitted")
    assert ans["reason_kind"] == "no_judgment"
    # 人が名指しして結ぶ。直した者と同じ者は結べない
    from thth.register.errors import RegisterRefused
    with pytest.raises(RegisterRefused) as caught:
        J.carry(v1, j_four[0], cand["appeared"][0], by="editor", at=AT, changed_by="editor")
    assert caught.value.code == "register_judgment_same_person"
    carried = J.carry(v1, j_four[0], cand["appeared"][0], by="checker", at=AT, changed_by="editor")
    assert J.evaluate(v1, carried) == "effective" and carried["carries"] == j_four[0]["id"]


def test_試験1g_丸ごと置き換えて2行とも変わると4通りを候補にしてどれも結ばない(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    dup = [u for u in v0.get(A312).units if u.ambiguous]
    js = [j for u in dup for j in judge_values(v0, A312, u, ["admitted"])]

    def replace(d):
        d["years"][0]["rounds"][0]["admitted"] = 2
        d["years"][0]["rounds"][1]["admitted"] = 2
    edit_json(root / "admission_results" / "312.json", replace)
    v1 = V.open_vessel(str(root))
    found = J.candidates(v1, js)
    assert len(found) == 2 and sum(len(c["appeared"]) for c in found) == 4
    assert all(J.evaluate(v1, j) == "vanished" for j in js)


def test_柵_鍵で決まらない単位を黙って1つにしない(capsys, env):
    root = copy(env, "kanto")
    v = V.open_vessel(str(root))
    from thth.register.errors import RegisterRefused
    with pytest.raises(RegisterRefused) as caught:
        V.find_units(v.get(A312), {"gender": "男子"})
    assert caught.value.code == "register_unit_ambiguous" and len(caught.value.extra["candidates"]) == 2
    add(capsys, root, "kanto")
    rc_, value, _ = jrun(capsys, "register", "links", "kanto", A312, "--key", "gender=男子")
    assert rc_ == 2 and value["code"] == "register_unit_ambiguous" and len(value["candidates"]) == 2
    assert value["why"] and value["fix"] and value["better"]
    rc_, out, err = run(capsys, "register", "links", "kanto", A312, "--key", "gender=男子")
    assert rc_ == 2 and "通すには: " in err and "もっと良く: " in err
    # 指し先で名指せば引ける
    rc_, value, _ = jrun(capsys, "register", "links", "kanto", A312, "--unit", value["candidates"][0])
    assert rc_ == 0 and value["unit"]["ambiguous"] is True


def test_柵_同じ番号の記録は黙って1つにしない(capsys, env):
    root = copy(env, "kopicha")
    claims = root / "data" / "knowledge" / "claims.csv"
    lines = claims.read_text(encoding="utf-8").splitlines(keepends=True)
    claims.write_text("".join(lines + [lines[1]]), encoding="utf-8")
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_record_invalid"
    assert any("id_duplicate" in p["words"] for p in value["problems"])
    rc_, value, _ = jrun(capsys, "register", "links", "kopicha", CLAIM)
    assert rc_ == 2 and value["code"] == "register_unit_ambiguous"


# ================================================================== 加えて 1: 支えと反証が両方ある主張

def test_加えて1_支えと反証が両方ある主張は語を上書きしない(capsys, env):
    root = copy(env, "kopicha")
    v = V.open_vessel(str(root))
    base = J.accept_baseline(v, "claim", "says_so", by="masaru", at=AT)
    contested = J.answer_record(v, base, "claim:c-origin-brazil-024", "says_so")
    assert contested["conclusion"] == "pending" and contested["reason_kind"] == "both_sides"  # だめにしない
    supported = J.answer_record(v, base, "claim:c-origin-brazil-cerrado-003", "says_so")
    assert supported["conclusion"] == "good"  # 反する根拠があることだけで語を上書きしない
    edges = V.links(v)["edges"]
    for ref in ("claim:c-origin-brazil-024", "claim:c-origin-brazil-cerrado-003"):
        assert {e["meaning"] for e in edges if e["to"] == ref} == {"supports", "contradicts"}
    # 器が意味を知らない relation の語（context）は、支えにも反証にも数えず「関わる」
    assert {e["meaning"] for e in edges if e["to"] == CLAIM and e["from"].startswith("evidence:")} == {
        "supports", "related"}
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 0 and value["health"]["contradicted_good"] == 1


def test_加えて1_判断が無ければ書いてある語は観測だけ(env):
    v = V.open_vessel(str(copy(env, "kopicha")))
    ans = J.answer_record(v, [], "claim:c-origin-brazil-cerrado-003", "says_so")
    assert ans["observed"] == "supported" and ans["conclusion"] == "pending" and ans["reason_kind"] == "no_judgment"


# ================================================================== 加えて 2: 断る書き方の例（YAML）

@yaml_needed
def test_加えて2_今の書き方は全部受け入れ型を決めつけずに字のまま読む(env):
    v = V.open_vessel(str(copy(env, "kopicha")))
    page = v.get("page:brazil")
    assert page.inspected and page.problems == []
    # 段 4 §9: 引用していない null と真偽だけは型にする（日付は字のまま）
    assert page.values["updated"] == "2026-08-27" and page.values["draft"] is False
    assert page.values["parent"] is None and page.values["aliases"] == ["Brazil"]
    assert page.values["summary"].startswith("ブラジルの見本の頁。\n")
    assert v.get("tea_page:tieguanyin").values["tags"] == {"kind": "oolong"}


@yaml_needed
@pytest.mark.parametrize("text, word", [
    ("name: a\nname: b\n", "key_duplicate:2"),
    ("a:\n  b: 1\n  b: 2\n", "key_duplicate:3"),
    ("a: &x 1\nb: *x\n", "anchor:1"),
    ("name: a\n---\nname: b\n", "multi_document:2"),
    ("updated: !!str 2026-08-27\n", "tag:1"),
    ("base: {a: 1}\n<<: {b: 2}\n", "merge:2"),
])
def test_柵_範囲の外のYAMLは黙って読み違えず語と行を言って断る(capsys, env, text, word):
    root = copy(env, "kopicha")
    (root / "src" / "content" / "origins" / "bad.yaml").write_text(text, encoding="utf-8")
    v = V.open_vessel(str(root))
    rec = v.get("page:bad")
    assert rec.problems == [f"yaml:{word}"] and rec.values is None and len(rec.version) == 64  # 版は取る
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_yaml_refused"
    assert value["why"] and value["fix"] and value["better"]
    assert any(p["words"] == [f"yaml:{word}"] for p in value["problems"])


def test_YAMLの部品が無い機械では中は見ていないと言い版だけ取る(capsys, env, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    root = copy(env, "kopicha")
    v = V.open_vessel(str(root))
    page = v.get("page:brazil")
    assert page.inspected is False and page.values is None and page.problems == []
    assert page.version == hashlib.sha256((root / "src/content/origins/brazil.yaml").read_bytes()).hexdigest()
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 0 and value["not_inspected"] == len(list((root / "src" / "content").rglob("*.yaml")))  # 枚の全部
    rc_, value, _ = jrun(capsys, "register", "kinds", "kopicha")
    assert value["yaml_reader"] is False


# ================================================================== 段 1 の芯: 版・依る所・内側の単位・観測

def test_混ざった改行の表は書き直さず版は全バイトで依る所は読んだ値(env):
    root = env["tmp"] / "nigami"
    (root / ".thth" / "kinds").mkdir(parents=True)
    (root / ".thth" / "kinds" / "hub.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "hub", "place": "data/topic-hubs.csv", "format": "csv", "id": {"field": "id"},
        "questions": [{"name": "reads_right", "depends": ["title", "question"]}]}), encoding="utf-8")
    (root / "data").mkdir()
    table = root / "data" / "topic-hubs.csv"
    raw = (b"id,title,question,path,sort_order\r\n"
           b"beer-ibu,IBU,\"IBU \xe3\x81\xaf\r\n\xe4\xbd\x95\xe3\x81\x8b\",/beer/ibu/,1\n"
           b"coffee-cga,CGA,\xe8\x8b\xa6\xe5\x91\xb3,/coffee/cga/,2\r\n"
           b"tea-catechin,catechin,x,/tea/catechin/,3\n")
    table.write_bytes(raw)
    v0 = V.open_vessel(str(root))
    assert [r.id for r in v0.all_records] == ["beer-ibu", "coffee-cga", "tea-catechin"]
    assert v0.get("hub:beer-ibu").values["question"] == "IBU は\r\n何か"
    q = V.question_of(v0.kinds["hub"], "reads_right")
    deps0 = {r.ref: V.record_depend(v0.kinds["hub"], r, q) for r in v0.all_records}
    vers0 = {r.ref: r.version for r in v0.all_records}
    vessel_cli.check(str(root))
    assert table.read_bytes() == raw  # 読むだけで、1 バイトも書き直さない
    # 1 行の終わりを CRLF → LF にする: その行の版だけが変わり、依る所（読んだ値）はどの行も変わらない
    table.write_bytes(raw.replace(b",2\r\n", b",2\n"))
    v1 = V.open_vessel(str(root))
    deps1 = {r.ref: V.record_depend(v1.kinds["hub"], r, q) for r in v1.all_records}
    vers1 = {r.ref: r.version for r in v1.all_records}
    assert deps1 == deps0
    assert [ref for ref in vers0 if vers0[ref] != vers1[ref]] == ["hub:coffee-cga"]
    # 値の中の改行も LF にそろえて指紋を取る（CRLF の値と LF の値は同じ指紋）
    assert V.fp({"a": "x\r\ny"}) == V.fp({"a": "x\ny"}) and V.fp({"a": "x y"}) != V.fp({"a": "x  y"})


def test_依る所の指紋の作り方は設計書に書いた形と同じ():
    """設計 §5.1 の内側の単位の 3 に書いた作り方（キョンシーラボのレーンへ知らせる物）を、ここで固める。"""
    value = {"statement_ja": "茶は\r\n苦い", "locator": "p.3", "n": 296, "x": 2.7, "empty": "", "none": None}
    text = json.dumps({"empty": "", "locator": "p.3", "n": 296, "none": None, "statement_ja": "茶は\n苦い", "x": 2.7},
                      ensure_ascii=False, separators=(",", ":"))
    assert V.canonical(value) == text
    assert V.fp(value) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert V.fp({"n": 296}) != V.fp({"n": 296.0}) and V.fp({"a": ""}) != V.fp({"a": None})


def test_JSONの書き方だけが変わっても依る所は変わらず版は変わる(env):
    root = copy(env, "kanto")
    v0 = V.open_vessel(str(root))
    js = judge_values(v0, A1, unit_of(v0, A1, **MALE_2026), ["applicants"])
    path = root / "admission_results" / "1.json"
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=4),
                    encoding="utf-8")
    v1 = V.open_vessel(str(root))
    assert v1.get(A1).version != v0.get(A1).version
    assert states(v1, js) == ["effective"]


def test_枚と点は同じ物でも2つの記録で枚が点についてを指す(capsys, env):
    root = copy(env, "kopicha")
    v = V.open_vessel(str(root))
    page, point = v.get("page:brazil"), v.get("point:origin-brazil")
    assert page.kind != point.kind and page.path != point.path
    j = J.make(v, {"record": point.ref}, "reads_right", by="v", at=AT, conclusion="good")
    add(capsys, root, "kopicha")
    rc_, value, _ = jrun(capsys, "register", "links", "kopicha", "page:brazil")
    assert rc_ == 0 and value["points_to"] == [{"from": "page:brazil", "to": "point:origin-brazil",
                                                "meaning": "about", "field": None}]
    assert value["judgments"] == []  # 点への判断は枚に及ばない
    assert j["target"]["record"] == "point:origin-brazil"


def test_往復する主張は止めず輪を禁じるのは含む親置き換えるだけ(capsys, env):
    root = copy(env, "kopicha")
    v = V.open_vessel(str(root))
    graph = V.links(v)
    pairs = {(e["from"], e["to"]) for e in graph["edges"] if e["from"].startswith("claim:c-")}
    assert ("claim:c-origin-brazil-bourbon-01", "point:variety-bourbon") in pairs
    assert ("claim:c-variety-bourbon-brazil-01", "point:origin-brazil") in pairs
    assert graph["cycles"] == [] and graph["dangling"] == []
    add(capsys, root, "kopicha")
    assert jrun(capsys, "register", "check", "kopicha")[0] == 0
    # 親の輪は赤
    rewrite_csv(root / "data" / "knowledge" / "entities.csv", "origin-brazil", parent="origin-brazil-cerrado")
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ == 2 and value["code"] == "register_link_invalid"
    assert value["why"] and value["fix"] and value["better"]


def test_出し口の違う照合台帳は出し口ごとの観測と判断を持ち観測は判断にならない(env):
    root = copy(env, "mmr")
    v0 = V.open_vessel(str(root))
    rec = v0.get("credit:ap-other.produced_by")
    obs = {o["question"]: o["word"] for o in V.observations(v0.kinds["credit"], rec) if o["unit"] is None}
    assert obs == {"may_publish@artist_page": "false", "may_publish@song_page": "true"}
    held = [o for o in V.observations(v0.kinds["credit"], v0.get("credit:ap-sample.mastered_by")) if o["unit"]]
    assert sorted((o["word"], o["reason_kind"]) for o in held) == [
        ("held", "other_version"), ("held", "unresolved"), ("shown", None)]
    # 観測だけでは判断にならない
    assert J.answer_record(v0, [], rec.ref, "may_publish@song_page")["reason_kind"] == "no_judgment"
    song = J.accept_baseline(v0, "credit", "may_publish@song_page", by="masaru", at=AT)
    assert J.answer_record(v0, song, rec.ref, "may_publish@song_page")["conclusion"] == "good"
    assert J.answer_record(v0, song, rec.ref, "may_publish@artist_page")["reason_kind"] == "no_judgment"
    # 曲の頁の出し口の判断は、作者の頁の出し口の欄が変わっても効いたまま。自分の欄が変われば古い
    path = root / "site" / "src" / "credits" / "ap-other.produced_by.json"
    edit_json(path, lambda d: d.__setitem__("public", True))
    assert J.answer_record(V.open_vessel(str(root)), song, rec.ref, "may_publish@song_page")["conclusion"] == "good"
    edit_json(path, lambda d: d.__setitem__("public_song", False))
    assert J.answer_record(V.open_vessel(str(root)), song, rec.ref, "may_publish@song_page")["reason_kind"] == \
        "no_inspection_for_new_version"


def test_目録は宣言とファイルから作り直せて番号でない枚は置き場に入らない(capsys, env):
    root = copy(env, "kanto")
    add(capsys, root, "kanto")
    rc_, value, _ = jrun(capsys, "register", "check", "kanto", "--catalog")
    assert rc_ == 0
    refs = [c["record"] for c in value["catalog"]]
    assert "admission_result:1" in refs and not any("_audit" in r for r in refs)
    first = value["catalog"]
    rc_, again, _ = jrun(capsys, "register", "check", "kanto", "--catalog")
    assert again["catalog"] == first
    assert not (root / ".thth" / "catalog.json").exists()  # 目録は正本でなく、書かない


def test_番号の表は名前が変わった記録の場所を言い候補を見せるだけ(env):
    root = copy(env, "mmr")
    (root / ".thth" / "ids.json").write_text(json.dumps({"schema": "thth.ids/1", "ids": {
        "credit:ap-sample.mastered_by": "site/src/credits/ap-sample.mastered_by.json"}}), encoding="utf-8")
    os.rename(root / "site/src/credits/ap-sample.mastered_by.json", root / "site/src/credits/ap-renamed.mastered_by.json")
    moves = V.ids_table_moves(V.open_vessel(str(root)))
    assert moves == [{"record": "credit:ap-sample.mastered_by", "path": "site/src/credits/ap-sample.mastered_by.json",
                      "candidates": ["site/src/credits/ap-other.produced_by.json",
                                     "site/src/credits/ap-renamed.mastered_by.json"]}]


# ================================================================== 柵: 今の見本の台帳 3 つが同じ結果

@pytest.mark.parametrize("name, kind", [("media-sample", "media"), ("cafe-sample", "store"), ("goods-sample", "store")])
def test_柵_今の見本の台帳3つは器でも同じ番号と同じ指紋で読める(capsys, env, name, kind):
    path = env["tmp"] / name
    shutil.copytree(EXAMPLES / name, path)
    before = form.scan(str(path))
    add(capsys, path, name, kind=kind)
    rc_, value, err = jrun(capsys, "register", "check", name)
    assert rc_ == 0, err
    assert value == {"register": name, "kind": kind, "rows": len(before["rows"]), "skipped": before["skipped"],
                     "problems": []}  # 宣言の無い台帳は今の check のまま
    v = V.open_vessel(str(path))
    assert sorted(r.id for r in v.all_records) == sorted(r.id for r in before["rows"])
    kind_ = v.kinds["thth_row"]
    q = V.question_of(kind_, "says_so")
    for row in before["rows"]:
        rec = v.get(f"thth_row:{row.id}")
        assert V.record_depend(kind_, rec, q) == "row:" + form.fingerprint(row.text)  # 依る所 = 今の指紋の取り方
        assert rec.version == hashlib.sha256((path / row.path).read_bytes()).hexdigest()
    rc_, value, _ = jrun(capsys, "register", "kinds", name)
    assert rc_ == 0 and value["declared"] is False and [k["kind"] for k in value["kinds"]] == ["thth_row"]


# ================================================================== 柵: 宣言に何を書いても命令が走らない

@pytest.fixture
def no_exec(monkeypatch):
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("宣言から命令が走った")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, boom)
    monkeypatch.setattr(os, "system", boom)
    monkeypatch.setattr(os, "popen", boom)
    return calls


@pytest.mark.parametrize("change, word", [
    (lambda d: d.__setitem__("command", "touch PWNED"), "unknown_key:command"),
    (lambda d: d.__setitem__("check", ["sh", "-c", "touch PWNED"]), "unknown_key:check"),
    (lambda d: d.__setitem__("place", "$(touch PWNED)"), "place"),
    (lambda d: d.__setitem__("place", "../../outside/*.json"), "place"),
    (lambda d: d["units"].__setitem__("key", ["round; touch PWNED"]), "units"),
    (lambda d: d.__setitem__("format", "python"), "format"),
    (lambda d: d.__setitem__("questions", [{"name": "x", "state": "__import__('os').system('touch PWNED')"}]),
     "questions"),
])
def test_柵_宣言に何を書いても命令は走らず知らない欄は断る(capsys, env, no_exec, change, word):
    root = copy(env, "kanto")
    decl = root / ".thth" / "kinds" / "admission_result.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    change(data)
    decl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    add(capsys, root, "kanto")
    for verb in (("check", "kanto"), ("kinds", "kanto"), ("links", "kanto", A1)):
        rc_, value, _ = jrun(capsys, "register", *verb)
        assert rc_ == 2 and value["code"] == "register_kind_invalid" and value["word"] == word
        assert value["why"] and value["fix"] and value["better"]
    assert not list(env["tmp"].rglob("PWNED")) and not pathlib.Path("PWNED").exists() and no_exec == []


def test_柵_宣言の注に命令らしい文を書いても字として読むだけ(capsys, env, no_exec):
    root = copy(env, "kanto")
    decl = root / ".thth" / "kinds" / "admission_result.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    data["note"] = "`touch PWNED`; $(touch PWNED) && rm -rf ~"
    decl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    add(capsys, root, "kanto")
    for verb in (("check", "kanto"), ("kinds", "kanto"), ("links", "kanto", A1, "--units")):
        assert jrun(capsys, "register", *verb)[0] == 0
    assert not list(env["tmp"].rglob("PWNED")) and no_exec == []


def test_段1の命令は台帳にもTHTHの側にも書かない(capsys, env):
    root = copy(env, "kopicha")
    add(capsys, root, "kopicha")

    def snapshot(base):
        return {p: p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}
    before, state_before = snapshot(root), snapshot(env["root"])
    for verb in (("check", "kopicha", "--catalog"), ("kinds", "kopicha"), ("links", "kopicha", CLAIM)):
        jrun(capsys, "register", *verb)
    assert snapshot(root) == before and snapshot(env["root"]) == state_before
