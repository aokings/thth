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
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for draft, source in (("a" * 64, "trial"), ("b" * 64, "production")):
        proc = _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                                form_spec_id=spec_id,
                                findings=[_finding_for()]),
                        ["--provenance", source])
        assert proc.returncode == 0, proc.stdout + proc.stderr

    out = _json(_thth(["topics", "improvements", "--form-spec", spec_id]))
    assert out["provenance"] == {"production": 1, "trial": 1}
    candidate = out["candidates"][0]
    assert candidate["provenance"] == {"production": 1, "trial": 1}
    # 実運用が 1 件でも混じっていれば代表性の但し書きは付けない。
    assert "representativeness" not in candidate


def test_試作だけの候補には代表性の但し書きを付ける(isolated_account, thth_root):
    """**試験のために書かれた原稿の癖を、全体の傾向として読まない。**"""
    vocabulary_id = _register_vocabulary()
    spec_id = _register_spec()
    for draft in ("a" * 64, "b" * 64):
        _record(_review(vocabulary_id, draft_sha256=draft, form=FORM,
                         form_spec_id=spec_id, findings=[_finding_for()]),
                 ["--provenance", "trial"])
    candidate = _json(_thth(["topics", "improvements",
                              "--form-spec", spec_id]))["candidates"][0]
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
    assert "実運用の検収が 1 件もありません" in \
        out["candidates"][0]["representativeness"]


def test_知らない出自は断る(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    proc = _record(_review(vocabulary_id, draft_sha256="a" * 64,
                            provenance="本番っぽいもの"))
    assert proc.returncode == 2
    assert "provenance" in _json(proc)["error"]["message"]
