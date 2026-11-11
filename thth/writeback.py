"""front-matter の書き戻し ＋ git add/commit/fetch・rebase/push（設計 §4.3）。

`rewrite_front_matter()` が書き換えるのは `status`・`post_id`・`posted_at` の
3 行だけ。本文には触らない。push 失敗は commit を残して非ゼロ（手で push できる
状態を残す。inflight は消さない・呼び出し側の core.py の責務）。

`set_front_matter_fields()`（外部レビュー §1・受け入れ 1〜5）は `thth approve` 用の
もっと汎用の書き換えで、任意のキーを書ける。既存のキーは値だけ差し替え、front-matter
に無いキー（`approved_sha`・`approved_at` は新しい schema なので既存ファイルには
無いことがある）は閉じ `---` の直前に追加する。

`sync_repo()`（外部レビュー再レビュー A・2026-09-09）は利用者 repo を select より前に
同期する。`commit_and_push()` の `validate` 引数（外部レビュー再レビュー B）は
push の直前・取り込み（rebase）の後にもう一度「送った本文といまの内容が一致するか」を
確かめる口。どちらも `thth/core.py` から呼ばれる。
"""
from __future__ import annotations

import os
import sys
import re
import subprocess

from . import gitopts
from . import jst as jst_mod
from . import postid as postid_mod
from . import queuefile as queuefile_mod
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
# 制御文字はまとめて弾く。**判定の正本は `thth/postid.py`**（監査 2 回目・P3-9）
# ——ここに別の綴りを持つと、`postid.is_usable()` しか通らない口
# （`collect._safe_post_id()`・`sent.write()`）だけが守られないまま残る。


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
    """制御文字（`\\x00`〜`\\x1f`・`\\x7f`）を含むか。

    **判定は `postid.CONTROL_RE` の 1 本**（監査 2 回目・P3-9）。この名前は
    呼び出し側のために残す。
    """
    if not isinstance(text, str):
        return False
    return bool(postid_mod.CONTROL_RE.search(text))


