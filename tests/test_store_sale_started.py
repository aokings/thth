"""販売が始まった（買えない売り方 → 売っている）を違いに出し、新着と同じように下書きを作る（監査 5.18.0 の 6・5.18.1）。

壊れ方（直す前）: 販売の期間の前（STORES の before_sale・BASE の item_waiting_for_sale）や予約販売（preorder）の
ときに写しに入った商品は、販売が始まって on_sale になっても違いが 0 件で、`thth shop draft` も作れなかった。

柵:
  - 公開のまま `sales_status` が「売っている」の外 → 内になったら `sale_started`。下書きの選び方は `new`
    （`--kind new` で選ぶ・本文は「販売を始めました」）。
  - 同じ商品の在庫の節目（無い → ある）が同時なら `restock` は言わない（販売の開始のほうが正しい言い方）。
  - 前の写しの行に `sales_status` が無い・文字列でない（形の違う古い写し）ときは言わない（最初の 1 回で誤った
    下書きを出さない）。
  - 販売が始まっても在庫が無い（買えない）商品は下書きにしない。承認の読み直しで期間が終わっていれば断る。
  - Shopify は公開の行がいつも `active` なので、この違いは出ない（下書き・アーカイブ → 公開は `published`）。
本物の店は呼ばない。
"""
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from thth import queuefile
from thth.shop import diff, draft, ledger, recheck, rows, stores
from tests import test_store_stores as st
from tests.test_store_stores import env  # noqa: F401 — STORES の偽の店（同じ fixture を使う）

DAY = datetime.timedelta(days=1)


def _kinds(value):
    return [(c["kind"], str(c["id"])) for c in value["changes"]]


def _later(monkeypatch, days):
    monkeypatch.setattr(stores, "NOW", lambda: st.NOW + days * DAY)


def test_STORESで販売の期間が始まれば違いが出て新着として下書きを作れる(capsys, env, monkeypatch,
                                                                 isolated_account_factory):
    account = isolated_account_factory()
    soon = st.item(60, period={"period_start_at": (st.NOW + DAY).isoformat(), "period_end_at": None})
    env["items"] = [soon]
    st.setup_store(capsys, env)
    assert st.run(capsys, "shop", "diff", st.STORE)[0] == 0          # 基準（販売の前）
    _later(monkeypatch, 2)
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert rc_ == 0 and _kinds(value) == [("sale_started", soon["id"])]
    rc_, value, err = st.jrun(capsys, "shop", "draft", st.STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    [path] = sorted(pathlib.Path(account["queue_dir"]).glob("*-store-*.md"))
    q = queuefile.parse(str(path))
    assert q.front_matter["from_store_change"] == "sale_started"
    assert "販売を始めました" in path.read_text()
    assert recheck.check(str(path), q.front_matter) == (None, None)
    # 承認の前に販売の期間が終わっていれば、今までどおり断る。
    env["items"] = [st.item(60, period={"period_start_at": None, "period_end_at": (st.NOW - DAY).isoformat()})]
    reason, _ = recheck.check(str(path), q.front_matter)
    assert reason and "store_item_changed" in reason and "not_buyable" in reason


def test_STORESで予約販売が終わって普通の販売になれば販売の開始(capsys, env):
    pre = st.item(61, preorder={"shipping_start_at": "2027-01-01"})
    env["items"] = [pre]
    st.setup_store(capsys, env)
    assert st.run(capsys, "shop", "diff", st.STORE)[0] == 0
    env["items"] = [st.item(61)]
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert _kinds(value) == [("sale_started", pre["id"])]
    _, out, _ = st.run(capsys, "shop", "diff", st.STORE)
    assert "販売が始まりました" in out


def test_販売が始まっても在庫が無ければ下書きにしない(capsys, env, monkeypatch, isolated_account_factory):
    account = isolated_account_factory()
    soon = st.item(62, quantities=(0, 0), period={"period_start_at": (st.NOW + DAY).isoformat(),
                                                   "period_end_at": None})
    env["items"] = [soon]
    st.setup_store(capsys, env)
    assert st.run(capsys, "shop", "diff", st.STORE)[0] == 0
    _later(monkeypatch, 2)
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert _kinds(value) == [("sale_started", soon["id"])]
    rc_, value, _ = st.jrun(capsys, "shop", "draft", st.STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_draft_nothing"


def test_前の写しの行にsales_statusが無ければ販売の開始を言わない(capsys, env):
    env["items"] = [st.item(63)]
    st.setup_store(capsys, env)
    assert st.run(capsys, "shop", "diff", st.STORE)[0] == 0
    path = pathlib.Path(ledger.snapshot_path(st.STORE))
    snap = json.loads(path.read_text())
    for row in snap["items"].values():
        row.pop("sales_status", None)
    path.write_text(json.dumps(snap))
    rc_, value, _ = st.jrun(capsys, "shop", "diff", st.STORE)
    assert rc_ == 0 and value["changes"] == []


# ------------------------------------------------------------------ 行だけで（BASE・Shopify・在庫の節目）

def _row(i, status, stock=3, **extra):
    row = {"id": i, "name": f"豆 {i}", "price": 1200, "stock": stock, "visible": True, "sales_status": status,
           "image_url": None, "item_url": None, "modified_at": None}
    row.update(extra)
    return row


@pytest.mark.parametrize("before,after,kinds", [
    ("item_waiting_for_sale", "item_now_on_sale", ["sale_started"]),           # BASE の販売の前 → 販売中
    ("item_end_of_sale", "item_now_on_sale", ["sale_started"]),                # BASE の販売の終わり → また販売
    ("item_now_on_sale", "item_end_of_sale", []),                              # 売るのをやめた（下書きは作らない）
    ("item_now_on_sale", "item_now_on_sale", []),
    ("active", "active", []),                                                  # Shopify はいつも active
    ("before_sale", "after_sale", []),                                         # 売っている状態を通らなかった
    (None, "on_sale", []),                                                     # 形の違う古い写し
    (5, "on_sale", []),
])
def test_販売の開始は売っているの外から内(before, after, kinds):
    old = rows.snapshot_row(_row(1, before))
    found = [c["kind"] for c in diff.compare({"1": old}, [_row(1, after)], complete=True)]
    assert found == kinds


def test_在庫の節目と同時なら再入荷でなく販売の開始だけ():
    old = rows.snapshot_row(_row(1, "before_sale", stock=0))
    found = [c["kind"] for c in diff.compare({"1": old}, [_row(1, "on_sale", stock=4)], complete=True)]
    assert found == ["sale_started"]


def test_販売の開始は新着の下書きとして選ぶ():
    change = {"id": 1, "kind": "sale_started", "name": "豆 1"}
    [(picked, _row_)] = draft.choose([change], [_row(1, "on_sale")], kind="new")
    assert picked["draft_kind"] == "new"
    text = draft.text_for("s", "a", "threads", picked, _row(1, "on_sale"), shop_url="https://x.example.invalid",
                          read_at="t", publish_at="t")
    assert "豆 1 販売を始めました" in text and "from_store_change: sale_started" in text
