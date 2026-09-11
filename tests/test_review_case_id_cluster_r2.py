"""外部レビュー（Codex 再々々判定 R2・2026-09-12）を、自分のテストとしても固定する

（`tests/test_review_case_id_cluster_m2.py` の書き方に合わせる）。

R2 が指摘したのは **M2 の結合の順番が逆だった**こと。

> production 系列 A が `case-a`、同系列の trial 再検査が `shared-case`、
> 非接続 production 系列 B が `shared-case` を名乗る場合、現在は
> `independent_cases=2` で候補成立する。trial の ID は同系列内の競合表示
> には使うが、系列横断の結合と競合には使っていない。

受入条件は「provenance に関係なく既知の同一性・衝突を制約へ使い、その後で
production の根拠を持つ成分だけを候補母数へ数える」こと。trial を正例として
加算する修正は不要——**trial は橋渡し（制約）にはなるが、それ自体は件数を
増やさない**。

- R2 本体: trial の `case_id` が非接続の production 2 系列を橋渡しすると、
  独立事例は 1 件に畳まれる（`thth/topic_models.py` の
  `_case_id_clusters()` に production の根拠を持つ成分だけを絞る仕組みを
  足した）。
- その橋渡しは `case_id_conflicts`（系列横断側）に出る。
- 止めすぎていないことの対照実験: 違う `case_id` の非接続 production 2 件は、
  これまでどおり独立 2 件。
- trial だけが橋渡しする成分（production の根拠が片方にも無い）は、
  橋渡しはしても母数には 1 件も数えない——trial を正例として件数に
  足していないことの直接証拠。
"""
from __future__ import annotations

from tests.test_editorial_audit import _improvements
from tests.test_external_acceptance import record
from tests.test_form_spec_cli import _register_spec
from tests.test_review_cli import _register_vocabulary


def _setup():
    return _register_vocabulary(), _register_spec()


def test_trialのidが橋渡しするproduction2系列は独立2件にならない(isolated_account):
    """R2 本体: trial 経由の `case_id` 一致も、production 系列の結合に使う。"""
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
    print("R2_TRIAL_BRIDGE_NOT_INDEPENDENT", out)
    assert out["candidates"] == [], (
        "trial 経由で橋渡しされた 2 つの production 系列が独立 2 件のまま候補"
        "成立している")
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 1


def test_trialのidの橋渡しが系列横断の競合表示に出る(isolated_account):
    """R2: 橋渡しの根拠になった `case_id` が `case_id_conflicts` から見える。"""
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
    print("R2_TRIAL_BRIDGE_VISIBLE_IN_CONFLICTS", out)
    item = out["not_enough_cases"][0]
    cross = [c for c in item["case_id_conflicts"]
             if c.get("case_id") == "shared-case"]
    assert len(cross) == 1
    assert cross[0]["series"] == sorted(["a" * 64, "c" * 64])


def test_違うidの非接続production2件はこれまでどおり独立2件(isolated_account):
    """止めすぎていないことの対照実験。橋渡しが無ければ畳まない。"""
    vocabulary_id, spec_id = _setup()
    record(vocabulary_id, "a" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-a")
    record(vocabulary_id, "b" * 64, form_spec_id=spec_id,
           provenance="production", case_id="case-b")

    out = _improvements(spec_id)
    print("R2_DIFFERENT_ID_STILL_INDEPENDENT", out)
    assert len(out["candidates"]) == 1
    candidate = out["candidates"][0]
    assert candidate["independent_cases"] == 2
    assert candidate["case_id_conflicts"] == []


def test_trialだけが橋渡しする成分はproductionの根拠が無ければ母数に数えない(
        isolated_account):
    """trial を正例として加算しないことの直接証拠。

    production 側はどちらの系列にも `case_id` を申告していない（＝
    production だけを見れば「線の無い版」2 件）。trial の再検査だけが
    両系列に同じ `case_id` を付けて橋渡ししている。橋渡しで身元は
    わかる（「線の無い版」からは外れる）が、**production の根拠が
    どちらの成分にも無いので独立事例には 1 件も数えない**。
    """
    vocabulary_id, spec_id = _setup()
    first = record(
        vocabulary_id, "a" * 64, form_spec_id=spec_id,
        provenance="production",
        disposition="fixed", revised_draft_sha256="b" * 64)
    record(
        vocabulary_id, "b" * 64, form_spec_id=spec_id,
        provenance="trial", case_id="trial-only-bridge", recheck_of=first)
    second = record(
        vocabulary_id, "d" * 64, form_spec_id=spec_id,
        provenance="production",
        disposition="fixed", revised_draft_sha256="e" * 64)
    record(
        vocabulary_id, "e" * 64, form_spec_id=spec_id,
        provenance="trial", case_id="trial-only-bridge", recheck_of=second)

    out = _improvements(spec_id)
    print("R2_TRIAL_ONLY_BRIDGE_NOT_COUNTED", out)
    assert out["candidates"] == []
    item = out["not_enough_cases"][0]
    assert item["independent_cases"] == 0
    # 身元は trial の申告からわかるので「線の無い版」からは外れる
    # （production だけを見て孤立扱いにはしない）。
    assert item["unlinked_versions"] == 0
