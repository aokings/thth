"""3.10.0 招待リンクの文書。運営者の手順・招待された人の道・Worker の deploy が要ること。

見出しの「— 下書き」は固定しない（版を上げるときに外す）。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def read(name):
    return (DOCS / name).read_text(encoding="utf-8")


def test_運営者の手順に招待リンクの節_作る_取り消す_資格情報のactorは口座名():
    text = read("運用_招待する側.md")
    section = text.split("## 10. 招待リンクで招く", 1)[1]
    for phrase in ("thth admin invite create --media threads", "thth admin invite revoke <id>",
                   "thth admin invite list", "tty に 1 度だけ", "`actor` は**口座名と同じ綴り**",
                   "invite_already_used", "invite_project_in_owner_group", "invite_stalled",
                   "thth admin approver set <口座名>", "`INVITE_OBJECT`", "`v5-invite`"):
        assert phrase in section, phrase


def test_招待された人の道_secretは1度だけ_口座名と_thth_me():
    text = read("導入_招待されたら.md")
    # 3.12.0 で招待の道は 2 節（始めるまで）に移った。
    section = text.split("## 2. 始めるまで", 1)[1].split("\n## ", 1)[0]
    for phrase in ("Threads で認可する", "最初のタブに戻ります", "**表示は 1 度だけです。**",
                   "`inv-…`", "`thth.me`", "この招待リンクは使えません", "準備が進んでいません"):
        assert phrase in section, phrase


def test_リリースノートはWorkerのdeployが要ると言い_手順は5行():
    text = read("リリースノート_3.10.0_2026-09-25.md")
    # 版を上げる commit で題（「— 下書き」）と deploy の書き方（要る→要った）は変わる。見るのは
    # 3.10.0 の題と、Worker の deploy と migration の名が書かれていること。
    assert text.startswith("# リリースノート 3.10.0（招待リンク")
    assert "Worker（`callback/`）の deploy が要" in text and "`v5-invite`" in text
    steps = text.split("## 招待を作って渡す手順（運営者）", 1)[1].split("\n## ", 1)[0]
    numbered = [line for line in steps.splitlines() if line[:2] in {f"{n}." for n in range(1, 10)}]
    assert len(numbered) == 5
    assert "thth admin invite create --media threads" in steps
