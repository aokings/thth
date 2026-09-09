"""test_20260901_leftover_worktree_breaks_rebase（発注 §5・kopicha 2026-09-01 の事故と同じ型）:
中断が残した未ステージ変更で `pull --rebase` が壊れないこと。"""
from __future__ import annotations

import os
import subprocess

from tests.conftest import init_git_pair, run_git
from thth import writeback


def test_20260901_leftover_worktree_breaks_rebase(tmp_path):
    seed_content = (
        "---\nthth: 1\naccount: nigamilab-threads\n"
        "publish_at: 2026-09-09T08:00:00+09:00\nstatus: approved\n"
        "reply_to:\npost_id:\nposted_at:\n---\n## threads\n\n本文\n"
    )
    pair = init_git_pair(tmp_path, seed_content=seed_content)
    work = pair["work"]

    # 「他所」の追跡ファイルを origin に足しておき、work 側にだけ未ステージの
    # 変更を残す（中断が残した未ステージ変更を模す。observe.sh・kopicha と同じ型）。
    other_path_seed = os.path.join(pair["seed"], "other.txt")
    with open(other_path_seed, "w", encoding="utf-8") as f:
        f.write("original\n")
    run_git(pair["seed"], ["add", "-A"])
    run_git(pair["seed"], ["commit", "-m", "add other.txt"])
    run_git(pair["seed"], ["push"])

    run_git(work, ["pull"])
    other_path_work = os.path.join(work, "other.txt")
    with open(other_path_work, "w", encoding="utf-8") as f:
        f.write("中断が残した未コミットの変更\n")

    # queue ファイルを書き換えて push する（本来の writeback の仕事）。
    queue_path = os.path.join(pair["queue_dir"], "a.md")
    writeback.rewrite_front_matter(queue_path, status="posted", post_id="12345",
                                    posted_at="2026-09-09T08:00:30+09:00")
    ok, err = writeback.commit_and_push(
        work, rel_path=os.path.join("docs", "sns", "queue", "a.md"),
        message="thth: nigamilab-threads a.md を投稿（post_id 12345）",
    )
    assert ok, f"push が失敗した（--autostash が効いていない可能性）: {err}"

    # push できている（origin に届いている）。
    verify_dir = str(tmp_path / "verify")
    subprocess.run(["git", "clone", pair["bare"], verify_dir], check=True,
                    capture_output=True, text=True)
    with open(os.path.join(verify_dir, "docs", "sns", "queue", "a.md"), encoding="utf-8") as f:
        assert "status: posted" in f.read()

    # 未ステージだった other.txt の変更は autostash で退避・復元されて残っている
    # （壊れて消えていない）。
    with open(other_path_work, encoding="utf-8") as f:
        assert f.read() == "中断が残した未コミットの変更\n"
