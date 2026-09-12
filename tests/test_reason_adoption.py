"""修正理由を 1 件ずつ採用する（masaru 裁定 2026-09-12・2 番）。

> **確認できた項目から個別に `accepted` へ上げましょう。** 実際の原稿で意味・
> 使い分け・適用例を確認する。**`accepted` は「編集に使う語彙として採用した」で
> あり「反応率を改善すると実証された」ではありません。一括昇格はしません。**

**規約 14**（新しい schema を足したら (1) 必須項目の検査 (2) `expected_schema` の
返却 (3) 空・欠損での拒否テスト を**同時に**入れる）もここで満たす。
"""
from __future__ import annotations

import json

import pytest

from thth import topic_cli, topic_models as models, topic_store as store


def _渡す(tmp_path, row):
    """`--input` でファイルから渡す（実際の使い方と同じ経路）。"""
    path = tmp_path / "adoption.json"
    path.write_text(row if isinstance(row, str) else json.dumps(row, ensure_ascii=False),
                     encoding="utf-8")
    return str(path)


def _検収(vocab, *, sha, result, reason_id="too_long", account="kopicha-threads",
           disposition="unresolved"):
    """**採用の根拠になる検収記録を、実際に保存する。**

    採用は**自己申告では通らない**（外部レビュー R2・2026-09-12）。
    """
    review = models.build_review({
        "account": account, "draft_sha256": sha,
        "vocabulary_id": vocab["vocabulary_id"],
        "findings": [{"reason_id": reason_id, "check_method": "human",
                       "result": result, "evidence_refs": [], "note": "採用の根拠としてテストが置いた判定"}],
        "judged_by": {"kind": "human", "id": "masaru"},
        "judged_at": "2026-09-12T10:10:00+09:00",
        "disposition": disposition,
    }, vocabulary=vocab)
    saved, _ = store.put("reviews", review, id_key="review_id")
    return saved["review_id"]


def _語彙(state="shadow"):
    return models.build_vocabulary({
        "name": "修正理由 v1", "created_at": "2026-09-12T10:00:00+09:00",
        "created_by": "テスト", "supersedes": None,
        "entries": [
            {"reason_id": "too_long", "display": "長すぎる",
             "definition": "1 段が読み切れない長さ", "includes": [], "excludes": [],
             "scope": "kopicha-threads", "judged_by": ["human"],
             "state": state, "meaning_version": 1},
            {"reason_id": "other", "display": "その他",
             "definition": "分類できないもの", "includes": [], "excludes": [],
             "scope": "kopicha-threads", "judged_by": ["human"],
             "state": state, "meaning_version": 1},
        ]})


def _採用(vocabulary_id, **上書き):
    row = {"vocabulary_id": vocabulary_id, "reason_id": "too_long",
            "meaning_version": 1,
            "cases": [
                {"kind": "positive", "path": "docs/sns/queue/a.md",
                 "sha256": "a" * 64, "account": "kopicha-threads",
                 "review_id": "sha256:" + "1" * 64},
                {"kind": "positive", "path": "docs/sns/queue/b.md",
                 "sha256": "b" * 64, "account": "kopicha-threads",
                 "review_id": "sha256:" + "2" * 64},
                {"kind": "counter", "path": "docs/sns/queue/c.md",
                 "sha256": "c" * 64, "account": "kopicha-threads",
                 "review_id": "sha256:" + "3" * 64},
            ],
            "meaning": "1 段が読み切れない長さのこと",
            "distinguished_from": "`too_many` は本数の話で、長さではない",
            "applied_example": "a.md の 2 段目を 2 つに割った",
            "decided_by": "masaru", "decided_at": "2026-09-12T10:30:00+09:00"}
    row.update(上書き)
    return row


# --- 一括昇格ができないこと（裁定の中心） -----------------------------------

def test_reason_idに配列を渡せない():
    """**「一括昇格はしない」を型で守る。**"""
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64,
                                            reason_id=["too_long", "other"]))
    assert "1 件だけ" in str(e.value)


def test_採用の記録は1件に1つのreason_id(thth_root):
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    a = models.build_reason_adoption(_採用(v["vocabulary_id"]))
    assert a["reason_id"] == "too_long"
    assert isinstance(a["reason_id"], str)


# --- 規約 14 (1) 必須項目の検査 ---------------------------------------------

