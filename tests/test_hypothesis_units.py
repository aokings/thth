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
        supersedes=first,
        supersede_reason="反証の敷居を『複数回』と書いていた（B なのに M の敷居）"
        )))["hypothesis_id"]

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
        supersedes=first, supersede_reason="統制していないものを書いていなかった",
        created_at="2026-09-12T04:00:00+09:00", proposed_by="テスト"))
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


@pytest.mark.parametrize("bad", [[], {}, 0, False, "", "   ", "sha256:xyz",
                                  "sha256:" + "1" * 63, "1" * 64])
def test_空の不正型をnullと同じに扱わない(thth_root, bad):
    """**`if not link:` は `[]` `{}` `0` `false` を null と同じに読んでいた**
    （外部レビュー V1 残件・2026-09-12）。

    **新規登録では拒否する値が、読取では「指し先なし」に化けていた。**
    `None` だけを読み飛ばし、あとは型検査へ通す。
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth
    from thth import topic_store

    good = _json(_register(_hypothesis()))["hypothesis_id"]

    # **登録の口は通らない**（型検査で拒否される）ので、直接置く——「直す前の口が
    # 受理してしまった記録」の再現。**将来の機能の証拠ではない。**
    row = models.build_hypothesis(_hypothesis(
        code="T03", created_at="2026-09-12T05:00:00+09:00", proposed_by="テスト"))
    row["supersedes"] = bad
    row["hypothesis_id"] = models.content_id(row, exclude=("hypothesis_id",))
    saved, _wrote = topic_store.put("hypotheses", row, id_key="hypothesis_id")

    listed = _json(_thth(["topics", "hypotheses"]))

    assert listed["ok"] is True, "一覧が読めなくなっている"
    assert [p["hypothesis_id"] for p in listed["broken_version_links"]] \
        == [saved["hypothesis_id"]], \
        f"{bad!r} を null と同じ『指し先なし』として黙って通している"
    by_id = {h["hypothesis_id"]: h for h in listed["hypotheses"]}
    assert by_id[good]["superseded_by"] == []


def test_nullは診断しない(thth_root):
    """**出しすぎない。** `null`（＝最初の版）は正常で、診断に出してはいけない。"""
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    _register(_hypothesis())
    listed = _json(_thth(["topics", "hypotheses"]))
    assert listed["broken_version_links"] == [], \
        "null（最初の版）を壊れとして出している"


@pytest.mark.parametrize("bad", ["", "   ", "sha256:xyz", "sha256:" + "1" * 63,
                                  "1" * 64, ["sha256:" + "1" * 64], {}, 0, False])
def test_hypothesis_idの表記でない値は登録で拒まれる(thth_root, bad):
    """**登録と読取で同じ基準にする**（外部レビュー・2026-09-12）。

    型だけを見ていたときは、**空文字列と空白だけの文字列が素通りしていた**
    （`str` なので）。「指し先なし」を意味する値が診断されずに索引へ入る——
    `[]` `{}` `0` `false` と同じ形だった。

    **見るのは表記だけ。** 形式が正しいが存在しない ID は通る（参照先の
    実在性・循環検査は対象外との指示）。
    """
    from tests.test_hypotheses import _register

    proc = _register(_hypothesis(supersedes=bad))
    assert proc.returncode != 0, f"{bad!r} が保存されている"
    assert "supersedes" in proc.stdout + proc.stderr


def test_形式が正しければ存在しないIDでも登録できる(thth_root):
    """**見るのは表記だけ**という境界を、こちら側からも固定する。
    参照先の実在検査を勝手に足していないことの確認でもある。"""
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    ghost = "sha256:" + "a" * 64
    proc = _register(_hypothesis(supersedes=ghost,
                                  supersede_reason="表記だけを見ることの確認"))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    listed = _json(_thth(["topics", "hypotheses"]))
    assert listed["broken_version_links"] == [], \
        "**表記は正しいのに壊れとして出している**（実在検査は対象外）"


# --- 版を改めた理由（運用指摘 2026-09-12）------------------------------------

def test_版を改めるなら理由が要る(thth_root):
    """**検収記録には `disposition_reason` があるのに、仮説には無かった。**

    運用は当座 `claim` の末尾に書いていたが、本人が**置き場所として間違って
    いると自覚していた**——`claim` は主張そのものの欄なので、版の運用メモが
    混ざると**次に読む人が主張の一部として読む。**

    **版を改めた記録でいちばん価値があるのは「何を間違えていたか」。**
    それが残らないなら、古い版を消さずに置いておく意味が薄い。
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    first = _json(_register(_hypothesis()))["hypothesis_id"]
    proc = _register(_hypothesis(supersedes=first))

    assert proc.returncode != 0, "理由なしで版を差し替えられている"
    assert "supersede_reason" in proc.stdout + proc.stderr


