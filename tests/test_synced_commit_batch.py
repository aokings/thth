"""原稿の照合をまとめて読む（5.12.4）: git の回数は原稿の数に比例しない・判定は変えない。

`thth board` が VM で毎回 90 秒かかっていた。`matches_synced_commit()` が原稿 1 本ごとに
git を 2 回呼んでいたため。ここは投稿の経路の安全の検査でもあるので、回数と同時に
「書き換えた原稿を見逃さない」「読めなければ一致と言わない」を本物の git で確かめる。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_git
from thth import core, managed_repo, writeback


def _repo(tmp_path, count, *, names=None):
    pair = init_git_pair(tmp_path, seed_content="", seed_name=".gitkeep")
    queue = Path(pair["queue_dir"])
    for name in names or [f"d{i:04}.md" for i in range(count)]:
        (queue / name).write_text(make_queue_text({"status": "draft", "approved_sha": None},
                                                  body=f"## threads\n\n{name} の本文\n"), encoding="utf-8")
    run_git(pair["work"], ["add", "-A"])
    run_git(pair["work"], ["commit", "-m", "drafts"])
    run_git(pair["work"], ["push"])
    cfg = {"repo_dir": pair["work"], "queue_dir": "docs/sns/queue"}
    return cfg, queue, writeback.upstream_sha(pair["work"])


def _verified(cfg, tree_sha):
    return {os.path.basename(qf.path): qf.verified for qf in core.list_queue_files(cfg, tree_sha=tree_sha)}


def _count_git(monkeypatch):
    calls = []
    real = subprocess.run

    def counting(cmd, *args, **kwargs):
        calls.append(cmd)
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return calls


def test_gitの回数は原稿の数に比例しない(tmp_path, monkeypatch):
    few, _, few_sha = _repo(tmp_path / "few", 3)
    many, _, many_sha = _repo(tmp_path / "many", 60)
    calls = _count_git(monkeypatch)

    assert all(_verified(few, few_sha).values())
    few_calls = len(calls)
    calls.clear()
    verdicts = _verified(many, many_sha)
    assert len(verdicts) == 60 and all(verdicts.values())

    assert len(calls) == few_calls == 2


def test_手で書き換えた原稿だけが不一致になる(tmp_path):
    cfg, queue, sha = _repo(tmp_path, 5)
    edited = queue / "d0002.md"
    edited.write_bytes(edited.read_bytes() + b" ")

    verdicts = _verified(cfg, sha)

    assert verdicts.pop("d0002.md") is False
    assert all(verdicts.values())


def test_commitに無い原稿とsymlinkは不一致になる(tmp_path):
    cfg, queue, sha = _repo(tmp_path, 2)
    (queue / "new.md").write_text((queue / "d0000.md").read_text(encoding="utf-8"), encoding="utf-8")
    (queue / "link.md").symlink_to(queue / "d0001.md")

    verdicts = _verified(cfg, sha)

    assert verdicts == {"d0000.md": True, "d0001.md": True, "link.md": False, "new.md": False}


def test_空白と日本語の名前もまとめて読める(tmp_path):
    cfg, _, sha = _repo(tmp_path, 0, names=["a b.md", "原稿 missing.md", "c.md"])

    assert all(_verified(cfg, sha).values())


def test_改行を含む名前も今までどおり照合する(tmp_path):
    cfg, queue, sha = _repo(tmp_path, 0, names=["a\nb.md", "c.md"])

    assert all(_verified(cfg, sha).values())
    (queue / "a\nb.md").write_bytes(b"x")
    assert _verified(cfg, sha) == {"a\nb.md": False, "c.md": True}


def test_照合先がNoneなら全部不一致(tmp_path):
    cfg, _, _ = _repo(tmp_path, 3)

    assert not any(_verified(cfg, None).values())


def test_照合先はHEADでなく渡されたcommitに固定する(tmp_path):
    cfg, queue, old_sha = _repo(tmp_path, 2)
    target = queue / "d0000.md"
    target.write_bytes(target.read_bytes() + b"local\n")
    run_git(cfg["repo_dir"], ["commit", "-am", "local only"])

    assert _verified(cfg, old_sha) == {"d0000.md": False, "d0001.md": True}


def test_覚えを持ち越さず呼ぶたびに読み直す(tmp_path):
    cfg, queue, sha = _repo(tmp_path, 2)
    assert all(_verified(cfg, sha).values())

    (queue / "d0001.md").write_bytes(b"changed")

    assert _verified(cfg, sha)["d0001.md"] is False


def test_1本の口もまとめの口と同じ判定(tmp_path):
    cfg, queue, sha = _repo(tmp_path, 1)
    path = str(queue / "d0000.md")
    raw = Path(path).read_bytes()

    assert writeback.matches_synced_commit(cfg["repo_dir"], path, tree_sha=sha, disk_bytes=raw)
    assert writeback.matches_synced_commit(cfg["repo_dir"], path, tree_sha=sha)
    assert not writeback.matches_synced_commit(cfg["repo_dir"], path, tree_sha=sha, disk_bytes=raw + b"x")


def _break_batch(monkeypatch, change):
    real = writeback._run_git_bytes

    def broken(repo_dir, args, **kwargs):
        result = real(repo_dir, args, **kwargs)
        if "--batch" in args:
            return change(result)
        return result

    monkeypatch.setattr(writeback, "_run_git_bytes", broken)


@pytest.mark.parametrize("change", [
    pytest.param(lambda r: subprocess.CompletedProcess(r.args, 128, r.stdout, b"fatal"), id="終了コード"),
    pytest.param(lambda r: subprocess.CompletedProcess(r.args, 0, r.stdout[:len(r.stdout) // 2], b""), id="途中で切れた"),
    pytest.param(lambda r: subprocess.CompletedProcess(r.args, 0, b"", b""), id="空"),
    pytest.param(lambda r: subprocess.CompletedProcess(r.args, 0, r.stdout + b"extra\n", b""), id="余る"),
    pytest.param(lambda r: subprocess.CompletedProcess(r.args, 0, b"nonsense\n" + r.stdout, b""), id="形が違う"),
])
def test_まとめ読みが失敗したら全部不一致(tmp_path, monkeypatch, change):
    cfg, _, sha = _repo(tmp_path, 3)
    _break_batch(monkeypatch, change)

    assert not any(_verified(cfg, sha).values())


def test_まとめ読みを起動できなければ全部不一致(tmp_path, monkeypatch):
    cfg, _, sha = _repo(tmp_path, 3)

    def refuse(result):
        raise OSError("起動できない")

    _break_batch(monkeypatch, refuse)

    assert not any(_verified(cfg, sha).values())


def test_本物のgitでobjectが壊れた原稿は一致と言わない(tmp_path):
    cfg, queue, sha = _repo(tmp_path, 3)
    oid = run_git(cfg["repo_dir"], ["rev-parse", f"{sha}:docs/sns/queue/d0001.md"]).stdout.strip()
    loose = Path(cfg["repo_dir"]) / ".git" / "objects" / oid[:2] / oid[2:]
    loose.chmod(0o644)
    loose.write_bytes(b"not a git object")

    assert _verified(cfg, sha)["d0001.md"] is False


def test_managed_repoの口もinputを渡す(monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake)

    managed_repo.run("/nowhere", ["cat-file", "--batch"], text=False, check=False, input=b"x\n")

    assert seen["input"] == b"x\n"
