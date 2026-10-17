"""4.1（設計 S4 §20）: はてなブックマークのまとめて承認と、timer が時刻を散らして付ける順番待ち。

見るもの:
  - ファイルの形（JSON Lines・配列・形の合わない行はその行だけ除く・100 件まで）
  - 1 段目は何も書かず HTTP も打たない。全件の一覧（付ける・翌日以降・除く理由）と 1 つの digest
  - digest は含める札の指紋の並び。除く札は入らない。2 段目は digest・--by・--production・止め・timer の有無を見る
  - timer: 時間帯・次に付けてよい時刻・1 回に 1 件（遅れていれば 2 件）・1 件ずつ今と同じ柵（guard・柵 1〜3）
  - 柵に断られた札は理由つきで refused・結果不明は unknown で打ち直さない・枠は翌日へ回す・止めでは待つ
  - 取り消し・期限・状態の口・遠くの道・MCP・`thth run` の末尾・127.0.0.1 の本物の HTTP

HTTP は偽物（`hatena_s4_helpers.Bookmarks`）か 127.0.0.1 のサーバ。はてなには出ない。秘密の値は偽物だけ。
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import sys
import threading
import urllib.parse

import pytest

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import (ACCOUNT, EXTERNAL, EXTERNAL_2, OTHER, OTHER_LANE_URL, OWN_URL,  # noqa: F401
                                     bm, bookmark_object, calls, cfg, fake_http, published, record_row, rows, world)
from tests.test_v400_s1_remote import key  # noqa: F401
from thth import bookmark_batch, guard, hatena_bookmark, httpsafe, jst, principals, sent

URL_A1 = "https://a.example.com/page/1"
URL_A2 = "https://a.example.com/page/2"
URL_A3 = "https://a.example.com/page/3"
URL_B1 = "https://b.example.org/x"
URL_C1 = "https://c.example.net/y"


@pytest.fixture
def bb(bm, monkeypatch):
    """まとめて承認の世界。時間帯は一日中・乱数は真ん中・窓はたっぷり（遅れていない）。"""
    monkeypatch.setattr(bookmark_batch, "ACTIVE_HOURS", (0, 24))
    monkeypatch.setattr(bookmark_batch, "RANDOM", lambda: 0.5)
    bm.edit_principal("hatena-main", daily_max_bookmarks=30, bookmark_same_host_daily=3)
    for name in (ACCOUNT, OTHER):
        data = bm.account(name)
        data["scheduled"] = True      # timer に載っている口座（試験の雛形は scheduled: false）
        bm.write_account(data)
    _plenty(monkeypatch)
    return bm


def _plenty(monkeypatch, minutes=600):
    def window(now):
        return now - datetime.timedelta(hours=1), now + datetime.timedelta(minutes=minutes)
    monkeypatch.setattr(bookmark_batch, "_window", window)


def write(world, entries, name="day.jsonl"):
    path = world.tmp / name
    path.write_text("\n".join(e if isinstance(e, str) else json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
                    encoding="utf-8")
    return str(path)


def item(url, comment="捜査メモ", tags=("一次資料",)):
    return {"url": url, "comment": comment, "tags": list(tags)}


def stage1(path, account=ACCOUNT):
    rc, out, err = call(["bookmark", "batch", account, path, "--json"])
    assert rc == 1, (out, err)
    return json.loads(out)


def approve(path, account=ACCOUNT, by="masaru"):
    first = stage1(path, account)
    rc, out, err = call(["bookmark", "batch", account, path, "--confirm", first["digest"], "--by", by,
                         "--production", "--json"])
    assert rc == 0, (out, err)
    return json.loads(out)


def run(world, now=None, account=ACCOUNT):
    logs = []
    out = bookmark_batch.run_due(account, cfg(account), log=logs.append, now=now)
    return out, logs


def batch_file(world, account=ACCOUNT):
    [b] = [b for b in bookmark_batch.load_batches(account)]
    return b


def states(world, account=ACCOUNT):
    return [it["state"] for it in batch_file(world, account)["items"]]


def later(minutes):
    return jst.now_jst() + datetime.timedelta(minutes=minutes)


# ---------------------------------------------------------------- ファイルの形

def test_形_JSON_Linesと配列_空行と注釈を飛ばし_合わない行だけ除く():
    text = "\n".join([
        "# 10/18 の札", "",
        json.dumps(item(URL_A1)),
        "{壊れた行",
        json.dumps({"url": URL_A2, "comment": "c", "memo": "x"}),
        json.dumps({"url": URL_A3, "delivery": "dl_000000000001", "comment": "c"}),
        json.dumps({"delivery": "dl_000000000001", "comment": "c", "tags": "a,b"}),
    ])
    got = bookmark_batch.parse_text(text)
    assert [(e["n"], e["line"], e["problem"]) for e in got] == [
        (1, 3, None), (2, 4, "invalid_json"), (3, 5, "unknown_key"), (4, 6, "invalid_item"), (5, 7, None)]
    arr = bookmark_batch.parse_text(json.dumps([item(URL_A1), {"url": 3, "comment": "c"}]))
    assert [e["problem"] for e in arr] == [None, "invalid_item"]
    with pytest.raises(bookmark_batch.BatchError) as e:
        bookmark_batch.parse_text("\n".join(json.dumps(item(f"https://h{i}.example.com/")) for i in range(101)))
    assert e.value.code == "batch_too_large"


# ---------------------------------------------------------------- 1 段目

def test_1段目は全件の一覧とdigestを見せ_何も書かずHTTPも打たない(bb):
    """柵: 1 段目でまとめのファイルを書く・柵 2 を打つと赤。"""
    dl = published(ACCOUNT, OWN_URL)
    path = write(bb, [item(URL_A1), item(URL_A2), {"delivery": dl, "comment": "自分の記事。まとめ", "tags": "相場"},
                      item("ftp://example.com/x"), item(URL_A1, comment="同じ URL"), "{壊れ"])
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path])
    assert rc == 1, (out, err)
    assert "digest: " in out and "付ける" in out and "除く: invalid_url" in out
    assert "除く: duplicate_in_batch" in out and "除く: invalid_json" in out
    assert "自分の記事 " + dl in out
    assert "付ける直前に" in out    # 柵 2・3 の予告
    p = stage1(path)["preview"]
    assert (p["n"], p["included"], p["excluded"]) == (6, 3, 3)
    assert [r["reason"] for r in p["items"]] == [None, None, None, "invalid_url", "duplicate_in_batch",
                                                 "invalid_json"]
    assert bookmark_batch.load_batches(ACCOUNT) == []
    assert calls(bb) == []


def test_1段目_柵1と別レーンの記事と自分の主体の記事のURLを除く(bb):
    record_row(bb, OTHER, URL_B1)                       # 同じ owner の別の主体が付けた
    published(OTHER, OTHER_LANE_URL, principal="hatena-other", blog_id="c.hatenablog.com", article="lane-c/x")
    published(ACCOUNT, OWN_URL)
    path = write(bb, [item(URL_B1), item(OTHER_LANE_URL), item(OWN_URL), item(URL_C1)])
    p = stage1(path)["preview"]
    assert [(r["reason"], r["detail"]) for r in p["items"]] == [
        ("bookmark_once", "other_principal:hatena-other"), ("bookmark_other_lane_article", None),
        ("use_delivery", None), (None, None)]


def test_1段目_同じホストの枠を超える分は断らず翌日へ回す見込み(bb):
    bb.edit_principal("hatena-main", bookmark_same_host_daily=2)
    record_row(bb, ACCOUNT, "https://a.example.com/old")     # 今日このホストに 1 件
    path = write(bb, [item(URL_A1), item(URL_A2), item(URL_B1)])
    p = stage1(path)["preview"]
    assert [r["day"] for r in p["items"]] == [0, 1, 0]
    assert p["forecast"]["today"] == 2 and p["forecast"]["later"] == 1
    assert {"host": "a.example.com", "n": 2, "today": 1} in p["hosts"]


def test_digestは含める札の指紋の並び_除く札は入らない_中身と順で変わる(bb):
    """柵: 指紋に札の中身を入れないと赤。"""
    base = stage1(write(bb, [item(URL_A1), item(URL_B1)]))["digest"]
    assert stage1(write(bb, [item(URL_A1), item(URL_B1), item("ftp://x/")], "b.jsonl"))["digest"] == base
    assert stage1(write(bb, [item(URL_B1), item(URL_A1)], "c.jsonl"))["digest"] != base
    assert stage1(write(bb, [item(URL_A1, comment="別の文"), item(URL_B1)], "d.jsonl"))["digest"] != base
    assert stage1(write(bb, [item(URL_A1, tags=("別",)), item(URL_B1)], "e.jsonl"))["digest"] != base
    p = stage1(write(bb, [item(URL_A1)], "f.jsonl"))["preview"]
    single = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=URL_A1, comment="捜査メモ", tags=["一次資料"],
                                     check_remote=False)
    assert p["items"][0]["approved_sha"] == single["approved_sha"]   # 1 件ずつの承認と同じ指紋
    assert p["approved_sha"] == bookmark_batch.compute_sha(ACCOUNT, "hatena-main", [single["approved_sha"]])


# ---------------------------------------------------------------- 2 段目

def test_2段目は順番待ちに置く_digest_by_production_同じ並びは2回置かない(bb):
    path = write(bb, [item(URL_A1), item(URL_B1), item("ftp://x/")])
    digest = stage1(path)["digest"]
    for extra, code in ((["--confirm", "000000000000", "--by", "m", "--production"], "confirm_mismatch"),
                        (["--confirm", digest, "--production"], "by_required"),
                        (["--confirm", digest, "--by", "m"], "production_disabled")):
        rc, out, err = call(["bookmark", "batch", ACCOUNT, path, *extra, "--json"])
        assert rc == 1 and json.loads(out)["error"] == code, (out, err)
    assert bookmark_batch.load_batches(ACCOUNT) == []
    done = approve(path)
    assert done["queued"] == 2 and done["excluded"] == 1 and done["batch"].startswith("bb_")
    b = batch_file(bb)
    assert b["approved_by"] == "masaru" and b["via"] == "cli" and states(bb) == ["pending", "pending"]
    assert [e["reason"] for e in b["excluded"]] == ["invalid_url"]
    assert oct(os.stat(os.path.join(bb.state(ACCOUNT), "bookmark_batches", done["batch"] + ".json")).st_mode)[-3:] \
        == "600"
    # 同じファイルをもう一度: 札は already_queued で除かれ digest が変わる（2 回は並ばない）
    rc, out, err = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "m", "--production",
                         "--json"])
    assert rc == 1 and json.loads(out)["error"] == "confirm_mismatch"
    assert [r["reason"] for r in stage1(path)["preview"]["items"]] == ["already_queued", "already_queued",
                                                                         "invalid_url"]
    # 同じ指紋のまとめが既にあれば batch_exists（付け終わって柵 1 が無い札でも、同じ並びは 2 回置かない）
    again = bookmark_batch.prepare(ACCOUNT, cfg(ACCOUNT), open(path, encoding="utf-8").read())
    again.update(included=2, approved_sha=batch_file(bb)["approved_sha"])
    with pytest.raises(bookmark_batch.BatchError) as caught:
        bookmark_batch.approve_locked(ACCOUNT, cfg(ACCOUNT), again, actor="m", via="cli")
    assert caught.value.code == "batch_exists"
    # 別の並びに同じ URL を書くと already_queued で除く
    p = stage1(write(bb, [item(URL_A1, comment="別の文"), item(URL_C1)], "b.jsonl"))["preview"]
    assert [r["reason"] for r in p["items"]] == ["already_queued", None]
    assert calls(bb) == []


def test_2段目_止めた主体と口座_timerの無い口座_レーンの無い口座は断る(bb):
    path = write(bb, [item(URL_A1)])
    digest = stage1(path)["digest"]
    principals.stop("hatena-main", "owner", actor="tester")
    assert "principal_stopped" in "".join(stage1_lines := call(["bookmark", "batch", ACCOUNT, path])[1:])
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "m", "--production", "--json"])
    assert rc == 1 and json.loads(out)["error"] == "principal_stopped"
    principals.resume("hatena-main", by="tester")
    data = bb.account(ACCOUNT)
    data["scheduled"] = False
    bb.write_account(data)
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "m", "--production", "--json"])
    assert json.loads(out)["error"] == "schedule_unavailable"
    data["scheduled"] = True
    bb.write_account(data)
    bb.edit_principal("hatena-main", bookmark={"lanes": ["lane-b"]})
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path, "--confirm", digest, "--by", "m", "--production", "--json"])
    assert json.loads(out)["error"] == "bookmark_not_allowed_for_lane"
    assert bookmark_batch.load_batches(ACCOUNT) == []
    assert stage1_lines  # 1 段目は止めを予告した


# ---------------------------------------------------------------- timer

def test_timerは1回に1件_次に付けてよい時刻まで待ち_1件ずつ柵を通してsent_bmに書く(bb):
    """柵: next_at を見ないと赤（2 回目の run で 2 件目が付く）。"""
    approve(write(bb, [item(URL_A1), item(URL_B1), item(URL_C1)]))
    now = jst.now_jst()
    out, logs = run(bb, now=now)
    assert out["status"] == "placed" and out["placed"] == 1
    assert states(bb) == ["placed", "pending", "pending"]
    row = sent.read(bb.state(ACCOUNT), sent.bookmark_post_id(URL_A1))
    assert row["result"] == "confirmed" and row["tags"] == ["一次資料"] and row["text"] == "捜査メモ"
    it = batch_file(bb)["items"][0]
    assert it["post_id"] == row["post_id"] and it["permalink"]
    # 1 件ごとに柵 2（公開）・柵 3（GET）・POST を 1 回ずつ
    assert [m for m, _u in calls(bb)] == ["GET", "GET", "POST"]
    pace = bookmark_batch.status(ACCOUNT)
    nxt = jst.parse(pace["next_at"])
    assert nxt > now
    out, _ = run(bb, now=now + datetime.timedelta(minutes=1))
    assert out["status"] == "waiting" and states(bb) == ["placed", "pending", "pending"]
    out, _ = run(bb, now=nxt + datetime.timedelta(seconds=1))
    assert out["placed"] == 1 and states(bb) == ["placed", "placed", "pending"]


def test_間は窓の残りを残りの件数で割り乱数で散らす(bb, monkeypatch):
    """柵: 乱数を掛けないと赤（同じ残りで間が変わらない）。"""
    now = jst.now_jst()
    _plenty(monkeypatch, minutes=300)
    monkeypatch.setattr(bookmark_batch, "RANDOM", lambda: 0.0)
    low = bookmark_batch._gap_minutes(now, 10)
    monkeypatch.setattr(bookmark_batch, "RANDOM", lambda: 0.999)
    high = bookmark_batch._gap_minutes(now, 10)
    assert round(low) == 18 and round(high) == 42      # 300 分 ÷ 10 件 = 30 分 × 0.6〜1.4
    assert bookmark_batch._gap_minutes(now, 1000) == bookmark_batch.MIN_GAP_MINUTES
    assert bookmark_batch._gap_minutes(now, 1) == bookmark_batch.MAX_GAP_MINUTES


def test_遅れていれば1回に2件まで(bb, monkeypatch):
    _plenty(monkeypatch, minutes=15)                      # 刻みは 1 回・待ちは 3 件
    approve(write(bb, [item(URL_A1), item(URL_B1), item(URL_C1)]))
    out, _ = run(bb)
    assert out["placed"] == 2 and states(bb) == ["placed", "placed", "pending"]


def test_時間帯の外では付けない(bb, monkeypatch):
    approve(write(bb, [item(URL_A1)]))
    monkeypatch.setattr(bookmark_batch, "_window",
                        lambda now: (now + datetime.timedelta(hours=1), now + datetime.timedelta(hours=2)))
    out, _ = run(bb)
    assert out["status"] == "outside_hours" and states(bb) == ["pending"] and calls(bb) == []


def test_柵2と柵3に断られた札は理由つきでrefused_同じrunで次の札へ(bb):
    """柵: 柵 2・3 の断りを pending のままにすると赤（毎回打ちに行く）。"""
    bb.bookmarks.public[URL_A1] = ["someone-else"]                         # 同じ owner の別の主体の ID
    bb.bookmarks.marks[("lane-owner", URL_B1)] = bookmark_object(URL_B1)    # この主体で手で付けた
    approve(write(bb, [item(URL_A1), item(URL_B1), item(URL_C1)]))
    out, _ = run(bb)
    assert (out["refused"], out["placed"]) == (2, 1)
    b = batch_file(bb)
    assert [(i["state"], i["reason"], i["detail"]) for i in b["items"]] == [
        ("refused", "bookmark_once", "other_principal_remote:someone-else"),
        ("refused", "bookmark_exists_remote", None), ("placed", None, None)]
    assert [m for m, _u in calls(bb, "POST")] == ["POST"]
    rc, text, err = call(["bookmark", "batch-status", ACCOUNT])
    assert rc == 0 and "bookmark_once（other_principal_remote:someone-else）" in text
    assert "bookmark_exists_remote" in text


def test_台帳の柵1は付けるときにもう一度見る(bb):
    approve(write(bb, [item(URL_A1)]))
    record_row(bb, OTHER, URL_A1)                 # 承認のあとに別のレーンが付けた
    out, _ = run(bb)
    assert batch_file(bb)["items"][0]["reason"] == "bookmark_once" and calls(bb, "POST") == []


def test_結果不明はunknownで残し打ち直さない_runは止める(bb):
    """柵: unknown の札を pending に戻すと赤（次の run で 2 回目の POST）。"""
    bb.bookmarks.post = lambda url, form: (502, b"bad gateway")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    out, logs = run(bb)
    assert out["status"] == "unknown" and states(bb) == ["unknown", "pending"]
    assert sent.read(bb.state(ACCOUNT), sent.bookmark_post_id(URL_A1))["result"] == "unknown"
    assert any("thth bookmark reconcile" in line for line in logs)
    bb.bookmarks.post = None
    out, _ = run(bb, now=later(60))
    assert states(bb) == ["unknown", "pending"]
    assert [u for m, u in calls(bb, "POST") if URL_A1 in urllib.parse.unquote(u)].__len__() == 1
    # 状態の口は照合のあとの result を引く
    bb.bookmarks.marks[("lane-owner", URL_A1)] = bookmark_object(URL_A1)
    assert call(["bookmark", "reconcile", ACCOUNT, "--by", "t"])[0] == 0
    s = bookmark_batch.status(ACCOUNT)
    assert s["batches"][0]["items"][0]["result"] == "confirmed"


def test_同じホストの枠は翌日へ回し_1日の全体の枠は次の時刻を翌日に(bb):
    """柵: ホストの枠を見ないと赤（枠を越えて付ける）。"""
    bb.edit_principal("hatena-main", bookmark_same_host_daily=1, daily_max_bookmarks=2)
    approve(write(bb, [item(URL_A1), item(URL_A2), item(URL_B1), item(URL_C1)]))
    out, _ = run(bb)
    assert states(bb) == ["placed", "pending", "pending", "pending"]
    out, _ = run(bb, now=later(200))
    b = batch_file(bb)
    assert [i["state"] for i in b["items"]] == ["placed", "pending", "placed", "pending"]
    assert b["items"][1]["wait"] == "bookmark_host_limit" and b["items"][1]["not_before"]
    out, _ = run(bb, now=later(400))
    assert out["status"] == "limit" and states(bb) == ["placed", "pending", "placed", "pending"]
    assert batch_file(bb)["items"][3]["wait"] == "bookmark_limit"
    assert jst.parse(bookmark_batch.status(ACCOUNT)["next_at"]) > later(400)
    assert len(calls(bb, "POST")) == 2


def test_guardの同じホストの断りも翌日へ回す(bb, monkeypatch):
    """先に除く数えが外れても、決めるのは guard.check（断りを翌日へ回し、次の札へ）。"""
    monkeypatch.setattr(bookmark_batch, "_budget", lambda *a, **k: None)
    bb.edit_principal("hatena-main", bookmark_same_host_daily=1)
    record_row(bb, ACCOUNT, "https://a.example.com/old")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    out, _ = run(bb)
    b = batch_file(bb)
    assert [(i["state"], i["wait"]) for i in b["items"]] == [("pending", "bookmark_host_limit"), ("placed", None)]


def test_止めた主体では付けず待つ_解けば続きから(bb):
    """柵: 止めの断りで札を refused にすると赤（止めが解けても付かない）。"""
    approve(write(bb, [item(URL_A1)]))
    principals.stop("hatena-main", "owner", actor="tester")
    out, _ = run(bb)
    assert out["status"] == "blocked" and out["blocked"]["code"] == "principal_stopped"
    assert states(bb) == ["pending"] and calls(bb) == []
    rc, text, _ = call(["bookmark", "batch-status", ACCOUNT])
    assert "止まっています: principal_stopped" in text
    principals.resume("hatena-main", by="tester")
    out, _ = run(bb, now=later(30))
    assert states(bb) == ["placed"]


def test_productionでなくなれば付けない(bb):
    approve(write(bb, [item(URL_A1)]))
    data = bb.account(ACCOUNT)
    data["production"] = False
    bb.write_account(data)
    out, _ = run(bb)
    assert out["blocked"]["code"] == "production_disabled" and states(bb) == ["pending"] and calls(bb) == []


def test_承認のあとに札か対象が変われば断る(bb):
    """柵: 札の指紋を作り直して照合しないと赤（書き換えた札が出る）。"""
    dl = published(ACCOUNT, OWN_URL)
    approve(write(bb, [item(URL_A1), {"delivery": dl, "comment": "自分の記事。", "tags": []}, item(URL_B1)]))
    path = os.path.join(bb.state(ACCOUNT), "bookmark_batches", batch_file(bb)["batch"] + ".json")
    data = json.loads(open(path, encoding="utf-8").read())
    data["items"][0]["comment"] = "書き換えた"
    open(path, "w", encoding="utf-8").write(json.dumps(data))
    from thth import deliveries
    deliveries.advance(ACCOUNT, dl, state="deleted", at=jst.iso())
    out, _ = run(bb)
    b = batch_file(bb)
    assert [(i["state"], i["reason"]) for i in b["items"]] == [
        ("refused", "approval_stale"), ("refused", "bookmark_target_not_own"), ("placed", None)]


def test_まとめの指紋が合わなければ断る(bb):
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    path = os.path.join(bb.state(ACCOUNT), "bookmark_batches", batch_file(bb)["batch"] + ".json")
    data = json.loads(open(path, encoding="utf-8").read())
    data["items"].reverse()
    open(path, "w", encoding="utf-8").write(json.dumps(data))
    run(bb)
    assert {i["reason"] for i in batch_file(bb)["items"] if i["state"] == "refused"} == {"approval_stale"}
    assert calls(bb, "POST") == []


def test_期限を過ぎた札はexpired(bb):
    approve(write(bb, [item(URL_A1)]))
    out, _ = run(bb, now=later(60 * 24 * bookmark_batch.TTL_DAYS + 5))
    assert states(bb) == ["expired"] and calls(bb) == []


def test_取り消し_待ちの札だけ_付けたものは消さない(bb):
    done = approve(write(bb, [item(URL_A1), item(URL_B1), item(URL_C1)]))
    run(bb)
    rc, out, err = call(["bookmark", "batch-cancel", ACCOUNT, done["batch"], "--item", "1", "--by", "m", "--json"])
    assert rc == 2 and json.loads(out)["error"] == "batch_not_cancellable"
    rc, out, err = call(["bookmark", "batch-cancel", ACCOUNT, done["batch"], "--item", "2", "--by", "m", "--json"])
    assert rc == 0 and json.loads(out)["cancelled"] == 1
    rc, out, err = call(["bookmark", "batch-cancel", ACCOUNT, done["batch"], "--json"])
    assert rc == 1 and json.loads(out)["error"] == "by_required"
    rc, out, err = call(["bookmark", "batch-cancel", ACCOUNT, done["batch"], "--by", "m", "--json"])
    assert rc == 0 and json.loads(out)["cancelled"] == 1
    assert states(bb) == ["placed", "cancelled", "cancelled"]
    assert bookmark_batch.run_due(ACCOUNT, cfg(ACCOUNT), log=lambda _l: None) is None   # 待ちが無ければ何もしない
    rc, out, err = call(["bookmark", "batch-cancel", ACCOUNT, "bb_0000000000000000", "--by", "m", "--json"])
    assert json.loads(out)["error"] == "batch_not_found"


def test_状態の口のJSONと1件ずつのaddは変わらない(bb):
    done = approve(write(bb, [item(URL_A1), item("ftp://x/")]))
    run(bb)
    rc, out, err = call(["bookmark", "batch-status", ACCOUNT, "--json"])
    s = json.loads(out)
    assert rc == 0 and s["pending"] == 0 and s["batches"][0]["counts"]["placed"] == 1
    assert s["batches"][0]["excluded"][0]["reason"] == "invalid_url"
    rc, out, err = call(["bookmark", "batch-status", ACCOUNT, "--batch", "bb_ffffffffffffffff", "--json"])
    assert rc == 2 and json.loads(out)["error"] == "batch_not_found"
    assert done["batch"] in call(["bookmark", "batch-status", ACCOUNT])[1]
    # 1 件ずつの add はそのまま（まとめで付けた URL は柵 1 で断る）
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", URL_A1, "--comment", "c", "--json"])
    assert rc == 1 and json.loads(out)["stage"] == 1


# ---------------------------------------------------------------- 口座ごと（kopicha 専用にしない・本線 10/17 夜）

def test_2つの口座_別の主体と別のレーンの札は互いの順番待ちに混ざらない(bb):
    """ACCOUNT（主体 hatena-main・lane-a）と ZED（主体 hatena-z・lane-z・別の owner）。

    柵: 札の置き場を口座でなく共有にすると赤（片方の run がもう片方の札を付ける・状態に混ざる）。
    """
    from tests.hatena_s4_helpers import ZED
    data = bb.account(ZED)
    data["scheduled"] = True
    bb.write_account(data)
    bb.edit_principal("hatena-z", daily_max_bookmarks=1)        # 上限は主体ごと（片方を絞っても他方は変わらない）
    mine = approve(write(bb, [item(URL_A1), item(URL_B1)], "kopicha.jsonl"))
    theirs = approve(write(bb, [item(URL_A1), item(URL_C1)], "asmon.jsonl"), account=ZED)  # 別の owner なら同じ URL も可
    assert mine["batch"] != theirs["batch"]
    assert [b["batch"] for b in bookmark_batch.load_batches(ACCOUNT)] == [mine["batch"]]
    assert [b["batch"] for b in bookmark_batch.load_batches(ZED)] == [theirs["batch"]]
    assert batch_file(bb, ZED)["principal"] == "hatena-z" and batch_file(bb)["principal"] == "hatena-main"
    # ACCOUNT の run は ACCOUNT の札だけを、ACCOUNT の主体の token で付ける
    run(bb)
    assert states(bb) == ["placed", "pending"] and states(bb, ZED) == ["pending", "pending"]
    assert ("lane-owner", URL_A1) in bb.bookmarks.marks and ("zed", URL_A1) not in bb.bookmarks.marks
    # ZED の run は ZED の札だけ。主体の上限（1 日 1 件）は ZED の主体だけに効く
    run(bb, account=ZED)
    run(bb, account=ZED, now=later(200))
    assert states(bb, ZED) == ["placed", "pending"] and ("zed", URL_A1) in bb.bookmarks.marks
    assert batch_file(bb, ZED)["items"][1]["wait"] == "bookmark_limit"
    run(bb, now=later(200))
    assert states(bb) == ["placed", "placed"]
    # 状態・取り消しも口座の中だけ
    assert [b["batch"] for b in bookmark_batch.status(ZED)["batches"]] == [theirs["batch"]]
    rc, out, _ = call(["bookmark", "batch-cancel", ACCOUNT, theirs["batch"], "--by", "m", "--json"])
    assert rc == 2 and json.loads(out)["error"] == "batch_not_found"
    assert states(bb, ZED) == ["placed", "pending"]


# ---------------------------------------------------------------- thth run の末尾

def test_thth_runの末尾で付ける(bb, monkeypatch):
    from thth import cli, core
    approve(write(bb, [item(URL_A1)]))
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.core, "throw_once",
                        lambda *_a, **_k: core.ThrowResult(0, "production", "skip", "出すものが無い"))
    from thth import hatena_reactions
    monkeypatch.setattr(hatena_reactions, "run_collect", lambda *a, **k: 0)
    rc, out, err = call(["run", ACCOUNT])
    assert states(bb) == ["placed"], (rc, out, err)
    assert "1 件付けました" in out
    # 付ける側が壊れても run の rc は変えない
    monkeypatch.setattr(bookmark_batch, "run_due", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rc2, out2, err2 = call(["run", ACCOUNT])
    assert rc2 == rc and "ブックマークのまとめを付けられませんでした" in err2


# ---------------------------------------------------------------- 遠くの道・MCP

def test_遠くの道_2段で置き_状態を読み_取り消す(bb, key):
    from thth import server_writes as writes
    from thth.report_service import ReportServiceError, execute_report
    context = key({ACCOUNT: "lane-a"})
    text = "\n".join(json.dumps(i) for i in (item(URL_A1), item(URL_B1)))

    def req(**body):
        return writes.execute(context, dict(operation="bookmark_batch_request", account=ACCOUNT, **body), via="mcp")

    def refused(op, **body):
        with pytest.raises(ReportServiceError) as caught:
            writes.execute(context, dict(operation=op, account=ACCOUNT, **body), via="mcp")
        return str(caught.value)

    first = req(text=text)
    assert first["stage"] == 1 and first["preview"]["included"] == 2 and bookmark_batch.load_batches(ACCOUNT) == []
    assert refused("bookmark_batch_request", text=text, confirm="000000000000") == "confirm_mismatch"
    assert refused("bookmark_batch_request", text=text, memo="x") == "invalid_request"
    done = req(text=text, confirm=first["digest"])
    assert done["queued"] == 2 and done["via"] == "mcp" and batch_file(bb)["approved_by"] == "person"
    assert refused("bookmark_batch_request", text=text, confirm=first["digest"]) == "confirm_mismatch"
    s = execute_report(context, {"operation": "bookmark_batch_status", "account": ACCOUNT})
    assert s["pending"] == 2
    gone = writes.execute(context, dict(operation="bookmark_batch_cancel", account=ACCOUNT, batch=done["batch"],
                                        item=2), via="mcp")
    assert gone["cancelled"] == 1 and states(bb) == ["pending", "cancelled"]
    assert batch_file(bb)["items"][1]["detail"] == "person"
    assert refused("bookmark_batch_cancel", batch=done["batch"], item="2") == "invalid_request"
    for code in bookmark_batch.REASONS:
        assert code in writes.SAFE_ERRORS


@pytest.fixture
def mcp_server():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    return server


def test_MCPの道具と手元の写し(mcp_server):
    from thth import server_writes
    names = {t["name"] for t in mcp_server.SERVER_TOOLS}
    assert {"thth_bookmark_batch_request", "thth_bookmark_batch_status", "thth_bookmark_batch_cancel"} <= names
    assert "bookmark_batch_status" not in server_writes.WRITE_OPERATIONS     # 読む口（writes の無い鍵でも出る）
    assert {"bookmark_batch_request", "bookmark_batch_cancel"} <= server_writes.WRITE_OPERATIONS
    build, stdin_key = mcp_server.REMOTE_CLI["thth_bookmark_batch_request"]
    assert stdin_key == "text"
    assert build({"account": "a", "text": "x", "confirm": "d"}) == [
        "bookmark", "batch", "a", "-", "--confirm=d", "--production", "--json"]
    build, _ = mcp_server.REMOTE_CLI["thth_bookmark_batch_cancel"]
    assert build({"account": "a", "batch": "bb_1", "item": 2}) == [
        "bookmark", "batch-cancel", "a", "bb_1", "--json", "--item=2"]
    assert not [n for n in names if "reconcile" in n]


def test_遠くの道のCLIはファイルの中身をtextで送る(bb, monkeypatch):
    from thth import remote
    sent_calls = []

    def fake_call(operation, account, **fields):
        sent_calls.append((operation, account, fields))
        if operation == "bookmark_batch_request" and not fields.get("confirm"):
            p = bookmark_batch.prepare(ACCOUNT, cfg(ACCOUNT), fields["text"])
            return {"stage": 1, "digest": p["digest"], "preview": p}
        return {"queued": 1, "batch": "bb_0000000000000000", "excluded": 0}
    monkeypatch.setattr(remote, "call", fake_call)
    path = write(bb, [item(URL_A1)])
    import argparse
    args = argparse.Namespace(file=path, confirm=None, json=False)
    assert remote.COMMANDS["bookmark batch"](args, "far-account") == 1
    assert sent_calls[0][0] == "bookmark_batch_request" and sent_calls[0][2]["text"] == open(path).read()
    args.confirm = "abc"
    assert remote.COMMANDS["bookmark batch"](args, "far-account") == 0


# ---------------------------------------------------------------- 127.0.0.1 の本物の HTTP

class _Hatena:
    """127.0.0.1 の本物の HTTP。GET（柵 3）は 404・POST は 200・公開のエントリー情報は null。"""

    def __init__(self):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, status, raw):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.requests.append(("GET", self.path, self.headers.get("Authorization")))
                if self.path.startswith("/entry/jsonlite/"):
                    return self._reply(200, b"null")
                return self._reply(404, b"{}")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append(("POST", self.path, body.decode()))
                url = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["url"][0]
                return self._reply(200, json.dumps(bookmark_object(url)).encode())

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


_REAL_URLOPEN = httpsafe.urlopen


def test_127_0_0_1の本物のHTTPでtimerが1件付ける(bb, monkeypatch):
    """`httpsafe.urlopen` は差し替えない。公開の情報に Authorization を付けない・form にコメントとタグ。"""
    from thth.adapters import hatenabookmark
    approve(write(bb, [item(URL_A1, comment="捜査メモ & 一次資料", tags=("香り", "一次資料"))]))
    with _Hatena() as srv:
        base = f"http://127.0.0.1:{srv.port}"
        monkeypatch.setattr(hatenabookmark, "BASE", base)
        monkeypatch.setattr(hatenabookmark, "PUBLIC_BASE", base)
        monkeypatch.setattr(httpsafe, "urlopen", _REAL_URLOPEN)
        out, logs = run(bb)
    assert out["placed"] == 1, logs
    methods = [(m, p.split("?")[0]) for m, p, _x in srv.requests]
    assert methods == [("GET", "/entry/jsonlite/"), ("GET", "/rest/1/my/bookmark"), ("POST", "/rest/1/my/bookmark")]
    assert srv.requests[0][2] is None and srv.requests[1][2].startswith("OAuth ")
    assert urllib.parse.parse_qs(srv.requests[2][2]) == {"comment": ["捜査メモ & 一次資料"], "tags": ["一次資料", "香り"]}
    assert states(bb) == ["placed"]
    assert sent.read(bb.state(ACCOUNT), sent.bookmark_post_id(URL_A1))["result"] == "confirmed"
