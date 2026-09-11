"""Independent counterexamples for editorial knowledge A, fixed at 5a7dd9c."""
from __future__ import annotations

import copy

from tests.test_external_acceptance import record
from tests.test_form_spec_cli import FORM, SPEC, _register_spec, _role
from tests.test_review_cli import (
    VOCABULARY,
    _finding,
    _json,
    _register_vocabulary,
    _review,
    _thth,
)


def _improvements(spec_id):
    proc = _thth(["topics", "improvements", "--form-spec", spec_id])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _json(proc)


def test_r1_same_semantics_survive_metadata_version_bump(isolated_account):
    """`meaning_version` が上がると、定義文が同じでも指紋は分かれる（保守的な挙動）。

    もとの反例は「版番号だけが変わった同一定義が 1 件の候補に合流するべき」
    という期待だった。だが `_meaning_fingerprint()` は `reason_id` に加えて
    `meaning_version` も指紋に含めている。**外部レビュー（Codex 再々判定）の
    裁定はこうだった**:

    > 意味指紋には実際には `meaning_version` も入る。定義が同じでも版番号
    > 変更で分かれるため、「版上げによる分断をなくした」という説明は訂正が
    > 必要。ただし保守的に分かれる挙動であり、今回の合格阻害項目にはしない。

    つまり**いまの「同じ定義でも版番号が違えば分かれる」挙動は受容されて
    いる**——合流させる実装変更はしない。ここでは分かれること自体を
    固定する（無理に合流させる直しが後から紛れ込んだら、この反例で気付く）。
    """
    first_id = _register_vocabulary()
    second = copy.deepcopy(VOCABULARY)
    second["name"] = "same semantics, next vocabulary version"
    second["supersedes"] = first_id
    second["entries"][0]["meaning_version"] = 2
    second_id = _register_vocabulary(second)
    spec_id = _register_spec()

    record(first_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(second_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("R1_SAME_SEMANTICS_VERSION_BUMP", out)
    # **保守的に分かれる。** 定義文は同一でも `meaning_version` が違うので、
    # 1 件の候補に合流せず、それぞれ独立事例 1 件（閾値未満）の
    # `not_enough_cases` に分かれて出る。
    assert out["candidates"] == []
    assert len(out["not_enough_cases"]) == 2
    assert {c["independent_cases"] for c in out["not_enough_cases"]} == {1}


def test_r1_different_definitions_with_same_version_do_not_merge(isolated_account):
    """Exercise the repaired branch with case IDs so R5 cannot make it vacuous."""
    first_id = _register_vocabulary()
    second = copy.deepcopy(VOCABULARY)
    second["name"] = "opposite semantics"
    second["entries"][0]["definition"] = "条件が多すぎるので削るべき"
    second_id = _register_vocabulary(second)
    spec_id = _register_spec()

    record(first_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(second_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("R1_DIFFERENT_MEANINGS", out)
    assert out["candidates"] == []


def test_r2_resolved_but_wrong_form_spec_is_not_candidate_evidence(isolated_account):
    """A resolvable spec for another form must not validate a review's claimed form."""
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
    print("R2_WRONG_FORM_SPEC", out)
    assert out["candidates"] == []
    assert len(out["unresolved_records"]) == 2


def test_r3_disjoint_carried_from_is_not_described_as_confirmed_carry(
        isolated_account, tmp_path):
    """carried_from is accepted without any shared finding or scope evidence."""
    vocabulary_id = _register_vocabulary()
    draft = tmp_path / "draft.md"
    draft.write_text("old", encoding="utf-8")
    old_proc = _thth(
        ["topics", "record-review", "--json-stdin", "--by", "auditor",
         "--draft", str(draft)],
        _review(vocabulary_id, findings=[_finding("missing_condition")]),
    )
    assert old_proc.returncode == 0, old_proc.stdout + old_proc.stderr
    old_id = _json(old_proc)["review_id"]

    draft.write_text("new", encoding="utf-8")
    new_proc = _thth(
        ["topics", "record-review", "--json-stdin", "--by", "auditor",
         "--draft", str(draft)],
        _review(vocabulary_id, carried_from=old_id,
                findings=[_finding("unsupported_claim")]),
    )
    assert new_proc.returncode == 0, new_proc.stdout + new_proc.stderr

    out = _json(_thth(["topics", "review", "nigamilab-threads",
                       "--draft", str(draft)]))
    print("R3_DISJOINT_CARRY", out)
    old = next(row for row in out["unreviewed_history"]
               if row["review_id"] == old_id)
    assert old.get("carried_by"), "raw carried_from link may remain visible"
    assert "申告" in old["note"] and "対応" in old["note"] and "未確認" in old["note"]


def test_r4_unverified_production_claim_is_disclosed(isolated_account):
    """A valid-looking hash and two strings are not measured production evidence."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="claimed-case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="claimed-case-b")

    out = _improvements(spec_id)
    print("R4_UNVERIFIED_PRODUCTION", out)
    assert out["candidates"], "this confirms the claim currently reaches the threshold"
    rendered = str(out)
    assert "自己申告" in rendered or "未検証" in rendered or "unverified" in rendered


def test_r5_known_one_series_cannot_become_two_cases_by_claim(
        isolated_account):
    """Known revision linkage is stronger evidence than two conflicting case labels."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    first = record(
        vocabulary_id,
        "a" * 64,
        form_spec_id=spec_id,
        provenance="production",
        case_id="claimed-case-a",
        disposition="fixed",
        revised_draft_sha256="b" * 64,
    )
    record(
        vocabulary_id,
        "b" * 64,
        form_spec_id=spec_id,
        provenance="production",
        case_id="claimed-case-b",
        recheck_of=first,
    )

    out = _improvements(spec_id)
    print("R5_LINKED_SERIES_CONFLICT", out)
    assert out["candidates"] == [], (
        "a known single revision series was promoted by two unchecked case_id strings"
    )
    assert out["not_enough_cases"][0]["independent_cases"] == 1
