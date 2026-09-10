"""外部レビュー第 5 巡（2026-09-10）の 3 件を実プロセス・実 git で再現する。

出所: 外部レビュアーが `2c0b993` を隔離して実行した再現コード
（`/tmp/thth-fifth-xjq14n/REVIEW.md`）。統括が同じ筋を書き直したもの。

**P1-1**: `matches_synced_commit()` が照合先に **HEAD を引き直していた**。HEAD は
動く——同期を確認したあとに別の commit が載れば HEAD はそちらへ動き、**push が
remote に拒否されていても**作業ツリーと HEAD は一致するので「確認済み」になった。
結果、**remote に届いていない本文が公開される**。

**P1-2**: `thth approve` の commit が、**無関係な stage 済み変更を巻き込んで**
remote まで push していた（`git add -- <path>` の後の素の `git commit`）。

**P2-3**: commit できたが push を拒否された承認が、board では
`approved_waiting: 1`・要確認 0 件（＝正常に待っているだけ）に見えていた。
"""
from __future__ import annotations

import dataclasses
import datetime
import os
from pathlib import Path

from tests.conftest import approve_via_cli, init_git_pair, make_queue_text, run_git, run_thth
from thth import core, report, writeback
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
REL = "docs/sns/queue/a.md"
BODY_A = "# メモ\n\n" + "\n".join(f"文脈 {i}" for i in range(12)) + "\n\n## threads\n\n本文 A。\n"


class Spy:
    def __init__(self):
        self.calls = []

    def publish(self, post, **kw):
        self.calls.append(post.text)
        return PublishResult("FIFTH", None, NOW.isoformat())


def _reject_pushes(bare_dir: str) -> None:
    """bare origin に「何が来ても拒否する」hook を仕込む（push 失敗を本物で作る）。"""
    hook = Path(bare_dir) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho '拒否します' >&2\nexit 1\n")
    hook.chmod(0o755)


def _setup(tmp_path, factory, *, status="draft"):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": status}, body=BODY_A))
    account = factory(repo_dir=pair["work"], production=True, quiet_hours=None,
                       min_interval_hours=0)
    return pair, account, Path(pair["work"]) / REL


def _approve_body_b_locally(pair, path):
    """`thth approve` を経ずに、承認済み BODY_B を **local commit だけ**で載せる。

    人が手で `git commit` した場合に相当する。ロックでは防げない経路なので、
    **照合先の固定（OID を持ち回ること）だけが効く**。
    """
    path.write_text(make_queue_text({"status": "approved"},
                                     body=BODY_A.replace("本文 A。", "本文 B。")))
    run_git(pair["work"], ["add", REL])
    run_git(pair["work"], ["commit", "-m", "手で承認（push していない）"])


def test_同期したcommitに固定するのでHEADが動いても公開しない(tmp_path, isolated_account_factory, monkeypatch):
    """P1-1。同期の直後に HEAD が動いても、照合先は同期した OID のまま。"""
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    _reject_pushes(pair["bare"])

    real_sync = writeback.sync_repo
    interleaved = {}

    def sync_then_interleave(repo_dir):
        result = real_sync(repo_dir)          # 本物の同期をそのまま行う
        if result[0] and "done" not in interleaved:
            interleaved["done"] = True
            # 同期の**直後**に HEAD を動かす（push は hook が拒否するので remote は
            # 元のまま。作業ツリーと HEAD だけが BODY_B になる）。
            _approve_body_b_locally(pair, path)
            interleaved["head_moved"] = run_git(pair["work"], ["rev-parse", "HEAD"]).stdout.strip()
            interleaved["synced"] = result[2]
        return result

    monkeypatch.setattr(core.writeback, "sync_repo", sync_then_interleave)
    spy = Spy()
    result = core.throw_once(account["name"], production_flag=True,
                              adapter_factory=lambda *_: spy, now=NOW)

    assert interleaved.get("head_moved") != interleaved.get("synced"), "HEAD が動いていない（前提が崩れている）"
    assert not spy.calls, f"remote に届いていない本文を公開した: {spy.calls}"
    reasons = {r["file"]: r["reason"] for r in (result.rejections or [])}
    assert reasons.get("a.md") == "unverified_content", reasons


