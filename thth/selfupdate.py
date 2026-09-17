"""app 自身を最新にしてから走る（設計 §3.2 の順序・2026-09-10 に未実装が発覚）。

設計は「`thth run` は flock の前に **app 自身を `git pull --ff-only`** し、進んで
いたら 1 回だけ exec しなおす」と書いてあったが、**どこにも実装されていなかった**。
2026-09-10 に VM を見たら `/srv/thth/app` は `9e4817b` のまま——**外部レビュー
4 巡分の修正が 1 つも入っていない状態で timer が 10 分ごとに回っていた**。
timer は動いているので「動いている」ように見え、誰も気づかない形だった。

**pull は lock を取る前に行う。** lock を握ったまま exec しなおすと、取り直しに
失敗するか二重に握ることになる。

**pull に失敗しても止めない。** GitHub に届かない日に投稿が全部止まるのは重すぎる。
ただし**黙って古いまま走らない**: `runs` に記録が残り、`thth board` の `app` に
`behind_release` として出る（「動いているのに古い」を見える形にする）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from . import jst

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# exec しなおしたことを子に伝える（無限ループを作らない）。
REEXEC_ENV = "THTH_SELF_UPDATED"

# **本番が追いかける枝**（masaru 裁定 2026-09-12）。
#
# **`push` が本番反映と同義だった。** `thth run` は仕事の前に自分自身を
# `git pull --ff-only` する。それまでは**checkout している枝の上流**（＝`main`）を
# 引いていたので、**`main` に push した時点で、次の timer（10 分ごと）で本番の
# 道具が入れ替わっていた。** 実測でタイマー発火の 4〜7 秒後に降りている。
#
# 2026-09-11 の 1 日で 20 回以上 push しており、**公開経路の P1 が入っていた版も
# 同じ経路で本番に降りていた。** 実害が出なかったのは連投が 1 本も承認されて
# いなかったからで、**仕組みが止めたわけではない。**
#
# **`main` への push は開発の保存・共有。配布は `release` を進める操作。**
# ここは**checkout している枝を見ない**——`main` がどれだけ進んでも、この枝が
# 動かなければ本番は変わらない。
RELEASE_REF = os.environ.get("THTH_RELEASE_REF") or "release"

# **配布参照の署名を確かめるか**（セキュリティ監査 2026-09-14・P2-5）。
#
# `thth run` は `git fetch` → `git merge --ff-only origin/<ref>` で**自分自身を
# 入れ替えてから走る**。確かめているのは「ff できるか」だけなので、**origin を
# 握った者は、次の timer（10 分）で VM の上に任意のコードを置ける。**
#
# **だが既定で入れることはできない。** いまの release の commit は署名されて
# いない（開発の commit は無署名）。無条件に検証すると**次の配布で VM が止まる**
# （設計 §8 の止まる条件）。よって**環境変数で明示的に入れたときだけ**検証し、
# 既定では「確かめていない」ことを board に 1 語出す。
#
# **署名を始めるときの手順**（3 行）:
#   1. 配布する人の手元で `git config --global commit.gpgsign true`（または
#      `gpg.format=ssh` ＋ `user.signingkey`）を入れ、`release` を署名付きで進める。
#   2. VM で公開鍵を信頼させる（gpg なら import、ssh 署名なら
#      `gpg.ssh.allowedSignersFile` に 1 行）。`git verify-commit origin/release`
#      が手で通ることを確かめる。
#   3. VM の unit に `Environment=THTH_REQUIRE_SIGNED_RELEASE=1` を足す。
#      以後、署名を確かめられない配布は**取り込まれず、古いまま走る**（止まらない）。
REQUIRE_SIGNED_ENV = "THTH_REQUIRE_SIGNED_RELEASE"

# **署名を確かめられなかったことを、取得の記録に残す言葉**（監査 2 回目・P2-4）。
# `board` はこの綴りで「確認できず」を出し分けるので、**1 か所に置く**（文言を
# 直したときに board だけ古い綴りを探す、を作らない）。
SIGNATURE_ERROR = "署名を確かめられません"


def require_signed_release() -> bool:
    return os.environ.get(REQUIRE_SIGNED_ENV) == "1"


def verify_release_signature(app_dir: str, oid: str) -> bool:
    """固定した commit `oid` の署名を確かめられるか（`git verify-commit`）。

    **確かめられないこと**と**署名が偽物であること**を区別しない——どちらも
    「取り込まない」で同じだから（作法 5・fail-closed）。

    **可変の `origin/<ref>` は受け取らない。** 署名を確かめたあと merge までの間に
    remote-tracking ref が動くと、確かめた commit と取り込む commit が分かれる。
    呼び手が fetch 直後の OID を 1 回だけ読み、検証と merge の両方へ渡す。
    """
    return _git(["verify-commit", oid], cwd=app_dir).returncode == 0

# **「渡していない」と「渡したが不明」を分ける**（外部レビュー・2026-09-12）。
#
# `base=None` を「省略」と読んでいたため、**記録が無い画面が `None` を渡すと、
# 数える側が記録を読み直しに行った。** その間に別の更新が終わっていると、**無い
# はずの基準で `0`（＝一致しています）を返す。**
#
# **今日ずっと潰してきた「読めない ≠ 無い」を、引数の設計で作っていた。**
_未指定 = object()


def _git(args: list, *, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


def _refspec(ref: str) -> str:
    """**取りに行く先を明示する。** clone の作られ方に依存させない。

    `git fetch origin release` は、**clone の refspec が拾っている場合にだけ**
    `refs/remotes/origin/release` を作る。`--single-branch` や `--depth` 付きで
    clone されていると refspec は `+refs/heads/main:refs/remotes/origin/main`
    だけになり、**同じ命令が rc=0 で成功したまま `origin/release` を作らない。**

    実際に確かめた（2026-09-12・単一枝に絞った clone）:

    - `git fetch origin release` → **rc=0**（失敗しない）
    - `refs/remotes/origin/release` → **作られない**
    - `git merge --ff-only origin/release` → `not something we can merge`
    - `git rev-list HEAD..origin/release` → rc=128 ＝ **遅れは「判らない」**

    つまり**配っても永久に届かず、board には「ff に失敗」としか出ない**
    ——原因（clone の作られ方）は誰にも辿れない。**止まりはしないが、
    理由が届かない。** refspec を書けば clone の設定を見に行かなくて済む。

    いまの VM は普通の clone（wildcard refspec・運用セッションが現物で確認・
    2026-09-12）なので**今日は刺さらない**。ここで塞ぐのは、**VM を作り直す
    人が `--depth 1` を打たない保証がないから**であって、いま壊れている
    からではない。
    """
    return f"+refs/heads/{ref}:refs/remotes/origin/{ref}"


def _remote_ref(ref: str) -> str:
    return f"refs/remotes/origin/{ref}"


def _check_record_path(app_dir: str) -> str | None:
    """**取りに行けたかどうか**を書き置く場所。その clone の `.git` の中。

    `state/` ではなく `.git` に置くのは、**この事実が「その clone のもの」だから**
    ——別の clone に持ち越されても意味が無い。追跡もされない。
    """
    r = _git(["rev-parse", "--absolute-git-dir"], cwd=app_dir)
    if r.returncode != 0:
        return None
    return os.path.join(r.stdout.strip(), "thth-release-check.json")


def _write_check(app_dir: str, ref: str, payload: dict) -> bool:
    """記録を書く。**書けたかどうかを返す**（握り潰さない）。"""
    path = _check_record_path(app_dir)
    if path is None:
        return False
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            # 書けないなら**せめて古いものを消す**——**古い成功を有効なまま
            # 残すのがいちばん悪い。**
            os.remove(path)
        except OSError:
            pass
        return False


def _begin_check(app_dir: str, ref: str) -> bool:
    """**取りに行く前に、前の成功を無効にする**（外部レビュー F3・P2・2026-09-12）。

    以前は結果を書くときだけ記録していた。**その書き込みが失敗すると
    `except OSError: pass` で握り潰され、前回の `ok: true` がそのまま有効に
    残った**——通信に失敗しているのに、別プロセスの board が古い成功から
    「追いついています」と出す形。

    **順番を変える。** 先に「まだ結果が無い」を書いてから取りに行く。**結果の
    書き込みが失敗しても、残るのは古い成功ではなくこれ。**

    **限界は残る**（そしてそれを隠すために記録の記録を増やさない・外部レビュー
    の指示）: **この書き込み自体が失敗した場合**、古い成功が残る。そのときは
    `False` を返すので、呼び出し側が**その実行の中では**知ることができる。
    **別プロセスの board には伝わらない。** ここは塞げていない。
    """
    return _write_check(app_dir, ref, {
        "ref": ref, "ok": False, "checked_at": jst.iso(), "pending": True,
        "error": "取りに行った結果がまだ書けていません"})


def _record_check(app_dir: str, ref: str, *, ok: bool,
                   error: str | None = None, release: str | None = None) -> bool:
    """**取りに行った結果を残す。** 書けたかどうかを返す。

    **失敗しても呼び出し側は止めない**（記録係が転んだせいで投稿が止まるのは
    重すぎる）。ただし**握り潰さない**——`_begin_check` が先に無効化している
    ので、ここが失敗しても**古い成功は残らない。**
    """
    payload = {"ref": ref, "ok": ok, "checked_at": jst.iso(), "error": error}
    if ok:
        # `_pull_locked()` は fetch 直後に固定した OID を渡す。ここで可変の ref を
        # 読み直すと、記録だけが検証・merge と別の commit を指しうる。
        payload["release"] = release or _cached_release(app_dir, ref)
    return _write_check(app_dir, ref, payload)


def _read_check(app_dir: str, ref: str) -> dict | None:
    """記録を読む**内部の口**。

    公開の `release_check()` とは別にしてある。**内部が公開名を呼ぶと、
    呼び出し側が公開名を差し替えたときに内部まで巻き込まれる**（テストで
    `functools.partial` で `app_dir` を束ねたら、内部の呼び出しが二重に
    束ねられて落ちた）。**公開の口は外から差し替えられる前提で扱う。**
    """
    path = _check_record_path(app_dir)
    if path is None or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict) or row.get("ref") != ref:
        # **別の枝についての記録を、この枝の確認として読まない。**
        return None
    return row


def release_check(app_dir: str = APP_DIR, *, ref: str | None = None) -> dict | None:
    """**最後に配布の枝を取りに行った結果。** 記録が無ければ `None`。

    外部レビュー F2 残件（P2・2026-09-12）。**`fetch=True` の経路だけ直しても
    閉じなかった**——board は `fetch=False` で呼ぶので、**取りに行けなくなった
    あとも古い追跡 ref から `0` を数えて「追いついています」と出ていた。**

    取得結果を**別プロセスからも読める形**にして、board がそれを見る。
    """
    return _read_check(app_dir, ref or RELEASE_REF)


def _confirmed(app_dir: str, ref: str) -> bool:
    """**いまの配布状況を確かめられているか。** 記録が無い・失敗しているなら偽。"""
    row = _read_check(app_dir, ref)
    return bool(row and row.get("ok"))


def _recorded_release(app_dir: str, ref: str) -> str | None:
    """**比較の基準にする SHA。** 最後に記録できた取得試行のときの配布参照。

    外部レビュー F5（P2・2026-09-12）。**表示していた SHA と、比較に使っていた
    SHA が別物だった。** 表示は記録された SHA（A）、比較は**いまの**
    `origin/release`（B）。記録の更新に失敗すると両者がずれ、**実際は B にいるのに
    「A と一致しています」と出た。**

    **基準を記録側に揃える。** board は取りに行かないので、**言えるのは記録に
    ついてだけ**——比較もそこに合わせる。結果として、記録できなかった配布は
    「**配っていない commit で動いています**」と出る。**記録が無い以上、配られた
    証拠が無いのは本当のこと。**

    （`_pull_locked` の中だけは別で、**その場で fetch した直後**なので手元の追跡
    ref が現在を指している。**そこは現在を見てよい唯一の場所。**）
    """
    row = _read_check(app_dir, ref)
    if not (row and row.get("ok")):
        return None
    base = row.get("release")
    return base if isinstance(base, str) and base else None


# **1 枚の画面は、1 組の SHA で作る**（外部レビュー F5 残件・P2・2026-09-12）。
#
# `board_summary()` が記録から A を取り出して表示用に持ったあと、`behind_release()`
# と `ahead_of_release()` が**それぞれ記録を読み直し、さらに可変の `HEAD` で数えて
# いた。** その間に自己更新が B へ進むと、**表示は A、計数は B** になる。
#
# `base`・`head_sha` を渡せるようにして、**呼び出し側が 1 度だけ読んだ 2 値を、
# 表示と両方向の計数の全部に使う。** 計数の途中で記録も `HEAD` も読み直さない。
#
# **追加の lock も、記録の状態機械も要らない。** 「読み直さない」だけで閉じる。


def _cached_release(app_dir: str, ref: str) -> str | None:
    """**手元が覚えている配布の枝の SHA。** 持っていなければ `None`。

    これは「**前回取りに行けたときの値**」であって、いまの origin の値ではない。
    取りに行けたかどうかと**必ず一緒に扱う**——単体で読むと、外部レビューが
    見つけた「古い値で『追いついています』と言う」形になる（F2・2026-09-12）。
    """
    r = _git(["rev-parse", "--verify", "-q", _remote_ref(ref)], cwd=app_dir)
    return r.stdout.strip() if r.returncode == 0 else None


def _count(app_dir: str, spec: str) -> int | None:
    r = _git(["rev-list", "--count", spec], cwd=app_dir)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _has_git(app_dir: str) -> bool:
    """`app_dir` が git の管理下か（T6-3）。

    `pip install` で入った環境には `.git` が無い——それでも `head()` は import
    のたびに `git -C <site-packages> rev-parse HEAD` を走らせ、毎回 rc=128 に
    なっていた（無害だが無駄・試験の記録に 1 コマンドごとに 1 行汚れる）。
    ここで先に確かめて、**無ければ git を 1 回も呼ばない**。

    **`os.path.isdir` ではなく `os.path.exists` で見る**——worktree では
    `.git` が（`gitdir: …` を指す）ファイルで、ディレクトリではない。
    """
    return os.path.exists(os.path.join(app_dir, ".git"))


def head(app_dir: str = APP_DIR) -> str | None:
    if not _has_git(app_dir):
        return None
    r = _git(["rev-parse", "HEAD"], cwd=app_dir)
    return r.stdout.strip() if r.returncode == 0 else None


def behind_release(app_dir: str = APP_DIR, *, fetch: bool = False,
                    ref: str | None = None, base=_未指定,
                    head_sha=_未指定) -> int | None:
    """**配布の枝**より何 commit 遅れているか。判らなければ `None`。

    **名前を変えた**（`behind_origin` → `behind_release`・2026-09-12）。見る先が
    `origin/main` から配布の枝に変わったので、**古い読み手が黙って通らないように
    する**（規約 5）。

    `fetch=False` のときは**取りに行かない**（board のように頻繁に呼ぶ場所で
    ネットワークに触れないため。直前に `thth run` が fetch している）。

    **`None` は「遅れていない」ではなく「判らない」。**——配布の枝が origin に
    無い場合もここに来る。**0 と混ぜない。**

    **`.git` が無ければ（pip 版）git を 1 回も呼ばず `None`**（T6-3）。
    """
    if not _has_git(app_dir):
        return None
    ref = ref or RELEASE_REF
    if fetch:
        # **取りに行けなかったなら、手元の値は古い。** 数えられるからといって
        # 数えない——外部レビュー F2-1（2026-09-12）: 取得の戻り値を無視して
        # 古い `origin/release` から数え、**到達不能なのに `0`（＝追いついて
        # います）を返していた。**
        _begin_check(app_dir, ref)
        ok = _git(["fetch", "origin", _refspec(ref)], cwd=app_dir).returncode == 0
        _record_check(app_dir, ref, ok=ok,
                       error=None if ok else "取りに行けませんでした")
        if not ok:
            return None
    # **確かめられていないなら数えない**（外部レビュー F2 残件・P2・2026-09-12）。
    # **`fetch=True` だけ直しても閉じなかった**——board は `fetch=False` で呼ぶので、
    # 取りに行けなくなったあとも古い追跡 ref から `0` を数え、**「追いついて
    # います」と出していた。** 取りに行けた事実そのものを見に行く。
    if base is _未指定:
        base = _recorded_release(app_dir, ref)
    here = "HEAD" if head_sha is _未指定 else head_sha
    # **渡された `None` は「不明」。** 読み直して埋めない。
    if base is None or here is None:
        return None
    return _count(app_dir, f"{here}..{base}")


def ahead_of_release(app_dir: str = APP_DIR, *, fetch: bool = False,
                      ref: str | None = None, base=_未指定,
                      head_sha=_未指定) -> int | None:
    """**配布の枝より何 commit 先にいるか。** 判らなければ `None`。

    **`0` でないなら「配っていない commit で動いている」。**

    外部レビュー F1（P1・2026-09-12）。`behind` が `0` でも**配ったもので
    動いているとは限らない**——`merge --ff-only origin/release` は相手が祖先
    なら**成功する（何もせずに）**ので、HEAD が release より先にいると
    「更新は正常に終わった」と見え、`HEAD..origin/release` も `0` になる。
    **board は「追いついています」と出していた。**

    起きうる経路が実際にある: VM のローカル枝の upstream が `origin/main` の
    ままなので、**保守で誰かが `git pull` を打てば、そこで配布の境界を迂回
    する。** しかも以後、迂回したことが**どこにも出ない。**

    **`.git` が無ければ（pip 版）git を 1 回も呼ばず `None`**（T6-3）。
    """
    if not _has_git(app_dir):
        return None
    ref = ref or RELEASE_REF
    if fetch:
        _begin_check(app_dir, ref)
        ok = _git(["fetch", "origin", _refspec(ref)], cwd=app_dir).returncode == 0
        _record_check(app_dir, ref, ok=ok,
                       error=None if ok else "取りに行けませんでした")
        if not ok:
            return None
    if base is _未指定:
        base = _recorded_release(app_dir, ref)
    here = "HEAD" if head_sha is _未指定 else head_sha
    if base is None or here is None:
        return None
    return _count(app_dir, f"{base}..{here}")


# **このモジュールを import した瞬間の版。** プロセスが実際に読み込んだコードの版で
# あり、以後どれだけディスクが動いても変わらない——それが基準として要る性質
# （外部レビュー第 7 巡 P2-2）。ここで 1 度だけ git を見る。
LOADED_REV = head(APP_DIR)


def pull_and_reexec(argv: list, *, app_dir: str = APP_DIR,
                     loaded_rev: str | None = None, ref: str | None = None,
                     log=print) -> str | None:
    """app を `git pull --ff-only` し、進んでいたら同じ引数で 1 回だけ exec しなおす。

    戻り値は「先へ進んでよい」ときの説明（`None` なら特に言うことなし）。
    exec した場合はこの関数から戻らない。**lock を取る前に呼ぶこと。**

    **同時に pull しない**（2026-09-10 に実際に衝突した）。timer は 3 本が 10 分の
    中でずれて走り、そこに手で叩いた pull が重なると、作業ツリーが checkout の
    途中で見える。git 自身が index を守るので壊れはしないが、片方が
    「古いまま走ります」になって**黙って古いコードで動く**。取れなければ更新を
    諦める（待たない）——誰かが今まさに更新しているので、この実行はそのまま
    進めばよい。**exec は lock を放してから**行う（exec は戻らないので、
    握ったまま渡すと子が持ち続ける）。
    """
    if os.environ.get(REEXEC_ENV):
        return None  # exec しなおした後の子。もう pull しない。

    from . import accounts as accounts_mod
    from . import lock as lock_mod
    lock = lock_mod.AccountLock(accounts_mod.app_lock_path())
    try:
        lock.acquire()
    except lock_mod.LockBusy:
        return "ほかの実行が app を更新中なので、この実行は更新を見送りました"
    # **基準は「このプロセスが読み込んだコードの版」**（外部レビュー第 6 巡 P2-4）。
    # 以前は lock を取ったあとのディスクの HEAD を基準にしていた。**別プロセスが
    # 先に更新を終えていると `before == after` になり、「進んでいない＝exec しなくて
    # よい」と誤判定して、古いコードを読み込んだまま走り続けた。** lock は git の
    # 更新を直列化するだけで、**すでに読み込んだコードと更新後のファイルが混ざる**
    # ことは防げない。
    anchor = loaded_rev if loaded_rev is not None else _loaded_rev(app_dir)

    try:
        message, moved = _pull_locked(app_dir, anchor=anchor, ref=ref)
    finally:
        lock.release()

    if not moved:
        return message

    # **動いたときも、言うことがあるなら落とさない。** ff で追いついた直後は
    # `message` は None になるはずだが、**「はずだ」で握り潰さない。**
    if message:
        log(message)
    log(f"app を更新しました（{moved[0][:7]} → {moved[1][:7]}）。実行しなおします。")
    env = dict(os.environ)
    env[REEXEC_ENV] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)
    return None  # ここには来ない


def _loaded_rev(app_dir: str) -> str | None:
    """基準にする版を返す。

    **`APP_DIR`（本番）については `LOADED_REV`——このモジュールを import した瞬間に
    記録した版を返す。**（外部レビュー第 7 巡 P2-2）

    第 6 巡でここを直したつもりだったが、**直っていなかった。** 「読み込んだ時点の
    版」と書きながら、実際には `pull_and_reexec()` の中——**lock を取ったあと**に
    git を見ていた。その時点で別プロセスが更新を終えていれば、記録される値は
    すでに新しい版で、`after` と一致して「exec 不要」になる。**穴はそのまま
    残っていた。**

    テストが通ったのは、テストが `loaded_rev` を引数で渡していたから。
    **本番経路は渡していない。** 引数で正しい値を注入できるテストは、
    引数を渡さない本番経路を検証していない（規約 11 の変種）。

    `app_dir` が本番と違う場合（テストの隔離 clone）は、その場で見る。
    """
    if os.path.realpath(app_dir) == os.path.realpath(APP_DIR):
        return LOADED_REV
    return head(app_dir)


def _pull_locked(app_dir: str, *, anchor: str | None = None,
                  ref: str | None = None) -> tuple:
    """lock の中で pull だけを行う。`(説明, (前, 後) または None)` を返す。

    `anchor` は**このプロセスが読み込んだ版**。pull の結果がこれと違えば、
    ディスクが動いていなくても exec しなおす必要がある。
    """
    before = head(app_dir)
    if before is None:
        return "app が git repo として読めません（自己更新をしていません）", None

    ref = ref or RELEASE_REF
    # **配布の枝だけを名指しで取りに行く。** `git pull` のように checkout して
    # いる枝の上流を見ない——**`main` に何が push されても、ここには入らない。**
    # **取りに行く前に、前の成功を無効にする**（外部レビュー F3・2026-09-12）。
    began = _begin_check(app_dir, ref)
    if not began:
        # **前の成功を無効にできなかった。** 別プロセスの board には伝わらない
        # ——**そこは塞げていない**ので、せめてこの実行では言う（外部レビュー
        # F3 の「記録の記録を増やさない」に従い、ここで止める）。
        log_prefix = (f"**配布の確認の記録を書けませんでした**"
                       f"（`{_check_record_path(app_dir) or '置き場が読めません'}`。"
                       f"**board には前回の確認が残ったままになります**）\n")
    else:
        log_prefix = ""

    fetch = _git(["fetch", "origin", _refspec(ref)], cwd=app_dir)
    # **取りに行った結果を、成否どちらでも残す**（外部レビュー F2 残件・
    # 2026-09-12）。board は別プロセスで `fetch=False` で呼ぶので、**ここで
    # 残さないと「確かめられているか」を board が知る術がない。**
    if fetch.returncode != 0:
        _record_check(app_dir, ref, ok=False, error="取りに行けませんでした")
        # **枝が無いのと、取りに行けなかったのを混ぜない。** 枝が無いなら
        # 「配ってもらえていない」であって、ネットワークの話ではない。
        #
        # **ここで 1 度混ぜた（2026-09-12）。** `ls-remote` の戻り値が 0 以外
        # なら「枝が無い」と読んでいたが、**届かないときも 0 以外になる。**
        # つまり GitHub に繋がらない日に「**配布されていません**」と言って
        # しまう——今夜ずっと潰してきた「読めない ≠ 無い」そのもの。
        #
        # `git ls-remote --exit-code` は**一致する ref が無いときだけ 2**、
        # 繋がらない等のエラーは別の値（128 など）。**2 のときだけ「無い」と
        # 言い切る。** それ以外は「判らない」側に倒す。
        exists = _git(["ls-remote", "--exit-code", "--heads", "origin", ref],
                       cwd=app_dir)
        if exists.returncode == 2:
            # **無いと判ったものを、手元に残さない**（外部レビュー F2-2・
            # 2026-09-12）。残しておくと `behind_release` がその古い値から
            # `0` を数え、**枝が消えているのに board が「追いついています」と
            # 出す。** 判ったことを、判った時点で反映する。
            _git(["update-ref", "-d", _remote_ref(ref)], cwd=app_dir)
            _record_check(app_dir, ref, ok=False,
                           error=f"`origin/{ref}` が origin にありません")
            return (log_prefix + f"**配布の枝 `origin/{ref}` が origin にありません**"
                     f"（古いまま走ります。**まだ配布されていないか、枝の名前が"
                     f"違います**）"), None
        return (log_prefix + f"配布の枝 `origin/{ref}` を取りに行けませんでした"
                 f"（古いまま走ります。**枝が無いのか、届かないのかは"
                 f"区別できていません**）"), None

    # **この取得で得た commit を 1 回だけ固定する。** 以後は署名検証・merge・
    # 記録・結果判定のすべてに同じ OID を使う。`origin/<ref>` は別の git process
    # でも動かせるため、検証後に読み直してはいけない（監査 D11・2026-09-17）。
    release_oid = _cached_release(app_dir, ref)
    if release_oid is None:
        _record_check(app_dir, ref, ok=False,
                      error="取得した配布参照の commit を読めませんでした")
        return (log_prefix + f"配布の枝 `origin/{ref}` の commit を読めませんでした"
                f"（取り込まずに古いまま走ります）"), None
    _record_check(app_dir, ref, ok=True, release=release_oid)

    # **署名を確かめてから取り込む**（セキュリティ監査 2026-09-14・P2-5）。
    # `THTH_REQUIRE_SIGNED_RELEASE=1` のときだけ（既定 off の理由は
    # `REQUIRE_SIGNED_ENV` の注記）。確かめられなければ**更新せず、古いまま走る**
    # ——止めない（取りに行けない日に投稿を全部止めるのが重すぎるのと同じ理由）。
    if require_signed_release() and not verify_release_signature(app_dir, release_oid):
        # **失敗を記録に残す**（監査 2 回目・P2-4）。前は `fetch` が成功した時点の
        # `ok=True` がそのまま残り、**署名を確かめられずに取り込まなかった回でも
        # board が「署名: 確認」と出していた**——`signature_checked` が見ていたのは
        # 「確かめる設定か」だけで、**確かめた結果ではなかった**。
        _record_check(app_dir, ref, ok=False, error=SIGNATURE_ERROR)
        return (log_prefix + f"**配布参照の署名を確かめられません**（`origin/{ref}`・"
                 f"{REQUIRE_SIGNED_ENV}=1）。**取り込まずに古いまま走ります**"), None

    merged = _git(["merge", "--ff-only", release_oid], cwd=app_dir)
    if merged.returncode != 0:
        n = behind_release(app_dir, ref=ref)
        if n == 0:
            # 遅れていないのに ff できない＝**枝分かれしている**（本番で誰かが
            # commit した・配布の枝が巻き戻された）。**黙って古いまま走らない。**
            return (log_prefix + f"**配布の枝 `origin/{ref}` と枝分かれしています**"
                     f"（古いまま走ります。本番側に commit が残っていないか"
                     f"確かめてください）"), None
        suffix = "" if n is None else f"（`origin/{ref}` より {n} commit 遅れ）"
        return (log_prefix + f"配布の枝への ff に失敗しました{suffix}"
                 f"（古いまま走ります）"), None

    after = head(app_dir)

    # **ff が成功しても、配ったもので動いているとは限らない**（外部レビュー
    # F1・P1・2026-09-12）。`merge --ff-only origin/release` は**相手が祖先なら
    # 何もせずに成功する**ので、HEAD が release より先にいると「正常に終わった」
    # と見える。`HEAD..origin/release` も `0` なので、**board は「追いついて
    # います」と出していた。**
    #
    # 実際に届く経路がある: VM のローカル枝の upstream が `origin/main` のままで、
    # **保守で誰かが `git pull` を打てば、そこで配布の境界を迂回する。**
    # しかも以後、迂回したことがどこにも出ない。**黙って正常扱いにしない。**
    #
    # **止めはしない**（取りに行けない日に投稿を全部止めるのが重すぎるのと同じ
    # 理由）。出すのは「いま何で動いているか」の事実。
    released = release_oid
    unreleased = None
    if released is not None and after != released:
        n = _count(app_dir, f"{release_oid}..HEAD")
        count = "" if n is None else f" {n} commit"
        unreleased = (log_prefix + f"**配っていない commit で動いています**"
                       f"（`origin/{ref}` より{count}先。配布の枝: "
                       f"{released[:7]} / いま: {(after or '不明')[:7]}）")

    # **ディスクが動いたかではなく、読み込んだ版と違うかで決める。**
    if unreleased is None and log_prefix:
        unreleased = log_prefix.rstrip("\n")
    if after == (anchor if anchor is not None else before):
        return unreleased, None
    return unreleased, (anchor if anchor is not None else before, after)
