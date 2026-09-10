"""`thth revoke`: 承認を取り消す（asmon 関東セッションの指摘 2026-09-10・最優先）。

> revoke が無い。承認後に 1 本だけ止めたいとき、正しい操作が用意されていません。
> いまできるのは本文を書き換えて approval_stale にすることだけで、**止める手段が
> 壊すことになっています。**

Meta の権限で投稿を削除できない（設計 §2.2）ので、**出る前に止める道**が唯一の
安全弁になる。47 本を 16 日かけて出す状況では、ここが無いのが最も怖い。
"""
from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import core, jst, queuefile

NOW = datetime.datetime(2026, 9, 9, 10, 0, tzinfo=jst.JST)


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


def test_検査とロックの間に公開されたら取り消し成功を返さない(tmp_path, isolated_account_factory, monkeypatch, capsys):
    """外部レビュー第 6 巡 P1-2。

    以前は post_id と status を**ロックの外**で読んでいた。ロックが守るのは書き込み
    だけで、**読んだ事実はその間に古くなる**。検査の直後・ロック取得の直前に公開が
    完了すると、`post_id` が付いているのに `draft` へ書き換え、**exit 0 で
    「取り消しました」と返していた。止められなかった投稿を、止められたと伝える。**

    monkeypatch がプロセス境界を越えないので、`cmd_revoke()` を同一プロセスで呼ぶ
    （ロック・git・公開・書き戻しは実装をそのまま通す）。
    """
    import argparse
    from tests.conftest import approve_via_cli, init_git_pair, make_queue_text
    from thth import cli as cli_mod
    from thth import core
    from thth.adapters.base import PublishResult

    REL = "docs/sns/queue/a.md"
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                        quiet_hours=None, min_interval_hours=0)
    path = str(Path(pair["work"]) / REL)
    assert approve_via_cli(path).returncode == 0

    class _Publisher:
        def publish(self, post, **kw):
            return PublishResult("2", None, "2026-09-09T10:00:30+09:00")

    # **ロックを取る直前**に公開を完走させる（公開はロックを取って、書き戻して、
    # 放すところまで終わる）。旧実装はこの時点で post_id を読み終えていたので、
    # 「まだ出ていない」と判断したまま書き換えに進んだ。
    real_acquire = cli_mod.lock_mod.AccountLock.acquire
    done = {}

    def publish_then_acquire(self):
        if "done" not in done:
            done["done"] = True
            core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: _Publisher(), now=NOW)
        return real_acquire(self)

    monkeypatch.setattr(cli_mod.lock_mod.AccountLock, "acquire", publish_then_acquire)

    args = argparse.Namespace(file=path, reason=None, by="テスト", json=False)
    rc = cli_mod.cmd_revoke(args)
    captured = capsys.readouterr()

    assert done.get("done"), "割り込みの公開が走っていない（前提が崩れている）"
    assert rc != 0, "公開済みなのに取り消し成功を返した"
    assert "もう出ています" in captured.err, captured.err
    fm = queuefile.parse(path).front_matter
    assert fm.get("post_id") == "2"
    assert fm.get("status") == "posted", "公開済みなのに draft へ書き換えた"
