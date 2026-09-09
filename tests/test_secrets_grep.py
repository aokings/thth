"""発注 §0-3・§5 受け入れ 13: 秘密が repo・出力に出ないことを grep で確認する。

対象は thth/・bin/・accounts/・systemd/・mcp/（テスト自身と fixtures は除く）。
watchtower/tests/test_secrets_grep.py を写した。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SECRET_RE = re.compile(r"(sk-ant-[a-zA-Z0-9_-]{20,}|[a-f0-9]{32,})")

TARGET_DIRS = ["thth", "bin", "accounts", "systemd", "mcp"]
ALLOWED_SUFFIXES = {".pyc"}


def _iter_target_files():
    for d in TARGET_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix not in ALLOWED_SUFFIXES and "__pycache__" not in path.parts:
                yield path


def test_repoの台本と設定に秘密らしき文字列が無い():
    offenders = []
    for path in _iter_target_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in SECRET_RE.finditer(text):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {m.group(0)[:12]}...")
    assert not offenders, "秘密らしき文字列が見つかりました:\n" + "\n".join(offenders)


def test_grepコマンド自体でも確認する():
    pattern = r"(sk-ant-[a-zA-Z0-9_-]{20,}|[a-f0-9]{32,})"
    result = subprocess.run(
        [
            "grep", "-rE", pattern,
            "--include=*.py", "--include=*.sh", "--include=*.json",
            "--include=*.service", "--include=*.timer",
            str(REPO_ROOT / "thth"), str(REPO_ROOT / "bin"),
            str(REPO_ROOT / "accounts"), str(REPO_ROOT / "systemd"),
            str(REPO_ROOT / "mcp"),
        ],
        capture_output=True, text=True,
    )
    hits = [line for line in result.stdout.splitlines() if ".example" not in line]
    assert not hits, "秘密らしき文字列:\n" + "\n".join(hits)
