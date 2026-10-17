"""4.2（設計 S4 §21）: THTH が付けたブックマークのコメントとタグを直す口（`thth bookmark edit`・札の `op: edit`）。

見るもの:
  - 1 段目は台帳の今・はてな側の今（自分の主体の GET 1 回）・差分・digest を見せ、POST を打たず何も書かない
  - 直せるのは自分の主体の confirmed の行だけ（手で付けた・unknown・消した・not_found は断る）
  - はてなに付いていなければ打たない（POST は「無ければ追加」——直しの口を新規の追加の口にしない）
  - 日・同じホスト・自分の記事の枠は数えず当てない。連投の窓には数え、当てる
  - 結果不明は打ち直さず attempts[] に残し、reconcile で決める
  - 遠くの道（`bookmark_request` の `op: edit`）・MCP の写し・まとめの札（`op: edit`）

HTTP は偽物（`hatena_s4_helpers.Bookmarks`）。はてなには出ない。秘密の値は偽物だけ。
"""
from __future__ import annotations

import datetime
import json

import pytest

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import (ACCOUNT, EXTERNAL, EXTERNAL_2, bm, bookmark, bookmark_object,  # noqa: F401
                                     calls, cfg, fake_http, published, record_row, rows, world)
from tests.test_v400_s1_remote import key  # noqa: F401
from thth import approval, bookmark_batch, guard, hatena_bookmark, jst, principals, sent


def _placed(world, url=EXTERNAL, comment="旧名のメモ", tags=("旧名",)):
    """THTH で 1 件付けておく（偽物のはてなにも付く）。"""
    done = bookmark(ACCOUNT, url=url, comment=comment, tags=list(tags))
    assert done["result"] == "confirmed"
    return sent.read(world.state(ACCOUNT), sent.bookmark_post_id(url))


def edit_cli(*extra):
    return call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, *extra])


def edit_two_stages(*extra, by="masaru"):
    rc, out, err = edit_cli(*extra, "--json")
    assert rc == 1, (out, err)
    digest = json.loads(out)["digest"]
    return edit_cli(*extra, "--confirm", digest, "--by", by, "--production", "--json")


def _today(world):
    whole = guard.principal_summary("hatena-main", principals.load_principal("hatena-main"))
    marks = guard.bookmark_counts("hatena-main", url=EXTERNAL)
    return whole["bookmarks_today"], marks["same_host_today"]


# ---------------------------------------------------------------- 1 段目

def test_1段目は台帳とはてなの今と差分とdigest_POSTを打たず書かない(bm):
    """柵: 1 段目で POST を打つ・行を書くと赤。"""
    before = _placed(bm)
    posts = len(calls(bm, "POST"))
    rc, out, err = edit_cli("--tags", "新名,一次資料")
    assert rc == 1, (out, err)
    assert "THTH の台帳の今: コメント: 旧名のメモ  タグ: 旧名" in out
    assert "はてな側の今: コメント: 旧名のメモ  タグ: 旧名" in out
    assert "タグ: 旧名 → 一次資料・新名（外す 旧名・足す 一次資料・新名）" in out
    assert "コメント: 変えない" in out and "digest: " in out and "--confirm " in out
    assert len(calls(bm, "POST")) == posts
    assert sent.read(bm.state(ACCOUNT), before["post_id"]) == before


def test_1段目_はてな側が台帳と違えば両方を見せて断らない(bm):
    """きっかけの形: はてなが長いタグを切った（10/17 の 3 件）。直しの口の主な用途なので断らない。"""
    _placed(bm, tags=("長めのタグ",))
    bm.bookmarks.marks[("lane-owner", EXTERNAL)]["tags"] = ["長めの"]
    rc, out, _ = edit_cli("--tags", "短いタグ")
    assert rc == 1
    assert "はてな側が台帳と違います" in out and "タグ: 長めの" in out


# ---------------------------------------------------------------- 2 段目

