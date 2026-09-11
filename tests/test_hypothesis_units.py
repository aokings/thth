"""予測の**観測単位**（運用指摘 2026-09-12・`LEDGER_METRICS` が平らな一覧だった件）。

**このファイルは外部レビューの反例ではなく、こちらで書いたもの。** 外部から
受け取った反例ファイル（`test_hypothesis_boundaries.py` ほか）には追記しない
——byte 一致が崩れ、「どこまでが外部の検査か」が読めなくなるため。

きっかけ: 運用セッションが仮説を 1 件登録し、**自分で穴を見つけて報告してきた。**

  > `views` は投稿単位、`clicks_by_url` はアカウント日次×URL。つまり私の予測は、
  > **「その投稿が生んだクリック」という変数が台帳に存在しないまま書かれている。**

`LEDGER_METRICS` が **8 つの名前を並べただけの平らな一覧**だったので、どちらも
「うちにある指標」として通っていた。**同じ根の間違いが `thth/measured.py` の
欠測判定にも出ていた**（投稿単位とアカウント日次を 1 つの集合に混ぜて、1 つの
期待一覧と突き合わせていた）。元を層のある定義にして、両方を塞ぐ。
"""
from __future__ import annotations

import pytest

from tests.test_hypotheses import _hypothesis
from thth import topic_models as models


def test_層の違う指標を並べた予測に警告が出る(thth_root):
    """**拒否はしない**（proposed は未検証の仮説を置く場所）。**黙って通さない。**"""
    row = {"predictions": [
        {"statement": "views 最多の投稿の URL が clicks でも最多",
         "metrics": ["views", "clicks"]},
    ]}
    problems = models.prediction_unit_problems(row)

    assert any("観測単位が食い違" in p for p in problems), \
        "層の違う指標を並べた予測が黙って通っている"
    assert any("どの投稿が生んだクリックかは台帳にありません" in p
               for p in problems), \
        "`clicks` に何が無いのか（URL ごとの内訳しか無い）が出ていない"


def test_同じ層の指標だけなら警告は出ない(thth_root):
    """**警告を出しすぎない。** `views` と `likes` はどちらも投稿単位でも
    アカウント日次でも取れるので、食い違っていない。"""
    row = {"predictions": [
        {"statement": "views が多い投稿は likes も多い",
         "metrics": ["views", "likes"]},
    ]}
    assert models.prediction_unit_problems(row) == []


def test_そろう観測単位が無い予測は登録できない(thth_root):
    """食い違い（警告）と、**そろう読み方が 1 つも無い**（拒否）を分ける。
    `shares` は投稿にしか無く、`clicks` はアカウント日次にしか無い——
    **1 つの表には決して並ばない。**"""
    row = _hypothesis(predictions=[
        {"statement": "shares が多い日は clicks も多い",
         "metrics": ["shares", "clicks"], "window": "24h", "scope": "kopicha"},
    ])
    with pytest.raises(models.SchemaError) as e:
        models.build_hypothesis(row)
    assert "同じ観測単位にそろいません" in str(e.value)
    assert "shares" in str(e.value) and "clicks" in str(e.value)
