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


def pull_and_reexec(argv: list, *, app_dir: str = APP_DIR, log=print) -> str | None:
    """app を `git pull --ff-only` し、進んでいたら同じ引数で 1 回だけ exec しなおす。

    戻り値は「先へ進んでよい」ときの説明（`None` なら特に言うことなし）。
    exec した場合はこの関数から戻らない。**lock を取る前に呼ぶこと。**
    """
    if os.environ.get(REEXEC_ENV):
        return None  # exec しなおした後の子。もう pull しない。

    before = head(app_dir)
    if before is None:
        return "app が git repo として読めません（自己更新をしていません）"

    fetch = _git(["fetch", "origin"], cwd=app_dir)
    if fetch.returncode != 0:
        return "app の fetch に失敗しました（古いまま走ります）"

    pull = _git(["pull", "--ff-only"], cwd=app_dir)
    if pull.returncode != 0:
        n = behind_origin(app_dir)
        suffix = "" if n is None else f"（origin/main より {n} commit 遅れ）"
        return f"app の pull --ff-only に失敗しました{suffix}（古いまま走ります）"

    after = head(app_dir)
    if after == before:
        return None

    log(f"app を更新しました（{(before or '')[:7]} → {(after or '')[:7]}）。実行しなおします。")
    env = dict(os.environ)
    env[REEXEC_ENV] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)
    return None  # ここには来ない