@pytest.mark.parametrize("欠く", models.ADOPTION_KEYS)
def test_必須項目が欠けたら拒否する(欠く):
    row = _採用("sha256:" + "b" * 64)
    row.pop(欠く)
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(row)
    assert 欠く in str(e.value) or "足りません" in str(e.value)


# --- 規約 14 (3) 空・欠損での拒否 -------------------------------------------

@pytest.mark.parametrize("鍵", ["meaning", "distinguished_from", "applied_example"])
def test_意味と使い分けと適用例は空にできない(鍵):
    """**「確かめた」と書くだけにしない。**"""
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, **{鍵: "  "}))
    assert 鍵 in str(e.value)


def test_確かめた原稿が無ければ拒否する():
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, cases=[]))
    assert "cases" in str(e.value)


@pytest.mark.parametrize("鍵", ["path", "sha256", "account"])
def test_確かめた原稿の素性が空なら拒否する(鍵):
    """**どの原稿で確かめたのかが後から辿れること**が、この記録の値打ち。"""
    cases = [{"kind": "positive", "path": "a.md", "sha256": "a" * 64,
               "account": "kopicha-threads", "review_id": "sha256:" + "1" * 64}]
    cases[0][鍵] = ""
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, cases=cases))
    assert 鍵 in str(e.value)


def test_vocabulary_idの形を見る():
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("これは ID ではない"))
    assert "vocabulary_id" in str(e.value)


# --- accepted の意味 --------------------------------------------------------

def test_採用の記録に意味しないことを毎回書く():
    """**読む人が入れ替わる。** 「採用済み＝効果が実証済み」と読まれる余地を残さない。"""
    a = models.build_reason_adoption(_採用("sha256:" + "b" * 64))
    assert a["state"] == "accepted"
    assert "実証された、という意味ではありません" in a["means"]


# --- 規約 14 (2) expected_schema の返却 -------------------------------------

def test_入力が壊れていたら採用の形を返す(thth_root, tmp_path, capsys):
    _入力 = _渡す(tmp_path, "{")
    rc = topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert "ReasonAdoption" in json.dumps(out["expected_schema"], ensure_ascii=False)


# --- 語彙との突き合わせ -----------------------------------------------------

def test_語彙に無い理由は採用できない(thth_root, tmp_path, capsys):
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = json.dumps(_採用(v["vocabulary_id"], reason_id="知らない語"))
    _入力 = _渡す(tmp_path, row)
    rc = topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["error"]["code"] == "reason_not_found"


def test_意味の版がずれていたら採用できない(thth_root, tmp_path, capsys):
    """**どの意味の版を採用したのかがずれたまま残らないようにする。**"""
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = json.dumps(_採用(v["vocabulary_id"], meaning_version=2))
    _入力 = _渡す(tmp_path, row)
    rc = topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["error"]["code"] == "meaning_version_mismatch"


def test_採用してから一覧に出る(thth_root, tmp_path, capsys):
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = _採用(v["vocabulary_id"])
    # **根拠になる検収記録を実際に保存する**（自己申告では通らない）。
    for case, result in zip(row["cases"], ("problem", "problem", "no_problem")):
        case["review_id"] = _検収(v, sha=case["sha256"], result=result)
    _入力 = _渡す(tmp_path, json.dumps(row))
    assert topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"]) == 0
    capsys.readouterr()

    assert topic_cli.dispatch(["topics", "adoptions", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    r = out["adoptions"][0]
    assert r["reason_id"] == "too_long"
    assert r["case_count"] == 3
    # **本体は出さない**（一覧は ID と件数まで・既存の射影と同じ）。
    assert "meaning" not in r and "applied_example" not in r
    assert "実証された、という意味ではありません" in out["means"]


def test_語彙の記録は書き換わらない(thth_root, tmp_path, capsys):
    """**内容アドレスの追記のみ。** 採用は別の記録として重ねる。"""
    v = _語彙()
    saved, _ = store.put("vocabularies", v, id_key="vocabulary_id")
    前 = store.get("vocabularies", saved["vocabulary_id"])
    row = json.dumps(_採用(saved["vocabulary_id"]))
    _入力 = _渡す(tmp_path, row)
    topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"])
    capsys.readouterr()
    assert store.get("vocabularies", saved["vocabulary_id"]) == 前

# --- 採用に足る材料か（Codex §8 と同じ数え方）-------------------------------
#
# **2026-09-12 に運用セッションが手で数え直して「まだ 1 件も採用できない」と
# 結論した。** その数え方を道具に置く。**手で数えた合意ではなく、検査にする。**

def test_同じ原稿を2回数えない():
    """`missing_condition` は 6 回記録されていたが、**実体は 2 件**で、
    **どちらも同じ原稿・同じ段・同じ往復**だった。"""
    同じ原稿 = [
        {"kind": "positive", "path": "a.md", "sha256": "a" * 64, "account": "k",
         "review_id": "sha256:" + "1" * 64},
        {"kind": "positive", "path": "a.md", "sha256": "d" * 64, "account": "k",
         "review_id": "sha256:" + "2" * 64},
        {"kind": "counter", "path": "c.md", "sha256": "c" * 64, "account": "k", "review_id": "sha256:" + "3" * 64},
    ]
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, cases=同じ原稿))
    assert "直す前と後でも 1 件" in str(e.value)


