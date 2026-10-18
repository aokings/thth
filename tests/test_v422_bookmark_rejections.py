"""4.2.2（設計 S4 §24・kopicha の本番 10/18）: はてなブックマークの 1 件の断りでまとめ全体を止めない。

見るもの:
  B（主）. POST が 401/403 なら同じ run の中で `/rest/1/my` を GET して token を確かめる。通ればその札だけ
     `hatena_rejected_item`（詳細に HTTP の番号）でまとめは進む。GET も断られたら `hatena_auth_rejected`。
     1 件ずつの `add`・`edit` でも同じ見分けで、人向けの文を分ける。原因が何であれ、打ち直さない。
  A. 本文の予告（**断らない**・原因が未確定）: 半角「%」（`bookmark_percent_maybe_rejected`）と、
     `[tag]…comment` の形で 135 字か 343 バイトを超える長さ（`bookmark_length_maybe_rejected`）を、
     `add`・`edit`・`batch` の 1 段目と `thth lint <札のファイル>` で予告する（lint は `warning:`）。
     承認の指紋は変えない。台帳に観測の項目（確かさ low・数は台帳とコードで同じ）。
  C. はてなに打って止まった（blocked）ら `BLOCKED_HOLD_MINUTES`（60 分）は次の run も HTTP を打たない。
     `thth bookmark batch-resume <口座> --by` で今すぐ見直させる。打たずに止まる止め（主体の止めなど）は今どおり。

HTTP は偽物（`hatena_s4_helpers.Bookmarks`）。はてなには出ない。秘密の値は偽物だけ。
"""
from __future__ import annotations

import datetime
import json

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import ACCOUNT, EXTERNAL, EXTERNAL_2, bm, bookmark, calls, cfg, fake_http, world  # noqa: F401
from tests.test_v410_bookmark_batch import (URL_A1, URL_B1, approve, batch_file, bb, item, later, run,  # noqa: F401
                                            states, write)
from thth import bookmark_batch, hatena_bookmark, lint, media_traits, sent

ME = "https://bookmark.hatenaapis.com/rest/1/my"
# `[tag]…comment` の形で 138 字・398 バイト（予告の長さを超える）。コメントは 100 字以内・タグは 31 バイト以内。
LONG_TAGS = ["一次資料いろはにほへ", "二次資料いろはにほへ", "三次資料いろはにほへ", "四次資料いろはにほへ"]
LONG_COMMENT = "あ" * 90


def hatena_bookmark_api(target):
    import urllib.parse
    return "https://bookmark.hatenaapis.com/rest/1/my/bookmark?" + urllib.parse.urlencode([("url", target)])


def me_calls(world):
    return [u for m, u in calls(world, "GET") if u == ME]


def codes(warnings):
    return [w["code"] for w in warnings]


# ================================================================ A. 本文の予告（断らない）

def test_A_予告の境目は135字と343バイト_半角パーセントは全角なら出ない():
    assert hatena_bookmark.body_form("c", ["a", "b"]) == "[a][b]c"
    assert codes(hatena_bookmark.body_warnings("x" * 135, [])) == []
    assert codes(hatena_bookmark.body_warnings("x" * 136, [])) == ["bookmark_length_maybe_rejected"]
    assert codes(hatena_bookmark.body_warnings("あ" * 114 + "x", [])) == []                 # 115 字・343 バイト
    assert codes(hatena_bookmark.body_warnings("あ" * 114 + "xy", [])) == ["bookmark_length_maybe_rejected"]  # 344 バイト
    assert codes(hatena_bookmark.body_warnings("約 85％", [])) == []
    got = hatena_bookmark.body_warnings("約 85%", ["割合%"])
    assert codes(got) == ["bookmark_percent_maybe_rejected"]
    assert "コメント 1 個" in got[0]["text"] and "タグ「割合%」 1 個" in got[0]["text"]
    assert len(hatena_bookmark.body_form(LONG_COMMENT, LONG_TAGS)) == 138


