"""4.0 S1 §9 の順 10: 出来事の id を配信の台帳から決める（§8）。

見るのは:
  - T-8a: 配信の出来事の `event_id` は `delivery`＋`seq` だけで決まる（時刻を書き換えても同じ・seq で変わる）。
  - T-8b: 記録の前に落ちた（媒体には出た・台帳の結果の行が無い）状態から `thth inflight … resolve
    --published` で解くと、inflight に書いた seq で `published` の行が入り、event_id が「落ちなかった
    場合」と同じ。「出ていない」で解くと元の状態に戻る。
  - T-8c: はてなの `sent/` の行を `published`／`retracted` で二重に出さない。
  - inflight に `delivery`・`seq`（`inflight_open` の ref は配信 id）・主体の止めは `principal_stopped`。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, call, factory_for, fm_of,  # noqa: F401
                                          lane, world)
from thth import core, deliveries, events_cli, hatena_publish, inflight, principals, sent


def throw(adapter):
    return core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter), now=NOW)


def events(target=ACCOUNT):
    return events_cli.build(target, since="7d", now=NOW)["events"]


def published_once(lane):
    path = lane.queue()
    approve(path)
    assert throw(FakeAdapter()).action == "post"
    return path, fm_of(path)["delivery"]


def test_T8a_event_idはdeliveryとseqだけから決まる(lane):
    """柵: event_id に時刻（や種類）を混ぜると赤。"""
    _path, delivery = published_once(lane)
    first = [e for e in events() if e["kind"] == "delivery_published"]
    assert len(first) == 1
    expected = "ev_" + hashlib.sha256(f"thth.delivery/1|{delivery}|3".encode()).hexdigest()[:24]
    assert first[0]["event_id"] == expected == events_cli.delivery_event_id(delivery, 3)
    assert first[0]["ref"] == delivery and first[0]["seq"] == 3
    # 同じ台帳を 2 度読んで同じ
    assert [e["event_id"] for e in events()] == [e["event_id"] for e in events()]
    # 時刻を書き換えても同じ（台帳の history の at だけ変える）
    path = deliveries.path_for(ACCOUNT, delivery)
    with open(path, encoding="utf-8") as f:
        record = json.load(f)
    record["history"][-1]["at"] = "2026-09-09T09:30:00+09:00"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    again = [e for e in events() if e["kind"] == "delivery_published"]
    assert again[0]["event_id"] == expected and again[0]["at"] == "2026-09-09T09:30:00+09:00"
    # seq が違えば違う
    assert events_cli.delivery_event_id(delivery, 4) != expected
    assert events_cli.delivery_event_id("dl_000000000001", 3) != expected


def test_T8c_はてなのsentの行はpublishedで二重に出さない(lane):
    _path, delivery = published_once(lane)
    kinds = [e["kind"] for e in events()]
    assert "published" not in kinds and kinds.count("delivery_published") == 1
    # 削除まで進めても retracted ではなく delivery_deleted
    path = lane.queue("d.md", op="delete", delivery=delivery)
    approve(path)
    assert throw(FakeAdapter()).action == "post"
    kinds = [e["kind"] for e in events()]
    assert "retracted" not in kinds and kinds.count("delivery_deleted") == 1


def test_T8c_SNSのsentの行は今までどおりpublished(lane):
    sent.write(lane.state, post_id="123", text="SNS の本文", body_hash="0" * 64, sent_at="2026-09-09T09:00:00+09:00")
    kinds = [e["kind"] for e in events()]
    assert kinds.count("published") == 1


class CrashAfterSend(Exception):
    pass


def _crash_after_send(lane, monkeypatch):
    """媒体には出たが、結果の行（sent・台帳）を書く前に落ちた状態を作る。"""
    path = lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]

    def crash(*args, **kwargs):
        raise CrashAfterSend()
    adapter = FakeAdapter()
    with monkeypatch.context() as patched:
        patched.setattr(hatena_publish, "_record_outcome", crash)
        with pytest.raises(CrashAfterSend):
            throw(adapter)
    assert adapter.calls == [("create", "アラビカの値動き")]
    return path, delivery, adapter


def test_T8b_落ちたあと人がpublishedで解くとinflightのseqで書き_event_idが同じ(lane, monkeypatch):
    """柵: resolve が seq を振り直すと赤。"""
    path, delivery, adapter = _crash_after_send(lane, monkeypatch)
    held = inflight.read(lane.state)
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "inflight" and record["seq"] == 2
    assert held["delivery"] == delivery and held["seq"] == 3
    # 次の run は止まる（出し直さない）
    again = FakeAdapter()
    assert throw(again).action == "inflight" and again.calls == []
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--published", adapter.entry_id, "--by", "tester"])
    assert rc == 1, (out, err)
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--published", adapter.entry_id, "--by", "tester",
                         "--confirm", digest])
    assert rc == 0, (out, err)
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published" and record["seq"] == held["seq"] == 3
    assert record["history"][-1]["resolution"] == "human_published"
    assert record["entry_id"] == adapter.entry_id
    (row,) = sent.records(lane.state)
    assert row["kind"] == "entry_create" and row["delivery"] == delivery
    assert fm_of(path)["status"] == "posted" and inflight.read(lane.state) is None
    (published,) = [e for e in events() if e["kind"] == "delivery_published"]
    # 落ちずに書けていたとき（published_once の流れ）も published は seq 3 に入る＝同じ id
    assert published["event_id"] == events_cli.delivery_event_id(delivery, 3)


def test_T8b_出ていないで解くと元のwantedに戻り次のrunが出す(lane, monkeypatch):
    path, delivery, _adapter = _crash_after_send(lane, monkeypatch)
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--not-published", "--by", "tester"])
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--not-published", "--by", "tester", "--confirm", digest])
    assert rc == 0, (out, err)
    record = deliveries.read(ACCOUNT, delivery)
    assert [r["state"] for r in record["history"]] == ["wanted", "inflight", "wanted"]
    assert record["seq"] == 3 and inflight.read(lane.state) is None
    adapter = FakeAdapter()
    assert throw(adapter).action == "post" and adapter.calls == [("create", "アラビカの値動き")]


def test_T8b_台帳のseqと合わないinflightは解かない(lane, monkeypatch):
    path, delivery, adapter = _crash_after_send(lane, monkeypatch)
    inflight.update(lane.state, seq=7)
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--published", adapter.entry_id, "--by", "tester"])
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--published", adapter.entry_id, "--by", "tester",
                         "--confirm", digest])
    assert rc == 1 and "seq_mismatch" in err
    assert deliveries.read(ACCOUNT, delivery)["state"] == "inflight"


def test_結果不明のunknownのあと人が解いても番号は1つずつ(lane):
    class Boom(FakeAdapter):
        def create_entry(self, **kwargs):
            raise TimeoutError()
    path = lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    assert throw(Boom()).action == "inflight"
    held = inflight.read(lane.state)
    assert held["seq"] == deliveries.read(ACCOUNT, delivery)["seq"] + 1 == 4
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--published", "tag:x-1", "--by", "tester"])
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    rc, out, err = call(["inflight", ACCOUNT, "resolve", "--published", "tag:x-1", "--by", "tester",
                         "--confirm", digest])
    assert rc == 0, (out, err)
    record = deliveries.read(ACCOUNT, delivery)
    assert [r["state"] for r in record["history"]] == ["wanted", "inflight", "unknown", "published"]
    kinds = [e["kind"] for e in events()]
    assert kinds.count("delivery_unknown") == 1 and kinds.count("delivery_published") == 1


def test_inflight_openのrefは配信idで_showにdeliveryとseq(lane, monkeypatch):
    path, delivery, _adapter = _crash_after_send(lane, monkeypatch)
    (row,) = [e for e in events() if e["kind"] == "inflight_open"]
    assert row["ref"] == delivery and row["ongoing"] is True
    rc, out, err = call(["inflight", ACCOUNT, "show", "--json"])
    info = json.loads(out)["inflight"]
    assert info["delivery"] == delivery and info["seq"] == 3


def test_主体の止めはprincipal_stoppedとしてその主体の口座ごとに出る(lane):
    principals.stop("hatena-main", "owner", now=NOW)
    rows = [e for e in events_cli.build("lane-a", since="7d", now=NOW)["events"] if e["kind"] == "principal_stopped"]
    assert {e["account"] for e in rows} == {"lane-a-hatenablog", "lane-a-shared"}
    assert all(e["ongoing"] and e["ref"] == "hatena-main" for e in rows)
    assert [e for e in events_cli.build("lane-c-hatenablog", since="7d", now=NOW)["events"]
            if e["kind"] == "principal_stopped"] == []


@pytest.mark.parametrize("row", [
    {"seq": 3, "state": "published", "op": "update", "error": "refused"},
    {"seq": 3, "state": "published", "op": "delete", "error": "refused"},
    {"seq": 3, "state": "published", "op": "update", "resolution": "human_not_published"},
])
def test_断られた更新削除の戻しの行は出来事にしない(row):
    """S1 監査 1: 戻しの行（元の published に戻す）を「更新した」「出た」と読ませない。"""
    assert events_cli._delivery_kind(row) is None


def test_人がpublishedで解いた行と普通の公開は出来事になる():
    assert events_cli._delivery_kind({"seq": 2, "state": "published", "op": "create"}) == "delivery_published"
    assert events_cli._delivery_kind({"seq": 4, "state": "published", "op": "update",
                                      "resolution": "human_published"}) == "delivery_updated"
