"""3.6.0 投稿の目的 `goal:` の lint と記録（設計 3.6.0 §A1・§C）。

約束: 目的は 4 語の固定。**承認の指紋に入れない**（承認のあとに書き換えても
`approval_stale` にしない・書き換えは runs と sent に「目的の変更」として残す）。
**本文のメモ（「目的: 誘導」）は読まない**——front-matter の `goal:` だけが正。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from tests.conftest import (approve_via_cli, commit_and_push_path, init_git_pair,
                            make_queue_text, parse_verified, run_thth, write_queue_file)
from tests.test_thread_publish import FakeAdapter, bundle_text
from tests.test_v320_reply_to_file_resolve import RecordingAdapter
from thth import accounts as accounts_mod
from thth import approval as approval_mod
from thth import bundle as bundle_mod
from thth import cli, core, goals, lint as lint_mod, queuefile
from thth import runs as runs_mod
from thth import select as select_mod
from thth import sent as sent_mod
from thth import threadrun, threadthrow

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
MEMO_BODY = "目的: 誘導\nメモ: 記事へ誘導したい\n\n## threads\n\n本文です。\n"
SELECT_CFG = {"media": "threads", "quiet_hours": None, "min_interval_hours": 0,
              "stale_days": 7, "hashtags": False}


# ---------------------------------------------------------------- lint

@pytest.mark.parametrize("value", ["reach", "click", "follow", "reply", None])
def test_目的は4語か無し(isolated_account, value):
    fm = {"status": "draft", "approved_sha": None, **({"goal": value} if value else {})}
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False, fm_overrides=fm)
    assert lint_mod.lint_file(path) == []


@pytest.mark.parametrize("value", ["sales", "Reach", "誘導", "reach click", "none"])
def test_4語以外はgoal_invalidで断る(isolated_account, value):
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False,
                            fm_overrides={"status": "draft", "approved_sha": None, "goal": value})
    problems = lint_mod.lint_file(path)
    assert [p for p in problems if p.startswith("goal_invalid: ")], problems
    assert lint_mod.reason_code(problems) == "goal_invalid"
    assert "goal_invalid" in lint_mod.REASONS


def test_4語以外は承認できない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False,
                            fm_overrides={"status": "draft", "approved_sha": None, "goal": "sales"})
    prepared, problem = cli._prepare_one(path)
    assert prepared is None and "goal_invalid" in problem


def test_正規化は揺れを畳まない():
    assert goals.normalize(None) == goals.NONE
    assert goals.normalize("  ") == goals.NONE
    assert goals.normalize(" follow ") == "follow"
    assert goals.normalize("Follow") is None
    assert goals.normalize("none") is None, "none は書く語ではない（無ければ書かない）"


def test_本文のメモは読まない_lintは通り目的はnone(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False,
                            fm_overrides={"status": "draft", "approved_sha": None},
                            body=MEMO_BODY)
    assert lint_mod.lint_file(path) == []
    assert goals.goal_of(queuefile.parse(path)) == goals.NONE


def test_連投は束に1つ_段には書けない_4語以外は断る(tmp_path, isolated_account):
    good = bundle_text().replace("posts:\n", "goal: follow\nposts:\n")
    bad = bundle_text().replace("posts:\n", "goal: sales\nposts:\n")
    per_segment = bundle_text().replace("  - index: 1\n", "  - index: 1\n    goal: reach\n")
    for name, text in (("good.md", good), ("bad.md", bad), ("seg.md", per_segment)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert not [p for p in lint_mod.lint_file(str(tmp_path / "good.md")) if "goal" in p]
    assert goals.goal_of(bundle_mod.parse_text(good, "good.md")) == "follow"
    bad_problems = lint_mod.lint_file(str(tmp_path / "bad.md"))
    assert lint_mod.reason_code([p for p in bad_problems if "goal" in p]) == "goal_invalid"
    seg_problems = lint_mod.lint_file(str(tmp_path / "seg.md"))
    assert any("知らない項目" in p and "goal" in p for p in seg_problems), seg_problems


# ---------------------------------------------------------------- 指紋に入れない

def test_承認の指紋は目的で変わらない(isolated_account):
    shas = []
    for value in ("reach", "click", None):
        fm = {"status": "draft", "approved_sha": None, **({"goal": value} if value else {})}
        path = write_queue_file(isolated_account["queue_dir"], f"{value}.md", commit=False,
                                fm_overrides=fm)
        prepared, problem = cli._prepare_one(path)
        assert problem is None
        assert prepared["goal"] == (value or goals.NONE)
        shas.append(prepared["approved_sha"])
    assert len(set(shas)) == 1
    assert shas[0] == approval_mod.compute_approved_sha(
        section="本文です。", account="nigamilab-threads", reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00")


def test_承認すると承認の時点の目的を控える_取り消すと消える(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None, "goal": "reach"})
    assert approve_via_cli(path).returncode == 0
    fm = queuefile.parse(path).front_matter
    assert fm["status"] == "approved" and fm["approved_goal"] == "reach"
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    fm = queuefile.parse(path).front_matter
    assert fm["status"] == "draft" and not fm.get("approved_goal")


def test_目的の無い原稿を承認するとnoneを控える(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    assert approve_via_cli(path).returncode == 0
    assert queuefile.parse(path).front_matter["approved_goal"] == goals.NONE


def test_一段目に目的を見せる_digestには入らないと言う(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None, "goal": "click"})
    first = run_thth(["approve", path])
    assert first.returncode == 1
    assert "goal      : click（サイト誘導・digest には入りません" in first.stdout


def test_承認のあとに目的を変えてもapproval_staleにしない(tmp_path):
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(fm_overrides={"goal": "click", "approved_goal": "reach"}),
                    encoding="utf-8")
    qf = parse_verified(str(path))
    result = select_mod.select_one([qf], account_name="nigamilab-threads", account_cfg=SELECT_CFG,
                                   now=NOW, last_post_at=None, recent_texts=set())
    assert result.chosen is qf, [(r.file, r.reason) for r in result.rejections]
    assert goals.change(qf) == {"from": "reach", "to": "click"}


def _publish_account(isolated_account_factory):
    return isolated_account_factory(production=True, min_interval_hours=0)


def _throw(account, adapter, log=None):
    return core.throw_once(account["name"], production_flag=True,
                           adapter_factory=lambda _c, _t: adapter, now=NOW,
                           log=log or (lambda _l: None))


def _ok_runs(account):
    state_dir = accounts_mod.state_dir_for(account["name"])
    return [r for r in runs_mod.read_runs(state_dir) if r["action"] == "post" and r["status"] == "ok"]


def test_承認のあとに目的を変えると出て_runsとsentに変更が残る(isolated_account_factory):
    account = _publish_account(isolated_account_factory)
    path = write_queue_file(account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None, "goal": "reach"})
    assert approve_via_cli(path).returncode == 0
    approved_sha = queuefile.parse(path).front_matter["approved_sha"]
    text = Path(path).read_text(encoding="utf-8").replace("\ngoal: reach", "\ngoal: click")
    assert "approved_goal: reach" in text
    Path(path).write_text(text, encoding="utf-8")
    assert commit_and_push_path(path, message="目的を click に")

    adapter, lines = RecordingAdapter(), []
    result = _throw(account, adapter, lines.append)
    assert result.exit_code == 0 and result.action == "post", result.message
    assert len(adapter.posts) == 1
    assert any(line.startswith("目的の変更: reach → click") for line in lines), lines

    sent = sent_mod.read(accounts_mod.state_dir_for(account["name"]), result.post_id)
    assert sent["goal"] == "click"
    assert sent["goal_change"] == {"from": "reach", "to": "click"}
    assert sent["approved_fingerprint"] == approved_sha, "指紋は承認のときのまま"
    row = _ok_runs(account)[-1]
    assert row["goal"] == "click" and row["goal_change"] == {"from": "reach", "to": "click"}


def test_目的を変えなければ変更は残らない(isolated_account_factory):
    account = _publish_account(isolated_account_factory)
    write_queue_file(account["queue_dir"], "a.md",
                     fm_overrides={"goal": "follow", "approved_goal": "follow"})
    result = _throw(account, RecordingAdapter())
    assert result.action == "post", result.message
    sent = sent_mod.read(accounts_mod.state_dir_for(account["name"]), result.post_id)
    assert sent["goal"] == "follow" and "goal_change" not in sent
    row = _ok_runs(account)[-1]
    assert row["goal"] == "follow" and "goal_change" not in row


def test_本文のメモから目的を拾わない_記録はnone(isolated_account_factory):
    account = _publish_account(isolated_account_factory)
    write_queue_file(account["queue_dir"], "a.md", body=MEMO_BODY)
    result = _throw(account, RecordingAdapter())
    assert result.action == "post", result.message
    sent = sent_mod.read(accounts_mod.state_dir_for(account["name"]), result.post_id)
    assert sent["goal"] == goals.NONE
    assert _ok_runs(account)[-1]["goal"] == goals.NONE
    assert goals.recorded_goals(account["name"]) == {result.post_id: goals.NONE}


def test_承認の時点の目的が分からない原稿は変更と言わない(isolated_account_factory):
    """3.6.0 より前の承認（approved_goal を持たない）に目的を足しても変更は作らない。"""
    account = _publish_account(isolated_account_factory)
    write_queue_file(account["queue_dir"], "a.md", fm_overrides={"goal": "reply"})
    result = _throw(account, RecordingAdapter())
    sent = sent_mod.read(accounts_mod.state_dir_for(account["name"]), result.post_id)
    assert sent["goal"] == "reply" and "goal_change" not in sent


def test_sentの記録は知らない語を断る(tmp_path):
    with pytest.raises(ValueError):
        sent_mod.write(str(tmp_path), post_id="1", text="x", body_hash="h", sent_at="t",
                       goal="sales")
    with pytest.raises(ValueError):
        sent_mod.write(str(tmp_path), post_id="1", text="x", body_hash="h", sent_at="t",
                       goal="click", goal_change={"from": "reach", "to": "follow"})


# ---------------------------------------------------------------- 連投

def test_連投の目的は段の実行記録に残り_変更も残る(tmp_path, isolated_account_factory):
    text = bundle_text().replace("posts:\n", "goal: follow\napproved_goal: reach\nposts:\n")
    pair = init_git_pair(tmp_path, seed_content=text, seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       quiet_hours=None, min_interval_hours=0)
    adapter = FakeAdapter()
    now = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")
    results = threadthrow.publish_bundle(account["name"], "docs/sns/queue/thread.md",
                                         adapter_factory=lambda *_: adapter, now=now)
    assert [r.action for r in results] == ["published"] * 3, [r.reason for r in results]
    run = threadrun.load(results[0].run_id)
    first = run["posts"][0]
    assert first["goal"] == "follow"
    assert first["goal_change"] == {"from": "reach", "to": "follow"}
    assert goals.recorded_goals(account["name"])[first["post_id"]] == "follow"
