"""4.2 の監査（段 4・edit・タグ長）の直し。

  - 中-1: 結果不明の直しは reconcile で必ず解ける（はてなに付いていて直したあとの値と違えば not_applied・
    404 も not_applied・消した行は照合から外す）。切られたタグのように前の値とも違う形を含む。
  - 中-2: 別の口座（同じ主体）が持つ行の直し: 案内の口座名は holder・reconcile は主体の全口座を見る・
    まとめの run の止めも主体の全口座を見る。
  - 中-3: まとめの直しの札の結果不明が attention・finished・status に出る。
  - 低-1: 観測の時刻は runs のファイルの月から・run_id の無い行はまとめない・同時の集めで重複しない。
  - 低-2: タグの重複を除く（指紋は重複の無い形と同じ）。

偽物のはてなだけ。媒体には出ない。
"""
from __future__ import annotations

import datetime
import json
import threading

import pytest

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s1_helpers import EXAMPLES, world  # noqa: F401
from tests.hatena_s4_helpers import (ACCOUNT, EXTERNAL, EXTERNAL_2, bm, bookmark, calls, cfg,  # noqa: F401
                                     fake_http)
from thth import approval, bookmark_batch, hatena_bookmark, jst, media_observations, runs, sent

HOLDER = "lane-b-hatenablog"     # 同じ主体 hatena-main・別のレーン


def _placed(account=ACCOUNT, url=EXTERNAL, tags=("旧名",)):
    assert bookmark(account, url=url, comment="メモ", tags=list(tags))["result"] == "confirmed"


def _edit_unknown(world, *extra, account=ACCOUNT, url=EXTERNAL):
    world.bookmarks.post = lambda u, form: (502, b"bad gateway")
    rc, out, _ = call(["bookmark", "edit", account, "--url", url, *extra, "--json"])
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "edit", account, "--url", url, *extra, "--confirm", digest, "--by", "m",
                         "--production"])
    world.bookmarks.post = None
    assert rc == 1, (out, err)
    return err


def _pending(world, account=ACCOUNT, url=EXTERNAL):
    return sent.pending_bookmark_edit(sent.read(world.state(account), sent.bookmark_post_id(url)))


# ---------------------------------------------------------------- 中-1

def test_中1a_はてなの今が直したあととも前とも違えばnot_applied(bm):
    """柵: 「前の値と一致するときだけ not_applied」に戻すと赤（切られたタグで永久に解けない）。"""
    _placed(tags=("とても長い旧名",))
    bm.bookmarks.marks[("lane-owner", EXTERNAL)]["tags"] = ["とても長"]     # はてなが切った形
    _edit_unknown(bm, "--tags", "新名")
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="t", sleep=lambda s: None)
    assert [e["result"] for e in out["edits"]] == ["not_applied"]
    assert _pending(bm) is None
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert row["tags"] == ["とても長い旧名"]                     # 行は前のまま・また直せる


def test_中1b_GETが404ならnot_applied(bm):
    _placed()
    _edit_unknown(bm, "--comment", "新")
    del bm.bookmarks.marks[("lane-owner", EXTERNAL)]
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="t", sleep=lambda s: None)
    assert [e["result"] for e in out["edits"]] == ["not_applied"]


def test_中1c_消した行は照合から外す(bm):
    _placed()
    _edit_unknown(bm, "--comment", "新")
    post_id = sent.bookmark_post_id(EXTERNAL)
    sent.mark_retracted(bm.state(ACCOUNT), post_id, retracted_at=jst.iso(), retracted_by="t", retract_reason="t")
    gets = len(calls(bm, "GET"))
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="t", sleep=lambda s: None)
    assert out["edits"] == [] and len(calls(bm, "GET")) == gets
    assert hatena_bookmark.pending_edit_rows("hatena-main") == []


# ---------------------------------------------------------------- 中-2・中-3

def test_中2_別の口座が持つ行の直し_案内はholder_reconcileは主体の全口座(bm):
    """柵: reconcile が自分の口座の state だけを見ると赤（edits が空のまま）。"""
    _placed(account=HOLDER)
    err = _edit_unknown(bm, "--comment", "新")
    assert f"thth bookmark reconcile {HOLDER}" in err
    assert _pending(bm, account=HOLDER) is not None
    bm.bookmarks.marks[("lane-owner", EXTERNAL)]["comment"] = "新"
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="t", sleep=lambda s: None)
    assert [(e["result"], e["holder"]) for e in out["edits"]] == [("confirmed", HOLDER)]
    assert sent.read(bm.state(HOLDER), sent.bookmark_post_id(EXTERNAL))["text"] == "新"