class PushValidationFailed(Exception):
    """`commit_and_push()` の `validate` コールバックが不一致を返した。

    取り込み（rebase）の後・push の前に検知したので、push はしていない
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


def _find_duplicate_front_matter_keys(lines: list, end_idx: int) -> list:
    """`lines[1:end_idx]`（front-matter の中身）に同じ**top-level**の鍵が
    2 回以上あれば、その鍵名の一覧を返す（`thth/queuefile.py::_parse_kv()` と
    同じ判定・セキュリティ監査 2026-09-14「撤回が効かない嘘」）。

    **字下げした行は見ない**（`thth: 2` の `posts:` 配列・`bundle.py` 参照）。
    束の原稿は段ごとに `  - index: 1`・`  - index: 2` … と同じ形の行が複数
    並ぶのが正常なので、字下げまで対象にすると**それ自体を重複と誤検知する**
    （実際にスレッド連投の `thth revoke` で踏んだ）。`set_front_matter_fields()`
    はキーの並び替えを字下げの有無で区別している（`end_idx` の中を平たく走査
    しているのは従来どおりだが、**重複の判定だけ**は top-level に絞る）。
    """
    seen: set = set()
    duplicates: list = []
    for i in range(1, end_idx):
        line = lines[i]
        if line[:1] in (" ", "\t"):
            continue
        if not line.strip() or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if not key:
            continue
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
    return duplicates


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
    with open(path, encoding="utf-8") as f:
        text = f.read()
    updated = front_matter_text(text, fields, label=path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)


def front_matter_text(text, fields, *, label="draft"):
    """One pure transformation shared by CLI and pinned server writes."""
    for key, value in fields.items():
        check_front_matter_field(key, value)
    path = label
    lines = text.split("\n")
    try:
        end_idx = _split_front_matter_lines(lines)
    except ValueError as e:
        raise ValueError(f"{e}: {path}") from e

    # **重複した鍵があれば 1 文字も書かない**（セキュリティ監査 2026-09-14・
    # 「撤回が効かない嘘」）。この関数は最初に見つかった行**だけ**を書き換える
    # ので、鍵が重複していると 2 つ目以降の行が後勝ちで効いたまま残る
    # ——`thth revoke` はこの検査より前（`queuefile.parse()` の `malformed`）で
    # 止まるが、`thth approve`・`select` の post_id 書き戻しなど、この関数を
    # 通る他の経路も同じ穴を持つ。書く前に断る（作法 5・loud reject）。
    duplicate_keys = _find_duplicate_front_matter_keys(lines, end_idx)
    if duplicate_keys:
        raise ValueError(
            queuefile_mod.duplicate_keys_message(duplicate_keys)
            + f"。**書きませんでした。**: {path}")

    remaining = dict(fields)
    for i in range(1, end_idx):
        if lines[i][:1] in (" ", "\t"):
            continue
        key = lines[i].split(":", 1)[0].strip()
        if key in remaining:
            value = remaining.pop(key)
            lines[i] = f"{key}: {value if value is not None else ''}"
    if remaining:
        new_lines = [f"{key}: {value if value is not None else ''}" for key, value in remaining.items()]
        lines[end_idx:end_idx] = new_lines
    return "\n".join(lines)


def _run_git(repo_dir: str, args: list) -> subprocess.CompletedProcess:
    from . import managed_repo
    if managed_repo.account_for(repo_dir) is not None:
        return managed_repo.run(repo_dir, args)
    return subprocess.run(gitopts.git(repo_dir, *args), capture_output=True, text=True)


def _run_git_bytes(repo_dir: str, args: list, *, input: bytes | None = None) -> subprocess.CompletedProcess:
    """`_run_git` のバイト列版（blob をそのまま取り出して比較するため）。"""
    from . import managed_repo
    if managed_repo.account_for(repo_dir) is not None:
        return managed_repo.run(repo_dir, args, text=False, input=input)
    return subprocess.run(gitopts.git(repo_dir, *args), capture_output=True, input=input)


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


# `sync_state()` の状態（設計 3.7.0 §B1）。
SYNC_SYNCED = "synced"            # HEAD == @{u}
SYNC_BEHIND_ONLY = "behind_only"  # 遅れているだけ（ahead 0）——次の run の sync_repo が取り込む
SYNC_AHEAD = "ahead"              # push していない commit がある（遅れは無い）
SYNC_DIVERGED = "diverged"        # 両方
SYNC_NO_UPSTREAM = "no_upstream"  # upstream が無い・数えられない
SYNC_NOT_REPO = "not_repo"        # repo が無い・git でない


def sync_state(repo_dir: str) -> dict:
    """HEAD と `@{u}`（**最後に取り込んだ remote の姿**）の差を数える。**fetch しない。**

    `thth account` と `thth board` が同じ 1 つの関数で repo の状態を言うための土台
    （設計 3.7.0 §B1・「board と食い違わない」）。遅れているだけ（ahead 0）なら、
    `thth run` の `sync_repo()` が次の実行で取り込む——「投稿できません」ではない。
    戻り値 `{"state", "ahead", "behind"}`。数えられなければ `ahead`・`behind` は None。
    """
    if not repo_dir or not os.path.isdir(repo_dir) or not os.path.exists(
            os.path.join(repo_dir, ".git")):
        return {"state": SYNC_NOT_REPO, "ahead": None, "behind": None}
    counts = _run_git(repo_dir, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    parts = counts.stdout.split() if counts.returncode == 0 else []
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return {"state": SYNC_NO_UPSTREAM, "ahead": None, "behind": None}
    ahead, behind = int(parts[0]), int(parts[1])
    state = (SYNC_SYNCED if not ahead and not behind else
             SYNC_BEHIND_ONLY if not ahead else
             SYNC_AHEAD if not behind else SYNC_DIVERGED)
    return {"state": state, "ahead": ahead, "behind": behind}


def behind_remote(repo_dir: str) -> dict:
    """利用者 repo（queue の repo）を **fetch だけして** remote と比べる（T8-2・
    kopicha 続報）。**pull はしない**——`git fetch` は実際に行うが、
    remote-tracking ref が進むだけで作業ツリーは動かない。

    なぜ要るか: `thth approve`（と `revoke`・`throw`・`collect`・`retract`）は
    既に `sync_repo()` を通しているが、**読むだけの口**（`thth queue`・
    `schedule`・`board`）は通していない——**読むだけの口が勝手に pull するのは
    避ける**のが設計の芯（loud reject と対になる規律）。だから push 直後に
    `schedule` で見ると、まだ取り込み前の一覧が出て「反映されていない」と
    見えていた。ここは**遅れていることを言う**ための土台で、取り込みは
    `thth pull`（T8-3）か `thth approve` に任せる。

    **これは `thth board` の道具自身の自己更新チェック（`_pull_locked` の
    docstring「board は取りに行かない」）とは別の対象**——あちらは配布 repo
    （app 自身）の話で board は意図して fetch しない。こちらは account ごとの
    **利用者 queue の repo**の話で、ここでだけ fetch する。

    戻り値: `{"behind": n|None, "ahead": n|None, "head": "<7桁>",
    "fetched_at": "<ISO 8601>", "reason": "<読めない理由>"|None}`。

    `behind`/`ahead` は fetch できて HEAD と `@{u}` の差分を数えられたときだけ
    整数。**読めなければ `None`**（`0` と混ぜない——`0` は「確かめて遅れて
    いない」の意味なので、「確かめられなかった」と絶対に同じ値にしない）。
    そのときは `reason` に理由が入る。

    **例外が 1 つ**（3.8.2）: repo のロックを取れず fetch しなかったときは、
    `reason` が `BEHIND_UNCHECKED`・`fetched_at` が None で、`behind`/`ahead` は
    前回取り込んだ remote の姿で数えた整数（数えられなければ None）。
    """
    fetched_at = jst_mod.iso()
    if not repo_dir or not os.path.isdir(repo_dir):
        return {"behind": None, "ahead": None, "head": None,
                "fetched_at": fetched_at, "reason": "repo が見当たりません"}
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return {"behind": None, "ahead": None, "head": None,
                "fetched_at": fetched_at, "reason": "git repository ではありません"}

    head_now = _run_git(repo_dir, ["rev-parse", "--short", "HEAD"])
    head7 = head_now.stdout.strip() if head_now.returncode == 0 else None

    remote = _run_git(repo_dir, ["remote"])
    if remote.returncode != 0 or "origin" not in remote.stdout.split():
        return {"behind": None, "ahead": None, "head": head7,
                "fetched_at": fetched_at, "reason": "origin という remote が見つかりません"}

    # **repo のロックを待たずに取りに行き、取れたときだけ fetch する**（依頼 3.8.2 の
    # 裁定 1）。読むだけの口（`queue`・`schedule`・`board`・手元パスの読み替え）の
    # fetch がロックの中の同期と同時に走ると、`refs/remotes/origin/*` の書き込みが
    # ぶつかって同期の側の fetch が失敗し、承認が「git fetch に失敗しました」で
    # 断られうる。取れなければ fetch せず、**前回取り込んだ remote の姿で数えて**、
    # 「いま確かめられない」と `reason` に言う（`fetched_at` は None——確かめていない
    # 時刻を書かない）。ロックの置き場に触れないとき（OSError）も同じ扱い。
    from . import accounts as accounts_mod
    from . import lock as lock_mod
    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except (lock_mod.LockBusy, OSError):
        return _behind_unchecked(repo_dir, head7)
    try:
        # FETCH_HEAD は書かない（読み手が書く理由が無い・同期は読まないが、書き手を減らす）。
        fetch = _run_git(repo_dir, ["fetch", "--no-write-fetch-head", "origin"])
    finally:
        repo_lock.release()
    # **fetch した直後の時刻に取り直す**（呼んだ時刻ではなく、実際に確かめた時刻）。
    fetched_at = jst_mod.iso()
    if fetch.returncode != 0:
        return {"behind": None, "ahead": None, "head": head7,
                "fetched_at": fetched_at,
                "reason": "git fetch に失敗しました: " + redact_mod.redact(fetch.stderr)}

    counts = _run_git(repo_dir, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    if counts.returncode != 0:
        return {"behind": None, "ahead": None, "head": head7,
                "fetched_at": fetched_at,
                "reason": "HEAD と upstream の差分を確認できませんでした: "
                          + redact_mod.redact(counts.stderr)}
    parts = counts.stdout.split()
    if len(parts) != 2:
        return {"behind": None, "ahead": None, "head": head7,
                "fetched_at": fetched_at, "reason": "差分の出力を読めませんでした"}
    ahead_str, behind_str = parts
    return {"behind": int(behind_str), "ahead": int(ahead_str), "head": head7,
            "fetched_at": fetched_at, "reason": None}


# `behind_remote()` が repo のロックを取れず fetch しなかったときの `reason`（静的）。
# 呼び出し側はこの文字列と照らして「いま確かめられない」の 1 行を足す。
BEHIND_UNCHECKED = ("いま確かめられません（他の実行が repo を使用中）。"
                    "前回取り込んだ remote の姿で数えています")


def _behind_unchecked(repo_dir: str, head7) -> dict:
    """fetch せずに、前回取り込んだ remote の姿（`@{u}`）と HEAD の差を数える。

    `behind`・`ahead` は数えられれば整数（前回の fetch の結果）、数えられなければ None。
    どちらでも `reason` は `BEHIND_UNCHECKED`、`fetched_at` は None。
    """
    counts = _run_git(repo_dir, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    parts = counts.stdout.split() if counts.returncode == 0 else []
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        ahead, behind = int(parts[0]), int(parts[1])
    else:
        ahead = behind = None
    return {"behind": behind, "ahead": ahead, "head": head7,
            "fetched_at": None, "reason": BEHIND_UNCHECKED}


def push_pending(repo_dir: str) -> dict:
    """手元の承認の commit を push し直す（`thth pull --push-pending`・依頼 3.8.2 の裁定 2）。

    **呼び出し側が repo のロックを握っている前提。** `fetch origin` のあと HEAD と
    `@{u}` を数え、**ahead があって behind が 0（早送りで押せる）ときだけ** push する。
    behind があれば押さない——rebase もしない（手元の commit の並びを道具が黙って
    書き換えない）。戻り値 `{"pushed", "count", "subjects", "ahead", "behind", "error"}`。

    ここは**人の commit も押す口**（`--by` で名乗った人が押す）なので、並びは書き換えない。
    並びを書き換えて載せ直すのは、未 push の並びが**すべて道具自身の書き戻し**だと
    確かめられたときの `sync_repo()` だけ（2026-11-04）。人の commit は、どちらの口でも
    道具が書き換えない。
    """
    out = {"pushed": False, "count": 0, "subjects": [], "ahead": None, "behind": None,
           "error": None}
    fetch = _run_git(repo_dir, ["fetch", "origin"])
    if fetch.returncode != 0:
        out["error"] = "git fetch に失敗しました: " + redact_mod.redact(fetch.stderr)
        return out
    counts = _run_git(repo_dir, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    parts = counts.stdout.split() if counts.returncode == 0 else []
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        out["error"] = "HEAD と upstream の差分を確認できませんでした（upstream が無い？）"
        return out
    out["ahead"], out["behind"] = int(parts[0]), int(parts[1])
    if out["behind"]:
        out["error"] = (f"upstream に {out['behind']} commit 遅れているので押しません"
                        "（rebase はしません。手元の commit と upstream のどちらを残すかは人が決めます）。"
                        "手元が THTH の書き戻しの commit だけなら、--push-pending を外した"
                        " thth pull <口座> が upstream の上に載せ直して push します")
        return out
    if not out["ahead"]:
        return out
    log = _run_git(repo_dir, ["log", "--format=%s", "@{u}..HEAD"])
    out["subjects"] = [line for line in log.stdout.splitlines() if line.strip()]
    out["count"] = out["ahead"]
    push = _run_git(repo_dir, ["push"])
    if push.returncode != 0:
        out["error"] = "push に失敗しました（commit は残っています）: " + redact_mod.redact(push.stderr)
        return out
    out["pushed"] = True
    return out


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
    return matches_synced_commit_many(repo_dir, [(path, disk_bytes)], tree_sha=tree_sha)[0]


def matches_synced_commit_many(repo_dir: str, items, *, tree_sha: str | None) -> list:
    """`matches_synced_commit()` の判定を `(path, disk_bytes)` の並びへまとめて行う。

    戻り値は `items` と同じ順の bool の list。判定の中身は 1 本のときと同じで、1 本の口も
    ここを通る。違うのは blob の読み方だけ（git 2 回で全部読む）——board は原稿 1 本ごとに
    git を 2 回呼び、原稿が増えるほど遅くなっていた（VM で 90 秒のうち 75 秒）。

    **読んだ blob はこの呼び出しの中の変数にしか置かない。** 呼び出しをまたぐ覚えを
    持つと、投稿の経路が古い結果を見る口になる。比べるのは毎回 `disk_bytes` そのもの。
    """
    items = list(items)
    verdicts = [False] * len(items)
    if not repo_dir or not os.path.isdir(repo_dir):
        return verdicts
    live = [i for i, (path, _) in enumerate(items) if not os.path.islink(path)]
    if not live:
        return verdicts

    top = _run_git(repo_dir, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0 or not top.stdout.strip():
        return verdicts
    toplevel = os.path.realpath(top.stdout.strip())

    rels = {}
    for i in live:
        rel = os.path.relpath(os.path.realpath(items[i][0]), toplevel)
        if not (os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep)):
            rels[i] = rel.replace(os.sep, "/")

    if not tree_sha or not rels:
        return verdicts
    blobs = _read_blobs(repo_dir, tree_sha, list(dict.fromkeys(rels.values())))
    for i, rel in rels.items():
        blob = blobs.get(rel)
        if blob is None:
            continue
        path, disk = items[i]
        if disk is None:
            try:
                with open(path, "rb") as f:
                    disk = f.read()
            except OSError:
                continue
        verdicts[i] = blob == disk
    return verdicts


def _read_blobs(repo_dir: str, tree_sha: str, rels: list) -> dict:
    """`tree_sha` の中の blob を `{rel: バイト列}` で返す。blob でない・無いパスは入らない。

    **読み切れなければ空を返す**（＝全部「確認できない」）。途中までの結果を使わない。
    `cat-file --batch` は 1 行 1 件の口なので、改行を含むパスだけは 1 本ずつ読む。
    """
    blobs = {}
    batched = []
    for rel in rels:
        spec = f"{tree_sha}:{rel}"
        if "\n" not in spec and "\r" not in spec:
            batched.append(rel)
            continue
        one = _run_git_bytes(repo_dir, ["cat-file", "blob", spec])
        if one.returncode == 0:
            blobs[rel] = one.stdout
    if not batched:
        return blobs
    request = b"".join(os.fsencode(f"{tree_sha}:{rel}") + b"\n" for rel in batched)
    try:
        out = _run_git_bytes(repo_dir, ["cat-file", "--batch"], input=request)
    except OSError:
        return {}
    answers = _parse_batch(out.stdout, len(batched)) if out.returncode == 0 else None
    if answers is None:
        return {}
    blobs.update((rel, blob) for rel, blob in zip(batched, answers) if blob is not None)
    return blobs


def _parse_batch(stdout: bytes, count: int) -> list | None:
    """`cat-file --batch` の出力を `count` 件ぶん読む。blob 以外・無いものは None の要素。

    形が崩れている・足りない・余る、なら全体を None にする。
    """
    answers = []
    pos = 0
    for _ in range(count):
        end = stdout.find(b"\n", pos)
        if end < 0:
            return None
        header = stdout[pos:end].split(b" ")
        pos = end + 1
        if header[-1] in (b"missing", b"ambiguous"):
            answers.append(None)
            continue
        if len(header) != 3 or not header[2].isdigit():
            return None
        size = int(header[2])
        if stdout[pos + size:pos + size + 1] != b"\n":
            return None
        answers.append(stdout[pos:pos + size] if header[1] == b"blob" else None)
        pos += size + 1
    return answers if pos == len(stdout) else None


def commit_and_push(repo_dir: str, *, rel_path: str, message: str, validate=None) -> tuple:
    """`git add -- <rel_path>` → commit → `fetch origin`・`rebase --autostash @{u}` → push（3.8.2 までは `pull --rebase --autostash`）。

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
    committed, err = commit_local(repo_dir, rel_path=rel_path, message=message)
    if not committed:
        return False, err
    return push_committed(repo_dir, validate=validate)


