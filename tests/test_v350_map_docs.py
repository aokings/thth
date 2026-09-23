"""観測の地図 第 8 段——文書と skill（設計 3.5.0 §3・§4）。

見るのは:
  - skill と使い方の文書に地図の 1 段落（点と線は管理者が足す・世間の層は既定で無効）。
  - リリースノートの題（「— 下書き」の有無は固定しない・版を上げる commit で外れる）と、
    世間の層は既定で無効・有効にする条件が Meta への申請のあとであること。
  - observe の limitations と MCP の説明に地図の 1 句。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_skillに地図の1段落():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    assert "**話題を選ぶ前に観測の地図を読む**（3.5.0）" in skill
    section = skill.split("**話題を選ぶ前に観測の地図を読む**")[1].split("\n\n")[0]
    for text in ("thth_map_show", "thth map show", "thth admin map node add", "**既定で無効**",
                 "world_layer_disabled", "**あなたが点を足さない**"):
        assert text in section, text


def test_使い方の文書に地図の段():
    doc = (ROOT / "docs" / "使い方_プロジェクトのセッション向け_2026-09-09.md").read_text(encoding="utf-8")
    assert "## 3.5.0: 観測の地図（話題を選ぶ前に読む）" in doc
    section = doc.split("## 3.5.0: 観測の地図")[1].split("\n## ")[0]
    assert "thth map show" in section and "**既定で無効**" in section


def test_リリースノートの題と世間の層の条件():
    note = (ROOT / "docs" / "リリースノート_3.5.0_2026-09-23.md").read_text(encoding="utf-8")
    assert note.startswith("# リリースノート 3.5.0（観測の地図）")
    head = note.split("## 何が変わったか")[0]
    assert "**世間の層は実装したが既定で無効**" in head
    assert "Meta への説明の申請" in head and "のあと" in head
    assert "THTH_MAP_WORLD=1" in note


def test_observeとMCPに地図の1句():
    from tests.test_mcp import _load_server_module
    source = (ROOT / "thth" / "morning.py").read_text(encoding="utf-8")
    assert "地図は人が足した点だけ（thth map show・thth_map_show）" in source
    server = _load_server_module()
    assert server.MAP_SHOW_DESCRIPTION.startswith("話題を選ぶ前に呼ぶ。")
    assert "世間の層は管理者が有効にしたときだけ" in server.MAP_SHOW_DESCRIPTION
