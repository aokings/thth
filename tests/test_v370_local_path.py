"""3.7.0 §B4 lint・preview・approve が手元の Mac のパスを受ける。

台帳の `repo_dir` に対応しない絶対パスでも、repo の中の相対パス（`docs/sns/queue/<file>`）に
切り出せれば VM のパスに読み替える。読み替えたことを 1 行言い、VM 側の clone が upstream
より遅れていれば「VM 側は古い（thth pull で取り込む）」と言う。approve は digest が VM の
本文で出ることを明示する。
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import init_git_pair, make_queue_text, run_git
from thth import cli

MAC = "/Users/someone/Developer/kopicha/docs/sns/queue/a.md"


def _account(tmp_path, isolated_account_factory):
    pair = init_git_pair(tmp_path, seed_content=make_queue_text(
        {"status": "draft", "approved_sha": None}))
    account = isolated_account_factory(repo_dir=pair["work"])
    return pair, account


def test_lintは手元のパスをVMのパスに読み替えて1行言う(tmp_path, isolated_account_factory, capsys):
    pair, _account_ = _account(tmp_path, isolated_account_factory)
    rc = cli.main(["lint", MAC, "--json"])
    out = capsys.readouterr()
    vm_path = str(Path(pair["queue_dir"], "a.md").resolve())
    assert rc == 0, out.err
    assert f"手元のパスを VM のパスに読み替えました: {MAC} → {vm_path}" in out.err
    assert "VM 側は古い" not in out.err
    rows = json.loads(out.out)
    assert (rows[0] if isinstance(rows, list) else rows)["file"] == vm_path


def test_VM側のcloneが古ければそう言う(tmp_path, isolated_account_factory, capsys):
    pair, account = _account(tmp_path, isolated_account_factory)
    (Path(pair["seed"]) / "later.txt").write_text("あとから\n")
    run_git(pair["seed"], ["add", "later.txt"])
    run_git(pair["seed"], ["commit", "-m", "あとから"])
    run_git(pair["seed"], ["push", "origin", "main"])
    cli.main(["preview", MAC])
    err = capsys.readouterr().err
    assert "手元のパスを VM のパスに読み替えました" in err
    assert (f"VM 側は古い（upstream より 1 commit 遅れています・thth pull {account['name']}"
            " で取り込めます）——いま見ているのは VM の本文です") in err


def test_approveは読み替えとdigestがVMの本文で出ることを言う(tmp_path, isolated_account_factory, capsys):
    _pair, _account_ = _account(tmp_path, isolated_account_factory)
    rc = cli.main(["approve", MAC])
    out = capsys.readouterr()
    assert rc == 1, out.err   # 1 段目
    assert "手元のパスを VM のパスに読み替えました" in out.err
    assert "承認の digest は VM の本文（同期のあと）で出ます——手元の本文ではありません" in out.err
    assert "digest: " in out.out


def test_切り出せてもVMに無ければ取り込みの案内で断る(tmp_path, isolated_account_factory, capsys):
    _pair, account = _account(tmp_path, isolated_account_factory)
    rc = cli.main(["lint", "/Users/someone/kopicha/docs/sns/queue/new.md"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "VM の repo の docs/sns/queue/new.md に読み替えましたが、VM にありません" in err
    assert f"thth pull {account['name']}" in err


def test_切り出せない手元のパスは従前どおり断る(tmp_path, isolated_account_factory, capsys):
    _account(tmp_path, isolated_account_factory)
    rc = cli.main(["lint", "/Users/someone/elsewhere/a.md"])
    assert rc == 2 and "手元のパス" in capsys.readouterr().err
