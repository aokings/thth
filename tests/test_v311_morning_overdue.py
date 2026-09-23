"""3.1.1 件 6: 毎朝の一枚の「時刻超過」に名前を出す。

数（`queue.overdue`）だけでなく、どの原稿が何時間超過しているかを予定の段に並べ、
「次の一手」に `kind: "overdue"` の候補を足す（本文は作らない・先頭 60 字も載せない）。
"""
from __future__ import annotations

from tests.conftest import write_queue_file
from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one, sections  # noqa: F401
from thth import morning


def overdue(one, name, publish_at, status="approved"):
    write_queue_file(one["queue_dir"], name,
                     fm_overrides={"account": ACCOUNT, "status": status, "publish_at": publish_at})


def today_value(payload):
    return sections(payload)["today"]["value"]["by_account"][ACCOUNT]["value"]


def test_overdue_draft_is_named_in_cell_steps_and_text(one):
    overdue(one, "late.md", "2026-09-23T05:00:00+09:00")          # 2h 超過
    overdue(one, "soon.md", "2026-09-23T06:30:00+09:00")          # 0.5h（超過ではない）
    overdue(one, "draft.md", "2026-09-23T04:00:00+09:00", "draft")  # 承認前は超過に数えない
    payload = morning.build(PROJECT, now=NOW, mark=False)
    cell = today_value(payload)["overdue_items"]
    assert cell["cannot_say"] is None
    value = cell["value"]
    assert value["n"] == 1 and value["limit"] == 10
    [row] = value["items"]
    assert row["file"] == "late.md" and row["status"] == "approved"
    assert row["elapsed_hours"] == 2.0 and row["publish_at"].startswith("2026-09-23T05:00")
    assert len(row["head"]) <= morning.PREVIEW_CHARS
    steps = [s for s in sections(payload)["next_steps"]["value"]["steps"] if s["kind"] == "overdue"]
    assert steps == [{"kind": "overdue", "account": ACCOUNT, "file": "late.md",
                      "publish_at": row["publish_at"], "elapsed_hours": 2.0}]
    lines = []
    morning.render(payload, out=lines.append)
    assert any(f"時刻超過: late.md {row['publish_at']}（2.0h）" in line for line in lines)
    assert any("超過" in line and "late.md" in line and line.strip().startswith("超過") for line in lines)


def test_at_most_ten_names_with_the_full_count(one):
    for i in range(12):
        overdue(one, f"late{i:02d}.md", f"2026-09-22T{i:02d}:00:00+09:00")
    payload = morning.build(PROJECT, now=NOW, mark=False)
    value = today_value(payload)["overdue_items"]["value"]
    assert value["n"] == 12 and len(value["items"]) == 10
    assert [r["file"] for r in value["items"]] == [f"late{i:02d}.md" for i in range(10)]
    lines = []
    morning.render(payload, out=lines.append)
    assert any("ほか 2 本（全 12 本）" in line for line in lines)


def test_no_overdue_is_zero_not_missing(one):
    value = today_value(morning.build(PROJECT, now=NOW, mark=False))["overdue_items"]["value"]
    assert value["n"] == 0 and value["items"] == []
