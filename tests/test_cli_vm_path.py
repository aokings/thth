"""VM に無いパスを渡したときの断り方（発注 T8-1・kopicha 続報）。

`~/.local/bin/thth` は Mac から VM へ ssh する薄いラッパで、引数はそのまま
VM 側の `thth` に渡る。**Mac 側の相対パスも絶対パスも VM には無い**ので、
いままでは `FileNotFoundError` の素の traceback が出ていた（`--article
/tmp/x.json` で踏んだのと同じ根）。

ファイルを受け取る全ての引数（`lint`・`approve`・`revoke`・`preview`・
`send --text-file`・`topics suggest` の queue ファイル・`--article`・
`--proposal`・`--input`・`--draft`・`--revised-draft`）が、共通の
`cli._require_vm_path()` を通って rc=2・案内つきで断ることを確かめる。
存在するパスは従来どおり動くことも確かめる（回帰）。
"""
from __future__ import annotations

import os

from tests.conftest import run_thth, write_queue_file

MISSING = "/tmp/thth-t8-does-not-exist-まさかここには無い.md"
MISSING_JSON = "/tmp/thth-t8-does-not-exist-まさかここには無い.json"


def _assert_vm_refusal(result, *, rc=2):
    combined = result.stdout + result.stderr
    assert result.returncode == rc, f"rc={result.returncode}: {combined}"
    assert "VM" in combined, combined
    assert "thth queue" in combined, combined
    assert "Traceback" not in result.stderr, result.stderr


def test_lintにVMに無いパスを渡すとrc2で案内する():
    result = run_thth(["lint", MISSING])
    _assert_vm_refusal(result)


def test_approveにVMに無いパスを渡すとrc2で案内する():
    result = run_thth(["approve", MISSING])
    _assert_vm_refusal(result)


def test_sendのtext_fileにVMに無いパスを渡すとrc2で案内する(isolated_account):
    result = run_thth(["send", isolated_account["name"], "--text-file", MISSING])
    _assert_vm_refusal(result)


def test_topics_suggestのarticleにVMに無いパスを渡すとrc2で案内する(isolated_account):
    queue_path = write_queue_file(
        isolated_account["queue_dir"], "q.md",
        body="## threads\n\nhello https://example.test/x\n",
        fm_overrides={"status": "draft", "account": isolated_account["name"]})
    result = run_thth(["topics", "suggest", queue_path, "--article", MISSING_JSON])
    _assert_vm_refusal(result)


def test_revokeにVMに無いパスを渡すとrc2で案内する():
    result = run_thth(["revoke", MISSING, "--by", "test"])
    _assert_vm_refusal(result)


def test_previewにVMに無いパスを渡すとrc2で案内する():
    result = run_thth(["preview", MISSING])
    _assert_vm_refusal(result)


def test_存在するパスは従来どおり動く(isolated_account):
    """回帰: 存在するパスには案内を出さない（発注書「存在するパスは従来どおり」）。"""
    queue_path = write_queue_file(
        isolated_account["queue_dir"], "ok.md",
        body="## threads\n\nこんにちは https://example.test/x\n",
        fm_overrides={"status": "draft", "account": isolated_account["name"]})
    assert os.path.exists(queue_path)

    result = run_thth(["lint", queue_path])
    assert "VM" not in (result.stdout + result.stderr)
    assert "thth queue" not in (result.stdout + result.stderr)

    result = run_thth(["preview", queue_path])
    assert result.returncode == 0, result.stderr
    assert "こんにちは" in result.stdout
