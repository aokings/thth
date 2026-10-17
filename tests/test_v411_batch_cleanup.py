"""4.1.x（夜の監査で回したもの・設計 上限と連投の柵の末尾）: ブックマークのまとめの片づけと見せ方。

  - 中-3: 終わったまとめ（待ちも照合待ちの結果不明も無い）のファイルを期限から `RETAIN_DAYS` 日のあとに消す。
    待ち・照合待ちのまとめは消さない。状態の口は終わったまとめの札を畳む（`--batch` で名指せば細目）。
  - 低-6: 止まったまとめ（待ちがあるのに timer が止まっている）と照合待ちの結果不明が `thth board`・
    `thth morning` に出る。

HTTP は偽物（`hatena_s4_helpers.Bookmarks`）。はてなには出ない。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import ACCOUNT, bm, bookmark_object, fake_http, world  # noqa: F401
from tests.test_v400_s1_remote import key  # noqa: F401
from tests.test_v410_bookmark_batch import (URL_A1, URL_B1, approve, batch_file, bb, item, later, run,  # noqa: F401
                                            states, write)
from thth import bookmark_batch, jst, principals


def _age(world, days):
    """まとめの期限を `days` 日前にする（ファイルを直に・指紋は変えない）。"""
    b = batch_file(world)
    path = os.path.join(bookmark_batch._dir(ACCOUNT), b["batch"] + ".json")   # noqa: SLF001
    data = json.loads(open(path, encoding="utf-8").read())
    data["expires_at"] = jst.iso(jst.now_jst() - datetime.timedelta(days=days))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


# ---------------------------------------------------------------- 中-3 片づけ

def test_終わったまとめは期限から7日のあとに消す(bb):
    """柵: `prunable` が終わったかを見ないと赤（待ちの札を消す）。期限から日数を見ないと赤（すぐ消える）。"""
    approve(write(bb, [item(URL_A1)]))
    run(bb)
    assert states(bb) == ["placed"]
    path = _age(bb, bookmark_batch.RETAIN_DAYS - 1)
    assert bookmark_batch.prunable(ACCOUNT) == []
    assert bookmark_batch.run_due(ACCOUNT, bb.account(ACCOUNT), log=lambda *_: None) is None
    assert os.path.exists(path)
    _age(bb, bookmark_batch.RETAIN_DAYS + 1)
    logs = []
    assert bookmark_batch.run_due(ACCOUNT, bb.account(ACCOUNT), log=logs.append) is None
    assert not os.path.exists(path) and bookmark_batch.load_batches(ACCOUNT) == []
    assert any("片づけました" in line for line in logs)
    # 付けた記録（sent/bm:）は残る
    rc, out, _err = call(["bookmark", "list", ACCOUNT, "--json"])
    assert rc == 0 and URL_A1 in out


def test_待ちのまとめは期限を過ぎても期限切れにするまで消さない_照合待ちも消さない(bb):
    bb.bookmarks.post = lambda url, form: (502, b"bad gateway")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    run(bb)
    assert states(bb) == ["unknown", "pending"]
    _age(bb, bookmark_batch.RETAIN_DAYS + 1)
    # 待ちの札は期限切れ（expired）だが、結果不明が照合されていないので終わっていない
    assert bookmark_batch.prunable(ACCOUNT) == []
    bb.bookmarks.post = None
    bb.bookmarks.marks[("lane-owner", URL_A1)] = bookmark_object(URL_A1)
    assert call(["bookmark", "reconcile", ACCOUNT, "--by", "t"])[0] == 0
    assert bookmark_batch.prunable(ACCOUNT) == [batch_file(bb)["batch"]]


def test_状態の口は終わったまとめの札を畳み_名指せば細目(bb):
    """柵: 畳まずに全欄を返すと赤（遠くの道の答えが札の数だけ太る）。"""
    approve(write(bb, [item(URL_A1), item("ftp://x/")]))
    run(bb)
    s = bookmark_batch.status(ACCOUNT)
    b = s["batches"][0]
    assert b["folded"] is True and set(b["items"][0]) == set(bookmark_batch.FOLDED_ITEM_KEYS)
    assert b["excluded"][0]["reason"] == "invalid_url"
    full = bookmark_batch.status(ACCOUNT, batch=b["batch"])["batches"][0]
    assert "folded" not in full and "tries" in full["items"][0]
    rc, out, _err = call(["bookmark", "batch-status", ACCOUNT])
    assert rc == 0 and "畳んでいます" in out


def test_待ちのあるまとめは畳まない(bb):
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    b = bookmark_batch.status(ACCOUNT)["batches"][0]
    assert "folded" not in b and "not_before" in b["items"][0]


# ---------------------------------------------------------------- 低-6 板と朝

def test_止まったまとめは板と朝に出る(bb):
    """柵: `attention` が pace の blocked を見ないと赤（止まっていても板が黙る）。"""
    from thth import morning, report
    approve(write(bb, [item(URL_A1)]))
    assert bookmark_batch.attention(ACCOUNT) is None
    principals.stop("hatena-main", "owner", actor="tester")
    out, _ = run(bb)
    assert out["status"] == "blocked"
    value = bookmark_batch.attention(ACCOUNT)
    assert value["blocked"]["code"] == "principal_stopped" and value["pending"] == 1
    row = next(r for r in report.board_summary()["accounts"] if r["account"] == ACCOUNT)
    assert row["bookmark_batch"]["blocked"]["code"] == "principal_stopped"
    rc, text, _err = call(["board"])
    assert rc == 0 and "ブックマークのまとめ: 止まっています（principal_stopped" in text
    payload = morning.build(ACCOUNT, mark=False, no_world=True)
    assert "principal_stopped" in json.dumps(payload, ensure_ascii=False)
    lines = []
    morning.render(payload, out=lines.append)
    assert any("ブックマークのまとめ: 止まっています" in line for line in lines)
    # 戻して付ければ消える
    principals.resume("hatena-main", by="tester")
    run(bb, now=later(30))
    assert states(bb) == ["placed"] and bookmark_batch.attention(ACCOUNT) is None


def test_照合待ちの結果不明は板に出る(bb):
    bb.bookmarks.post = lambda url, form: (502, b"bad gateway")
    approve(write(bb, [item(URL_A1)]))
    run(bb)
    value = bookmark_batch.attention(ACCOUNT)
    assert value["unknown"] == 1 and value["blocked"] is None
    line = bookmark_batch.attention_line(ACCOUNT, value)
    assert f"thth bookmark reconcile {ACCOUNT}" in line
    from thth import report
    row = next(r for r in report.board_summary()["accounts"] if r["account"] == ACCOUNT)
    assert row["bookmark_batch"]["unknown"] == 1
    bb.bookmarks.marks[("lane-owner", URL_A1)] = bookmark_object(URL_A1)
    assert call(["bookmark", "reconcile", ACCOUNT, "--by", "t"])[0] == 0
    assert bookmark_batch.attention(ACCOUNT) is None


def test_SNSの口座の板の行は鍵を足さない(isolated_account_factory):
    from thth import report
    a = isolated_account_factory(media="threads")
    row = next(r for r in report.board_summary()["accounts"] if r["account"] == a["name"])
    assert "bookmark_batch" not in row
