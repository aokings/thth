"""型の仕様と改善候補の CLI（Codex §5・§12 第 2 段階）。

**実プロセスで回す**（規約 11）。出口条件のうち「検収履歴から改善案を提示する
まで」を、記録 → 束ね → 候補、の順に通す。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.conftest import BIN_THTH, write_queue_file
from tests.test_review_cli import (VOCABULARY, _entry, _finding, _json, _register_vocabulary,
                                    _review, _thth, NOW)

FORM = "比較→条件→選択"


def _role(role_id, **over):
    row = {"role_id": role_id, "display": role_id, "description": "渡すもの",
           "evidence_required": []}
    row.update(over)
    return row


SPEC = {
    "form": FORM, "scope": "すべての原稿",
    "applies_when": ["比較対象が明示されている"],
    "roles": [_role("対象と結論の範囲"),
               _role("共通の比較軸", evidence_required=["比較項目ごとの原資料参照"]),
               _role("条件付きの選び方")],
    "unfit_examples": ["片方だけ実測なのに同列に並べる"],
    "machine_checks": ["roles_covered", "segments_have_role", "evidence_refs_exist"],
    "semantic_questions": ["本当に比較可能か", "読者が選べるか"],
    "success_measure": "条件漏れの再発が減る",
    "cases": [], "state": "proposed", "meaning_version": 1,
    "created_at": NOW, "supersedes": None,
}


def _register_spec(payload=None):
    proc = _thth(["topics", "record-form-spec", "--json-stdin", "--by", "テスト"],
                  payload or SPEC)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _json(proc)["form_spec_id"]


def _record(payload, extra=None, by="masaru"):
    return _thth(["topics", "record-review", "--json-stdin", "--by", by]
                  + list(extra or []), payload)


# --- 仕様を登録して読む -----------------------------------------------------

def test_仕様を登録して型で絞って読む(isolated_account, thth_root):
    spec_id = _register_spec()
    other = _register_spec(dict(SPEC, form="列挙",
                                 roles=[_role("何を並べるか")],
                                 applies_when=["数えられるものを並べる"]))
    listed = _json(_thth(["topics", "form-spec", "--form", FORM]))
    assert listed["count"] == 1
    assert listed["form_specs"][0]["form_spec_id"] == spec_id
    assert listed["form_specs"][0]["roles"] == ["対象と結論の範囲", "共通の比較軸",
                                                 "条件付きの選び方"]
    assert _json(_thth(["topics", "form-spec"]))["count"] == 2
    assert _json(_thth(["topics", "form-spec", other]))["form_spec"]["form"] == "列挙"


def test_走らない検査名は正しい形を添えて断る(isolated_account, thth_root):
    proc = _thth(["topics", "record-form-spec", "--json-stdin", "--by", "テスト"],
                  dict(SPEC, machine_checks=["読者に伝わるか"]))
    assert proc.returncode == 2
    out = _json(proc)
    assert "semantic_questions" in out["error"]["message"]
    assert "FormSpec" in out["expected_schema"]


# --- 段の役割の申告を検査する（意味は見ていない） --------------------------

def test_form_checkは申告の形だけを見る(isolated_account, thth_root):
    spec_id = _register_spec()
    path = str(write_queue_file(isolated_account["queue_dir"], "c.md",
                                 body="## threads\n\n比較の本文。\n",
                                 fm_overrides={"status": "draft"}))
    out = _json(_thth(["topics", "form-check", "--form-spec", spec_id,
                        "--json-stdin", "--draft", path],
                       {"segments": [
                           {"index": "1/3", "roles": ["対象と結論の範囲"]},
                           {"index": "2/3", "roles": []},
                           {"index": "3/3", "roles": ["条件付きの選び方"]}],
                        "evidence": {}}))
    checks = {c["check"]: c for c in out["machine_checks"]}
    assert checks["roles_covered"]["missing_roles"] == ["共通の比較軸"]
    assert checks["segments_have_role"]["segments_without_role"] == ["2/3"]
    assert checks["evidence_refs_exist"]["missing_evidence"][0]["role_id"] \
        == "共通の比較軸"
    # **意味は見ていない**と出力に書く（§7）。
    assert [q["result"] for q in out["semantic_evaluations"]] == \
        ["not_evaluated", "not_evaluated"]
    assert "公開してよいかどうか" in out["out_of_scope"]
    assert out["draft_sha256"]


def test_仕様を指さずには検査できない(isolated_account, thth_root):
    proc = _thth(["topics", "form-check", "--json-stdin"],
                  {"segments": [{"index": "1/1", "roles": []}]})
    assert _json(proc)["error"]["code"] == "missing_form_spec"
    proc = _thth(["topics", "form-check", "--form-spec", "sha256:" + "9" * 64,
                   "--json-stdin"], {"segments": [{"index": "1/1", "roles": []}]})
    assert _json(proc)["error"]["code"] == "not_found"


# --- 検収履歴から改善候補（§12 第 2 段階の出口） ---------------------------

def _finding_for(role_id, reason_id="missing_condition", note="条件が足りない"):
    return dict(_finding(reason_id, note=note), role_id=role_id)


def test_独立ケースが2件そろって初めて候補になる(isolated_account, thth_root):
    """**1 件で仕様を動かさない**（Codex §8）。0 件と「足りない」も分ける。

    **閾値は実運用（`provenance=production`）の `case_id` が明示された系列
    だけで数える**（数え方の契約・再判定 R4/R5・2026-09-11 Codex）。
    """
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()

    _record(_review(vocabulary_id, draft_sha256="a" * 64, form=FORM,
                     form_spec_id=spec_id, provenance="production",
                     case_id="case-a",
                     findings=[_finding_for("共通の比較軸", note="時点が無い")]))
    first = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert first["candidates"] == []
    assert first["not_enough_cases"][0]["independent_cases"] == 1
    assert first["minimum_independent_cases"] == 2

    # **同じ原稿（同じ系列）を 2 回数えない。** `case_id` を同じにして送る。
    _record(_review(vocabulary_id, draft_sha256="a" * 64, form=FORM,
                     form_spec_id=spec_id, judged_by={"kind": "human"},
                     provenance="production", case_id="case-a",
                     findings=[_finding_for("共通の比較軸", note="単位が無い")]),
             by="別の人")
    still = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert still["candidates"] == [], "同じ原稿 2 件を独立ケースにした"

    _record(_review(vocabulary_id, draft_sha256="b" * 64, form=FORM,
                     form_spec_id=spec_id, provenance="production",
                     case_id="case-b",
                     findings=[_finding_for("共通の比較軸", note="換算前の単位が無い")]))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert len(out["candidates"]) == 1
    candidate = out["candidates"][0]
    assert candidate["role_id"] == "共通の比較軸"
    assert candidate["independent_cases"] == 2
    assert len(candidate["review_ids"]) == 3
    assert "evidence_required" in candidate["suggestion"]
    # **説明をそのまま渡す**（何を要求すれば防げたかは人が読んで決める）。
    assert "時点が無い" in candidate["notes"]
    # **書き換えていない。**
    assert _json(_thth(["topics", "form-spec", spec_id]))["form_spec"]["roles"][1] \
        ["evidence_required"] == ["比較項目ごとの原資料参照"]


def test_仕様に無い役割への指摘は名前の不一致として出す(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for i, draft in enumerate(("a" * 64, "b" * 64)):
        _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                         form_spec_id=spec_id, provenance="production",
                         case_id=f"case-{i}",
                         findings=[_finding_for("まとめ", note="役割名が違う")]))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    candidate = out["candidates"][0]
    assert candidate["role_known"] is False
    assert "仕様に足りない役割" in candidate["suggestion"]


def test_別の型の検収を混ぜない(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for draft in ("a" * 64, "b" * 64):
        _record(_review(vocabulary_id, draft_sha256=draft, form="列挙",
                         findings=[_finding_for("共通の比較軸")]))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["candidates"] == [] and out["not_enough_cases"] == []
    assert out["reviewed"] == 0


def test_役割の記録が無ければどこを直すか決まらないと言う(isolated_account, thth_root):
    """**履歴があっても、役割が付いていなければ仕様の改善には使えない。**"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for i, draft in enumerate(("a" * 64, "b" * 64)):
        _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                         provenance="production", case_id=f"case-{i}",
                         findings=[_finding("missing_condition", note="条件不足")]))
    candidate = _json(_thth(["topics", "improvements",
                              "--form-spec", spec_id]))["candidates"][0]
    assert candidate["role_id"] is None
    assert "決まりません" in candidate["suggestion"]


