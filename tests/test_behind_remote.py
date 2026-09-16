"""`writeback.behind_remote()` と `thth queue`/`schedule`/`board` の遅れ表示
（発注 T8-2・kopicha 続報）。

`thth approve`（と `revoke`・`throw`・`collect`・`retract`）は既に
`writeback.sync_repo()` を通しているが、**読むだけの口**（`queue`・
`schedule`・`board`）は通していない。push した直後に読むだけの口を見ると、
timer の `collect` が pull するまでの間、まだ取り込み前の一覧が出る
——**読むだけの口が勝手に pull するのは避ける**のが設計の芯なので、
ここでは「遅れていることを言う」だけを確かめる（取り込みは T8-3 の
`thth pull` か `thth approve` が行う）。
"""
from __future__ import annotations

from tests.conftest import init_git_pair, run_git, run_thth, write_queue_file
from thth import writeback


def test_behind_remoteは追いついていれば0(tmp_path):
    pair = init_git_pair(tmp_path / "a", seed_content="", seed_name=".gitkeep")
    info = writeback.behind_remote(pair["work"])
    assert info == {"behind": 0, "ahead": 0, "head": info["head"],
                     "fetched_at": info["fetched_at"], "reason": None}
    assert info["head"]
    assert info["reason"] is None


def test_behind_remoteはfetchだけでpullしない(tmp_path):
    """**pull はしない**——fetch のあと、work の作業ツリー（HEAD）は動かない。"""
    pair = init_git_pair(tmp_path / "a", seed_content="旧", seed_name="a.md")
    head_before = run_git(pair["work"], ["rev-parse", "HEAD"]).stdout.strip()

    run_git(pair["seed"], ["commit", "--allow-empty", "-m", "second"])
    run_git(pair["seed"], ["push"])

    info = writeback.behind_remote(pair["work"])
    assert info["behind"] == 1
    assert info["ahead"] == 0

    head_after = run_git(pair["work"], ["rev-parse", "HEAD"]).stdout.strip()
    assert head_after == head_before, "fetch だけのはずが HEAD が動いた（pull してしまった）"


def test_behind_remoteはfetchできないとNoneとreason(tmp_path):
    broken = tmp_path / "broken"
    run_git_init = __import__("subprocess").run(
        ["git", "init", "-b", "main", str(broken)], capture_output=True, text=True)
    assert run_git_init.returncode == 0
    run_git(str(broken), ["remote", "add", "origin", "/does/not/exist/repo.git"])
    # **git の名前とメールを repo に持たせる**（`conftest.init_git_pair()` と同じ）。
    # 開発機（macOS）は全体の設定があるので通っていたが、GitHub Actions の
    # Linux には無く、`commit` が "Author identity unknown" で落ちた（2026-09-16）。
    run_git(str(broken), ["config", "user.email", "thth-test@example.invalid"])
    run_git(str(broken), ["config", "user.name", "thth-test"])
    run_git(str(broken), ["commit", "--allow-empty", "-m", "x"])

    info = writeback.behind_remote(str(broken))
    assert info["behind"] is None
    assert info["ahead"] is None
    assert info["reason"]
    assert "0" != info["reason"]  # **0 と混ぜない**: reason があるのに behind: 0 ではない


def test_queueは遅れているときに先頭に1行(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])
    write_queue_file(account["queue_dir"], "a.md",
                      fm_overrides={"status": "draft", "account": account["name"]})

    # **`seed` を origin の最新に合わせてから**新しい commit を積む（`work` の
    # `write_queue_file()` push で origin が進んでいるため、合わせないと
    # `seed` の push 自体が non-fast-forward で失敗する）。
    run_git(pair["seed"], ["pull"])
    run_git(pair["seed"], ["commit", "--allow-empty", "-m", "second"])
    run_git(pair["seed"], ["push"])

    result = run_thth(["queue", account["name"]])
    assert result.returncode == 0, result.stderr
    assert "remote に 1 commit 分の新しいものがあります" in result.stdout
    assert f"thth pull {account['name']}" in result.stdout


def test_queueは遅れていなければ何も出さない(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])
    write_queue_file(account["queue_dir"], "a.md",
                      fm_overrides={"status": "draft", "account": account["name"]})

    result = run_thth(["queue", account["name"]])
    assert result.returncode == 0, result.stderr
    assert "remote に" not in result.stdout
    assert "thth pull" not in result.stdout


def test_queueのjsonにrepoが乗る(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])
    write_queue_file(account["queue_dir"], "a.md",
                      fm_overrides={"status": "draft", "account": account["name"]})

    result = run_thth(["queue", account["name"], "--json"])
    assert result.returncode == 0, result.stderr
    import json
    out = json.loads(result.stdout)
    assert out[account["name"]]["repo"]["behind"] == 0


def test_scheduleは遅れているときに先頭に1行(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])
    write_queue_file(account["queue_dir"], "a.md",
                      fm_overrides={"status": "draft", "account": account["name"],
                                    "publish_at": "2026-09-20T08:00:00+09:00"})

    run_git(pair["seed"], ["pull"])
    run_git(pair["seed"], ["commit", "--allow-empty", "-m", "second"])
    run_git(pair["seed"], ["push"])

    result = run_thth(["schedule", account["name"]])
    assert result.returncode == 0, result.stderr
    assert "remote に 1 commit 分の新しいものがあります" in result.stdout


def test_boardは遅れているaccountの行の下に1行(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])

    run_git(pair["seed"], ["commit", "--allow-empty", "-m", "second"])
    run_git(pair["seed"], ["push"])

    result = run_thth(["board"])
    assert result.returncode == 0, result.stderr
    assert "repo が 1 commit 遅れています" in result.stdout
    assert f"thth pull {account['name']}" in result.stdout


def test_boardは遅れていなければ何も出さない(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    isolated_account_factory(repo_dir=pair["work"])

    result = run_thth(["board"])
    assert result.returncode == 0, result.stderr
    assert "repo が" not in result.stdout
