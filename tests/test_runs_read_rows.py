"""`runs.append_run()` を読み取りの行も受けられる形に広げる（T5-3）。

いきさつ: `runs.record_minimal()` は投稿の実行専用の 11 項目（`RUNS_FIELDS`）
とは別の形で行を書いていた（T1-2 の妥協・`thread_read`・`where_cli`・
`who_cli` が使う）。ここでは:

  1. `append_run()` の必須を `REQUIRED_FIELDS`（6 個: account・run_id・
     mode・action・status・error）に縮め、残り（file・post_id・collected・
     refreshed・quota）は無ければ `None` で書く——出力の行の形
     （`RUNS_FIELDS` の 11 個）は変えない。
  2. `record_minimal()` はその `append_run()` を呼ぶ薄い口になった。
     `mode`・`run_id` を呼ぶ側が渡さなくても、`mode: "read"`・
     `run_id: "<action>-<ISO>"` を補ってから渡す。
  3. 禁止語（`engagements.FORBIDDEN_KEYS`）の網はそのまま
     （`record_minimal()` が書く前に検査する）。
  4. 古い行（11 項目そのまま・`mode` が `rehearsal`/`production`）は
     `read_runs()` で今までどおり読める（互換）。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from thth import engagements as engagements_mod
from thth import jst
from thth import runs as runs_mod

NOW = datetime.datetime(2026, 9, 16, 10, 0, tzinfo=jst.JST)


def test_append_runの必須は6項目だけ(tmp_path):
    """投稿の 11 項目を渡さなくても、6 個（account・run_id・mode・action・
    status・error）さえあれば書ける——残り 5 個は `None` で埋まる。"""
    state_dir = str(tmp_path / "state")
    path = runs_mod.append_run(state_dir, {
        "account": "demo-threads", "run_id": "where_to_appear-2026-09-16T10:00:00+09:00",
        "mode": "read", "action": "where_to_appear", "status": "ok", "error": None,
    }, "2026-09")
    with open(path, encoding="utf-8") as f:
        row = json.loads(f.readline())
    assert set(runs_mod.RUNS_FIELDS) <= set(row)
    for k in ("file", "post_id", "collected", "refreshed", "quota"):
        assert row[k] is None, (k, row)
    assert row["mode"] == "read"
    assert row["action"] == "where_to_appear"


def test_append_runは必須が足りないとValueError(tmp_path):
    """6 個のうち 1 つでも欠けたら書かずに `ValueError`（作法 5・loud reject）。
    ここは元の 11 項目版と同じ検査の形——必須が縮んだだけで、検査そのものは
    残っている（変異テストの対象）。
    """
    state_dir = str(tmp_path / "state")
    with pytest.raises(ValueError) as exc:
        runs_mod.append_run(state_dir, {
            "account": "demo-threads", "run_id": "r1",
            "mode": "read", "action": "where_to_appear", "status": "ok",
            # "error" が無い。
        }, "2026-09")
    assert "error" in str(exc.value)
    assert not os.path.exists(runs_mod.path_for(state_dir, "2026-09"))


def test_record_minimalはmodeとrun_idを補ってappend_runを呼ぶ(tmp_path, monkeypatch):
    """呼ぶ側は `action`・`account`・`status`・`error` と call 固有の項目
    （例: `words`・`n`）だけ渡せばよい。`mode: "read"`・
    `run_id: "<action>-<ISO>"` はここで補う。
    """
    state_dir = str(tmp_path / "state" / "demo-threads")
    monkeypatch.setattr(runs_mod.accounts_mod, "state_dir_for", lambda name: state_dir)

    path = runs_mod.record_minimal("demo-threads", {
        "action": "where_to_appear", "account": "demo-threads",
        "words": ["コーヒー"], "n": 3, "status": "ok", "error": None,
    }, now=NOW)
    with open(path, encoding="utf-8") as f:
        row = json.loads(f.readline())

    assert row["mode"] == "read"
    assert row["run_id"] == f"where_to_appear-{jst.iso(NOW)}"
    assert row["words"] == ["コーヒー"]
    assert row["n"] == 3
    # 出力の形（11 項目）は変わらない——渡していない 5 個は None で埋まる。
    assert set(runs_mod.RUNS_FIELDS) <= set(row)
    for k in ("file", "post_id", "collected", "refreshed", "quota"):
        assert row[k] is None, (k, row)


def test_record_minimalは呼ぶ側がmodeとrun_idを渡してもそのまま使う(tmp_path, monkeypatch):
    """既に `mode`・`run_id` を渡している呼び出し元（将来の拡張）を壊さない
    ——補うのは**無いときだけ**。"""
    state_dir = str(tmp_path / "state" / "demo-threads")
    monkeypatch.setattr(runs_mod.accounts_mod, "state_dir_for", lambda name: state_dir)

    path = runs_mod.record_minimal("demo-threads", {
        "action": "where_to_appear", "account": "demo-threads",
        "mode": "read", "run_id": "custom-id-1", "status": "ok", "error": None,
    }, now=NOW)
    with open(path, encoding="utf-8") as f:
        row = json.loads(f.readline())
    assert row["run_id"] == "custom-id-1"


def test_record_minimalは禁止語が混ざれば書かずに例外(tmp_path, monkeypatch):
    """**禁止語の網はそのまま**（T5-3 で変えていない・変異テストの対象）。
    1 バイトも書かない——ファイルごと出来ない。
    """
    state_dir = str(tmp_path / "state" / "demo-threads")
    monkeypatch.setattr(runs_mod.accounts_mod, "state_dir_for", lambda name: state_dir)
    forbidden_key = sorted(engagements_mod.FORBIDDEN_KEYS)[0]

    with pytest.raises(RuntimeError):
        runs_mod.record_minimal("demo-threads", {
            "action": "where_to_appear", "account": "demo-threads",
            "status": "ok", "error": None, forbidden_key: "混入",
        }, now=NOW)
    assert not os.path.exists(runs_mod.path_for(state_dir, jst.month_str(NOW)))


def test_read_runsは古い11項目の行をそのまま読める(tmp_path):
    """互換: T5-3 より前に書かれた行（`mode` が `production`・11 項目ちょうど）
    を `read_runs()` が今までどおり読める。"""
    state_dir = str(tmp_path / "state" / "demo-threads")
    os.makedirs(state_dir, exist_ok=True)
    old_row = {
        "account": "demo-threads", "run_id": "old-run-1", "mode": "production",
        "action": "post", "file": "docs/sns/queue/x.md", "post_id": "123",
        "collected": None, "refreshed": False, "quota": None,
        "status": "ok", "error": None,
    }
    with open(runs_mod.path_for(state_dir, "2026-08"), "w", encoding="utf-8") as f:
        f.write(json.dumps(old_row, ensure_ascii=False) + "\n")

    rows = runs_mod.read_runs(state_dir)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "old-run-1"
    assert rows[0]["mode"] == "production"
    assert rows[0]["post_id"] == "123"


def test_readモードの行は投稿の行と混ざらない(tmp_path, monkeypatch):
    """`mode: "read"` の行は `mode` が `rehearsal`/`production` の投稿の行と
    同じファイルに並んでも、`mode`（か `action`）で区別できる——投稿の集計を
    書く側が `mode in ("rehearsal", "production")` で絞ればそのまま除ける
    （発注 T5-3「投稿の集計に混ざらないことをテストで固定」）。
    """
    state_dir = str(tmp_path / "state" / "demo-threads")
    os.makedirs(state_dir, exist_ok=True)
    post_row = {
        "account": "demo-threads", "run_id": "post-run-1", "mode": "production",
        "action": "post", "file": "docs/sns/queue/x.md", "post_id": "123",
        "collected": None, "refreshed": False, "quota": None,
        "status": "ok", "error": None,
    }
    with open(runs_mod.path_for(state_dir, jst.month_str(NOW)), "w", encoding="utf-8") as f:
        f.write(json.dumps(post_row, ensure_ascii=False) + "\n")

    monkeypatch.setattr(runs_mod.accounts_mod, "state_dir_for", lambda name: state_dir)
    runs_mod.record_minimal("demo-threads", {
        "action": "where_to_appear", "account": "demo-threads",
        "words": ["コーヒー"], "n": 0, "status": "ok", "error": None,
    }, now=NOW)

    rows = runs_mod.read_runs(state_dir)
    assert len(rows) == 2
    posts = [r for r in rows if r["mode"] in ("rehearsal", "production")]
    reads = [r for r in rows if r["mode"] == "read"]
    assert len(posts) == 1 and posts[0]["run_id"] == "post-run-1"
    assert len(reads) == 1 and reads[0]["action"] == "where_to_appear"