def test_候補を出しても何も書き換えない(isolated_account, thth_root):
    """Codex §12 第 2 段階「共通語彙の自動追加や profile 自動変更はしません」。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for i, draft in enumerate(("a" * 64, "b" * 64)):
        _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                         provenance="production", case_id=f"case-{i}",
                         findings=[_finding_for("共通の比較軸")]))
    before = {kind: sorted(os.listdir(os.path.join(
        thth_root, "state", "topic_advice", kind)))
        for kind in ("reviews", "vocabularies", "form_specs")}

    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["candidates"]
    assert "書き換えていません" in out["notice"]

    after = {kind: sorted(os.listdir(os.path.join(
        thth_root, "state", "topic_advice", kind)))
        for kind in ("reviews", "vocabularies", "form_specs")}
    assert before == after, "候補を出すだけで棚が動いた"
    assert not os.path.isdir(os.path.join(thth_root, "state", "topic_advice",
                                           "profiles"))


def test_formを後付けしても承認は無効にならない(isolated_account, thth_root):
    """運用セッションの問い（2026-09-11）を**推論でなく実物で確かめる**（規約 12）。

    > 承認済み 28 本に form を後付けするのが筋に見えます（本文を変えないので
    > 承認には触りません——front-matter の form は approved_sha の入力 5 項目に
    > 入っていないはずです。**念のためそちらで確認してもらえますか**）。

    入っていない。`section`・`account`・`reply_to`・`topic`・`publish_at` の 5 つ
    だけ（`thth/approval.py`）。**この test はその事実を固定する**——ここが変わると
    型を後付けした瞬間に承認済みの原稿が一斉に `approval_stale` になる。
    """
    from tests.conftest import approve_via_cli, run_thth
    path = write_queue_file(isolated_account["queue_dir"], "approved.md",
                             body="## threads\n\n本文です。\n",
                             fm_overrides={"status": "draft"})
    assert approve_via_cli(path, by="テスト").returncode == 0
    before = run_thth(["lint", str(path), "--json"])
    assert before.returncode == 0, before.stdout + before.stderr

    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "approved_sha:" in text and "form:" not in text
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("status: approved",
                               "status: approved\nform: 比較→条件→選択", 1))

    after = run_thth(["lint", str(path), "--json"])
    assert after.returncode == 0, after.stdout + after.stderr
    assert "stale" not in after.stdout, after.stdout


def test_無関係な語彙が同じ番号を名乗っても束ねない(isolated_account, thth_root):
    """**逆監査（2026-09-11）で出た。** 版の番号は語彙ごとに独立して採番される。

    `(reason_id, meaning_version)` だけで束ねていたので、**定義文がまるで違う
    2 つの語彙が、同じ `reason_id` に同じ番号を振っただけで 1 つの改善候補に
    まとまった**（`independent_cases: 2`）。系統（`supersedes` の根）が違うものは
    束ねない。
    """
    import copy
    from tests.test_review_cli import VOCABULARY
    spec_id = _register_spec()

    first = _register_vocabulary()
    other = copy.deepcopy(VOCABULARY)
    other["entries"][0]["definition"] = "まったく別の理由づけ（同じ番号を独立に採番）"
    other["name"] = "別系統の語彙"
    second = _register_vocabulary(other)          # supersedes は付けない＝別系統

    # **見出しの `entries[0]` は語彙 v1/v2 で書き換えた `missing_condition`。**
    # 束ねる鍵は `reason_id` の意味の指紋（定義文そのもの）なので、指摘も
    # `missing_condition` を使う——書き換えていない `unsupported_claim` を使うと
    # 定義文が同じままなので、**別系統でも意味が同じという別の話**になってしまう
    # （数え方の契約・再判定 R1・2026-09-11 Codex）。
    for vocabulary_id, draft in ((first, "a" * 64), (second, "b" * 64)):
        proc = _record(dict(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                                     form_spec_id=spec_id),
                             findings=[dict(_finding("missing_condition",
                                                      note="裏付けが無い"),
                                             role_id="共通の比較軸")]))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["candidates"] == [], "別系統の語彙を 1 つの候補に束ねている"
    assert len(out["not_enough_cases"]) == 2, out["not_enough_cases"]
    # `vocabulary_lineage` はいまリストで返る（系統をたどった根の集合）。
    roots = {c["vocabulary_lineage"][0] for c in out["not_enough_cases"]}
    assert roots == {first, second}


def test_同じ系統で版が上がったものは束ねる(isolated_account, thth_root):
    """**止めすぎない。** 同じ語彙の版が上がっただけなら、同じ話として束ねる。"""
    import copy
    from tests.test_review_cli import VOCABULARY
    spec_id = _register_spec()
    first = _register_vocabulary()
    nxt = copy.deepcopy(VOCABULARY)
    nxt["supersedes"] = first                     # 同じ系統
    second = _register_vocabulary(nxt)

    for i, (vocabulary_id, draft) in enumerate(
            ((first, "a" * 64), (second, "b" * 64))):
        _record(dict(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                              form_spec_id=spec_id, provenance="production",
                              case_id=f"case-{i}"),
                      findings=[dict(_finding("unsupported_claim", note="裏付け無し"),
                                      role_id="共通の比較軸")]))

    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert len(out["candidates"]) == 1, out
    assert out["candidates"][0]["independent_cases"] == 2
    # `vocabulary_lineage` はいまリストで返る（系統をたどった根の集合）。
    assert out["candidates"][0]["vocabulary_lineage"] == [first]