@pytest.fixture
def bb(bm, monkeypatch):
    monkeypatch.setattr(bookmark_batch, "ACTIVE_HOURS", (0, 24))
    monkeypatch.setattr(bookmark_batch, "RANDOM", lambda: 0.5)
    monkeypatch.setattr(bookmark_batch, "_window", lambda now: (now - datetime.timedelta(hours=1),
                                                                 now + datetime.timedelta(minutes=600)))
    data = bm.account(ACCOUNT)
    data["scheduled"] = True
    bm.write_account(data)
    return bm


def test_中2_中3_まとめの直しの結果不明は止めて_attentionとstatusとfinishedに出る(bb):
    """柵: `_item_result` が直しの札を見ないと赤（attention の unknown が 0・finished が真）。"""
    _placed(account=HOLDER)
    _placed(account=HOLDER, url=EXTERNAL_2)
    path = bb.tmp / "edit.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"op": "edit", "url": EXTERNAL, "comment": "新"}, {"op": "edit", "url": EXTERNAL_2, "comment": "新"}]) + "\n",
        encoding="utf-8")
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, str(path), "--json"])
    digest = json.loads(out)["digest"]
    assert call(["bookmark", "batch", ACCOUNT, str(path), "--confirm", digest, "--by", "m", "--production"])[0] == 0
    bb.bookmarks.post = lambda u, form: (502, b"bad gateway")
    run = lambda m: bookmark_batch.run_due(ACCOUNT, cfg(ACCOUNT), log=lambda line: None,  # noqa: E731
                                           now=jst.now_jst() + datetime.timedelta(minutes=m))
    out = run(0)
    assert out["status"] == "unknown" and out["blocked"]["code"] == "bookmark_unknown"
    bb.bookmarks.post = None
    assert run(60)["status"] == "blocked" and len(calls(bb, "POST")) == 3     # 付けた 2 + 直し 1
    seen = bookmark_batch.attention(ACCOUNT)
    assert seen["unknown"] == 1
    [b] = bookmark_batch.load_batches(ACCOUNT)
    assert not bookmark_batch.finished(ACCOUNT, b)
    status = bookmark_batch.status(ACCOUNT)
    assert status["batches"][0]["items"][0]["result"] == "unknown"
    # 照合のあと次の札へ
    bb.bookmarks.marks[("lane-owner", EXTERNAL)]["comment"] = "新"
    assert call(["bookmark", "reconcile", ACCOUNT, "--by", "t"])[0] == 0
    assert bookmark_batch.attention(ACCOUNT)["unknown"] == 0      # 止めの印（pace）は次の run で消える
    assert run(120)["status"] == "placed"


# ---------------------------------------------------------------- 低-1

def _x_account(world, name="x-obs"):
    data = json.loads((EXAMPLES / "x.json").read_text(encoding="utf-8"))
    data.update({"account": name, "media": "x"})
    world.write_account(data)
    return name


def test_低1_時刻はrunsの月から_run_idの無い行はまとめない_同時でも重複しない(world):
    name = _x_account(world)
    state = world.state(name)
    for month in ("2026-08", jst.month_str(jst.now_jst())):
        for _ in range(2):
            runs.append_run(state, {"account": name, "run_id": None, "mode": "production", "action": "post",
                                    "status": "failed", "error": "x_not_permitted"}, month)
    results = []
    threads = [threading.Thread(target=lambda: results.append(media_observations.collect("x"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = media_observations.read("x")
    assert len(rows) == 4 and sum(r.get("x", 0) for r in results) == 4
    old = [r for r in rows if r["month"] == "2026-08"]
    assert {r["at"] for r in old} == {"2026-08-31T23:59:59+09:00"}
    assert all(r.get("seen_at") for r in rows)


# ---------------------------------------------------------------- 低-2

def test_低2_タグの重複を除く_指紋は重複の無い形と同じ():
    assert hatena_bookmark.parse_tags("a, a,b") == ["a", "b"]
    assert hatena_bookmark.parse_tags(["x", "x"]) == ["x"]
    kw = dict(account="a", principal="p", op="add", url=EXTERNAL, delivery="", comment="c")
    assert (approval.compute_hatena_bookmark_sha(tags=["t", "t", "u"], **kw)
            == approval.compute_hatena_bookmark_sha(tags=["t", "u"], **kw))
