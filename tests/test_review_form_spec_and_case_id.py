"""外部レビュー（Codex 再々判定・2026-09-12）で残った編集知識側の 2 件を、

自分のテストとしても固定する（`tests/test_review_provenance.py` の書き方に
合わせる）。

- N3: 解決できる `form_spec_id` でも、指した先の型がレビューの型と
  違うなら候補の母数に入れない（`thth/topic_models.py`
  `improvement_candidates()`）。
- N4: `recheck_of` で既知につながる系列に別々の `case_id` を申告しても、
  独立事例には系列 1 件としてしか数えない。競合は `case_id_conflicts` に
  出す。
- 表示の条件: 候補表示は「確認済み」ではなく「申告」だと `notice` で
  分かる。
"""
from __future__ import annotations

import copy

from tests.test_editorial_audit import _improvements
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import SPEC, _register_spec, _role
from tests.test_review_cli import _register_vocabulary


def test_別の型のform_spec_idは候補の母数に入らず理由が出る(isolated_account):
    """N3: 比較型のレビューに列挙型の `form_spec_id` を付けても通さない。"""
    vocabulary_id = _register_vocabulary()
    target_id = _register_spec()
    wrong = copy.deepcopy(SPEC)
    wrong["form"] = "列挙"
    wrong["scope"] = "列挙だけ"
    wrong["applies_when"] = ["数えられる項目を並べる"]
    wrong["roles"] = [_role("何を並べるか")]
    wrong_id = _register_spec(wrong)

    record(vocabulary_id, "a" * 64, form_spec_id=wrong_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=wrong_id,
           provenance="production", case_id="case-b")

    out = _improvements(target_id)
    print("N3_WRONG_FORM_SPEC", out)
    assert out["candidates"] == []
    assert len(out["unresolved_records"]) == 2
    # **理由が出る。** 「解決できない」ではなく「別の型」だと分かること。
    for row in out["unresolved_records"]:
        assert "別の型" in row["reason"]


def test_同じ型のform_spec_idならこれまでどおり数えられる(isolated_account):
    """止めすぎていないことの対照実験。同じ型を指せば普通に候補になる。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()

    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("SAME_FORM_SPEC_STILL_COUNTS", out)
    assert out["unresolved_records"] == []
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["independent_cases"] == 2


def test_recheck_ofで繋がった系列に別々のcase_idを付けても独立2件にならない(
        isolated_account):
    """N4: 既知の改訂・再検査系列より `case_id` の申告を優先しない。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production", case_id="claimed-case-a",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="production", case_id="claimed-case-b",
        recheck_of=first)

    out = _improvements(spec_id)
    print("N4_LINKED_SERIES_CONFLICT", out)
    assert out["candidates"] == [], (
        "既知の単一系列が、別々の case_id 申告だけで独立 2 件に昇格している")
    not_yet = out["not_enough_cases"][0]
    assert not_yet["independent_cases"] == 1
    # **競合は消さず出力に出す。**
    conflicts = not_yet["case_id_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["case_ids"] == ["claimed-case-a", "claimed-case-b"]


def test_系列が繋がっていなければ別々のcase_idはこれまでどおり独立2件になる(
        isolated_account):
    """止めすぎていないことの対照実験。系列を繋ぐ線が無ければ独立に数える。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("UNLINKED_SERIES_STILL_INDEPENDENT", out)
    assert len(out["candidates"]) == 1
    candidate = out["candidates"][0]
    assert candidate["independent_cases"] == 2
    assert candidate["case_id_conflicts"] == []


def test_候補表示に申告であることの但し書きが出る(isolated_account):
    """表示の条件: `case_id`・`provenance`・独立性は申告であって確認済みではない。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("SELF_REPORTED_DISCLOSED", out)
    assert out["candidates"], "この確認には候補が 1 件出ている必要がある"
    notice = out["notice"]
    assert "自己申告" in notice or "未検証" in notice
    # 「確認済み」を意味する名前は使わない。
    assert "confirmed_case_ids" not in str(out)
    assert "reported_case_ids" in out["candidates"][0]
