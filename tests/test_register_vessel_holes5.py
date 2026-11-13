"""台帳の器 段 1 — Codex の五回めの検収（`~/Documents/THTH/codex/答え_段1の五回め_2026-11-14.md`）の穴と、
同じ型の穴（再帰で深さに弱い所・たどった先の問題を見ない所）を突く試験。

穴ごとに、直す前に赤を見てから直した。
1. たどった先の記録に読みの問題（表の幅の違う行の欠けたセル）があっても、検収の記録の明示の null と合って判断になる
2. 同じ鍵の行を足すだけで、前からある行の指し先が k: から kc: に替わり、判断が消え、結びの候補も空になる
3. 参照の鎖が長いと、輪の検査が再帰で RecursionError になる
"""
from __future__ import annotations

import json
import pathlib

from thth.register import judgment as J, vessel as V, vessel_cli
from tests.test_register_vessel import env  # noqa: F401
from tests.test_register_vessel_holes3 import _ledger
from tests.test_register_vessel_holes4 import _one, _tiny, _write

AT = "2026-11-14"
NOTE = ("statement", "note")


def _csv_claim(root: pathlib.Path, text: str) -> None:
    """主張の種類を表（CSV）に替える。"""
    kinds = root / ".thth" / "kinds"
    (kinds / "claim.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "claim", "place": "claim.csv", "format": "csv", "id": {"field": "id"},
        "questions": [{"name": "says_so", "state": "status", "depends": list(NOTE)}]}), encoding="utf-8")
    (root / "claim.csv").write_text(text, encoding="utf-8")


def _csv_evidence(root: pathlib.Path, text: str) -> None:
    kinds = root / ".thth" / "kinds"
    (kinds / "evidence.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "evidence", "place": "evidence.csv", "format": "csv",
        "id": {"field": "id"}, "pointers": [{"field": "claim_id", "meaning": "supports", "to": "claim"}]}),
        encoding="utf-8")
    (root / "evidence.csv").write_text(text, encoding="utf-8")


INSP_NULL = {"i": {"evidence_ids": ["e"], "statement": "hello", "note": None, "status": "supported"}}


# ================================================================== 穴 1

def test_五1_たどった先の記録に読みの問題があれば判断にしない(env):
    root = _ledger(env, insp=INSP_NULL, evidence={"e": {"claim_id": "c"}}, claim={}, depends=NOTE)
    _csv_claim(root, "id,statement,status,note\nc,hello,supported\n")
    v = V.open_vessel(str(root))
    assert v.get("claim:c").problems == ["row_width"]
    report = J.inspection_report(v)
    assert report["judgments"] == [] and report["skipped"] == [{"source": "insp:i", "reason": "target_problem"}]
    assert J.answer_record(v, report["judgments"], "claim:c", "says_so")["conclusion"] == "pending"


def test_五1_途中でたどった記録に読みの問題があっても判断にしない(env):
    root = _ledger(env, insp=INSP_NULL, evidence={}, claim={"c": {"statement": "hello", "note": None,
                                                                  "status": "supported"}}, depends=NOTE)
    _csv_evidence(root, "id,claim_id,memo\ne,c\n")
    v = V.open_vessel(str(root))
    assert v.get("evidence:e").problems == ["row_width"]
    report = J.inspection_report(v)
    assert report["judgments"] == [] and report["skipped"] == [{"source": "insp:i", "reason": "target_problem"}]


def test_五1_欠けたセルと読んだnullは依る所で区別する(env):
    root = _ledger(env, insp={}, evidence={}, claim={}, depends=NOTE)
    _csv_claim(root, "id,statement,status,note\nc,hello,supported\n")
    v = V.open_vessel(str(root))
    content = {"statement": "hello", "note": None, "status": "supported"}
    j = J.make(v, {"record": "claim:c"}, "says_so", by="central", at=None, word="supported", content=content)
    assert J.evaluate(v, j) == "stale"
    assert J.answer_record(v, [j], "claim:c", "says_so")["conclusion"] == "pending"
    assert "note" not in v.get("claim:c").values  # 欠けたセルは値として読まない（null と取り違えない）


