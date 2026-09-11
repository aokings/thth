"""検収の出自を分ける（再検収の付帯指摘・運用セッターの申し出 2026-09-11）。

> 指摘は本物、原稿は試験データ。…試作 1 本の癖を全体の傾向として読むことになる。

**消さず、母数からも外さず、印を付けて区別できるようにする。**
"""
from __future__ import annotations

import pytest

from tests.test_form_spec_cli import FORM, SPEC, _register_spec
from tests.test_review_cli import (_finding, _json, _register_vocabulary,
                                    _review, _thth)


def _record(payload, extra=None):
    return _thth(["topics", "record-review", "--json-stdin", "--by", "テスト"]
                  + list(extra or []), payload)


def _finding_for(role_id="共通の比較軸", note="条件が足りない"):
    return dict(_finding("missing_condition", note=note), role_id=role_id)


def test_試作と実運用を分けて数える(isolated_account, thth_root):
    """**閾値は実運用の `case_id` 付きの系列だけで数える**（数え方の契約・
    再判定 R4・2026-09-11 Codex）。試作は消さず、根拠には残るが、独立事例には
    数えない——それを確かめるため、実運用 2 件（別 `case_id`）に試作 1 件を
    混ぜる。
    """
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    records = (("a" * 64, "trial", None),
                ("b" * 64, "production", "case-b"),
                ("c" * 64, "production", "case-c"))
    for draft, source, case_id in records:
        over = {}
        if case_id:
            over["case_id"] = case_id
        proc = _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                                form_spec_id=spec_id,
                                findings=[_finding_for()], **over),
                        ["--provenance", source])
        assert proc.returncode == 0, proc.stdout + proc.stderr

    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["provenance"] == {"production": 2, "trial": 1}
    candidate = out["candidates"][0]
    assert candidate["provenance"] == {"production": 2, "trial": 1}
    assert candidate["independent_cases"] == 2
    # **実運用が母数を満たしていれば候補にはなるが、試作が混ざっている事実は
    # 隠さない**（数え方の契約・再判定 R4）。「候補になれない」ではなく
    # 「閾値には実運用だけを数えている」という但し書きに変わった。
    assert "実運用以外の記録が混ざっています" in candidate["representativeness"]


def test_試作だけの候補には代表性の但し書きを付ける(isolated_account, thth_root):
    """**試験のために書かれた原稿の癖を、全体の傾向として読まない。**

    **試作だけの記録は独立事例に数えないので、いまは `candidates` ではなく
    `not_enough_cases` に出る**（数え方の契約・再判定 R4・2026-09-11 Codex）。
    但し書きはどちらの欄でも同じ理由で付く——「まだ足りない」も 0 件と
    同じに丸めない。
    """
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for draft in ("a" * 64, "b" * 64):
        _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                         form_spec_id=spec_id, findings=[_finding_for()]),
                 ["--provenance", "trial"])
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["candidates"] == [], "試作だけの記録が独立事例に数えられている"
    candidate = out["not_enough_cases"][0]
    assert "実運用の検収が 1 件もありません" in candidate["representativeness"]


def test_印の無い記録をunknownとして数える_本番と読まない(isolated_account, thth_root):
    """**印が付く前に書かれた記録**（VM にある 8 件がこれ）。

    `unknown` は「本番」ではない。**次の実運用の検収と混ざって見分けが
    つかなくなる**のを防ぐのがこの欄の目的。
    """
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for draft in ("a" * 64, "b" * 64):
        _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                         form_spec_id=spec_id, findings=[_finding_for()]))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["provenance"] == {"unknown": 2}
    # `unknown` は実運用ではないので、いまは `not_enough_cases` に出る
    # （数え方の契約・再判定 R4・2026-09-11 Codex）。
    assert out["candidates"] == []
    assert "実運用の検収が 1 件もありません" in \
        out["not_enough_cases"][0]["representativeness"]


def test_知らない出自は断る(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    proc = _record(_review(vocabulary_id, draft_sha256="a" * 64,
                            provenance="本番っぽいもの"))
    assert proc.returncode == 2
    assert "provenance" in _json(proc)["error"]["message"]


# --- 再判定 R4・R5 を、こちらの側からも固定する（2026-09-11） ---------------
#
# **Codex の反例は通るが、それだけでは R4・R5 の直しが固定されない。**
# 誤実装を入れて確かめたところ、次の 2 つが素通りした:
#   - 試作も閾値に数える（反例は case_id が無いので、どちらでも候補にならない）
#   - 線の無い版を独立として数える（候補が空なので、報告する数が変わっても通る）
# **反例が通ることと、直しが効いていることは別。**

def _case(vocabulary_id, spec_id, draft, *, case_id=None, provenance="production"):
    row = _review(vocabulary_id, draft_sha256=draft, form=FORM,
                   form_spec_id=spec_id, findings=[_finding_for()],
                   provenance=provenance)
    if case_id:
        row["case_id"] = case_id
    return row


def test_R4_試作は閾値に数えない_実運用だけで数える(isolated_account, thth_root):
    """**試験のために書かれた原稿を、実運用の候補成立に足さない**（再判定 R4）。"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()

    # 実運用 1 件＋試作 1 件（どちらも別の系列を名乗る）→ **候補にならない。**
    _record(_case(vocabulary_id, spec_id, "a" * 64, case_id="記事A"))
    _record(_case(vocabulary_id, spec_id, "b" * 64, case_id="試作B",
                   provenance="trial"))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["candidates"] == [], "試作を閾値に数えている"
    not_yet = out["not_enough_cases"][0]
    assert not_yet["independent_cases"] == 1
    assert not_yet["provenance"] == {"production": 1, "trial": 1}
    assert "試作" in not_yet["why_not_yet"]

    # 実運用がもう 1 件入って初めて成立する。
    _record(_case(vocabulary_id, spec_id, "c" * 64, case_id="記事C"))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert len(out["candidates"]) == 1
    candidate = out["candidates"][0]
    assert candidate["independent_cases"] == 2
    # 「確認済み」ではなく申告なので、フィールド名も `reported_case_ids`
    # に変わった（表示の条件・2026-09-12 Codex 再々判定）。
    assert candidate["reported_case_ids"] == ["記事A", "記事C"]
    # **試作が混ざっていることは隠さない。**
    assert candidate["provenance"]["trial"] == 1
    assert "実運用以外の記録が混ざっています" in candidate["representativeness"]


def test_R5_線の無い版を独立として数えない(isolated_account, thth_root):
    """**指紋が違うことは、別の事例であることではない**（再判定 R5）。

    同じ原稿の改訂かもしれない。**人が `case_id` を名乗ったときだけ**独立と数える。
    """
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for draft in ("a" * 64, "b" * 64):
        _record(_case(vocabulary_id, spec_id, draft))      # case_id を書かない

    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["candidates"] == []
    not_yet = out["not_enough_cases"][0]
    assert not_yet["independent_cases"] == 0, "線の無い版を独立として数えている"
    assert not_yet["unlinked_versions"] == 2
    assert not_yet["draft_versions"] == 2
    assert "別の原稿なのか同じ原稿の改訂なのかを記録から言えない" \
        in not_yet["why_not_yet"]

    # **名乗れば数える。**
    _record(_case(vocabulary_id, spec_id, "c" * 64, case_id="記事C"))
    _record(_case(vocabulary_id, spec_id, "d" * 64, case_id="記事D"))
    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["independent_cases"] == 2
    assert out["candidates"][0]["unlinked_versions"] == 2   # 数えはする・隠さない