def test_2段目は1回だけPOSTし_行の中身を直してattemptsに前の値を残す(bm):
    _placed(bm)
    rc, out, err = edit_two_stages("--comment", "新名のメモ", "--tags", "新名")
    assert rc == 0, (out, err)
    done = json.loads(out)
    assert done["status"] == "bookmark_edited" and done["previous_tags"] == ["旧名"]
    posts = [u for m, u in calls(bm, "POST")]
    assert len(posts) == 2      # 付けた 1 回 + 直した 1 回
    assert bm.bookmarks.marks[("lane-owner", EXTERNAL)]["comment"] == "新名のメモ"
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert (row["text"], row["tags"], row["result"]) == ("新名のメモ", ["新名"], "confirmed")
    assert row["body_hash"] == approval.compute_body_hash("新名のメモ") and row["edited_at"]
    [attempt] = row["attempts"]
    assert attempt["op"] == "edit" and attempt["result"] == "confirmed" and attempt["by"] == "masaru"
    assert (attempt["previous_comment"], attempt["previous_tags"]) == ("旧名のメモ", ["旧名"])
    assert "sent_at" not in attempt
    # 省いた方（コメント）は今の値のまま送る
    rc, out, err = edit_two_stages("--tags", "新名,一次資料")
    assert rc == 0, (out, err)
    assert bm.bookmarks.marks[("lane-owner", EXTERNAL)]["comment"] == "新名のメモ"
    assert sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))["tags"] == ["一次資料", "新名"]


def test_手で付けたもの_結果不明_消したもの_not_foundは直さない(bm):
    """柵: `_check_editable` を外すと赤（台帳に confirmed の行が無い URL に POST する）。"""
    bm.bookmarks.marks[("lane-owner", EXTERNAL)] = bookmark_object(EXTERNAL)     # 手で付けた
    rc, out, err = edit_cli("--comment", "x", "--json")
    assert rc == 2 and json.loads(out)["error"] == "bookmark_not_found"
    for result, code in (("unknown", "bookmark_result_unknown"), ("not_found", "bookmark_not_found")):
        record_row(bm, ACCOUNT, EXTERNAL_2 + "?" + result, result=result)
        rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL_2 + "?" + result, "--comment", "x",
                           "--json"])
        assert json.loads(out)["error"] == code
    record_row(bm, ACCOUNT, EXTERNAL_2, retracted=True)
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL_2, "--comment", "x", "--json"])
    assert json.loads(out)["error"] == "bookmark_already_deleted"
    assert calls(bm, "POST") == []


def test_はてなに付いていなければ打たない_直しの口で新しく付けない(bm):
    """柵: 2 段目の GET（`bookmark_edit_missing_remote`）を外すと赤（POST が新規の追加になる）。"""
    _placed(bm)
    rc, out, _ = edit_cli("--comment", "直す", "--json")
    digest = json.loads(out)["digest"]
    del bm.bookmarks.marks[("lane-owner", EXTERNAL)]          # はてなの画面で消した
    posts = len(calls(bm, "POST"))
    rc, out, err = edit_cli("--comment", "直す", "--confirm", digest, "--by", "m", "--production", "--json")
    assert rc == 2 and json.loads(out)["error"] == "bookmark_edit_missing_remote", (out, err)
    assert len(calls(bm, "POST")) == posts
    assert ("lane-owner", EXTERNAL) not in bm.bookmarks.marks


def test_同じ中身は断り_直すものが無ければ断る_digestは台帳の今で変わる(bm):
    _placed(bm)
    rc, out, _ = edit_cli("--comment", "旧名のメモ", "--json")
    assert json.loads(out)["error"] == "bookmark_edit_no_change"
    rc, out, _ = edit_cli("--json")
    assert json.loads(out)["error"] == "invalid_request"
    rc, out, _ = edit_cli("--comment", "A", "--json")
    stale = json.loads(out)["digest"]
    assert edit_two_stages("--comment", "B")[0] == 0              # 間に別の直し
    rc, out, _ = edit_cli("--comment", "A", "--confirm", stale, "--by", "m", "--production", "--json")
    assert rc == 1 and json.loads(out)["error"] == "confirm_mismatch"


# ---------------------------------------------------------------- 上限