SPARSE_PREFIX = "sparse_checkout_outside"


def sparse_excluded(repo_dir: str, rel_paths) -> list:
    """sparse-checkout の定義の外にあるパス（repo の根からの相対・`/` 区切り）の list（4.2.2・S6 §17）。

    sparse-checkout が無効なら空。cone の形（`core.sparseCheckoutCone`）なら定義のディレクトリから決める
    （根の直下のファイル・定義のディレクトリの下・定義のディレクトリの親の直下のファイルが中）。cone でない形
    （gitignore の型）は、定義のファイルを除外の型として `git check-ignore --no-index` に照らす（型に当たる＝中）。
    判定できなければ空を返し、`git add` の断りを `commit_local` が言い換える。ファイルが無くても判定できる。
    """
    enabled = _run_git(repo_dir, ["config", "--bool", "core.sparseCheckout"])
    if enabled.returncode != 0 or enabled.stdout.strip() != "true":
        return []
    paths = [str(rel).replace(os.sep, "/").strip("/")
             for rel in ([rel_paths] if isinstance(rel_paths, str) else list(rel_paths))]
    cone = _run_git(repo_dir, ["config", "--bool", "core.sparseCheckoutCone"])
    if cone.returncode != 0 or cone.stdout.strip() != "true":
        where = _run_git(repo_dir, ["rev-parse", "--git-path", "info/sparse-checkout"])
        if where.returncode != 0 or not where.stdout.strip():
            return []
        patterns = os.path.join(repo_dir, where.stdout.strip())
        if not os.path.isfile(patterns):
            return []
        out = []
        for path in paths:
            hit = _run_git(repo_dir, ["-c", f"core.excludesFile={patterns}", "check-ignore", "--no-index", "-q",
                                      "--", path])
            if hit.returncode == 1:
                out.append(path)
        return out
    listed = _run_git(repo_dir, ["sparse-checkout", "list"])
    if listed.returncode != 0:
        return []
    dirs = [line.strip().strip("/") for line in listed.stdout.splitlines() if line.strip()]
    out = []
    for path in paths:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        inside = (parent == ""
                  or any(path.startswith(d + "/") for d in dirs)
                  or any(d == parent or d.startswith(parent + "/") for d in dirs))
        if not inside:
            out.append(path)
    return out


