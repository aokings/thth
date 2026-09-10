"""board に `approval_stale`（要確認）の件数とファイル名を出す（外部レビュー再レビュー C）。

統括が別途見つけた穴: `thth queue` は理由を出すが `thth board` には出ず、
`approved_waiting: 1` としか出ないので「承認して待っている（正常）」と
「承認が古くて（approval_stale）永久に出ない（異常）」が board 1 画面で区別
できなかった（黙って失敗する形）。あわせて `thth throw` を手で打って何も出せ
なかったときも理由を添える（いまは「出すものが無い」とだけ出る）。

`run_thth()`（`bin/thth` を別プロセスで呼ぶ）を使うテストは `frozen_now_jst`
autouse fixture の monkeypatch がプロセス境界を越えないため、実の壁時計を使う
（`tests/conftest.py` の docstring 参照）。**時刻に依存しない形にする**——
`quiet_hours: None`・`min_interval_hours: 0` の account を使い、`publish_at` は
テストを書いた「いま」に関係ない明確に過去の日付にする。
"""
from __future__ import annotations

import datetime
import json

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import report as report_mod

PAST_PUBLISH_AT = "2020-01-01T08:00:00+09:00"


def _rewrite_body(path: str, before: str, after: str) -> None:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert before in text
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(before, after))


def test_board_summaryはapproval_staleをファイル名付きで出す(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                             fm_overrides={"status": "draft", "approved_sha": None})
    approved = approve_via_cli(path)
    assert approved.returncode == 0, approved.stderr
    _rewrite_body(path, "本文です。", "書き換えた本文です。")

    # in-process（frozen_now_jst が効く・NOW=2026-09-09T10:00 JST・静かな時間帯の外）。
    summary = report_mod.board_summary()
    row = next(r for r in summary["accounts"] if r["account"] == isolated_account["name"])

    # approved_waiting だけでは「承認が古い（もう出ない）」と「承認して待っている
    # （出る）」を区別できない、という元の穴。needs_review で区別できること。
    assert row["approval_stale_count"] == 1
    assert {"file": "a.md", "reason": "approval_stale"} in row["needs_review"]


def test_board_summaryはstaleと型外approvedも要確認に拾う(isolated_account):
    qdir = isolated_account["queue_dir"]
    write_queue_file(qdir, "b-stale.md", fm_overrides={
        "status": "approved", "publish_at": "2026-08-01T08:00:00+09:00"},
        body="## threads\n\n古い本文\n")
    write_queue_file(qdir, "c-badpublishat.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-09T08:00:00", "approved_sha": "dummy"},
        body="## threads\n\ntz無し\n")

    summary = report_mod.board_summary()
    row = next(r for r in summary["accounts"] if r["account"] == isolated_account["name"])
    reasons = {item["file"]: item["reason"] for item in row["needs_review"]}
    assert reasons.get("b-stale.md") == "stale"
    assert reasons.get("c-badpublishat.md") == "publish_at_invalid"


def test_board_summaryは正常な承認待ちをneeds_reviewに入れない(isolated_account):
    """**「承認待ち」とは publish_at がこれから来るもの**（frozen now は 10:00）。

    以前この fixture は 08:00（＝2 時間前）を「正常な承認待ち」と呼んでいたが、
    指定した時刻を 2 時間過ぎて出ていないものは正常ではない（masaru 受け入れ条件
    2026-09-10）。**これから出るもの**に直した。過ぎているものが要確認に出ることは
    下のテストで固定する。
    """
    write_queue_file(isolated_account["queue_dir"], "ok.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-09T18:00:00+09:00"})

    summary = report_mod.board_summary()
    row = next(r for r in summary["accounts"] if r["account"] == isolated_account["name"])
    assert row["approved_waiting"] == 1
    assert row["needs_review"] == []
    assert row["approval_stale_count"] == 0


def test_board_summaryは指定時刻を過ぎても出ていないものを要確認にする(isolated_account):
    """masaru 受け入れ条件 2026-09-10（「複数本数の投稿の日時指定が出来て」）。

    指定時刻を 1 時間以上過ぎても出ていない＝10 分刻みの実行が 6 回以上空振り
    している。台帳が `production: false`（＝リハーサルなので永久に出ない）なら、
    その理由をそのまま名指しする。
    """
    write_queue_file(isolated_account["queue_dir"], "late.md", fm_overrides={
        "status": "approved", "publish_at": "2026-09-09T08:00:00+09:00"})

    summary = report_mod.board_summary()
    row = next(r for r in summary["accounts"] if r["account"] == isolated_account["name"])
    assert row["needs_review"] == [{"file": "late.md", "reason": "rehearsal"}], row


def test_thth_boardの人向け出力にapproval_staleが要確認として出る(isolated_account_factory):
    # subprocess（run_thth）越しは実の壁時計を使うので、静かな時間帯・最短間隔に
    # 時刻依存で落ちないよう明示的に外す。
    account = isolated_account_factory(quiet_hours=None, min_interval_hours=0)
    path = write_queue_file(account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None, "publish_at": PAST_PUBLISH_AT})
    assert approve_via_cli(path).returncode == 0
    _rewrite_body(path, "本文です。", "書き換えた本文です。")

    result = run_thth(["board"])
    assert result.returncode == 0
    assert "要確認" in result.stdout
    assert "a.md" in result.stdout
    assert "approval_stale" in result.stdout


def test_thth_boardのjson出力は機械可読のまま残る(isolated_account):
    result = run_thth(["board", "--json"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "accounts" in payload
    row = payload["accounts"][0]
    assert "needs_review" in row
    assert "approval_stale_count" in row


def test_thth_throwを手で打って出すものが無いときは理由が出る(isolated_account_factory):
    account = isolated_account_factory(quiet_hours=None, min_interval_hours=0)
    # 「未来」であることだけが理由になるよう、実行される「いま」から確実に
    # 遠い未来の日付にする（テストを書いた日・実行した日のどちらにも依存しない）。
    write_queue_file(account["queue_dir"], "future.md", fm_overrides={
        "status": "approved", "publish_at": "2099-01-01T08:00:00+09:00"})

    result = run_thth(["throw", account["name"]])
    assert result.returncode == 0
    assert "出すものが無い" in result.stdout
    assert "future.md" in result.stdout
    assert "future" in result.stdout
