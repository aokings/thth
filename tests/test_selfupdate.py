"""app 自身の自己更新（設計 §3.2・2026-09-10 に未実装が発覚）。

VM の `/srv/thth/app` は `9e4817b` のまま、外部レビュー 4 巡分の修正が 1 つも
入らずに timer だけが 10 分ごとに回っていた。timer が動いているので「動いている」
ように見え、誰にも見えない形だった。ここではその再発を防ぐ。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from tests.conftest import init_git_pair
from thth import selfupdate


def _app_pair(tmp_path):
    """app repo に見立てた bare origin ＋ clone を作る。"""
    pair = init_git_pair(tmp_path, seed_content="v1\n", seed_name="version.txt")
    return pair


def _advance_origin(pair, text="v2\n"):
    path = os.path.join(pair["seed"], "docs", "sns", "queue", "version.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    subprocess.run(["git", "-C", pair["seed"], "commit", "-aqm", "advance"], check=True)
    subprocess.run(["git", "-C", pair["seed"], "push", "-q"], check=True)


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
    assert message and "fetch に失敗" in message
    assert execs == []


def test_遅れをcommit数で数える(tmp_path):
    pair = _app_pair(tmp_path)
    assert selfupdate.behind_origin(pair["work"]) == 0
    _advance_origin(pair)
    _advance_origin(pair, "v3\n")
    assert selfupdate.behind_origin(pair["work"], fetch=True) == 2, "遅れを数えられていない"


def test_git_repoでなければその旨を返す(tmp_path):
    plain = tmp_path / "ただのディレクトリ"
    plain.mkdir()
    message = selfupdate.pull_and_reexec(["thth"], app_dir=str(plain), log=lambda _l: None)
    assert message and "git repo" in message