def test_A_addの1段目は予告して断らず_2段目も打つ_指紋は変わらない(bm):
    """柵: `body_warnings` を 1 段目に出さないと赤。断る形に戻すと赤（rc 2）。"""
    args = ["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "約 85% が一次資料", "--tags", "割合%,統計"]
    rc, out, _ = call(args + ["--json"])
    body = json.loads(out)
    assert rc == 1 and body["stage"] == 1
    assert codes(body["preview"]["warnings"]) == ["bookmark_percent_maybe_rejected"]
    rc, text, _ = call(args)
    assert "予告（断りません）: bookmark_percent_maybe_rejected" in text and "原因は未確定" in text
    p = hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=EXTERNAL, comment="約 85% が一次資料", tags="割合%,統計",
                                check_remote=False)
    from thth import approval
    assert p["approved_sha"] == approval.compute_hatena_bookmark_sha(
        account=ACCOUNT, principal="hatena-main", op="add", url=EXTERNAL, delivery="", comment="約 85% が一次資料",
        tags=["割合%", "統計"])
    rc, out, err = call(args + ["--confirm", body["digest"], "--by", "masaru", "--production", "--json"])
    assert rc == 0, (out, err)
    assert len(calls(bm, "POST")) == 1


def test_A_長い札も予告する_editは直したあとの値(bm):
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", LONG_COMMENT, "--tags",
                       ",".join(LONG_TAGS), "--json"])
    body = json.loads(out)
    assert rc == 1 and codes(body["preview"]["warnings"]) == ["bookmark_length_maybe_rejected"]
    assert "138 字・398 バイト" in body["preview"]["warnings"][0]["text"]
    assert "135 字・343 バイト" in body["preview"]["warnings"][0]["text"]
    bookmark(ACCOUNT, url=EXTERNAL_2, comment="旧", tags=["旧名"])
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL_2, "--comment", "100%", "--json"])
    assert rc == 1 and codes(json.loads(out)["preview"]["warnings"]) == ["bookmark_percent_maybe_rejected"]
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL_2, "--comment", "100％", "--json"])
    assert rc == 1 and json.loads(out)["preview"]["warnings"] == []


def test_A_batchは札を除かず予告を添える_指紋は今と同じ(bb):
    path = write(bb, [item(URL_A1, comment="約 85%"), {"url": URL_B1, "comment": LONG_COMMENT, "tags": LONG_TAGS},
                      item("https://c.example.net/y", comment="約 85％")])
    first = json.loads(call(["bookmark", "batch", ACCOUNT, path, "--json"])[1])
    items = first["preview"]["items"]
    assert [r["include"] for r in items] == [True, True, True]
    assert [r.get("warnings") for r in items] == [["bookmark_percent_maybe_rejected"],
                                                  ["bookmark_length_maybe_rejected"], None]
    shas = [hatena_bookmark.prepare(ACCOUNT, cfg(ACCOUNT), url=r["url"], comment=r["comment"], tags=r["tags"],
                                    check_remote=False)["approved_sha"] for r in items]
    assert first["preview"]["approved_sha"] == bookmark_batch.compute_sha(ACCOUNT, "hatena-main", shas)
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path])
    assert "予告（断りません）: bookmark_percent_maybe_rejected" in out
    approve(path)
    assert all("warnings" not in it for it in batch_file(bb)["items"])     # 札の形は変えない


def test_A_lintは札のファイルの予告をwarningで言いrcは0(tmp_path):
    path = tmp_path / "day.jsonl"
    path.write_text("\n".join([
        json.dumps({"url": EXTERNAL, "comment": "約 85%", "tags": "a%b"}, ensure_ascii=False),
        json.dumps({"url": EXTERNAL_2, "comment": LONG_COMMENT, "tags": LONG_TAGS}, ensure_ascii=False),
        json.dumps({"url": "https://c.example.net/y", "comment": "約 85％"}, ensure_ascii=False)]) + "\n",
        encoding="utf-8")
    problems = lint.lint_file(str(path))
    assert [p.split(": 札")[0] for p in problems] == ["warning: bookmark_percent_maybe_rejected",
                                                    "warning: bookmark_length_maybe_rejected"]
    assert "札 1（1 行目）" in problems[0] and "タグ「a%b」 1 個" in problems[0]
    assert all(lint.is_warning(p) for p in problems)
    rc, out, err = call(["lint", str(path)])
    assert rc == 0 and "bookmark_length_maybe_rejected" in out + err


