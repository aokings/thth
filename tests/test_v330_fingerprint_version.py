"""3.3.0 A3 版上げで承認が壊れるときは、その版が言う（FINGERPRINT_VERSION と golden）。

- 既知の入力で指紋の計算が変わったのに `approval.FINGERPRINT_VERSION` を上げて
  いなければ落ちる（`fingerprint_golden.drift()`）。
- 指紋の版が変わった版へ上がったら、`handoff-report` の `tool` と `thth morning` の
  0 段に「この版で再承認が要る原稿: n 本（account ごと）」、運用通知に 1 回だけ。
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import write_queue_file
from tests.test_v330_held import held_run  # noqa: F401
from thth import approval, fingerprint_golden, handoff_cursor, jst, morning
from thth import healthcheck, incident
from thth import operations_handoff
from thth import tags as tags_mod
from thth import tool_version


def test_いまの指紋はいまの版のgoldenと一致する():
    assert fingerprint_golden.drift() == [], (
        "承認の指紋の計算が変わりました。意図した変更なら approval.FINGERPRINT_VERSION を"
        " 1 上げ、FINGERPRINT_HISTORY に道具の版を足し、thth/fingerprint_golden.py の"
        " GOLDEN に新しい版の行を足してください（既存の承認は approval_stale になります）")


def test_版と履歴とgoldenは揃っている():
    assert max(approval.FINGERPRINT_HISTORY) == approval.FINGERPRINT_VERSION
    assert approval.FINGERPRINT_VERSION in fingerprint_golden.GOLDEN
    for introduced in approval.FINGERPRINT_HISTORY.values():
        assert tool_version.numbers(introduced) is not None
    # 見本は全部 golden に行がある（比べ漏らさない）。
    names = {case["name"] for case in fingerprint_golden.CASES}
    assert names == set(fingerprint_golden.GOLDEN[approval.FINGERPRINT_VERSION])


def test_タグの付け方が変わると検出する(monkeypatch):
    original = tags_mod.prepared

    def changed(media, text, topic, *, hashtags):
        out = original(media, text, topic, hashtags=hashtags)
        return out + "!" if media == "bluesky" and topic else out

    monkeypatch.setattr(tags_mod, "prepared", changed)
    drift = fingerprint_golden.drift()
    assert "bluesky_topic_tag" in drift and "threads_plain" not in drift


def test_版を上げてgoldenを足さなければ落ちる(monkeypatch):
    monkeypatch.setattr(approval, "FINGERPRINT_VERSION", approval.FINGERPRINT_VERSION + 1)
    assert fingerprint_golden.drift() == ["no_golden_for_version"]


def test_道具の版の間に指紋の版が変わったか():
    assert approval.fingerprint_changed_between("2.7.3", "3.3.0") is True
    assert approval.fingerprint_changed_between("2.8.0", "3.3.0") is False
    assert approval.fingerprint_changed_between("3.2.0", "3.3.0") is False
    assert approval.fingerprint_changed_between(None, "3.3.0") is None
    summary = tool_version.summary("2.7.0")
    assert summary["fingerprint_version"] == approval.FINGERPRINT_VERSION
    assert summary["fingerprint_changed_since_last_read"] is True
    assert tool_version.summary()["fingerprint_changed_since_last_read"] is None


def _cursor_at(name, version, now):
    node = operations_handoff.answer(name, now=now)["by_account"][name]
    node["tool"] = {**node["tool"], "version": version}
    handoff_cursor.write(name, node, "テスト", now)


def test_指紋の版が変わった版へ上がるとhandoffとmorningが再承認の本数を言う(isolated_account):
    name = isolated_account["name"]
    now = jst.now_jst()
    write_queue_file(isolated_account["queue_dir"], "stale.md",
                     fm_overrides={"approved_sha": "deadbeef",
                                   "publish_at": "2026-09-10T08:00:00+09:00"})
    write_queue_file(isolated_account["queue_dir"], "ok.md",
                     fm_overrides={"publish_at": "2026-09-10T09:00:00+09:00"},
                     body="## threads\n\n別の本文。\n")
    _cursor_at(name, "2.7.0", now)
    payload = operations_handoff.answer(name, now=now, since_last_read=True)
    needed = payload["by_account"][name]["tool"]["reapproval_required"]
    assert needed == {"n": 1, "fingerprint_version": approval.FINGERPRINT_VERSION,
                      "files": ["stale.md"], "cannot_say": None}
    assert "この版で再承認が要る原稿: 1 本" in operations_handoff.render_markdown(payload)

    tool = {s["section"]: s for s in morning.build(name, now=now, mark=False)["sections"]}["tool"]
    assert tool["value"]["reapproval_required"]["n"] == 1
    assert tool["value"]["reapproval_required"]["by_account"] == {name: 1}
    lines = []
    morning.render(morning.build(name, now=now, mark=False), out=lines.append)
    assert any("この版で再承認が要る原稿: 1 本" in line for line in lines)


def test_指紋の版が変わっていなければ言わない(isolated_account):
    name = isolated_account["name"]
    now = jst.now_jst()
    write_queue_file(isolated_account["queue_dir"], "stale.md",
                     fm_overrides={"approved_sha": "deadbeef"})
    _cursor_at(name, "2.8.0", now)
    payload = operations_handoff.answer(name, now=now, since_last_read=True)
    assert "reapproval_required" not in payload["by_account"][name]["tool"]
    tool = {s["section"]: s for s in morning.build(name, now=now, mark=False)["sections"]}["tool"]
    assert tool["value"]["reapproval_required"] is None


def test_指紋の版が上がったら運用通知に1回だけ流す(held_run):  # noqa: F811
    write_queue_file(held_run.account["queue_dir"], "later.md",
                     fm_overrides={"approved_sha": "deadbeef",
                                   "publish_at": "2026-09-10T08:00:00+09:00"})
    outbox = Path(held_run.state) / incident.STATE_FILE
    assert held_run.run() == 0
    # 初めての run は版を控えるだけ（どこから変わったか言えないので知らせない）。
    assert json.loads(outbox.read_text())["fingerprint_version"] == approval.FINGERPRINT_VERSION
    assert held_run.mails == []
    row = json.loads(outbox.read_text())
    row["fingerprint_version"] = approval.FINGERPRINT_VERSION - 1
    incident._save(outbox, row)
    assert held_run.run() == 0
    assert held_run.mails == [("user@example.org", "notice", "reapproval_required: 1"),
                              ("admin@example.org", "notice", "reapproval_required: 1")]
    assert held_run.run() == 0
    assert len(held_run.mails) == 2  # 1 回だけ
    text = healthcheck.reason_text("reapproval_required: 1")
    assert "再承認が要る原稿: 1 本" in text