def test_日の枠とホストの枠は数えず当てない(bm):
    """柵: 直しを日の数え（`_classified`）に入れると赤（bookmarks_today が増える）。"""
    _placed(bm)
    bm.edit_principal("hatena-main", daily_max_bookmarks=1, bookmark_same_host_daily=1)
    before = _today(bm)
    rc, out, err = edit_two_stages("--comment", "直した")
    assert rc == 0, (out, err)
    assert _today(bm) == before == (1, 1)
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL_2, comment="c")
    assert p["remaining_today"]["total"] == 0          # 付ける方の枠はそのまま（直しで増えも減りもしない）


def test_連投の窓には数え_当てる(bm):
    """柵: `EVENT_ALIASES` から `bookmark_edit` を外すと赤（直しのあとの追加が窓を抜ける）。"""
    bm.edit_principal("hatena-main", bookmark_burst={"count": 2, "minutes": 10}, daily_max_bookmarks=30,
                      bookmark_same_host_daily=30)
    _placed(bm)
    assert edit_two_stages("--comment", "直した")[0] == 0         # 窓の 2 件目
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL_2, comment="c", check_remote=False)
    with pytest.raises(guard.GuardRefused) as caught:
        hatena_bookmark.execute_locked(ACCOUNT, cfg(ACCOUNT), p, actor="t", via="cli")
    assert str(caught.value) == "burst_limit" and caught.value.reason == "bookmark_burst"
    # 窓が埋まっていれば直しも待たせる
    rc, out, _ = edit_two_stages("--comment", "もう一度")
    assert rc == 1 and json.loads(out)["error"] == "burst_limit"


def test_guardは直しに連投の窓と安全装置だけを当てる():
    assert guard.EDIT_RULES == ("bookmark.stop", "bookmark.burst", "write.floor")
    assert "bookmark_edit" in guard.HATENA_KINDS


# ---------------------------------------------------------------- 結果不明

def test_結果不明は打ち直さずattemptsに残し_reconcileで決める(bm):
    """柵: unknown の直しで行の中身を変えると赤。`pending_bookmark_edit` を見ないと 2 回目の直しが通って赤。"""
    _placed(bm)
    bm.bookmarks.post = lambda url, form: (502, b"bad gateway")
    rc, out, err = edit_two_stages("--comment", "新しい")
    assert rc == 1 and json.loads(out)["status"] == "unknown", (out, err)
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert row["text"] == "旧名のメモ" and row["attempts"][-1]["result"] == "unknown"
    bm.bookmarks.post = None
    rc, out, _ = edit_cli("--comment", "別の", "--json")
    assert json.loads(out)["error"] == "bookmark_result_unknown"
    # はてな側は直っていた（5xx でも届いていた）→ reconcile で confirmed
    bm.bookmarks.marks[("lane-owner", EXTERNAL)]["comment"] = "新しい"
    rc, out, err = call(["bookmark", "reconcile", ACCOUNT, "--by", "t", "--json"])
    assert rc == 0, (out, err)
    assert [e["result"] for e in json.loads(out)["edits"]] == ["confirmed"]
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert row["text"] == "新しい" and row["attempts"][-1]["result"] == "confirmed"
    # 届いていなかった形 → not_applied（行は前のまま・また直せる）
    bm.bookmarks.post = lambda url, form: (502, b"bad gateway")
    assert edit_two_stages("--comment", "三度目")[0] == 1
    bm.bookmarks.post = None
    out = hatena_bookmark.reconcile(ACCOUNT, cfg(ACCOUNT), by="t", sleep=lambda s: None)
    assert [e["result"] for e in out["edits"]] == ["not_applied"]
    assert sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))["text"] == "新しい"
    assert edit_two_stages("--comment", "三度目")[0] == 0


# ---------------------------------------------------------------- 遠くの道・MCP