def test_A_予告の札が断られてもその札だけ外してまとめは進む(bb):
    """本番の形: 14 件目「約 85%」・26 件目（長い札）が 401。token は生きている → その札だけ refused。"""
    bb.bookmarks.post = lambda target, form: ((401, b"no") if target in (URL_A1, URL_B1)
                                              else _place_default(bb, target, form))
    approve(write(bb, [item(URL_A1, comment="約 85%"), {"url": URL_B1, "comment": LONG_COMMENT, "tags": LONG_TAGS},
                       item("https://c.example.net/y")]))
    out, _ = run(bb)
    b = batch_file(bb)
    assert [(i["state"], i["reason"]) for i in b["items"]] == [
        ("refused", "hatena_rejected_item"), ("refused", "hatena_rejected_item"), ("placed", None)]
    assert out["status"] == "placed" and not out.get("blocked")


def test_A_媒体の台帳に観測の項目があり_数はコードと同じ():
    doc = media_traits.load("hatenabookmark")
    found = next(t for t in doc["traits"] if t["id"] == "hatenabookmark.body_rejected_observed")
    assert found["kind"] == "observation" and found["confidence"] == "low" and found["signals"] == []
    assert found["evidence"]["type"] == "observed" and found["evidence"]["observed_at"] == "2026-10-18"
    assert "kopicha" in found["evidence"]["record"]
    assert "半角 %" in found["statement"] and "未確定" in found["statement"] and "401" in found["statement"]
    assert f"{hatena_bookmark.BODY_OK_MAX_CHARS} 字・{hatena_bookmark.BODY_OK_MAX_BYTES} バイト" in found["statement"]
    assert not any(t["id"] == "hatenabookmark.percent_in_body_rejected_observed" for t in doc["traits"])


# ================================================================ B. 1 件だけの 401/403

def test_B_addでPOSTが401でもtokenが生きていればその札だけの断り(bm):
    """柵: `_rejection` の GET を外すと赤（token の生きている 401 を認可の問題と言う）。"""
    bm.bookmarks.post = (401, b"Unauthorized")
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json"])
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                         "--by", "masaru", "--production", "--json"])
    body = json.loads(out)
    assert rc == 2 and body["error"] == "hatena_rejected_item", (out, err)
    assert "この札だけはてなに断られました" in body["message"] and "token は生きています" in body["message"]
    assert "HTTP 401" in body["message"] and "principal auth" not in body["message"]
    assert len(me_calls(bm)) == 1           # POST のあとに 1 回だけ確かめる
    assert [r for r in sent.records(bm.state(ACCOUNT)) if r.get("kind") == "bookmark_add"] == []


def test_B_addでtokenも断られたら認可の問題(bm):
    bm.bookmarks.post = (403, b"Forbidden")
    bm.bookmarks.me = (401, b"Unauthorized")
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json"])
    digest = json.loads(out)["digest"]
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                       "--by", "masaru", "--production", "--json"])
    body = json.loads(out)
    assert rc == 2 and body["error"] == "hatena_auth_rejected"
    assert "thth principal auth" in body["message"] and "--force" in body["message"]


def test_B_editでも同じ見分け(bm):
    bookmark(ACCOUNT, url=EXTERNAL, comment="旧", tags=["旧名"])
    bm.bookmarks.post = (401, b"Unauthorized")
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "新", "--json"])
    digest = json.loads(out)["digest"]
    args = ["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "新", "--confirm", digest, "--by", "masaru",
            "--production", "--json"]
    rc, out, _ = call(args)
    assert rc == 2 and json.loads(out)["error"] == "hatena_rejected_item"
    bm.bookmarks.me = (401, b"Unauthorized")
    rc, out, _ = call(args)
    assert rc == 2 and json.loads(out)["error"] == "hatena_auth_rejected"
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert row["text"] == "旧" and not row.get("attempts")


def test_B_まとめは断られた札だけrefusedにして進む(bb):
    """柵: `hatena_rejected_item` を `_FINAL_BOOKMARK` から外すと赤（まとめ全体が止まる）。"""
    bb.bookmarks.post = lambda target, form: (401, b"no") if target == URL_A1 else _place_default(bb, target, form)
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    out, _ = run(bb)
    b = batch_file(bb)
    assert [(i["state"], i["reason"], i["detail"]) for i in b["items"]] == [
        ("refused", "hatena_rejected_item", "HTTP 401"), ("placed", None, None)]
    assert out["status"] == "placed" and not out.get("blocked")
    assert bookmark_batch.status(ACCOUNT)["blocked"] is None


