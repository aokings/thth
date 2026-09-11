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


@pytest.mark.parametrize("design", [models.ESTIMATION, models.UNIDENTIFIABLE])
def test_そろう観測単位が無くてもproposedには残せる(thth_root, design):
    """**一度は拒否にしたが、間違っていた**（外部レビュー U1・2026-09-12）。

    `shares` は投稿にしか無く、`clicks` はアカウント日次にしか無い——確かに
    1 つの表には並ばない。だが拒否にすると **2 つの点で破綻していた。**

      1. **案内が効かなかった。** エラーは「`sample_design` を `'U'` にして
         残してください」と言うのに、**同じ予測を `U` にしても同じエラーで
         保存できなかった。逃げ道の無い案内。**
      2. 指標の単位が違うことから判るのは「**そのまま同じ粒度の量として
         扱えない**」まで。集計・対応づけ・相関を検討する**未検証の案そのもの**
         を保存できないとまでは言えない。**それを保持するのが proposed の役割。**

    **警告は返す。予測本文は削らない。**
    """
    row = _hypothesis(sample_design=design, predictions=[
        {"statement": "shares が多い日は clicks も多い",
         "metrics": ["shares", "clicks"], "window": "24h", "scope": "kopicha"},
    ])
    built = models.build_hypothesis(row)

    assert built["predictions"][0]["metrics"] == ["shares", "clicks"], \
        "予測本文を削って保存している"
    problems = models.prediction_unit_problems(built)
    assert any("そろう読み方がありません" in p for p in problems), \
        "そろう観測単位が無いことを、黙って通している"


def test_そろう観測単位が無くてもshadowには上げられない(thth_root):
    """**拒否を外したことで、shadow まで開いてはいけない。**"""
    row = _hypothesis(sample_design=models.UNIDENTIFIABLE, state="shadow",
                      predictions=[
        {"statement": "shares が多い日は clicks も多い",
         "metrics": ["shares", "clicks"], "window": "24h", "scope": "kopicha"},
    ])
    with pytest.raises(models.SchemaError) as e:
        models.build_hypothesis(row)
    assert "shadow" in str(e.value)


def test_うちに無い指標は今も拒否される(thth_root):
    """**U1 の修正は「単位がそろわない」だけを警告に落としたもの。**
    持っていない量（滞在時間・親密度）で予測を書かせない検査は生きている。"""
    row = _hypothesis(predictions=[
        {"statement": "滞在時間が長い投稿は views も多い",
         "metrics": ["views", "dwell_time"], "window": "24h", "scope": "kopicha"},
    ])
    with pytest.raises(models.SchemaError) as e:
        models.build_hypothesis(row)
    assert "うちの台帳に無い指標" in str(e.value)


def test_一覧でも観測単位の警告が出る(thth_root):
    """**詳細で出て一覧で出ないと、一覧を見た人は「警告が無い」と読む**
    （外部レビュー U2・2026-09-12）。

    `unit_warnings` は保存しないので、**読むたびに当てる**——その約束が
    **一覧の経路だけ落ちていた。** 保存済みの古い仮説にも効かせるための作りが、
    いちばん人が見る画面で効いていなかった。
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    proc = _register(_hypothesis(sample_design=models.UNIDENTIFIABLE, predictions=[
        {"statement": "shares が多い日は clicks も多い",
         "metrics": ["shares", "clicks"], "window": "24h", "scope": "kopicha"},
    ]))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    hypothesis_id = _json(proc)["hypothesis_id"]

    detail = _json(_thth(["topics", "hypotheses", hypothesis_id]))
    listed = _json(_thth(["topics", "hypotheses"]))

    assert detail["unit_warnings"], "詳細で警告が出ていない"
    assert listed["count"] == 1
    row = listed["hypotheses"][0]
    assert row.get("unit_warnings"), "**一覧で警告が落ちている**"
    assert row["unit_warnings"] == detail["unit_warnings"], \
        "詳細と一覧で警告の中身が違う"


def test_登録の応答にも観測単位の警告が出る(thth_root):
    """登録した本人が、その場で気づけること。"""
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    proc = _register(_hypothesis(predictions=[
        {"statement": "views 最多の投稿の URL が clicks でも最多",
         "metrics": ["views", "clicks"], "window": "24h", "scope": "kopicha"},
    ]))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = _json(proc)
    assert any("観測単位が食い違" in w for w in out["unit_warnings"])
    stored = out["hypothesis_id"]
    assert stored, "警告を出しつつ保存はする（proposed は置き場）"