def sparse_message(repo_dir: str, rel_paths) -> str:
    """sparse-checkout の外を言う 1 行（符丁で始める・直し方つき）。"""
    paths = sorted({os.path.dirname(p) or p for p in rel_paths})
    return (f"{SPARSE_PREFIX}: sparse-checkout の定義の外なので repo に載せません: {'・'.join(paths)}"
            f"（載せるなら git -C {repo_dir} sparse-checkout add {' '.join(paths)}）")


def sparse_outside_dirs(repo_dir: str, dirs) -> str | None:
    """書く前の確かめ（4.2.2・S6 §17）: 置き場（実パス）のどれかが sparse-checkout の定義の外なら、理由と
    直し方の 1 行（`sparse_message`）。みな中か、repo の外の置き場だけなら None。"""
    root = os.path.realpath(repo_dir)
    probes = []
    for directory in dirs:
        base = os.path.realpath(directory)
        if base.startswith(root + os.sep):
            probes.append(os.path.relpath(os.path.join(base, "thth_probe.ndjson"), root))
    outside = sparse_excluded(repo_dir, probes) if probes else []
    return sparse_message(repo_dir, outside) if outside else None


def commit_local(repo_dir: str, *, rel_path, message: str) -> tuple:
    """`git add -- <rel_path>` → `commit --only`（push しない）。`(ok, error)` を返す。

    `thth approve --confirm-file`（依頼 3.8.2 件 2）は 1 本ずつ commit して最後に
    1 回だけ push する。`commit_and_push()` はこれと `push_committed()` を続けて
    呼ぶだけ——commit の仕方（`--only`）を 2 通りに持たない。
    """
    # `rel_path` は 1 本でもリストでもよい（複数本まとめて承認する経路・
    # asmon 関東セッション指摘 2026-09-10）。**commit に入るのはここに並べた
    # パスだけ**（`--only`）。
    rel_paths = [rel_path] if isinstance(rel_path, str) else list(rel_path)
    # 前から rebase の途中なら commit しない（detached HEAD に積み、あとの abort で消える・4.4.2 監査）。
    if _rebase_in_progress(repo_dir):
        return False, REBASE_IN_PROGRESS.format(repo=repo_dir)
    # **sparse-checkout の定義の外は載せない**（4.2.2・設計 S6 §17）。`git add --sparse` で載せると、次の
    # 同期（rebase・merge）で git がそのファイルを作業ツリーから消し（skip-worktree）、追記の台帳を次の回が
    # 空から書き直して履歴を上書きする（本物の git で確かめた）。理由と直し方を言って、git には触らない。
    outside = sparse_excluded(repo_dir, rel_paths)
    if outside:
        return False, sparse_message(repo_dir, outside)
    add = _run_git(repo_dir, ["add", "--", *rel_paths])
    if add.returncode != 0:
        if "sparse-checkout" in (add.stderr or ""):
            # 判定できなかった定義で git が断った。同じ符丁で言い換える（--sparse では載せない）。
            return False, sparse_message(repo_dir, rel_paths)
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
    return True, ""


