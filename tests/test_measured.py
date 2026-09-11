"""実測を台帳から機械的に並べる口（`thth/measured.py`・T5）。

運用の担当が VM の台帳（ndjson）を目で追って実測表を作っていた結果、一晩で
2 回、読み違いが起きた——

  1. 返信の台帳の行（`marks: [1, 6]` が同居）を、views の行と取り違えた
  2. `お茶` の 6 時間値（views 42）が既に入っていたのを見落とし、
     「未取得」と報告した

現物を目で追うのも十分に間違える。ここではその機械的に並べる口を確かめる。
**読むだけ。何も書かない。**

外部レビュー再判定 M3・M4・R3（2026-09-12）——**所有 account の根拠付き選別**、
**`form_now`（いまの原稿の値であることの明示）**、そして**所有の根拠を採取
時点のものに絞る**（R3）の閉じる条件をここで確かめる。

投稿の所有は、**台帳の行そのものが持つ `account`**（`thth/collect.py` が
採取時点に書く）でしか判らない。**ここから先の post 系テストは（所有不明を
試したい一部を除き）行に `account` を必ず持たせる**——持たせなければその
投稿は「所有不明」（`posts_unknown_ownership`）に回り、`posts` に出ない。

R3 より前は、行に `account` が無いことを前提に、`file`（採取当時の queue
ファイル名）が指す**現在の**原稿の front-matter `account` を過去の所有として
使っていた。だが原稿の account を書き換えると、過去の台帳が黙って新しい
account の実測へ移し替えられてしまっていた（外部レビュー再判定 R3）。
**現在の原稿を過去の所有の根拠にしない**——`_write_queue_file()` は
`form_now` の確認にだけ使い、所有の根拠には使わない。
"""
from __future__ import annotations

import argparse
import json
import os

from thth import measured as measured_mod


def _insight_path(account, post_id: str = "POST1") -> str:
    return os.path.join(account["repo_dir"], "data", "sns", "insights", "posts",
                        f"{post_id}.ndjson")


def _account_daily_path(account, ym: str = "2026-09") -> str:
    return os.path.join(account["repo_dir"], "data", "sns", "insights", "account",
                        f"{account['name']}-{ym}.ndjson")


def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_queue_file(account, file_name: str, *, post_id: str, form: str | None) -> None:
    """queue ファイルを直接書く（`form` を持たせたいので `make_queue_text()` は
    使わない——`tests/conftest.py` の `FM_ORDER` に `form` が無く、それ経由だと
    落ちてしまうため）。git commit はしない（`measured._form_for()` はディスクを
    直接読むだけで、同期は問わない）。

    **R3 以降、所有の根拠には使われない**（`form_now` の解決にだけ使う）。
    ここに書く `account:` front-matter は「いまの原稿の account」であって、
    台帳の行の所有とは別物——`measured.load()` はもうこれを所有の根拠にしない。
    """
    form_line = f"form: {form}\n" if form is not None else ""
    text = (
        "---\n"
        "thth: 1\n"
        f"account: {account['name']}\n"
        "status: posted\n"
        f"post_id: {post_id}\n"
        "posted_at: 2026-09-10T10:00:00+09:00\n"
        f"{form_line}"
        "---\n"
        "## threads\n\n本文です。\n"
    )
    os.makedirs(account["queue_dir"], exist_ok=True)
    with open(os.path.join(account["queue_dir"], file_name), "w", encoding="utf-8") as f:
        f.write(text)