def test_五1_表の行の明示の空のセルは読んだ値で問題もない(env):
    root = _ledger(env, insp={"i": {"evidence_ids": ["e"], "statement": "hello", "note": "",
                                    "status": "supported"}}, evidence={"e": {"claim_id": "c"}}, claim={},
                   depends=NOTE)
    _csv_claim(root, "id,statement,status,note\nc,hello,supported,\n")
    v = V.open_vessel(str(root))
    [j] = J.inspections(v)
    assert J.evaluate(v, j) == "effective"


# ================================================================== 穴 2

def _add_same_key(env, extra: dict):
    root = _tiny(env, [{"year": 2026, "rows": [{"key": "a", "value": 10}]}], not_values=["key"])
    v0 = V.open_vessel(str(root))
    unit = _one(v0)
    assert unit.pointer.startswith("k:")
    j = J.make(v0, {"record": "t:a", "unit": unit.pointer, "value": "value"}, "reads_right", by="a", at=AT,
               conclusion="good")
    _write(root, [{"year": 2026, "rows": [{"key": "a", "value": 10}, extra]}])
    return V.open_vessel(str(root)), j


def test_五2_同じ鍵の行を足しても前の行は候補に必ず出る(env):
    v1, j = _add_same_key(env, {"key": "a", "value": 20})
    assert J.evaluate(v1, j) == "vanished"  # 鍵で決まらなくなった行を黙って結ばない（決まりどおり）
    [cand] = J.candidates(v1, [j])
    rec = v1.get("t:a")
    old = next(u.pointer for u in rec.units if u.item["value"] == 10)
    new = next(u.pointer for u in rec.units if u.item["value"] == 20)
    assert sorted(cand["appeared"]) == sorted([old, new])
    assert cand["same_depend"] == [old]  # 前の依る所と同じ中身の物を名指しで見せる


def test_五2_完全な複製を足しても同じ中身なので判断はそのまま効く(env):
    v1, j = _add_same_key(env, {"key": "a", "value": 10})
    rec = v1.get("t:a")
    assert {u.pointer for u in rec.units} == {j["target"]["unit"]} and rec.problems == []
    assert J.evaluate(v1, j) == "effective"
    assert J.answer_value(v1, [j], "t:a", j["target"]["unit"], "value")["usable"] is True


# ================================================================== 穴 3

def _chain(env, text: str) -> pathlib.Path:
    root = env["tmp"] / "chain"
    (root / ".thth" / "kinds").mkdir(parents=True)
    (root / ".thth" / "kinds" / "n.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "n", "place": "n.csv", "format": "csv", "id": {"field": "id"},
        "pointers": [{"field": "parent", "meaning": "parent", "to": "n"}]}), encoding="utf-8")
    (root / "n.csv").write_text(text, encoding="utf-8")
    return root


def test_五3_長い参照の鎖でも輪の検査は終わる(env):
    text = "id,parent\n" + "".join(f"n{i:04},n{i + 1:04}\n" for i in range(1100)) + "n1100,\n"
    root = _chain(env, text)
    graph = V.links(V.open_vessel(str(root)))
    assert len(graph["edges"]) == 1100 and graph["cycles"] == [] and graph["dangling"] == []
    out = vessel_cli.check(str(root))
    assert out["links"]["cycles"] == []


def test_五3_長い輪も1つの輪として見つける(env):
    n = 1500
    text = "id,parent\n" + "".join(f"n{i:04},n{(i + 1) % n:04}\n" for i in range(n))
    graph = V.links(V.open_vessel(str(_chain(env, text))))
    [cycle] = graph["cycles"]
    assert cycle["meaning"] == "parent" and len(cycle["records"]) == n + 1
    assert cycle["records"][0] == cycle["records"][-1] and len(set(cycle["records"])) == n
