"""R1 の明記済み3契約と正当writeback失敗の境界を独立検収する。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import run_git
from tests.test_thread_publish import (
    FakeAdapter,
    REL,
    bundle_text,
    publish,
    thread_account,
    write_and_push,
)
from thth import accounts, bundle, inflight, threadrun


def _write_run(row: dict) -> Path:
    path = Path(threadrun.run_path(row["run_id"]))
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return path


def _clean_pending(row: dict) -> None:
    post = row["posts"][0]
    post.update({
        "state": threadrun.PENDING,
        "post_id": None,
        "posted_at": None,
        "reply_to": None,
        "container_id": None,
        "last_ok": None,
        "note": None,
    })
    row["root_post_id"] = None


@pytest.mark.parametrize("trace", ["last_ok", "root_post_id"])
def test_各残存痕跡が単独で公開経路を止める(thth_root, thread_account, trace):
    """原稿receiptを戻し、他の2関門に覆われない形で各run関門を確認する。"""
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    assert len(first_api.calls) == 1
    row = threadrun.load(first[0].run_id)

    # 同期済み原稿側のreceiptを消し、原稿突合せ関門がこのcaseを拾わない形にする。
    write_and_push(thread_account["pair"], bundle_text())
    _clean_pending(row)
    if trace == "last_ok":
        row["posts"][0]["last_ok"] = "publish"
    else:
        row["root_post_id"] = "POST1"
    path = _write_run(row)

    problem = threadrun.run_problem(row)
    broken = threadrun.unreadable_runs()
    second_api = FakeAdapter()
    results = publish(thread_account, second_api, max_posts=1)
    print("ISOLATED_TRACE_GATE", trace, problem, broken,
          [(r.action, r.reason) for r in results], second_api.calls)
    assert problem is not None
    assert str(path.resolve()) in broken
    assert second_api.calls == []
    assert results[0].action == "stopped"


def test_正当writeback失敗は再送せず同じ親で停止復旧する(
        thth_root, thread_account):
    """run=published・同期原稿receipt無しの逆向きを実Git失敗から作る。"""
    pair = thread_account["pair"]
    work = pair["work"]
    hook = Path(pair["bare"]) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    api = FakeAdapter()
    first = publish(thread_account, api, max_posts=1)
    assert len(api.calls) == 1
    assert first[0].action == "stopped"
    assert "再公開しません" in first[0].reason
    run_id = first[0].run_id
    row = threadrun.load(run_id)
    assert row["posts"][0]["state"] == threadrun.PUBLISHED
    assert row["posts"][0]["post_id"] == "POST1"
    assert row["root_post_id"] == "POST1"
    assert row["stop_confirmed_at"]

    # ローカル書き戻しcommitはあるが、remoteの正本原稿にはまだreceiptが無い。
    failed_head = run_git(work, ["rev-parse", "HEAD"]).stdout.strip()
    remote_text = run_git(work, ["show", f"origin/main:{REL}"]).stdout
    assert bundle.parse_text(remote_text, f"origin/main:{REL}").posts[0].get("post_id") is None
    run_git(work, ["reset", "--hard", "origin/main"])
    assert bundle.parse(thread_account["path"]).posts[0].get("post_id") is None

    blocked = publish(thread_account, api, max_posts=1)
    print("WRITEBACK_FAILED_BLOCKED", [(r.action, r.reason) for r in blocked], api.calls)
    assert len(api.calls) == 1, "書き戻し未確定の先頭段を再送した"
    kept = threadrun.load(run_id)
    assert kept["posts"][0]["post_id"] == "POST1"
    assert kept["root_post_id"] == "POST1"

    # 運用者が外部成功を確認し、残っていたreceipt commitをpushしてinflightを解消。
    hook.unlink()
    run_git(work, ["push", "origin", f"{failed_head}:main"])
    run_git(work, ["reset", "--hard", "origin/main"])
    restored_draft = bundle.parse(thread_account["path"])
    assert restored_draft.posts[0].get("post_id") == "POST1"
    inflight.clear(accounts.state_dir_for(thread_account["account"]["name"]))

    resumed = publish(thread_account, api, max_posts=1)
    print("WRITEBACK_RECOVERED", [(r.action, r.index, r.reason) for r in resumed],
          api.calls)
    assert len(api.calls) == 2
    assert api.calls[1]["reply_to"] == "POST1"
    assert resumed[0].action == "published"
    assert resumed[0].index == 2
    final = threadrun.load(run_id)
    assert final["run_id"] == run_id
    assert final["root_post_id"] == "POST1"
    assert final["posts"][0]["post_id"] == "POST1"
    assert final["stop_confirmed_at"] is None


def test_root_post_id値の不一致は親を汚さないが記録契約は未達(
        thth_root, thread_account):
    api = FakeAdapter()
    first = publish(thread_account, api, max_posts=1)
    row = threadrun.load(first[0].run_id)
    row["root_post_id"] = "WRONG-ROOT"
    _write_run(row)

    problem = threadrun.run_problem(row)
    broken = threadrun.unreadable_runs()
    continued = publish(thread_account, api, max_posts=1)
    print("ROOT_VALUE_MISMATCH", problem, broken,
          [(r.action, r.index, r.reason) for r in continued], api.calls)
    # **ここだけ、こちらの判断で期待を変えている**（2026-09-12・要判定）。
    #
    # 外部レビューの元の期待は「誤った root でも**公開は続く**」だった
    # （現行の親解決は段の `post_id` を使うので、誤 root は API の親にならない）。
    # **こちらは止める側に倒した。** 理由:
    #
    # - **信用できない記録に追記しながら公開を続ける**のが、今夜 3 回続けて
    #   P1 を出した形そのものだった（読めない→形が違う→中身が矛盾）
    # - `unreadable_runs()` は**公開と承認の共通の関門**なので、ここだけ
    #   「診断はするが止めない」にすると、**関門が 2 種類になる**
    # - 止めたときのエラーには**絶対パスと戻し方**が出るので、人が動ける
    #
    # **元の入力と失敗経路は変えていない。** 変えたのは「止まるか続くか」の
    # 期待だけ。**外部レビューが「止めすぎ」と判定したら戻す。**
    assert continued[0].action == "stopped"
    assert "root_post_id" in (continued[0].reason or "") or \
        "実行記録を読めません" in (continued[0].reason or "")
    assert len(api.calls) == 1, "止めたのに追加の公開をした"
    assert threadrun.load(first[0].run_id)["root_post_id"] == "WRONG-ROOT"
    # 契約は「root が先頭段 post_id と一致する」までを含む。
    assert problem is not None
    assert broken
