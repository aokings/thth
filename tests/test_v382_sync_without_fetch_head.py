"""3.8.2 件 1: 同期で FETCH_HEAD に依らない（報告 r20260924-9f07f36b・P1）。

実測: 確定の命令を `set -e` で続けて打つと 1 本目だけ承認され、2 本目で
「git pull --ff-only に失敗しました: fatal: Cannot fast-forward to multiple branches.」。
`git pull` は自分の fetch のあとに FETCH_HEAD を読み直して取り込む相手を決めるので、
その間に別のプロセスの fetch が FETCH_HEAD に行を足すと for-merge の行が 2 つになる。

**本物の git で再現する。** 「別のプロセスの fetch」は、git の実行ファイルの前に
挟んだ小さな包み（`GIT_EXEC_PATH` と `PATH` の先頭）で作る——**どの fetch の直後にも、
別の fetch が `--append` で FETCH_HEAD に for-merge の行を足す**。`git pull` が内部で
呼ぶ fetch もこの包みを通るので、従前の `pull --ff-only` はここで必ず断られる
（単変異で確かめる）。直したあとの `merge --ff-only @{u}` は FETCH_HEAD を読まない。
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.conftest import init_git_pair, run_git, run_thth, write_queue_file
from thth import writeback


def _for_merge_lines(repo: str) -> list:
    path = os.path.join(repo, ".git", "FETCH_HEAD")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as stream:
        return [line for line in stream.read().splitlines()
                if line.strip() and "\tnot-for-merge\t" not in line]


def _push_other_branch(seed: str) -> None:
    """origin に `other` 枝を置く（別の枝も for-merge にする fetch の相手）。"""
    run_git(seed, ["checkout", "-q", "-b", "other"])
    with open(os.path.join(seed, "other.txt"), "w", encoding="utf-8") as stream:
        stream.write("other\n")
    run_git(seed, ["add", "other.txt"])
    run_git(seed, ["commit", "-q", "-m", "other"])
    run_git(seed, ["push", "-q", "origin", "other"])
    run_git(seed, ["checkout", "-q", "main"])


def _advance_upstream(seed: str, name: str) -> str:
    """別の clone が origin/main を 1 commit 進める（承認の対象とは別のファイル）。"""
    # 先に追いつく（`pull` は使わない——包みが足す行で試験の側が断られるため）。
    run_git(seed, ["fetch", "-q", "origin"])
    run_git(seed, ["rebase", "-q", "origin/main"])
    with open(os.path.join(seed, name), "w", encoding="utf-8") as stream:
        stream.write(name + "\n")
    run_git(seed, ["add", name])
    run_git(seed, ["commit", "-q", "-m", f"elsewhere: {name}"])
    run_git(seed, ["push", "-q", "origin", "main"])
    return run_git(seed, ["rev-parse", "HEAD"]).stdout.strip()


def _install_concurrent_fetch(tmp_path, monkeypatch, extra: list) -> None:
    """どの `git fetch` の直後にも、別の fetch が FETCH_HEAD に for-merge の行を足す。

    `extra` は足す fetch の引数（`["refs/heads/other"]` なら別の枝、`[]` なら同じ
    upstream の行がもう一度——並んで走った `fetch origin` 2 本が書き込みを
    重ねた形）。本物の git をそのまま呼び、fetch のときだけ後ろに 1 本足す。
    """
    real_git = shutil.which("git")
    real_exec = subprocess.run([real_git, "--exec-path"], capture_output=True, text=True,
                               check=True).stdout.strip()
    wrap = tmp_path / "concurrent-fetch-exec"
    wrap.mkdir()
    for entry in os.listdir(real_exec):
        if entry != "git":
            os.symlink(os.path.join(real_exec, entry), wrap / entry)
    append = " ".join(["fetch", "--append", "--quiet", "origin", *extra])
    script = wrap / "git"
    # 素通しの側は GIT_EXEC_PATH を包みのまま渡す（`git pull` が中で呼ぶ fetch も
    # 包みを通るように）。足す側の fetch だけ本物の置き場で呼ぶ（包みを二重に通さない）。
    script.write_text(
        "#!/bin/sh\n"
        f"GIT_EXEC_PATH='{wrap}' '{real_git}' \"$@\"\n"
        "rc=$?\n"
        "if [ \"$1\" = fetch ] || { [ \"$1\" = -C ] && [ \"$3\" = fetch ]; }; then\n"
        "  dir=.\n"
        "  [ \"$1\" = -C ] && dir=\"$2\"\n"
        f"  GIT_EXEC_PATH='{real_exec}' '{real_git}' -C \"$dir\" {append} >/dev/null 2>&1\n"
        "fi\n"
        "exit $rc\n", encoding="utf-8")
    script.chmod(0o755)
    # `git pull` は内部の fetch を GIT_EXEC_PATH の `git` で呼ぶ。道具が直接呼ぶ
    # `git -C <repo> fetch origin` は PATH の `git` で呼ばれる。両方に挟む。
    monkeypatch.setenv("GIT_EXEC_PATH", str(wrap))
    monkeypatch.setenv("PATH", str(wrap) + os.pathsep + os.environ.get("PATH", ""))


@pytest.fixture
def pair(tmp_path):
    info = init_git_pair(tmp_path / "repo", seed_content="seed\n")
    _push_other_branch(info["seed"])
    return info


@pytest.mark.parametrize("extra", [["refs/heads/other"], []], ids=["別の枝", "同じ枝がもう一度"])
def test_FETCH_HEADにfor_mergeが2行あっても同期が通り_upstreamに追いつく(pair, tmp_path, monkeypatch, extra):
    work = pair["work"]
    target = _advance_upstream(pair["seed"], "b.txt")
    # 別の枝も for-merge にした fetch の直後（FETCH_HEAD に for-merge が 2 行）。
    run_git(work, ["fetch", "-q", "origin", "main", "other"])
    assert len(_for_merge_lines(work)) == 2

    _install_concurrent_fetch(tmp_path, monkeypatch, extra)
    ok, err, sha = writeback.sync_repo(work)

    assert ok, err
    # 同期の最中も FETCH_HEAD は for-merge 2 行のまま（読まなかったから通った）。
    assert len(_for_merge_lines(work)) == 2
    assert sha == target
    assert run_git(work, ["rev-parse", "HEAD"]).stdout.strip() == target


def test_ffできないときは従前どおり断る(pair, tmp_path, monkeypatch):
    work = pair["work"]
    with open(os.path.join(work, "local.txt"), "w", encoding="utf-8") as stream:
        stream.write("local\n")
    run_git(work, ["add", "local.txt"])
    run_git(work, ["commit", "-q", "-m", "local only"])
    before = run_git(work, ["rev-parse", "HEAD"]).stdout.strip()
    _advance_upstream(pair["seed"], "b.txt")

    _install_concurrent_fetch(tmp_path, monkeypatch, ["refs/heads/other"])
    ok, err, sha = writeback.sync_repo(work)

    assert not ok and sha is None
    assert err.startswith("git merge --ff-only @{u} に失敗しました")
    # 何も書き換えない（枝分かれのまま・手で直せる状態）。
    assert run_git(work, ["rev-parse", "HEAD"]).stdout.strip() == before


def test_pushの再試行の取り込みもFETCH_HEADを読まない(pair, tmp_path, monkeypatch):
    work = pair["work"]
    elsewhere = _advance_upstream(pair["seed"], "b.txt")
    with open(os.path.join(pair["queue_dir"], "seed.md"), "a", encoding="utf-8") as stream:
        stream.write("承認の書き戻し\n")
    _install_concurrent_fetch(tmp_path, monkeypatch, ["refs/heads/other"])

    ok, err = writeback.commit_and_push(
        work, rel_path=os.path.join("docs", "sns", "queue", "seed.md"), message="承認: seed.md")

    assert ok, err
    # origin に届いている（別の clone の commit の上に載っている）。
    head = run_git(pair["bare"], ["rev-parse", "main"]).stdout.strip()
    assert run_git(work, ["rev-parse", "HEAD"]).stdout.strip() == head
    assert run_git(pair["bare"], ["merge-base", "--is-ancestor", elsewhere, head]).returncode == 0


# ------------------------------------------------------------ 確定を 3 本続けて打つ

def _digest(stdout: str) -> str:
    return [line.split(": ", 1)[1].strip() for line in stdout.splitlines()
            if line.startswith("digest: ")][0]


def test_確定を3本続けて打つと_間に別のfetchを挟んでも3本とも承認される(isolated_account, tmp_path, monkeypatch):
    work = isolated_account["repo_dir"]
    seed = os.path.join(os.path.dirname(work), "seed")
    bare = os.path.join(os.path.dirname(work), "origin.git")
    _push_other_branch(seed)
    names = ["a.md", "b.md", "c.md"]
    paths = [write_queue_file(isolated_account["queue_dir"], name,
                              body=f"## threads\n\n{name} の本文です。\n",
                              fm_overrides={"account": isolated_account["name"], "status": "draft",
                                            "approved_sha": None})
             for name in names]
    # 1 段目を先に 3 本（masaru は digest を並べてから確定を 10 行続けて打った）。
    digests = []
    for path in paths:
        first = run_thth(["approve", path])
        assert first.returncode == 1, first.stderr
        digests.append(_digest(first.stdout))

    _install_concurrent_fetch(tmp_path, monkeypatch, ["refs/heads/other"])
    for i, (path, digest) in enumerate(zip(paths, digests)):
        # 確定の合間に、別のプロセスの fetch（別の枝も for-merge）と別の clone の push。
        _advance_upstream(seed, f"elsewhere-{i}.txt")
        run_git(work, ["fetch", "-q", "origin", "main", "other"])
        assert len(_for_merge_lines(work)) >= 2
        result = run_thth(["approve", path, "--confirm", digest, "--by", "masaru"])
        assert result.returncode == 0, f"{i + 1} 本目: {result.stderr}"

    for name in names:
        shown = run_git(bare, ["show", f"main:docs/sns/queue/{name}"]).stdout
        assert "status: approved" in shown and "approved_by: masaru" in shown