def test_遠くの道はop_editを同じ2段で受ける(bm, key):
    from thth import server_writes as writes
    from thth.report_service import ReportServiceError
    _placed(bm)
    context = key({ACCOUNT: "lane-a"})

    def req(**body):
        return writes.execute(context, dict(operation="bookmark_request", account=ACCOUNT, **body), via="mcp")

    first = req(url=EXTERNAL, op="edit", tags="新名")
    assert first["stage"] == 1 and first["preview"]["base_tags"] == ["旧名"] and first["preview"]["tags"] == ["新名"]
    done = req(url=EXTERNAL, op="edit", tags="新名", confirm=first["digest"])
    assert done["status"] == "bookmark_edited" and done["via"] == "mcp"
    with pytest.raises(ReportServiceError) as caught:
        req(url=EXTERNAL_2, op="edit", comment="x")
    assert str(caught.value) == "bookmark_not_found"
    for code in ("bookmark_edit_missing_remote", "bookmark_edit_no_change", "bookmark_result_unknown"):
        assert code in writes.SAFE_ERRORS


def test_MCPの写しはop_editでbookmark_editを呼ぶ():
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    build, _ = server.REMOTE_CLI["thth_bookmark_request"]
    assert build({"account": "a", "url": EXTERNAL, "op": "edit", "tags": "x"}) == [
        "bookmark", "edit", "a", "--url=" + EXTERNAL, "--tags=x", "--production", "--json"]
    assert build({"account": "a", "url": EXTERNAL, "op": "edit", "comment": "c", "confirm": "d"}) == [
        "bookmark", "edit", "a", "--url=" + EXTERNAL, "--comment=c", "--confirm=d", "--production", "--json"]
    tool = next(t for t in server.SERVER_TOOLS if t["name"] == "thth_bookmark_request")
    assert "op: edit" in tool["description"]


# ---------------------------------------------------------------- まとめの札

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


def _batch(world, entries):
    path = world.tmp / "edit.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    rc, out, err = call(["bookmark", "batch", ACCOUNT, str(path), "--json"])
    assert rc == 1, (out, err)
    return str(path), json.loads(out)


def test_まとめの札にop_editを許し_timerが直す_日の枠は使わない(bb):
    """柵: 札の op を見ないと赤（直しの札が追加の柵 bookmark_once で除かれる）。"""
    _placed(bb)
    bb.edit_principal("hatena-main", daily_max_bookmarks=1)
    path, first = _batch(bb, [{"op": "edit", "url": EXTERNAL, "tags": ["新名"]},
                              {"op": "edit", "url": EXTERNAL_2, "comment": "x"},
                              {"op": "edit", "url": EXTERNAL},
                              {"op": "update", "url": EXTERNAL, "comment": "x"}])
    p = first["preview"]
    assert [r["reason"] for r in p["items"]] == [None, "bookmark_not_found", "invalid_item", "invalid_item"]
    assert p["items"][0]["op"] == "edit" and p["items"][0]["day"] == 0
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path])
    assert "[直す]" in out and "タグ: 旧名 → 新名" in out and "直す 1" in out
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--confirm", first["digest"], "--by", "masaru",
                         "--production", "--json"])
    assert rc == 0, (out, err)
    before = _today(bb)
    out = bookmark_batch.run_due(ACCOUNT, cfg(ACCOUNT), log=lambda line: None)
    assert out["status"] == "placed" and out.get("edited") == 1
    assert bb.bookmarks.marks[("lane-owner", EXTERNAL)]["tags"] == ["新名"]
    assert _today(bb) == before
    [b] = bookmark_batch.load_batches(ACCOUNT)
    assert b["items"][0]["state"] == "placed" and b["items"][0]["op"] == "edit"
    assert bookmark_batch.status(ACCOUNT, batch=b["batch"])["batches"][0]["items"][0]["op"] == "edit"


def test_追加の札の形と指紋は変わらない(bb):
    """柵: 追加の札に op の鍵を足すと赤（4.1 の札・digest が変わる）。"""
    path, first = _batch(bb, [{"url": EXTERNAL_2, "comment": "c", "tags": ["t"]}])
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL_2, comment="c", tags=["t"], check_remote=False)
    assert first["preview"]["approved_sha"] == bookmark_batch.compute_sha(ACCOUNT, "hatena-main", [p["approved_sha"]])
    assert "op" not in first["preview"]["items"][0]