@pytest.mark.parametrize("blank", ["", "   "])
def test_理由が空なら書いたことにしない(thth_root, blank):
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    first = _json(_register(_hypothesis()))["hypothesis_id"]
    proc = _register(_hypothesis(supersedes=first, supersede_reason=blank))
    assert proc.returncode != 0, f"{blank!r} を理由として受け取っている"


def test_版を改めていないのに理由だけは書けない(thth_root):
    """**何の理由か判らない。** 欄があるからといって、どこにでも書けるように
    はしない。"""
    from tests.test_hypotheses import _register

    proc = _register(_hypothesis(supersede_reason="なんとなく"))
    assert proc.returncode != 0, "版を改めていないのに理由が保存されている"


def test_理由は一覧でも読める(thth_root):
    """**一覧に出さないと、「差し替わっている」ことは読めても「何を間違えて
    いたか」は読めない。** そこがいちばん価値のある部分。"""
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    first = _json(_register(_hypothesis()))["hypothesis_id"]
    why = "反証の敷居を『複数回』と書いていた（B なのに M の敷居）"
    second = _json(_register(_hypothesis(
        supersedes=first, supersede_reason=why)))["hypothesis_id"]

    listed = _json(_thth(["topics", "hypotheses"]))
    by_id = {h["hypothesis_id"]: h for h in listed["hypotheses"]}

    assert by_id[second]["supersede_reason"] == why, "一覧で理由が読めない"
    assert by_id[first]["supersede_reason"] is None, \
        "差し替えていない版に理由が付いている"

    detail = _json(_thth(["topics", "hypotheses", second]))
    assert detail["hypothesis"]["supersede_reason"] == why


# --- 旧記録との同一性（外部レビュー・2026-09-12）-----------------------------

def test_理由欄ができる前と同じIDになる(thth_root):
    """**この欄を `null` で埋めていたら、欄ができる前の記録と別 ID になっていた。**

    「書いた記録と書かなかった記録で内容 ID が変わらないように」という意図で
    `null` を埋めたが、**それは新しい記録どうしの話**だった。**欄ができる前に
    保存した記録と同じ入力を出すと、旧はキー無し・新は `null` で別 ID**になる
    ——**同じ仮説が 2 件に見える。避けたかったことを、別の向きで起こしていた。**

    しかも**旧 ID を指す改訂を作っても、複製のほうは別 ID なので後継として
    結び付かない。**

    省略と明示 `null` を**どちらもキー無しに正規化**すれば、新しい記録どうしの
    同一性も旧記録との同一性も両方保てる（**二択ではなかった**）。
    """
    built = models.build_hypothesis(_hypothesis())
    assert "supersede_reason" not in built, "無い欄を null で埋めている"

    # **この欄ができる前のコードが作っていた ID** を独立に計算する
    # （`out = dict(row)` に schema_version を足して content_id、だけだった）。
    before = dict(_hypothesis())
    before["schema_version"] = models.SCHEMA_VERSION
    old_id = models.content_id(before, exclude=("hypothesis_id",))

    assert built["hypothesis_id"] == old_id, \
        "**欄ができる前と同じ入力が、別 ID になっている**（棚に複製ができる）"


def test_省略と明示nullは同じID(thth_root):
    omitted = models.build_hypothesis(_hypothesis())
    explicit = models.build_hypothesis(_hypothesis(supersede_reason=None))
    assert omitted["hypothesis_id"] == explicit["hypothesis_id"]
    assert "supersede_reason" not in explicit