def push_committed(repo_dir: str, *, validate=None) -> tuple:
    """ローカルの commit を upstream に取り込んで push する（`commit_and_push()` の後半）。

    `(ok, error)` を返す。失敗しても commit はローカルに残す（手で push できる状態）。
    """
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
    # 人や前の実行が残した rebase は、こちらで abort しない（その途中の作業を消す）。
    if _rebase_in_progress(repo_dir):
        return False, REBASE_IN_PROGRESS.format(repo=repo_dir)
    try:
        ok, last_err = _rebase_and_push(repo_dir, validate=validate)
    except PushValidationFailed:
        # 照合で止めた commit に印を付ける（4.x・2026-11-04）。`sync_repo()` は書き戻しの
        # commit を載せ直して push するが、**この commit だけは載せ直さない**——載せ直すと
        # 照合を飛ばして押すことになる。
        _hold_head(repo_dir)
        raise
    if ok:
        return True, ""
    msg = "push に失敗しました（commit は残っています。手で push してください）: " + redact_mod.redact(last_err)
    return False, msg


def _rebase_and_push(repo_dir: str, *, validate=None) -> tuple:
    """`fetch origin` → `rebase --autostash @{u}` → push を 2 回まで。`(ok, 最後の stderr)`。

    stage の区別は前後で保つ（`save_index`・`restore_index`）。`validate` が False なら
    `PushValidationFailed`。`push_committed()` と `sync_repo()` の載せ直しが使う。
    """
    saved_tree, staged_paths = save_index(repo_dir)
    before_pull = _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()

    last_err = ""
    try:
        for _attempt in range(2):
            # **`pull --rebase` をやめ、`fetch origin` → `rebase --autostash @{u}` に
            # 分ける**（依頼 3.8.2 件 1・報告 r20260924-9f07f36b）。`git pull` は自分で
            # fetch したあと **FETCH_HEAD を読み直して**取り込む相手を決める。その間に
            # 別のプロセスの fetch が FETCH_HEAD に行を足すと（`fetch --append`・枝を
            # 名指しした fetch）、for-merge の行が 2 つになり「Cannot rebase onto
            # multiple branches」で断られる（本物の git で再現）。upstream の ref
            # （`@{u}`）を名指しすれば、FETCH_HEAD を誰が書いても読まない。
            fetch = _run_git(repo_dir, ["fetch", "origin"])
            if fetch.returncode != 0:
                last_err = fetch.stderr
                continue
            rebase = _run_git(repo_dir, ["rebase", "--autostash", "@{u}"])
            if rebase.returncode != 0:
                last_err = rebase.stderr
                # ぶつかった rebase を途中のまま残さない（残すと次の取り込みも push も
                # 「rebase の途中」で断られ続ける・kopicha #4254 10/22）。abort で元の commit と
                # autostash に戻る。
                _abort_rebase_in_progress(repo_dir)
                continue
            if validate is not None and not validate():
                raise PushValidationFailed(
                    "取り込み（rebase）のあと、本文が送った内容と食い違うため push しません"
                    "（commit はローカルに残っています。手で確認してください）")
            push = _run_git(repo_dir, ["push"])
            if push.returncode == 0:
                return True, ""
            last_err = push.stderr
    finally:
        restore_index(repo_dir, saved_tree, staged_paths, since=before_pull)
    return False, last_err


# 照合（`validate`）で push を止めた commit の印。手元だけの ref で、push しない（`git push` は
# 枝だけを送る）。印の commit が手元の未 push の並びに居る間、`sync_repo()` は載せ直さない。
HELD_REF = "refs/thth/push-held"


def _hold_head(repo_dir: str) -> None:
    _run_git(repo_dir, ["update-ref", HELD_REF, "HEAD"])


def _rebase_in_progress(repo_dir: str) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        marker = _run_git(repo_dir, ["rev-parse", "--git-path", name])
        path = marker.stdout.strip()
        if marker.returncode == 0 and path and os.path.exists(os.path.join(repo_dir, path)):
            return True
    return False


REBASE_IN_PROGRESS = ("repo が rebase の途中です（前の取り込みがぶつかったまま）。書き戻しません。"
                      "git -C {repo} status で見て、rebase --abort か --continue で戻してください")


