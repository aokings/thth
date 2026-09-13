"""発注 §0-3・§5 受け入れ 13: 秘密が repo・出力に出ないことを grep で確認する。

対象は thth/・bin/・systemd/・mcp/（テスト自身と fixtures は除く）。`accounts/` も
綴りとしては残してあるが、**2026-09-14 に repo から消えた**（台帳は repo の外・
設計 v2 §3）——万一戻って来たときに見落とさないため、在れば見る形にしてある。
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
            # **在るものだけ渡す。** `accounts/` は 2026-09-14 に repo から消えた
            # （設計 v2 §3）ので、名前を書いたままだと grep が「そんな
            # ディレクトリは無い」を stderr に出して rc=2 で終わる——hits は
            # stdout から作っているので**試験は緑のまま**で、この grep が
            # 何も見ていないことに気づけない。
            *[str(REPO_ROOT / d) for d in TARGET_DIRS if (REPO_ROOT / d).exists()],
        ],
        capture_output=True, text=True,
    )
    hits = [line for line in result.stdout.splitlines() if ".example" not in line]
    assert not hits, "秘密らしき文字列:\n" + "\n".join(hits)
