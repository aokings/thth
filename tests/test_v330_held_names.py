"""3.3.0 A2 出られない原稿の名前を出す（morning の held_items・next_steps・board）。

- `thth morning` の予定の段に `held_items`（file・理由・publish_at・経過）を
  `overdue_items` と同じ形で。時刻前の approval_stale も名前は出す（`due: false`）。
- `next_steps` に `kind: "held"`（approval_stale は「再承認」の候補）。本文は作らない。
- `thth board` の要確認は数だけでなく先頭 5 本の名前。
"""
from __future__ import annotations

import json

from tests.conftest import write_queue_file
from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one, sections  # noqa: F401
from thth import cli, morning
from thth import report as report_mod

STALE = {"account": ACCOUNT, "approved_sha": "deadbeef"}


def today_value(payload):
    return sections(payload)["today"]["value"]["by_account"][ACCOUNT]["value"]


def test_morningはheld_itemsに名前と理由と時刻を出す(one):
    write_queue_file(one["queue_dir"], "late.md",
                     fm_overrides={**STALE, "publish_at": "2026-09-23T04:00:00+09:00"})
    write_queue_file(one["queue_dir"], "later.md",
                     fm_overrides={**STALE, "publish_at": "2026-09-24T08:00:00+09:00"})
    write_queue_file(one["queue_dir"], "q.md",
                     fm_overrides={"account": ACCOUNT, "publish_at": "2026-09-24T08:00:00+09:00"},
                     body="## threads\n\n問い。\n")
    write_queue_file(one["queue_dir"], "a.md",
                     fm_overrides={"account": ACCOUNT, "reply_to_file": "q.md",
                                   "publish_at": "2026-09-23T04:00:00+09:00"},
                     body="## threads\n\n答え。\n")
    payload = morning.build(PROJECT, now=NOW, mark=False)
    cell = today_value(payload)["held_items"]
    assert cell["cannot_say"] is None
    value = cell["value"]
    assert value["n"] == 2 and value["n_due"] == 1 and value["limit"] == 10
    by_file = {row["file"]: row for row in value["items"]}
    assert set(by_file) == {"late.md", "later.md"}  # 返信待ち a.md は入らない
    assert by_file["late.md"] == {"file": "late.md", "reason": "approval_stale",
                                  "category": "approval_stale",
                                  "publish_at": "2026-09-23T04:00:00+09:00",
                                  "due": True, "elapsed_hours": 3.0}
    assert by_file["later.md"]["due"] is False and by_file["later.md"]["elapsed_hours"] is None
    # 本文は持たない。
    assert all("head" not in row and "body" not in row for row in value["items"])

    steps = [s for s in sections(payload)["next_steps"]["value"]["steps"] if s["kind"] == "held"]
    assert {s["file"] for s in steps} == {"late.md", "later.md"}
    late = next(s for s in steps if s["file"] == "late.md")
    assert late == {"kind": "held", "account": ACCOUNT, "file": "late.md",
                    "reason": "approval_stale", "publish_at": "2026-09-23T04:00:00+09:00",
                    "due": True, "candidate": "reapprove"}
    # 同じ原稿に「超過」を重ねない。
    assert not any(s["kind"] == "overdue" and s["file"] == "late.md"
                   for s in sections(payload)["next_steps"]["value"]["steps"])

    lines = []
    morning.render(payload, out=lines.append)
    assert any("出られない: late.md（approval_stale" in line and "3.0h 超過" in line
               for line in lines)
    assert any("出られない: later.md" in line and "時刻前" in line for line in lines)
    assert any(line.strip().startswith("再承認") and "late.md" in line for line in lines)


def test_morningはheldが無ければ0と言う(one):
    value = today_value(morning.build(PROJECT, now=NOW, mark=False))["held_items"]["value"]
    assert value["n"] == 0 and value["n_due"] == 0 and value["items"] == []


def test_boardの要確認は先頭5本の名前と残りの本数(isolated_account, capsys):
    for i in range(7):
        write_queue_file(isolated_account["queue_dir"], f"s{i}.md",
                         fm_overrides={"approved_sha": "deadbeef"},
                         body=f"## threads\n\n本文{i}\n")
    assert cli.main(["board"]) == 0
    out = capsys.readouterr().out
    assert "要確認: 7 件（approval_stale 7 件・時刻を過ぎて出られない 7 件）" in out
    for i in range(5):
        assert f"    s{i}.md — approval_stale" in out
    assert "s5.md" not in out and "s6.md" not in out
    assert "ほか 2 件（全 7 件は thth board --json か thth morning nigamilab-threads）" in out

    summary = report_mod.board_summary()
    row = summary["accounts"][0]
    assert len(row["needs_review"]) == 7  # --json は全部のまま
    assert row["held_count"] == 7 and row["held_upcoming_count"] == 0
    assert row["held_reason_code"] == "approved_but_held: approval_stale 7"
    json.dumps(summary)
