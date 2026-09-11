"""一覧の射影が、読み手に「無い」と「空」を混ぜさせていた（運用指摘 2026-09-12）。

運用セッションが語彙の一覧を `len(row.get("entries") or [])` で数えて **0** になり、
「空の語彙が 1 件ある」と誤報した。実際は `entries` というキーを一覧が返していない
だけで、`reason_ids` が隣に 8 件並んでいた。**読み手の誤読だが、道具がそう読める
形で出していた。**

**同じ根が 3 回目。** `missing_account_daily_metrics`（日次の台帳が無いのと欠測）・
`missing_post_metrics`（投稿ゼロと欠測）に続いて、**道具が自分の出力で同じことを
していた。**

探すと 3 箇所あり、`form_spec` はもっと悪い形だった。

  reviews       findings → **キーごと無い**（`reason_ids` に化ける）
  vocabularies  entries  → **キーごと無い**（`reason_ids`・`states` に化ける）
  form_specs    roles    → **同じ名前のまま、中身が `role_id` の文字列配列**

**止まり方が違う。** 前 2 つは `r["findings"]` と書いた時点で `KeyError`。
`form_specs` は `r["roles"]` が通り、`r["roles"][0]` も通り、**文字列に
`["role_id"]` を当てる最後の一手で `TypeError`** になる。

**【訂正・2026-09-12】ここを「止まらずに壊れる」と 3 回書いた**（commit・依頼書・
このファイル）。**不正確。** 外部レビューの指摘——「`roles` が文字列配列なら
**最後の文字列キー参照で TypeError になる**」。そのとおり。

**正確に言えるのは `len()` の側だけ。** `len(r["roles"])` は**型の違いを見抜けず、
数だけたまたま合う**ので、**数えるぶんには気づけない。** 危ないのはここ。

**しかも `form_spec` は一覧と登録の応答の 2 箇所**で同じ文字列配列を返していた。
単体取得だけが object の配列なので、**3 つのうち 2 つが一致していて、その 2 つが
間違っている**——突き合わせても気づけない。**最初に踏むのは登録した人。**

**今夜見つけたもののうち、これだけがまだ誰も踏んでいない**（`form_spec` の棚が
空だったため）。
"""
from __future__ import annotations

from tests.test_review_cli import _json, _thth, VOCABULARY


def test_語彙の一覧は件数を直接返す(thth_root):
    proc = _thth(["topics", "record-vocabulary", "--json-stdin", "--by", "t"],
                  VOCABULARY)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    row = _json(_thth(["topics", "vocabulary"]))["vocabularies"][0]

    assert row["entry_count"] == len(VOCABULARY["entries"]), \
        "**数えたい読み手が、配列を数えずに済まない**"
    assert len(row["reason_ids"]) == row["entry_count"]
    # `entries` は返さない——**返さないなら、数を別に渡す。**
    assert "entries" not in row


def test_検収の一覧も件数を直接返す(isolated_account, thth_root):
    """`findings` も一覧では返らない。**同じ扱いに揃える。**"""
    from tests.conftest import write_queue_file
    from tests.test_review_cli import (
        BODY, _finding, _record, _register_vocabulary, _review)

    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "roast.md",
                                 body=BODY, fm_overrides={"status": "draft"}))
    proc = _record(_review(vocabulary_id, findings=[
        _finding(note="浅煎りの焙煎度の範囲が無い"),
        _finding("unsupported_claim", note="裏付けが無い"),
    ]), ["--draft", path])
    assert proc.returncode == 0, proc.stdout + proc.stderr

    listed = _json(_thth(["topics", "review", isolated_account["name"]]))
    row = listed["reviews"][0]

    assert "findings" not in row
    assert row["finding_count"] == 2
    assert row["finding_count"] == len(row["reason_ids"])


def test_判断の一覧から候補比較と記事証拠へ辿れる(isolated_account, thth_root):
    """**一覧は ID と件数を返し、本体は単体取得へ**——外部レビューが承認した
    原則（2026-09-12）。`proposal_id`（候補比較）と `article_id`（記事証拠）は
    **まさにその「ID」で、本体ではない**のに落ちていた。

    **一覧からは候補比較にも記事証拠にも辿れず**、decision を単体取得し直す
    必要があった。運用セッションが `topics` / `observations` / `proposals` を
    横断で見たときに残った 1 箇所（`decisions`）を、こちらで確かめて見つけた。
    """
    import json as _json_mod
    from tests.test_topic_cli import _queue, _suggest
    from tests.test_topic_advice import candidate, make_article, make_observation

    path = _queue(isolated_account, name="decide.md")
    article = make_article()
    obs = make_observation("コーヒー")
    proc = _thth(["topics", "observe", "--json-stdin", "--by", "t"], obs)
    assert proc.returncode == 0, proc.stderr
    observation_id = _json(proc)["observation_id"]

    suggested = _json_mod.loads(_suggest(path, {"article": article}).stdout)
    proc = _thth(["topics", "record-decision", "--json-stdin", "--by", "t"], {
        "draft_path": path, "context_id": suggested["context_id"],
        "article": {k: v for k, v in article.items()
                     if k not in ("article_id", "content_sha256", "schema_version")},
        "proposal": {
            "context_id": suggested["context_id"], "prompt_version": "t",
            "intended_reader": "コーヒーを淹れる人", "article_value": "精製の違い",
            "post_angle": "同じ豆でも変わる",
            "candidates": [candidate("コーヒー", [observation_id])],
            "selected_topic": "コーヒー", "selection_reason": "記事の主題",
        }})
    assert proc.returncode == 0, proc.stdout + proc.stderr

    listed = _json(_thth(["topics", "decision", isolated_account["name"]]))
    row = listed["decisions"][0]

    assert row["proposal_id"], "候補比較へ辿る ID が一覧から落ちている"
    assert row["article_id"], "記事証拠へ辿る ID が一覧から落ちている"
    # **本体は返さない。** 承認された原則の後半（「本体は単体取得へ」）を、
    # こちら側からも固定する——**足しすぎを止めるテスト**（2026-09-12・
    # 変異 AJ で、この assert が無いと「候補本体まで返す」が通ってしまった）。
    for body in ("candidates", "proposal", "article", "context", "evidence"):
        assert body not in row, f"一覧が本体（{body}）まで返している"

    # **辿れることまで確かめる**（ID があっても引けなければ意味がない）。
    detail = _json(_thth(["topics", "decision", row["decision_id"]]))
    assert detail["proposal_id"] == row["proposal_id"]
    assert detail["article_id"] == row["article_id"]