def test_反例が無ければ採用できない():
    """**反例が無いと、語の境界を確かめたことにならない。**

    実際、8 件の記録に**反例は 1 件も無かった**（`no_problem` が 1 件あるだけ）。
    """
    正例だけ = [
        {"kind": "positive", "path": "a.md", "sha256": "a" * 64, "account": "k", "review_id": "sha256:" + "1" * 64},
        {"kind": "positive", "path": "b.md", "sha256": "b" * 64, "account": "k", "review_id": "sha256:" + "2" * 64},
    ]
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, cases=正例だけ))
    assert "反例が 1 件以上" in str(e.value)


def test_いまの材料では1件も採用できない():
    """**2026-09-12 時点の実際の材料**（運用セッションが台帳から数えたもの）。

        missing_condition  正例 2 件だが**同じ原稿**
        claim_overstated   正例 1 件（もう 1 件は no_problem＝反例ではない）
        unsupported_claim  0 件（not_evaluated。**未評価は正例にならない**）
        other              2 件（束ねない合意のまま）

    **どれも通らないこと**を、道具の側で確かめる。
    """
    原稿 = "green-spec-spread.md"
    sha = "1" * 64
    材料 = {
        "missing_condition": [
            {"kind": "positive", "path": 原稿, "sha256": sha, "account": "k",
             "review_id": "sha256:" + "1" * 64},
            {"kind": "positive", "path": 原稿, "sha256": sha, "account": "k",
             "review_id": "sha256:" + "2" * 64},
        ],
        "claim_overstated": [
            {"kind": "positive", "path": 原稿, "sha256": sha, "account": "k",
             "review_id": "sha256:" + "1" * 64},
        ],
        "unsupported_claim": [],
    }
    for reason_id, cases in 材料.items():
        with pytest.raises(models.SchemaError):
            models.build_reason_adoption(
                _採用("sha256:" + "b" * 64, reason_id=reason_id, cases=cases))

def test_accepted_が保証することを記録に書く():
    """**条件より先に、何を保証する状態かがある**（masaru 指示 2026-09-12）。"""
    a = models.build_reason_adoption(_採用("sha256:" + "b" * 64))
    assert "判断の記録" in a["guarantees"]
    assert "この記録では確かめていません" in a["guarantees"]


def test_件数のしきい値は暫定だと明示する():
    """**型の昇格条件の流用を、決定済みの条件として扱わない**（masaru 指示）。"""
    正例1件 = [
        {"kind": "positive", "path": "a.md", "sha256": "a" * 64, "account": "k", "review_id": "sha256:" + "1" * 64},
        {"kind": "counter", "path": "c.md", "sha256": "c" * 64, "account": "k", "review_id": "sha256:" + "3" * 64},
    ]
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, cases=正例1件))
    assert "暫定" in str(e.value)


def test_判定者の人数は条件にしない():
    """**「3 人目必須」を決定済みの条件として扱わない**（masaru 指示）。

    同じ 1 人が判定した材料でも、**件数と境界の条件を満たせば通る。**
    人数を条件にするかどうかは、まだ決まっていない。
    """
    同じ人 = [
        {"kind": "positive", "path": "a.md", "sha256": "a" * 64, "account": "k",
         "review_id": "sha256:" + "1" * 64},
        {"kind": "positive", "path": "b.md", "sha256": "b" * 64, "account": "k",
         "review_id": "sha256:" + "2" * 64},
        {"kind": "counter", "path": "c.md", "sha256": "c" * 64, "account": "k",
         "review_id": "sha256:" + "3" * 64},
    ]
    a = models.build_reason_adoption(_採用("sha256:" + "b" * 64, cases=同じ人))
    assert a["state"] == "accepted"


