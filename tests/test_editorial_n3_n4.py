"""Independent N3/N4 boundary tests against fixed commit e045f73."""
from __future__ import annotations

from tests.test_editorial_audit import _improvements
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import _register_spec
from tests.test_review_cli import _register_vocabulary


def test_same_reported_case_on_unlinked_hashes_is_not_two_independent_cases(
        isolated_account):
    """The repeated case_id says one case even when the revision edge is missing."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="same-case")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="same-case")

    out = _improvements(spec_id)
    print("SAME_CASE_UNLINKED_HASHES", out)
    assert out["candidates"] == []
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    assert item["reported_case_ids"] == ["same-case"]


def test_trial_case_id_conflict_in_known_series_remains_visible(isolated_account):
    """Trial stays outside the threshold, but its conflicting claim must not disappear."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production", case_id="production-case",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="trial", case_id="trial-case", recheck_of=first)

    out = _improvements(spec_id)
    print("TRIAL_PRODUCTION_SERIES_CONFLICT", out)
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    assert item["provenance"] == {"production": 1, "trial": 1}
    assert item["case_id_conflicts"] == [{
        "series": "a" * 64,
        "case_ids": ["production-case", "trial-case"],
    }]


def test_reported_case_id_is_not_ambiguous_across_accounts(isolated_account):
    """Global improvements cannot count two cases while reporting one identifier."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="shared-id",
           account="nigamilab-threads")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="shared-id",
           account="other-threads")

    out = _improvements(spec_id)
    print("CROSS_ACCOUNT_CASE_ID_SCOPE", out)
    assert out["candidates"] == []
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    assert item["reported_case_ids"] == ["shared-id"]


def test_series_with_one_reported_id_is_not_also_unlinked(isolated_account):
    """A missing ID on one revision does not make the already identified series unlinked."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production", case_id="known-case",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="production", recheck_of=first)

    out = _improvements(spec_id)
    print("PARTIALLY_IDENTIFIED_SERIES", out)
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    assert item["reported_case_ids"] == ["known-case"]
    assert item["unlinked_versions"] == 0

