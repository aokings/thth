"""語彙を替える前に影響を見る・撤回されたら再確認にする（Codex §8・§11・A12）。

**替える判断の前に、替えたら何が読めなくなるかを言う。**
**撤回された観測に依存する判断は、記録を消さずに再確認の印を付ける。**
"""
from __future__ import annotations

import json
import os

import pytest

from tests.test_review_cli import (VOCABULARY, _entry, _finding, _json,
                                    _register_vocabulary, _review, _thth)

OBSERVATION = {
    "topic": "コーヒー", "search_mode": "topic_tag", "provider": "browser",
    "retrieved_at": "2026-09-11T12:00:00+09:00", "status": "ok",
    "samples": [{"post_id": "p1", "excerpt": "豆の話", "author_key": "a1",
                  "tagged": True}],
}


def _record(payload, extra=None, by="masaru"):
    return _thth(["topics", "record-review", "--json-stdin", "--by", by]
                  + list(extra or []), payload)


# --- A12 撤回の波及 ---------------------------------------------------------

def test_A12_根拠の観測が取り下げられたら再確認の印が付く(isolated_account, thth_root):
    """受け入れ A12。**依存する判断に再確認の印。採用時の履歴は保存。**"""
    observed = _json(_thth(["topics", "observe", "--json-stdin", "--by", "テスト"],
                            OBSERVATION))
    observation_id = observed["observation_id"]
    vocabulary_id = _register_vocabulary()
    review = _json(_record(_review(
        vocabulary_id, draft_sha256="a" * 64,
        findings=[_finding(evidence_refs=[observation_id], note="観測に基づく")])))

    before = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert before["needs_recheck"] == []

    taken = _thth(["topics", "retract", observation_id, "--reason", "見間違い",
                    "--by", "テスト"])
    assert taken.returncode == 0, taken.stdout + taken.stderr

    after = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert after["count"] == 1, "記録を消している"
    assert after["needs_recheck"][0]["review_id"] == review["review_id"]
    assert after["needs_recheck"][0]["withdrawn_evidence"][0]["withdrawn_refs"] \
        == [observation_id]
    assert any("再確認" in w for w in after["warnings"])

    one = _json(_thth(["topics", "review", review["review_id"]]))
    assert one["withdrawn_evidence"][0]["reason_id"] == "missing_condition"
    # **判定そのものは書き換わっていない**（取り下げた事実を横に置くだけ）。
    assert one["review"]["findings"][0]["evidence_refs"] == [observation_id]


def test_取り下げを取り消せば印も消える(isolated_account, thth_root):
    observed = _json(_thth(["topics", "observe", "--json-stdin", "--by", "テスト"],
                            OBSERVATION))
    vocabulary_id = _register_vocabulary()
    _record(_review(vocabulary_id, draft_sha256="a" * 64,
                     findings=[_finding(
                         evidence_refs=[observed["observation_id"]])]))
    _thth(["topics", "retract", observed["observation_id"], "--reason", "誤り",
            "--by", "テスト"])
    assert _json(_thth(["topics", "review",
                         isolated_account["name"]]))["needs_recheck"]
    _thth(["topics", "unretract", observed["observation_id"]])
    assert _json(_thth(["topics", "review",
                         isolated_account["name"]]))["needs_recheck"] == []


# --- §8 手順 2・3 影響範囲と shadow -----------------------------------------

def test_替えたら読めなくなる記録を先に名前で出す(isolated_account, thth_root):
    """**過去のラベルを読み出せなくしない**（Codex §11）。0 件に変換しない。"""
    vocabulary_id = _register_vocabulary()
    for draft in ("a" * 64, "b" * 64):
        _record(_review(vocabulary_id, draft_sha256=draft,
                         findings=[_finding("missing_condition")]))
    _record(_review(vocabulary_id, draft_sha256="c" * 64,
                     findings=[_finding("other", note="分類できない")]))

    # `missing_condition` を落とした新しい語彙を**当ててみる**。
    candidate = dict(VOCABULARY, entries=[
        e for e in VOCABULARY["entries"] if e["reason_id"] != "missing_condition"])
    out = _json(_thth(["topics", "impact", "--json-stdin",
                        "--against", vocabulary_id], candidate))

    assert out["stored"] is False
    assert out["reviews_examined"] == 3
    broken = out["records_that_stop_reading"]
    assert len(broken) == 2
    assert all(b["lost_reason_ids"] == ["missing_condition"] for b in broken)
    assert out["reasons_removed"] == ["missing_condition"]
    assert out["reasons_in_use"] == {"missing_condition": 2, "other": 1}
    assert "現行版は替えていません" in out["notice"]

    # **本当に保存していない。**
    assert _json(_thth(["topics", "vocabulary"]))["count"] == 1


def test_一度も使われていない理由を言う(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    _record(_review(vocabulary_id, draft_sha256="a" * 64,
                     findings=[_finding("missing_condition")]))
    out = _json(_thth(["topics", "impact", "--json-stdin",
                        "--against", vocabulary_id], VOCABULARY))
    assert out["records_that_stop_reading"] == []
    assert "unsupported_claim" in out["reasons_never_used"]
    assert "missing_condition" not in out["reasons_never_used"]


def test_足す理由と落とす理由を分けて出す(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    candidate = dict(VOCABULARY, entries=[
        e for e in VOCABULARY["entries"] if e["reason_id"] != "unsupported_claim"
    ] + [_entry("evidence_mismatch", definition="根拠と主張が対応しない")])
    out = _json(_thth(["topics", "impact", "--json-stdin",
                        "--against", vocabulary_id], candidate))
    assert out["reasons_added"] == ["evidence_mismatch"]
    assert out["reasons_removed"] == ["unsupported_claim"]


def test_形の違う語彙は当てる前に断る(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    broken = dict(VOCABULARY, entries=[
        e for e in VOCABULARY["entries"] if e["reason_id"] != "other"])
    proc = _thth(["topics", "impact", "--json-stdin", "--against", vocabulary_id],
                  broken)
    assert proc.returncode == 2
    assert "other" in _json(proc)["error"]["message"]
