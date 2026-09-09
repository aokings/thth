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

    `thth/core.py::_throw_locked()` が repo ロックの中・inflight 確認の後・
    queue を読む前に呼ぶ。

    **形を反転させてある（外部レビュー第 3 巡 P1）**。旧実装は「こういう場合は
    同期しなくてよい」という**例外を数え上げる**作りだった（repo_dir が無い・
    git repo でない・origin が無い、の 3 つを「成功扱い」として列挙）。例外が
    ひとつ増えるたびに素通りの経路ができる——実際、外部レビューは 2 巡目で
    「origin が無い」を、3 巡目で「.git が無い」を見つけた。queue ファイルは
    残っているのに `.git` だけ退避されていると、旧実装は「git repo でない」を
    無条件で成功扱いにしていたので、同期を経ずに select → 公開まで進み、
    書き戻しの `git add` で初めて失敗した（公開はもう取り消せない）。次も
    同じ形で新しい壊れ方が出るだろう。

    そこで**同期は必ず成功しなければならない・例外はただ 1 つだけ**という形に
    反転した。唯一の例外は `repo_dir` が**存在しない**ことだけ——この場合
    queue を読む先が無いので
    `core.list_queue_files()` は `os.path.isdir(queue_dir)` で空を返し、どのみち
    何も公開されない（安全）。`repo_dir` が存在する場合は、以降の一切を
    `_confirm_synced()` に渡し、**「成功したと確認できたときだけ」** `(True, "")`
    を返させる。ディレクトリはあるが `.git` が無い／`origin` が無い／
    `git remote` が失敗／fetch 失敗／`pull --ff-only` 失敗／pull 後に HEAD が
    upstream に追いついたと確認できない、など「確認できない」場合はすべて
    `(False, ...)` になる——分岐を列挙して素通りさせるのではなく、確認できな
    かったものはすべて同じ扱いで落ちる形にしてあるので、ここに載っていない
    新しい壊れ方が今後見つかっても、この関数を触らなくても安全側に倒れる。

    `(False, ...)` は呼び出し側 `core.py` で `exit_code=2`・
    `error="repo_sync_failed"` になり、**adapter.publish() は一度も呼ばれない**。

    `thth send`（同席の様態）は queue を読まないのでこの関数を呼ばない。
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        # 唯一の例外。queue を読む先そのものが無いので、どのみち公開されない。
        return True, ""

    return _confirm_synced(repo_dir)


def _confirm_synced(repo_dir: str) -> tuple:
    """`repo_dir` が存在する前提で、同期の成功を積極的に確認する。

    確認できたステップだけを通す（数え上げ式の逆）。どのステップであれ
    「成功した」と確認できなければ、その場で `(False, 理由)` を返す。
    """
    git_dir = _run_git(repo_dir, ["rev-parse", "--git-dir"])
    if git_dir.returncode != 0:
        return False, ("git repository として確認できませんでした（.git が見当たりません）: "
                        + redact_mod.redact(git_dir.stderr))

    remote = _run_git(repo_dir, ["remote"])
    if remote.returncode != 0:
        return False, "git remote の確認に失敗しました: " + redact_mod.redact(remote.stderr)
    if "origin" not in remote.stdout.split():
        return False, "origin という remote が見つかりません（同期元を確認できないため投稿しません）"

    fetch = _run_git(repo_dir, ["fetch", "origin"])
    if fetch.returncode != 0:
        return False, "git fetch に失敗しました: " + redact_mod.redact(fetch.stderr)

    pull = _run_git(repo_dir, ["pull", "--ff-only"])
    if pull.returncode != 0:
        return False, "git pull --ff-only に失敗しました: " + redact_mod.redact(pull.stderr)

    # 最終確認: pull --ff-only が exit 0 を返しただけでなく、HEAD が実際に
    # upstream に追いついたことを直接見る（「エラーが出なかった」ではなく
    # 「成功したと確認できた」で通すため）。
    head = _run_git(repo_dir, ["rev-parse", "HEAD"])
    upstream = _run_git(repo_dir, ["rev-parse", "@{u}"])
    if (head.returncode != 0 or upstream.returncode != 0
            or head.stdout.strip() != upstream.stdout.strip()):
        return False, "pull --ff-only の後、HEAD が upstream に追いついたことを確認できませんでした"

    return True, ""
