"""`thth revoke`: 承認を取り消す（asmon 関東セッションの指摘 2026-09-10・最優先）。

> revoke が無い。承認後に 1 本だけ止めたいとき、正しい操作が用意されていません。
> いまできるのは本文を書き換えて approval_stale にすることだけで、**止める手段が
> 壊すことになっています。**

Meta の権限で投稿を削除できない（設計 §2.2）ので、**出る前に止める道**が唯一の
安全弁になる。47 本を 16 日かけて出す状況では、ここが無いのが最も怖い。
"""
from __future__ import annotations

import subprocess

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import core, queuefile


def _approved(isolated_account, name="a.md", **overrides):
    fm = {"status": "draft", "approved_sha": None}
    fm.update(overrides)
    path = write_queue_file(isolated_account["queue_dir"], name, fm_overrides=fm)
    assert approve_via_cli(path).returncode == 0
    return path


def test_取り消すとdraftに戻り本文は変わらない(isolated_account):
    path = _approved(isolated_account)
    before_body = queuefile.parse(path).body

    result = run_thth(["revoke", path, "--reason", "連載の順を変えた",
                       "--by", "claude（kanto セッション）"])
    assert result.returncode == 0, result.stderr

    qf = queuefile.parse(path)
    assert qf.front_matter.get("status") == "draft"
    assert not qf.front_matter.get("approved_sha")
    assert not qf.front_matter.get("approved_at")
    assert qf.front_matter.get("revoked_at")
    assert qf.front_matter.get("revoked_by") == "claude（kanto セッション）"
    assert qf.front_matter.get("revoked_reason") == "連載の順を変えた"
    # **止めることと壊すことを分ける。** 本文には触らない。
    assert qf.body == before_body


def test_取り消したものは出ない(isolated_account):
    path = _approved(isolated_account)
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    result = core.throw_once(isolated_account["name"])
    assert result.action == "none", result


def test_取り消しは記録として残る(isolated_account):
    path = _approved(isolated_account)
    run_thth(["revoke", path, "--by", "masaru"])
    log = subprocess.run(["git", "-C", isolated_account["repo_dir"], "log", "--oneline", "-1"],
                          capture_output=True, text=True).stdout
    assert "承認の取り消し" in log and "masaru" in log, log


def test_取り消したあと直して承認し直せる(isolated_account):
    path = _approved(isolated_account)
    assert run_thth(["revoke", path, "--by", "テスト"]).returncode == 0
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace("本文です。", "直した本文です。"))

    assert approve_via_cli(path).returncode == 0
    qf = queuefile.parse(path)
    assert qf.front_matter.get("status") == "approved"
    assert qf.front_matter.get("approved_sha")


def test_もう出たものは取り消せないと断る(isolated_account):
    """`post_id` が付いていれば THTH では止められない。**その場でそう言う。**"""
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "posted", "post_id": "999", "posted_at": "2026-09-09T08:00:00+09:00"})
    result = run_thth(["revoke", path, "--by", "テスト"])
    assert result.returncode == 1
    assert "Threads の画面から" in result.stderr, result.stderr
    assert queuefile.parse(path).front_matter.get("status") == "posted"


def test_承認されていないものは断る(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    result = run_thth(["revoke", path, "--by", "テスト"])
    assert result.returncode == 1
    assert "承認されていません" in result.stderr


def test_意図して止めたものと承認が古いものをboardで区別できる(isolated_account):
    """関東セッションの懸念そのもの。

    - 意図して止めた → `draft` に戻る（要確認に出ない・承認待ちにも数えない）
    - うっかり書き換えた → `approved` のまま `approval_stale` で要確認に出る
    """
    from thth import report as report_mod

    stopped = _approved(isolated_account, "stopped.md")
    assert run_thth(["revoke", stopped, "--by", "テスト"]).returncode == 0

    broken = _approved(isolated_account, "broken.md",
                        publish_at="2026-09-09T18:00:00+09:00")
    with open(broken, encoding="utf-8") as f:
        text = f.read()
    with open(broken, "w", encoding="utf-8") as f:
        f.write(text.replace("本文です。", "うっかり書き換えた本文です。"))

    row = next(r for r in report_mod.board_summary()["accounts"]
               if r["account"] == isolated_account["name"])
    reasons = {item["file"]: item["reason"] for item in row["needs_review"]}
    assert reasons == {"broken.md": "approval_stale"}, reasons
    assert row["approved_waiting"] == 1  # broken.md だけ（stopped.md は draft）
