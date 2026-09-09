"""`thth queue --json`（発注 §5 受け入れ 4）。draft/approved/posted/型外 を数え、
次に出るものと時刻を返す。"""
from __future__ import annotations

from tests.conftest import write_queue_file
from thth import report as report_mod


def test_queue_summaryが状態別に数える(isolated_account):
    qdir = isolated_account["queue_dir"]
    write_queue_file(qdir, "a-draft.md", fm_overrides={"status": "draft"})
    write_queue_file(qdir, "b-approved.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-09T08:00:00+09:00"})
    write_queue_file(qdir, "c-approved-later.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-10T08:00:00+09:00"})
    write_queue_file(qdir, "d-posted.md", fm_overrides={
        "status": "posted", "post_id": "999", "posted_at": "2026-09-08T08:00:00+09:00"})
    write_queue_file(qdir, "e-typemismatch.md", no_front_matter=True, body="ただの文章\n")

    summary = report_mod.queue_summary(isolated_account["name"])
    info = summary[isolated_account["name"]]
    assert info["counts"]["draft"] == 1
    assert info["counts"]["approved"] == 2
    assert info["counts"]["posted"] == 1
    assert info["type_mismatch"] == 1
    # 次に出るのは publish_at が早い approved（b）。
    assert info["next_file"] == "b-approved.md"
    assert info["next_publish_at"] == "2026-09-09T08:00:00+09:00"


def test_queue_summaryはpost_id付きを次の候補から外す(isolated_account):
    qdir = isolated_account["queue_dir"]
    write_queue_file(qdir, "a-approved-but-posted.md", fm_overrides={
        "status": "approved", "post_id": "123", "publish_at": "2026-09-09T08:00:00+09:00"})
    write_queue_file(qdir, "b-approved.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-10T08:00:00+09:00"})

    summary = report_mod.queue_summary(isolated_account["name"])
    info = summary[isolated_account["name"]]
    assert info["next_file"] == "b-approved.md"
