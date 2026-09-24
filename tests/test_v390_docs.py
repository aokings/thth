"""3.9.0 文書と skill（設計 3.9.0 §A〜§C）。

見るのは:
  - skill と使い方の文書に 3.9.0 の段（--per-post-clicks・clicks_before_post・also の当たり率・
    --suggest は語と数だけで保存しない・rate_by_span・more_may_exist）。
  - リリースノートの題（「— 下書き」の有無は固定しない）と、各 project のセッション向けの 5 行、
    材料の報告 id。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_IDS = ("r20260924-8b8d37b5", "r20260924-9d3a6e1a", "r20260924-a1ba1829")


def test_skillに3_9_0の段():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    marker = "**検索の語は道具が数えて候補を出し、クリックは目的に依らず投稿ごとに見る**（3.9.0）"
    assert marker in skill
    section = skill.split(marker)[1].split("\n\n")[0]
    for text in ("--per-post-clicks", "unique_url_72h", "goal の層とは混ぜない", "clicks_before_post",
                 "**窓の前と後は窓の和に足さない**", "--also", "also に合った投稿が 0 件",
                 "--suggest", "**語と数だけで、本文・username・post_id は出ない・保存しない**",
                 "where_to_appear", "rate_by_span", "rough", "more_may_exist"):
        assert text in section, text


def test_使い方の文書に3_9_0の段():
    doc = (ROOT / "docs" / "使い方_プロジェクトのセッション向け_2026-09-09.md").read_text(encoding="utf-8")
    heading = "## 3.9.0: 検索の語選びと、クリック・速さの見え方"
    assert heading in doc
    section = doc.split(heading)[1].split("\n## ")[0]
    for text in ("--per-post-clicks", "unrecorded", "clicks_before_post", "**窓の前と後は窓の和に足しません**",
                 "原因は言えません", "--also", "also に合った投稿が 0 件（直近 n 日）", "--suggest",
                 "その場の人が書いた語", "**語と数だけで、本文・username・post_id・author_key は出ません。"
                 "候補も当たり率も保存しません**", "rate_by_span", "rough: true", "more_may_exist",
                 "一次資料で未確認"):
        assert text in section, text


def test_リリースノートの題とセッション向けの5行と材料の報告id():
    note = (ROOT / "docs" / "リリースノート_3.9.0_2026-09-24.md").read_text(encoding="utf-8")
    assert note.startswith("# リリースノート 3.9.0（検索の語選びと、クリック・速さの見え方）")
    section = note.split("## 各 project のセッション向け")[1].split("\n## ")[0]
    lines = [line for line in section.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(lines) == 5
    assert "--per-post-clicks" in lines[0] and "clicks_before_post" in lines[1]
    assert "--also" in lines[2] and "--suggest" in lines[3] and "rate_by_span" in lines[4]
    for report_id in REPORT_IDS:
        assert report_id in note, report_id
