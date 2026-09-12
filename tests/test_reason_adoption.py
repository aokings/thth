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
            "checked_on": [{"path": "docs/sns/queue/a.md", "sha256": "a" * 64}],
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
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, checked_on=[]))
    assert "checked_on" in str(e.value)


@pytest.mark.parametrize("鍵", ["path", "sha256"])
def test_確かめた原稿の素性が空なら拒否する(鍵):
    """**どの原稿で確かめたのかが後から辿れること**が、この記録の値打ち。"""
    checked = [{"path": "a.md", "sha256": "a" * 64}]
    checked[0][鍵] = ""
    with pytest.raises(models.SchemaError) as e:
        models.build_reason_adoption(_採用("sha256:" + "b" * 64, checked_on=checked))
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
    row = json.dumps(_採用(v["vocabulary_id"]))
    _入力 = _渡す(tmp_path, row)
    assert topic_cli.dispatch(["topics", "adopt-reason", "--input", _入力, "--json"]) == 0
    capsys.readouterr()

    assert topic_cli.dispatch(["topics", "adoptions", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    r = out["adoptions"][0]
    assert r["reason_id"] == "too_long"
    assert r["checked_on"] == ["docs/sns/queue/a.md"]
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
