"""`thth preview`（発注 §5 受け入れ 2）。出力が節の本文とバイト単位で一致する。"""
from __future__ import annotations

import os

from tests.conftest import BIN_THTH, FIXTURES_DIR, run_thth
from thth import lint as lint_mod


def test_previewはバイト単位で節の本文と一致する(isolated_account):
    path = os.path.join(FIXTURES_DIR, "umami-bile.md")
    section = lint_mod.preview_file(path)
    with open(path, "rb") as f:
        raw = f.read()
    # 節（`## threads` の次から末尾まで）を素朴に切り出して比較する。
    # 送る本文は前後の空白を落とした文字列（T1 検収 2026-09-09 で確定。末尾改行は
    # 含めない）。
    text = raw.decode("utf-8")
    body_start = text.index("## threads\n") + len("## threads\n")
    expected = text[body_start:].strip()
    assert section.encode("utf-8") == expected.encode("utf-8")
    assert not section.endswith("\n")


def test_cli_previewは前後に何も足さない(isolated_account):
    path = os.path.join(FIXTURES_DIR, "umami-bile.md")
    result = run_thth(["preview", path])
    assert result.returncode == 0
    section = lint_mod.preview_file(path)
    assert result.stdout == section
