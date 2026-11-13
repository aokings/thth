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
