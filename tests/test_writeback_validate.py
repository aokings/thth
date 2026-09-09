"""`writeback.commit_and_push()` の `validate` 引数（外部レビュー再レビュー B）。

外部レビュアーの再レビューが見つけたもう 1 件: `thth/core.py` の照合
（`text_mismatch_before_writeback`）は書き戻し前の**ローカルのファイル**に対して
行い、その**あと**に `commit_and_push()` の `pull --rebase` が remote の変更を
取り込む。rebase で remote の本文変更が取り込まれても push 前にもう一度照合しな
ければ、「本文 B ＋ 本文 A の post_id」が push される（元の穴が塞がっていない）。

`tests/test_atlas_review.py`（レビュアーの再現をそのまま取り込んだもの）にも
`test_remote_edit_during_publication_must_stop` として同種のテストがあるが、
こちらは THTH 自身の回帰スイートとして別に持つ（「ローカルのファイル書き換えで
模擬した」ことが今回の見逃しの原因だったので、実プロセスの git repo で書く・
統括の指示）。ここでは `thth.core.throw_once()` を経由せず、`writeback.commit_and_push()`
そのものを実 git repo に対して直接叩く、より狭いユニットテストにする。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import init_git_pair, run_git
from thth import writeback

REL = "docs/sns/queue/a.md"


def _twenty_lines() -> str:
    return "\n".join(f"line{i}" for i in range(20)) + "\n"


def test_rebase後に本文が食い違うとpushせずcommitは残る(tmp_path):
    # 20 行の内容にして、ローカルの変更（先頭行）と remote の変更（末尾行）を
    # 十分離す（git のデフォルトの diff context 3 行を超える距離）。こうすると
    # git は自動で衝突なくマージできる（衝突すると `pull --rebase` 自体が失敗して
    # しまい、「rebase は成功したが結果の中身が食い違う」という、この関数の
    # `validate` が本来検知すべき状況を再現できない）。
    seed_content = _twenty_lines()
    pair = init_git_pair(tmp_path, seed_content=seed_content, seed_name="a.md")
    work_path = Path(pair["work"]) / REL
    work_path.write_text(work_path.read_text().replace("line0", "line0-local"), encoding="utf-8")

    calls = []

    def validate():
        # rebase 後のファイルの中身を見て、自分が送ったはずの内容（"line0-local"
        # だけがあり、remote の変更が混ざっていない）かどうかを返す。
        content = work_path.read_text()
        calls.append(content)
        return "line0-local" in content and "line19-remote" not in content

    # commit する前に、別 clone（seed）側で同じファイルの別の行を書き換えて push
    # しておく（work の commit と衝突しない離れた行にする）。
    seed_path = Path(pair["seed"]) / REL
    seed_path.write_text(seed_path.read_text().replace("line19", "line19-remote"), encoding="utf-8")
    run_git(pair["seed"], ["add", REL])
    run_git(pair["seed"], ["commit", "-m", "remote edit"])
    run_git(pair["seed"], ["push"])

    with pytest.raises(writeback.PushValidationFailed):
        writeback.commit_and_push(
            pair["work"], rel_path=REL, message="local change", validate=validate)

    # push していない: origin 側にはまだ work の commit が届いていない。
    remote_log = run_git(pair["bare"], ["log", "--oneline", "-5"]).stdout
    assert "local change" not in remote_log

    # commit はローカルに残っている（手で直せる状態）。
    work_log = run_git(pair["work"], ["log", "--oneline", "-1"]).stdout
    assert "local change" in work_log

    # validate は「pull --rebase のあと」に呼ばれている＝rebase で取り込まれた
    # remote の変更（"line19-remote"）が見えている。
    assert any("line19-remote" in c for c in calls)


def test_validateが真なら通常どおりpushされる(tmp_path):
    pair = init_git_pair(tmp_path, seed_content="original\n", seed_name="a.md")
    work_path = Path(pair["work"]) / REL
    work_path.write_text("original\nlocal change\n", encoding="utf-8")

    ok, err = writeback.commit_and_push(
        pair["work"], rel_path=REL, message="local change ok", validate=lambda: True)
    assert ok, err

    remote_log = run_git(pair["bare"], ["log", "--oneline", "-1"]).stdout
    assert "local change ok" in remote_log


def test_validateを渡さなければ従来どおり(tmp_path):
    """後方互換: `validate` 省略時は今までどおり検証なしで push する。"""
    pair = init_git_pair(tmp_path, seed_content="original\n", seed_name="a.md")
    work_path = Path(pair["work"]) / REL
    work_path.write_text("original\nno validate\n", encoding="utf-8")

    ok, err = writeback.commit_and_push(pair["work"], rel_path=REL, message="no validate arg")
    assert ok, err
