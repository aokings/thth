"""`thth --version`（設計 v1.0.0・§2 の C1）。

道具の版とプロセスが実際に読み込んだ head を、`thth <サブコマンド>` の外で
1 行にして出す。doctor の「次の一手」（§3・§4・§5）は
`tests/test_doctor_next_step.py` を見よ。
"""
from __future__ import annotations

import re

from tests.conftest import run_thth
from thth import __version__ as pkg_version

VERSION_RE = re.compile(r"^thth \d+\.\d+\.\d+ \((?:[0-9a-f]{7}|head 不明)\)$")


def test_versionの形式():
    result = run_thth(["--version"])
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert VERSION_RE.match(out), f"形式が合わない: {out!r}"
    assert out.startswith(f"thth {pkg_version} ("), out
