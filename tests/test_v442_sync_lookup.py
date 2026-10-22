"""4.4.2: 手元と VM の食い違い（計画 4.4 ①・本線 第7 10/22 の返事で推しの範囲）。

- lint・approve は、パスが VM に無ければその口座の repo を取り込んで 1 回だけ探し直す
  （push した直後に「VM の repo に無いパス」と断っていた）。
- 取り込んでも無ければ、VM と remote の commit を並べて push を確かめるよう言う。
- send --text-file に VM に無いパスが来たら、標準入力の道を言う。
- 書き戻しの push で rebase がぶつかったら、途中のまま残さない（kopicha #4254）。
"""
from __future__ import annotations

import os

from tests.conftest import init_git_pair, run_git, run_thth, write_queue_file
from thth import writeback


def _push_new_draft(account, name):
    seed = os.path.join(os.path.dirname(account["repo_dir"]), "seed")
    path = write_queue_file(os.path.join(seed, "docs", "sns", "queue"), name,
                            body=f"## threads\n\n{name} の本文です。\n",
                            fm_overrides={"account": account["name"], "status": "draft",
                                          "approved_sha": None})
    # write_queue_file が seed で commit と push まで済ませる（手元で push した形）。
    assert run_git(seed, ["status", "--porcelain"]).stdout.strip() == ""
    return os.path.relpath(path, seed)


def test_approve_pulls_when_path_was_just_pushed(isolated_account):
    rel = _push_new_draft(isolated_account, "fresh.md")
    assert not os.path.exists(os.path.join(isolated_account["repo_dir"], rel))
    first = run_thth(["approve", rel])
    # 2 は「VM に無い」の断り。取り込んで見つかれば 1 段目（digest を見せて rc 1）まで進む。
    assert first.returncode == 1, first.stderr
    assert "digest: " in first.stdout
    assert "取り込みました" in first.stderr


def test_lint_pulls_when_path_was_just_pushed(isolated_account):
    rel = _push_new_draft(isolated_account, "lintme.md")
    result = run_thth(["lint", rel])
    assert result.returncode == 0, result.stderr
    assert "取り込みました" in result.stderr


def test_missing_even_after_pull_names_both_commits(isolated_account):
    result = run_thth(["approve", "docs/sns/queue/never-pushed.md"])
    assert result.returncode == 2
    assert "VM の repo に無いパスです" in result.stderr
    assert "push は済んでいますか" in result.stderr


def test_send_text_file_refusal_offers_stdin(isolated_account):
    result = run_thth(["send", isolated_account["name"], "--text-file", "/Users/someone/draft.txt"])
    assert result.returncode == 2
    assert "標準入力で渡せます" in result.stderr


def test_conflicting_rebase_is_aborted_not_left_in_progress(tmp_path):
    pair = init_git_pair(tmp_path / "repo", seed_content="seed\n")
    seed, work = pair["seed"], pair["work"]
    with open(os.path.join(seed, "docs/sns/queue/a.md"), "w", encoding="utf-8") as stream:
        stream.write("elsewhere\n")
    run_git(seed, ["commit", "-q", "-am", "elsewhere"])
    run_git(seed, ["push", "-q", "origin", "main"])
    with open(os.path.join(pair["queue_dir"], "a.md"), "w", encoding="utf-8") as stream:
        stream.write("written back by thth\n")

    ok, err = writeback.commit_and_push(work, rel_path="docs/sns/queue/a.md", message="書き戻し")

    assert not ok and "push に失敗しました" in err
    for name in ("rebase-merge", "rebase-apply"):
        marker = run_git(work, ["rev-parse", "--git-path", name]).stdout.strip()
        assert not os.path.exists(os.path.join(work, marker)), name
    # 自分の commit は残る（手で直せる）。
    assert run_git(work, ["log", "-1", "--format=%s"]).stdout.strip() == "書き戻し"


# 監査 1: 前から rebase の途中の repo には commit しない（detached HEAD に積んで abort で消えていた）。
def test_preexisting_rebase_is_left_alone_and_nothing_is_lost(tmp_path):
    pair = init_git_pair(tmp_path / "repo", seed_content="seed\n")
    seed, work = pair["seed"], pair["work"]
    with open(os.path.join(seed, "docs/sns/queue/a.md"), "w", encoding="utf-8") as stream:
        stream.write("elsewhere\n")
    run_git(seed, ["commit", "-q", "-am", "elsewhere"])
    run_git(seed, ["push", "-q", "origin", "main"])
    with open(os.path.join(pair["queue_dir"], "a.md"), "w", encoding="utf-8") as stream:
        stream.write("local\n")
    run_git(work, ["commit", "-q", "-am", "local"])
    run_git(work, ["fetch", "-q", "origin"])
    import subprocess
    subprocess.run(["git", "-C", work, "rebase", "@{u}"], capture_output=True)   # ぶつかって途中のまま
    with open(os.path.join(pair["queue_dir"], "b.md"), "w", encoding="utf-8") as stream:
        stream.write("written back\n")

    ok, err = writeback.commit_and_push(work, rel_path="docs/sns/queue/b.md", message="書き戻し b")

    assert not ok and "rebase の途中" in err
    marker = run_git(work, ["rev-parse", "--git-path", "rebase-merge"]).stdout.strip()
    assert os.path.exists(os.path.join(work, marker))           # 人の途中の rebase はそのまま
    assert os.path.exists(os.path.join(pair["queue_dir"], "b.md"))   # 書いたものは消えない


# 監査 2: 取り込んで HEAD が進んでも目当てが無ければ、commit を並べて push を確かめるよう言う。
def test_moved_but_still_missing_names_both_commits(isolated_account):
    _push_new_draft(isolated_account, "other.md")
    result = run_thth(["approve", "docs/sns/queue/not-this-one.md"])
    assert result.returncode == 2
    assert "取り込みました" in result.stderr and "push は済んでいますか" in result.stderr


# 監査 3: 相対パスは、そのフォルダを持たない口座の repo を取り込まない。
def test_relative_path_does_not_pull_unrelated_repo(isolated_account_factory, tmp_path):
    from tests.conftest import init_real_repo
    main = isolated_account_factory()
    other_repo = init_real_repo(tmp_path, "unrelated")
    isolated_account_factory(name="other-threads", repo_dir=other_repo)
    _push_new_draft({"repo_dir": other_repo, "name": "other-threads"}, "x.md")
    before = run_git(other_repo, ["rev-parse", "HEAD"]).stdout.strip()
    result = run_thth(["lint", "nowhere/at/all.md"])
    assert result.returncode == 2
    assert run_git(other_repo, ["rev-parse", "HEAD"]).stdout.strip() == before
    assert main["name"]
