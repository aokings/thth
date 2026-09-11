"""Independent M2 boundaries for commit 509e9be."""
from tests.test_editorial_audit import _improvements
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import _register_spec
from tests.test_review_cli import _register_vocabulary


def _setup():
    return _register_vocabulary(), _register_spec()


def test_production_case_ids_form_a_transitive_cluster(isolated_account):
    """A-X, X-Y, Y-C must be one component, not three cases."""
    vocabulary_id, spec_id = _setup()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="bridge-x")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="bridge-x")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="bridge-y")
    record(vocabulary_id, "c" * 64, form_spec_id=spec_id,
           provenance="production", case_id="bridge-y")
    record(vocabulary_id, "c" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-c")

    out = _improvements(spec_id)
    print("AUDIT_M2_PRODUCTION_TRANSITIVE", out)
    assert out["candidates"] == []
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    assert {c.get("case_id") for c in item["case_id_conflicts"]} >= {
        "bridge-x", "bridge-y"
    }


def test_trial_id_bridge_constrains_independence_and_is_cross_series_visible(
        isolated_account):
    """A trial ID is no positive case evidence, but is contrary identity evidence.

    Production series A reports case-a.  A trial recheck in that known series
    reports shared-case.  Disconnected production series B also reports
    shared-case.  Those records contradict the claim that A and B are two
    independent cases, so the trial claim must constrain counting even though
    it must not add a case to the threshold.
    """
    vocabulary_id, spec_id = _setup()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production", case_id="case-a",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="trial", case_id="shared-case", recheck_of=first)
    record(
        vocabulary_id, "c" * 64, form_spec_id=spec_id,
        provenance="production", case_id="shared-case")

    out = _improvements(spec_id)
    print("AUDIT_M2_TRIAL_BRIDGE", out)
    assert out["candidates"] == [], (
        "trial case_id must not add evidence, but must constrain two production "
        "series that claim the same identity")
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    cross = [c for c in item["case_id_conflicts"]
             if c.get("case_id") == "shared-case"]
    assert len(cross) == 1
    assert cross[0]["series"] == sorted(["a" * 64, "c" * 64])


def test_same_series_different_ids_and_disconnected_same_id_both_visible(
        isolated_account):
    """Both conflict directions remain visible in one result."""
    vocabulary_id, spec_id = _setup()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production", case_id="case-a",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="production", case_id="shared", recheck_of=first)
    record(
        vocabulary_id, "c" * 64, form_spec_id=spec_id,
        provenance="production", case_id="shared")

    out = _improvements(spec_id)
    print("AUDIT_M2_BOTH_DIRECTIONS", out)
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    assert {tuple(c.get("case_ids", ())) for c in item["case_id_conflicts"]} >= {
        ("case-a", "shared")
    }
    assert {c.get("case_id") for c in item["case_id_conflicts"]} >= {"shared"}

