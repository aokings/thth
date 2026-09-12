"""app 自身の自己更新（設計 §3.2・2026-09-10 に未実装が発覚）。

VM の `/srv/thth/app` は `9e4817b` のまま、外部レビュー 4 巡分の修正が 1 つも
入らずに timer だけが 10 分ごとに回っていた。timer が動いているので「動いている」
ように見え、誰にも見えない形だった。ここではその再発を防ぐ。
"""
from __future__ import annotations

import argparse
import os
import subprocess

import pytest

from tests.conftest import init_git_pair
from thth import cli as cli_mod
from thth import report as report_mod
from thth import selfupdate


def _app_pair(tmp_path):
    """app repo に見立てた bare origin ＋ clone を作る。

    **配布の枝（`release`）を `main` と同じところに作る**（masaru 裁定
    2026-09-12）。本番はこの枝だけを追いかけ、**`main` がどれだけ進んでも
    動かない。**
    """
    pair = init_git_pair(tmp_path, seed_content="v1\n", seed_name="version.txt")
    subprocess.run(["git", "-C", pair["seed"], "push", "-q", "origin", "HEAD:release"],
                    check=True)
    return pair


def _advance_main(pair, text="v2\n"):
    """**`main` だけを進める。** 配布はしない（開発の保存・共有）。"""
    path = os.path.join(pair["seed"], "docs", "sns", "queue", "version.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    subprocess.run(["git", "-C", pair["seed"], "commit", "-aqm", "advance"], check=True)
    subprocess.run(["git", "-C", pair["seed"], "push", "-q"], check=True)


def _release(pair):
    """**いまの `main` の先端を配布する。** ここだけが「配る」操作。"""
    subprocess.run(["git", "-C", pair["seed"], "push", "-q", "origin", "HEAD:release"],
                    check=True)


def _advance_origin(pair, text="v2\n"):
    """従来どおり「進めて配る」——既存のテストが期待している動き。"""
    _advance_main(pair, text)
    _release(pair)


def test_進んでいたらexecしなおす(tmp_path, monkeypatch):
    pair = _app_pair(tmp_path)
    before = selfupdate.head(pair["work"])
    _advance_origin(pair)

    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    lines = []
    selfupdate.pull_and_reexec(["thth", "run", "x"], app_dir=pair["work"], log=lines.append)

    assert len(execs) == 1, "更新したのに実行しなおしていない"
    assert execs[0][2].get(selfupdate.REEXEC_ENV) == "1", "再実行の目印が無い（無限ループになる）"
    assert selfupdate.head(pair["work"]) != before, "pull されていない"


def test_execしなおした子はpullしない(tmp_path, monkeypatch):
    """`THTH_SELF_UPDATED` が立っている＝もう更新済み。無限ループを作らない。"""
    pair = _app_pair(tmp_path)
    _advance_origin(pair)
    before = selfupdate.head(pair["work"])
    monkeypatch.setenv(selfupdate.REEXEC_ENV, "1")
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))

    assert selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"], log=lambda _l: None) is None
    assert execs == []
    assert selfupdate.head(pair["work"]) == before, "子が pull してしまった"


def test_進んでいなければ何もしない(tmp_path, monkeypatch):
    pair = _app_pair(tmp_path)
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    assert selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"], log=lambda _l: None) is None
    assert execs == []


def test_取りに行けなくても止まらないが黙らない(tmp_path, monkeypatch):
    """GitHub に届かない日に投稿が全部止まるのは重すぎる。ただし古いまま黙って走らない。"""
    pair = _app_pair(tmp_path)
    subprocess.run(["git", "-C", pair["work"], "remote", "set-url", "origin",
                    str(tmp_path / "届かない.git")], check=True)
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    message = selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"], log=lambda _l: None)
    # **「届かない」を「枝が無い」と言わない**（2026-09-12）。一度この 2 つを
    # 混ぜた——`ls-remote` が 0 以外なら「枝が無い」と読んでいたが、**届かない
    # ときも 0 以外になる。** GitHub に繋がらない日に「配布されていません」と
    # 言ってしまう形だった。**読めない ≠ 無い。**
    assert message and "取りに行けませんでした" in message
    assert "ありません" not in message.replace("取りに行けませんでした", ""), \
        "届かないだけなのに『枝が無い』と言い切っている"
    assert "区別できていません" in message
    assert execs == []


def test_配布の枝が無いときは無いと言い切る(tmp_path, monkeypatch):
    """**届くのに枝が無い**なら、それは判る——「まだ配布されていない」。"""
    pair = _app_pair(tmp_path)
    subprocess.run(["git", "-C", pair["seed"], "push", "-q", "origin",
                    "--delete", "release"], check=True)
    monkeypatch.setattr(os, "execve", lambda *a: None)
    message = selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"],
                                          log=lambda _l: None)
    assert message and "origin にありません" in message
    assert "区別できていません" not in message, "判ることを判らないと言っている"


