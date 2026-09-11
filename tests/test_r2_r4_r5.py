"""Independent R2/R4/R5 acceptance boundaries for d9bec3b."""
import json
from pathlib import Path

import pytest

from tests.test_editorial_audit import _improvements
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import _register_spec
from tests.test_hypotheses import _hypothesis, _prediction, _register
from tests.test_review_cli import _json, _register_vocabulary, _thth


# 外部レビューは repo を `<対象>/repo/` へ展開して検証するので、この行は
# そちらの置き方を前提にしていた。**こちらの repo では 1 つ上がそのまま repo。**
# **中身の前提（何を assert するか）は 1 文字も変えていない。**
REPO = Path(__file__).parents[1]
if not (REPO / "docs").is_dir():
    REPO = Path(__file__).parents[1] / "repo"


def test_r2_trial_only_middle_series_closes_transitive_component(isolated_account):
    """A production -- trial-only -- production identity chain is one case."""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production", case_id="case-a",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="trial", case_id="bridge-x", recheck_of=first)
    record(vocabulary_id, "c" * 64, form_spec_id=spec_id,
           provenance="trial", case_id="bridge-x")
    record(vocabulary_id, "c" * 64, form_spec_id=spec_id,
           provenance="trial", case_id="bridge-y")
    record(vocabulary_id, "d" * 64, form_spec_id=spec_id,
           provenance="production", case_id="bridge-y")

    out = _improvements(spec_id)
    print("AUDIT_R2_TRANSITIVE_TRIAL_COMPONENT", out)
    assert out["candidates"] == []
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1
    cross = {c.get("case_id"): c for c in item["case_id_conflicts"]
             if c.get("case_id")}
    assert cross["bridge-x"]["series"] == sorted(["a" * 64, "c" * 64])
    assert cross["bridge-y"]["series"] == sorted(["c" * 64, "d" * 64])


def test_r2_trial_only_component_does_not_add_a_case(isolated_account):
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="production-case")
    record(vocabulary_id, "e" * 64, form_spec_id=spec_id,
           provenance="trial", case_id="trial-only")
    out = _improvements(spec_id)
    print("AUDIT_R2_TRIAL_ONLY_NOT_COUNTED", out)
    assert out["candidates"] == []
    assert out["not_enough_cases"][0]["independent_cases"] == 1


@pytest.mark.parametrize("prediction", [
    _prediction(window=None, scope=None),
    _prediction(statement="非フォロワー由来viewsだけが増える",
                metrics=["views"], scope="非フォロワー由来のみ"),
    _prediction(statement="外回り返信回数が多いほど自投稿viewsが増える",
                metrics=["views", "replies"]),
])
def test_r4_shadow_is_rejected_and_not_stored(thth_root, prediction):
    proc = _register(_hypothesis(state="shadow", predictions=[prediction]))
    print("AUDIT_R4_SHADOW", proc.returncode, proc.stdout)
    assert proc.returncode == 2
    assert _json(proc)["error"]["code"] == "promotion_not_implemented"
    assert _json(_thth(["topics", "hypotheses", "--state", "shadow"]))["count"] == 0


def test_r4_same_freeform_remains_storable_as_proposed(thth_root):
    prediction = _prediction(window=None, scope=None)
    proc = _register(_hypothesis(state="proposed", predictions=[prediction]))
    print("AUDIT_R4_PROPOSED", proc.returncode, proc.stdout)
    assert proc.returncode == 0
    out = _json(proc)
    assert out["state"] == "proposed"
    assert "申告値" in out["declaration_notice"]


def test_r5_migrated_unknown_days_keep_month_and_kind():
    rows = {r["code"]: r for r in json.loads(
        (REPO / "docs/仮説_露出と反応率_v1.json").read_text())}
    actual = {
        "H05/E3": next(s for s in rows["H05"]["sources"] if s["ref"] == "E3"),
        "H08/E3": next(s for s in rows["H08"]["sources"] if s["ref"] == "E3"),
        "H09/Reddit": rows["H09"]["sources"][0],
    }
    print("AUDIT_R5_MIGRATED_MONTHS", actual)
    for key in ("H05/E3", "H08/E3"):
        assert actual[key]["date"] == "2026-04"
        assert actual[key]["date_precision"] == "month"
        assert actual[key]["date_kind"] == "observed_period"
    assert actual["H09/Reddit"]["date"] == "2025-03"
    assert actual["H09/Reddit"]["date_precision"] == "month"
    assert actual["H09/Reddit"]["date_kind"] == "published_at"


def test_r5_updated_date_kind_is_representable_and_o1_is_migrated_faithfully(
        thth_root):
    """The source report labels O1's 2025-03-07 value as an update date."""
    rows = {r["code"]: r for r in json.loads(
        (REPO / "docs/仮説_露出と反応率_v1.json").read_text())}
    o1 = rows["H01"]["sources"][0]
    print("AUDIT_R5_O1_KIND", o1)
    payload = _hypothesis(sources=[{
        "tier": "L1", "ref": "O1", "date": "2025-03-07",
        "date_precision": "day", "date_kind": "updated_at",
        "note": "source report says updated 2025-03-07",
    }])
    proc = _register(payload)
    print("AUDIT_R5_UPDATED_AT_REGISTRATION", proc.returncode, proc.stdout)
    assert o1["date_kind"] == "updated_at"
    assert proc.returncode == 0
