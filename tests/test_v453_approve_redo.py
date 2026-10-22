"""4.5.3 の柵: 承認のやり直しの手数（計画 4.4.3・こぴちゃ 第3 の 2・3・6）。

- (1) `thth approve --digest-only`: 本文を出さず束の digest を 1 行だけ標準出力へ。lint の誤りは標準エラーと
  rc 1。`--confirm` とは一緒に使えない。出した digest で `--confirm` が通る。
- (2) `thth revoke` の複数・ディレクトリ・`--account`・`-`（標準入力のパスの並び）。取り消せない本だけ断る。
  同期は repo ごとに 1 回（と書いたあとの見直しの 1 回）。相対パスが 2 つの repo に当たっても `--account` で解ける。
- (3) 手元のラッパ（`bin/thth-remote`）は、`-`・`--…stdin`・本文の引数の無い `send` のときだけ
  標準入力を VM へ渡す。それ以外は `ssh -n`（`while read` のループの入力を吸わない）。
- (4) 承認済みの原稿の本文を直したら、revoke せずに approve の 2 段で承認し直せる。1 段目が「差し替わる」と言う。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.conftest import approve_via_cli, init_git_pair, make_queue_text, run_git, run_thth
from thth import approval_ledger

REL = "docs/sns/queue/a.md"


def _setup(tmp_path, factory, *, name=None, sub="r1", fm=None, body="## threads\n\nBODY_A\n"):
    fm = {"status": "draft", **({"account": name} if name else {}), **(fm or {})}
    pair = init_git_pair(tmp_path / sub, seed_content=make_queue_text(fm, body=body))
    kwargs = {"name": name} if name else {}
    acct = factory(repo_dir=pair["work"], production=True, quiet_hours=None, min_interval_hours=0, **kwargs)
    return pair, acct


def _push(pair, rel, text, message="add"):
    seed = Path(pair["seed"]) / rel
    run_git(pair["seed"], ["pull", "-q", "--ff-only"])
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(text, encoding="utf-8")
    run_git(pair["seed"], ["add", rel])
    run_git(pair["seed"], ["commit", "-q", "-m", message])
    run_git(pair["seed"], ["push", "-q"])
    run_git(pair["work"], ["pull", "-q", "--ff-only"])
    return Path(pair["work"]) / rel


def _digest_lines(stdout):
    return [line.split(": ", 1)[1].strip() for line in stdout.splitlines()
            if line.startswith(("digest: ", "束の digest: "))]


# ---------------------------------------------------------------- (1) --digest-only

def test_digest_only_prints_one_line_and_confirms(tmp_path, isolated_account_factory):
    pair, acct = _setup(tmp_path, isolated_account_factory)
    path = str(Path(pair["work"]) / REL)
    full = run_thth(["approve", path])
    assert full.returncode == 1
    only = run_thth(["approve", path, "--digest-only"])
    assert only.returncode == 0, only.stderr
    assert only.stdout.splitlines() == [_digest_lines(full.stdout)[-1]]
    assert "BODY_A" not in only.stdout and "BODY_A" not in only.stderr
    done = run_thth(["approve", path, "--confirm", only.stdout.strip(), "--by", "テスト"])
    assert done.returncode == 0, done.stderr
    assert approval_ledger.load(acct["name"])[REL]["state"] == "approved"


def test_digest_only_bundles_many_files_into_one_digest(tmp_path, isolated_account_factory):
    pair, _acct = _setup(tmp_path, isolated_account_factory)
    second = _push(pair, "docs/sns/queue/b.md", make_queue_text({"status": "draft"}, body="## threads\n\nBODY_B\n"))
    first = str(Path(pair["work"]) / REL)
    full = run_thth(["approve", first, str(second)])
    only = run_thth(["approve", first, str(second), "--digest-only"])
    assert only.returncode == 0, only.stderr
    assert len(only.stdout.splitlines()) == 1
    assert only.stdout.strip() == _digest_lines(full.stdout)[-1]   # 束の digest


def test_digest_only_lint_error_goes_to_stderr_and_rc1(tmp_path, isolated_account_factory):
    pair, _acct = _setup(tmp_path, isolated_account_factory, body="## threads\n\n#tag1 #tag2 本文\n")
    bad = run_thth(["approve", str(Path(pair["work"]) / REL), "--digest-only"])
    assert bad.returncode == 1
    assert bad.stdout == ""
    assert "lint に通りません" in bad.stderr


def test_digest_only_json_has_no_body(tmp_path, isolated_account_factory):
    pair, _acct = _setup(tmp_path, isolated_account_factory)
    only = run_thth(["approve", str(Path(pair["work"]) / REL), "--digest-only", "--json"])
    assert only.returncode == 0, only.stderr
    data = json.loads(only.stdout)
    assert data["digest_only"] is True and data["ok"] is True and len(data["bundle_digest"]) == 12
    assert "BODY_A" not in only.stdout
    assert all("text" not in row and "segments" not in row for row in data["files"])


def test_digest_only_refuses_with_confirm(tmp_path, isolated_account_factory):
    pair, _acct = _setup(tmp_path, isolated_account_factory)
    both = run_thth(["approve", str(Path(pair["work"]) / REL), "--digest-only", "--confirm", "0" * 12])
    assert both.returncode == 2
    assert "--digest-only" in both.stderr


# ---------------------------------------------------------------- (2) revoke の複数

def _three(tmp_path, factory):
    """承認済み 2 本（a・b）と下書き 1 本（c）。"""
    pair, acct = _setup(tmp_path, factory)
    b = _push(pair, "docs/sns/queue/b.md", make_queue_text({"status": "draft"}, body="## threads\n\nBODY_B\n"))
    c = _push(pair, "docs/sns/queue/c.md", make_queue_text({"status": "draft"}, body="## threads\n\nBODY_C\n"))
    a = Path(pair["work"]) / REL
    for path in (a, b):
        assert approve_via_cli(str(path)).returncode == 0
    return pair, acct, a, b, c


def test_revoke_many_refuses_only_the_one_that_cannot(tmp_path, isolated_account_factory):
    _pair, acct, a, b, c = _three(tmp_path, isolated_account_factory)
    # 断る本（c・下書き）を先頭に置く: 1 本目の断りで残りを止めない。
    result = run_thth(["revoke", str(c), str(a), str(b), "--by", "テスト", "--json"])
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert [Path(r["file"]).name for r in data["revoked"]] == ["a.md", "b.md"]
    assert [Path(r["file"]).name for r in data["refused"]] == ["c.md"]
    assert "承認されていません" in data["refused"][0]["reason"]
    ledger = approval_ledger.load(acct["name"])
    assert ledger[REL]["state"] == ledger["docs/sns/queue/b.md"]["state"] == "revoked"


def test_revoke_directory_picks_only_approved(tmp_path, isolated_account_factory):
    pair, acct, _a, _b, _c = _three(tmp_path, isolated_account_factory)
    result = run_thth(["revoke", str(Path(pair["work"]) / "docs/sns/queue"), "--by", "テスト"])
    assert result.returncode == 0, result.stderr
    assert "取り消し 2 本・断った 0 本" in result.stdout
    assert approval_ledger.load(acct["name"])["docs/sns/queue/b.md"]["state"] == "revoked"


def test_revoke_paths_from_stdin_only_with_dash(tmp_path, isolated_account_factory):
    _pair, acct, a, b, _c = _three(tmp_path, isolated_account_factory)
    result = run_thth(["revoke", "-", "--by", "テスト"], stdin=f"{a}\n\n# 注釈\n{b}\n")
    assert result.returncode == 0, result.stderr
    assert "取り消し 2 本" in result.stdout
    assert approval_ledger.load(acct["name"])[REL]["state"] == "revoked"


def test_revoke_syncs_and_locks_once_per_repo(tmp_path, isolated_account_factory, monkeypatch, capsys):
    import argparse
    from thth import cli as cli_mod
    _pair, _acct, a, b, _c = _three(tmp_path, isolated_account_factory)
    calls = {"sync": 0, "lock": 0}
    real_sync, real_acquire = cli_mod.writeback_mod.sync_repo, cli_mod.lock_mod.acquire

    def sync(repo):
        calls["sync"] += 1
        return real_sync(repo)

    def acquire(lock, seconds=0):
        calls["lock"] += 1
        return real_acquire(lock, seconds)
    monkeypatch.setattr(cli_mod.writeback_mod, "sync_repo", sync)
    monkeypatch.setattr(cli_mod.lock_mod, "acquire", acquire)
    rc = cli_mod.cmd_revoke(argparse.Namespace(file=[str(a), str(b)], reason=None, by="テスト", json=False,
                                               account=None, wait=0))
    capsys.readouterr()
    assert rc == 0
    # 取り消しの前の同期 1 回と、書いたあとの見直しの 1 回（1 本ずつではない）。
    assert calls == {"sync": 2, "lock": 1}


def test_revoke_account_resolves_same_relative_path_in_two_repos(tmp_path, isolated_account_factory):
    first, acct1 = _setup(tmp_path, isolated_account_factory, sub="r1")
    second, acct2 = _setup(tmp_path, isolated_account_factory, name="other-threads", sub="r2")
    for pair in (first, second):
        assert approve_via_cli(str(Path(pair["work"]) / REL)).returncode == 0
    ambiguous = run_thth(["revoke", REL, "--by", "テスト"], cwd=str(tmp_path))
    assert ambiguous.returncode == 2
    assert "複数の repo に同じパス" in ambiguous.stderr and "--account" in ambiguous.stderr
    chosen = run_thth(["revoke", REL, "--account", "other-threads", "--by", "テスト"], cwd=str(tmp_path))
    assert chosen.returncode == 0, chosen.stderr
    assert approval_ledger.load(acct2["name"])[REL]["state"] == "revoked"
    assert approval_ledger.load(acct1["name"])[REL]["state"] == "approved"


def test_approve_account_also_resolves_same_relative_path(tmp_path, isolated_account_factory):
    _setup(tmp_path, isolated_account_factory, sub="r1")
    _setup(tmp_path, isolated_account_factory, name="other-threads", sub="r2")
    first = run_thth(["approve", REL, "--account", "other-threads", "--digest-only"], cwd=str(tmp_path))
    assert first.returncode == 0, first.stderr
    assert "r2" in first.stderr   # 解いた先は other-threads の repo


# ---------------------------------------------------------------- (3) ラッパの標準入力

WRAPPER = Path(__file__).resolve().parents[1] / "bin" / "thth-remote"


def _wrapper_env(tmp_path):
    """偽の ssh: `-n` が無ければ標準入力を読み切る（本物の ssh と同じく VM へ送る形）。呼ばれた形を書く。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ssh.log"
    ssh = bin_dir / "ssh"
    ssh.write_text('#!/bin/sh\n'
                   'if [ "$1" = "-n" ]; then printf "n\\n" >> "' + str(log) + '"; '
                   'else cat > /dev/null; printf "stdin\\n" >> "' + str(log) + '"; fi\n')
    ssh.chmod(0o755)
    wrapper = tmp_path / "thth"
    wrapper.write_text(WRAPPER.read_text())
    wrapper.chmod(0o755)
    return wrapper, {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}, log


