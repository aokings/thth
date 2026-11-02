"""届ける流れの skill（`thth-flow`）の在り処と、出力の先頭に置く 1 行（5.7.0・設計 効果測定と連動 §3 段 1）。

**読めとかやれとか言う前に skill を示す**（masaru 11/03）: `thth scenario`・`thth scenario show`・
`thth dashboard <project> campaign <id>` の端末の出力の 1 行目と、`--json` の `skill` の欄に置く。
形は広告の skill（`thth/ads/guide.py` の `skill_ref`）と同じ。

skill の置き場は repo の `skills/thth-flow/SKILL.md`。wheel には `thth/skills/thth-flow/SKILL.md` として入る
（pyproject の force-include `"skills" = "thth/skills"`）。`thth scenario skill` が場所を、`--print` が全文を出す。
"""
from __future__ import annotations

import os

SKILL_NAME = "thth-flow"
COMMAND = "thth scenario skill --print"
_HERE = os.path.dirname(os.path.abspath(__file__))
# 探す順: wheel（thth/skills/…）→ repo（<repo>/skills/…）。
_CANDIDATES = (os.path.join(_HERE, "skills", SKILL_NAME, "SKILL.md"),
               os.path.join(_HERE, os.pardir, "skills", SKILL_NAME, "SKILL.md"))


def skill_path() -> str | None:
    for path in _CANDIDATES:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def skill_line() -> str:
    """人と LLM に見せる 1 行（**出力の先頭に置く**）。絶対パスは入れない（場所は `thth scenario skill`）。"""
    return (f"流れの型は skill {SKILL_NAME}（`thth scenario skill` で場所、`{COMMAND}` で全文）。"
            "段の名・印（UTM）・各段の命令・広告に進む条件はそこに")


def skill_ref() -> dict:
    """json に載せる skill の欄（名前と入手先）。`path` は置き場の名前だけ（絶対パスを出さない約束）。"""
    return {"name": SKILL_NAME, "path": f"skills/{SKILL_NAME}/SKILL.md", "command": COMMAND,
            "text": skill_line()}


def read_text() -> str | None:
    path = skill_path()
    if path is None:
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()