def test_B_まとめでtokenも断られたら止まる(bb):
    bb.bookmarks.post = (401, b"no")
    bb.bookmarks.me = (401, b"no")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    out, _ = run(bb)
    assert out["status"] == "blocked" and out["blocked"]["code"] == "hatena_auth_rejected"
    assert states(bb) == ["pending", "pending"]


def _place_default(world, target, form):
    """偽物のはてなの既定の POST（付ける）。"""
    from tests.hatena_s4_helpers import bookmark_object
    mark = bookmark_object(target, comment=form.get("comment", [""])[0], tags=form.get("tags", []))
    world.bookmarks.marks[("lane-owner", target)] = mark
    return 200, json.dumps(mark).encode()


# ================================================================ C. 止まったら打ち直し続けない

def _blocked(bb):
    bb.bookmarks.post = (401, b"no")
    bb.bookmarks.me = (401, b"no")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    now = later(0)
    out, _ = run(bb, now=now)
    assert out["status"] == "blocked" and out["blocked"]["code"] == "hatena_auth_rejected"
    return now


def test_C_はてなに打って止まったら次のrunはHTTPを打たない_60分あとに試す(bb):
    """柵: blocked に `next_at` を置かないと赤（10 分ごとに同じ札を打ち直す・kopicha 10/18 に 57 回）。"""
    now = _blocked(bb)
    before = len(bb.http.calls)
    for minutes in (10, 20, 30, 59):
        out, _ = run(bb, now=now + datetime.timedelta(minutes=minutes))
        assert out["status"] == "waiting" and out["blocked"]["code"] == "hatena_auth_rejected"
    assert len(bb.http.calls) == before
    s = bookmark_batch.status(ACCOUNT)
    assert s["blocked"]["until"] == s["next_at"]
    assert bookmark_batch.jst.parse(s["next_at"]) - now == datetime.timedelta(
        minutes=bookmark_batch.BLOCKED_HOLD_MINUTES)
    rc, text, _ = call(["bookmark", "batch-status", ACCOUNT])
    assert "まではてなに打ちません" in text and f"thth bookmark batch-resume {ACCOUNT} --by" in text
    # 60 分あとは今の柵で見直す（直っていなければまた止まる）
    run(bb, now=now + datetime.timedelta(minutes=61))
    assert len(bb.http.calls) > before


def test_C_batch_resumeで今すぐ見直させ_札と指紋は変えない(bb):
    now = _blocked(bb)
    before_items = batch_file(bb)["items"]
    rc, out, _ = call(["bookmark", "batch-resume", ACCOUNT, "--json"])
    assert rc == 1 and json.loads(out)["error"] == "by_required"
    bb.bookmarks.post = None
    bb.bookmarks.me = None
    rc, out, err = call(["bookmark", "batch-resume", ACCOUNT, "--by", "masaru", "--json"])
    body = json.loads(out)
    assert rc == 0 and body["was_blocked"]["code"] == "hatena_auth_rejected" and body["pending"] == 2, (out, err)
    s = bookmark_batch.status(ACCOUNT)
    assert s["blocked"] is None and s["next_at"] is None
    assert [i["approved_sha"] for i in batch_file(bb)["items"]] == [i["approved_sha"] for i in before_items]
    out, _ = run(bb, now=now + datetime.timedelta(minutes=5))
    assert out["status"] == "placed" and states(bb)[0] == "placed"
    from thth import admin_log
    assert any(r["event"] == "bookmark_batch_resumed" and r["by"] == "masaru" for r in admin_log.read()[0])


def test_C_打たずに止まる止めは今どおり次の刻みで見直す(bb):
    """主体の止め（HTTP を打たない）は 60 分待たせない（止めを解けば次の刻みで付く）。"""
    from thth import principals
    approve(write(bb, [item(URL_A1)]))
    principals.stop("hatena-main", "owner", actor="tester")
    out, _ = run(bb)
    assert out["blocked"]["code"] == "principal_stopped" and "until" not in out["blocked"]
    principals.resume("hatena-main", by="tester")
    run(bb, now=later(10))
    assert states(bb) == ["placed"]
