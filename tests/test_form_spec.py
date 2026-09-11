"""型をテンプレートから検収可能な仕様へ（Codex §5・§8・§12 第 2 段階）。

**段数と役割数は同じではない**。**走らない検査名を書けない**。
**accepted には独立ケース 2 件と反例 1 件**（§8・昇格条件を種類別に分ける）。
"""
from __future__ import annotations

import pytest

from thth import topic_models as m

NOW = "2026-09-11T20:00:00+09:00"


def _role(role_id, **over):
    row = {"role_id": role_id, "display": role_id, "description": "この段が渡すもの",
           "evidence_required": []}
    row.update(over)
    return row


def _case(kind, draft, account="kopicha-threads", **over):
    row = {"kind": kind, "draft_sha256": draft, "account": account,
           "review_ids": [], "note": "例"}
    row.update(over)
    return row


def _spec(**over):
    row = {
        "form": "比較→条件→選択", "scope": "すべての原稿",
        "applies_when": ["比較対象が明示されている",
                          "読者の目的が選択または差の理解である"],
        "roles": [
            _role("対象と結論の範囲"),
            _role("共通の比較軸",
                  evidence_required=["比較項目ごとの原資料参照（値・対象・時点・単位）"]),
            _role("条件付きの選び方"),
        ],
        "unfit_examples": ["A だけ実測・B は推測なのに同列の事実として比較する",
                            "限定条件を後段に隠す"],
        "machine_checks": ["roles_covered", "segments_have_role",
                            "evidence_refs_exist"],
        "semantic_questions": ["本当に比較可能か", "断定が強すぎないか",
                                "読者が選べるか"],
        "success_measure": "検収者が指摘した条件漏れの減少",
        "cases": [], "state": "proposed", "meaning_version": 1,
        "created_at": NOW, "created_by": "claude（開発セッション）",
        "supersedes": None,
    }
    row.update(over)
    return m.build_form_spec(row)


def _claim(segments, evidence=None):
    return {"segments": segments, "evidence": evidence or {}}


# --- 仕様の形 ---------------------------------------------------------------

def test_テンプレートのままの型は仕様にならない():
    """**適用条件・不適合例・意味評価は型の本体**（Codex §5）。"""
    for key in ("applies_when", "unfit_examples", "semantic_questions"):
        with pytest.raises(m.SchemaError) as e:
            _spec(**{key: []})
        assert key in str(e.value)


def test_走らない検査名を書けない():
    """**意味評価を機械検査の欄に置かせない。**

    書けてしまうと、**やっていない検査を「やった」と記録できる。**
    """
    with pytest.raises(m.SchemaError) as e:
        _spec(machine_checks=["roles_covered", "読者に伝わるか"])
    assert "semantic_questions" in str(e.value)
    assert "roles_covered" in str(e.value)


def test_旧語彙の型名は読み替えずに断る():
    """`FORM_MIGRATION` はあるが、**黙って移さない**（どの型に移すかは原稿の判断）。"""
    with pytest.raises(m.SchemaError) as e:
        _spec(form="比較")
    assert "比較→条件→選択" in str(e.value)
    assert "機械的な読み替えはしません" in str(e.value)


def test_空の仕様を断る():
    with pytest.raises(m.SchemaError):
        m.build_form_spec({})


# --- §8 昇格条件（編集上の型） ----------------------------------------------

def test_acceptedには独立した正例2件と反例1件が要る():
    """**型の作成者の自己採点だけで採用しない**（Codex §8・独立確認）。"""
    one = "a" * 64
    with pytest.raises(m.SchemaError) as e:
        _spec(state="accepted", cases=[_case("positive", one),
                                        _case("counter", "c" * 64)])
    assert "正例が 2 件以上" in str(e.value)

    # **同じ原稿を 2 回数えない。**
    with pytest.raises(m.SchemaError) as e:
        _spec(state="accepted", cases=[_case("positive", one),
                                        _case("positive", one),
                                        _case("counter", "c" * 64)])
    assert "同じ原稿を 2 回数えません" in str(e.value)

    with pytest.raises(m.SchemaError) as e:
        _spec(state="accepted", cases=[_case("positive", one),
                                        _case("positive", "b" * 64)])
    assert "反例が 1 件以上" in str(e.value)
    assert "何を除いているかを言っていません" in str(e.value)


def test_一分野だけの確認を共通仕様と呼ばない():
    """**「一分野だけならその範囲に限定する」**（Codex §8）。"""
    cases = [_case("positive", "a" * 64), _case("positive", "b" * 64),
             _case("counter", "c" * 64)]
    with pytest.raises(m.SchemaError) as e:
        _spec(state="accepted", cases=cases, scope="すべての原稿")
    assert "kopicha-threads" in str(e.value)

    _spec(state="accepted", cases=cases, scope="kopicha-threads の原稿")
    _spec(state="accepted", scope="すべての原稿",
          cases=[_case("positive", "a" * 64),
                  _case("positive", "b" * 64, account="nigamilab-threads"),
                  _case("counter", "c" * 64)])


