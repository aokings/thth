"""N1/N2 の run schema を公開経路まで独立に攻める。実 API は使わない。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_thread_publish import FakeAdapter, publish, thread_account
from thth import threadrun


def _write(row: dict, run_id: str) -> Path:
    path = Path(threadrun.run_path(run_id))
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return path


def test_pendingへ化けた公開済み段をrootとして再公開しない(
        thth_root, thread_account):
    """published receiptを残したままstateだけpendingなら明白な矛盾。

    ``post_id``、``posted_at``、``last_ok=publish``、rootの``root_post_id``が
    公開成功を示すので、単なる未着手として進めてはならない。
    """
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    assert len(first_api.calls) == 1
    run_id = first[0].run_id
    row = threadrun.load(run_id)
    assert row["posts"][0]["post_id"] == "POST1"
    assert row["root_post_id"] == "POST1"

    row["posts"][0]["state"] = threadrun.PENDING
    _write(row, run_id)
    problem = threadrun.run_problem(row)

    second_api = FakeAdapter()
    results = publish(thread_account, second_api, max_posts=1)
    print("PUBLISHED_RECEIPT_AS_PENDING", problem,
          [(r.action, r.index, r.reason, r.run_id) for r in results],
          second_api.calls)
    assert second_api.calls == [], (
        "公開成功receiptと矛盾するpendingを信じ、同じ先頭段をrootとして再公開した"
    )


@pytest.mark.parametrize("missing", ["post_id", "text_sha256", "bundle_sha"])
def test_published段の必須receipt欠落をrun破損として止める(
        thth_root, thread_account, missing):
    """publishedの意味を復元するfieldが無ければ正常runとして扱えない。"""
    api = FakeAdapter()
    first = publish(thread_account, api, max_posts=1)
    run_id = first[0].run_id
    row = threadrun.load(run_id)
    row["posts"][0].pop(missing)
    path = _write(row, run_id)

    problem = threadrun.run_problem(row)
    unreadable = threadrun.unreadable_runs()
    next_api = FakeAdapter()
    try:
        results = publish(thread_account, next_api, max_posts=1)
        outcome = [(r.action, r.index, r.reason) for r in results]
    except Exception as exc:  # 未処理例外かAPI到達かも証拠にする
        outcome = f"{type(exc).__name__}: {exc}"
    print("PUBLISHED_FIELD_MISSING", missing, problem, unreadable,
          outcome, next_api.calls)
    assert problem is not None
    assert str(path.resolve()) in unreadable


def test_run_idとファイル名の不一致を破損として止める(thth_root):
    """scan経路でも ``<run_id>.json`` と内部run_idの一致が必要。"""
    directory = Path(threadrun.runs_dir())
    directory.mkdir(parents=True, exist_ok=True)
    filename_id = "run-20260915T190000-aaaaaaaa"
    inside_id = "run-20260915T190000-bbbbbbbb"
    row = {
        "run_id": inside_id,
        "account": "nigamilab-threads",
        "rel_path": "docs/sns/queue/thread.md",
        "posts": [{"index": 1, "state": "pending", "post_id": None}],
    }
    path = directory / f"{filename_id}.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    print("RUN_ID_FILENAME_MISMATCH", threadrun.run_problem(row),
          threadrun.unreadable_runs())
    assert str(path.resolve()) in threadrun.unreadable_runs()


def test_run側continue_until欠落でも現在の承認期限は公開直前に効く(
        thth_root, thread_account):
    """runの履歴field欠落と、現在の公開期限関門を分けて観測する。"""
    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    run_id = first[0].run_id
    row = threadrun.load(run_id)
    row.pop("continue_until")
    _write(row, run_id)

    # 現実装ではrun_problemを通るが、公開判断は同期済み原稿のcontinue_untilを使う。
    problem = threadrun.run_problem(row)
    second_api = FakeAdapter()
    results = publish(thread_account, second_api,
                      now=__import__("datetime").datetime.fromisoformat(
                          "2026-09-15T21:00:00+09:00"), max_posts=1)
    print("MISSING_RUN_CONTINUE_UNTIL", problem,
          [(r.action, r.reason) for r in results], second_api.calls)
    assert second_api.calls == []
    assert results[0].action == "stopped"
    assert "継続期限" in results[0].reason


def test_helper経由でもN1拒否理由を製品出力で固定する(
        thth_root, thread_account):
    """helperのno-digest早期returnを、単なるreturn codeだけで合格にしない。"""
    from tests.conftest import approve_via_cli
    from tests.test_thread_publish import SEGMENTS, bundle_text, write_and_push

    first_api = FakeAdapter()
    first = publish(thread_account, first_api, max_posts=1)
    run_path = Path(threadrun.run_path(first[0].run_id))
    run_path.write_text("{}", encoding="utf-8")
    revised = list(SEGMENTS)
    revised[0] = "記録破損後に書き直した先頭段。"
    write_and_push(thread_account["pair"], bundle_text(
        segments=revised, status="draft",
        posts=[{"index": 1}, {"index": 2}, {"index": 3}],
    ))

    approved = approve_via_cli(thread_account["path"])
    combined = approved.stdout + approved.stderr
    print("HELPER_REJECTION_REASON", approved.returncode, combined)
    assert approved.returncode != 0
    assert "実行記録を読めません" in combined
    assert str(run_path.resolve()) in combined
    assert "1 本も承認しませんでした" in combined
