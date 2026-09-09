"""front-matter の書き戻し ＋ git add/commit/pull --rebase/push（設計 §4.3）。

`rewrite_front_matter()` が書き換えるのは `status`・`post_id`・`posted_at` の
3 行だけ。本文には触らない。push 失敗は commit を残して非ゼロ（手で push できる
状態を残す。inflight は消さない・呼び出し側の core.py の責務）。

`set_front_matter_fields()`（外部レビュー §1・受け入れ 1〜5）は `thth approve` 用の
もっと汎用の書き換えで、任意のキーを書ける。既存のキーは値だけ差し替え、front-matter
に無いキー（`approved_sha`・`approved_at` は新しい schema なので既存ファイルには
無いことがある）は閉じ `---` の直前に追加する。

`sync_repo()`（外部レビュー再レビュー A・2026-09-09）は利用者 repo を select より前に
同期する。`commit_and_push()` の `validate` 引数（外部レビュー再レビュー B）は
push の直前・pull --rebase の後にもう一度「送った本文といまの内容が一致するか」を
確かめる口。どちらも `thth/core.py` から呼ばれる。
"""
from __future__ import annotations

import os
import subprocess

from . import redact as redact_mod


class PushValidationFailed(Exception):
    """`commit_and_push()` の `validate` コールバックが不一致を返した。

    pull --rebase の後・push の前に検知したので、push はしていない
    （commit はローカルに残ったまま。人が手で確認・修正できる状態）。
    呼び出し側（`thth/core.py`）はこれを捕まえて inflight を残したまま exit 1 で
    止める（外部レビュー再レビュー B・受け入れ）。
    """


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


def commit_and_push(repo_dir: str, *, rel_path: str, message: str, validate=None) -> tuple:
    """`git add -- <rel_path>` → commit → `pull --rebase --autostash` → push。

    衝突したら 1 回だけ pull し直して再 push、それでも駄目なら commit を残して
    `(False, error)` を返す（観測.sh・watchtower と同じ流儀）。

    `--autostash` を付けるのは、中断が残した未ステージ変更（この 1 ファイル以外の
    変更）で rebase 自体が失敗し続ける事故（発注 §5 test_20260901）を避けるため。
    自分たちが今回 add した分は commit 済みなので rebase の対象にならず、
    autostash が退避・復元するのは「関係ない残骸」だけになる。

    `validate`（省略可・外部レビュー再レビュー B）: 引数を取らない callable で、
    「いまのファイルの本文が送った本文と一致するか」を bool で返す。**pull --rebase
    が成功するたびに、push の直前に必ず呼ぶ**（再試行のループでも毎回）。

    公開している最中に利用者が別 clone から本文を書き換えて push していると、
    ここで rebase した直後のファイルには相手の変更が混ざっている。呼び出し側
    （`core.py`）が渡す `validate` はそれを検知するためのもの。`core.py` 側で
    push 前に一度だけ行う同種の検査（rebase を経ない・ローカルのファイルに対して
    行う）だけでは、**rebase の後に remote の変更が入ってくる**ケースを見逃す
    （外部レビュー再レビュー §「validation occurs before remote changes are
    incorporated」）。ここで rebase 後の状態をもう一度見ることで、その穴を塞ぐ。

    `validate` が False を返したら `PushValidationFailed` を送出する。push は
    行わず、commit はローカルに残したまま（手で直せる状態）。
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
        if validate is not None and not validate():
            raise PushValidationFailed(
                "pull --rebase のあと、本文が送った内容と食い違うため push しません"
                "（commit はローカルに残っています。手で確認してください）")
        push = _run_git(repo_dir, ["push"])
        if push.returncode == 0:
            return True, ""
        last_err = push.stderr

    msg = "push に失敗しました（commit は残っています。手で push してください）: " + redact_mod.redact(last_err)
    return False, msg


def sync_repo(repo_dir: str) -> tuple:
    """利用者 repo を select より前に同期する（設計 §3.3・外部レビュー再レビュー A）。

    `git fetch` ＋ `git pull --ff-only`（merge commit を作らない）相当。設計は
    「pull(利用者 repo) → inflight 確認 → queue を読む」の順だったが、実装（`core.py`・
    `cli.py`）のどこにも利用者 repo を pull する処理が無く、timer が clone した
    時点の内容を永久に見てしまっていた（承認しても撤回しても届かない）。

    `thth/core.py::_throw_locked()` が repo ロックの中・inflight 確認の後・
    queue を読む前に呼ぶ。**失敗したら投稿しない**（呼び出し側が続行不能として
    扱う）。前回の書き戻しが中断して push できていないローカル commit が残っている
    場合、`--ff-only` はここで失敗する（fast-forward できない）——通常は inflight が
    残っていて手前で止まるはずだが、万一 inflight が無い状態でここに来ても、同期
    失敗として扱い投稿しない。

    次の場合は「同期の必要が無い」として何もせず成功扱いにする（壊れないことを
    優先する）:
      - `repo_dir` が存在しない（例: `masaru-threads` の `repos/_none`。この
        アカウントは `thth send` だけを使い、queue を読まないので同期は元々不要）
      - `repo_dir` が git repo ではない（`.git` が無い）
      - `origin` という remote が無い

    `thth send`（同席の様態）は queue を読まないのでこの関数を呼ばない。
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return True, ""
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return True, ""
    remote = _run_git(repo_dir, ["remote"])
    if remote.returncode != 0 or "origin" not in remote.stdout.split():
        return True, ""

    fetch = _run_git(repo_dir, ["fetch", "origin"])
    if fetch.returncode != 0:
        return False, "git fetch に失敗しました: " + redact_mod.redact(fetch.stderr)

    pull = _run_git(repo_dir, ["pull", "--ff-only"])
    if pull.returncode != 0:
        return False, "git pull --ff-only に失敗しました: " + redact_mod.redact(pull.stderr)

    return True, ""
