"""front-matter 3 行の書き戻し ＋ git add/commit/pull --rebase/push（設計 §4.3）。

書き換えるのは `status`・`post_id`・`posted_at` の 3 行だけ。本文には触らない。
push 失敗は commit を残して非ゼロ（手で push できる状態を残す。inflight は消さない・
呼び出し側の core.py の責務）。
"""
from __future__ import annotations

import subprocess

from . import redact as redact_mod


def rewrite_front_matter(path: str, *, status: str, post_id: str | None,
                          posted_at: str | None) -> None:
    """`status`・`post_id`・`posted_at` の 3 行だけを書き換える。本文には触らない。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"front-matter が壊れている: {path}")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError(f"front-matter が壊れている（閉じ --- が無い）: {path}")
    for i in range(1, end_idx):
        key = lines[i].split(":", 1)[0].strip()
        if key == "status":
            lines[i] = f"status: {status}"
        elif key == "post_id":
            lines[i] = f"post_id: {post_id or ''}"
        elif key == "posted_at":
            lines[i] = f"posted_at: {posted_at or ''}"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _run_git(repo_dir: str, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True, text=True,
    )


def commit_and_push(repo_dir: str, *, rel_path: str, message: str) -> tuple:
    """`git add -- <rel_path>` → commit → `pull --rebase --autostash` → push。

    衝突したら 1 回だけ pull し直して再 push、それでも駄目なら commit を残して
    `(False, error)` を返す（観測.sh・watchtower と同じ流儀）。

    `--autostash` を付けるのは、中断が残した未ステージ変更（この 1 ファイル以外の
    変更）で rebase 自体が失敗し続ける事故（発注 §5 test_20260901）を避けるため。
    自分たちが今回 add した分は commit 済みなので rebase の対象にならず、
    autostash が退避・復元するのは「関係ない残骸」だけになる。
    """
    add = _run_git(repo_dir, ["add", "--", rel_path])
    if add.returncode != 0:
        return False, redact_mod.redact(add.stderr)

    commit = _run_git(repo_dir, ["commit", "-m", message])
    if commit.returncode != 0:
        return False, redact_mod.redact(commit.stderr)

    last_err = ""
    for _attempt in range(2):
        pull = _run_git(repo_dir, ["pull", "--rebase", "--autostash"])
        if pull.returncode != 0:
            last_err = pull.stderr
            continue
        push = _run_git(repo_dir, ["push"])
        if push.returncode == 0:
            return True, ""
        last_err = push.stderr

    msg = "push に失敗しました（commit は残っています。手で push してください）: " + redact_mod.redact(last_err)
    return False, msg