def test_投稿ごとに時系列が並ぶこと(isolated_account):
    """`age_hours`・`collected_at` は台帳に書いた実値のまま出る（他の値に丸めない）。"""
    _write_ndjson(_insight_path(isolated_account), [
        {"post_id": "POST1", "file": "POST1.md", "topic": "お茶",
         "account": isolated_account["name"],
         "posted_at": "2026-09-10T10:00:00+09:00",
         "collected_at": "2026-09-10T11:03:00+09:00", "age_hours": 1.05,
         "marks": [1], "metrics": {"views": 10}},
        {"post_id": "POST1", "file": "POST1.md", "topic": "お茶",
         "account": isolated_account["name"],
         "posted_at": "2026-09-10T10:00:00+09:00",
         "collected_at": "2026-09-10T16:12:00+09:00", "age_hours": 6.2,
         "marks": [6], "metrics": {"views": 42}},
    ])

    result = measured_mod.load(isolated_account["name"])

    assert len(result["posts"]) == 1
    post = result["posts"][0]
    assert post["post_id"] == "POST1"
    assert post["topic"] == "お茶"
    assert post["posted_at"] == "2026-09-10T10:00:00+09:00"
    rows = post["rows"]
    assert [r["age_hours"] for r in rows] == [1.05, 6.2], "実値のまま・並び順は時系列"
    assert [r["collected_at"] for r in rows] == [
        "2026-09-10T11:03:00+09:00", "2026-09-10T16:12:00+09:00"]
    assert rows[1]["metrics"]["views"] == 42, "既に入っている値を見落としていないか"


def test_刻みが同居している行に印が付く(isolated_account):
    """`marks: [1, 6]` が同居する行には `marks_collapsed: true`。単独の刻みには付かない。"""
    _write_ndjson(_insight_path(isolated_account), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-11T11:00:00+09:00", "age_hours": 25.0,
         "marks": [1, 6], "metrics": {"views": 5}},
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-12T10:00:00+09:00", "age_hours": 48.0,
         "marks": [24], "metrics": {"views": 20}},
    ])

    result = measured_mod.load(isolated_account["name"])
    rows = result["posts"][0]["rows"]
    by_marks = {tuple(r["marks"]): r for r in rows}

    assert by_marks[(1, 6)]["marks_collapsed"] is True, \
        "同居している行に印が付いていない——過去複数時点を復元したと誤読させる"
    assert by_marks[(24,)]["marks_collapsed"] is False, \
        "単独の刻みにまで印が付いている——「n 点測った」の n を水増しする"


def test_一度も現れていない指標がmissing_metricsに出る(isolated_account):
    """`clicks` を含まない台帳では `missing_metrics` に `clicks` が出る。
    値として `0` と混ぜてはいけない——実際に `0` として記録された指標は
    missing に出てはいけない。"""
    _write_ndjson(_insight_path(isolated_account), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 10, "likes": 2, "replies": 0}},
    ])

    result = measured_mod.load(isolated_account["name"])

    assert "clicks" in result["missing_metrics"], "1 度も現れていない指標が出ていない"
    assert "views" not in result["missing_metrics"], "現れている指標を欠けたことにしている"
    assert "replies" not in result["missing_metrics"], \
        "0 として記録された指標を『無い』と混ぜている"
    assert all(isinstance(m, str) for m in result["missing_metrics"]), \
        "missing_metrics は名前の一覧であって、値（0 等）と混ぜてはいけない"


def test_clicksが0として記録されていればmissingに出ない(isolated_account):
    """（上のテストの裏）実際に `clicks: 0` を記録した account では、
    `clicks` は `missing_metrics` に出ない——『無い』と『0 だった』を混同しない。"""
    _write_ndjson(_account_daily_path(isolated_account), [
        {"account": isolated_account["name"], "date": "2026-09-09",
         "collected_at": "2026-09-10T00:05:00+09:00",
         "metrics": {"views": 100, "clicks": 0}},
    ])

    result = measured_mod.load(isolated_account["name"])

    assert "clicks" not in result["missing_metrics"]


def test_壊れたndjsonを実測0件と言わない(isolated_account):
    """壊れた行のあるファイルは `broken` に名前が出て、中身を `posts` に混ぜない。"""
    path = _insight_path(isolated_account, post_id="POST_BROKEN")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"post_id": "POST_BROKEN", "marks": [1], "metrics": {"views": 1}}\n')
        f.write("これは JSON ではない\n")

    result = measured_mod.load(isolated_account["name"])

    assert result["broken"] == ["POST_BROKEN.ndjson"]
    assert all(p["post_id"] != "POST_BROKEN" for p in result["posts"]), \
        "壊れたファイルの中身を posts に混ぜてはいけない"

    # 存在しないファイル（まだ採っていないだけ）は broken に出ない
    empty = measured_mod.load(isolated_account["name"])
    assert "POST_NOTHING_YET.ndjson" not in empty["broken"]


