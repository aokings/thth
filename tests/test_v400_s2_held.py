"""4.0 S2 §8: 承認のあとに記事を直すと止まることをレーンに知らせる（T-S2-8a・8b）。

見るのは:
  - 8a: `thth events` に `delivery_held`（`ongoing`・`ref` が delivery・`reason`）。同じ止まり方の
    あいだは同じ id、再承認して指紋が変われば別の id。台帳の人待ち（conflict など）も同じ型。
    SNS の口座には出ない。
  - 8b: run のログに再承認の命令が 1 行（`thth approve <原稿> --by <名前>`）。
"""
from __future__ import annotations

import hashlib

from tests.hatena_s2_helpers import (ACCOUNT, NOW, FakeAdapter, approve, fm_of, lane, s2lane,  # noqa: F401
                                     throw, world)
from thth import core, deliveries, events_cli, hatena_entry, jst


def held(target=ACCOUNT):
    return [e for e in events_cli.build(target, since="1h", now=NOW)["events"] if e["kind"] == "delivery_held"]


def stale(lane, note="直した"):
    lane.write("articles/a.md", f"# アラビカ\n\n値動きのまとめ（{note}）。\n")


def test_T_S2_8a_承認が古い原稿はdelivery_heldで出て同じ止まり方なら同じid(s2lane):
    """柵: event_id に時刻を混ぜる・held を出さないと赤。"""
    path = s2lane.queue()
    approve(path)
    assert held() == []
    stale(s2lane)
    fm = fm_of(path)
    (event,) = held()
    expected = "ev_" + hashlib.sha256(
        f"thth.held/1|{ACCOUNT}|{fm['delivery']}|approval_stale|{fm['approved_sha']}".encode()).hexdigest()[:24]
    assert event["event_id"] == expected == events_cli.held_event_id(
        ACCOUNT, fm["delivery"], "approval_stale", fm["approved_sha"])
    assert event["ongoing"] is True and event["ref"] == fm["delivery"] and event["reason"] == "approval_stale"
    assert event["account"] == ACCOUNT and event["file"] == "a.md" and event["due"] is True
    assert "thth approve docs/sns/queue/a.md" in event["summary"]
    # 窓の外（--since 1h より前）でも、続いているあいだは出る・何度読んでも同じ id
    later = NOW.replace(hour=23)
    again = [e for e in events_cli.build(ACCOUNT, since="1h", now=later)["events"] if e["kind"] == "delivery_held"]
    assert [e["event_id"] for e in again] == [expected]
    # 再承認すれば消え、また直せば別の id
    approve(path)
    assert held() == []
    stale(s2lane, "もう一度直した")
    (event2,) = held()
    assert event2["event_id"] != expected and fm_of(path)["approved_sha"] != fm["approved_sha"]


def test_T_S2_8a_台帳が人待ちの原稿も同じ型で理由が違う(s2lane):
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    deliveries.advance(ACCOUNT, delivery, state="conflict", at=jst.iso(NOW),
                       row={"op": "create", "reason": "test"})
    (event,) = held()
    assert event["reason"] == "delivery_state_conflict" and event["ref"] == delivery
    assert "conflict" in event["summary"]
    assert event["event_id"] == events_cli.held_event_id(ACCOUNT, delivery, "delivery_state_conflict",
                                                         fm_of(path)["approved_sha"])


def test_T_S2_8a_S2の断りもdelivery_held(s2lane):
    path = s2lane.queue()
    approve(path)
    s2lane.write("articles/a.md", "# アラビカ\n\n未 commit。\n", commit=False)
    (event,) = held()
    assert event["reason"] == "article_not_committed" and "commit" in event["summary"]


def test_T_S2_8a_SNSの口座にはdelivery_heldが出ない(isolated_account):
    from tests.conftest import write_queue_file
    from thth import accounts
    path = write_queue_file(isolated_account["queue_dir"], "a.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n承認のあとに足した行\n")
    cfg = accounts.load_account(isolated_account["name"])
    rows = core.held_items_for_account(isolated_account["name"], cfg, now=jst.now_jst())
    kinds = [e["kind"] for e in events_cli.build(isolated_account["name"], since="7d")["events"]]
    assert "delivery_held" not in kinds
    assert [row["reason"] for row in rows] == ["approval_stale"]   # SNS でも止まっている（が出来事にしない）


# --------------------------------------------------------------------------
# 8b
# --------------------------------------------------------------------------

def test_T_S2_8b_runのログに再承認の命令が1行(s2lane):
    path = s2lane.queue()
    approve(path)
    stale(s2lane)
    logs = []
    adapter = FakeAdapter()
    result = throw(adapter, logs=logs)
    assert adapter.calls == [] and result.action == "none"
    lines = [line for line in logs if "approval_stale" in line]
    assert lines == ["承認が古いので出しません: a.md（approval_stale）。記事か原稿を直した場合は "
                     "thth approve docs/sns/queue/a.md --by <名前> で承認し直してください"]


def test_T_S2_8b_S2の断りもrunのログに次の一手(s2lane):
    path = s2lane.queue()
    approve(path)
    s2lane.write("articles/a.md", "# アラビカ\n\n未 commit。\n", commit=False)
    logs = []
    throw(FakeAdapter(), logs=logs)
    assert any(line.startswith("出しません: a.md（article_not_committed: ") for line in logs), logs


def test_T_S2_8b_ログの文の形():
    assert hatena_entry.refusal_log_line("/x/docs/sns/queue/a.md", "approval_stale").endswith(
        "thth approve /x/docs/sns/queue/a.md --by <名前> で承認し直してください")
    assert hatena_entry.refusal_log_line("/x/a.md", "delivery_state_conflict") == \
        "承認が古いので出しません: a.md（delivery_state_conflict）"
