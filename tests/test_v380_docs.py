"""3.8.0 文書と skill（設計 3.8.0 §A〜§E）。

見るのは:
  - skill の開始手順に「新しいセッションは最初に生きたコツ集を読む」の 1 行と、3.8.0 の段
    （読む瞬間・owner の範囲は管理者が登録・--from の 3 種・observed は道具の数字だけ・
    置くきっかけ・届いた知らせ）。open と join の案内はしない（3.4.0 の約束のまま）。
  - 使い方の文書の 3.8.0 の段。
  - リリースノートの題（「— 下書き」の有無は固定しない）と、各 project のセッション向けの 5 行、
    材料の報告 id。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_IDS = ("r20260924-5cf3859f", "r20260924-90aafc42", "r20260924-a51eaafa")


def test_skillの開始手順に生きたコツ集の1行():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    body = skill.split("---", 2)[2]
    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    # 開始手順（observe の段）のすぐ次の段落。
    assert paragraphs[0].startswith("**セッションの始めと区切りごとに `thth_observe`")
    assert paragraphs[1].startswith("**新しいセッションは最初に生きたコツ集を読む**（3.8.0）")
    assert "thth plaza digest <project>" in paragraphs[1] and "thth_plaza_digest" in paragraphs[1]


def test_skillに3_8_0の段():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    marker = "**広場は道具が読ませ、置く手間は道具が減らす**（3.8.0・知見共有を回す）"
    assert marker in skill
    section = skill.split(marker)[1].split("\n\n")[0]
    for text in ("1 日 1 件・読んだものは出さない", "**題と id だけ**", "--goal",
                 "thth admin plaza owner set", "あなたが組を作らない", "visibility: owner", "--owner",
                 "--from analytics-report|after", "**observed は道具が付けた数字だけ**",
                 "--from study-report", "--from-doc", "--from-report", "--trial-due",
                 "追試の結果を足す", "広場に置く", "あなたの書き込みに"):
        assert text in section, text
    # open と join は案内しない（3.4.0 §7 の約束・tests/test_v340_plaza_docs.py と同じ）。
    assert "--open" not in skill and "admin plaza join" not in skill


def test_使い方の文書に3_8_0の段():
    doc = (ROOT / "docs" / "使い方_プロジェクトのセッション向け_2026-09-09.md").read_text(encoding="utf-8")
    heading = "## 3.8.0: 知見共有を回す（道具が読ませる・置く手間を減らす・持ち主の組）"
    assert heading in doc
    section = doc.split(heading)[1].split("\n## ")[0]
    for text in ("thth plaza digest <project>", "1 日 1 件・読んだものは出さない", "本文は出ません",
                 "--goal", "thth admin plaza owner set", "--owner", "--from after",
                 "--from study-report", "--from-doc", "--from-report", "**observed は道具が付けた数字だけ**",
                 "--trial-due", "あなたの書き込みに"):
        assert text in section, text
    assert "--open" not in section


def test_リリースノートの題とセッション向けの5行と材料の報告id():
    note = (ROOT / "docs" / "リリースノート_3.8.0_2026-09-24.md").read_text(encoding="utf-8")
    assert note.startswith("# リリースノート 3.8.0（知見共有を回す）")
    section = note.split("## 各 project のセッション向け: いつ置くか・どこで読めるか")[1].split("\n## ")[0]
    lines = [line for line in section.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(lines) == 5
    assert "thth plaza digest" in lines[0] and "--from" in lines[2] and "--owner" in lines[4]
    for report_id in REPORT_IDS:
        assert report_id in note, report_id
