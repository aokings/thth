"""front-matter の書き戻し ＋ git add/commit/pull --rebase/push（設計 §4.3）。

`rewrite_front_matter()` が書き換えるのは `status`・`post_id`・`posted_at` の
3 行だけ。本文には触らない。push 失敗は commit を残して非ゼロ（手で push できる
状態を残す。inflight は消さない・呼び出し側の core.py の責務）。

`set_front_matter_fields()`（外部レビュー §1・受け入れ 1〜5）は `thth approve` 用の
もっと汎用の書き換えで、任意のキーを書ける。既存のキーは値だけ差し替え、front-matter
に無いキー（`approved_sha`・`approved_at` は新しい schema なので既存ファイルには
無いことがある）は閉じ `---` の直前に追加する。
"""
from __future__ import annotations

import subprocess

from . import redact as redact_mod


def rewrite_front_matter(path: str, *, status: str, post_id: str | None,
                          posted_at: str | None) -> None:
    """`status`・`post_id`・`posted_at` の 3 行だけを書き換える。本文には触らない。"""
    set_front_matter_fields(path, {"status": status, "post_id": post_id, "posted_at": posted_at})


def _split_front_matter_lines(lines: list) -> int:
    """先頭が `---` で始まる front-matter の閉じ `---` の行番号を返す。"""
    if not lines or lines[0].strip() != "---":
        raise ValueError("front-matter が壊れている")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    raise ValueError("front-matter が壊れている（閉じ --- が無い）")


def set_front_matter_fields(path: str, fields: dict) -> None:
    """front-matter の任意のキーを書き換える（無ければ閉じ `---` の直前に追加）。

    `fields` の値が None のキーは空文字列として書く（既存の `rewrite_front_matter()`
    の `post_id`・`posted_at` と同じ規約）。本文には一切触らない。キーの並び順は
    既存のキーはその場、新規のキーは末尾（閉じ `---` の直前）に足される順。
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    try:
        end_idx = _split_front_matter_lines(lines)
    except ValueError as e:
        raise ValueError(f"{e}: {path}") from e

    remaining = dict(fields)
    for i in range(1, end_idx):
        key = lines[i].split(":", 1)[0].strip()
        if key in remaining:
            value = remaining.pop(key)
            lines[i] = f"{key}: {value if value is not None else ''}"
    if remaining:
        new_lines = [f"{key}: {value if value is not None else ''}" for key, value in remaining.items()]
        lines[end_idx:end_idx] = new_lines
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
