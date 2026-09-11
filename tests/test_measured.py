"""実測を台帳から機械的に並べる口（`thth/measured.py`・T5）。

運用の担当が VM の台帳（ndjson）を目で追って実測表を作っていた結果、一晩で
2 回、読み違いが起きた——

  1. 返信の台帳の行（`marks: [1, 6]` が同居）を、views の行と取り違えた
  2. `お茶` の 6 時間値（views 42）が既に入っていたのを見落とし、
     「未取得」と報告した

現物を目で追うのも十分に間違える。ここではその機械的に並べる口を確かめる。
**読むだけ。何も書かない。**
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
    直接読むだけで、同期は問わない）。"""
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
         "posted_at": "2026-09-10T10:00:00+09:00",
         "collected_at": "2026-09-10T11:03:00+09:00", "age_hours": 1.05,
         "marks": [1], "metrics": {"views": 10}},
        {"post_id": "POST1", "file": "POST1.md", "topic": "お茶",
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
         "collected_at": "2026-09-11T11:00:00+09:00", "age_hours": 25.0,
         "marks": [1, 6], "metrics": {"views": 5}},
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
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
    """front-matter の `form` を引ける。対応する queue ファイルが無ければ `None`。"""
    _write_queue_file(isolated_account, "POST1.md", post_id="POST1", form="相談形式")
    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])
    _write_ndjson(_insight_path(isolated_account, post_id="POST2"), [
        {"post_id": "POST2", "file": "POST2_無い.md", "topic": None,
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    result = measured_mod.load(isolated_account["name"])
    by_id = {p["post_id"]: p for p in result["posts"]}

    assert by_id["POST1"]["form"] == "相談形式"
    assert by_id["POST2"]["form"] is None, "取れなければ null（判らないものを判らないと言う）"


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
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1, "shares": 3}},
    ])
    _write_ndjson(_insight_path(account_b, post_id="POST_B"), [
        {"post_id": "POST_B", "file": "POST_B.md", "topic": "B のトピック",
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


def test_CLIのpostフィルタは指定した投稿だけに絞る(isolated_account, capsys):
    from thth import cli as cli_mod

    _write_ndjson(_insight_path(isolated_account, post_id="POST1"), [
        {"post_id": "POST1", "file": "POST1.md", "topic": None,
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])
    _write_ndjson(_insight_path(isolated_account, post_id="POST2"), [
        {"post_id": "POST2", "file": "POST2.md", "topic": None,
         "collected_at": "2026-09-10T11:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 1}},
    ])

    args = argparse.Namespace(account=isolated_account["name"], post="POST1", json=True)
    rc = cli_mod.cmd_measured(args)
    captured = capsys.readouterr()

    assert rc == 0
    printed = json.loads(captured.out)
    assert [p["post_id"] for p in printed["posts"]] == ["POST1"]