def test_遅れをcommit数で数える(tmp_path):
    pair = _app_pair(tmp_path)
    # **取りに行く前は「判らない」**（`None`）。clone した時点では配布の枝を
    # まだ持っていない。**0（＝追いついている）と混ぜない。**
    assert selfupdate.behind_release(pair["work"]) is None
    assert selfupdate.behind_release(pair["work"], fetch=True) == 0
    _advance_origin(pair)
    _advance_origin(pair, "v3\n")
    assert selfupdate.behind_release(pair["work"], fetch=True) == 2, "遅れを数えられていない"


def test_git_repoでなければその旨を返す(tmp_path):
    plain = tmp_path / "ただのディレクトリ"
    plain.mkdir()
    message = selfupdate.pull_and_reexec(["thth"], app_dir=str(plain), log=lambda _l: None)
    assert message and "git repo" in message


def test_他のプロセスが先に更新しても読み込んだ版と違えばexecしなおす(tmp_path, monkeypatch):
    """外部レビュー第 6 巡 P2-4。

    lock を取ったあとのディスクの HEAD を基準にしていたので、**別プロセスが先に
    更新を終えていると `before == after` になり、古いコードを読み込んだまま
    走り続けた。** lock は git の更新を直列化するだけで、**読み込んだコードと
    更新後のファイルが混ざる**ことは防げない。
    """
    pair = _app_pair(tmp_path)
    loaded = selfupdate.head(pair["work"])      # このプロセスが読み込んだ版
    _advance_origin(pair)
    # 別プロセスが先に更新を終えた状態を作る（ディスクはもう新しい）
    subprocess.run(["git", "-C", pair["work"], "pull", "-q", "--ff-only"], check=True)
    assert selfupdate.head(pair["work"]) != loaded

    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"], loaded_rev=loaded,
                                log=lambda _l: None)

    assert len(execs) == 1, "ディスクが動いていないので exec しないと誤判定した"


def test_読み込んだ版のままなら何もしない(tmp_path, monkeypatch):
    pair = _app_pair(tmp_path)
    loaded = selfupdate.head(pair["work"])
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    assert selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"], loaded_rev=loaded,
                                       log=lambda _l: None) is None
    assert execs == []


def test_読み込んだ版はimportの瞬間に固定される(monkeypatch):
    """外部レビュー第 7 巡 P2-2。**第 6 巡の修正は本番経路では効いていなかった。**

    「読み込んだ時点の版」と書きながら、実際には `pull_and_reexec()` の中——
    **lock を取ったあと**に git を見ていた。その時点で別プロセスが更新を終えて
    いれば、記録される値はすでに新しい版で `after` と一致し、「exec 不要」になる。

    テストが通ったのは、テストが `loaded_rev` を引数で渡していたから。
    **引数で正しい値を注入できるテストは、引数を渡さない本番経路を検証していない。**
    """
    assert selfupdate.LOADED_REV is not None, "import 時に版を記録していない"
    assert selfupdate._loaded_rev(selfupdate.APP_DIR) == selfupdate.LOADED_REV

    # ディスクが動いても、基準は動かない（ここが第 6 巡で効いていなかった性質）
    monkeypatch.setattr(selfupdate, "head", lambda *_a, **_k: "ちがう版")
    assert selfupdate._loaded_rev(selfupdate.APP_DIR) == selfupdate.LOADED_REV


# --- 配布の枝（masaru 裁定 2026-09-12）--------------------------------------
#
# **`push` が本番反映と同義だった。** `thth run` は仕事の前に自分を
# `git pull --ff-only` する。それまでは checkout している枝の上流（＝`main`）を
# 引いていたので、**`main` に push した時点で、次の timer（10 分ごと）で本番の
# 道具が入れ替わっていた。** 実測でタイマー発火の 4〜7 秒後。
#
# 2026-09-11 の 1 日で 20 回以上 push しており、**公開経路の P1 が入っていた版も
# 同じ経路で降りていた。** 実害が出なかったのは連投が 1 本も承認されていなかった
# からで、**仕組みが止めたわけではない。**
#
# 受け入れ条件は 3 つ（masaru）:
#   1. **`main` への push では本番が変わらない**
#   2. **`release` へ進めた指定 commit が届く**
#   3. **届かない場合に分かる**