def test_approveは投稿中のrepoロックに参加する(tmp_path, isolated_account_factory, monkeypatch):
    """P1-1 のもう半分。公開の最中は承認が割り込めない（何も書き換えない）。"""
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    real_sync = writeback.sync_repo
    attempt = {}

    def sync_then_try_approve(repo_dir):
        result = real_sync(repo_dir)
        if result[0] and "rc" not in attempt:
            attempt["before"] = path.read_text()
            # **一段目から断られる**（同期も承認の中で行うようになったため）。
            # 二段確認のヘルパは digest が出ることを前提にしているので直に呼ぶ。
            proc = run_thth(["approve", str(path)])
            attempt["rc"] = proc.returncode
            attempt["stderr"] = proc.stderr
            attempt["after"] = path.read_text()
        return result

    monkeypatch.setattr(core.writeback, "sync_repo", sync_then_try_approve)
    core.throw_once(account["name"], production_flag=True,
                     adapter_factory=lambda *_: Spy(), now=NOW)

    assert attempt["rc"] != 0, "投稿の最中に承認が通ってしまった"
    assert "別の実行" in attempt["stderr"], attempt["stderr"]
    assert attempt["after"] == attempt["before"], "断ったのにファイルを書き換えた"


def test_approveは無関係なstage済み変更を巻き込まない(tmp_path, isolated_account_factory):
    """P1-2。承認の commit に入ってよいのは、承認したファイルだけ。"""
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    other = Path(pair["work"]) / "unfinished.txt"
    other.write_text("書きかけの別作業\n")
    run_git(pair["work"], ["add", "unfinished.txt"])

    approved = approve_via_cli(str(path))
    assert approved.returncode == 0, approved.stderr

    in_origin = run_git(pair["bare"], ["show", "--name-only", "--format=", "main"]).stdout.split()
    assert in_origin == [REL], f"承認の commit に無関係な変更が入った: {in_origin}"
    # 巻き込まないだけでなく、**消さない**。stage したままの状態が残っている。
    staged = run_git(pair["work"], ["diff", "--cached", "--name-only"]).stdout.split()
    assert "unfinished.txt" in staged, f"stage 済みの別作業が失われた: {staged}"
    assert other.read_text() == "書きかけの別作業\n"


def test_pushを断られた承認はboardで要確認になる(tmp_path, isolated_account_factory):
    """P2-3。commit できたが remote に届いていない承認を「待っているだけ」に見せない。"""
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    _reject_pushes(pair["bare"])

    approved = approve_via_cli(str(path))
    assert approved.returncode == 1, "push を断られたのに成功で終わった"

    row = next(r for r in report.board_summary()["accounts"]
               if r["account"] == account["name"])
    assert row["needs_review"], f"push が届いていない承認が要確認に出ない: {row}"
    assert row["needs_review"][0]["reason"] == "unverified_content", row["needs_review"]

    summary = report.queue_summary(account["name"], now=NOW)[account["name"]]
    assert summary["next_file"] is None, f"次に出るものとして表示された: {summary}"


def test_同期も承認も正常なら公開される(tmp_path, isolated_account_factory):
    """過剰に塞いでいないことの確認。"""
    pair, account, path = _setup(tmp_path, isolated_account_factory)
    assert approve_via_cli(str(path)).returncode == 0
    spy = Spy()
    result = core.throw_once(account["name"], production_flag=True,
                              adapter_factory=lambda *_: spy, now=NOW)
    assert spy.calls == ["本文 A。"], dataclasses.asdict(result)


def test_無関係なstage状態がrebaseで失われない(tmp_path, isolated_account_factory):
    """外部レビュー第 6 巡 P2-5。

    `commit --only` は実 index の他のエントリを残す（第 5 巡の対応）。ところが
    その直後の `pull --rebase --autostash` が、**staged も unstaged もまとめて
    退避して、戻すときは全部 unstaged にする。** 中身は消えないが、
    「stage してある／していない」の区別が消える。第 5 巡のテストは**新規ファイル**を
    stage する場合だけを見ていて、**既に追跡されているファイルの編集**を試していなかった。
    """
    pair, account, path = _setup(tmp_path, isolated_account_factory)

    tracked = Path(pair["work"]) / "other.txt"
    tracked.write_text("最初の中身\n")
    run_git(pair["work"], ["add", "other.txt"])
    run_git(pair["work"], ["commit", "-m", "other.txt を足す"])
    run_git(pair["work"], ["push"])

    tracked.write_text("組み立て中の変更\n")
    run_git(pair["work"], ["add", "other.txt"])          # ← stage してある

    approved = approve_via_cli(str(path))
    assert approved.returncode == 0, approved.stderr

    staged = run_git(pair["work"], ["diff", "--cached", "--name-only"]).stdout.split()
    assert "other.txt" in staged, f"stage 状態が失われた: {staged}"
    assert tracked.read_text() == "組み立て中の変更\n"
    # origin には送られていない
    assert "組み立て中" not in run_git(pair["bare"], ["show", "main:other.txt"]).stdout
