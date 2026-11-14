"""台帳の器 段 4「検査の部品」（指示 `docs/指示_台帳の器_段4_検査の形と済みの線_2026-11-14.md` §1 の 1・§2）。

部品ごとに「受ける例」と「断る例（赤になる例）」。加えて、宣言の攻め（どの値を書いても `DeclarationError("checks")`
で、例外が漏れず、命令も正規表現も受け取らない）・「全部が弱い種類なら赤」の `count` の言い換え・表／JSON の枚／
内側の単位・6,000 行ほどの表 2 枚での手間・YAML の部品が無い機械。見本は試験の中で小さく書く。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import pytest

from thth import cli
from thth.register import checks as C, judgment as J, vessel as V

AT = "2026-11-14T10:00:00+09:00"

CLAIM_KIND = {"schema": "thth.kind/1", "kind": "claim", "place": "claims.csv", "format": "csv",
              "id": {"field": "claim_id"}}
EVIDENCE_KIND = {"schema": "thth.kind/1", "kind": "evidence", "place": "evidence.csv", "format": "csv",
                 "id": {"field": "evidence_id"},
                 "pointers": [{"field": "claim_id", "to": "claim", "meaning": "supports"},
                              {"field": "source_id", "to": "source", "meaning": "related"}]}
SOURCE_KIND = {"schema": "thth.kind/1", "kind": "source", "place": "sources.csv", "format": "csv",
               "id": {"field": "source_id"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    return {"tmp": tmp_path, "root": root}


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


def with_checks(kind: dict, checks: list) -> dict:
    return {**kind, "checks": checks}


def result(root, cid, judgments=None):
    out = C.run(V.open_vessel(str(root)), judgments or [])
    return next(c for c in out["list"] if c["id"] == cid)


def bad_refs(root, cid, judgments=None) -> list:
    return sorted(r["record"] for r in result(root, cid, judgments)["records"])


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def add(capsys, path, name):
    rc_, _out, err = run(capsys, "register", "add", name, "--path", str(path), "--project", "sample", "--kind",
                         "media", "--by", "m")
    assert rc_ == 0, err


CLAIM_HEADER = ["claim_id", "status", "title", "url", "doi", "slug", "date", "lang", "reason"]


def claims_ledger(tmp, checks, rows, *, evidence=None, sources=None, extra_kinds=()):
    files = {"claims.csv": csv_text(CLAIM_HEADER, rows)}
    kinds = [with_checks(CLAIM_KIND, checks), *extra_kinds]
    if evidence is not None:
        files["evidence.csv"] = csv_text(["evidence_id", "claim_id", "strength", "source_id"], evidence)
        kinds.append(EVIDENCE_KIND)
    if sources is not None:
        files["sources.csv"] = csv_text(["source_id", "kind", "retracted"], sources)
        kinds.append(SOURCE_KIND)
    return make_ledger(tmp / "ledger", kinds, files)


# ================================================================== 部品ごと: 受ける例と断る例

def test_unique_重ならなければ受け_重なれば両方を赤にする(env):
    chk = [{"check": "unique", "id": "doi_once", "fields": ["doi"], "fold": "lower"}]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": "c1", "doi": "10.1/AB"}, {"claim_id": "c2", "doi": "10.1/cd"},
                                           {"claim_id": "c3", "doi": ""}, {"claim_id": "c4", "doi": ""}])
    assert result(root, "doi_once")["violations"] == 0  # 空の組は見ない（skip_blank の既定）
    (root / "claims.csv").write_text(csv_text(CLAIM_HEADER, [{"claim_id": "c1", "doi": "10.1/AB"},
                                                             {"claim_id": "c2", "doi": "10.1/ab"}]), encoding="utf-8")
    assert bad_refs(root, "doi_once") == ["claim:c1", "claim:c2"]  # 小文字にそろえて重なる


def test_unique_組で比べ_skip_blankをfalseにすると空の組も重なりに数える(env):
    chk = [{"check": "unique", "id": "pair", "fields": ["title", "slug"]},
           {"check": "unique", "id": "pair_blank", "fields": ["title", "slug"], "skip_blank": False}]
    rows = [{"claim_id": "a", "title": "t", "slug": "x"}, {"claim_id": "b", "title": "t", "slug": "y"},
            {"claim_id": "c"}, {"claim_id": "d"}]
    root = claims_ledger(env["tmp"], chk, rows)
    assert result(root, "pair")["violations"] == 0
    assert bad_refs(root, "pair_blank") == ["claim:c", "claim:d"]


def test_required_whenに合う記録だけ見る(env):
    # 段 4 §9: 空白を落とすのは trim を書いたときだけ（条件にも検査にも書く）
    chk = [{"check": "required", "id": "j_25", "when": {"status": {"in": ["established"], "trim": True}},
            "fields": ["url", "date"], "trim": True}]
    rows = [{"claim_id": "ok", "status": "established", "url": "https://a", "date": "2026-01-01"},
            {"claim_id": "draft", "status": "draft"},
            {"claim_id": "ng", "status": " established ", "url": "https://a", "date": "   "}]
    root = claims_ledger(env["tmp"], chk, rows)
    r = result(root, "j_25")
    assert r["checked"] == 2 and r["not_when"] == 1
    assert [(x["record"], x["reason"], x["fields"]) for x in r["records"]] == [("claim:ng", "blank", ["date"])]


def test_empty_この状態ならこの欄は空(env):
    chk = [{"check": "empty", "id": "j_14", "when": {"status": {"not_in": ["rejected"]}}, "fields": ["reason"]}]
    rows = [{"claim_id": "a", "status": "rejected", "reason": "x"}, {"claim_id": "b", "status": "draft"},
            {"claim_id": "c", "status": "draft", "reason": "残っている"}]
    root = claims_ledger(env["tmp"], chk, rows)
    assert bad_refs(root, "j_14") == ["claim:c"]


def test_one_of_少なくとも1つ_ちょうど1つ(env):
    chk = [{"check": "one_of", "id": "url_or_doi", "fields": ["url", "doi"]},
           {"check": "one_of", "id": "only_one", "fields": ["url", "doi"], "mode": "exactly_one"}]
    rows = [{"claim_id": "u", "url": "https://a"}, {"claim_id": "d", "doi": "10.1/x"},
            {"claim_id": "both", "url": "https://a", "doi": "10.1/x"}, {"claim_id": "none"}]
    root = claims_ledger(env["tmp"], chk, rows)
    assert bad_refs(root, "url_or_doi") == ["claim:none"]
    assert bad_refs(root, "only_one") == ["claim:both", "claim:none"]


def test_words_語の中から_空は既定で赤(env):
    chk = [{"check": "words", "id": "j_3", "field": "status", "in": ["draft", "established"]},
           {"check": "words", "id": "lang_words", "field": "lang", "in": ["ja", "en"], "allow_blank": True}]
    rows = [{"claim_id": "a", "status": " draft "}, {"claim_id": "b", "status": "Draft"}, {"claim_id": "c"}]
    root = claims_ledger(env["tmp"], chk, rows)
    r = result(root, "j_3")
    # 段 4 §9: 空白を落とさない（" draft " は語の外）
    assert {(x["record"], x["reason"]) for x in r["records"]} == {
        ("claim:a", "word_not_allowed"), ("claim:b", "word_not_allowed"), ("claim:c", "blank")}
    assert result(root, "lang_words")["violations"] == 0


@pytest.mark.parametrize("shape,good,bad", [
    ("slug", ["a", "a-b-1"], ["A", "a--b", "-a", "a-", "a b", "a_b"]),
    ("slug_loose", ["a", "a--b", "a-"], ["-a", "A"]),
    ("date", ["2026-11-14", "2026-02-31"], ["2026-1-1", "2026/11/14", "2026-11-14T00", "２０２６-11-14"]),
    ("date_partial", ["2026", "2026-11", "2026-11-14"], ["26", "2026-1", "2026-11-"]),
    ("lang", ["ja", "eng"], ["JA", "j", "japa"]),
    ("http_url", ["http://a", "https://a"], ["ftp://a", " http://a", "HTTP://a"]),
    ("https_url", ["https://a"], ["http://a"]),
    ("positive_int", ["1", "120"], ["0", "01", "-1", "1.0"]),
    ("sha256", ["a" * 64], ["a" * 63, "g" * 64, "A" * 64]),
])
def test_shape_備え付けの形の名前(env, shape, good, bad):
    chk = [{"check": "shape", "id": "s", "field": "slug", "shape": shape}]
    rows = [{"claim_id": f"g{i}", "slug": v} for i, v in enumerate(good)]
    rows += [{"claim_id": f"b{i}", "slug": v} for i, v in enumerate(bad)]
    rows.append({"claim_id": "blank", "slug": ""})  # 空は既定で受ける
    root = claims_ledger(env["tmp"], chk, rows)
    assert bad_refs(root, "s") == sorted(f"claim:b{i}" for i in range(len(bad)))


def test_shape_頭の字は字のまま比べ_正規表現として読まない(env):
    chk = [{"check": "shape", "id": "head", "field": "url", "prefix": "https://(.*)+$", "allow_blank": False}]
    rows = [{"claim_id": "lit", "url": "https://(.*)+$x"}, {"claim_id": "re", "url": "https://example"},
            {"claim_id": "none"}]
    root = claims_ledger(env["tmp"], chk, rows)
    r = result(root, "head")
    assert {(x["record"], x["reason"]) for x in r["records"]} == {("claim:re", "shape"), ("claim:none", "blank")}


def test_forbid_pair_禁じた組を指す記録を赤にし_よその台帳は確かめられないと数える(env):
    chk_e = [{"check": "forbid_pair", "id": "j_11", "when": {"strength": {"in": ["strong"]}}, "via": "source_id",
              "target": {"retracted": {"in": ["yes"]}}}]
    root = make_ledger(env["tmp"] / "ledger", [CLAIM_KIND, with_checks(EVIDENCE_KIND, chk_e), SOURCE_KIND], {
        "claims.csv": csv_text(CLAIM_HEADER, [{"claim_id": "c1"}]),
        "evidence.csv": csv_text(["evidence_id", "claim_id", "strength", "source_id"], [
            {"evidence_id": "e1", "claim_id": "c1", "strength": "strong", "source_id": "s_ok"},
            {"evidence_id": "e2", "claim_id": "c1", "strength": "strong", "source_id": "s_bad"},
            {"evidence_id": "e3", "claim_id": "c1", "strength": "weak", "source_id": "s_bad"},
            {"evidence_id": "e4", "claim_id": "c1", "strength": "strong", "source_id": "other:s9"},
        ]),
        "sources.csv": csv_text(["source_id", "kind", "retracted"], [
            {"source_id": "s_ok", "retracted": "no"}, {"source_id": "s_bad", "retracted": "yes"}]),
    })
    r = result(root, "j_11")
    assert [x["record"] for x in r["records"]] == ["evidence:e2"]
    assert r["records"][0]["targets"] == ["source:s_bad"]
    assert r["unverifiable"] == 1


def test_count_指してくる行の数と違う値の数(env):
    chk = [{"check": "count", "id": "j_19", "from": {"kind": "evidence", "via": "claim_id"}, "min": 1},
           {"check": "count", "id": "j_20", "when": {"status": {"in": ["established"]}},
            "from": {"kind": "evidence", "via": "claim_id"}, "distinct": "source_id", "min": 2,
            "trim": True},  # 段 4 §9: 空白を落とすのは trim を書いたときだけ
           {"check": "count", "id": "cap", "from": {"kind": "evidence", "via": "claim_id"}, "max": 2}]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": "c0", "status": "draft"},
                                           {"claim_id": "c1", "status": "established"},
                                           {"claim_id": "c2", "status": "established"}],
                         evidence=[{"evidence_id": "e1", "claim_id": "c1", "source_id": "s1"},
                                   {"evidence_id": "e2", "claim_id": "c1", "source_id": "s1"},
                                   {"evidence_id": "e3", "claim_id": "c1", "source_id": " s1 "},
                                   {"evidence_id": "e4", "claim_id": "c2", "source_id": "s1"},
                                   {"evidence_id": "e5", "claim_id": "c2", "source_id": "s2"},
                                   {"evidence_id": "e6", "claim_id": "c2", "source_id": ""}])
    assert bad_refs(root, "j_19") == ["claim:c0"]
    r = result(root, "j_20")
    assert [(x["record"], x["count"]) for x in r["records"]] == [("claim:c1", 1)]  # 空白を落として同じ値・空は数えない
    assert [(x["record"], x["reason"], x["count"]) for x in result(root, "cap")["records"]] == [
        ("claim:c1", "too_many", 3), ("claim:c2", "too_many", 3)]


@pytest.mark.parametrize("evidence,red", [
    ([], True),                                                       # 根拠 0
    ([{"strength": "weak"}, {"strength": "anecdote"}], True),         # 全部弱い
    ([{"strength": "weak"}, {"strength": "strong"}], False),          # 1 つ強い
])
def test_全部が弱い種類なら赤_をcountで言い換えた見本(env, capsys, evidence, red):
    chk = [{"check": "count", "id": "j_19", "when": {"status": {"in": ["established", "supported"]}},
            "from": {"kind": "evidence", "via": "claim_id"}, "min": 1, "note": "根拠が 1 つ以上"},
           {"check": "count", "id": "j_22", "when": {"status": {"in": ["established", "supported"]}},
            "from": {"kind": "evidence", "via": "claim_id"},
            "where": {"strength": {"not_in": ["weak", "anecdote"]}}, "min": 1,
            "note": "根拠の全部が弱い種類なら赤（弱い種類でない根拠が 1 つ以上、と言い換えた）"}]
    ev = [{"evidence_id": f"e{i}", "claim_id": "c1", **e} for i, e in enumerate(evidence)]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": "c1", "status": "supported"},
                                           {"claim_id": "c2", "status": "draft"}], evidence=ev)
    assert (bad_refs(root, "j_22") == ["claim:c1"]) is red
    assert (bad_refs(root, "j_19") == ["claim:c1"]) is (not evidence)
    add(capsys, root, "weak")
    rc_, value, _ = jrun(capsys, "register", "check", "weak")
    if red:
        assert rc_ == 2 and value["code"] == "register_record_invalid" and value["detail"].startswith("checks:")
        assert value["why"] and value["fix"] and value["better"]
        failing = {c["id"] for c in value["checks"]["list"] if c["violations"]}
        assert "j_22" in failing
    else:
        assert rc_ == 0 and value["checks"]["error"] == 0


def test_count_内側の単位を数える(env):
    kind = {"schema": "thth.kind/1", "kind": "school", "place": "schools/*.json", "format": "json",
            "units": {"groups": ["years", "*"], "items": ["rows", "*"], "key": ["group.year", "course"]},
            "checks": [{"check": "count", "id": "rows_min", "from": {"units": True}, "min": 2},
                       {"check": "count", "id": "boys", "from": {"units": True},
                        "where": {"sex": {"in": ["male"]}}, "max": 1}]}
    one = {"years": [{"year": 2025, "rows": [{"course": "a", "sex": "male"}, {"course": "b", "sex": "male"}]}]}
    two = {"years": [{"year": 2025, "rows": [{"course": "a", "sex": "female"}]}]}
    root = make_ledger(env["tmp"] / "ledger", [kind], {"schools/s1.json": json.dumps(one),
                                                       "schools/s2.json": json.dumps(two)})
    assert bad_refs(root, "rows_min") == ["school:s2"]
    assert bad_refs(root, "boys") == ["school:s1"]


def test_judged_今効いているよいの判断が足りなければ赤(env):
    kind = {"schema": "thth.kind/1", "kind": "page", "place": "pages/*.json", "format": "json",
            "questions": [{"name": "says_so", "depends": ["body"]}],
            "checks": [{"check": "judged", "id": "seen", "question": "says_so"},
                       {"check": "judged", "id": "seen_by_source", "question": "says_so", "how": "原典で照らした"}]}
    root = make_ledger(env["tmp"] / "ledger", [kind], {"pages/p1.json": json.dumps({"body": "x"}),
                                                       "pages/p2.json": json.dumps({"body": "y"}),
                                                       "pages/p3.json": json.dumps({"body": "z"})})
    v = V.open_vessel(str(root))
    js = [J.make(v, {"record": "page:p1"}, "says_so", by="a", at=AT, conclusion="good", how="原典で照らした"),
          J.make(v, {"record": "page:p2"}, "says_so", by="a", at=AT, conclusion="good"),
          J.make(v, {"record": "page:p3"}, "says_so", by="a", at=AT, conclusion="bad")]
    J.append(str(root), js)
    judgments = J.read(str(root))
    assert bad_refs(root, "seen", judgments) == ["page:p3"]
    assert bad_refs(root, "seen_by_source", judgments) == ["page:p2", "page:p3"]
    (root / "pages" / "p1.json").write_text(json.dumps({"body": "changed"}), encoding="utf-8")  # 古い判断は効かない
    assert bad_refs(root, "seen", judgments) == ["page:p1", "page:p3"]


# ================================================================== 表・JSON の枚・JSON の並び

def test_JSONの枚とJSONの並びでも回る(env):
    page = {"schema": "thth.kind/1", "kind": "page", "place": "pages/*.json", "format": "json",
            "checks": [{"check": "required", "id": "title", "fields": ["title"]},
                       {"check": "empty", "id": "no_tags", "fields": ["tags"]}]}
    row = {"schema": "thth.kind/1", "kind": "item", "place": "items.json", "format": "json", "records": "rows",
           "rows_at": ["items"], "id": {"field": "id"},
           "checks": [{"check": "words", "id": "state", "field": "state", "in": ["ok"]}]}
    root = make_ledger(env["tmp"] / "ledger", [page, row], {
        "pages/a.json": json.dumps({"title": "A", "tags": []}),
        "pages/b.json": json.dumps({"title": "", "tags": ["x"]}),
        "items.json": json.dumps({"items": [{"id": "i1", "state": "ok"}, {"id": "i2", "state": ["ok"]}]})})
    assert bad_refs(root, "title") == ["page:b"]
    assert bad_refs(root, "no_tags") == ["page:b"]  # 空の並びは空・中身のある並びは空でない
    r = result(root, "state")
    assert [(x["record"], x["reason"]) for x in r["records"]] == [("item:i2", "not_text")]


def test_条件の欄が辞書や並びならblankだけが合う(env):
    page = {"schema": "thth.kind/1", "kind": "page", "place": "pages/*.json", "format": "json",
            "checks": [{"check": "required", "id": "in_on_list", "when": {"tags": {"in": ["x"]}}, "fields": ["t"]},
                       {"check": "required", "id": "blank_on_list", "when": {"tags": {"blank": False}},
                        "fields": ["t"]}]}
    root = make_ledger(env["tmp"] / "ledger", [page], {"pages/a.json": json.dumps({"tags": ["x"]})})
    assert result(root, "in_on_list")["checked"] == 0
    assert bad_refs(root, "blank_on_list") == ["page:a"]


# ================================================================== check と kinds の出力

def test_checksの無い台帳は今までと同じでchecksの空の欄が足されるだけ(env, capsys):
    root = make_ledger(env["tmp"] / "ledger", [CLAIM_KIND], {"claims.csv": csv_text(CLAIM_HEADER, [{"claim_id": "a"}])})
    add(capsys, root, "plain")
    rc_, value, _ = jrun(capsys, "register", "check", "plain")
    assert rc_ == 0
    assert value["checks"] == {"total": 0, "error": 0, "warn": 0, "review": 0, "failed": 0, "list": []}
    rc_, value, _ = jrun(capsys, "register", "kinds", "plain")
    assert value["kinds"][0]["checks"] == []


def test_warnとreviewは赤にしない_人が読む出力に呼び名と注(env, capsys):
    chk = [{"check": "required", "id": "nice_title", "level": "warn", "fields": ["title"], "note": "題があると探しやすい"},
           {"check": "required", "id": "maybe_url", "level": "review", "fields": ["url"]}]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": "a"}])
    add(capsys, root, "soft")
    rc_, out, _ = run(capsys, "register", "check", "soft")
    assert rc_ == 0
    assert "nice_title" in out and "題があると探しやすい" in out and "claim:a" in out
    rc_, value, _ = jrun(capsys, "register", "check", "soft")
    assert value["checks"]["warn"] == 1 and value["checks"]["review"] == 1 and value["checks"]["error"] == 0
    rc_, out, _ = run(capsys, "register", "kinds", "soft")
    assert "検査 nice_title（required・warn）: 題があると探しやすい" in out
    rc_, value, _ = jrun(capsys, "register", "kinds", "soft")
    assert value["kinds"][0]["checks"][1] == {"id": "maybe_url", "check": "required", "level": "review", "note": None}


def test_赤のときは断りの3つと_人が読む出力に検査の呼び名と注と記録(env, capsys):
    chk = [{"check": "required", "id": "j_25", "fields": ["url"], "note": "出典の URL が要る（J-25）"}]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": "a"}, {"claim_id": "b", "url": "https://x"}])
    add(capsys, root, "red")
    rc_, out, err = run(capsys, "register", "check", "red")
    assert rc_ == 2
    assert "j_25" in err and "出典の URL が要る（J-25）" in err and "claim:a" in err and "url" in err


def test_違反の記録は1つの検査につき50件まで_残りは数だけ(env):
    chk = [{"check": "required", "id": "t", "fields": ["title"]}]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": f"c{i}"} for i in range(120)])
    r = result(root, "t")
    assert r["violations"] == 120 and len(r["records"]) == 50 and r["more"] == 70
    assert set(r["records"][0]) >= {"kind", "record", "check", "reason"}


# ================================================================== 宣言の攻め

_ATTACKS = [
    None, 1, "x", [], {}, True,
    {"check": "nope", "id": "a"},
    {"check": "required", "id": "a", "fields": ["t"], "unknown": 1},
    {"check": "required", "id": "A-1", "fields": ["t"]},
    {"check": "required", "id": "a", "fields": ["t"], "level": "fatal"},
    {"check": "required", "id": "a", "fields": []},
    {"check": "required", "id": "a", "fields": "t"},
    {"check": "required", "id": "a", "fields": [None]},
    {"check": "required", "id": "a", "fields": [["t"]]},
    {"check": "required", "id": "a", "fields": ["t"] * 2},
    {"check": "required", "id": "a", "fields": [f"f{i}" for i in range(17)]},
    {"check": "required", "id": "a", "fields": [f"f{i}" for i in range(100000)]},
    {"check": "required", "id": "a", "fields": ["$(rm -rf /)"]},
    {"check": "required", "id": "a", "fields": ["t"], "note": 5},
    {"check": "required", "id": "a", "fields": ["t"], "when": {}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": "x"}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": "x"}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": []}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": [1.5]}}},  # 段 4 §9: 整数は語に書ける
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": [["x"]]}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": [str(i) for i in range(1025)]}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": ["x"], "not_in": ["y"]}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"regex": "^.*$"}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"blank": "yes"}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s.t.u.v.w": {"blank": True}}},
    {"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": ["x" * 257]}}},
    {"check": "unique", "id": "a", "fields": ["t"], "fold": "upper"},
    {"check": "unique", "id": "a", "fields": ["t"], "skip_blank": 0},
    {"check": "one_of", "id": "a", "fields": ["t"]},
    {"check": "one_of", "id": "a", "fields": ["t", "u"], "mode": "any"},
    {"check": "words", "id": "a", "field": "t"},
    {"check": "words", "id": "a", "field": "t", "in": {"x": 1}},
    {"check": "words", "id": "a", "field": ["t"], "in": ["x"]},
    {"check": "shape", "id": "a", "field": "t"},
    {"check": "shape", "id": "a", "field": "t", "shape": "^[a-z]+$"},
    {"check": "shape", "id": "a", "field": "t", "shape": "email"},
    {"check": "shape", "id": "a", "field": "t", "prefix": ""},
    {"check": "shape", "id": "a", "field": "t", "prefix": "x" * 41},
    {"check": "shape", "id": "a", "field": "t", "prefix": "a\nb"},
    {"check": "shape", "id": "a", "field": "t", "prefix": 1},
    {"check": "forbid_pair", "id": "a", "via": "title", "target": {"s": {"blank": True}}},
    {"check": "forbid_pair", "id": "a", "via": "claim_id"},
    {"check": "forbid_pair", "id": "a", "via": "claim_id", "target": {"s": {"blank": True}}, "depth": 2},
    {"check": "count", "id": "a", "from": {"units": True}},
    {"check": "count", "id": "a", "from": {"units": 1}, "min": 1},
    {"check": "count", "id": "a", "from": {"kind": "evidence"}, "min": 1},
    {"check": "count", "id": "a", "from": {"kind": "evidence", "via": "claim_id", "then": "x"}, "min": 1},
    {"check": "count", "id": "a", "from": {"kind": "Evidence!", "via": "claim_id"}, "min": 1},
    {"check": "count", "id": "a", "from": {"units": True}, "min": -1},
    {"check": "count", "id": "a", "from": {"units": True}, "min": True},
    {"check": "count", "id": "a", "from": {"units": True}, "min": 1.5},
    {"check": "count", "id": "a", "from": {"units": True}, "min": 10 ** 100},
    {"check": "count", "id": "a", "from": {"units": True}, "min": 3, "max": 2},
    {"check": "count", "id": "a", "from": {"units": True}, "min": 1, "distinct": {"x": 1}},
    {"check": "judged", "id": "a"},
    {"check": "judged", "id": "a", "question": "says so"},
    {"check": "judged", "id": "a", "question": "says_so", "min": 0},
    {"check": "judged", "id": "a", "question": "says_so", "how": ["x"]},
    {"check": "judged", "id": "a", "question": "says_so", "how": "x" * 65},
]


@pytest.mark.parametrize("attack", _ATTACKS, ids=[str(i) for i in range(len(_ATTACKS))])
def test_宣言の攻め_どの値でもDeclarationErrorのchecksで例外が漏れない(attack):
    raw = dict(EVIDENCE_KIND, checks=[attack])
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(raw, ".thth/kinds/evidence.json")
    assert info.value.word == "checks"


@pytest.mark.parametrize("checks", [
    "x", {"check": "required"}, [{"check": "required", "id": "a", "fields": ["t"]}] * 2,
    [{"check": "required", "id": f"c{i}", "fields": ["t"]} for i in range(65)],
    [{"check": "required", "id": "a", "fields": ["t"], "when": {"s": {"in": ["x"]}}, "note": "x" * 3000}],
])
def test_宣言の攻め_並びの形と数と重なる呼び名(checks):
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(dict(EVIDENCE_KIND, checks=checks), ".thth/kinds/evidence.json")
    assert info.value.word == "checks"


def test_宣言の攻め_深い入れ子は宣言の読みで止まり例外を漏らさない(env, capsys):
    deep = {"s": {"blank": True}}
    for _ in range(200):
        deep = {"s": {"in": [deep]}}
    root = make_ledger(env["tmp"] / "ledger", [dict(CLAIM_KIND, checks=[
        {"check": "required", "id": "a", "fields": ["t"], "when": deep}])], {"claims.csv": "claim_id\n"})
    add(capsys, root, "deep")
    rc_, value, _ = jrun(capsys, "register", "check", "deep")
    assert rc_ == 2 and value["code"] == "register_kind_invalid"


def test_宣言の攻め_countのfromが無い種類か_この種類を指さない欄なら宣言の断り(env, capsys):
    for src in ({"kind": "ghost", "via": "claim_id"}, {"kind": "evidence", "via": "source_id"}):
        root = claims_ledger(env["tmp"] / src["via"] / src["kind"],
                             [{"check": "count", "id": "n", "from": src, "min": 1}], [{"claim_id": "a"}],
                             evidence=[])
        with pytest.raises(V.RegisterRefused) as info:
            V.open_vessel(str(root))
        assert info.value.code == "register_kind_invalid" and info.value.extra["word"] == "checks"
        assert info.value.extra["declaration"] == ".thth/kinds/claim.json"


def test_宣言の攻め_受ける最大の形は通る():
    big = [{"check": "required", "id": f"c{i}", "fields": [f"f{j}" for j in range(16)],
            "when": {f"w{j}": {"in": [f"x{k}" for k in range(256)]} for j in range(16)}} for i in range(64)]
    kind = V.parse_kind(dict(EVIDENCE_KIND, checks=big), ".thth/kinds/evidence.json")
    assert len(kind.checks) == 64


def test_記録の値がどんな形でも検査は例外を漏らさない(env):
    page = {"schema": "thth.kind/1", "kind": "page", "place": "pages/*.json", "format": "json",
            "checks": [{"check": c, "id": f"x_{c}", **args} for c, args in [
                ("unique", {"fields": ["v"], "fold": "lower"}), ("required", {"fields": ["v"]}),
                ("empty", {"fields": ["v"]}), ("one_of", {"fields": ["v", "w"]}),
                ("words", {"field": "v", "in": ["a"]}), ("shape", {"field": "v", "shape": "date"}),
                ("count", {"from": {"units": True}, "min": 1, "distinct": "v"})]]}
    values = [None, 1, 1.5, True, [], [1, [2]], {"a": {"b": None}}, "", " ", "a", "　", float("1e308")]
    root = make_ledger(env["tmp"] / "ledger", [page],
                       {f"pages/p{i}.json": json.dumps({"v": v, "w": v}) for i, v in enumerate(values)})
    out = C.run(V.open_vessel(str(root)), [])
    assert out["failed"] == 0 and out["total"] == 7


# ================================================================== 手間（記録の数に比例）

def test_主張6000と根拠7000の表でcheckが数秒で終わる(env, capsys):
    claims = [{"claim_id": f"c{i}", "status": "supported" if i % 2 else "draft", "url": f"https://x/{i}",
               "doi": f"10.1/{i}", "slug": f"s-{i}", "date": "2026-11-14", "lang": "ja"} for i in range(6114)]
    evidence = [{"evidence_id": f"e{i}", "claim_id": f"c{i % 6114}", "strength": "weak" if i % 3 else "strong",
                 "source_id": f"s{i % 500}"} for i in range(6995)]
    chk = [{"check": "unique", "id": "doi", "fields": ["doi"], "fold": "lower"},
           {"check": "unique", "id": "slug", "fields": ["slug", "lang"]},
           {"check": "required", "id": "url", "fields": ["url"]},
           {"check": "words", "id": "status", "field": "status", "in": ["draft", "supported", "established"]},
           {"check": "shape", "id": "date", "field": "date", "shape": "date"},
           {"check": "one_of", "id": "url_or_doi", "fields": ["url", "doi"]},
           {"check": "count", "id": "j_19", "level": "warn", "from": {"kind": "evidence", "via": "claim_id"},
            "min": 1},
           {"check": "count", "id": "j_20", "level": "warn", "from": {"kind": "evidence", "via": "claim_id"},
            "distinct": "source_id", "min": 1},
           {"check": "count", "id": "j_22", "level": "warn", "when": {"status": {"in": ["supported"]}},
            "from": {"kind": "evidence", "via": "claim_id"}, "where": {"strength": {"not_in": ["weak"]}},
            "min": 1}]
    ev_kind = with_checks(EVIDENCE_KIND, [
        {"check": "forbid_pair", "id": "j_11", "level": "warn", "via": "claim_id",
         "target": {"status": {"in": ["rejected"]}}}])
    root = make_ledger(env["tmp"] / "ledger", [with_checks(CLAIM_KIND, chk), ev_kind, SOURCE_KIND], {
        "claims.csv": csv_text(CLAIM_HEADER, claims),
        "evidence.csv": csv_text(["evidence_id", "claim_id", "strength", "source_id"], evidence),
        "sources.csv": csv_text(["source_id", "kind", "retracted"], [{"source_id": f"s{i}"} for i in range(500)])})
    add(capsys, root, "big")
    start = time.monotonic()
    rc_, value, _ = jrun(capsys, "register", "check", "big")
    took = time.monotonic() - start
    assert rc_ == 0, value
    assert value["records"] == {"claim": 6114, "evidence": 6995, "source": 500}
    j22 = next(c for c in value["checks"]["list"] if c["id"] == "j_22")
    assert j22["checked"] == 3057 and j22["violations"] > 0
    assert took < 10, took


# ================================================================== YAML の部品が無い機械

def test_YAMLの部品が無い機械では検査も中は見ていないと数えて出す(env, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)
    kind = {"schema": "thth.kind/1", "kind": "page", "place": "pages/*.yaml", "format": "yaml",
            "checks": [{"check": "required", "id": "title", "fields": ["title"]}]}
    root = make_ledger(env["tmp"] / "ledger", [kind], {"pages/a.yaml": "title: A\n", "pages/b.yaml": "x: 1\n"})
    add(capsys, root, "noyaml")
    rc_, value, _ = jrun(capsys, "register", "check", "noyaml")
    assert rc_ == 0 and value["not_inspected"] == 2
    title = value["checks"]["list"][0]
    assert title["not_inspected"] == 2 and title["checked"] == 0 and title["violations"] == 0


# ================================================================== 段 4 §6 の直し: G5・G9・G10・G11

def test_G5_語は1024個まで受け_1025個は断る():
    words = [f"w{i}" for i in range(1024)]
    kind = V.parse_kind(dict(EVIDENCE_KIND, checks=[
        {"check": "words", "id": "a", "field": "strength", "in": words},
        {"check": "required", "id": "b", "fields": ["strength"], "when": {"strength": {"not_in": words}}}]),
        ".thth/kinds/evidence.json")
    assert len(kind.checks) == 2
    for chk in ({"check": "words", "id": "a", "field": "strength", "in": words + ["x"]},
                {"check": "required", "id": "b", "fields": ["s"], "when": {"s": {"in": words + ["x"]}}}):
        with pytest.raises(V.DeclarationError) as info:
            V.parse_kind(dict(EVIDENCE_KIND, checks=[chk]), ".thth/kinds/evidence.json")
        assert info.value.word == "checks"


def test_G9_or_blankでこの語か空(env):
    chk = [{"check": "required", "id": "k_a2", "when": {"status": {"in": ["draft"], "or_blank": True}},
            "fields": ["title"]},
           {"check": "required", "id": "k_a6", "when": {"status": {"not_in": ["draft"], "or_blank": True}},
            "fields": ["title"]},
           {"check": "required", "id": "plain", "when": {"status": {"in": ["draft"]}}, "fields": ["title"]}]
    rows = [{"claim_id": "d", "status": "draft"}, {"claim_id": "blank", "status": ""},  # 段 4 §9: 空白だけは空でない
            {"claim_id": "e", "status": "established"}]
    root = claims_ledger(env["tmp"], chk, rows)
    assert bad_refs(root, "k_a2") == ["claim:blank", "claim:d"]
    assert bad_refs(root, "k_a6") == ["claim:blank", "claim:e"]
    assert bad_refs(root, "plain") == ["claim:d"]


@pytest.mark.parametrize("test", [{"or_blank": True}, {"blank": True, "or_blank": True},
                                  {"in": ["x"], "or_blank": 1}, {"in": ["x"], "or_blank": None},
                                  {"in": ["x"], "or_blank": "true"}, {"in": ["x"], "not_in": ["y"], "or_blank": True}])
def test_G9_宣言の攻め_or_blank(test):
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(dict(EVIDENCE_KIND, checks=[{"check": "required", "id": "a", "fields": ["s"],
                                                  "when": {"s": test}}]), ".thth/kinds/evidence.json")
    assert info.value.word == "checks"


def test_G10_columns_要る欄_ほかを許さない_あってはならない欄_表(env):
    chk = [{"check": "columns", "id": "need", "required": ["claim_id", "status", "missing_col"]},
           {"check": "columns", "id": "closed", "allowed": ["claim_id", "status", "title"]},
           {"check": "columns", "id": "never", "forbidden": ["doi", "nope"]},
           {"check": "columns", "id": "fine", "required": ["claim_id"], "allowed": CLAIM_HEADER, "forbidden": ["x"]}]
    root = claims_ledger(env["tmp"], chk, [{"claim_id": "a"}])
    r = {c["id"]: c for c in C.run(V.open_vessel(str(root)), [])["list"]}
    assert [(x["record"], x["reason"], x["fields"]) for x in r["need"]["records"]] == [
        ("claim:a", "column_missing", ["missing_col"])]
    assert r["closed"]["records"][0]["reason"] == "column_not_allowed"
    assert r["closed"]["records"][0]["fields"] == ["date", "doi", "lang", "reason", "slug", "url"]
    assert [(x["reason"], x["fields"]) for x in r["never"]["records"]] == [("column_forbidden", ["doi"])]
    assert r["fine"]["violations"] == 0


def test_G10_columns_JSONの枚は頭の段の鍵で_幅の足りない行の欄も在ると数える(env):
    page = {"schema": "thth.kind/1", "kind": "page", "place": "pages/*.json", "format": "json",
            "checks": [{"check": "columns", "id": "keys", "required": ["title"], "allowed": ["title", "body"]}]}
    root = make_ledger(env["tmp"] / "ledger", [page], {"pages/a.json": json.dumps({"title": "", "body": "x"}),
                                                       "pages/b.json": json.dumps({"body": "x", "extra": 1})})
    assert set(bad_refs(root, "keys")) == {"page:b"}
    root2 = claims_ledger(env["tmp"] / "t2", [{"check": "columns", "id": "w", "required": ["reason"]}], [])
    (root2 / "claims.csv").write_text(",".join(CLAIM_HEADER) + "\nc1,draft\n", encoding="utf-8")
    assert result(root2, "w")["violations"] == 0


@pytest.mark.parametrize("chk", [
    {"check": "columns", "id": "a"},
    {"check": "columns", "id": "a", "required": []},
    {"check": "columns", "id": "a", "required": "x"},
    {"check": "columns", "id": "a", "allowed": [None]},
    {"check": "columns", "id": "a", "forbidden": [f"f{i}" for i in range(1025)]},
    {"check": "columns", "id": "a", "required": ["x"], "forbidden": ["x"]},
    {"check": "columns", "id": "a", "required": ["x"], "allowed": ["y"]},
    {"check": "columns", "id": "a", "required": ["a.b"]},
    {"check": "columns", "id": "a", "required": ["x"], "fields": ["x"]},
])
def test_G10_宣言の攻め_columns(chk):
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(dict(EVIDENCE_KIND, checks=[chk]), ".thth/kinds/evidence.json")
    assert info.value.word == "checks"


def test_G11_prefixed_words_頭ごとに語を替える_nullならslugの形だけ(env):
    chk = [{"check": "prefixed_words", "id": "j_7", "field": "slug", "sep": ":",
            "prefixes": {"pillar": ["color", "dye"], "topic": None}}]
    rows = [{"claim_id": "ok1", "slug": "pillar:color"}, {"claim_id": "ok2", "slug": "topic:indigo-blue"},
            {"claim_id": "ok3", "slug": ""},
            {"claim_id": "bad_word", "slug": "pillar:red"}, {"claim_id": "bad_shape", "slug": "topic:Indigo"},
            {"claim_id": "bad_head", "slug": "class:x"}, {"claim_id": "no_sep", "slug": "pillar"},
            {"claim_id": "empty_tail", "slug": "topic:"}]
    root = claims_ledger(env["tmp"], chk, rows)
    r = result(root, "j_7")
    assert {(x["record"], x["reason"]) for x in r["records"]} == {
        ("claim:bad_word", "word_not_allowed"), ("claim:bad_shape", "shape"), ("claim:bad_head", "prefix_not_allowed"),
        ("claim:no_sep", "no_prefix"), ("claim:empty_tail", "shape")}


@pytest.mark.parametrize("chk", [
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": "::", "prefixes": {"a": None}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": "", "prefixes": {"a": None}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": "\n", "prefixes": {"a": None}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": ["a"]},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {"a:b": None}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {"": None}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {"a": "x"}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {"a": []}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {"a": [1]}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {f"p{i}": None for i in range(65)}},
    {"check": "prefixed_words", "id": "a", "field": "t", "prefixes": {"a": None}},
    {"check": "prefixed_words", "id": "a", "sep": ":", "prefixes": {"a": None}},
    {"check": "prefixed_words", "id": "a", "field": "t", "sep": ":", "prefixes": {"a": None}, "shape": "slug"},
])
def test_G11_宣言の攻め_prefixed_words(chk):
    with pytest.raises(V.DeclarationError) as info:
        V.parse_kind(dict(EVIDENCE_KIND, checks=[chk]), ".thth/kinds/evidence.json")
    assert info.value.word == "checks"
