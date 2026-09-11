"""外部レビュー（Codex 再判定 M2・2026-09-12）を、自分のテストとしても固定する

（`tests/test_review_form_spec_and_case_id.py` の書き方に合わせる）。

- M2: 別々の非接続系列が同じ `case_id` を名乗っても、独立事例には
  1 件としてしか数えない（`thth/topic_models.py` の `_case_id_clusters()`）。
  N4（同じ系列に別々の `case_id`）の**逆向き**。
- 止めすぎていないことの対照実験: 違う `case_id` の非接続系列は、
  これまでどおり独立事例として数える。
- 畳んでも申告は消さない: 両向きの競合が `case_id_conflicts` に出る。
"""
from __future__ import annotations

from tests.test_editorial_audit import _improvements
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import _register_spec
from tests.test_review_cli import _register_vocabulary


def test_同じcase_idの非接続2件は独立2件にならない(isolated_account):
    """M2 本体: 同じ `case_id` を名乗る非接続 2 系列は 1 件に畳む。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="same-case")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="same-case")

    out = _improvements(spec_id)
    print("M2_SAME_CASE_ID_UNLINKED", out)
    assert out["candidates"] == [], (
        "同じ case_id を名乗るだけの非接続系列が独立 2 件に昇格している")
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1


def test_違うcase_idの非接続2件はこれまでどおり独立2件になる(isolated_account):
    """止めすぎていないことの対照実験。違う `case_id` なら畳まない。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("M2_DIFFERENT_CASE_ID_STILL_INDEPENDENT", out)
    assert len(out["candidates"]) == 1
    candidate = out["candidates"][0]
    assert candidate["independent_cases"] == 2
    assert candidate["case_id_conflicts"] == []


def test_同じcase_idを名乗る非接続系列の競合が両向きに出る(isolated_account):
    """畳んでも申告は消さない。`case_id_conflicts` に `case_id` → 複数系列で出る。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="same-case",
           account="nigamilab-threads")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="same-case",
           account="nigamilab-threads")

    out = _improvements(spec_id)
    print("M2_CROSS_SERIES_CONFLICT_VISIBLE", out)
    item = out["not_enough_cases"][0]
    conflicts = item["case_id_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["case_id"] == "same-case"
    assert conflicts[0]["series"] == sorted(["a" * 64, "b" * 64])
    # 系列 1 件へ畳んだ理由が読める（`why_not_yet` に説明が出る）。
    assert "case_id_conflicts" in item["why_not_yet"]