def test_採用できなくても投稿と記録は妨げない(thth_root, tmp_path, capsys):
    """**採用が 0 件でも、proposed のまま使える**（masaru 指示 2026-09-12）。

    語彙の登録も、検収の記録も、採用の可否に触らない。
    """
    v = _語彙(state="proposed")
    saved, _ = store.put("vocabularies", v, id_key="vocabulary_id")
    # 採用は 1 件も無い。
    assert topic_cli.dispatch(["topics", "adoptions", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 0
    # それでも語彙は読めるし、使える状態のまま。
    読めた = store.get("vocabularies", saved["vocabulary_id"])
    assert {e["state"] for e in 読めた["entries"]} == {"proposed"}

# --- 証拠の実在と対応（外部レビュー R2・2026-09-12）-------------------------
#
# **変異で分かった**: `review_id` を必須にしただけでは、**実在も対応も守られて
# いなかった**。突き合わせを丸ごと外しても、こちらのテストも外部の反例も
# 素通りした。

def _採用を試す(tmp_path, capsys, v, row):
    _入力 = _渡す(tmp_path, json.dumps(row))
    rc = topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"])
    return rc, json.loads(capsys.readouterr().out)


def _根拠つき(v, results=("problem", "problem", "no_problem")):
    row = _採用(v["vocabulary_id"])
    for case, result in zip(row["cases"], results):
        case["review_id"] = _検収(v, sha=case["sha256"], result=result)
    return row


def test_検収記録が無い_review_idは通さない(thth_root, tmp_path, capsys):
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = _根拠つき(v)
    row["cases"][0]["review_id"] = "sha256:" + "9" * 64      # 保存されていない
    rc, out = _採用を試す(tmp_path, capsys, v, row)
    assert rc == 2 and out["error"]["code"] == "evidence_not_verified"
    assert "見つかりません" in out["error"]["message"]


def test_別の版の記録を根拠にできない(thth_root, tmp_path, capsys):
    """**パスの誤記・別版の転記**もここで止まる。"""
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = _根拠つき(v)
    row["cases"][0]["sha256"] = "f" * 64                      # 記録と違う版
    rc, out = _採用を試す(tmp_path, capsys, v, row)
    assert rc == 2 and "別の版の記録" in out["error"]["message"]


def test_その理由の判定が無い記録は根拠にできない(thth_root, tmp_path, capsys):
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = _根拠つき(v)
    row["cases"][0]["review_id"] = _検収(
        v, sha=row["cases"][0]["sha256"], result="problem", reason_id="other")
    rc, out = _採用を試す(tmp_path, capsys, v, row)
    assert rc == 2 and "の判定がありません" in out["error"]["message"]


def test_未評価は正例にならない(thth_root, tmp_path, capsys):
    """**§7: 未評価と問題なしは別。** `unsupported_claim` の 1 件がこれだった。"""
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = _根拠つき(v, results=("not_evaluated", "problem", "no_problem"))
    rc, out = _採用を試す(tmp_path, capsys, v, row)
    assert rc == 2 and "未評価は正例になりません" in out["error"]["message"]


def test_問題ありの記録を反例にできない(thth_root, tmp_path, capsys):
    """**反例は「当てはまらないと判定した」記録。**"""
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    row = _根拠つき(v, results=("problem", "problem", "problem"))
    rc, out = _採用を試す(tmp_path, capsys, v, row)
    assert rc == 2 and "反例は「当てはまらないと判定した」" in out["error"]["message"]


def test_機械が確かめたことと人が判断したことを分けて返す(thth_root, tmp_path, capsys):
    """**外部レビューの指示。** 参照と件数は機械、意味の適合は人。"""
    v = _語彙()
    store.put("vocabularies", v, id_key="vocabulary_id")
    rc, out = _採用を試す(tmp_path, capsys, v, _根拠つき(v))
    assert rc == 0
    assert len(out["machine_verified"]) == 3
    assert {m["kind"] for m in out["machine_verified"]} == {"positive", "counter"}
    assert set(out["self_reported"]) == {"path", "meaning", "distinguished_from",
                                          "applied_example"}
