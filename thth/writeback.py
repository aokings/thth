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
import re
import subprocess

from . import redact as redact_mod

# front-matter は 1 行 1 項目の平たい `key: value`。**値に改行が入れば、そこから
# 先は「別の行」になる**——`thth revoke --reason $'x\nstatus: approved'` が
# `status: approved` を front-matter に足し、あとから書かれた行が後勝ちで効いて
# **撤回したはずの原稿が承認済みに戻る**（セキュリティ監査 2026-09-14・P1-3/P1-4）。
# 同じ口を `thth approve --by`・`THTH_ACTOR`・媒体が返した `post_id` も通る。
#
# **書く前に断る**（作法 5・loud reject）。`appenv.run_app_set()` の `--app-id`
# 検査と同じ型: 直せないものは黙って直さず、書かずに名指しで断る。
FORBIDDEN_IN_FRONT_MATTER = ("\n", "\r", "\x00")

# `post_id` は front-matter だけでなくファイル名・台帳の鍵にもなるので、
# 制御文字はまとめて弾く（`thth/core.py` が公開の直後に通す）。
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _forbidden_name(ch: str) -> str:
    return {"\n": "改行（\\n）", "\r": "復帰（\\r）", "\x00": "NUL"}.get(ch, repr(ch))


def check_front_matter_field(key, value) -> None:
    """front-matter に書ける鍵と値か。書けなければ `ValueError` で断る。

    値が `None` は「空文字列を書く」という既存の規約なので通す。数値等は
    `str()` した姿で検査する（書かれるのはその姿なので）。
    """
    for label, raw in (("鍵", key), ("値", value)):
        if raw is None:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        for ch in FORBIDDEN_IN_FRONT_MATTER:
            if ch in text:
                raise ValueError(
                    f"front-matter の{label}に{_forbidden_name(ch)}が入っています"
                    f"（{key!r}）。**書きませんでした。**"
                    f"（改行を含む値は front-matter の別の行になり、"
                    f"`status:` 等を後勝ちで上書きできてしまいます）")


