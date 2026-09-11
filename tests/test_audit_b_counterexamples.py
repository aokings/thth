"""B 公開経路の独立反例。実 API は使わず、実 git fixture と FakeAdapter を使う。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests.conftest import approve_via_cli, init_git_pair
from tests.test_thread_publish import (
    FakeAdapter,
    NOW,
    REL,
    SEGMENTS,
    bundle_text,
    publish,
    thread_account,
    write_and_push,
)
from thth import threadrun, threadthrow


def _runs_dir(thth_root: str) -> Path:
    path = Path(thth_root) / "state" / "threads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_jsonとして読めてもrunとして壊れた記録から重複公開しない(
        thth_root, thread_account):
    """1 段公開済みのrunが ``{}`` になり、原稿側の記録も失われた場合。

    JSON parse の成功だけを「読める」としてはならない。承認か公開のどちらかで
    fail-closed なら合格。両方を通ると、同じ原稿を新しいrootとして再公開する。
    """
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    assert len(first_api.calls) == 1
    run_id = first[0].run_id
    run_path = Path(threadrun.run_path(run_id))
    run_path.write_text("{}", encoding="utf-8")

    revised = list(SEGMENTS)
    revised[0] = "公開済みだった先頭段を、記録喪失後に書き直した本文。"
    write_and_push(thread_account["pair"], bundle_text(
        segments=revised,
        status="draft",
        posts=[{"index": 1}, {"index": 2}, {"index": 3}],
    ))

    approved = approve_via_cli(thread_account["path"])
    second_api = FakeAdapter()
    results = []
    if approved.returncode == 0:
        results = publish(thread_account, second_api, max_posts=1)

    print("CORRUPT_OBJECT_APPROVAL", approved.returncode,
          approved.stdout, approved.stderr)
    print("CORRUPT_OBJECT_PUBLISH",
          [(r.action, r.reason, r.run_id) for r in results], second_api.calls)
    assert approved.returncode != 0 or second_api.calls == [], (
        "run schema が壊れた記録を無視し、公開済み原稿を新しいrootとして再公開した"
    )


def test_runs_pathが通常ファイルなら不存在として承認しない(
        thth_root, thread_account):
    """``state/threads`` 不在は正常だが、同名の通常ファイルは破損状態。"""
    state = Path(thth_root) / "state"
    state.mkdir(parents=True, exist_ok=True)
    runs_path = state / "threads"
    runs_path.write_text("not a directory", encoding="utf-8")

    write_and_push(thread_account["pair"], bundle_text(status="draft"))
    approved = approve_via_cli(thread_account["path"])
    api = FakeAdapter()
    publish_outcome = None
    if approved.returncode == 0:
        try:
            publish_outcome = publish(thread_account, api, max_posts=1)
        except Exception as exc:  # 例外型とAPI呼出有無自体が観測対象
            publish_outcome = f"{type(exc).__name__}: {exc}"

    print("BROKEN_RUNS_DIR_APPROVAL", approved.returncode,
          approved.stdout, approved.stderr)
    print("BROKEN_RUNS_DIR_PUBLISH", publish_outcome, api.calls)
    combined = approved.stdout + approved.stderr
    assert approved.returncode != 0 and "実行記録" in combined, (
        "破損した runs path を『記録なし』と扱って連投承認を通した"
    )


def test_別account由来でも構文破損runは全連投を止める(
        thth_root, thread_account):
    """壊れた内容からaccountを確定できないので、別account想定でも全停止する。"""
    path = _runs_dir(thth_root) / "run-other-account.json"
    path.write_text('{"account":"other-threads",', encoding="utf-8")
    api = FakeAdapter()
    results = publish(thread_account, api, max_posts=1)
    print("FOREIGN_CORRUPT_RUN", [(r.action, r.reason) for r in results], api.calls)
    assert api.calls == []
    assert results[0].action == "stopped"
    assert "全 account" in results[0].reason


def test_run内accountを書き換えても続行しない(thth_root, thread_account):
    """同じrun_id・rel_pathでも永続runのaccountが違えば後続を出さない。"""
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    row = threadrun.load(first[0].run_id)
    row["account"] = "other-threads"
    Path(threadrun.run_path(first[0].run_id)).write_text(
        json.dumps(row, ensure_ascii=False), encoding="utf-8")

    second_api = FakeAdapter()
    results = publish(thread_account, second_api, max_posts=1)
    print("RUN_ACCOUNT_MISMATCH", [(r.action, r.reason) for r in results],
          second_api.calls)
    assert second_api.calls == []
    assert results[0].action == "stopped"
    assert "別の account" in results[0].reason


def test_別accountの正常runは対象accountを止めない(
        thth_root, thread_account):
    """読める別account recordまで全停止させる過剰制約がないことを確認する。"""
    row = {
        "run_id": "run-20260915T180000-deadbeef",
        "account": "other-threads",
        "rel_path": "docs/sns/queue/other.md",
        "started_at": "2026-09-15T18:00:00+09:00",
        "posts": [{"index": 1, "state": "unresolved", "post_id": None}],
    }
    _runs_dir(thth_root).joinpath(f"{row['run_id']}.json").write_text(
        json.dumps(row), encoding="utf-8")
    api = FakeAdapter()
    results = publish(thread_account, api, max_posts=1)
    print("FOREIGN_VALID_RUN", [(r.action, r.reason) for r in results], api.calls)
    assert len(api.calls) == 1
    assert results[0].action == "published"
