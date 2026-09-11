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


# --- 後から来た版の印（運用指摘 2026-09-12）---------------------------------

def test_一覧で後から来た版が読める(thth_root):
    """**記録を消さない造りにしたのに、消していないことが読み手に見えない**
    ——検収台帳（`superseded_by`）では解いていたのに、**仮説の棚だけ落ちていた。**

    実物で、同じ `code` の仮説が `U` と `B` で 2 件並び、**どちらが現行か
    読めなかった**（運用セッションが `B` を `U` に改めた直後）。**古いほうを
    掴む筋がある。**
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    first = _json(_register(_hypothesis()))["hypothesis_id"]
    second = _json(_register(_hypothesis(
        sample_design=models.UNIDENTIFIABLE,
        refutation="差が大きくても識別できない",
        supersedes=first)))["hypothesis_id"]

    listed = _json(_thth(["topics", "hypotheses"]))
    by_id = {h["hypothesis_id"]: h for h in listed["hypotheses"]}

    assert by_id[second]["supersedes"] == first, \
        "新しい版が、どれを差し替えたのか一覧から読めない"
    assert by_id[first]["superseded_by"] == [second], \
        "**古い版に、後から来た版の印が付いていない**（古いほうを掴む筋がある）"
    assert by_id[second]["superseded_by"] == [], \
        "現行の版に印が付いている"


def test_状態で絞っても絞りの外の新しい版が印に出る(thth_root):
    """**逆向きの索引は、絞る前の全件から張る。** 絞った中だけで張ると、
    **絞りの外に新しい版があるときに「これが現行だ」と読めてしまう。**

    **最初に書いたテストは、この場所を守っていなかった**（2026-09-12）。
    2 件とも `proposed` で登録したので、`--state proposed` で絞っても両方
    残り、**索引を絞る前に張ろうが後に張ろうが同じ結果**になっていた
    （変異で確認: 索引を絞った後の行から張っても通ってしまった）。

    **絞りの外に置くには、CLI を通せない。** 登録の口は `proposed` しか
    受け付けない（昇格は未実装・外部レビュー R4）ので、ここだけ **store に
    直接置く**。「いつか shadow が開いたとき」を先取りして守る。
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth
    from thth import topic_store

    first = _json(_register(_hypothesis()))["hypothesis_id"]
    # **CLI では作れない状態**（登録は proposed 限定）。ここは store に直接置く。
    later = models.build_hypothesis(_hypothesis(
        state="shadow", refutation="差が大きくても識別できない",
        supersedes=first, created_at="2026-09-12T04:00:00+09:00",
        proposed_by="テスト"))
    saved, _wrote = topic_store.put("hypotheses", later, id_key="hypothesis_id")

    listed = _json(_thth(["topics", "hypotheses", "--state", "proposed"]))
    by_id = {h["hypothesis_id"]: h for h in listed["hypotheses"]}

    assert saved["hypothesis_id"] not in by_id, "絞りが効いていない（前提が崩れている）"
    assert by_id[first]["superseded_by"] == [saved["hypothesis_id"]], \
        "**絞りの外にある新しい版が見えず、古いほうが現行に読める**"


# --- V1: 版の指し先が壊れていても一覧を落とさない（外部レビュー 2026-09-12）---

_BAD_LINK = ["sha256:" + "1" * 64]


def test_supersedesに配列を渡すと登録で拒まれる(thth_root):
    """**登録は通るのに、その記録があると一覧が丸ごと読めなくなっていた。**

    逆索引が `supersedes` を辞書のキーに使うので `TypeError: unhashable type`。
    **既存の検査だけなら受理できる入力が、後から足した読取処理を壊していた。**
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    good = _json(_register(_hypothesis()))["hypothesis_id"]
    proc = _register(_hypothesis(supersedes=_BAD_LINK))

    assert proc.returncode != 0, "配列の supersedes が保存されている"
    assert "supersedes" in proc.stdout + proc.stderr

    listed = _json(_thth(["topics", "hypotheses"]))
    assert listed["ok"] is True, "**一覧が読めなくなっている**"
    assert [h["hypothesis_id"] for h in listed["hypotheses"]] == [good]


def test_保存済みの壊れた版の指し先で一覧を落とさない(thth_root):
    """**登録の口を直しても、それ以前に保存された記録は直せない**
    （append-only・消さない）。**1 件の壊れで、正常な仮説まで読めなくしない。**

    `thth/topic_store.py` の `broken_ids` と同じ流儀——**読めないものを数に
    混ぜず、読めるものまで巻き添えにしない。**
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth
    from thth import topic_store

    good = _json(_register(_hypothesis()))["hypothesis_id"]

    # **登録の口を通らない**（いまは拒否される）ので、直接置く。「直す前の口が
    # 受理してしまった記録」を再現するため。
    bad = models.build_hypothesis(_hypothesis(
        code="T02", created_at="2026-09-12T04:30:00+09:00", proposed_by="テスト"))
    # **中身は実在する ID にする。** ここを架空の ID にすると、「壊れを黙って
    # 正常な版関係として扱う」変異を当てても**どの仮説にも結び付かないので
    # テストが通ってしまう**（2026-09-12・変異 O で発覚）。実在する ID を
    # 配列に入れてはじめて、**誤って印を付けたことが観測できる。**
    bad["supersedes"] = [good]
    bad["hypothesis_id"] = models.content_id(bad, exclude=("hypothesis_id",))
    saved, _wrote = topic_store.put("hypotheses", bad, id_key="hypothesis_id")

    listed = _json(_thth(["topics", "hypotheses"]))

    assert listed["ok"] is True, "**壊れ 1 件で一覧が丸ごと落ちている**"
    ids = [h["hypothesis_id"] for h in listed["hypotheses"]]
    assert good in ids, "正常な仮説が巻き添えで読めなくなっている"
    assert saved["hypothesis_id"] in ids, "本体は読めるのに消している"

    problems = listed["broken_version_links"]
    assert [p["hypothesis_id"] for p in problems] == [saved["hypothesis_id"]], \
        "版の関係が読めなかったことを黙っている"
    by_id = {h["hypothesis_id"]: h for h in listed["hypotheses"]}
    assert by_id[good]["superseded_by"] == [], \
        "**壊れた指し先を、正常な版関係として扱っている**"
