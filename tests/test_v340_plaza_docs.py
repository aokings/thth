"""施策の広場 第 8 段——文書と skill と board（設計 3.4.0 §5・§7）。

見るのは:
  - skill の description と本文に「施策を試したら広場に置く・他の媒体の施策を読んでから
    次を決める」がある（置くのは作業の一部と明言）。
  - 案内は project 範囲から（skill は open の使い方を案内しない・設計 §7）。
  - observe の limitations と MCP の説明に同じ 1 句。
  - `thth board` は広場を読まない（他の持ち主の書き込みの題が出ない・leak probe）。
"""
from __future__ import annotations

import json
from pathlib import Path

from thth import cli, plaza
from tests.test_v340_plaza_store import owners, post  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def test_skillに広場の1段落():
    skill = (ROOT / "skills" / "thth" / "SKILL.md").read_text(encoding="utf-8")
    head = skill.split("---", 2)[1]
    assert "施策を試したら広場に置き、他の媒体の施策を読んでから次を決める" in head
    assert "**施策を試したら広場に置き、次を決める前に他の媒体の施策を読む**" in skill
    assert "どちらも利用者の作業の一部です" in skill
    for name in ("thth_plaza_post", "thth_plaza_list", "thth_plaza_show", "thth_plaza_reply",
                 "thth_plaza_update", "--scope", "--how", "observed"):
        assert name in skill, name
    # 案内は project 範囲から（open は招待者が 2 組以上になってから）。
    assert "--open" not in skill and "admin plaza join" not in skill


def test_使い方の文書に広場の段():
    doc = (ROOT / "docs" / "使い方_プロジェクトのセッション向け_2026-09-09.md").read_text(encoding="utf-8")
    assert "## 3.4.0: 施策の広場（試したら置く・次を決める前に読む）" in doc
    section = doc.split("## 3.4.0: 施策の広場")[1].split("\n## ")[0]
    assert "--open" not in section


def test_リリースノートの下書き():
    note = (ROOT / "docs" / "リリースノート_3.4.0_2026-09-23.md").read_text(encoding="utf-8")
    assert note.startswith("# リリースノート 3.4.0（施策の広場）— 下書き\n")
    assert "**版は据え置き**" in note
    section = note.split("## 各 project のセッション向け: いつ何を置くか")[1].split("\n## ")[0]
    assert len([line for line in section.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]) == 5


def test_observeとMCPに同じ1句():
    from thth import morning
    from tests.test_mcp import _load_server_module
    assert plaza.WELCOME.startswith("施策を試したら広場に置き、次を決める前に他の媒体の施策を読む")
    server = _load_server_module()
    assert server.PLAZA_WELCOME in plaza.WELCOME
    post_tool = next(tool for tool in server.PLAZA_TOOLS if tool["name"] == "thth_plaza_post")
    assert post_tool["description"].startswith(server.PLAZA_WELCOME)
    source = (ROOT / "thth" / "morning.py").read_text(encoding="utf-8")
    assert "施策を試したら広場へ（thth plaza post・thth_plaza_post）" in source
    assert morning  # import できること


def test_boardは広場を読まない(owners, capsys):
    post(title="BOARD-LEAK-TITLE", account="other-threads", by="o")
    rc = cli.main(["board", "--json"])
    out = capsys.readouterr().out
    assert rc in (0, 2)
    assert "BOARD-LEAK-TITLE" not in out and "_plaza" not in out
    json.loads(out)
