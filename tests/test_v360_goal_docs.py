"""3.6.0 文書と skill（設計 3.6.0 §A・§B）。

見るのは:
  - skill と使い方の文書に目的の 1 段落（4 語・本文のメモは読まない・指紋に入らない・
    click と follow は言えない・観察の差）。
  - リリースノートの題（「— 下書き」の有無は固定しない）と、各 project のセッション向けの 5 行。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_skillに目的の1段落():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    assert "**原稿に投稿の目的を 1 つ書く**（3.6.0）" in skill
    section = skill.split("**原稿に投稿の目的を 1 つ書く**")[1].split("\n\n")[0]
    for text in ("goal: reach|click|follow|reply", "goal_invalid", "**本文のメモ（「目的: 誘導」）は道具が読まない**",
                 "**承認の指紋に入らない**", "--by goal", "per_post_clicks_unavailable",
                 "per_post_follows_unavailable", "観察の差（因果ではない）", "thth_report_timeline"):
        assert text in section, text


def test_使い方の文書に目的の段():
    doc = (ROOT / "docs" / "使い方_プロジェクトのセッション向け_2026-09-09.md").read_text(encoding="utf-8")
    assert "## 3.6.0: 投稿の目的（goal）で束ねて比べる・つまずきの年表" in doc
    section = doc.split("## 3.6.0: 投稿の目的")[1].split("\n## ")[0]
    for text in ("goal: click", "--by goal", "thth report timeline", "観察の差（因果ではない）",
                 "本文のメモ「目的: 誘導」は読みません"):
        assert text in section, text


def test_リリースノートの題とセッション向けの5行():
    note = (ROOT / "docs" / "リリースノート_3.6.0_2026-09-24.md").read_text(encoding="utf-8")
    assert note.startswith("# リリースノート 3.6.0（投稿の目的とつまずきの年表）")
    section = note.split("## 各 project のセッション向け")[1].split("\n## ")[0]
    lines = [line for line in section.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(lines) == 5
    assert "goal: reach|click|follow|reply" in lines[0]
    assert "--by goal" in lines[3]
    assert "r20260923-7c297144" in note