def test_wrapper_does_not_eat_loop_stdin(tmp_path):
    wrapper, env, log = _wrapper_env(tmp_path)
    script = f'while read f; do /bin/bash "{wrapper}" revoke "$f" --by t; done'
    subprocess.run(["/bin/bash", "-c", script], input="a.md\nb.md\nc.md\n", env=env, text=True, timeout=30,
                   check=True)
    assert log.read_text().splitlines() == ["n", "n", "n"]


def test_wrapper_forwards_stdin_only_when_asked(tmp_path):
    wrapper, env, log = _wrapper_env(tmp_path)
    cases = [
        (["revoke", "-", "--by", "t"], "stdin"),
        (["approve", "--confirm-file", "-", "--by", "t"], "stdin"),
        (["token", "set", "acct", "--stdin"], None),          # tty を割り当てる道（-t）は変えない
        (["topics", "observe", "--json-stdin"], "stdin"),
        (["send", "acct"], "stdin"),                           # 4.4.2 で案内した `send … < <ファイル>`
        (["send", "acct", "--text-file", "-"], "stdin"),
        (["send", "acct", "--text-file", "x.txt"], "n"),
        (["send", "acct", "--text", "hi"], "n"),
        (["approve", "docs/sns/queue/a.md"], "n"),
    ]
    for argv, expected in cases:
        if log.exists():
            log.unlink()
        subprocess.run(["/bin/bash", str(wrapper), *argv], input="x\n", env=env, text=True, timeout=30,
                       check=True)
        if expected is None:
            continue
        assert log.read_text().splitlines() == [expected], argv