def test_同じ入力を二度登録しても棚は1件(thth_root):
    """**内容で決まる ID なので、同じものは 2 件にならない。**"""
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json, _thth

    first = _json(_register(_hypothesis()))
    second = _json(_register(_hypothesis(supersede_reason=None)))

    assert first["hypothesis_id"] == second["hypothesis_id"]
    assert first["stored"] is True
    assert second["stored"] is False, "同じ中身を 2 件目として保存している"
    assert _json(_thth(["topics", "hypotheses"]))["count"] == 1


def test_理由を変えればIDは変わる(thth_root):
    """**同一性を保つのと、理由を無視するのは違う。** 理由は中身なので、
    変えれば別の記録になる。"""
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    first = _json(_register(_hypothesis()))["hypothesis_id"]
    a = _json(_register(_hypothesis(supersedes=first, supersede_reason="理由 A")))
    b = _json(_register(_hypothesis(supersedes=first, supersede_reason="理由 B")))
    assert a["hypothesis_id"] != b["hypothesis_id"], \
        "理由を変えても同じ ID になっている（理由が内容 ID に入っていない）"


# --- 規約 14: 断るときは正しい形も返す（2026-09-12）--------------------------

def test_仮説を断るときも正しい形を返す(thth_root):
    """**規約 14 の 4 回目をやっていた。**

    > 新しい schema を足したら、(1) 必須項目の検査 (2) `expected_schema` の返却
    > (3) 空・欠損での拒否テスト を**同時に**入れる。

    `hypotheses` を足すとき (1) と (3) は入れて、**(2) だけ落としていた。**
    `reviews`・`vocabularies`・`form_specs` は 3 つとも載っているので、
    **仮説だけが例外**——規約 14 が名指しで禁じている形そのもの。

    しかも 2026-09-12 に**この関門を 3 回きつくした**（`supersedes` の表記・
    `supersede_reason` の条件付き必須・予測の観測単位）。**断られる回数を
    増やしておいて、断り文句だけ空にしていた。**

    実地で踏まれている——運用セッションは仮説 4 件を**すべて
    `topic_models.py` を直接読んで書いた**。手順書
    （`docs/手順_LLM_トピック選定.md:85`）の「**ソースを読む必要はありません**」
    が、仮説の棚だけ守られていなかった。
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    proc = _register({"code": "x"})
    assert proc.returncode != 0
    out = _json(proc)

    assert "Hypothesis" in out["expected_schema"], \
        "**断り文句が空**（規約 14 の (2) が入っていない）"
    shape = out["expected_schema"]["Hypothesis"]
    # **値域が入っていること。** 項目名だけでは、結局ソースを読む羽目になる。
    assert shape["kind"] == list(models.HYPOTHESIS_KINDS)
    assert set(shape["sample_design"]) == set(models.SAMPLE_DESIGNS), \
        "B/M/U の意味が渡っていない（ここを取り違えたのが実際の事故）"
    assert shape["sources"][0]["tier"] == list(models.EVIDENCE_TIERS)
    assert shape["sources"][0]["date_kind"] == list(models.DATE_KINDS)
    assert shape["sources"][0]["date_precision"] == list(models.DATE_PRECISIONS)


@pytest.mark.parametrize("payload,where", [
    ({"code": "x"}, "項目が足りません"),
    ({}, "項目が足りません"),
])
def test_空や欠損で断るときも形を返す(thth_root, payload, where):
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    proc = _register(payload)
    assert proc.returncode != 0
    out = _json(proc)
    assert where in out["error"]["message"]
    assert "Hypothesis" in out["expected_schema"]


def test_観測単位の断りに観測の形を返さない(thth_root):
    """**「観測単位が食い違っています」に `TopicObservation` の形を返していた**
    （語の重なり）。**断り文句に、関係のない形を渡すほうが質が悪い。**"""
    from thth import topic_cli
    import contextlib, io as _io, json as _json

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        topic_cli._fail("schema_error",
                         "predictions[0] の指標が、同じ観測単位にそろいません")
    got = _json.loads(buf.getvalue())["expected_schema"]

    assert list(got) == ["Hypothesis"], f"関係のない形を返している: {list(got)}"


# --- 断り文句の型を、文言から推測しない（外部レビュー・2026-09-12）----------

@pytest.mark.parametrize("label,over", [
    ("claim が空文字", {"claim": ""}),
    ("kind が値域外", {"kind": "bad"}),
    ("state が値域外", {"state": "bad"}),
    ("scope が空文字", {"scope": ""}),
    ("created_at が時刻でない", {"created_at": "bad"}),
    ("sources が空配列", {"sources": []}),
    ("refutation が空", {"refutation": ""}),
    ("counter_hypothesis が空", {"counter_hypothesis": ""}),
    ("verifier が空", {"verifier": ""}),
    # **入力値が案内先を決めてしまっていた**——`kind` に他の型の名前を書くと、
    # その型の形が返っていた。**断られた人が、自分の書いた値のせいで別の型の
    # 説明を読まされる。**
    ("kind に他の型の名前", {"kind": "TopicObservation"}),
    ("kind に『記事』", {"kind": "記事"}),
    # 断り文句は**入力値をそのまま引用する**ので、値に他の型の語が入ると
    # 引用ごしに誤選択が起きる（`created_at` は値を引用して断る）。
    ("created_at に『観測』", {"created_at": "観測した日"}),
    ("sample_design に『記事』", {"sample_design": "記事"}),
])
def test_値が不正なときも仮説の形だけを返す(thth_root, label, over):
    """**外部レビューの独立反例。** こちらのテストは 4 件のうち 3 件が
    「必須項目が足りない」経路しか見ておらず、**正常な項目集合を渡して値だけが
    不正な経路**と、**引用された入力文字列による誤選択**を確認できていなかった。

    `_fail()` は**エラー文言のキーワード一致**で形を選んでいた。通常の値エラーには
    型の語が入らないので**空の形しか返らず**、入力値に他の型の名前が入っていると
    **その型の形が返っていた。**
    """
    from tests.test_hypotheses import _register
    from tests.test_review_cli import _json

    proc = _register(_hypothesis(**over))
    assert proc.returncode != 0, f"{label}: 断られていない"
    out = _json(proc)
    assert list(out["expected_schema"]) == ["Hypothesis"], \
        f"{label}: 返った形が {list(out['expected_schema'])}"


def test_他のコマンドの断り方は変えていない(thth_root):
    """**指定があるときだけ推測をやめる。** ほかのコマンドの既存出力は維持する
    （今回の閉鎖条件にそう書かれている）。"""
    from tests.test_review_cli import _json, _thth

    proc = _thth(["topics", "record-vocabulary", "--json-stdin", "--by", "t"],
                  {"name": "x"})
    assert proc.returncode != 0
    assert list(_json(proc)["expected_schema"]) == ["ReasonVocabulary"]


def test_案内どおりにnoteを書けば通る(thth_root):
    """**案内が実装と違っていた。** shape は `note` を「省略可」と書いていたが、
    `SOURCE_KEYS` に `note` が入っていて `_require()` が省略を拒む——
    **案内どおりに書くと断られた。** キーは必須・値は null 可。"""
    from tests.test_hypotheses import _register, _source
    from tests.test_review_cli import _json
    from thth import topic_models as tm

    shape = tm.HYPOTHESIS_SHAPE["Hypothesis"]["sources"][0]
    assert "省略可" not in shape["note"], "実装と違う案内を出している"

    source = dict(_source()); source["note"] = None
    assert _register(_hypothesis(sources=[source])).returncode == 0

    # キーごと省略したら、断りに仮説の形が返る（**次にできることを渡す**）。
    missing = {k: v for k, v in _source().items() if k != "note"}
    proc = _register(_hypothesis(sources=[missing]))
    assert proc.returncode != 0
    out = _json(proc)
    assert "note" in out["error"]["message"]
    assert list(out["expected_schema"]) == ["Hypothesis"]