def test_mainへのpushでは本番が変わらない(tmp_path, monkeypatch):
    """**受け入れ条件 1。** これが今回の作業の全部。"""
    pair = _app_pair(tmp_path)
    before = selfupdate.head(pair["work"])

    _advance_main(pair)                      # 開発の保存・共有だけ
    _advance_main(pair, "v3\n")

    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    selfupdate.pull_and_reexec(["thth", "run"], app_dir=pair["work"],
                                log=lambda _l: None)

    assert selfupdate.head(pair["work"]) == before, \
        "**`main` に push しただけで本番が入れ替わった**"
    assert execs == [], "実行しなおしている（＝新しい版で動き出している）"
    # **遅れてもいない。** 配布の枝は動いていないので、追いついている。
    assert selfupdate.behind_release(pair["work"], fetch=True) == 0


def test_releaseへ進めた指定commitが届く(tmp_path, monkeypatch):
    """**受け入れ条件 2。** 配布は「いまの `main` を無条件に取り込む」ではなく、
    **指定した commit を `release` へ進める**操作。"""
    pair = _app_pair(tmp_path)
    _advance_main(pair, "v2\n")
    wanted = subprocess.run(["git", "-C", pair["seed"], "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
    _advance_main(pair, "v3\n")               # **配りたくない先の分**

    # 指定した commit だけを配る（`main` の先端ではない）。
    subprocess.run(["git", "-C", pair["seed"], "push", "-q", "origin",
                    f"{wanted}:release"], check=True)

    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    selfupdate.pull_and_reexec(["thth", "run"], app_dir=pair["work"],
                                log=lambda _l: None)

    assert selfupdate.head(pair["work"]) == wanted, \
        "配った commit ではないものが本番に入っている"
    assert len(execs) == 1, "更新したのに実行しなおしていない"


def test_届いていなければ遅れとして数えられる(tmp_path):
    """**受け入れ条件 3 の前半。** 届いていないことが**数として出る**。

    **ここは数えるところまでしか見ていない。** 人向けの画面に出るかは
    `test_thth_boardの人向け出力に道具の版と遅れが出る` が見る——この
    docstring は一度「人向けの画面に出る」と書いておきながら、**数しか
    見ていなかった。** 書いたことと確かめたことを一致させる。
    """
    pair = _app_pair(tmp_path)
    _advance_main(pair, "v2\n")
    _release(pair)                            # 配った

    # まだ取りに行っていない本番から見た遅れ。
    assert selfupdate.behind_release(pair["work"], fetch=True) == 1, \
        "配ったのに遅れとして数えられていない"


def test_単一枝のcloneでも配布が届く(tmp_path, monkeypatch):
    """**VM の clone がどう作られたかを、こちらからは見られない。**

    `--single-branch` / `--depth` 付きの clone は refspec が
    `+refs/heads/main:refs/remotes/origin/main` だけになる。そこで
    `git fetch origin release` を打つと、**rc=0 で成功したまま
    `refs/remotes/origin/release` を作らない**（2026-09-12 に実際に確かめた）。
    以後 ff は `not something we can merge` で落ち、`rev-list` は 128 で落ちる
    ので**遅れも「判らない」**。**配っても永久に届かず、board には「ff に
    失敗」としか出ない。**

    いまの VM は普通の clone（運用セッションが現物で確認）なので**今日は
    刺さらない**。**VM を作り直す人が `--depth 1` を打たない保証がない**ので
    塞ぐ。
    """
    pair = _app_pair(tmp_path)
    # VM が単一枝 clone だった場合と同じ設定にする。
    subprocess.run(["git", "-C", pair["work"], "config", "remote.origin.fetch",
                    "+refs/heads/main:refs/remotes/origin/main"], check=True)

    _advance_main(pair, "v2\n")
    _release(pair)
    wanted = subprocess.run(["git", "-C", pair["seed"], "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()

    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    selfupdate.pull_and_reexec(["thth", "run"], app_dir=pair["work"],
                                log=lambda _l: None)

    assert selfupdate.head(pair["work"]) == wanted, \
        "**単一枝 clone に配布が届いていない**（refspec を明示していない）"
    assert len(execs) == 1
    assert selfupdate.behind_release(pair["work"], fetch=True) == 0


# --- 受け入れ条件 3 の後半: **人向けの画面に出る** ---------------------------
#
# `app.head` と遅れは **`--json` にしか出ていなかった**——2026-09-10 に
# 「4 巡分古いまま timer が回っていた」のを見つけた当の欄が、**人が
# `thth board` を見ても分からなかった。** 出るかどうかを、出す側で確かめる。
#
# `board_summary()` を丸ごと差し替えず、**`behind_release` だけを差し替える**
# ——そうしないと `report` が書く鍵と `cli` が読む鍵の食い違いを、テストが
# 自分で埋めてしまう（**テストが通る理由が変わる**）。

_成功した記録 = {"ref": "release", "ok": True, "checked_at": "2026-09-09T10:00:00+09:00",
                  "error": None, "release": "abcdef1234567890"}


def _board_lines(monkeypatch, behind, capsys, *, check=_成功した記録):
    """**記録も一緒に差し替える。** 記録が無ければ board は数を読まない
    （外部レビュー F3 残件で出力契約が変わった・2026-09-12）。"""
    monkeypatch.setattr(report_mod.selfupdate_mod, "behind_release",
                         lambda *_a, **_k: behind)
    monkeypatch.setattr(report_mod.selfupdate_mod, "release_check",
                         lambda *_a, **_k: check)
    assert cli_mod.cmd_board(argparse.Namespace(json=False)) == 0
    return capsys.readouterr().out


def test_thth_boardの人向け出力に道具の版と遅れが出る(isolated_account, monkeypatch,
                                                       capsys):
    out = _board_lines(monkeypatch, 3, capsys)
    assert "道具:" in out, "道具の版が人向けに出ていない"
    assert "3 commit 遅れ" in out
    assert "届いていません" in out, "遅れているのに、届いていないと言っていない"
    # **「いま」を言わない**（外部レビュー F3 残件・2026-09-12）。board は
    # 取りに行かないので、言えるのは「そのとき記録した参照との比較」まで。
    assert "この画面では確認していません" in out


def test_thth_boardは遅れが判らないときを0と混ぜない(isolated_account, monkeypatch,
                                                       capsys):
    """**`None` は「遅れていない」ではない。** 配布の枝が無い場合もここに来る。"""
    失敗した記録 = {"ref": "release", "ok": False,
                     "checked_at": "2026-09-09T10:00:00+09:00",
                     "error": "取りに行けませんでした"}
    unknown = _board_lines(monkeypatch, None, capsys, check=失敗した記録)
    caught_up = _board_lines(monkeypatch, 0, capsys)
    assert "いまの配布状況は未確認です" in unknown
    assert "一致しています" not in unknown, \
        "**判らないのに『一致している』と言っている**"
    assert "一致しています" in caught_up
    assert "未確認です" not in caught_up
    assert unknown != caught_up


def test_thth_boardの人向け出力にも配布の枝の名前が出る(isolated_account, monkeypatch,
                                                         capsys):
    """どの枝を追いかけているのかが判らないと、**配る先を間違えても気づけない。**"""
    monkeypatch.setattr(selfupdate, "RELEASE_REF", "べつの枝")
    out = _board_lines(monkeypatch, 1, capsys)
    assert "べつの枝" in out, "追いかけている枝の名前が人向けに出ていない"

# --- 外部レビュー F1・F2（2026-09-12・要修正の判定）--------------------------
#
# **どちらも「キャッシュから答えられるなら答えてしまう」形だった。**
# 反例は `~/thth-exchange/out/2026-09-12_検収_配布の枝/controller/test_boundaries.py`
# に byte のまま置いてある（SHA-256 c1a33c0e…）。ここはそこから起こした版。

def test_配っていないcommitで動いていたら黙って正常扱いにしない(tmp_path):
    """**F1（P1）。`behind == 0` は「配ったもので動いている」ではない。**

    `merge --ff-only origin/release` は**相手が祖先なら何もせずに成功する**ので、
    HEAD が配布の枝より先にいると「正常に終わった」と見える。`HEAD..origin/release`
    も `0` なので、**board は「追いついています」と出していた。**

    届く経路が実際にある: VM のローカル枝の upstream が `origin/main` のままなので、
    **保守で誰かが `git pull` を打てば、そこで配布の境界を迂回する。**
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])                 # まず配布の枝に揃える
    released = subprocess.run(["git", "-C", pair["work"], "rev-parse", "origin/release"],
                               capture_output=True, text=True, check=True).stdout.strip()

    _advance_main(pair, "配っていない\n")                # **配らずに** main だけ進める
    subprocess.run(["git", "-C", pair["work"], "pull", "--ff-only", "-q"], check=True)

    message, moved = selfupdate._pull_locked(pair["work"])
    after = selfupdate.head(pair["work"])

    assert after != released, "前提が崩れている（先に進んでいない）"
    assert message, "**配っていない commit で動いているのに、黙って正常扱いにした**"
    assert "配っていない" in message
    # **`behind` は `0` のまま。** そこを直すのではなく、別の数で言う。
    assert selfupdate.behind_release(pair["work"]) == 0
    assert selfupdate.ahead_of_release(pair["work"]) == 1


def test_取りに行けなかったら遅れの数を古いまま返さない(tmp_path):
    """**F2-1。** 一度取れたあと origin へ届かなくなると、**古い
    `origin/release` から数えて `0`（＝追いついています）を返していた。**"""
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    _advance_origin(pair, "v2\n")                        # 配布の枝は進んでいる
    subprocess.run(["git", "-C", pair["work"], "remote", "set-url", "origin",
                    str(tmp_path / "とどかない.git")], check=True)

    assert selfupdate.behind_release(pair["work"], fetch=True) is None, \
        "**取りに行けなかったのに、古い値から数えて言い切っている**"


def test_枝が消えたら遅れの数を持ち越さない(tmp_path):
    """**F2-2。** origin から枝が消えても手元の追跡 ref は残るので、そこから
    `0` を数えて **board が「追いついています」と出していた。**"""
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    subprocess.run(["git", "-C", pair["seed"], "push", "-q", "origin",
                    "--delete", "release"], check=True)

    message, _moved = selfupdate._pull_locked(pair["work"])
    assert message and "origin にありません" in message
    assert selfupdate.behind_release(pair["work"]) is None, \
        "**消えた枝の古い値を持ち越して数えている**"


def test_thth_boardは記録より先にいることを出す(isolated_account,
                                                 monkeypatch, capsys):
    """**いちばん重い状態なので、遅れより先に出す。**

    ただし **「配っていない」とまでは言わない**（外部レビュー・2026-09-12）。
    **記録より先にいることは、未配布であることの証明にならない**——記録の
    書き込みに失敗しただけかもしれない。**比較の事実だけを書く。**
    """
    monkeypatch.setattr(report_mod.selfupdate_mod, "ahead_of_release",
                         lambda *_a, **_k: 2)
    out = _board_lines(monkeypatch, 0, capsys)
    assert "記録された配布参照より 2 commit 先です" in out
    assert "一致しています" not in out, "**先にいるのに『一致している』と出している**"
    assert "配っていない commit" not in out, \
        "**記録より先にいるだけで『配っていない』と断定している**"

# --- 外部レビュー F2 残件（P2・2026-09-12・再判定）---------------------------
#
# **`fetch=True` の経路だけ直しても閉じなかった。** board は `fetch=False` で
# 呼ぶので、取りに行けなくなったあとも**古い追跡 ref から `0` を数えて
# 「追いついています」と出ていた。** こちらの直しは半分だった。
#
# 反例は `~/thth-exchange/out/2026-09-12_再判定_配布の枝/controller/test_board_path.py`。
# **あの反例は `release_check` を tmp clone に束ねていない**ので、「最後に取りに
# 行けたのはいつか」を出す枝までは通らない。ここはそこまで通す。

def _real_board(pair, monkeypatch, capsys):
    """**board の既定経路をまるごと通す。** 値を差し替えない。"""
    import argparse
    import functools
    monkeypatch.setattr(report_mod.accounts_mod, "list_account_names", lambda: [])
    for name in ("head", "behind_release", "ahead_of_release", "release_check"):
        monkeypatch.setattr(selfupdate, name,
                             functools.partial(getattr(selfupdate, name), pair["work"]))
    summary = report_mod.board_summary()
    assert cli_mod.cmd_board(argparse.Namespace(json=False)) == 0
    return summary, capsys.readouterr().out


def test_取りに行けなくなったらboardは追いついているとは言わない(tmp_path, monkeypatch,
                                                                  capsys):
    """**残件の本体。** board は `fetch=False` で呼ぶ。"""
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])                  # 一度は取れている
    _advance_origin(pair, "v2\n")                          # 配布の枝は進んだ
    subprocess.run(["git", "-C", pair["work"], "remote", "set-url", "origin",
                    str(tmp_path / "とどかない.git")], check=True)
    selfupdate._pull_locked(pair["work"])                  # ここで取りに行けない

    summary, out = _real_board(pair, monkeypatch, capsys)

    assert "追いついています" not in out, \
        "**取りに行けていないのに『追いついています』と出している**"
    assert "いまの配布状況は未確認です" in out
    # **`checked_at` は成否を問わない「試みた時刻」**（外部レビュー F4・P3・
    # 2026-09-12）。**失敗した時刻を「取りに行けた」＝成功した時刻として説明
    # していた。** ここは一度その文言をテストで固定してしまっていた
    # ——**間違いを固定するテストは、間違いを守る。**
    assert "最後に記録された取得試行" in out, "いつ試みたのかが出ていない"
    assert "取りに行けたのは" not in out, \
        "**失敗した時刻を、成功した時刻として説明している**"
    assert "（**失敗**" in out, "成功と失敗を書き分けていない"
    # **機械の利用者にも伝わるか。** 鍵の名前で「手元の追跡 ref との比較」だと
    # 分かること、**remote の現在は未確認だと明示されていること。**
    app = summary["app"]
    assert "behind_release" not in app, \
        "**古い鍵が残っている**（remote に対する現在の遅れだと読まれる・規約 5）"
    assert app["behind_cached_release"] is None
    assert app["ahead_cached_release"] is None
    assert app["comparison_basis"] == "recorded_release"
    assert app["remote_current_verified"] is False
    assert app["release_check"]["ok"] is False
    assert app["release_check"]["checked_at"]


def test_取り直せたらboardは現在の状態に戻る(tmp_path, monkeypatch, capsys):
    """**閉じる条件の後半**（外部レビュー: 正常な取得後は適切な表示になること）。

    **未確認に倒しっぱなしにしない。** 倒しっぱなしなら「常に未確認」と書くのと
    同じで、何も言っていないことになる。
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    _advance_main(pair, "v2\n")
    _release(pair)
    good = str(pair["bare"])
    subprocess.run(["git", "-C", pair["work"], "remote", "set-url", "origin",
                    str(tmp_path / "とどかない.git")], check=True)
    selfupdate._pull_locked(pair["work"])                  # 一度壊す
    assert selfupdate.behind_release(pair["work"]) is None, "前提が崩れている"

    subprocess.run(["git", "-C", pair["work"], "remote", "set-url", "origin", good],
                    check=True)
    selfupdate._pull_locked(pair["work"])                  # 取り直せた

    # **束ねるのは、git を触り終えてから。** `_real_board` は `head` などを
    # `app_dir` 付きで束ねるので、そのあとに `_pull_locked` を呼ぶと二重に
    # 束ねられる（**テストの組み方の問題。製品コードの話ではない**）。
    summary, out = _real_board(pair, monkeypatch, capsys)
    assert "いまの配布状況は未確認です" not in out, \
        "取り直せたのに未確認のまま倒れている"
    assert "（成功・そのとき記録した配布参照" in out
    assert "その参照と一致しています" in out
    # **表示している SHA と、比較に使った SHA が同じであること**（外部レビュー
    # F5・2026-09-12）。**別々に取り出していたので、記録の更新に失敗すると
    # ずれ、実際は別の commit にいるのに「一致しています」と出た。**
    basis = summary["app"]["comparison_ref_sha"]
    assert basis and basis[:7] in out, "比較に使った SHA が画面に出ていない"
    # `head` は `_real_board` が束ねているので、summary 側から読む
    # （**束ねたあとに素で呼ぶと二重に束ねられる**・同じ型で 2 度踏んだ）。
    assert basis == summary["app"]["head"], \
        "一致していると言っているのに、比較の基準と HEAD が違う"
    # **取り直せても「いま」は言わない。** board は取りに行かないので、
    # **言えるのは「そのとき記録した参照との比較」まで**（外部レビュー F3 残件）。
    assert "この画面では確認していません" in out
    assert summary["app"]["behind_cached_release"] == 0
    assert summary["app"]["release_check"]["ok"] is True
    assert summary["app"]["remote_current_verified"] is False


def test_別の枝についての確認をこの枝の確認として読まない(tmp_path):
    """記録には枝の名前が入っている。**違う枝の確認を流用しない。**"""
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    assert selfupdate.release_check(pair["work"]) is not None
    assert selfupdate.release_check(pair["work"], ref="べつの枝") is None
    assert selfupdate.behind_release(pair["work"], ref="べつの枝") is None

# --- 外部レビュー F3（P2・2026-09-12・再々判定）------------------------------

def test_結果を保存できなくても古い成功を残さない(tmp_path, monkeypatch):
    """**F3。** 結果の書き込みが失敗したとき、`except OSError: pass` で握り潰され、
    **前回の `ok: true` がそのまま有効に残っていた**——通信に失敗しているのに、
    別プロセスの board が古い成功から「追いついています」と出す形。

    **順番を変えた。** 先に「まだ結果が無い」を書いてから取りに行く。**結果の
    書き込みが失敗しても、残るのは古い成功ではなくこれ。**
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    assert selfupdate.release_check(pair["work"])["ok"] is True, "前提が崩れている"

    _advance_origin(pair, "v2\n")
    subprocess.run(["git", "-C", pair["work"], "remote", "set-url", "origin",
                    str(tmp_path / "とどかない.git")], check=True)

    def 書けない(*_a):
        raise PermissionError("書き込みを失敗させる")

    with monkeypatch.context() as m:
        m.setattr(selfupdate.os, "replace", 書けない)
        selfupdate._pull_locked(pair["work"])

    assert selfupdate.behind_release(pair["work"]) is None, \
        "**保存に失敗したのに、古い成功が有効なまま残っている**"
    assert selfupdate.ahead_of_release(pair["work"]) is None


def test_無効化もできなかったらその実行では言う(tmp_path, monkeypatch):
    """**塞げていない範囲を、塞げている顔で通さない。**

    無効化の書き込み自体が失敗すると、**古い成功が残る。** 別プロセスの board には
    伝わらない（外部レビューの「記録の記録を増やさない」に従い、そこは塞がない）。
    **せめてその実行では言う。**
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])

    with monkeypatch.context() as m:
        m.setattr(selfupdate, "_write_check", lambda *_a, **_k: False)
        message, _moved = selfupdate._pull_locked(pair["work"])

    assert message and "配布の確認の記録を書けませんでした" in message
    assert "board には前回の確認が残ったままになります" in message, \
        "**何が起きるのかを言っていない**（書けなかった、だけでは伝わらない）"

def test_結果を書く前に死んでも古い成功を残さない(tmp_path, monkeypatch):
    """**先に無効化することが効くのは、ここ。**

    **変異で分かった**（2026-09-12）: 外部レビューの反例は「書けないなら古いものを
    消す」だけで満たされ、**先に無効化する処理を外しても通ってしまう。**
    つまりあの処理は**何にも守られていなかった。**

    効くのは**取得のあと、結果を書く前に実行が死んだとき**——VM の再起動、timer の
    kill、`timeout`。そのとき前回の `ok: true` が残っていると、**別プロセスの board
    が古い成功から「追いついています」と出す。**
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    assert selfupdate.behind_release(pair["work"]) == 0, "前提が崩れている"

    with monkeypatch.context() as m:
        # **結果を書かないまま終わる**（＝書く前に死んだ）。
        m.setattr(selfupdate, "_record_check", lambda *_a, **_k: True)
        selfupdate._pull_locked(pair["work"])

    assert selfupdate.behind_release(pair["work"]) is None, \
        "**結果を書く前に死んだのに、前回の成功が有効なまま残っている**"

# --- 外部レビュー F5（P2・2026-09-12・5 回目）--------------------------------

def test_表示するSHAと比較に使うSHAを同じにする(tmp_path, monkeypatch, capsys):
    """**F5。** 表示は記録された SHA（A）、比較は**いまの** `origin/release`（B）
    だった。記録の更新に失敗すると両者がずれ、**実際は B にいるのに「A と一致して
    います」と出た。**

    **変異で分かった**（2026-09-12）: F5 を戻す変異をかけても、**こちらのテストは
    27 本とも通った。** 落ちたのは外部レビューの反例だけで、**この性質を守る
    テストを 1 本も持っていなかった。**

    作る状況: **通信は成功させ、記録の書き込みだけ失敗させる。** すると
    `origin/release` は進むのに、記録は前のまま残る。
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    記録された = selfupdate.release_check(pair["work"])["release"]

    _advance_main(pair, "v2\n")
    _release(pair)                                   # 配布の枝は進む

    def 書けない(*_a):
        raise PermissionError("記録の書き込みだけ失敗させる")

    with monkeypatch.context() as m:
        m.setattr(selfupdate.os, "replace", 書けない)
        m.setattr(selfupdate.os, "remove", 書けない)
        selfupdate._pull_locked(pair["work"])        # fetch と merge は成功する

    head = selfupdate.head(pair["work"])
    assert head != 記録された, "前提が崩れている（HEAD が動いていない）"

    summary, out = _real_board(pair, monkeypatch, capsys)
    基準 = summary["app"]["comparison_ref_sha"]

    assert 基準 == 記録された, "比較の基準が記録と違う"
    assert 基準[:7] in out, "比較に使った SHA が画面に出ていない"
    assert not (基準 != summary["app"]["head"] and "その参照と一致しています" in out), \
        "**表示している SHA と違うところにいるのに『一致しています』と出している**"
    # 記録に無い commit で動いている、と言えていること。
    assert summary["app"]["ahead_cached_release"] == 1
    assert "記録された配布参照より 1 commit 先です" in out


def test_記録が読めないときに一度も取りに行っていないと言わない(isolated_account,
                                                                monkeypatch, capsys):
    """**読めない ≠ 無い。** 記録の消失・読取失敗でも同じ分岐に来る。"""
    monkeypatch.setattr(report_mod.selfupdate_mod, "release_check",
                         lambda *_a, **_k: None)
    assert cli_mod.cmd_board(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "取得試行の記録を確認できません" in out
    assert "まだ一度も" not in out, \
        "**記録が読めないだけかもしれないのに『一度も無い』と言い切っている**"

def test_1枚の画面は1組のSHAで作る(tmp_path, monkeypatch, capsys):
    """**F5 残件**（外部レビュー・P2・2026-09-12）。

    `board_summary()` が記録から A を取り出して表示用に持ったあと、
    `behind_release()` と `ahead_of_release()` が**それぞれ記録を読み直し、さらに
    可変の `HEAD` で数えていた。** その間に自己更新が B へ進むと、**表示は A、
    計数は B** になる。

    **確率の話ではない。** 読み直す限り、**その隙間に更新が入れば必ずずれる。**
    ここでは順序を固定して確かめる（記録を読んだ直後に更新を終わらせる）。
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])
    A = selfupdate.release_check(pair["work"])["release"]

    _advance_main(pair, "v2\n")
    _release(pair)

    本物 = selfupdate.release_check

    def 読んだ直後に更新が終わる():
        row = 本物(pair["work"])
        selfupdate._pull_locked(pair["work"])       # ここで B へ進む
        return row

    import functools
    monkeypatch.setattr(report_mod.accounts_mod, "list_account_names", lambda: [])
    monkeypatch.setattr(selfupdate, "release_check", 読んだ直後に更新が終わる)
    for name in ("behind_release", "ahead_of_release"):
        monkeypatch.setattr(selfupdate, name,
                             functools.partial(getattr(selfupdate, name), pair["work"]))
    素の_head = selfupdate.head
    monkeypatch.setattr(selfupdate, "head",
                         lambda app_dir=pair["work"]: 素の_head(app_dir))

    app = report_mod.board_summary()["app"]

    assert app["comparison_ref_sha"] == A, "表示の基準が記録と違う"
    assert app["head"] != A, "前提が崩れている（更新が入っていない）"
    assert app["ahead_cached_release"] == 1, \
        "**表示は古い基準、計数は新しい基準**（画面の途中で読み直している）"
    assert app["behind_cached_release"] == 0

def test_基準が無いまま渡されたら読み直して埋めない(tmp_path, monkeypatch):
    """**「渡していない」と「渡したが不明」を分ける**（外部レビュー・2026-09-12）。

    `base=None` を「省略」と読んでいたため、**記録が無い画面が `None` を渡すと、
    数える側が記録を読み直しに行った。** その間に別の更新が終わっていると、
    **無いはずの基準で `0`（＝一致しています）を返す。**

    **今日ずっと潰してきた「読めない ≠ 無い」を、引数の設計で作っていた。**
    """
    pair = _app_pair(tmp_path)
    selfupdate._pull_locked(pair["work"])          # 記録は「ある」状態にしておく
    assert selfupdate.behind_release(pair["work"]) == 0, "前提が崩れている"

    # **明示の `None` は「不明」。** 記録があっても、読み直して埋めない。
    assert selfupdate.behind_release(pair["work"], base=None) is None
    assert selfupdate.ahead_of_release(pair["work"], base=None) is None
    assert selfupdate.behind_release(pair["work"], head_sha=None) is None
    assert selfupdate.ahead_of_release(pair["work"], head_sha=None) is None

    # 省略したときは従来どおり読む。
    assert selfupdate.behind_release(pair["work"]) == 0


def test_記録が無い画面では数を出さない(tmp_path, monkeypatch, capsys):
    """**画面ぜんたいで見ても同じこと。** 基準が無いなら、数も無い。"""
    pair = _app_pair(tmp_path)
    assert selfupdate.release_check(pair["work"]) is None, "前提が崩れている"

    本物 = selfupdate.release_check

    def 読んだ直後に更新が終わる():
        row = 本物(pair["work"])                    # None（記録が無い）
        selfupdate._pull_locked(pair["work"])       # ここで記録ができてしまう
        return row

    import functools
    monkeypatch.setattr(report_mod.accounts_mod, "list_account_names", lambda: [])
    monkeypatch.setattr(selfupdate, "release_check", 読んだ直後に更新が終わる)
    for name in ("behind_release", "ahead_of_release"):
        monkeypatch.setattr(selfupdate, name,
                             functools.partial(getattr(selfupdate, name), pair["work"]))
    素の_head = selfupdate.head
    monkeypatch.setattr(selfupdate, "head",
                         lambda app_dir=pair["work"]: 素の_head(app_dir))

    app = report_mod.board_summary()["app"]
    assert app["comparison_ref_sha"] is None
    assert app["behind_cached_release"] is None, \
        "**基準が無いのに数を出している**（数える側が読み直して埋めた）"
    assert app["ahead_cached_release"] is None
