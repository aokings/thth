"""M1 の state/receipt 整合を実 publish 経路まで独立に攻める。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_thread_publish import FakeAdapter, publish, thread_account
from thth import threadrun


def _write(row: dict) -> Path:
    path = Path(threadrun.run_path(row["run_id"]))
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return path


def _strip_post_receipt(row: dict) -> None:
    """pending の正常形に戻し、個別に残す矛盾だけを呼び出し側で足す。"""
    post = row["posts"][0]
    post["state"] = threadrun.PENDING
    post["post_id"] = None
    post["posted_at"] = None
    post["reply_to"] = None
    post["container_id"] = None
    post["last_ok"] = None
    post["note"] = None
    row["root_post_id"] = None


@pytest.mark.parametrize("trace", ["last_ok", "root_post_id"])
def test_公開成功の残存痕跡だけでも同じrootを再公開しない(
        thth_root, thread_account, trace):
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    assert len(first_api.calls) == 1
    row = threadrun.load(first[0].run_id)
    assert row["posts"][0]["last_ok"] == "publish"
    assert row["root_post_id"] == "POST1"

    _strip_post_receipt(row)
    if trace == "last_ok":
        row["posts"][0]["last_ok"] = "publish"
    else:
        row["root_post_id"] = "POST1"
    path = _write(row)

    problem = threadrun.run_problem(row)
    unreadable = threadrun.unreadable_runs()
    second_api = FakeAdapter()
    results = publish(thread_account, second_api, max_posts=1)
    print("RESIDUAL_PUBLISH_TRACE", trace, str(path), problem, unreadable,
          [(r.action, r.index, r.reason, r.run_id) for r in results],
          second_api.calls)
    assert second_api.calls == [], (
        f"{trace} が公開成功を示すのに、同じ先頭段をrootとして再公開した"
    )


def test_published段はlast_okが無くても安全に続きを出せる(
        thth_root, thread_account):
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    row = threadrun.load(first[0].run_id)
    row["posts"][0]["last_ok"] = None
    _write(row)

    assert threadrun.run_problem(row) is None
    second_api = FakeAdapter()
    results = publish(thread_account, second_api, max_posts=1)
    print("PUBLISHED_WITHOUT_LAST_OK",
          [(r.action, r.index, r.reason) for r in results], second_api.calls)
    assert len(second_api.calls) == 1
    assert second_api.calls[0]["reply_to"] == "POST1"
    assert results[0].action == "published"
    assert results[0].index == 2


def test_確定失敗後のpendingはcontainerとreply_toが残っても再試行できる(
        thth_root, thread_account):
    failed_api = FakeAdapter(fail_at=1, failure="publish_failed")
    failed = publish(thread_account, failed_api, max_posts=1)
    assert failed[0].action == "failed"
    run_id = failed[0].run_id
    row = threadrun.load(run_id)
    assert row["posts"][0]["state"] == threadrun.PENDING
    assert row["posts"][0]["container_id"] == "container-1"
    assert row["posts"][0]["reply_to"] is None
    assert threadrun.run_problem(row) is None

    retry_api = FakeAdapter()
    retried = publish(thread_account, retry_api, max_posts=1)
    print("LEGITIMATE_PENDING_RETRY",
          [(r.action, r.index, r.reason) for r in retried], retry_api.calls)
    assert len(retry_api.calls) == 1
    assert retried[0].action == "published"
    assert retried[0].index == 1
