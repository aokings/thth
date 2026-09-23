"""3.7.0 文書と skill（設計 3.7.0 §A〜§C）。

見るのは:
  - skill と使い方の文書に 3.7.0 の段（click を一意のリンク先で・確定待ち・手元のパス・where）。
  - リリースノートの題（「— 下書き」の有無は固定しない）と、各 project のセッション向けの 5 行、
    材料の報告 id。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_IDS = ("r20260924-5820d911", "r20260924-32ae3a65", "r20260924-b77b1f09",
              "r20260924-e9ad6256", "r20260924-59ae563e", "r20260924-78b4a598",
              "r20260924-f8944208", "r20260924-4955cedd", "r20260924-17e5d890",
              "r20260924-30ead3ee")


def test_skillに3_7_0の1段落():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    assert "**click はリンク先を投稿ごとに分けると投稿単位で測れる**（3.7.0）" in skill
    section = skill.split("**click はリンク先を投稿ごとに分けると投稿単位で測れる**")[1].split("\n\n")[0]
    for text in ("unique_url_72h", "post_day_plus_2", "url_shared_72h", "no_link", "profile_link",
                 "**本文に札（utm）を付けて書き換えない**", "no_comparison_days", "--weekly-goals",
                 "**確定するまで出ません**", "confirm_due", "run が自分で直すもの", "--also",
                 "zero_or_filtered", "--aggregate", "保存しない"):
        assert text in section, text


def test_使い方の文書に3_7_0の段():
    doc = (ROOT / "docs" / "使い方_プロジェクトのセッション向け_2026-09-09.md").read_text(encoding="utf-8")
    assert "## 3.7.0: 測り方と表示の直し" in doc
    section = doc.split("## 3.7.0: 測り方と表示の直し")[1].split("\n## ")[0]
    for text in ("投稿日（JST）を含む 3 暦日", "url_shared_72h", "profile_links", "--weekly-goals",
                 "approve_pending.json", "confirm_due", "collection_stale_basis",
                 "VM 側は古い", "--also", "zero_or_filtered", "--aggregate", "**保存しません**"):
        assert text in section, text


def test_リリースノートの題とセッション向けの5行と材料の報告id():
    note = (ROOT / "docs" / "リリースノート_3.7.0_2026-09-24.md").read_text(encoding="utf-8")
    assert note.startswith("# リリースノート 3.7.0（測り方と表示の直し）")
    section = note.split("## 各 project のセッション向け")[1].split("\n## ")[0]
    lines = [line for line in section.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(lines) == 5
    assert "url_shared_72h" in lines[0] and "--weekly-goals" in lines[2]
    assert "confirm_due" in lines[3] and "--aggregate" in lines[4]
    for report_id in REPORT_IDS:
        assert report_id in note, report_id
