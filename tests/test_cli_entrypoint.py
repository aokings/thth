"""`bin/thth` の入口（2026-09-09 の VM 設置で踏んだ事故）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(REPO, "bin", "thth")


def test_20260909_symlinked_bin_could_not_import_thth():
    """PATH に置くための symlink 越しでも `thth` が起動する。

    `bin/thth` が `abspath(__file__)` で repo の親を求めていたため、
    `~/.local/bin/thth → /srv/thth/app/bin/thth` の symlink 越しに呼ぶと
    `ModuleNotFoundError: No module named 'thth'` になった（VM 設置時に実測）。
    `realpath` を通すことで直る。
    """
    with tempfile.TemporaryDirectory() as d:
        link = os.path.join(d, "thth")
        os.symlink(BIN, link)
        r = subprocess.run([sys.executable, link, "--help"],
                           capture_output=True, text=True, cwd=d)
        assert r.returncode == 0, r.stderr
        assert "ModuleNotFoundError" not in r.stderr
