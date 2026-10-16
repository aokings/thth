"""4.0 S2 §9: S1 監査 §15 の 3・7 の縛り（T-S2-9a〜9d）。

見るのは:
  - 9a: inflight だけ書けて台帳に行が無い（台帳 `seq + 2 == inflight.seq`・状態 `wanted`）から
    `resolve --not-published` → 台帳は触らず inflight だけ片づく。`resolve --published` は `seq_mismatch`。
  - 9b: 手元の `thth send`（返信も）・`thth retract` をはてなの口座に打つと `unsupported_operation`。
  - 9c: `sent/` を書いたあと台帳の `published` の行が落ちた → inflight が残る → `resolve --published
    <entry_id>` で `sent/` を二重に書かず、台帳に同じ seq で `published`。
  - 9d: 読めない台帳（JSON を壊す）を指す承認済みの原稿は `approval_stale`・timer は何も書かない。
"""
from __future__ import annotations

import pytest

from tests.conftest import parse_verified
from tests.hatena_s2_helpers import (ACCOUNT, FakeAdapter, approve, call, fm_of, lane, s2lane,  # noqa: F401
                                     states, throw, world)
from thth import accounts, deliveries, hatena_entry, hatena_publish, inflight, sent


def resolve(*flags):
    rc, out, err = call(["inflight", ACCOUNT, "resolve", *flags, "--by", "tester"])
    digests = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")]
    if not digests:
        return rc, out, err
    return call(["inflight", ACCOUNT, "resolve", *flags, "--by", "tester", "--confirm", digests[0]])


class Crash(Exception):
    pass


def _crash_on(monkeypatch, state):
    """`deliveries.advance` が `state` の行を書くところで 1 度だけ落ちる。"""
    real = deliveries.advance
    fired = []

    def advance(account, delivery, *, state: str, **kwargs):
        if state == target and not fired:
            fired.append(state)
            raise Crash(state)
        return real(account, delivery, state=state, **kwargs)
    target = state
    monkeypatch.setattr(deliveries, "advance", advance)
    return fired


# --------------------------------------------------------------------------
# 9a
# --------------------------------------------------------------------------

def test_T_S2_9a_inflightだけ書けて台帳に行が無い状態から出ていないで解く(s2lane, monkeypatch):
    """柵: resolve の never_sent の枝を消すと赤。"""
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    adapter = FakeAdapter()
    with monkeypatch.context() as patched:
        _crash_on(patched, "inflight")
        with pytest.raises(Crash):
            throw(adapter)
    assert adapter.calls == []          # 送る前に落ちた
    record = deliveries.read(ACCOUNT, delivery)
    held = inflight.read(s2lane.state)
    assert record["state"] == "wanted" and record["seq"] + 2 == held["seq"] == 3
    # 出ていた、とは言えない（何も送っていない）
    rc, out, err = resolve("--published", "tag:blog.hatena.ne.jp,2013:blog-x-1-1")
    assert rc == 1 and "seq_mismatch" in err
    assert inflight.read(s2lane.state) is not None
    # 出ていない、で解く: 台帳は触らず inflight だけ片づく
    rc, out, err = resolve("--not-published")
    assert rc == 0, (out, err)
    assert inflight.read(s2lane.state) is None
    assert deliveries.read(ACCOUNT, delivery) == record
    # 次の run は出す
    adapter = FakeAdapter()
    assert throw(adapter).action == "post" and adapter.calls == [("create", "アラビカの値動き")]
    assert states(delivery) == ["wanted", "inflight", "published"]


# --------------------------------------------------------------------------
# 9b
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["send", ACCOUNT, "--text", "こんにちは"],
    ["send", ACCOUNT, "--text", "こんにちは", "--production"],
    ["send", ACCOUNT, "--text", "返信です", "--reply-to", "tag:blog.hatena.ne.jp,2013:blog-x-1-1"],
    ["retract", ACCOUNT, "tag:blog.hatena.ne.jp,2013:blog-x-1-1", "--reason", "試験", "--by", "tester"],
])
def test_T_S2_9b_手元のsend_返信_retractはunsupported_operation(s2lane, argv):
    """柵: send_once・retract の断りを消すと赤。"""
    rc, out, err = call(argv)
    assert rc == 2, (out, err)
    assert "unsupported_operation" in out + err
    assert inflight.read(s2lane.state) is None and sent.records(s2lane.state) == []


# --------------------------------------------------------------------------
# 9c
# --------------------------------------------------------------------------

def test_T_S2_9c_sentのあと台帳が落ちてもresolveで二重に書かず同じseqでpublished(s2lane, monkeypatch):
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    adapter = FakeAdapter()
    with monkeypatch.context() as patched:
        _crash_on(patched, "published")
        with pytest.raises(Crash):
            throw(adapter)
    assert adapter.calls == [("create", "アラビカの値動き")]
    (row,) = sent.records(s2lane.state)
    assert row["post_id"] == adapter.entry_id and row["delivery"] == delivery
    held = inflight.read(s2lane.state)
    assert held["seq"] == 3 and states(delivery) == ["wanted", "inflight"]
    # 次の run は出し直さない
    again = FakeAdapter()
    assert throw(again).action == "inflight" and again.calls == []
    rc, out, err = resolve("--published", adapter.entry_id)
    assert rc == 0, (out, err)
    assert len(sent.records(s2lane.state)) == 1
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published" and record["seq"] == held["seq"] == 3
    assert record["history"][-1]["resolution"] == "human_published"
    assert inflight.read(s2lane.state) is None and fm_of(path)["status"] == "posted"


# --------------------------------------------------------------------------
# 9d
# --------------------------------------------------------------------------

def test_T_S2_9d_読めない台帳を指す原稿はapproval_staleでtimerは何も書かない(s2lane):
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    ledger_path = deliveries.path_for(ACCOUNT, delivery)
    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write("{ 壊れた")
    cfg = accounts.load_account(ACCOUNT)
    assert hatena_entry.select_problem(parse_verified(path), cfg, ACCOUNT)[0] == "approval_stale"
    adapter = FakeAdapter()
    result = throw(adapter)
    assert adapter.calls == [] and {"file": "a.md", "reason": "approval_stale"} in result.rejections
    assert inflight.read(s2lane.state) is None and sent.records(s2lane.state) == []
    with open(ledger_path, encoding="utf-8") as f:
        assert f.read() == "{ 壊れた"
    assert fm_of(path)["status"] == "approved"
    assert hatena_publish  # noqa: B018