# ---------------------------------------------------------------- (4) 本文の差し替え

def test_reapprove_after_edit_without_revoke(tmp_path, isolated_account_factory):
    pair, acct = _setup(tmp_path, isolated_account_factory)
    path = Path(pair["work"]) / REL
    assert approve_via_cli(str(path)).returncode == 0
    old_sha = approval_ledger.load(acct["name"])[REL]["approved_sha"]
    _push(pair, REL, (Path(pair["seed"]) / REL).read_text().replace("BODY_A", "BODY_B"), "edit")
    first = run_thth(["approve", str(path)])
    assert first.returncode == 1
    assert "差し替わります" in first.stdout and "revoke は要りません" in first.stdout
    assert old_sha[:12] in first.stdout
    as_json = json.loads(run_thth(["approve", str(path), "--json"]).stdout)
    assert as_json["files"][0]["replaces"]["approved_sha"] == old_sha[:12]
    again = approve_via_cli(str(path))
    assert again.returncode == 0, again.stderr
    entry = approval_ledger.load(acct["name"])[REL]
    assert entry["state"] == "approved" and entry["approved_sha"] != old_sha


def test_first_approval_has_no_replaces_line(tmp_path, isolated_account_factory):
    pair, _acct = _setup(tmp_path, isolated_account_factory)
    first = run_thth(["approve", str(Path(pair["work"]) / REL)])
    assert "差し替わります" not in first.stdout and "承認済みです" not in first.stdout
