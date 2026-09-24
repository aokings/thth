"""3.8.2 件 2: `thth approve --confirm-file <file> --by <名前>`（複数の確定を 1 回で）。

- 1 行に `<原稿のパス> <digest>`。1 回の同期と 1 回のロックの中で行の順に確定する。
- digest が合わない本はその 1 本だけ断って他は進める（断った本と理由は最後に並べる）。
- commit は 1 本ずつ・push は最後に 1 回。
- 同期・push で止まったら、どこまで通ったかと残りをそのまま打てる命令を出す。
- 1 段目（複数のとき）に `--confirm-file` の中身の形を 1 行。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest

from tests.conftest import run_git, run_thth, write_queue_file
from thth import cli
from thth import lock as lock_mod
from thth import writeback

NAMES = ["a.md", "b.md", "c.md"]


def _digest(stdout: str) -> str:
    return [line.split(": ", 1)[1].strip() for line in stdout.splitlines()
            if line.startswith("digest: ")][0]


@pytest.fixture
def drafts(isolated_account, tmp_path):
    """3 本の draft（commit・push 済み）と、それぞれの 1 段目の digest。"""
    paths = [write_queue_file(isolated_account["queue_dir"], name,
                              body=f"## threads\n\n{name} の本文です。\n",
                              fm_overrides={"account": isolated_account["name"], "status": "draft",
                                            "approved_sha": None})
             for name in NAMES]
    digests = []
    for path in paths:
        first = run_thth(["approve", path])
        assert first.returncode == 1, first.stderr
        digests.append(_digest(first.stdout))
    work = isolated_account["repo_dir"]
    return {"paths": paths, "digests": digests, "work": work,
            "bare": os.path.join(os.path.dirname(work), "origin.git"),
            "seed": os.path.join(os.path.dirname(work), "seed"),
            "file": str(tmp_path / "confirm.txt")}


def _write_confirm_file(drafts, digests=None, extra_lines=()) -> str:
    digests = digests or drafts["digests"]
    lines = ["# 確定の一覧（masaru）", ""]
    lines += [f"{path} {digest}" for path, digest in zip(drafts["paths"], digests)]
    lines += list(extra_lines)
    with open(drafts["file"], "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return drafts["file"]


def _origin_status(drafts, name: str) -> str:
    shown = run_git(drafts["bare"], ["show", f"main:docs/sns/queue/{name}"]).stdout
    return [line.split(": ", 1)[1] for line in shown.splitlines() if line.startswith("status: ")][0]


def _approval_commits(repo: str, ref: str = "HEAD") -> list:
    log = run_git(repo, ["log", "--format=%s", ref]).stdout.splitlines()
    return [subject for subject in log if subject.startswith("承認: ")]


def test_3本の組が全部通る_同期とロックとpushは1回ずつ_commitは1本ずつ(drafts, monkeypatch, capsys):
    counts = {"sync": 0, "push": 0, "lock": 0}
    real_sync, real_push, real_acquire = writeback.sync_repo, writeback.push_committed, lock_mod.acquire

    def sync(repo_dir):
        counts["sync"] += 1
        return real_sync(repo_dir)

    def push(repo_dir, **kwargs):
        counts["push"] += 1
        return real_push(repo_dir, **kwargs)

    def acquire(lock, wait=0):
        counts["lock"] += 1
        return real_acquire(lock, wait)

    monkeypatch.setattr(writeback, "sync_repo", sync)
    monkeypatch.setattr(writeback, "push_committed", push)
    monkeypatch.setattr(lock_mod, "acquire", acquire)

    rc = cli.main(["approve", "--confirm-file", _write_confirm_file(drafts), "--by", "masaru"])
    out, err = capsys.readouterr()

    assert rc == 0, err
    assert counts == {"sync": 1, "push": 1, "lock": 1}
    assert out.splitlines()[0] == "承認しました: 3 本（masaru）"
    for name in NAMES:
        assert _origin_status(drafts, name) == "approved"
    # commit は従前どおり 1 本ずつ（新しい順）。
    assert _approval_commits(drafts["bare"], "main")[:3] == [
        f"承認: {name}（nigamilab-threads・masaru）" for name in reversed(NAMES)]
    assert "report_channel" not in err


def test_1本だけdigestが違えばその1本だけ断り_他は進める(drafts, capsys):
    digests = list(drafts["digests"])
    digests[1] = "0" * 12
    rc = cli.main(["approve", "--confirm-file", _write_confirm_file(drafts, digests), "--by", "masaru"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert out.splitlines()[0] == "承認しました: 2 本（masaru）"
    assert _origin_status(drafts, "a.md") == "approved"
    assert _origin_status(drafts, "b.md") == "draft"
    assert _origin_status(drafts, "c.md") == "approved"
    assert "断った原稿: 1 本（ほかは進めました）" in err
    refused = [line for line in err.splitlines() if line.startswith("  4 行目 ")]
    assert len(refused) == 1 and drafts["paths"][1] in refused[0]
    assert f"いまの digest は {drafts['digests'][1]} です" in refused[0]


def test_形の違う行とパスの無い行もその1行だけ断る(drafts, capsys):
    path = _write_confirm_file(drafts, extra_lines=["digest-だけ", "/no/such/file.md " + "a" * 12])
    rc = cli.main(["approve", "--confirm-file", path, "--by", "masaru", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["count"] == 3 and payload["pushed"] is True
    assert [row["line"] for row in payload["refused"]] == [6, 7]
    assert payload["refused"][0]["reason"].startswith("行の形が違います")


def test_pushに失敗したら_commitまで済んだ本とそのまま打てるpushの命令を出す(drafts, capsys):
    hook = os.path.join(drafts["bare"], "hooks", "pre-receive")
    with open(hook, "w", encoding="utf-8") as stream:
        stream.write("#!/bin/sh\necho 'rejected for test' >&2\nexit 1\n")
    os.chmod(hook, 0o755)

    rc = cli.main(["approve", "--confirm-file", _write_confirm_file(drafts), "--by", "masaru"])
    out, err = capsys.readouterr()

    assert rc == 1
    assert "まだ push できていません" in out.splitlines()[0]
    assert "承認を commit しましたが push できませんでした" in err
    push_line = [line for line in err.splitlines() if "push が通れば出ます: " in line][0]
    command = push_line.split("push が通れば出ます: ", 1)[1]
    assert command == f"git -C {shlex.quote(os.path.realpath(drafts['work']))} push"
    # 3 本とも commit はローカルに残り、origin にはまだ無い。
    assert len(_approval_commits(drafts["work"])) == 3
    assert _approval_commits(drafts["bare"], "main") == []
    # 出した命令をそのまま打てば届く（hook を外してから）。
    os.unlink(hook)
    assert subprocess.run(shlex.split(command), capture_output=True, text=True).returncode == 0
    for name in NAMES:
        assert _origin_status(drafts, name) == "approved"


def test_同期で止まったら_残りをそのまま打てる確定の命令で出す(drafts, capsys):
    # 手元に push していない commit があり、origin も進んでいる（ff できない）。
    work = drafts["work"]
    with open(os.path.join(work, "local.txt"), "w", encoding="utf-8") as stream:
        stream.write("local\n")
    run_git(work, ["add", "local.txt"])
    run_git(work, ["commit", "-q", "-m", "local only"])
    run_git(drafts["seed"], ["pull", "-q", "--ff-only"])
    with open(os.path.join(drafts["seed"], "elsewhere.txt"), "w", encoding="utf-8") as stream:
        stream.write("elsewhere\n")
    run_git(drafts["seed"], ["add", "elsewhere.txt"])
    run_git(drafts["seed"], ["commit", "-q", "-m", "elsewhere"])
    run_git(drafts["seed"], ["push", "-q"])

    rc = cli.main(["approve", "--confirm-file", _write_confirm_file(drafts), "--by", "masaru"])
    out, err = capsys.readouterr()

    assert rc == 1 and out == ""
    assert err.splitlines()[0].startswith("repo を同期できないので承認しません: ")
    assert "通ったもの: 0 本。残り 3 本はそのまま打てます:" in err
    commands = [line.strip() for line in err.splitlines() if line.startswith("  thth approve ")]
    assert commands == [f"thth approve {shlex.quote(path)} --confirm {digest} --by masaru"
                        for path, digest in zip(drafts["paths"], drafts["digests"])]
    for path in drafts["paths"]:
        assert "status: draft" in open(path, encoding="utf-8").read()
    # 直したあと、出た命令をそのまま打てば通る。
    run_git(work, ["reset", "-q", "--hard", "origin/main"])
    result = run_thth(shlex.split(commands[0])[1:])
    assert result.returncode == 0, result.stderr


def test_confirm_fileにハイフンを渡すと標準入力から読む(drafts):
    """手元（Mac）のファイルは VM に無いので、`--confirm-file -` で標準入力から渡せる。"""
    lines = "".join(f"{path} {digest}\n" for path, digest in zip(drafts["paths"], drafts["digests"]))
    result = run_thth(["approve", "--confirm-file", "-", "--by", "masaru"], stdin=lines)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "承認しました: 3 本（masaru）"
    missing = run_thth(["approve", "--confirm-file", "/Users/someone/confirm.txt", "--by", "masaru"])
    assert missing.returncode == 2 and "手元のパス" in missing.stderr


def test_1段目が複数のときconfirm_fileの形を1行添える(drafts):
    first = run_thth(["approve", drafts["paths"][0], drafts["paths"][1]])
    assert first.returncode == 1
    lines = [line for line in first.stdout.splitlines() if "--confirm-file" in line]
    assert lines == ["1 本ずつの digest で確定するなら: thth approve --confirm-file <file> --by <名前>"
                     "（<file> は 1 行に「<原稿のパス> <digest>」）"]
    single = run_thth(["approve", drafts["paths"][0]])
    assert "--confirm-file" not in single.stdout


def test_confirm_fileは名指しや_confirmと一緒に使えない(drafts, capsys):
    path = _write_confirm_file(drafts)
    assert cli.main(["approve", drafts["paths"][0], "--confirm-file", path, "--by", "m"]) == 2
    assert cli.main(["approve", "--confirm-file", path, "--confirm", "a" * 12, "--by", "m"]) == 2
    assert cli.main(["approve"]) == 2
    capsys.readouterr()
    for path in drafts["paths"]:
        assert "status: draft" in open(path, encoding="utf-8").read()