def test_formはqueueのfront_matterから取れる(isolated_account):
    """`form_now` は**いまの queue ファイルの front-matter から引いた値**（M4）。
    所有そのものは行の `account`（R3）で判る——`account` が無い行は所有不明
    として `posts_unknown_ownership` に回り、`posts` には出ない（queue
    ファイルの有無は所有には関係しない）。"""
    _write_queue_file(isolated_account, "POST1.md", post_id="POST1", form="相談形式")
    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])
    # POST2 は `account` を持たない（R3 より前に採取した行、または壊れた採取
    # に相当）——対応する queue ファイルが**あっても**所有不明のまま。
    _write_queue_file(isolated_account, "POST2.md", post_id="POST2", form="別の型")
    _write_ndjson(_insight_path(isolated_account, post_id="POST2"), [
        {"post_id": "POST2", "file": "POST2.md", "topic": None,
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    result = measured_mod.load(isolated_account["name"])
    by_id = {p["post_id"]: p for p in result["posts"]}

    assert by_id["POST1"]["form_now"] == "相談形式"
    assert by_id["POST1"]["form_source"] == "current_draft", \
        "いまの原稿から引いた値だと明示していない"
    assert "form" not in by_id["POST1"], \
        "`form` という名前のままだと、採取時点の値だと誤読させる（M4）"
    assert "POST2" not in by_id, \
        "行に `account` が無く所有が判らないのに posts に出ている（R3）"
    assert result["posts_unknown_ownership"] == ["POST2"], \
        "所有不明は推定で混ぜず、区別して出す約束"


def test_アカウント日次も出る(isolated_account):
    _write_ndjson(_account_daily_path(isolated_account), [
        {"account": isolated_account["name"], "date": "2026-09-09",
         "collected_at": "2026-09-10T00:05:00+09:00",
         "metrics": {"views": 100, "followers_count": 5}},
    ])

    result = measured_mod.load(isolated_account["name"])

    assert result["account_daily"] == [
        {"date": "2026-09-09", "metrics": {"views": 100, "followers_count": 5}}]


def test_別accountの行が混ざらない(isolated_account_factory, tmp_path):
    """`load()` は 1 account だけを扱う。別 account の投稿・指標が混ざってはいけない。"""
    from tests.conftest import init_real_repo

    repo_a = init_real_repo(tmp_path, "account_a")
    repo_b = init_real_repo(tmp_path, "account_b")
    account_a = isolated_account_factory("account-a", repo_dir=repo_a)
    account_b = isolated_account_factory("account-b", repo_dir=repo_b)

    _write_ndjson(_insight_path(account_a, post_id="POST_A"), [
        {"post_id": "POST_A", "file": "POST_A.md", "topic": "A のトピック",
         "account": "account-a",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1, "shares": 3}},
    ])
    _write_ndjson(_insight_path(account_b, post_id="POST_B"), [
        {"post_id": "POST_B", "file": "POST_B.md", "topic": "B のトピック",
         "account": "account-b",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    result_a = measured_mod.load(account_a["name"])

    assert [p["post_id"] for p in result_a["posts"]] == ["POST_A"]
    assert "shares" not in result_a["missing_metrics"], \
        "自分の account の実測から集めた指標のはず"
    result_b = measured_mod.load(account_b["name"])
    assert [p["post_id"] for p in result_b["posts"]] == ["POST_B"]
    assert "shares" in result_b["missing_metrics"], \
        "他 account（A）の shares が B の missing_metrics 判定に混ざっている"


def test_CLIがjsonでloadと同じものを返す(isolated_account, capsys):
    from thth import cli as cli_mod

    _write_ndjson(_insight_path(isolated_account), [
        {"post_id": "POST1", "file": "POST1.md", "topic": "お茶",
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 10}},
    ])

    args = argparse.Namespace(account=isolated_account["name"], post=None, json=True)
    rc = cli_mod.cmd_measured(args)
    captured = capsys.readouterr()

    assert rc == 0
    printed = json.loads(captured.out)
    expected = measured_mod.load(isolated_account["name"])
    assert printed == expected


def test_CLIの人向け出力は同居に印をつけ欠けている指標を出す(isolated_account, capsys):
    from thth import cli as cli_mod

    _write_ndjson(_insight_path(isolated_account), [
        {"post_id": "POST1", "file": "POST1.md", "topic": "お茶",
         "account": isolated_account["name"],
         "collected_at": "2026-09-11T11:00:00+09:00", "age_hours": 25.0,
         "marks": [1, 6], "metrics": {"views": 42}},
    ])

    args = argparse.Namespace(account=isolated_account["name"], post=None, json=False)
    rc = cli_mod.cmd_measured(args)
    captured = capsys.readouterr()

    assert rc == 0
    assert "⚠同居" in captured.out, "刻みが同居している行に目で分かる印が無い"
    assert "25.0h" in captured.out, "age_hours は小数 1 桁まで出す約束"
    assert "clicks" in captured.out, "missing_metrics は必ず出す約束"
    assert "無し" in captured.out, "broken は空でも『無し』と出す約束"
    assert "所有不明: 無し" in captured.out, \
        "所有不明が無いときも、その旨を出す約束（R3）"


def test_CLIのpostフィルタは指定した投稿だけに絞る(isolated_account, capsys):
    from thth import cli as cli_mod

    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])
    _write_ndjson(_insight_path(isolated_account, post_id="POST2"), [
        {"post_id": "POST2", "file": "POST2.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    args = argparse.Namespace(account=isolated_account["name"], post="POST1", json=True)
    rc = cli_mod.cmd_measured(args)
    captured = capsys.readouterr()

    assert rc == 0
    printed = json.loads(captured.out)
    assert [p["post_id"] for p in printed["posts"]] == ["POST1"]


def test_共有repoでも所有accountだけを根拠付きで選別する(isolated_account_factory, tmp_path):
    """2 account が**同じ repo**を使っても、A の出力に B の投稿・日次は
    混ざらない。所有の根拠は**行そのものの `account`**（投稿・R3）とファイル名
    `<account>-<年月>.ndjson`（日次）——queue ファイルの有無・中身は関係ない。"""
    from tests.conftest import init_real_repo

    shared_repo = init_real_repo(tmp_path, "shared")
    account_a = isolated_account_factory("account-a", repo_dir=shared_repo)
    account_b = isolated_account_factory("account-b", repo_dir=shared_repo)

    # 投稿: 同じ posts_dir に、それぞれの行が自分の `account` を持つ
    # （所有の唯一の根拠・R3）。
    _write_ndjson(_insight_path(account_a, post_id="A_POST"), [
        {"post_id": "A_POST", "file": "A_POST.md", "topic": "A のトピック",
         "account": "account-a",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])
    # B の投稿にだけ clicks が付いている——A の missing_metrics から
    # clicks が消えてはいけない（外部レビュー再現の核心）。
    _write_ndjson(_insight_path(account_b, post_id="B_POST"), [
        {"post_id": "B_POST", "file": "B_POST.md", "topic": "B のトピック",
         "account": "account-b",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"clicks": 9}},
    ])

    # 日次: ファイル名に account が刻まれている（`thth/collect.py` の
    # `_collect_account_daily()` と同じ形）。
    _write_ndjson(_account_daily_path(account_a), [
        {"account": "account-a", "date": "2026-09-10",
         "collected_at": "2026-09-11T00:05:00+09:00", "metrics": {"views": 1}},
    ])
    _write_ndjson(_account_daily_path(account_b), [
        {"account": "account-b", "date": "2026-09-10",
         "collected_at": "2026-09-11T00:05:00+09:00", "metrics": {"clicks": 9}},
    ])

    result_a = measured_mod.load("account-a")

    assert [p["post_id"] for p in result_a["posts"]] == ["A_POST"], \
        "B の投稿が A の出力に混ざっている"
    assert result_a["account_daily"] == [
        {"date": "2026-09-10", "metrics": {"views": 1}}], \
        "B の日次（clicks 9）が A の日次に混ざっている"
    assert "clicks" in result_a["missing_metrics"], \
        "B の clicks が A の missing_metrics から消えている——A は一度も clicks を記録していない"

    result_b = measured_mod.load("account-b")
    assert [p["post_id"] for p in result_b["posts"]] == ["B_POST"]
    assert "views" in result_b["missing_metrics"], \
        "A の views が B の missing_metrics 判定に混ざっている"


def test_所有を決められない投稿台帳は不明として分けられる(isolated_account_factory, tmp_path):
    """行に `account` が無い（＝所有 account を判別できない）投稿台帳は、
    **どの account の `posts` にも混ぜず**、`posts_unknown_ownership` に
    分けて出す。混ぜないのと同じくらい、**見えなくもしない**（消えたことに
    しない）。不明な投稿の指標も、どちらの account の missing_metrics 判定にも
    使わない。"""
    from tests.conftest import init_real_repo

    shared_repo = init_real_repo(tmp_path, "shared_unknown")
    account_a = isolated_account_factory("account-a", repo_dir=shared_repo)
    isolated_account_factory("account-b", repo_dir=shared_repo)

    _write_ndjson(_insight_path(account_a, post_id="A_POST"), [
        {"post_id": "A_POST", "file": "A_POST.md", "topic": None,
         "account": "account-a",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])
    # 行に `account` が無い（R3 より前の採取、または壊れた採取に相当）
    # ——所有が判らない。`reposts` はこの行にしか出てこない指標にしておく。
    _write_ndjson(_insight_path(account_a, post_id="GHOST_POST"), [
        {"post_id": "GHOST_POST", "file": "GHOST_無い.md", "topic": None,
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"reposts": 5}},
    ])

    result_a = measured_mod.load("account-a")
    result_b = measured_mod.load("account-b")

    assert [p["post_id"] for p in result_a["posts"]] == ["A_POST"], \
        "所有不明の投稿を A の posts に推定で混ぜている"
    assert result_a["posts_unknown_ownership"] == ["GHOST_POST"], \
        "所有不明を区別して出す約束（消えたことにしない）"
    assert all(p["post_id"] != "GHOST_POST" for p in result_b["posts"]), \
        "所有不明を『他 account のものではない』ことを理由に B にも混ぜてはいけない"
    assert "reposts" in result_a["missing_metrics"], \
        "所有不明の投稿の指標を A の実測に数えてしまっている"


def test_formはform_nowとして明示され過去の型を装わない(isolated_account):
    """M4: 出力は `form` ではなく `form_now`・`form_source: current_draft`。
    採取時点の型は台帳に無いので、それを装った値を出さない。"""
    _write_queue_file(isolated_account, "POST1.md", post_id="POST1", form="相談形式")
    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    result = measured_mod.load(isolated_account["name"])
    post = result["posts"][0]

    assert post["form_now"] == "相談形式"
    assert post["form_source"] == "current_draft"
    assert "form" not in post


def test_原稿が後から編集されても過去の行に現在の型を断定して付けない(isolated_account):
    """M4 の再現そのもの: 採取後に queue ファイルの `form` が書き換わっても、
    `measured.load()` は「いまの原稿の値」だとしか言わない——採取時点の型
    だったかのように断定表示しない（外部レビューの再現物
    `current_queue_rewrites_history_form()` と同じ入力で確かめる）。"""
    _write_queue_file(isolated_account, "POST1.md", post_id="POST1", form="旧型")
    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    before = measured_mod.load(isolated_account["name"])["posts"][0]
    assert before["form_now"] == "旧型"
    assert before["form_source"] == "current_draft", \
        "採取時点の値だと誤読させない印が要る"

    # 原稿を後から編集（同名ファイルの再利用・後編集の両方に相当）。
    _write_queue_file(isolated_account, "POST1.md", post_id="POST1", form="新型")

    after = measured_mod.load(isolated_account["name"])["posts"][0]
    assert after["form_now"] == "新型", \
        "『いまの原稿から引いた値』なので、編集後は現在値に追従するのが筋"
    assert after["form_source"] == "current_draft", \
        "編集後も『いまの原稿の値である』ことは変わらず明示され続けるべき"
    assert "form" not in after, \
        "『新型』を、採取当時の型だったかのように `form` という名前で肯定表示していないか"


# --- R3（2026-09-12）: 所有 account を現在の原稿の account で移し替えない ---


def test_現在の原稿のaccountを過去の所有として使わない(isolated_account_factory, tmp_path):
    """外部レビュー再判定 R3 の再現そのもの
    （`repro_m3_boundaries.py` の `current_queue_account_reassigns_history()`
    と同じ入力）。行に `account` を持たない過去の台帳は、同じ `file` を指す
    queue ファイルの**現在の** account が account-a から account-b に
    変わっても、account-b の posts へは移らない——`account` が無い以上、
    どちらの posts にも入らず「不明」に留まる。"""
    from tests.conftest import init_real_repo

    shared_repo = init_real_repo(tmp_path, "shared_reassign")
    account_a = isolated_account_factory("account-a", repo_dir=shared_repo)
    isolated_account_factory("account-b", repo_dir=shared_repo)

    # 過去の採取行　—　`account` を持たない（R3 より前の schema のまま）。
    _write_ndjson(_insight_path(account_a, post_id="P"), [
        {"post_id": "P", "file": "post.md", "metrics": {"views": 7}},
    ])
    # 現在の原稿は account-b を名乗っている（例: 引き継ぎ・作り直しで書き換わった）。
    # `repro_m3_boundaries.py` の `queue()` と同じ形で直接書く。
    os.makedirs(account_a["queue_dir"], exist_ok=True)
    with open(os.path.join(account_a["queue_dir"], "post.md"), "w", encoding="utf-8") as f:
        f.write("---\nthth: 1\naccount: account-b\nform: current\n---\n## threads\nbody\n")

    a = measured_mod.load("account-a")
    b = measured_mod.load("account-b")

    assert [p["post_id"] for p in a["posts"]] == [], \
        "account-a の過去台帳が消えている——本来は『不明』に残るはず"
    assert [p["post_id"] for p in b["posts"]] == [], \
        "現在の原稿が account-b を名乗っただけで、過去の台帳が account-b の実測へ" \
        "移し替えられている（R3 が閉じていない）"
    assert a["posts_unknown_ownership"] == ["P"]
    assert b["posts_unknown_ownership"] == ["P"], \
        "所有不明はどちらの account から見ても同じ post_id が見える（推定で寄せない）"


def test_accountを持つ行はそのaccountの実測として出る(isolated_account):
    """R3 の表: `thth/collect.py` が書くとおり、行が自分の `account` を持って
    いれば、それがそのまま所有の根拠になる。"""
    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": "お茶",
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 10}},
    ])

    result = measured_mod.load(isolated_account["name"])

    assert [p["post_id"] for p in result["posts"]] == ["POST1"]
    assert result["posts_unknown_ownership"] == []


def test_CLIの人向け出力に所有不明の件数とIDと理由が出る(isolated_account, capsys):
    """外部レビュー再判定 R3 の可視性条件: JSON では `posts_unknown_ownership`
    が返るのに、人向け出力が「実測がありません」だけで終わっていた
    （`repro_m3_boundaries.py` の `human_cli_hides_known_unknown()` が示した
    バグ）。件数・post ID・混ぜていない理由を出す。"""
    from thth import cli as cli_mod

    _write_ndjson(_insight_path(isolated_account, post_id="GHOST"), [
        {"post_id": "GHOST", "file": "gone.md", "metrics": {"views": 3}},
    ])

    args = argparse.Namespace(account=isolated_account["name"], post=None, json=False)
    rc = cli_mod.cmd_measured(args)
    captured = capsys.readouterr()

    assert rc == 0
    out = captured.out
    assert "実測がありません" in out
    assert "所有不明" in out, "所有不明があることが人向け出力に出ていない"
    assert "GHOST" in out, "所有不明の post ID が出ていない"
    assert "1 件" in out, "所有不明の件数が出ていない"


def test_先頭行が古くても裏付けのある行はその実測として出る(isolated_account):
    """**R3 が残した制限を閉じる**（2026-09-12）。

    R3 の実装は 1 ファイルの所有を**先頭行 1 行**で決めていた。先頭行が R3
    より前（`account` 無し）だと、後から `account` 付きの行がいくら足されても
    その投稿は永久に「不明」のまま——実際 VM の kopicha は 6 投稿すべてが
    その状態で、`thth measured` が空で出続けていた。

    行ごとに選別すれば、**裏付けのある行は裏付けのあるまま出せる**。外した
    行は 0 にせず数を出す（`rows_unattributed`）。
    """
    _write_ndjson(_insight_path(isolated_account, post_id="MIXED"), [
        # R3 より前の採取（`account` 無し）。これが先頭にある。
        {"post_id": "MIXED", "file": "mixed.md", "topic": "お茶",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 10}},
        # R3 以降の採取（`account` あり）。
        {"post_id": "MIXED", "file": "mixed.md", "topic": "お茶",
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T16:00:00+09:00", "age_hours": 6.0,
         "marks": [6], "metrics": {"views": 42}},
    ])

    result = measured_mod.load(isolated_account["name"])

    assert [p["post_id"] for p in result["posts"]] == ["MIXED"], \
        "先頭行が古いだけで、裏付けのある行まで捨てている"
    assert result["posts_unknown_ownership"] == [], \
        "裏付けのある行があるのに投稿ごと不明へ回している"
    post = result["posts"][0]
    assert [r["age_hours"] for r in post["rows"]] == [6.0], \
        "裏付けの無い行を推定で混ぜている"
    assert post["rows_unattributed"] == 1, \
        "外した行の数を出していない（系列が 6h から始まったように読める）"


