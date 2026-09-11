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

3 番目は**止まらずに壊れる。** 前 2 つは `r["findings"]` と書けば `KeyError` で
止まるが、`r["roles"][0]["role_id"]` は `TypeError` になるまで進み、`len()` は
たまたま合うので**数えるぶんには気づけない。**

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
