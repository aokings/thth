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

import os
import subprocess
import sys

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


def head(app_dir: str = APP_DIR) -> str | None:
    r = _git(["rev-parse", "HEAD"], cwd=app_dir)
    return r.stdout.strip() if r.returncode == 0 else None


def behind_release(app_dir: str = APP_DIR, *, fetch: bool = False,
                    ref: str | None = None) -> int | None:
    """**配布の枝**より何 commit 遅れているか。判らなければ `None`。

    **名前を変えた**（`behind_origin` → `behind_release`・2026-09-12）。見る先が
    `origin/main` から配布の枝に変わったので、**古い読み手が黙って通らないように
    する**（規約 5）。

    `fetch=False` のときは**取りに行かない**（board のように頻繁に呼ぶ場所で
    ネットワークに触れないため。直前に `thth run` が fetch している）。

    **`None` は「遅れていない」ではなく「判らない」。**——配布の枝が origin に
    無い場合もここに来る。**0 と混ぜない。**
    """
    ref = ref or RELEASE_REF
    if fetch:
        _git(["fetch", "origin", _refspec(ref)], cwd=app_dir)
    r = _git(["rev-list", "--count", f"HEAD..origin/{ref}"], cwd=app_dir)
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
        message, moved = _pull_locked(app_dir, anchor=anchor, ref=ref)
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
    fetch = _git(["fetch", "origin", _refspec(ref)], cwd=app_dir)
    if fetch.returncode != 0:
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
            return (f"**配布の枝 `origin/{ref}` が origin にありません**"
                     f"（古いまま走ります。**まだ配布されていないか、枝の名前が"
                     f"違います**）"), None
        return (f"配布の枝 `origin/{ref}` を取りに行けませんでした"
                 f"（古いまま走ります。**枝が無いのか、届かないのかは"
                 f"区別できていません**）"), None

    merged = _git(["merge", "--ff-only", f"origin/{ref}"], cwd=app_dir)
    if merged.returncode != 0:
        n = behind_release(app_dir, ref=ref)
        if n == 0:
            # 遅れていないのに ff できない＝**枝分かれしている**（本番で誰かが
            # commit した・配布の枝が巻き戻された）。**黙って古いまま走らない。**
            return (f"**配布の枝 `origin/{ref}` と枝分かれしています**"
                     f"（古いまま走ります。本番側に commit が残っていないか"
                     f"確かめてください）"), None
        suffix = "" if n is None else f"（`origin/{ref}` より {n} commit 遅れ）"
        return (f"配布の枝への ff に失敗しました{suffix}"
                 f"（古いまま走ります）"), None

    after = head(app_dir)
    # **ディスクが動いたかではなく、読み込んだ版と違うかで決める。**
    if after == (anchor if anchor is not None else before):
        return None, None
    return None, (anchor if anchor is not None else before, after)
