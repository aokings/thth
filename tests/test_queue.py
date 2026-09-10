"""`thth queue --json`（発注 §5 受け入れ 4）。draft/approved/posted/型外 を数え、
`select.select_one()` に寄せた「次に出るもの・いつ」と、選ばれなかった候補の理由
（`quiet_hours`・`min_interval`・`future` 等）を返す（食い違い 7 の裁定・2026-09-09。
以前は `_peek_next` という別ロジックだったが、同じ答えを 2 か所で計算するとずれる
ので select_one() に一本化した）。"""
from __future__ import annotations

from tests.conftest import write_queue_file
from thth import queuefile
from thth import report as report_mod

# select_one() は「いま出せるか」まで見るので、_peek_next と違って now を固定しないと
# テストが不安定になる。
NOW = queuefile.parse_publish_at("2026-09-09T09:00:00+09:00")


def test_queue_summaryが状態別に数える(isolated_account):
    qdir = isolated_account["queue_dir"]
    write_queue_file(qdir, "a-draft.md", fm_overrides={"status": "draft"})
    write_queue_file(qdir, "b-approved.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-09T08:00:00+09:00"},
        body="## threads\n\nb の本文\n")
    write_queue_file(qdir, "c-approved-later.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-10T08:00:00+09:00"},
        body="## threads\n\nc の本文\n")
    write_queue_file(qdir, "d-posted.md", fm_overrides={
        "status": "posted", "post_id": "999", "posted_at": "2026-09-08T08:00:00+09:00"},
        body="## threads\n\nd の本文（既に投稿済み）\n")
    write_queue_file(qdir, "e-typemismatch.md", no_front_matter=True, body="ただの文章\n")

    summary = report_mod.queue_summary(isolated_account["name"], now=NOW)
    info = summary[isolated_account["name"]]
    assert info["counts"]["draft"] == 1
    assert info["counts"]["approved"] == 2
    assert info["counts"]["posted"] == 1
    assert info["type_mismatch"] == 1
    # 次に出るのは select_one() が選ぶ b（publish_at が早く、いま出せる）。
    assert info["next_file"] == "b-approved.md"
    assert info["next_publish_at"] == "2026-09-09T08:00:00+09:00"
    # c は publish_at が未来なので「いま出ない理由」に future が付く。
    assert {"file": "c-approved-later.md", "reason": "future"} in info["next_rejections"]


def test_queue_summaryはpost_id付きを次の候補から外す(isolated_account):
    qdir = isolated_account["queue_dir"]
    write_queue_file(qdir, "a-approved-but-posted.md", fm_overrides={
        "status": "approved", "post_id": "123", "publish_at": "2026-09-08T08:00:00+09:00"},
        body="## threads\n\na の本文\n")
    write_queue_file(qdir, "b-approved.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-08T09:00:00+09:00"},
        body="## threads\n\nb の本文\n")

    summary = report_mod.queue_summary(isolated_account["name"], now=NOW)
    info = summary[isolated_account["name"]]
    assert info["next_file"] == "b-approved.md"
    # a は post_id が付いているので select_one 条件 1 で落ちる（status を見る前に）。
    assert {"file": "a-approved-but-posted.md", "reason": "post_id_present"} in info["next_rejections"]


def test_queue_summaryはquiet_hoursとmin_intervalの理由も併記する(isolated_account_factory):
    """**間隔・静かな時間帯を明示して設定した場合**の表示（既定ではない）。

    既定は `min_interval_hours: 0`・`quiet_hours: null`（明示した publish_at を
    当て推量で上書きしない・masaru 受け入れ条件 2026-09-10）。ここでは設定した
    側の意思としてそれらを入れ、理由が併記されることを見る。
    """
    isolated_account = isolated_account_factory(
        min_interval_hours=6, quiet_hours=["22:00", "07:00"])
    qdir = isolated_account["queue_dir"]
    # 前回投稿（07:00）から min_interval_hours（6h）未満で now（09:00）を迎える。
    write_queue_file(qdir, "prev-posted.md", fm_overrides={
        "status": "posted", "post_id": "1", "posted_at": "2026-09-09T07:00:00+09:00"},
        body="## threads\n\n前回の本文\n")
    write_queue_file(qdir, "a-approved.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-09T08:00:00+09:00"},
        body="## threads\n\n今回の本文\n")

    summary = report_mod.queue_summary(isolated_account["name"], now=NOW)
    info = summary[isolated_account["name"]]
    assert info["next_file"] is None
    assert {"file": "a-approved.md", "reason": "min_interval"} in info["next_rejections"]

    # 静かな時間帯（既定 22:00〜07:00）に入っている場合も同様に理由が付く。
    quiet_now = queuefile.parse_publish_at("2026-09-09T23:00:00+09:00")
    summary2 = report_mod.queue_summary(isolated_account["name"], now=quiet_now)
    info2 = summary2[isolated_account["name"]]
    assert info2["next_file"] is None
    assert {"file": "a-approved.md", "reason": "quiet_hours"} in info2["next_rejections"]
