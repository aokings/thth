"""`thth pull <account|--project P>`（発注 T8-3・kopicha 続報）。

読むだけの口（`queue`・`schedule`・`board`。T8-2）は遅れを**言うだけ**で
pull しない。この道具は、その案内から誘導される**明示の**取り込み口——
`writeback.sync_repo()` を呼ぶだけで、`thth approve` が既に通しているのと
同じ関数・同じ repo ロックを使う。
"""
from __future__ import annotations

import json

from tests.conftest import init_git_pair, run_git, run_thth


def test_pullはffして取り込む(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="旧", seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"])
    head_before = run_git(pair["work"], ["rev-parse", "--short", "HEAD"]).stdout.strip()

    run_git(pair["seed"], ["commit", "--allow-empty", "-m", "second"])
    run_git(pair["seed"], ["push"])
    head_after_remote = run_git(pair["seed"], ["rev-parse", "--short", "HEAD"]).stdout.strip()

    result = run_thth(["pull", account["name"]])
    assert result.returncode == 0, result.stderr
    assert f"取り込みました: {head_before} → {head_after_remote}（1 commit）" in result.stdout

    # 本当に取り込まれている（work の HEAD が動いた）。
    head_now = run_git(pair["work"], ["rev-parse", "--short", "HEAD"]).stdout.strip()
    assert head_now == head_after_remote


def test_pullはすでに最新なら何も変えない(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])
    head_before = run_git(pair["work"], ["rev-parse", "--short", "HEAD"]).stdout.strip()

    result = run_thth(["pull", account["name"]])
    assert result.returncode == 0, result.stderr
    assert "すでに最新です" in result.stdout

    head_after = run_git(pair["work"], ["rev-parse", "--short", "HEAD"]).stdout.strip()
    assert head_after == head_before


def test_pullのjsonはbehind_remoteの形(isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="", seed_name=".gitkeep")
    account = isolated_account_factory(repo_dir=pair["work"])

    result = run_thth(["pull", account["name"], "--json"])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert len(out) == 1
    row = out[0]
    assert row["account"] == account["name"]
    assert row["ok"] is True
    assert set(row["repo"].keys()) == {"behind", "ahead", "head", "fetched_at", "reason"}
    assert row["repo"]["behind"] == 0


def test_pullのprojectは同じrepoを共有する2accountで1回だけ引く(
        isolated_account_factory, tmp_path):
    pair = init_git_pair(tmp_path / "acct", seed_content="旧", seed_name="a.md")
    account_a = isolated_account_factory("a-threads", repo_dir=pair["work"], project="shared")
    account_b = isolated_account_factory("b-threads", repo_dir=pair["work"], project="shared")
    assert account_a["repo_dir"] == account_b["repo_dir"]

    run_git(pair["seed"], ["commit", "--allow-empty", "-m", "second"])
    run_git(pair["seed"], ["push"])

    result = run_thth(["pull", "--project", "shared", "--json"])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    # **重複なく 1 回だけ**——2 account だが repo は 1 つ。
    assert len(out) == 1
    assert out[0]["repo"]["behind"] == 0


def test_pullは失敗をsync_repoの理由のままrc1(isolated_account_factory, tmp_path):
    account = isolated_account_factory(repo_dir=str(tmp_path / "no-such-repo-dir"))

    result = run_thth(["pull", account["name"]])
    assert result.returncode == 1, result.stdout
    assert "取り込めませんでした" in result.stderr or "見当たりません" in result.stderr


def test_pullはaccountもprojectも無ければ断る(isolated_account_factory):
    result = run_thth(["pull"])
    assert result.returncode == 2
    assert "account" in result.stderr


def test_pullは未知のprojectを断る(isolated_account_factory):
    result = run_thth(["pull", "--project", "no-such-project"])
    assert result.returncode == 2
    assert "no-such-project" in result.stderr
