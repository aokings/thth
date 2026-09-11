"""外部レビュー（Codex）再判定の再判定で残った 2 件（R2・R3）を閉じた確認。

`tests/test_rejudge_new.py`（外部レビューの反例そのもの）と
`tests/test_external_acceptance.py`（前回の反例）は変えない。ここは**その 2 つが
通ることを確かめたうえで**、修正条件をもう少し細かく確かめるための追加分。

- R2: 指定された型の仕様が読めないとき、`unresolved_records` に出て
  候補の母数から外れる。**未指定はこれまでどおり**（一律禁止にしない）。
- R3: 1 つの指摘を持ち越しても、同じ旧記録にある別の未確認指摘まで
  履歴から消えない。持ち越した先は `carried_by` で示す。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from thth import topic_store as store
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import SPEC, _register_spec
from tests.test_review_cli import _finding, _json, _register_vocabulary, _thth


# --- R3: 持ち越しは指摘 1 件の対応であって、記録まるごとの確認済み化ではない ---

def test_持ち越さなかった指摘はunreviewed_historyに残る(isolated_account, tmp_path):
    """旧版に 2 件の指摘があり、1 件だけ持ち越したとき、もう 1 件は消えない。"""
    v = _register_vocabulary()
    f = tmp_path / "draft.md"
    f.write_text("旧版の本文")
    old = record(v, hashlib.sha256(f.read_bytes()).hexdigest(),
                 findings=[_finding("missing_condition"),
                            _finding("unsupported_claim")])
    f.write_text("新版の本文")
    record(v, hashlib.sha256(f.read_bytes()).hexdigest(), carried_from=old,
           findings=[_finding("unsupported_claim", result="no_problem",
                               note="裏付けだけ再検査した。条件不足は未確認")])

    out = _json(_thth(["topics", "review", "nigamilab-threads",
                        "--draft", str(f)]))
    old_entries = [r for r in out["unreviewed_history"] if r["review_id"] == old]
    assert len(old_entries) == 1, "旧記録が丸ごと履歴から消えている"
    # **持ち越したのは unsupported_claim の 1 件だけ**——missing_condition は
    # まだ未確認のまま、旧記録の reason_ids に残っているはず。
    assert set(old_entries[0]["reason_ids"]) == {"missing_condition",
                                                    "unsupported_claim"}


def test_持ち越した先がcarried_byとして添えられる(isolated_account, tmp_path):
    """持ち越し先の記録が `carried_by` として旧記録に添えられること。"""
    v = _register_vocabulary()
    f = tmp_path / "draft.md"
    f.write_text("旧版の本文")
    old = record(v, hashlib.sha256(f.read_bytes()).hexdigest(),
                 findings=[_finding("missing_condition"),
                            _finding("unsupported_claim")])
    f.write_text("新版の本文")
    new = record(v, hashlib.sha256(f.read_bytes()).hexdigest(), carried_from=old,
                 findings=[_finding("unsupported_claim", result="no_problem",
                                     note="裏付けだけ再検査した")])

    out = _json(_thth(["topics", "review", "nigamilab-threads",
                        "--draft", str(f)]))
    old_entry = next(r for r in out["unreviewed_history"]
                      if r["review_id"] == old)
    assert old_entry["carried_by"] == [new]
    # **読む人が「忘れられているわけではない」と分かる**ように、note にも出す。
    assert new in old_entry["note"]


# --- R2: 未指定と解決失敗を分ける ------------------------------------------

def test_仕様が読めない記録はunresolved_recordsに出て候補の母数から外れる(
        isolated_account):
    """指定はしているが型の仕様を解決できない記録は、候補には数えない。

    **読めない記録が混ざっていても、残りの読める記録はそのぶんで普通に
    候補になれる**（1 件読めないだけで全体を諦めない）。
    """
    v = _register_vocabulary()
    sid = _register_spec()
    missing = _register_spec(dict(SPEC, meaning_version=2))

    # 棚から消える記録（form_spec_id の指定はしているが、指した先が読めない）。
    record(v, form_spec_id=missing, provenance="production", case_id="lost-1")
    record(v, "b" * 64, form_spec_id=missing, provenance="production",
           case_id="lost-2")
    (Path(store.root()) / "form_specs" / (missing[7:] + ".json")).unlink()

    # 読める記録も混ぜる——独立事例の母数はこちらだけで満たしてよい。
    record(v, "c" * 64, form_spec_id=sid, provenance="production",
           case_id="ok-1")
    record(v, "d" * 64, form_spec_id=sid, provenance="production",
           case_id="ok-2")

    out = _json(_thth(["topics", "improvements", "--form-spec", sid]))
    assert len(out["unresolved_records"]) == 2, out["unresolved_records"]
    assert len(out["candidates"]) == 1, out
    assert out["candidates"][0]["independent_cases"] == 2
    # 読めない記録の指摘が独立事例に混ざっていない。
    assert out["candidates"][0]["confirmed_case_ids"] == ["ok-1", "ok-2"]


def test_型の仕様を最初から指定していない記録はこれまでどおり扱う(
        isolated_account):
    """**未指定**（`form_spec_id` を付けていない）は**解決失敗**と別。

    一律禁止にはしない——これまでどおり候補の母数に数える。
    """
    v = _register_vocabulary()
    sid = _register_spec()
    record(v, provenance="production", case_id="no-spec-1")
    record(v, "b" * 64, provenance="production", case_id="no-spec-2")

    out = _json(_thth(["topics", "improvements", "--form-spec", sid]))
    assert out["unresolved_records"] == [], out["unresolved_records"]
    assert len(out["candidates"]) == 1, out
    assert out["candidates"][0]["independent_cases"] == 2