def test_外した行があることが人向け出力に出る(isolated_account, capsys):
    """欠けているものを黙って落とさない。**時系列の頭が欠けているのに、
    そこから始まったかのように読ませない。**"""
    from thth import cli as cli_mod

    _write_ndjson(_insight_path(isolated_account, post_id="MIXED"), [
        {"post_id": "MIXED", "file": "mixed.md", "topic": "お茶",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 10}},
        {"post_id": "MIXED", "file": "mixed.md", "topic": "お茶",
         "account": isolated_account["name"],
         "collected_at": "2026-09-10T16:00:00+09:00", "age_hours": 6.0,
         "marks": [6], "metrics": {"views": 42}},
    ])

    args = argparse.Namespace(account=isolated_account["name"], post=None, json=False)
    rc = cli_mod.cmd_measured(args)
    out = capsys.readouterr().out

    assert rc == 0
    assert "MIXED" in out
    assert "1 行" in out, "外した行数が人向け出力に出ていない"
    assert "始まったのではありません" in out, "系列が途中から始まっている旨が出ていない"


def test_1つの台帳に2つのaccountの行が同居したら壊れとして扱う(
        isolated_account_factory, tmp_path):
    """1 つの post_id が 2 つの account に属することは実際には起こらない。
    起きているならそのファイルは信用できない（取り違え・import/merge・改竄）
    ——**1 行も使わない**。どちらの account の実測にも、不明にも入れない
    （壊れと不明を混ぜない）。"""
    from tests.conftest import init_real_repo

    shared_repo = init_real_repo(tmp_path, "shared_conflict")
    account_a = isolated_account_factory("account-a", repo_dir=shared_repo)
    isolated_account_factory("account-b", repo_dir=shared_repo)

    _write_ndjson(_insight_path(account_a, post_id="CONFLICT"), [
        {"post_id": "CONFLICT", "file": "c.md", "account": "account-a",
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
        {"post_id": "CONFLICT", "file": "c.md", "account": "account-b",
         "collected_at": "2026-09-10T16:00:00+09:00", "age_hours": 6.0,
         "marks": [6], "metrics": {"views": 99}},
    ])

    result_a = measured_mod.load("account-a")
    result_b = measured_mod.load("account-b")

    assert result_a["posts"] == [] and result_b["posts"] == [], \
        "所有が食い違う台帳の行を実測として使っている"
    assert result_a["posts_unknown_ownership"] == [] and \
        result_b["posts_unknown_ownership"] == [], \
        "壊れを『不明』に混ぜている（読めない ≠ 判らない）"
    assert "CONFLICT.ndjson" in result_a["broken"], \
        "信用できない台帳を壊れとして出していない"