def has_control_chars(text) -> bool:
    """制御文字（`\\x00`〜`\\x1f`・`\\x7f`）を含むか。`post_id` の検査に使う。"""
    if not isinstance(text, str):
        return False
    return bool(_CONTROL_RE.search(text))


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

    **鍵・値に改行・復帰・NUL があれば 1 文字も書かずに `ValueError`**
    （セキュリティ監査 2026-09-14・P1-3/P1-4）。`thth approve --by`・
    `thth revoke --reason`・`THTH_ACTOR`・媒体が返す `post_id` はどれもここを
    通るので、検査はこの 1 か所に置く。
    """
    for key, value in fields.items():
        check_front_matter_field(key, value)
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


def _run_git_bytes(repo_dir: str, args: list) -> subprocess.CompletedProcess:
    """`_run_git` のバイト列版（blob をそのまま取り出して比較するため）。"""
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True)


def upstream_sha(repo_dir: str) -> str | None:
    """`HEAD` が upstream（`@{u}`）と一致していればその OID、していなければ None。

    **同期を伴わない読み手（`thth board`・`thth queue`）の照合先**（外部レビュー
    第 5 巡 P2）。board は fetch しないので「いま remote がどうなっているか」は
    知らないが、**最後に取り込んだ remote の姿**（remote-tracking ref）は知って
    いる。ローカルの HEAD がそれと一致していなければ、**まだ remote に届いて
    いない commit がある**——`thth approve` が commit できたのに push を拒否された
    場合がこれで、以前の board はそれを「承認して待っているだけ」と表示していた
    （`approved_waiting: 1`・要確認 0 件）。一致しない間は照合先が無い（None）＝
    どのファイルも `unverified_content` になるので、board にそのまま出る。
    """
    # **`.git` の無いディレクトリで git を呼ばない**（監査 2 回目・P3-1）。
    # `git -C <dir> rev-parse HEAD` は**上の階層まで遡って repo を探す**ので、
    # `repo_dir` が `$THTH_ROOT/repos/_none`（存在しない・空）でも、その上に
    # 別の clone があれば**他人の repo の HEAD を照合先として返していた**。
    # 判定は `accounts.repo_state()` と同じ 2 つ（ディレクトリがある・`.git` がある）。
    if not repo_dir or not os.path.isdir(repo_dir):
        return None
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return None
    head = _run_git(repo_dir, ["rev-parse", "HEAD"])
    upstream = _run_git(repo_dir, ["rev-parse", "@{u}"])
    if head.returncode != 0 or upstream.returncode != 0:
        return None
    if head.stdout.strip() != upstream.stdout.strip():
        return None
    return head.stdout.strip() or None


def repo_toplevel(path: str) -> str | None:
    """`path` が入っている git repo の作業ツリーの根を返す（repo でなければ None）。

    `thth approve` は「台帳の `repo_dir`」ではなく「**そのファイルが入っている
    repo**」に commit する。masaru が自分の clone で承認することもあるし（VM の
    clone と同じ repo の別の clone）、レビューの再現でもそうしている。承認を
    記録する先は、承認したファイルが実際に置かれている repo であるべき。
    """
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        return None
    top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    if top.returncode != 0 or not top.stdout.strip():
        return None
    return top.stdout.strip()


def matches_synced_commit(repo_dir: str, path: str, *, tree_sha: str | None, disk_bytes: bytes | None = None) -> bool:
    """`path` の**いま読める中身**が、同期を確認した commit（HEAD）の中身と
    1 バイトも違わないことを確かめる（外部レビュー第 4 巡 P1）。

    `sync_repo()` が確かめるのは **commit の一致**（`HEAD == @{u}`）であって、
    **これから読むファイルの一致**ではなかった。`git pull --ff-only` は
    「その pull が触らないファイル」の作業ツリー側の変更を黙って残すので、

    1. remote で承認が撤回され、
    2. その撤回を正常に pull できて（`HEAD == @{u}` も成立）、
    3. それでも作業ツリーには**撤回前の承認済みファイル**が残っている

    という状態が普通に作れる。実際に再現した（stash の復元・エディタの
    「元に戻す」・退避ファイルの取り違え、どれでも起きる）。この状態で select は
    作業ツリーを読むので、**撤回済みの本文が承認済みとして公開される**。
    「masaru が見たものだけ出る」という設計の中心が破れる。

    そこで、**選ぶ対象になるファイル 1 本ごとに**「読める中身＝確認した commit の
    中身」を検査する。`git status` の解釈（staged／unstaged／untracked／
    `.gitignore` 済み）に頼らないのは、解釈の隙間がそのまま素通りの経路になる
    から——ここでは HEAD の blob と disk のバイト列を直接比べる。したがって
    未 commit の変更・staged の変更・追跡されていないファイル・無視されている
    ファイル・HEAD に無いファイルは、**すべて同じ 1 つの理由で**「確認できない」に
    倒れる。

    symlink は中身ではなくリンク先を読むことになるので、無条件で「確認できない」
    とする（リンク先は同期の対象外でありうる）。同期した木の外を指すパスも同様。

    **何も消さない・戻さない。** 作業中の変更はそのまま残し、その 1 本を
    select の候補から外して board に出すだけ（`select` の `unverified_content`）。

    **比較先は呼び出し側が渡した `tree_sha` に固定する**（外部レビュー第 5 巡 P1）。
    以前はここで `HEAD` を引き直していた。`HEAD` は動く——同期を確認したあとに
    別プロセスの `thth approve` が commit すれば HEAD はそちらへ動き、**push が
    remote に拒否されていても**作業ツリーと HEAD は一致するので `True` を返した。
    その結果、**remote に届いていない本文が公開された**（実プロセスで再現）。
    「確かめた commit」と「いま指しているもの」は別物なので、確かめた側の OID を
    そのまま持ち回る（`tree_sha` が None なら何も確認できない＝全部落とす）。

    `disk_bytes` を渡すと、その中身と `tree_sha` を比べる（ファイルを読み直さない）。
    `core.list_queue_files()` は**読んだのと同じバイト列**を渡す——読み直すと、
    検査したバイト列と select が実際に見るバイト列が別物になりうるから
    （検査と使用の間に書き換えられる隙間を作らない）。
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return False
    if os.path.islink(path):
        return False

    top = _run_git(repo_dir, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0 or not top.stdout.strip():
        return False
    toplevel = os.path.realpath(top.stdout.strip())

    real = os.path.realpath(path)
    rel = os.path.relpath(real, toplevel)
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return False

    if not tree_sha:
        return False
    blob = _run_git_bytes(repo_dir, ["cat-file", "blob", f"{tree_sha}:" + rel.replace(os.sep, "/")])
    if blob.returncode != 0:
        return False
    disk = disk_bytes
    if disk is None:
        try:
            with open(path, "rb") as f:
                disk = f.read()
        except OSError:
            return False
    return blob.stdout == disk


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
    # `rel_path` は 1 本でもリストでもよい（複数本まとめて承認する経路・
    # asmon 関東セッション指摘 2026-09-10）。**commit に入るのはここに並べた
    # パスだけ**（`--only`）。
    rel_paths = [rel_path] if isinstance(rel_path, str) else list(rel_path)
    add = _run_git(repo_dir, ["add", "--", *rel_paths])
    if add.returncode != 0:
        return False, redact_mod.redact(add.stderr)

    # **`--only` を付ける**（外部レビュー第 5 巡 P1・2）。`git add -- <path>` は
    # その 1 本を stage するだけで、**その後の素の `git commit` は index に既に
    # 載っている無関係な変更を全部巻き込む**。実際、別作業を stage したまま
    # `thth approve` すると、承認の commit に他人の作業が混ざって remote まで
    # 行った。`--only <path>` は一時 index を作ってそのパスだけを commit し、
    # **実 index の他のエントリはそのまま残す**（何も消さない・戻さない）。
    commit = _run_git(repo_dir, ["commit", "--only", "-m", message, "--", *rel_paths])
    if commit.returncode != 0:
        return False, redact_mod.redact(commit.stderr)

    # **無関係な stage 状態を、rebase の前後で保存する**（外部レビュー第 6 巡 P2-5）。
    #
    # `commit --only` は実 index の他のエントリを残す（第 5 巡の対応）。ところが
    # その直後の `pull --rebase --autostash` が、**staged も unstaged もまとめて
    # 退避して、戻すときは全部 unstaged にする。** 中身は消えないが、
    # 「stage してある／していない」の区別が消える。**人が途中まで組み立てた
    # コミットが崩れる。**
    #
    # そこで index を tree として控えておき、rebase のあとに**stage されていた
    # パスだけ**を控えから戻す。worktree の中身には触らない（autostash が戻す）。
    saved_tree, staged_paths = save_index(repo_dir)
    before_pull = _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()

    last_err = ""
    try:
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
    finally:
        restore_index(repo_dir, saved_tree, staged_paths, since=before_pull)

    msg = "push に失敗しました（commit は残っています。手で push してください）: " + redact_mod.redact(last_err)
    return False, msg


def save_index(repo_dir: str) -> tuple:
    """いまの index を tree として控え、stage されているパスの一覧を返す。

    戻り値 `(tree の OID または None, パスの一覧)`。控えられなければ `(None, [])`
    ——そのときは復元も行わない（**壊すくらいなら何もしない**）。
    """
    names = _run_git(repo_dir, ["diff", "--cached", "--name-only"])
    if names.returncode != 0:
        return None, []
    staged = [line.strip() for line in names.stdout.splitlines() if line.strip()]
    if not staged:
        return None, []
    tree = _run_git(repo_dir, ["write-tree"])
    if tree.returncode != 0 or not tree.stdout.strip():
        return None, []
    return tree.stdout.strip(), staged


def restore_index(repo_dir: str, tree: str | None, staged_paths: list, *,
                   since: str | None = None, log=None) -> None:
    """控えた tree から、stage されていたパスの index エントリだけを戻す。

    **rebase が触ったファイルは戻さない**（外部レビュー第 7 巡 P2-3）。
    控えは「pull の前の中身」なので、pull で remote の変更が入ったファイルにこれを
    当てると、**その変更を打ち消す差分が stage される**——別 clone から届いた修正を
    黙って巻き戻す形になる。**戻すのは、pull が触らなかったファイルだけ。**

    触られたファイルは stage 状態を戻せない（autostash のまま unstaged で残る）ので、
    **黙らずにそう述べる。**
    """
    if not tree or not staged_paths:
        return
    skip = set()
    if since:
        moved = _run_git(repo_dir, ["diff", "--name-only", since, "HEAD"])
        if moved.returncode == 0:
            skip = {line.strip() for line in moved.stdout.splitlines() if line.strip()}
    restorable = [p for p in staged_paths if p not in skip]
    untouched = [p for p in staged_paths if p in skip]
    if restorable:
        _run_git(repo_dir, ["restore", "--staged", "--source", tree, "--", *restorable])
    if untouched and log is not None:
        log("stage 状態を戻せなかったファイルがあります（取り込みで中身が変わったため。"
            "中身は残っています）: " + ", ".join(untouched[:3]))


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
        return True, "", None

    return _confirm_synced(repo_dir)


def _confirm_synced(repo_dir: str) -> tuple:
    """`repo_dir` が存在する前提で、同期の成功を積極的に確認する。

    確認できたステップだけを通す（数え上げ式の逆）。どのステップであれ
    「成功した」と確認できなければ、その場で `(False, 理由)` を返す。
    """
    git_dir = _run_git(repo_dir, ["rev-parse", "--git-dir"])
    if git_dir.returncode != 0:
        return False, ("git repository として確認できませんでした（.git が見当たりません）: "
                        + redact_mod.redact(git_dir.stderr)), None

    remote = _run_git(repo_dir, ["remote"])
    if remote.returncode != 0:
        return False, "git remote の確認に失敗しました: " + redact_mod.redact(remote.stderr), None
    if "origin" not in remote.stdout.split():
        return False, "origin という remote が見つかりません（同期元を確認できないため投稿しません）", None

    fetch = _run_git(repo_dir, ["fetch", "origin"])
    if fetch.returncode != 0:
        return False, "git fetch に失敗しました: " + redact_mod.redact(fetch.stderr), None

    pull = _run_git(repo_dir, ["pull", "--ff-only"])
    if pull.returncode != 0:
        return False, "git pull --ff-only に失敗しました: " + redact_mod.redact(pull.stderr), None

    # 最終確認: pull --ff-only が exit 0 を返しただけでなく、HEAD が実際に
    # upstream に追いついたことを直接見る（「エラーが出なかった」ではなく
    # 「成功したと確認できた」で通すため）。
    head = _run_git(repo_dir, ["rev-parse", "HEAD"])
    upstream = _run_git(repo_dir, ["rev-parse", "@{u}"])
    if (head.returncode != 0 or upstream.returncode != 0
            or head.stdout.strip() != upstream.stdout.strip()):
        return False, "pull --ff-only の後、HEAD が upstream に追いついたことを確認できませんでした", None

    # **確かめた OID をそのまま返す**（外部レビュー第 5 巡 P1）。呼び出し側は
    # これを持ち回り、queue の照合先に使う。HEAD を引き直させない。
    return True, "", head.stdout.strip()
