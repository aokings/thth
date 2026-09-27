"""3.14.3 穴 4・7: 運営者が手で打つ CLI の入口。

- `thth` の入口（`cli.main`）は命令の間 umask 077（返るときに戻す）。
- 符丁だけの ValueError（`managed_git_store_unsafe_mode` 等）は traceback でなく `符丁: 次の一歩: …` の 1 行・rc 2。
  文を持つ ValueError は今までどおり上げる。
- `leave_incomplete` は理由を 1 行で添える（`unsafe_owned_directory` なら path と chmod）。
"""
from __future__ import annotations

import argparse
import json
import os

import pytest

from thth import cli, leave


def test_入口は命令の間umask077で_返るときに戻す(monkeypatch):
    seen = []

    def fake(argv, real_argv):
        current = os.umask(0o022)
        os.umask(current)
        seen.append(current)
        return 0
    monkeypatch.setattr(cli, "_main", fake)
    before = os.umask(0o002)
    try:
        assert cli.main(["doctor"]) == 0
        assert seen == [0o077]
        assert os.umask(0o002) == 0o002
    finally:
        os.umask(before)


def test_符丁のValueErrorは1行と次の一歩でrc2(monkeypatch, capsys):
    def fake(argv, real_argv):
        raise ValueError("managed_git_store_unsafe_mode")
    monkeypatch.setattr(cli, "_main", fake)
    assert cli.main(["collect", "alpha"]) == 2
    err = capsys.readouterr().err
    first = err.splitlines()[0]
    assert first.startswith("managed_git_store_unsafe_mode: 次の一歩: ") and "chmod -R go-w" in first
    assert "Traceback" not in err


def test_知らない符丁にも既定の次の一歩(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_main", lambda argv, real_argv: (_ for _ in ()).throw(ValueError("something_unsafe")))
    assert cli.main(["collect", "alpha"]) == 2
    assert capsys.readouterr().err.startswith("something_unsafe: 次の一歩: ")


def test_文を持つValueErrorは今までどおり上げる(monkeypatch):
    monkeypatch.setattr(cli, "_main", lambda argv, real_argv: (_ for _ in ()).throw(ValueError("not a code: x")))
    with pytest.raises(ValueError):
        cli.main(["collect", "alpha"])


def test_leaveの木で緩いディレクトリはpathを添えて断る(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    loose = root / "repos" / "_server" / "alpha" / "data" / "sns" / "insights"
    loose.mkdir(parents=True, mode=0o700)
    for part in (root / "repos", root / "repos/_server", root / "repos/_server/alpha",
                 root / "repos/_server/alpha/data", root / "repos/_server/alpha/data/sns"):
        part.chmod(0o700)
    loose.chmod(0o775)  # umask 0002 の ssh で作られた形
    with pytest.raises(ValueError) as caught:
        leave._tree(root / "repos" / "_server" / "alpha")
    assert str(caught.value) == "unsafe_owned_directory" and caught.value.path == str(loose)
    detail = leave._incomplete_detail(caught.value)
    assert detail.startswith("unsafe_owned_directory " + str(loose)) and "chmod -R go-w " + str(loose) in detail


def test_leave_incompleteは理由を1行で添える(monkeypatch, capsys):
    def broken(name, **kwargs):
        exc = ValueError("unsafe_owned_directory")
        exc.path = "/srv/thth/repos/_server/alpha/data/sns/insights"
        raise exc
    monkeypatch.setattr(leave, "run", broken)
    args = argparse.Namespace(name="alpha", by="masaru", json=True, plaza_open=None)
    assert leave.command(args) == 2
    out, err = capsys.readouterr()
    (line,) = err.splitlines()
    assert line.startswith("leave_incomplete: unsafe_owned_directory /srv/thth/repos/_server/alpha/data/sns/insights")
    assert "同じ leave を再実行" in line
    payload = json.loads(out)
    assert payload["reason"] == "leave_incomplete" and payload["detail"].startswith("unsafe_owned_directory ")
    monkeypatch.setattr(leave, "run", lambda name, **k: (_ for _ in ()).throw(OSError("disk full")))
    args.json = False
    assert leave.command(args) == 2
    assert capsys.readouterr().err.startswith("leave_incomplete: disk full。")
