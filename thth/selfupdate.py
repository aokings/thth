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
`behind_origin` として出る（「動いているのに古い」を見える形にする）。
"""
from __future__ import annotations

import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# exec しなおしたことを子に伝える（無限ループを作らない）。
REEXEC_ENV = "THTH_SELF_UPDATED"


def _git(args: list, *, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


def head(app_dir: str = APP_DIR) -> str | None:
    r = _git(["rev-parse", "HEAD"], cwd=app_dir)
    return r.stdout.strip() if r.returncode == 0 else None


def behind_origin(app_dir: str = APP_DIR, *, fetch: bool = False) -> int | None:
    """`origin/main` より何 commit 遅れているか。判らなければ None。

    `fetch=False` のときは**取りに行かない**（board のように頻繁に呼ぶ場所で
    ネットワークに触れないため。直前に `thth run` が fetch しているので、
    ローカルの `origin/main` はたいてい新しい）。
    """
    if fetch:
        _git(["fetch", "origin"], cwd=app_dir)
    r = _git(["rev-list", "--count", "HEAD..origin/main"], cwd=app_dir)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


# **このモジュールを import した瞬間の版。** プロセスが実際に読み込んだコードの版で
# あり、以後どれだけディスクが動いても変わらない——それが基準として要る性質
# （外部レビュー第 7 巡 P2-2）。ここで 1 度だけ git を見る。
LOADED_REV = head(APP_DIR)


def pull_and_reexec(argv: list, *, app_dir: str = APP_DIR,
                     loaded_rev: str | None = None, log=print) -> str | None:
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
    lock = lock_mod.AccountLock(
        os.path.join(accounts_mod.thth_root(), "state", "_app.lock"))
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
        message, moved = _pull_locked(app_dir, anchor=anchor)
    finally:
        lock.release()

    if not moved:
        return message

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


def _pull_locked(app_dir: str, *, anchor: str | None = None) -> tuple:
    """lock の中で pull だけを行う。`(説明, (前, 後) または None)` を返す。

    `anchor` は**このプロセスが読み込んだ版**。pull の結果がこれと違えば、
    ディスクが動いていなくても exec しなおす必要がある。
    """
    before = head(app_dir)
    if before is None:
        return "app が git repo として読めません（自己更新をしていません）", None

    fetch = _git(["fetch", "origin"], cwd=app_dir)
    if fetch.returncode != 0:
        return "app の fetch に失敗しました（古いまま走ります）", None

    pull = _git(["pull", "--ff-only"], cwd=app_dir)
    if pull.returncode != 0:
        n = behind_origin(app_dir)
        suffix = "" if n is None else f"（origin/main より {n} commit 遅れ）"
        return f"app の pull --ff-only に失敗しました{suffix}（古いまま走ります）", None

    after = head(app_dir)
    # **ディスクが動いたかではなく、読み込んだ版と違うかで決める。**
    if after == (anchor if anchor is not None else before):
        return None, None
    return None, (anchor if anchor is not None else before, after)
