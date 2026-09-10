"""利用者 repo を select より前に同期する（設計 §3.3・外部レビュー再レビュー A）。

外部レビュアーの再レビュー（`/tmp/thth-review-qVnPaj/REVIEW.md`）が見つけた 2 件の
うち 1 件はここ: `thth/core.py` にも `cli.py` にも利用者 repo を pull する処理が
無く、timer が clone した時点の内容を永久に見てしまっていた（承認しても撤回しても
届かない）。`tests/test_atlas_review.py`（レビュアーの再現コードをそのまま取り込んだ
もの）にも同種のテストがあるが、こちらは THTH 自身の回帰スイートとして別に持つ
（「ローカルのファイル書き換えで模擬した」ことが今回の見逃しの原因だったので、実
プロセスの git repo で書く・統括の指示）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import init_git_pair, make_queue_text, run_git
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import writeback
from thth.adapters.base import PublishResult

import datetime

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
REL = "docs/sns/queue/a.md"


class _Spy:
    def __init__(self):
        self.calls = []

    def publish(self, post, **kw):
        self.calls.append(post.text)
        return PublishResult("SYNC_TEST_P1", None, NOW.isoformat())


def test_別cloneで承認したものが同期後に届く(tmp_path, isolated_account_factory):
    """A の核心: 「いま出ている timer は clone した時点の内容を永久に見る」を直す。

    THTH が使う `work` clone を古いまま（draft のまま）にしておき、別の clone
    （`seed`）側で承認して push する。`work` が同期しなければ従来どおり
    action=none のまま。同期していれば、この throw_once 1 回で承認済みの内容が
    見えて実際に publish される。
    """
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"}))
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True, quiet_hours=None, min_interval_hours=0)

    # 別 clone（seed）側で承認 → push する。work はこの変更をまだ知らない。
    seed_path = Path(pair["seed"]) / REL
    approved_text = make_queue_text({"status": "approved"})
    seed_path.write_text(approved_text, encoding="utf-8")
    run_git(pair["seed"], ["add", REL])
    run_git(pair["seed"], ["commit", "-m", "editor approves"])
    run_git(pair["seed"], ["push"])

    spy = _Spy()
    result = core.throw_once(
        account["name"], production_flag=True, adapter_factory=lambda *_: spy, now=NOW)

    assert result.exit_code == 0, result
    assert result.action == "post", result
    assert result.post_id == "SYNC_TEST_P1"
    assert spy.calls == ["本文です。"]


def test_別cloneで撤回したものは同期後に出ない(tmp_path, isolated_account_factory):
    """revocation 側（REVIEW.md の 2 件目と同種）。承認して push したあと、別 clone
    （`seed`）から `status: withdrawn` に書き換えて push する。THTH 側の `work` は
    この撤回をまだ知らないが、throw_once の中で同期すれば選ばれない。
    """
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "approved"}))
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True, quiet_hours=None, min_interval_hours=0)

    seed_path = Path(pair["seed"]) / REL
    text = seed_path.read_text()
    seed_path.write_text(text.replace("status: approved", "status: withdrawn"))
    run_git(pair["seed"], ["add", REL])
    run_git(pair["seed"], ["commit", "-m", "editor withdraws"])
    run_git(pair["seed"], ["push"])

    spy = _Spy()
    result = core.throw_once(
        account["name"], production_flag=True, adapter_factory=lambda *_: spy, now=NOW)

    assert not spy.calls, "撤回済みなのに publish が呼ばれた"
    assert result.action == "none"
    assert result.exit_code == 0


def test_同期に失敗したら投稿しない(tmp_path, isolated_account_factory):
    """`--ff-only` が失敗する状況（work に push できていない local commit が残って
    いて、同時に origin が別の内容で進んでいる）では、投稿を試みない（exit 2）。
    """
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "approved"}))
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True, quiet_hours=None, min_interval_hours=0)

    # work 側に「中断が残した」未 push のローカル commit を作る。
    work_path = Path(pair["work"]) / REL
    work_path.write_text(work_path.read_text().replace("本文です。", "中断中のローカル変更"))
    run_git(pair["work"], ["add", REL])
    run_git(pair["work"], ["commit", "-m", "leftover local commit"])

    # seed 側でも同じ行を別内容に変えて push しておく（fast-forward できなくする）。
    seed_path = Path(pair["seed"]) / REL
    seed_path.write_text(seed_path.read_text().replace("本文です。", "別の内容"))
    run_git(pair["seed"], ["add", REL])
    run_git(pair["seed"], ["commit", "-m", "remote diverges"])
    run_git(pair["seed"], ["push"])

    spy = _Spy()
    result = core.throw_once(
        account["name"], production_flag=True, adapter_factory=lambda *_: spy, now=NOW)

    assert not spy.calls, "同期に失敗したのに publish が呼ばれた"
    assert result.exit_code == 2
    assert result.action == "none"
    assert result.error == "repo_sync_failed"
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert inflight_mod.read(state_dir) is None


def test_repoを持たないアカウントは同期をスキップして壊れない(tmp_path):
    """`masaru-threads` の `repos/_none` 相当（存在しないディレクトリ）は、そこに
    queue が無いので同期を静かにスキップする（唯一の例外・外部レビュー第 3 巡 P1・
    `writeback.sync_repo()` 自体のユニットテスト）。"""
    missing = str(tmp_path / "repos" / "_none")
    ok, err, sha = writeback.sync_repo(missing)
    assert ok is True and err == ""


def test_存在するがgit_repoでないディレクトリは同期失敗として扱う(tmp_path):
    """外部レビュー第 3 巡 P1: ディレクトリが存在するが `.git` が無い場合は、
    もはや「同期不要」ではなく同期失敗として扱う（fail-closed への反転）。
    queue ファイルが残っているのに `.git` だけ失われる・退避される事故が
    3 巡目レビューで見つかった（後続の `test_atlas_third_review.py` の
    `missing_git` モードが `core.throw_once()` 越しの受け入れを見る）。"""
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    ok, err, sha = writeback.sync_repo(str(plain_dir))
    assert ok is False
    assert err


def test_git_repoなのにoriginが無ければ同期失敗として投稿しない(tmp_path):
    """外部レビュー再々レビュー P1・2: `repo_dir` が git repo なのに `origin` が
    無い場合は「同期の必要が無い」ではない。有効な承認済み queue を残したまま
    `origin` を外すと、同期元を確認できないまま公開に進んでしまう（旧実装は
    ここを成功扱いにしていた）。ここは同期失敗として扱う。"""
    git_no_remote = tmp_path / "git_no_remote"
    git_no_remote.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(git_no_remote)], check=True,
                    capture_output=True, text=True)
    ok, err, sha = writeback.sync_repo(str(git_no_remote))
    assert ok is False
    assert "origin" in err


def test_git_repoでoriginを外すと公開まで進まない(tmp_path, isolated_account_factory):
    """`core.throw_once()` を通した受け入れ確認: 有効な承認済み queue がある git
    repo から `origin` remote を外すと、`adapter.publish()` が一度も呼ばれずに
    exit 2 で止まる（外部レビュー再々レビュー P1・2 の受け入れ）。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "approved"}))
    account = isolated_account_factory(
        repo_dir=pair["work"], production=True, quiet_hours=None, min_interval_hours=0)
    run_git(pair["work"], ["remote", "remove", "origin"])

    spy = _Spy()
    result = core.throw_once(
        account["name"], production_flag=True, adapter_factory=lambda *_: spy, now=NOW)

    assert not spy.calls, "origin が無いのに publish が呼ばれた"
    assert result.exit_code == 2
    assert result.action == "none"
    assert result.error == "repo_sync_failed"
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert inflight_mod.read(state_dir) is None
