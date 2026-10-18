"""4.2.2（設計 S6 §17・kopicha の本番 10/18）: sparse-checkout の repo に THTH が記録を書くとき。

本番で起きたこと: レーンの repo の sparse-checkout の定義の外（`data/hatena/reactions/`）に反応の台帳を書き、
`git add` が断られ続けた（「次で送り直す」が毎回失敗）。

決めたこと（推しと理由は設計 S6 §17）: **`git add --sparse` では載せない。** 定義の外なら読まない・書かず、
理由と直し方（`git -C <repo> sparse-checkout add <dir>`）を言う。`--sparse` で載せると、次の同期（rebase・
merge）で git がそのファイルを作業ツリーから消し（skip-worktree）、追記の台帳を次の回が空から書き直して
履歴を上書きする——その git の振る舞いもここで本物の git で縛る。

repo は `tmp_path` の本物の git（bare の origin つき）。はてなには出ない（公開の読む口は偽物）。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_git
from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s2_http import fake_http  # noqa: F401
from tests.hatena_s6_helpers import NOW, URL1, bm, collect, ledger_file, ledger_rows, put_delivery, s6, world  # noqa: F401
from tests.test_collect import NOW as SNS_NOW, FakeAdapter
from thth import collect as collect_mod, hatena_reactions, writeback


def git(repo, *args, check=True):
    result = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if check:
        assert result.returncode == 0, (args, result.stderr)
    return result


def _queue_rel(lane):  # noqa: F811
    return os.path.relpath(lane.queue_dir, lane.repo)


def _sparse(repo, *dirs, cone=True):
    if cone:
        git(repo, "sparse-checkout", "set", "--cone", *dirs)
    else:
        git(repo, "sparse-checkout", "set", "--no-cone", *[f"/{d}/" for d in dirs])


# ---------------------------------------------------------------- 判定（cone とそうでない形）

@pytest.mark.parametrize("cone", [True, False])
def test_定義の外を判定する_ファイルが無くても(tmp_path, cone):
    pair = init_git_pair(tmp_path, seed_content="x")
    work = pair["work"]
    assert writeback.sparse_excluded(work, ["data/hatena/reactions/dl_1.ndjson"]) == []   # sparse でない
    _sparse(work, "data/sns", "docs/sns/queue", cone=cone)
    got = writeback.sparse_excluded(work, ["data/hatena/reactions/dl_1.ndjson", "data/sns/insights/posts/P.ndjson",
                                           "docs/sns/queue/a.md"])
    assert got == ["data/hatena/reactions/dl_1.ndjson"]


def test_commit_localは定義の外を載せず理由と直し方を言い_indexに触らない(tmp_path):
    """柵: `commit_local` の判定を外すと赤（git の断りのまま・直し方を言わない）。"""
    pair = init_git_pair(tmp_path, seed_content="x")
    work = pair["work"]
    _sparse(work, "data/sns")
    path = os.path.join(work, "data", "hatena", "reactions", "dl_1.ndjson")
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}\n")
    inside = os.path.join(work, "data", "sns", "inbox", "x.ndjson")
    os.makedirs(os.path.dirname(inside))
    with open(inside, "w", encoding="utf-8") as f:
        f.write("{}\n")
    head = git(work, "rev-parse", "HEAD").stdout
    ok, err = writeback.commit_local(work, rel_path=["data/sns/inbox/x.ndjson", "data/hatena/reactions/dl_1.ndjson"],
                                     message="m")
    assert not ok and err.startswith("sparse_checkout_outside: ")
    assert "data/hatena/reactions" in err and f"git -C {work} sparse-checkout add data/hatena/reactions" in err
    assert "data/sns" not in err          # 外のものだけを名指す（中のものは git add も呼ばない）
    assert git(work, "rev-parse", "HEAD").stdout == head
    assert git(work, "diff", "--cached", "--name-only").stdout == ""


def test_git_add_sparseで載せると次の同期で作業ツリーから消える_だから使わない(tmp_path):
    """設計の根拠（本物の git の振る舞い）。これが変われば設計 S6 §17 の推しを見直す。"""
    pair = init_git_pair(tmp_path, seed_content="x")
    work, seed = pair["work"], pair["seed"]
    _sparse(work, "data/sns")
    path = os.path.join(work, "data", "hatena", "reactions", "dl_1.ndjson")
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write("row1\n")
    git(work, "add", "--sparse", "--", "data/hatena/reactions/dl_1.ndjson")
    git(work, "commit", "--only", "-m", "r", "--", "data/hatena/reactions/dl_1.ndjson")
    with open(os.path.join(seed, "b.md"), "w", encoding="utf-8") as f:
        f.write("b\n")
    git(seed, "add", "b.md")
    git(seed, "commit", "-m", "b")
    git(seed, "push", "origin", "HEAD:main")
    git(work, "fetch", "origin")
    git(work, "rebase", "--autostash", "@{u}")
    assert not os.path.exists(path)                      # 作業ツリーから消えた
    assert git(work, "ls-files", "-t", "--", "data/hatena").stdout.startswith("S ")   # skip-worktree


# ---------------------------------------------------------------- はてなの反応の採取

def test_反応の台帳が定義の外なら読まず書かず理由を言い_足せば載る(s6):
    """柵: `hatena_reactions._entry` の確かめを外すと赤（書いた台帳を載せられず、毎回「次で送り直す」）。"""
    _sparse(s6.repo, "articles", "images", _queue_rel(s6), "data/sns")
    dl = put_delivery(URL1, hours_ago=25)
    s6.hatena.counts[URL1] = 2
    s6.hatena.entries[URL1] = [bm("alice"), bm("bob")]
    logs = []
    rc = hatena_reactions.run_collect("lane-a-hatenablog", now=NOW, log=logs.append)
    assert rc == 1
    assert any("sparse-checkout の定義の外" in line and "sparse-checkout add data/hatena/reactions" in line
               for line in logs), logs
    assert s6.hatena.calls == [] and not os.path.exists(ledger_file(dl))
    from thth import accounts, runs
    [row] = [r for r in runs.read_runs(accounts.state_dir_for("lane-a-hatenablog")) if r.get("action") == "hatena_collect"]
    assert row["status"] == "error" and row["error"] == "sparse_outside"
    # 人が定義に足せば、次の run で読み、載せる（作業ツリーにも残る）
    git(s6.repo, "sparse-checkout", "add", "data/hatena/reactions")
    assert collect() == 0
    assert len(ledger_rows(dl)) == 1 and os.path.exists(ledger_file(dl))
    assert "data/hatena/reactions/" in run_git(s6.repo, ["ls-tree", "-r", "--name-only", "origin/main"]).stdout
    assert "data/hatena" not in run_git(s6.repo, ["status", "--porcelain"]).stdout


# ---------------------------------------------------------------- SNS の採取（同じ形）

def test_SNSの採取も置き場が定義の外なら採らず書かず理由を言う(tmp_path, isolated_account_factory):
    """柵: `collect._collect_entry` の確かめを外すと赤（書いた台帳を載せられない）。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({
        "status": "posted", "post_id": "POST1", "posted_at": "2026-09-10T10:00:00+09:00"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    _sparse(pair["work"], "docs/sns/queue")
    adapter = FakeAdapter()
    logs = []
    rc = collect_mod.run_collect(account["name"], adapter=adapter, now=SNS_NOW, log=logs.append)
    assert rc == 1 and adapter.insight_calls == []
    assert any("sparse-checkout add data/sns" in line for line in logs), logs
    assert not os.path.exists(os.path.join(pair["work"], "data", "sns"))
    # 定義に足せば今までどおり（push まで）
    git(pair["work"], "sparse-checkout", "add", "data/sns")
    assert collect_mod.run_collect(account["name"], adapter=FakeAdapter(), now=SNS_NOW, log=lambda _l: None) == 0
    in_origin = git(pair["bare"], "ls-tree", "-r", "--name-only", "main").stdout
    assert "data/sns/insights/posts/POST1.ndjson" in in_origin
