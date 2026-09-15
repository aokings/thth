"""採取が `runs` に 1 行残る（`action: "collect"`・引継ぎ 2026-09-15 §3-D）。

**なぜ要るか。** `thth run` は投稿の行を `runs` に書くが、**採取は何も書いて
いなかった**。だから「この刻みで採取が走ったのか、走らなかったのか」を後から
見分けられず、手で打った `thth collect` に至っては台帳に痕跡が残らなかった。
`thth maintain` が `action: "maintain"` を毎回書くのと同じ扱いにする。

固定するのは 4 つ:

  1. `thth collect <account>` を**手で打つ**と `action: "collect"`・
     `trigger: "manual"` の行が残る。
  2. `thth run` の中の採取は `trigger: "run"`（**timer 経由と手打ちを区別できる**）。
  3. 見に行った投稿の本数が `collected` に入る（**「そこまで行かなかった」は
     `null`**——0 と混ぜない）。
  4. 失敗（token が無い等）でも行は残り、`status: "error"` と理由が付く。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.conftest import init_git_pair, make_queue_text, run_thth
from tests.test_collect import FakeAdapter
from thth import accounts as accounts_mod
from thth import collect as collect_mod
from thth import jst
from thth import runs as runs_mod

NOW = datetime.datetime(2026, 9, 10, 12, 0, tzinfo=jst.JST)


def _setup(tmp_path, factory, **overrides):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1",
        "posted_at": "2026-09-10T10:00:00+09:00"}))
    account = factory(repo_dir=pair["work"], production=True, **overrides)
    return pair, account


def _collect_rows(account_name):
    state_dir = accounts_mod.state_dir_for(account_name)
    return [r for r in runs_mod.read_runs(state_dir) if r.get("action") == "collect"]


def test_採取は毎回runsに1行残す(tmp_path, thth_root, isolated_account_factory):
    _pair, account = _setup(tmp_path, isolated_account_factory)
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None,
                             trigger=collect_mod.TRIGGER_MANUAL)
    rows = _collect_rows(account["name"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["account"] == account["name"]
    assert row["action"] == "collect" and row["mode"] == "collect"
    assert row["trigger"] == "manual"
    assert row["status"] == "ok" and row["error"] is None
    assert row["collected"] == 1, "見に行った投稿の本数"
    assert row["run_id"].startswith("collect-")
    # **必須 11 項目の形は投稿の行と同じ**（読み手が 1 つの形だけを知ればよい）。
    assert set(runs_mod.RUNS_FIELDS) <= set(row)


def test_手で打ったcollectはtrigger_manual(tmp_path, thth_root, isolated_account_factory):
    """**CLI から**打った場合（`cmd_collect`）。ここが元の欠落。"""
    _pair, account = _setup(tmp_path, isolated_account_factory)
    r = run_thth(["collect", account["name"]])
    assert r.returncode in (0, 1, 2), r.stdout + r.stderr
    rows = _collect_rows(account["name"])
    assert len(rows) == 1, rows
    assert rows[0]["trigger"] == "manual", rows[0]


def test_runの中の採取はtrigger_run(tmp_path, thth_root, isolated_account_factory):
    """timer が呼ぶ形（`thth run`）は `"run"`。**手打ちと見分けられる。**"""
    _pair, account = _setup(tmp_path, isolated_account_factory)
    r = run_thth(["run", account["name"]])
    assert r.returncode == 2, r.stdout + r.stderr   # token が無いので投稿しない
    # 投稿が止まるので採取にも来ない（`cmd_run` は token が無ければ手前で返す）。
    assert _collect_rows(account["name"]) == []

    # 採取の側だけを `thth run` と同じ名乗りで呼ぶ。
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None, trigger=collect_mod.TRIGGER_RUN)
    rows = _collect_rows(account["name"])
    assert [row["trigger"] for row in rows] == ["run"], rows


def test_名乗らなければtriggerはnull(tmp_path, thth_root, isolated_account_factory):
    """**判らないことを「手で打った」にしない。**"""
    _pair, account = _setup(tmp_path, isolated_account_factory)
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None)
    assert _collect_rows(account["name"])[0]["trigger"] is None


def test_採れなかった回も行が残る(tmp_path, thth_root, isolated_account_factory):
    """token が無い＝**そこまで行かなかった**（`collected` は null・0 ではない）。"""
    _pair, account = _setup(tmp_path, isolated_account_factory)
    rc = collect_mod.run_collect(account["name"], now=NOW, log=lambda _l: None,
                                  trigger=collect_mod.TRIGGER_MANUAL)
    assert rc == 2
    rows = _collect_rows(account["name"])
    assert len(rows) == 1, rows
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "no_token"
    assert rows[0]["collected"] is None, "見に行っていないので 0 ではない"


def test_台帳を引けない名前では書かない(tmp_path, thth_root, isolated_account_factory):
    """置き場が決まらないので何も残さない（**採取の失敗は log で言う**）。"""
    isolated_account_factory()      # accounts ディレクトリだけ作る
    rc = collect_mod.run_collect("そんなアカウントは無い", now=NOW,
                                  log=lambda _l: None,
                                  trigger=collect_mod.TRIGGER_MANUAL)
    assert rc == 2
    root = accounts_mod.thth_root()
    state = os.path.join(root, "state", "そんなアカウントは無い")
    assert not os.path.isdir(state) or not [
        n for n in os.listdir(state) if n.startswith("runs-")]


def test_採取の行は投稿の行と混ざらない(tmp_path, thth_root, isolated_account_factory):
    """同じ ndjson に並ぶので、`action` で読み分けられることを確かめる。"""
    _pair, account = _setup(tmp_path, isolated_account_factory)
    collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=NOW,
                             log=lambda _l: None,
                             trigger=collect_mod.TRIGGER_MANUAL)
    state_dir = accounts_mod.state_dir_for(account["name"])
    path = runs_mod.path_for(state_dir, jst.month_str(NOW))
    lines = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert [l["action"] for l in lines] == ["collect"]