def _abort_rebase_in_progress(repo_dir: str) -> None:
    """自分が始めた rebase がぶつかったときだけ呼ぶ（途中でなければ何もしない）。"""
    if _rebase_in_progress(repo_dir):
        _run_git(repo_dir, ["rebase", "--abort"])


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
    `git remote` が失敗／fetch 失敗／`merge --ff-only @{u}` 失敗／取り込み後に HEAD が
    upstream に追いついたと確認できない、など「確認できない」場合はすべて
    `(False, ...)` になる——分岐を列挙して素通りさせるのではなく、確認できな
    かったものはすべて同じ扱いで落ちる形にしてあるので、ここに載っていない
    新しい壊れ方が今後見つかっても、この関数を触らなくても安全側に倒れる。

    `(False, ...)` は呼び出し側 `core.py` で `exit_code=2`・
    `error="repo_sync_failed"` になり、**adapter.publish() は一度も呼ばれない**。

    `thth send`（同席の様態）は queue を読まないのでこの関数を呼ばない。

    **手元に残った THTH 自身の書き戻しの commit は載せ直して push する**（2026-11-04・
    関東の口座）。書き戻しの push が落ちて commit が手元に残る（ahead）間に origin が
    進む（behind）と、早送りできず、`thth inflight resolve`・`thth pull`・投稿が全部
    ここで止まって道具が自分で解けなかった。未 push の並びが**すべて**道具の書き戻しで
    （`_own_writeback_files()`）、upstream の変更と同じファイルに重ならないときだけ、
    `_rebase_and_push()` で upstream の上に載せ直して push し、そのあと今までどおり
    `HEAD == @{u}` を確かめて通す。人の commit が 1 本でも混ざれば書き換えない
    （`push_pending()` の「手元の commit の並びを道具が黙って書き換えない」はそのまま）。
    **呼び出し側が repo のロックを握っている前提。** 読むだけ・下見の経路（dry-run の
    投稿・承認の 1 段目・パスの探し直し）は push しない `sync_repo_no_push()` を呼ぶ。
    """
    return _sync(repo_dir, push=True)


def sync_repo_no_push(repo_dir: str) -> tuple:
    """`sync_repo()` と同じ同期で、手元の書き戻しの commit を載せ直さない（push しない）。

    dry-run の投稿・承認の 1 段目（digest を出すだけ）・パスの探し直しが使う。下見で
    origin を書き換えない。載せ直せる commit が残っていれば、断りの理由で
    `thth pull <口座>` を案内する。
    """
    return _sync(repo_dir, push=False)


def _sync(repo_dir: str, *, push: bool) -> tuple:
    if not repo_dir or not os.path.isdir(repo_dir):
        # 唯一の例外。queue を読む先そのものが無いので、どのみち公開されない。
        return True, "", None

    return _confirm_synced(repo_dir, push=push)


def _confirm_synced(repo_dir: str, *, push: bool = True) -> tuple:
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

    # **`pull --ff-only` ではなく `merge --ff-only @{u}`**（依頼 3.8.2 件 1・報告
    # r20260924-9f07f36b）。`git pull` は自分の fetch のあと **FETCH_HEAD を読み直して**
    # 取り込む相手を決める。確定を続けて打つと、1 本目の commit・push と前後して
    # 別のプロセスの fetch（`fetch --append`・枝を名指しした fetch）が FETCH_HEAD に
    # 行を足し、for-merge の行が 2 つになって「Cannot fast-forward to multiple
    # branches」で 2 本目以降の承認が止まった（本物の git で再現）。直前の
    # `fetch origin` が進めた upstream の ref（`@{u}`）を名指しすれば、FETCH_HEAD を
    # 誰が書いても読まない。ff できないとき（枝分かれ・作業ツリーの衝突）は従前
    # どおりここで断る。
    merge = _run_git(repo_dir, ["merge", "--ff-only", "@{u}"])
    stuck = stuck_short = None
    if merge.returncode != 0:
        stuck_short = "git merge --ff-only @{u} に失敗しました（upstream に早送りで追いつけません）"
        stuck = stuck_short + ": " + redact_mod.redact(merge.stderr)
    elif not _head_is_upstream(repo_dir):
        # merge --ff-only が exit 0 を返しただけでなく、HEAD が実際に upstream に追いついた
        # ことを直接見る（HEAD が upstream より先にいると、merge は何もせずに 0 を返す）。
        stuck = stuck_short = "取り込みの後、HEAD が upstream に追いついたことを確認できませんでした"
    if stuck:
        # 手元に未 push の commit があれば、道具の書き戻しだけかを見て載せ直す（docstring）。
        settled, note = _settle_ahead(repo_dir, push=push)
        if not settled:
            return False, (f"{stuck_short}。{note}" if note else stuck), None

    # 最終確認（「エラーが出なかった」ではなく「成功したと確認できた」で通す）。
    head = _run_git(repo_dir, ["rev-parse", "HEAD"])
    upstream = _run_git(repo_dir, ["rev-parse", "@{u}"])
    if (head.returncode != 0 or upstream.returncode != 0
            or head.stdout.strip() != upstream.stdout.strip()):
        return False, "取り込みの後、HEAD が upstream に追いついたことを確認できませんでした", None

    damaged = history_damage(repo_dir)
    if damaged:
        return False, damaged, None

    # **確かめた OID をそのまま返す**（外部レビュー第 5 巡 P1）。呼び出し側は
    # これを持ち回り、queue の照合先に使う。HEAD を引き直させない。
    return True, "", head.stdout.strip()


REPAIR_HINT = ("直し方: reflog を空にし（git reflog expire --expire=now --all）、"
               "git fetch --refetch origin で取り直し、git commit-graph write --reachable で作り直す。"
               "直らなければ thth report file で開発へ")


def history_damage(repo_dir: str) -> str:
    """HEAD から辿れる commit が欠けていないか（運用 #7497・2026-11-11）。欠けていれば断りの文、無ければ ""。

    部分 clone の repo で自動の gc が commit を消すと、はてなの記事の確かめ（`log -- 記事`）が
    落ちて止まる（11/7・11/11）。投げる前に履歴を 1 度辿り、落ちたら壊れの形と直し方を言って止める。
    commit-graph だけが古い（graph を使わなければ辿れる）ときは止めない（道具は graph を使わずに
    辿り直す・5.14.2）。stderr に直し方を 1 行出す。
    """
    count = ["rev-list", "--count", "HEAD"]
    r = _run_git(repo_dir, count)
    if r.returncode == 0:
        return ""
    plain = _run_git(repo_dir, ["-c", "core.commitGraph=false", *count])
    if plain.returncode == 0:
        print("注意: この repo の commit-graph が無い commit を指しています（道具は graph を使わずに進みます）。"
              "git commit-graph write --reachable で作り直せます: " + os.path.abspath(repo_dir), file=sys.stderr)
        return ""
    return ("repo の履歴に欠けた commit があります（git の object が消えています。自動の gc の壊れの形）。"
            "このままだと記事の確かめが落ちるので投稿しません。" + REPAIR_HINT + ": "
            + redact_mod.redact(plain.stderr.strip()))


def _head_is_upstream(repo_dir: str) -> bool:
    head = _run_git(repo_dir, ["rev-parse", "HEAD"])
    upstream = _run_git(repo_dir, ["rev-parse", "@{u}"])
    return (head.returncode == 0 and upstream.returncode == 0
            and head.stdout.strip() == upstream.stdout.strip())


# 道具が自分で積む書き戻しの commit の形（`commit_and_push()` の呼び出し側の message）。
# **題だけでは決めない**: 題の形に加えて、触ったファイルの形も合うものだけを道具の commit と
# みなす（`_own_writeback_files()`）。形が合わないもの・ここに無い形はすべて人の commit。
# 呼び出し側の message を変えたら、ここと `tests/test_sync_replays_own_writeback.py` の見本も直す
# （直さなければ人の commit として扱われ、今までどおり止まるだけ）。
# 原稿 1 本の front-matter だけを書き換える。(題, 書く top-level の鍵, 書く `posts:` の段の鍵,
# 書いたあとの値の確かめ)。鍵は呼び出し側が実際に書くものだけ——**ほかの欄が 1 つでも変わって
# いれば人の commit**（題を真似て `status` だけ手で変えた commit を載せ直さない・11/05 Fable）。
_POSTED = frozenset({"status", "post_id", "posted_at", "reply_to_resolved"})
_OWN_FRONT_MATTER = (
    (re.compile(r"thth: \S+ (?P<name>[^/]+) を投稿（post_id (?P<pid>[^・）]+)(?:・inflight を解いて記録)?）"),
     _POSTED, frozenset(), "posted"),                                    # core・inflight_resolve
    (re.compile(r"thth: \S+ (?P<name>[^/]+) をはてなブログへ（.+）"),
     _POSTED | {"url"}, frozenset(), "posted"),                          # hatena_publish
    (re.compile(r"THTH: (?P<rel>.+) の \d+ 段目を公開（(?P<pid>.+)）"),
     frozenset(), frozenset({"post_id", "posted_at", "reply_to", "run_id", "bundle_sha", "text_sha256"}),
     "segment"),                                                         # threadthrow（bundle.set_post_fields）
    (re.compile(r"取り下げ: (?P<name>[^/]+) post_id .+（.+）"),
     frozenset({"retracted_at", "retracted_by", "retract_reason", "retracted_via", "retracted_credential"}),
     frozenset(), "retracted"),                                          # retract_cli
)
# `incident.py` が積む障害記録の commit の題（載せ直さない・断りの文を言い分ける）。
INCIDENT_SUBJECT = "thth: record account incident state"
_OWN_NEW_FILE = (  # 新しい原稿を 1 本置く
    re.compile(r"burst の順番待ち: (?P<name>[^/]+)（\S+）"),          # burst_queue
)
_OWN_DATA = (  # 採取の台帳（`data/` の下）を N 本
    re.compile(r"(?:収集|返信の取り直し): (?P<count>\d+) ファイル（\S+）"),  # collect
    re.compile(r"反応の採取: (?P<count>\d+) 配信（\S+）"),                   # hatena_reactions
)
_OWN_MANAGED = (  # 道具だけが書く clone（`managed_repo`）の原稿と添付
    re.compile(r"(?:draft|media) by=.+ via=.+"),                       # server_writes・media_uploads
    re.compile(r"media gc by=.+"),                                     # media_uploads
)
# `incident.py` の「thth: record account incident state」は入れない。あちらは自分の commit の
# OID を控えて自分で押し直すので、載せ直して OID が変わると控えと合わなくなる。


def _split_blob(blob: bytes):
    """`(front-matter の行の list, 本文のバイト列)`。front-matter が無ければ None。"""
    lines = blob.split(b"\n")
    if not lines or lines[0].strip() != b"---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == b"---":
            return lines[1:i], b"\n".join(lines[i + 1:])
    return None


def _line_key(line: bytes) -> str:
    text = line.decode("utf-8", "replace").strip()
    if text.startswith("- "):
        text = text[2:]
    return text.split(":", 1)[0].strip() if ":" in text else ""


def _without_tool_keys(lines: list, top: frozenset, nested: frozenset) -> list:
    """道具が書く鍵の行を除いた front-matter（残りが前後で同じなら、ほかの欄に触れていない）。"""
    out = []
    for line in lines:
        indented = line[:1] in (b" ", b"\t")
        if _line_key(line) in (nested if indented else top):
            continue
        out.append(line)
    return out


def _fm_value(lines: list, key: str, *, nested: bool = False) -> list:
    values = []
    for line in lines:
        if (line[:1] in (b" ", b"\t")) == nested and _line_key(line) == key:
            values.append(line.decode("utf-8", "replace").split(":", 1)[1].strip().strip("'\""))
    return values


def _own_writeback_files(repo_dir: str, sha: str, parents: list):
    """`sha` が道具の書き戻しの commit なら、触ったパスの集合。人の commit（見分けられない
    ものを含む）なら None。"""
    if len(parents) != 1:
        return None  # merge（と根の commit）は道具が積まない
    body = _run_git(repo_dir, ["log", "-1", "--format=%B", sha])
    if body.returncode != 0:
        return None
    subject = body.stdout.strip()
    if not subject or "\n" in subject:
        return None
    listed = _run_git(repo_dir, ["diff-tree", "--no-commit-id", "-r", "-z", "--name-status", sha])
    if listed.returncode != 0:
        return None
    fields = listed.stdout.split("\0")
    changes = [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if fields[i]]
    if not changes:
        return None
    paths = {path for _status, path in changes}

    for pattern, top_keys, nested_keys, wrote in _OWN_FRONT_MATTER:
        hit = pattern.fullmatch(subject)
        if hit is None:
            continue
        if len(changes) != 1 or changes[0][0] != "M":
            return None
        path = changes[0][1]
        if hit.groupdict().get("rel") is not None:
            if hit["rel"].replace(os.sep, "/") != path:
                return None
        elif os.path.basename(path) != hit["name"]:
            return None
        before = _run_git_bytes(repo_dir, ["cat-file", "blob", f"{parents[0]}:{path}"])
        after = _run_git_bytes(repo_dir, ["cat-file", "blob", f"{sha}:{path}"])
        if before.returncode != 0 or after.returncode != 0:
            return None
        old, new = _split_blob(before.stdout), _split_blob(after.stdout)
        # 本文（front-matter の後ろ）に 1 バイトでも触れていれば道具の書き戻しではない。
        if old is None or new is None or old[1] != new[1]:
            return None
        # front-matter も、道具が書く鍵の行のほかは前後で同じでなければならない。
        if _without_tool_keys(old[0], top_keys, nested_keys) != _without_tool_keys(new[0], top_keys, nested_keys):
            return None
        # 書いたあとの値も道具の書き戻しの形（投稿なら posted で、post_id が題と同じ）。
        fm, pid = new[0], hit.groupdict().get("pid")
        if wrote == "posted":
            ok = (_fm_value(fm, "status") == ["posted"] and len(_fm_value(fm, "post_id")) == 1
                  and _fm_value(fm, "post_id")[0] and (pid is None or _fm_value(fm, "post_id") == [pid]))
        elif wrote == "segment":
            ok = pid in _fm_value(fm, "post_id", nested=True)
        else:
            ok = len(_fm_value(fm, "retracted_at")) == 1 and bool(_fm_value(fm, "retracted_at")[0])
        return paths if ok else None
    for pattern in _OWN_NEW_FILE:
        hit = pattern.fullmatch(subject)
        if hit is None:
            continue
        ok = len(changes) == 1 and changes[0][0] == "A" and os.path.basename(changes[0][1]) == hit["name"]
        return paths if ok else None
    for pattern in _OWN_DATA:
        hit = pattern.fullmatch(subject)
        if hit is None:
            continue
        ok = (int(hit["count"]) == len(changes)
              and all(status in ("A", "M") and path.startswith("data/") for status, path in changes))
        return paths if ok else None
    from . import managed_repo
    if managed_repo.account_for(repo_dir) is not None:
        if any(pattern.fullmatch(subject) for pattern in _OWN_MANAGED):
            return paths
    return None


def _changed_names(repo_dir: str, args: list):
    out = _run_git(repo_dir, ["diff", "--name-only", "-z", *args])
    if out.returncode != 0:
        return None
    return {name for name in out.stdout.split("\0") if name}


def _ahead_rows(repo_dir: str):
    listed = _run_git(repo_dir, ["rev-list", "--reverse", "--parents", "@{u}..HEAD"])
    if listed.returncode != 0:
        return None
    return [line.split() for line in listed.stdout.splitlines() if line.strip()]


def _classify(repo_dir: str, rows: list) -> tuple:
    """`(触ったパスの集合, None)` か、人の commit が混ざれば `(None, (短い OID, 題))`。"""
    touched = set()
    for sha, *parents in rows:
        files = _own_writeback_files(repo_dir, sha, parents)
        if files is None:
            subject = _run_git(repo_dir, ["log", "-1", "--format=%s", sha]).stdout.strip()
            return None, (sha[:7], subject)
        touched |= files
    return touched, None


def _settle_ahead(repo_dir: str, *, push: bool) -> tuple:
    """手元の未 push の commit を、道具の書き戻しだけなら upstream の上に載せ直して push する。

    戻り値 `(載せ直して push できたか, 断りの理由)`。理由は「どうすれば通るか」で終わる
    （`docs/原則_前向きな断り.md`）。未 push の commit が無い（遅れているだけで早送りに失敗
    した等）なら `(False, "")`——呼び出し側は従前の理由をそのまま出す。**断るときは手元を
    元のままにする**（ぶつかった rebase は abort・動いた HEAD は戻す・stage の区別は
    `_rebase_and_push()` が保つ）。
    """
    if _rebase_in_progress(repo_dir):
        return False, REBASE_IN_PROGRESS.format(repo=repo_dir)
    rows = _ahead_rows(repo_dir)
    if not rows:
        return False, ""
    touched, human = _classify(repo_dir, rows)
    if human is not None and human[1] == INCIDENT_SUBJECT:
        return False, (f"手元に THTH の障害記録の commit（{human[0]}）が upstream へ届かないまま残っています。"
                       "障害記録は自分の commit の OID を控えて押し直すので、同期では載せ直しません。"
                       f"git -C {repo_dir} show --stat {human[0]} で中身を確かめ、git -C {repo_dir} pull"
                       f" --rebase のあと git -C {repo_dir} push で手で押してから、もう一度打ってください")
    if human is not None:
        return False, (f"手元に upstream へ届いていない commit が {len(rows)} 本あり、THTH の書き戻し"
                       f"ではないもの（{human[0]} {redact_mod.redact(human[1])[:60]}）が混ざっているので、"
                       f"道具では並びを書き換えません。git -C {repo_dir} log --stat @{{u}}..HEAD で見て、"
                       "残すなら手で rebase して push、要らなければ外してから、もう一度打ってください")
    held = _run_git(repo_dir, ["rev-parse", "--verify", "-q", HELD_REF]).stdout.strip()
    if held and any(sha == held for sha, *_ in rows):
        return False, (f"手元の commit {held[:7]} は、取り込みのあとの照合（送った本文と一致するか）で"
                       f"push を止めたものなので載せ直しません。git -C {repo_dir} show {held[:7]} で"
                       "中身を確かめ、正しければ手で push してから、もう一度打ってください")
    base = _run_git(repo_dir, ["merge-base", "HEAD", "@{u}"]).stdout.strip()
    upstream_changed = _changed_names(repo_dir, [base, "@{u}"]) if base else None
    dirty = _changed_names(repo_dir, ["HEAD"])
    if upstream_changed is None or dirty is None:
        return False, (f"upstream で変わったファイルを数えられません。git -C {repo_dir} status で"
                       "枝と upstream を確かめてから、もう一度打ってください")
    overlap = sorted(touched & upstream_changed)
    if overlap:
        return False, (f"手元の書き戻しの commit と upstream が同じファイル（{'・'.join(overlap[:3])}）を"
                       f"変えているので、道具では載せ直しません。git -C {repo_dir} log -p @{{u}}..HEAD と"
                       f" git -C {repo_dir} log -p HEAD..@{{u}} を見比べ、残す方を決めて手で rebase して"
                       "から、もう一度打ってください")
    in_the_way = sorted(dirty & upstream_changed)
    if in_the_way:
        return False, (f"作業ツリーの変更（{'・'.join(in_the_way[:3])}）が upstream の変更と重なるので"
                       "載せ直しません。その変更を commit するか退けてから、もう一度打ってください")
    if not push:
        return False, (f"手元に THTH の書き戻しの commit が {len(rows)} 本、upstream へ届かないまま"
                       "残っています。この経路は下見なので push しません。"
                       "thth pull <口座> で upstream の上に載せ直して push できます")

    before = _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()

    def still_safe() -> bool:
        # 取り込みの再試行は fetch し直すので、載せた直後にもう一度見る（その間に upstream が
        # 同じファイルを変えた・人が commit を足した、を押さない）。
        again = _ahead_rows(repo_dir)
        if again is None or len(again) != len(rows):
            return False
        now_touched, now_human = _classify(repo_dir, again)
        old_base = _run_git(repo_dir, ["merge-base", before, "@{u}"]).stdout.strip()
        moved = _changed_names(repo_dir, [old_base, "@{u}"]) if old_base else None
        return now_human is None and now_touched == touched and moved is not None and not (touched & moved)

    try:
        ok, last_err = _rebase_and_push(repo_dir, validate=still_safe)
        reason = redact_mod.redact(last_err).strip()
    except PushValidationFailed:
        ok, reason = False, "載せ直している間に upstream か手元の並びが変わりました"
    if ok:
        return True, ""
    if _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip() != before:
        # 載せ直したが押せなかった。HEAD を元の commit に戻す（中身は同じ・作業ツリーの変更は残す）。
        # 戻せなければ照合で止めたのと同じ印を付け、次の同期が黙って押さないようにする。
        # `reset --keep` は index を戻すので、stage の区別は前後で控えて戻す（重なりは上で断り済み）。
        saved_tree, staged_paths = save_index(repo_dir)
        if _run_git(repo_dir, ["reset", "-q", "--keep", before]).returncode != 0:
            _hold_head(repo_dir)
        restore_index(repo_dir, saved_tree, staged_paths)
    return False, (f"手元の書き戻しの commit（{len(rows)} 本）を upstream の上に載せて push できません"
                   f"でした（手元は元のままです）: {reason[:300]}。origin に届くようになってから、"
                   "もう一度打ってください（thth pull <口座> で載せ直せます）")