def test_proposedのうちはケースが無くてよい():
    """**揃うまで proposed か shadow。** 書きながら育てる。"""
    assert _spec(cases=[])["state"] == "proposed"
    assert _spec(state="shadow", cases=[])["state"] == "shadow"


# --- 段の役割の申告を検査する -----------------------------------------------

def test_1段で複数の役割を満たしてよい():
    """**段数と役割数は同じではない**（Codex §5）。水増しを誘わない。"""
    spec = _spec()
    out = m.check_form_claim(spec, _claim([
        {"index": "1/2", "roles": ["対象と結論の範囲", "共通の比較軸"]},
        {"index": "2/2", "roles": ["条件付きの選び方"]},
    ], evidence={"共通の比較軸": ["sha256:" + "1" * 64]}))
    covered = [c for c in out["machine_checks"] if c["check"] == "roles_covered"][0]
    assert covered["result"] == "no_problem"
    assert out["roles_claimed_by"]["対象と結論の範囲"] == ["1/2"]
    # **2 つの役割を持つ段を「役割が無い」扱いにしない**——ここを見ていないと
    # 「段 1 つにつき役割 1 つ」の実装が素通りする（2026-09-11 に素通りした）。
    empty = [c for c in out["machine_checks"]
             if c["check"] == "segments_have_role"][0]
    assert empty["result"] == "no_problem" and empty["segments_without_role"] == []


def test_役割を1つも持たない段を見つける():
    """**段を足しても新しい役割が増えていない手がかり**（`redundant_segment`）。"""
    out = m.check_form_claim(_spec(), _claim([
        {"index": "1/3", "roles": ["対象と結論の範囲", "共通の比較軸"]},
        {"index": "2/3", "roles": []},
        {"index": "3/3", "roles": ["条件付きの選び方"]},
    ], evidence={"共通の比較軸": ["sha256:" + "1" * 64]}))
    check = [c for c in out["machine_checks"]
             if c["check"] == "segments_have_role"][0]
    assert check["result"] == "problem"
    assert check["segments_without_role"] == ["2/3"]


def test_必須役割の欠けと知らない役割名を分けて言う():
    out = m.check_form_claim(_spec(), _claim([
        {"index": "1/2", "roles": ["対象と結論の範囲", "まとめ"]},
        {"index": "2/2", "roles": ["共通の比較軸"]},
    ], evidence={"共通の比較軸": ["sha256:" + "1" * 64]}))
    check = [c for c in out["machine_checks"] if c["check"] == "roles_covered"][0]
    assert check["missing_roles"] == ["条件付きの選び方"]
    assert check["unknown_roles"] == [{"segment": "1/2", "role_id": "まとめ"}]


def test_根拠の要る役割に根拠が無ければ言う():
    out = m.check_form_claim(_spec(), _claim([
        {"index": "1/1", "roles": ["対象と結論の範囲", "共通の比較軸",
                                    "条件付きの選び方"]},
    ]))
    check = [c for c in out["machine_checks"]
             if c["check"] == "evidence_refs_exist"][0]
    assert check["result"] == "problem"
    assert check["missing_evidence"][0]["role_id"] == "共通の比較軸"


def test_実在しない根拠を見つける():
    known = {"sha256:" + "1" * 64}
    out = m.check_form_claim(_spec(), _claim(
        [{"index": "1/1", "roles": ["対象と結論の範囲", "共通の比較軸",
                                     "条件付きの選び方"]}],
        evidence={"共通の比較軸": ["sha256:" + "9" * 64]}), known_ids=known)
    check = [c for c in out["machine_checks"]
             if c["check"] == "evidence_refs_exist"][0]
    assert check["unknown_refs"][0]["ref"] == "sha256:" + "9" * 64


def test_意味評価は未評価のまま返す():
    """**機械が見ていないものを、見ていないと書く**（Codex §7）。"""
    out = m.check_form_claim(_spec(), _claim([
        {"index": "1/1", "roles": ["対象と結論の範囲", "共通の比較軸",
                                    "条件付きの選び方"]}],
        evidence={"共通の比較軸": ["sha256:" + "1" * 64]}))
    assert [q["result"] for q in out["semantic_evaluations"]] == \
        ["not_evaluated"] * 3
    assert "本当に比較可能か" in [q["question"] for q in out["semantic_evaluations"]]
    assert "申告された" in out["notice"]


def test_仕様は棚に版として並ぶ(thth_root):
    """語彙と同じ扱い——**コードではなくデータ**（引継ぎ 設計条件 1）。"""
    from thth import topic_store as store
    first = _spec()
    store.put("form_specs", first, id_key="form_spec_id")
    second = _spec(supersedes=first["form_spec_id"], meaning_version=2,
                    semantic_questions=["本当に比較可能か", "断定が強すぎないか",
                                         "読者が選べるか", "比較の時点が揃っているか"])
    store.put("form_specs", second, id_key="form_spec_id")

    rows, broken, _ = store.load_all("form_specs")
    assert broken == [] and len(rows) == 2
    assert second["supersedes"] == first["form_spec_id"]
    assert first["form_spec_id"] != second["form_spec_id"]
